"""Operational pipeline: customers in -> recommendation -> review queue -> approved message -> outbox file for the CRM.

This is the glue that makes the flow run without anyone typing numbers. It adds NO new decision logic:
  * what to offer comes from app.recommend (twin ranking, SIMULATED uplift, guardrails, cost threshold),
  * the words come from app.message (template, or a Claude draft that is validated in code),
  * anything the guardrails mark needs_human_approval waits in the queue until a named person approves it.
Nothing is sent from here. export_outbox() writes the ready messages to a CSV that the CRM / marketing team sends from. The outbox
carries only customer_ref, first name and the message; the CRM joins contact details by customer_ref, so no contact data lives here.

Case statuses:
  pending_approval  the guardrails need a human (limit not configured, account state unknown ...) before anything is drafted
  needs_first_name  approved (or auto-permitted) but there is no valid first name yet, so no message can be drafted
  ready             message drafted and validated, waiting in the outbox
  exported          written to an outbox file
  rejected          a person turned it down
  escalated         vulnerable / escalated account: handed to a human, never contacted by this system
  no_action         not flagged, or nothing worthwhile and permitted
  invalid           the source row could not be used (reason stored)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd
import yaml

import churn_serving as cs
from app import guardrails as gr
from app import message as msg
from app import recommend as rec
from app import twin as tw
from app import uplift as up

ROOT = Path(__file__).resolve().parent.parent
MAPPING_FILE = ROOT / 'pipeline.yaml'
OPEN_STATUSES = ('pending_approval', 'needs_first_name', 'ready')
ACCOUNT_FIELDS = ('vulnerable', 'escalated', 'contacts_this_month', 'discount_pct_used_this_quarter')
MAPPING_KEYS = {'id_column', 'first_name_column', 'columns', 'account_state', 'true_values', 'false_values'}
DECISION_STATUS = {'recommend': 'needs_first_name', 'recommend_pending_approval': 'pending_approval',
                   'no_action': 'no_action', 'escalate_to_human': 'escalated'}
OUTBOX_COLUMNS = ['case_id', 'customer_ref', 'first_name', 'offer_name', 'offer_description', 'message', 'approved_by', 'message_log_id']


class CaseError(Exception):
    """The case is not in a state that allows this action (HTTP 409)."""


class CaseNotFound(CaseError):
    pass


def load_bundle(root=ROOT) -> dict:
    b = cs.load_bundle(root)
    b['scenarios'] = tw.load_scenarios(Path(root) / 'scenarios.yaml')
    b['guardrails'] = gr.load_guardrails(Path(root) / 'guardrails.yaml')
    b['uplift'] = up.load(Path(root) / up.ARTIFACT)
    return b


# ------------------------------------------------------------------ source mapping
def load_mapping(path=None) -> dict:
    p = Path(path or MAPPING_FILE)
    raw = (yaml.safe_load(p.read_text(encoding='utf8')) if p.exists() else None) or {}
    unknown = set(raw) - MAPPING_KEYS
    if unknown:
        raise ValueError('unknown key(s) in %s: %s (allowed: %s)' % (p.name, sorted(unknown), sorted(MAPPING_KEYS)))
    m = {'id_column': raw.get('id_column') or 'customerID', 'first_name_column': raw.get('first_name_column'),
         'columns': dict(raw.get('columns') or {}), 'account_state': dict(raw.get('account_state') or {}),
         'true_values': [str(x).strip().lower() for x in (raw.get('true_values') or ['yes', 'y', 'true', '1'])],
         'false_values': [str(x).strip().lower() for x in (raw.get('false_values') or ['no', 'n', 'false', '0'])]}
    bad = set(m['account_state']) - set(ACCOUNT_FIELDS)
    if bad:
        raise ValueError('account_state keys must be among %s, got %s' % (list(ACCOUNT_FIELDS), sorted(bad)))
    return m


def _text(v) -> str:
    return '' if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip()


def _coerce_customer(row: Mapping, meta: Mapping) -> dict:
    out = {}
    for c in meta['input_columns']:
        s = _text(row[c])
        if c in meta['categorical_levels']:
            out[c] = s
            continue
        if s == '':
            raise ValueError('%s is blank' % c)
        try:
            f = float(s)
        except ValueError:
            raise ValueError('%s=%r is not a number' % (c, s))
        if not math.isfinite(f):
            raise ValueError('%s=%r is not a finite number' % (c, s))
        out[c] = int(f) if c in ('tenure', 'SeniorCitizen') and f == int(f) else f
    return out


def _account_state(row: Mapping, mapping: Mapping) -> dict | None:
    """None when no account-state columns are mapped (everything unknown). An unreadable cell is also None = unknown, never 'safe'."""
    if not mapping['account_state']:
        return None
    st = {k: None for k in ACCOUNT_FIELDS}
    for field, col in mapping['account_state'].items():
        s = _text(row[col]).lower()
        if field in ('vulnerable', 'escalated'):
            st[field] = True if s in mapping['true_values'] else False if s in mapping['false_values'] else None
        else:
            try:
                f = float(s)
                ok = math.isfinite(f) and f >= 0
            except ValueError:
                ok = False
            st[field] = (int(f) if field == 'contacts_this_month' else f) if ok else None
    return st


def records_from_frame(df: pd.DataFrame, mapping: Mapping, meta: Mapping) -> list[dict]:
    """Turn a source table into pipeline records. A bad ROW becomes a record with an error (stored as 'invalid'); a source that lacks
    required COLUMNS raises ValueError because nothing in it can be used."""
    df = df.rename(columns=mapping['columns'])
    id_col, name_col = mapping['id_column'], mapping['first_name_column']
    need = [id_col] + list(meta['input_columns']) + list(mapping['account_state'].values()) + ([name_col] if name_col else [])
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError('source is missing column(s) %s; map them in pipeline.yaml (columns / id_column / account_state)' % missing)
    out = []
    for row in df.to_dict('records'):
        ref = _text(row[id_col])
        rec_ = {'customer_ref': ref[:64], 'first_name': _text(row[name_col]) or None if name_col else None,
                'customer': None, 'account_state': _account_state(row, mapping), 'error': None}
        try:
            if not msg.REF_RE.match(ref):
                raise ValueError('%s=%r is not a usable id (1-64 letters, digits, - or _)' % (id_col, ref))
            rec_['customer'] = _coerce_customer(row, meta)
        except ValueError as e:
            rec_['error'] = str(e)
        out.append(rec_)
    return out


def read_csv_source(source, mapping: Mapping, meta: Mapping, limit: int | None = None) -> list[dict]:
    """source: a path or a file-like object. Values are read as text so nothing is silently reinterpreted."""
    df = pd.read_csv(source, dtype=str, keep_default_na=False, nrows=limit)
    return records_from_frame(df, mapping, meta)


def read_sql_source(db: str | Path, query: str, mapping: Mapping, meta: Mapping, limit: int | None = None) -> list[dict]:
    """Read-only query against a SQLite file. (Other databases: export to CSV, or build the frame yourself and use records_from_frame.)"""
    con = sqlite3.connect('file:%s?mode=ro' % Path(db).resolve().as_posix(), uri=True)
    try:
        df = pd.read_sql_query(query, con)
    finally:
        con.close()
    return records_from_frame(df.head(limit) if limit else df, mapping, meta)


# ------------------------------------------------------------------ store
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path=None) -> sqlite3.Connection:
    con = sqlite3.connect(path or msg.db_path())
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts TEXT NOT NULL, updated_ts TEXT NOT NULL,
            customer_ref TEXT NOT NULL, first_name TEXT, status TEXT NOT NULL, decision TEXT, action_name TEXT,
            probability REAL, threshold REAL, threshold_is_placeholder INTEGER, guardrail_status TEXT,
            reasons_json TEXT, approvals_json TEXT, recommendation_json TEXT,
            message TEXT, message_source TEXT, message_log_id INTEGER, approved_by TEXT, error TEXT, note TEXT, exported_ts TEXT);
        CREATE INDEX IF NOT EXISTS cases_ref ON cases (customer_ref);
        CREATE INDEX IF NOT EXISTS cases_status ON cases (status);
        CREATE TABLE IF NOT EXISTS case_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, ts TEXT NOT NULL, event TEXT NOT NULL, actor TEXT, detail TEXT);""")
    return con


def _event(con, case_id: int, event: str, actor: str | None = None, detail: str | None = None) -> None:
    con.execute('INSERT INTO case_events (case_id, ts, event, actor, detail) VALUES (?,?,?,?,?)', (case_id, _now(), event, actor, detail))


def _public(row: sqlite3.Row, full=False) -> dict:
    d = dict(row)
    d['reasons'] = json.loads(d.pop('reasons_json') or 'null') or []
    d['approvals_needed'] = json.loads(d.pop('approvals_json') or 'null') or []
    rj = d.pop('recommendation_json')
    if full:
        d['recommendation'] = json.loads(rj) if rj else None
    d['threshold_is_placeholder'] = None if d['threshold_is_placeholder'] is None else bool(d['threshold_is_placeholder'])
    return d


def _get(con, case_id: int) -> sqlite3.Row:
    row = con.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
    if row is None:
        raise CaseNotFound('no case #%s' % case_id)
    return row


def _insert(con, run_id: str, ref: str, first_name, status: str, *, out: Mapping | None = None, error: str | None = None) -> int:
    decision = out['decision'] if out else None
    keep = out if decision in ('recommend', 'recommend_pending_approval') else (
        {k: out.get(k) for k in ('decision', 'risk', 'reasons', 'guardrails')} if out else None)
    action = (out or {}).get('action')
    g = (out or {}).get('guardrails') or {}
    risk = (out or {}).get('risk') or {}
    now = _now()
    cur = con.execute("""INSERT INTO cases (run_id, ts, updated_ts, customer_ref, first_name, status, decision, action_name, probability, threshold,
                         threshold_is_placeholder, guardrail_status, reasons_json, approvals_json, recommendation_json, error)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (run_id, now, now, ref, first_name, status, decision, action['name'] if action else None, risk.get('churn_probability'),
                       risk.get('threshold'), None if not risk else int(risk['threshold_is_placeholder']), g.get('status'),
                       json.dumps((out or {}).get('reasons') or []), json.dumps(g.get('approvals_needed') or []),
                       json.dumps(keep, default=str) if keep else None, error))
    return int(cur.lastrowid)


def _try_finalize(con, case_id: int, rules: Mapping, client, path) -> sqlite3.Row:
    """needs_first_name -> ready, if there is a valid first name. The offer is the one the recommendation already chose."""
    row = _get(con, case_id)
    if row['status'] != 'needs_first_name':
        return row
    try:
        first = msg.validate_first_name(row['first_name'] or '')
    except ValueError:
        return row
    rj = json.loads(row['recommendation_json'])
    offer = msg.offer_from_action(rj['action'])
    d = msg.draft_message(first, offer, rj['shap_reasons'], rules, client=client)
    con.commit()                                   # release the write lock: msg.log_message uses its own connection
    log_id = msg.log_message({'customer_ref': row['customer_ref'], 'first_name': first, 'offer': offer, 'shap_reasons': rj['shap_reasons'],
                              'message': d['message'], 'source': d['source'], 'model': d['model'], 'rejected_reason': d['rejected_reason'],
                              'rejected_text': d['rejected_text'], 'decision': rj['decision'], 'guardrail_status': row['guardrail_status'],
                              'approved_by': row['approved_by']}, path)
    cur = con.execute("UPDATE cases SET status='ready', message=?, message_source=?, message_log_id=?, updated_ts=? WHERE id=? AND status='needs_first_name'",
                      (d['message'], d['source'], log_id, _now(), case_id))
    if cur.rowcount:
        _event(con, case_id, 'message_drafted', None, d['source'])
    con.commit()
    return _get(con, case_id)


# ------------------------------------------------------------------ ingest
def ingest(records: Iterable[Mapping], bundle: dict, *, client=None, path=None, source: str = '',
           progress: Callable[[int, int], None] | None = None, recommender: Callable | None = None) -> dict:
    """Run every record through /recommend's logic and store a case for each. client=None -> deterministic template messages;
    client='auto' -> a Claude draft if ANTHROPIC_API_KEY is set (still validated in code). A customer who already has an open case
    (pending_approval / needs_first_name / ready) is skipped so the same person is never queued twice."""
    records = list(records)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    rules = bundle['guardrails']
    counts: Counter = Counter()
    skipped: list[str] = []
    con = _connect(path)
    try:
        open_refs = {r[0] for r in con.execute('SELECT customer_ref FROM cases WHERE status IN (%s)' % ','.join('?' * len(OPEN_STATUSES)), OPEN_STATUSES)}
        for i, r in enumerate(records, 1):
            ref = r['customer_ref']
            if ref in open_refs:
                skipped.append(ref)
                continue
            open_refs.add(ref)
            if r.get('error'):
                _insert(con, run_id, ref, r.get('first_name'), 'invalid', error=r['error'])
                counts['invalid'] += 1
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    tw.CustomerTwin(r['customer'], bundle['meta'])          # data-quality gate: scoring alone accepts incoherent rows
                    out = (recommender or rec.recommend)(r['customer'], bundle, r.get('account_state'))
            except ValueError as e:
                _insert(con, run_id, ref, r.get('first_name'), 'invalid', error=str(e))
                counts['invalid'] += 1
                continue
            status = DECISION_STATUS[out['decision']]
            cid = _insert(con, run_id, ref, r.get('first_name'), status, out=out)
            _event(con, cid, 'created', None, '%s via %s' % (out['decision'], source or run_id))
            if status == 'needs_first_name':
                status = _try_finalize(con, cid, rules, client, path)['status']
            counts[status] += 1
            if progress:
                progress(i, len(records))
        con.commit()
    finally:
        con.commit()
        con.close()
    return {'run_id': run_id, 'source': source, 'records': len(records), 'counts': dict(counts), 'skipped_open_case': skipped}


# ------------------------------------------------------------------ queue actions
def _check_actor(name, what='approved_by') -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError('%s is required (1-80 characters)' % what)
    return name.strip()


def approve(case_id: int, approved_by: str, rules: Mapping, *, first_name: str | None = None, client=None, path=None) -> dict:
    """A person accepts a pending_approval case. The offer is unchanged; this only releases it for drafting."""
    who = _check_actor(approved_by)
    first = msg.validate_first_name(first_name) if first_name else None
    con = _connect(path)
    try:
        _get(con, case_id)
        cur = con.execute("UPDATE cases SET status='needs_first_name', approved_by=?, first_name=COALESCE(?, first_name), updated_ts=? "
                          "WHERE id=? AND status='pending_approval'", (who, first, _now(), case_id))
        if not cur.rowcount:
            raise CaseError('case #%s is %s; only pending_approval cases can be approved' % (case_id, _get(con, case_id)['status']))
        _event(con, case_id, 'approved', who)
        con.commit()
        return _public(_try_finalize(con, case_id, rules, client, path))
    finally:
        con.close()


def provide_first_name(case_id: int, first_name: str, rules: Mapping, *, client=None, path=None) -> dict:
    """Supply the customer's first name (it is not in the model data). Drafts the message if the case is already cleared."""
    first = msg.validate_first_name(first_name)
    con = _connect(path)
    try:
        row = _get(con, case_id)
        if row['status'] not in ('pending_approval', 'needs_first_name'):
            raise CaseError('case #%s is %s; a first name can only be set while it is pending_approval or needs_first_name' % (case_id, row['status']))
        con.execute('UPDATE cases SET first_name=?, updated_ts=? WHERE id=?', (first, _now(), case_id))
        _event(con, case_id, 'first_name_set')
        con.commit()
        return _public(_try_finalize(con, case_id, rules, client, path))
    finally:
        con.close()


def edit_message(case_id: int, text: str, rules: Mapping, *, edited_by: str, path=None) -> dict:
    """A person rewrites a ready message. It must pass exactly the same checks as a machine draft."""
    who = _check_actor(edited_by, 'edited_by')
    con = _connect(path)
    try:
        row = _get(con, case_id)
        if row['status'] != 'ready':
            raise CaseError('case #%s is %s; only ready messages can be edited' % (case_id, row['status']))
        offer = msg.offer_from_action(json.loads(row['recommendation_json'])['action'])
        problems = msg.validate_message(text, offer, rules)
        if row['first_name'].lower() not in text.lower():
            problems.append('does not address the customer by first name')
        if problems:
            raise ValueError('edited message rejected: ' + '; '.join(problems))
        con.execute("UPDATE cases SET message=?, message_source='human_edited', updated_ts=? WHERE id=?", (text, _now(), case_id))
        _event(con, case_id, 'message_edited', who)
        con.commit()
        return _public(_get(con, case_id))
    finally:
        con.close()


def reject(case_id: int, rejected_by: str, reason: str, *, path=None) -> dict:
    who = _check_actor(rejected_by, 'rejected_by')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('a reason is required')
    con = _connect(path)
    try:
        row = _get(con, case_id)
        cur = con.execute("UPDATE cases SET status='rejected', note=?, updated_ts=? WHERE id=? AND status IN ('pending_approval','needs_first_name','ready')",
                          (reason.strip(), _now(), case_id))
        if not cur.rowcount:
            raise CaseError('case #%s is %s; it can no longer be rejected' % (case_id, row['status']))
        _event(con, case_id, 'rejected', who, reason.strip())
        con.commit()
        return _public(_get(con, case_id))
    finally:
        con.close()


def export_outbox(out_path=None, *, path=None) -> list[dict]:
    """Hand the ready messages over. Writes a CSV (if out_path) FIRST, then marks the cases exported, so a failed write loses nothing.
    Nothing is sent: the CRM / marketing team sends from the file, matching customer_ref to their contact details."""
    con = _connect(path)
    try:
        rows = []
        for r in con.execute("SELECT * FROM cases WHERE status='ready' ORDER BY id"):
            offer = msg.offer_from_action(json.loads(r['recommendation_json'])['action'])
            rows.append({'case_id': r['id'], 'customer_ref': r['customer_ref'], 'first_name': r['first_name'], 'offer_name': r['action_name'],
                         'offer_description': offer['description'], 'message': r['message'], 'approved_by': r['approved_by'],
                         'message_log_id': r['message_log_id']})
        if out_path and rows:
            p = Path(out_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=OUTBOX_COLUMNS)
                w.writeheader()
                w.writerows(rows)
        ts = _now()
        for r in rows:
            con.execute("UPDATE cases SET status='exported', exported_ts=?, updated_ts=? WHERE id=? AND status='ready'", (ts, ts, r['case_id']))
            _event(con, r['case_id'], 'exported', None, str(out_path) if out_path else 'returned')
        con.commit()
        return rows
    finally:
        con.close()


# ------------------------------------------------------------------ reading
def list_cases(status: str | None = None, *, limit: int = 200, path=None) -> list[dict]:
    con = _connect(path)
    try:
        q, args = 'SELECT * FROM cases', []
        if status:
            q, args = q + ' WHERE status=?', [status]
        return [_public(r) for r in con.execute(q + ' ORDER BY id DESC LIMIT ?', args + [int(limit)])]
    finally:
        con.close()


def scenario_comparison(case: Mapping) -> dict:
    """The twin's scenario results for a stored case, ready to show: nobody has to run a what-if by hand. Rows come from the recommendation
    saved at ingest (model sensitivity, not a causal estimate); None for cases that never reached the scenario step."""
    rj = case.get('recommendation') or {}
    if not rj.get('candidates'):
        return {'interpretation': tw.INTERPRETATION, 'baseline_risk': (rj.get('risk') or {}).get('churn_probability'), 'ranked_by': None, 'rows': [], 'not_applicable': []}
    base = rj['risk']['churn_probability']
    chosen = (rj.get('action') or {}).get('name')
    rows = [{'scenario': c['name'], 'chosen': c['name'] == chosen, 'risk_now': base, 'risk_after': base - c['risk_drop'], 'risk_drop': c['risk_drop'],
             'expected_net_value': c['expected_net_value'], 'guardrails': c['guardrails']['status'], 'confidence': c['confidence'],
             'warning': ' | '.join(c['warnings'])} for c in rj['candidates']]
    return {'interpretation': tw.INTERPRETATION, 'baseline_risk': base, 'ranked_by': rj.get('ranked_by'), 'rows': rows,
            'not_applicable': [{'scenario': n['name'], 'reason': n['reason']} for n in rj.get('not_applicable', [])]}


def get_case(case_id: int, *, path=None) -> dict:
    con = _connect(path)
    try:
        d = _public(_get(con, case_id), full=True)
        d['scenarios'] = scenario_comparison(d)
        d['events'] = [dict(e) for e in con.execute('SELECT ts, event, actor, detail FROM case_events WHERE case_id=? ORDER BY id', (case_id,))]
        return d
    finally:
        con.close()


def summary(*, path=None) -> dict:
    con = _connect(path)
    try:
        return {r['status']: r['n'] for r in con.execute('SELECT status, COUNT(*) AS n FROM cases GROUP BY status')}
    finally:
        con.close()


# ------------------------------------------------------------------ command line
def _print_cases(cases: list[dict]) -> None:
    if not cases:
        print('(no cases)')
    for c in cases:
        print('#%-5s %-16s %-17s %-14s p=%s  %s' % (c['id'], c['status'], c['decision'] or '-', c['action_name'] or '-',
                                                   '%.3f' % c['probability'] if c['probability'] is not None else '-', c['customer_ref']))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m app.pipeline', description=__doc__.split('\n\n')[0])
    ap.add_argument('--db', help='SQLite file for the queue (default: CHURN_DB or message_log.sqlite)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run', help='score a customer file and fill the review queue')
    r.add_argument('--source', help='CSV file')
    r.add_argument('--sqlite', help='SQLite file to read from (use with --query)')
    r.add_argument('--query', help='SELECT returning one row per customer')
    r.add_argument('--mapping', help='column mapping file (default: pipeline.yaml)')
    r.add_argument('--limit', type=int, help='only the first N rows (trial run)')
    r.add_argument('--llm', action='store_true', help='let Claude phrase messages (needs ANTHROPIC_API_KEY); default is the template')
    q = sub.add_parser('queue', help='list cases')
    q.add_argument('--status')
    q.add_argument('--limit', type=int, default=50)
    s = sub.add_parser('show', help='one case in full')
    s.add_argument('id', type=int)
    a = sub.add_parser('approve', help='approve a pending_approval case')
    a.add_argument('id', type=int)
    a.add_argument('--by', required=True)
    a.add_argument('--first-name')
    n = sub.add_parser('name', help='set the first name on a case')
    n.add_argument('id', type=int)
    n.add_argument('first_name')
    j = sub.add_parser('reject', help='reject a case')
    j.add_argument('id', type=int)
    j.add_argument('--by', required=True)
    j.add_argument('--reason', required=True)
    e = sub.add_parser('export', help='write ready messages to an outbox CSV and mark them exported')
    e.add_argument('--out', default=None, help='default: outbox/outbox_<timestamp>.csv')
    sub.add_parser('summary', help='count of cases by status')
    args = ap.parse_args(argv)
    path = args.db

    try:
        if args.cmd == 'run':
            if bool(args.source) == bool(args.sqlite):
                ap.error('give exactly one of --source (CSV) or --sqlite with --query')
            b = load_bundle()
            mapping = load_mapping(args.mapping)
            if args.source:
                recs = read_csv_source(args.source, mapping, b['meta'], args.limit)
            else:
                if not args.query:
                    ap.error('--sqlite needs --query')
                recs = read_sql_source(args.sqlite, args.query, mapping, b['meta'], args.limit)
            print('read %d rows; scoring (flagged customers take about a second each)...' % len(recs))
            res = ingest(recs, b, client='auto' if args.llm else None, path=path, source=args.source or args.sqlite,
                         progress=lambda i, n_: print('  %d/%d' % (i, n_), end='\r') if i % 50 == 0 else None)
            print(json.dumps(res, indent=2))
        elif args.cmd == 'queue':
            _print_cases(list_cases(args.status, limit=args.limit, path=path))
        elif args.cmd == 'show':
            print(json.dumps(get_case(args.id, path=path), indent=2, default=str))
        elif args.cmd == 'approve':
            print(json.dumps(approve(args.id, args.by, load_bundle()['guardrails'], first_name=args.first_name, path=path), indent=2, default=str))
        elif args.cmd == 'name':
            print(json.dumps(provide_first_name(args.id, args.first_name, load_bundle()['guardrails'], path=path), indent=2, default=str))
        elif args.cmd == 'reject':
            print(json.dumps(reject(args.id, args.by, args.reason, path=path), indent=2, default=str))
        elif args.cmd == 'export':
            out = args.out or ROOT / 'outbox' / ('outbox_%s.csv' % datetime.now().strftime('%Y%m%d_%H%M%S'))
            rows = export_outbox(out, path=path)
            print('%d message(s) exported%s' % (len(rows), ' to %s' % out if rows else ''))
        elif args.cmd == 'summary':
            print(json.dumps(summary(path=path), indent=2))
    except (ValueError, CaseError) as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
