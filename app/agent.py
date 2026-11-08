"""Retention agent: runs the whole churn process end to end as one tracked job.

    1 ingest       read the customer rows and check every one (bad rows are kept with a reason, never dropped silently)
    2 score        the served calibrated model gives every customer a churn probability
    3 prioritise   customers above the alert line, most at risk first (capped per run; the rest wait for the next run)
    4 twin         each prioritised customer's digital twin tries the scenarios.yaml options (model sensitivity, NOT causal)
    5 decide       app.recommend picks the best option the guardrails allow, or hands the account to a person
    6 queue        app.pipeline stores a case per customer and drafts a validated message where it is allowed to
    7 approve      HUMAN step: a named person approves or rejects each offer (/cases/{id}/approve | reject)
    8 handoff      HUMAN step: approved messages are exported for the CRM (/outbox/export). Nothing is ever sent from here.

The agent is deterministic: it chains the existing modules in a fixed order and adds no decision logic of its own. The LLM (if any)
only phrases messages inside app.message. Every step is written to SQLite (agent_runs / agent_steps / agent_customers in CHURN_DB)
so the web app can show what the agent did, customer by customer.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import traceback
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd

import churn_serving as cs
from app import message as msg
from app import pipeline as pl
from app import recommend as rec
from app import twin as tw

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv'

STAGES = [
    ('ingest', 'Ingest data', 'Read the customer file and check every row.', 'auto'),
    ('score', 'Score churn risk', 'The churn model gives every customer a likelihood of leaving.', 'auto'),
    ('prioritise', 'Prioritise', 'Customers above the alert line, most at risk first.', 'auto'),
    ('twin', 'Test on digital twin', 'Each retention option is tried on a copy of the customer before anyone is contacted.', 'auto'),
    ('decide', 'Decide', 'Pick the best option the contact rules allow, or hand the account to a person.', 'auto'),
    ('queue', 'Queue & draft', 'Store a case per customer and draft the message where it is allowed.', 'auto'),
    ('approve', 'Human approval', 'A named person approves or rejects each offer.', 'human'),
    ('handoff', 'Hand off outreach', 'Approved messages go to the CRM. Nothing is sent from here.', 'human'),
]
STAGE_KEYS = [s[0] for s in STAGES]
DEFAULT_SAMPLE = 100
DEFAULT_MAX_TWIN = 50
MAX_ROWS = 5000

# Plain-language names for the scenarios.yaml options (falls back to the scenario description).
OPTION_LABELS = {
    'do_nothing': 'Do nothing',
    'price_cut_10': '10% off the monthly bill',
    'contract_one_year': 'Move to a one-year contract',
    'add_tech_support': 'Add tech support',
    'combined_package': 'Bundle: one-year contract + tech support + 5% off',
}
DECISION_TEXT = {
    'recommend': 'Offer ready (rules allow it)',
    'recommend_pending_approval': 'Offer needs approval',
    'no_action': 'No action',
    'escalate_to_human': 'Handed to a person',
}
DECISION_PHRASE = {    # (one, many) for the run headline
    'recommend': ('offer ready', 'offers ready'),
    'recommend_pending_approval': ('offer needs approval', 'offers need approval'),
    'no_action': ('with no action', 'with no action'),
    'escalate_to_human': ('handed to a person', 'handed to a person'),
}
DEMO_NOTE = ('Demo data: real customers from the 20% hold-out of the Telco dataset (the model never trained on them). '
             'First names are made up; account state (vulnerable, contacts, discounts used) is unknown, so every offer needs approval.')

_LOCK = threading.Lock()
_ACTIVE: dict[str, Any] = {'run_id': None}


class AgentBusy(Exception):
    """Another run is still in progress (HTTP 409)."""


class RunNotFound(Exception):
    pass


# ------------------------------------------------------------------ plain language
def option_label(name: str | None, bundle: Mapping | None = None) -> str | None:
    if name is None:
        return None
    if name in OPTION_LABELS:
        return OPTION_LABELS[name]
    sc = ((bundle or {}).get('scenarios') or {}).get(name)
    return (sc or {}).get('description') or name.replace('_', ' ')


def plain_reason(feature: str, value: Any) -> str:
    """One model driver (a column that pushes risk up) said the way a retention manager would say it."""
    v = value.item() if isinstance(value, np.generic) else value
    if feature == 'Contract':
        return 'Month-to-month contract: can leave at any time' if v == 'Month-to-month' else 'On a %s contract' % str(v).lower()
    if feature == 'tenure':
        n = int(round(float(v)))
        return 'Only %d month%s with us' % (n, '' if n == 1 else 's') if n < 24 else '%d months with us' % n
    if feature == 'MonthlyCharges':
        return 'High monthly bill ($%.2f)' % float(v)
    if feature == 'InternetService':
        return 'Fiber optic internet' if v == 'Fiber optic' else 'Internet: %s' % v
    if feature == 'PaymentMethod':
        return 'Pays by %s' % str(v).lower()
    if feature == 'PaperlessBilling':
        return 'Paperless billing' if v == 'Yes' else 'Paper billing'
    if feature == 'SeniorCitizen':
        return 'Senior customer' if int(v) == 1 else 'Not a senior customer'
    if feature in ('Partner', 'Dependents'):
        return ('No %s on the account' if v == 'No' else 'Has %s on the account') % ('partner' if feature == 'Partner' else 'dependents')
    if feature in tw.ADDONS:
        name = {'OnlineSecurity': 'online security', 'OnlineBackup': 'online backup', 'DeviceProtection': 'device protection',
                'TechSupport': 'tech support', 'StreamingTV': 'streaming TV', 'StreamingMovies': 'streaming movies'}[feature]
        return 'No %s' % name if v == 'No' else ('Has %s' % name if v == 'Yes' else str(v))
    if feature == 'MultipleLines':
        return 'Multiple phone lines' if v == 'Yes' else 'Single phone line' if v == 'No' else 'No phone service'
    if feature == 'PhoneService':
        return 'Has phone service' if v == 'Yes' else 'No phone service'
    return '%s: %s' % (feature, v)


# ------------------------------------------------------------------ store
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path=None) -> sqlite3.Connection:
    con = sqlite3.connect(path or msg.db_path(), timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_ts TEXT NOT NULL, finished_ts TEXT, status TEXT NOT NULL,
            source TEXT, requested_by TEXT, params_json TEXT, summary_json TEXT, queue_run_id TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS agent_steps (
            run_id INTEGER NOT NULL, idx INTEGER NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL,
            started_ts TEXT, finished_ts TEXT, headline TEXT, detail_json TEXT, PRIMARY KEY (run_id, key));
        CREATE TABLE IF NOT EXISTS agent_customers (
            run_id INTEGER NOT NULL, customer_ref TEXT NOT NULL, first_name TEXT, customer_json TEXT, error TEXT,
            probability REAL, flagged INTEGER, monthly REAL, reasons_json TEXT, prioritised INTEGER DEFAULT 0, rank INTEGER,
            decision TEXT, action_name TEXT, risk_after REAL, options_json TEXT, case_id INTEGER,
            PRIMARY KEY (run_id, customer_ref));""")
    return con


def _step(con, run_id: int, key: str, status: str, headline: str | None = None, detail: Mapping | None = None) -> None:
    now = _now()
    sets, args = ['status=?'], [status]
    if status == 'running':
        sets.append('started_ts=COALESCE(started_ts, ?)'); args.append(now)
    if status in ('done', 'failed', 'skipped', 'waiting'):
        sets.append('finished_ts=?'); args.append(now)
    if headline is not None:
        sets.append('headline=?'); args.append(headline)
    if detail is not None:
        sets.append('detail_json=?'); args.append(json.dumps(detail, default=str))
    con.execute('UPDATE agent_steps SET %s WHERE run_id=? AND key=?' % ', '.join(sets), args + [run_id, key])
    con.commit()


def create_run(*, source: str, requested_by: str | None = None, params: Mapping | None = None, path=None) -> int:
    con = _connect(path)
    try:
        cur = con.execute('INSERT INTO agent_runs (started_ts, status, source, requested_by, params_json) VALUES (?,?,?,?,?)',
                          (_now(), 'running', source, requested_by, json.dumps(params or {})))
        rid = int(cur.lastrowid)
        for i, (key, *_rest) in enumerate(STAGES):
            con.execute('INSERT INTO agent_steps (run_id, idx, key, status) VALUES (?,?,?,?)', (rid, i, key, 'pending'))
        con.commit()
        return rid
    finally:
        con.close()


# ------------------------------------------------------------------ sources
def demo_records(meta: Mapping, *, sample: int = DEFAULT_SAMPLE, seed: int = 0, csv=CSV) -> list[dict]:
    """`sample` real customers from the hold-out split (seed-42 80/20, as train.py), with made-up first names and unknown account state."""
    from app.autopilot import NAMES
    from app.galaxy import load_holdout
    X, _ = load_holdout(csv)
    raw = pd.read_csv(csv, dtype=str, keep_default_na=False)
    rng = np.random.default_rng(seed)
    idx = rng.choice(X.index.to_numpy(), size=min(int(sample), len(X)), replace=False)
    df = raw.loc[np.sort(idx)].copy()
    df['first_name'] = [NAMES[i % len(NAMES)] for i in rng.permutation(len(df))]
    mapping = dict(pl.load_mapping(), first_name_column='first_name', account_state={})
    return pl.records_from_frame(df, mapping, meta)


def rows_records(rows: list[Mapping], meta: Mapping, mapping: Mapping | None = None) -> list[dict]:
    """Rows from an uploaded file (all values as text), mapped with pipeline.yaml."""
    if not rows:
        raise ValueError('the file has no rows')
    if len(rows) > MAX_ROWS:
        raise ValueError('at most %d rows per run, got %d' % (MAX_ROWS, len(rows)))
    df = pd.DataFrame([{k: '' if v is None else str(v) for k, v in r.items()} for r in rows])
    return pl.records_from_frame(df, mapping or pl.load_mapping(), meta)


# ------------------------------------------------------------------ the run
def _options(out: Mapping, bundle: Mapping) -> list[dict]:
    p = out['risk']['churn_probability']
    chosen = (out.get('action') or {}).get('name')
    rows = [{'name': c['name'], 'label': option_label(c['name'], bundle), 'changes': c['changes'], 'risk_after': p - c['risk_drop'],
             'risk_drop': c['risk_drop'], 'allowed': c['guardrails']['status'], 'needs': c['guardrails']['approvals_needed'],
             'blocked_because': c['guardrails']['violations'], 'confidence': c['confidence'], 'warnings': c['warnings'],
             'chosen': c['name'] == chosen} for c in out.get('candidates') or []]
    rows += [{'name': n['name'], 'label': option_label(n['name'], bundle), 'not_applicable': n['reason']} for n in out.get('not_applicable') or []
             if n['name'] != 'do_nothing']
    return rows


def run(records: Iterable[Mapping], bundle: dict, *, run_id: int | None = None, path=None, client=None, source: str = '',
        requested_by: str | None = None, max_twin: int = DEFAULT_MAX_TWIN, recommender: Callable | None = None) -> dict:
    """Run every stage in order (synchronously). Returns the finished run. A failure marks the current step and the run 'failed'."""
    records = list(records)
    if run_id is None:
        run_id = create_run(source=source, requested_by=requested_by, params={'max_twin': max_twin, 'records': len(records)}, path=path)
    meta = bundle['meta']
    recommend = recommender or rec.recommend
    con = _connect(path)
    current = 'ingest'
    try:
        # 1 ingest --------------------------------------------------------------------------------
        _step(con, run_id, 'ingest', 'running')
        good, invalid = [], []
        for r in records:
            if not r.get('error'):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        tw.CustomerTwin(r['customer'], meta)               # coherence + the serving validator
                except ValueError as e:
                    r = dict(r, error=str(e))
            (invalid if r.get('error') else good).append(r)
        for r in invalid:
            con.execute('INSERT OR REPLACE INTO agent_customers (run_id, customer_ref, first_name, error) VALUES (?,?,?,?)',
                        (run_id, r['customer_ref'] or '(no id)', r.get('first_name'), r['error']))
        con.commit()
        _step(con, run_id, 'ingest', 'done', '%d customers read · %d usable%s' % (
            len(records), len(good), ' · %d rejected with a reason' % len(invalid) if invalid else ''),
            {'read': len(records), 'usable': len(good), 'rejected': len(invalid),
             'rejected_examples': [{'ref': r['customer_ref'], 'reason': r['error']} for r in invalid[:5]]})

        # 2 score ---------------------------------------------------------------------------------
        current = 'score'
        _step(con, run_id, 'score', 'running')
        threshold, _src, placeholder = cs.get_threshold(meta, bundle.get('config'))
        if good:
            frame = pd.DataFrame([r['customer'] for r in good])
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                pred = cs.predict(frame, bundle)
                reasons = cs.top_reasons(frame, bundle)
            p = pred['churn_probability'].to_numpy(dtype=float)
            flagged = pred['flagged'].to_numpy(dtype=bool)
        else:
            frame, p, flagged, reasons = pd.DataFrame(), np.array([]), np.array([], dtype=bool), pd.DataFrame()
        monthly = np.array([float(r['customer']['MonthlyCharges']) for r in good])
        for i, r in enumerate(good):
            rs = [{'text': plain_reason(x['feature'], x['value']), 'feature': x['feature']} for x in reasons.loc[i, 'reasons']] if i in reasons.index else []
            con.execute("""INSERT OR REPLACE INTO agent_customers (run_id, customer_ref, first_name, customer_json, probability, flagged, monthly, reasons_json)
                           VALUES (?,?,?,?,?,?,?,?)""", (run_id, r['customer_ref'], r.get('first_name'), json.dumps(r['customer'], default=float),
                                                         float(p[i]), int(flagged[i]), float(monthly[i]), json.dumps(rs)))
        con.commit()
        at_risk = float((p * monthly).sum()) if len(p) else 0.0
        _step(con, run_id, 'score', 'done', '%d of %d above the alert line · $%s a month at risk' % (int(flagged.sum()), len(good), format(round(at_risk), ',')),
              {'scored': len(good), 'above_alert_line': int(flagged.sum()), 'expected_leavers': float(p.sum()) if len(p) else 0.0,
               'monthly_revenue': float(monthly.sum()) if len(monthly) else 0.0, 'monthly_revenue_at_risk': at_risk,
               'alert_line': threshold, 'alert_line_is_placeholder': placeholder})

        # 3 prioritise ----------------------------------------------------------------------------
        current = 'prioritise'
        _step(con, run_id, 'prioritise', 'running')
        order = [int(i) for i in np.argsort(-p, kind='stable') if flagged[i]]
        chosen, deferred = order[:max_twin], order[max_twin:]
        for rank, i in enumerate(chosen, 1):
            con.execute('UPDATE agent_customers SET prioritised=1, rank=? WHERE run_id=? AND customer_ref=?', (rank, run_id, good[i]['customer_ref']))
        con.commit()
        _step(con, run_id, 'prioritise', 'done', '%d customers to work on, most at risk first%s' % (
            len(chosen), ' · %d wait for the next run' % len(deferred) if deferred else ''),
            {'prioritised': len(chosen), 'deferred': len(deferred), 'cap_per_run': max_twin, 'below_alert_line': int((~flagged).sum())})

        # 4 twin + 5 decide -----------------------------------------------------------------------
        current = 'twin'
        _step(con, run_id, 'twin', 'running', 'Testing 0 of %d customers' % len(chosen), {'done': 0, 'total': len(chosen)})
        outs: dict[str, dict] = {}
        tried = Counter()
        for k, i in enumerate(chosen, 1):
            r = good[i]
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                out = recommend(r['customer'], bundle, r.get('account_state'))
            outs[json.dumps(r['customer'], sort_keys=True, default=float)] = out
            opts = _options(out, bundle)
            tried.update(o['name'] for o in opts if 'not_applicable' not in o)
            best = out.get('action') or max(out.get('candidates') or [{}], key=lambda c: c.get('risk_drop', -1), default=None)
            risk_after = out['risk']['churn_probability'] - best['risk_drop'] if best and 'risk_drop' in best else None
            con.execute('UPDATE agent_customers SET decision=?, action_name=?, risk_after=?, options_json=? WHERE run_id=? AND customer_ref=?',
                        (out['decision'], (out.get('action') or {}).get('name'), risk_after, json.dumps(opts, default=str), run_id, r['customer_ref']))
            con.commit()
            if k % 5 == 0 or k == len(chosen):
                _step(con, run_id, 'twin', 'running', 'Testing %d of %d customers' % (k, len(chosen)), {'done': k, 'total': len(chosen)})
        _step(con, run_id, 'twin', 'done', '%d customers tested · %d option runs on their twins' % (len(chosen), sum(tried.values())),
              {'done': len(chosen), 'total': len(chosen), 'option_runs': dict(tried), 'label': tw.INTERPRETATION})
        current = 'decide'
        _step(con, run_id, 'decide', 'running')
        decisions = Counter(o['decision'] for o in outs.values())
        actions = Counter(option_label(o['action']['name'], bundle) for o in outs.values() if o.get('action'))
        parts = ['%d %s' % (n, DECISION_PHRASE[d][n != 1]) for d, n in decisions.most_common()]
        _step(con, run_id, 'decide', 'done', ' · '.join(parts) or 'Nothing to decide', {'decisions': dict(decisions), 'options_chosen': dict(actions)})

        # 6 queue ---------------------------------------------------------------------------------
        current = 'queue'
        _step(con, run_id, 'queue', 'running')
        queued = [good[i] for i in chosen]
        memo = lambda customer, b, state: outs[json.dumps(customer, sort_keys=True, default=float)]
        res = pl.ingest(queued, bundle, client=client, path=path, source='agent run #%d' % run_id, recommender=memo) if queued else \
            {'run_id': None, 'counts': {}, 'skipped_open_case': []}
        con.execute('UPDATE agent_runs SET queue_run_id=? WHERE id=?', (res['run_id'], run_id))
        if res['run_id']:
            for c in con.execute('SELECT id, customer_ref FROM cases WHERE run_id=?', (res['run_id'],)).fetchall():
                con.execute('UPDATE agent_customers SET case_id=? WHERE run_id=? AND customer_ref=?', (c['id'], run_id, c['customer_ref']))
        con.commit()
        cnt = res['counts']
        _step(con, run_id, 'queue', 'done', '%d cases created · %d waiting for approval · %d messages drafted%s' % (
            sum(cnt.values()), cnt.get('pending_approval', 0), cnt.get('ready', 0),
            ' · %d already had an open case' % len(res['skipped_open_case']) if res['skipped_open_case'] else ''),
            {'counts': cnt, 'skipped_open_case': res['skipped_open_case']})

        # 7 + 8 are human steps -------------------------------------------------------------------
        _step(con, run_id, 'approve', 'waiting', 'Waiting for a person')
        _step(con, run_id, 'handoff', 'waiting', 'Waiting for approved messages')
        summary = {'read': len(records), 'usable': len(good), 'above_alert_line': int(flagged.sum()), 'prioritised': len(chosen),
                   'decisions': dict(decisions), 'monthly_revenue_at_risk': at_risk, 'queued': cnt}
        con.execute("UPDATE agent_runs SET status='done', finished_ts=?, summary_json=? WHERE id=?", (_now(), json.dumps(summary), run_id))
        con.commit()
    except Exception as e:
        con.rollback()
        _step(con, run_id, current, 'failed', 'Failed: %s' % e)
        for key in STAGE_KEYS[STAGE_KEYS.index(current) + 1:]:
            _step(con, run_id, key, 'skipped')
        con.execute("UPDATE agent_runs SET status='failed', finished_ts=?, error=? WHERE id=?", (_now(), '%s: %s' % (type(e).__name__, e), run_id))
        con.commit()
        if not isinstance(e, ValueError):
            traceback.print_exc()
    finally:
        con.close()
    return get_run(run_id, path=path)


def start(records: list[Mapping], bundle: dict, *, path=None, client=None, source: str = '', requested_by: str | None = None,
          max_twin: int = DEFAULT_MAX_TWIN, recommender: Callable | None = None, background: bool = True) -> dict:
    """Create a run and execute it on a background thread. Only one run at a time."""
    with _LOCK:
        if _ACTIVE['run_id'] is not None:
            raise AgentBusy('agent run #%s is still running' % _ACTIVE['run_id'])
        rid = create_run(source=source, requested_by=requested_by, params={'max_twin': max_twin, 'records': len(records)}, path=path)
        _ACTIVE['run_id'] = rid

    def work():
        try:
            run(records, bundle, run_id=rid, path=path, client=client, source=source, requested_by=requested_by, max_twin=max_twin,
                recommender=recommender)
        finally:
            with _LOCK:
                _ACTIVE['run_id'] = None

    if background:
        threading.Thread(target=work, daemon=True, name='agent-run-%d' % rid).start()
    else:
        work()
    return get_run(rid, path=path)


def mark_interrupted(path=None) -> int:
    """At start-up: a run left 'running' by a previous process will never finish. Mark it failed."""
    con = _connect(path)
    try:
        cur = con.execute("UPDATE agent_runs SET status='failed', finished_ts=?, error='interrupted (the server stopped during the run)' "
                          "WHERE status='running'", (_now(),))
        con.execute("UPDATE agent_steps SET status='skipped' WHERE status IN ('running','pending') AND run_id IN "
                    "(SELECT id FROM agent_runs WHERE error LIKE 'interrupted%')")
        con.commit()
        return cur.rowcount
    finally:
        con.close()


# ------------------------------------------------------------------ reading
def _queue_counts(con, queue_run_id: str | None) -> dict:
    if not queue_run_id:
        return {}
    try:
        return {r['status']: r['n'] for r in con.execute('SELECT status, COUNT(*) AS n FROM cases WHERE run_id=? GROUP BY status', (queue_run_id,))}
    except sqlite3.OperationalError:          # cases table not created yet
        return {}


def _human_steps(steps: list[dict], q: Mapping, run_status: str) -> None:
    """Approval and hand-off are live: their state comes from the review queue, not from the agent."""
    if run_status != 'done':
        return
    pending, rejected = q.get('pending_approval', 0), q.get('rejected', 0)
    need_name, ready, exported = q.get('needs_first_name', 0), q.get('ready', 0), q.get('exported', 0)
    approved = need_name + ready + exported
    for s in steps:
        if s['key'] == 'approve':
            s['status'] = 'waiting' if pending else 'done'
            s['headline'] = '%d waiting · %d approved · %d rejected' % (pending, approved, rejected)
            s['detail'] = {'waiting': pending, 'approved': approved, 'rejected': rejected, 'needs_first_name': need_name}
        elif s['key'] == 'handoff':
            s['status'] = 'waiting' if ready else ('done' if exported else 'waiting')
            s['headline'] = '%d ready for the CRM · %d handed off' % (ready, exported)
            s['detail'] = {'ready': ready, 'exported': exported}


def get_run(run_id: int, *, path=None) -> dict:
    con = _connect(path)
    try:
        r = con.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if r is None:
            raise RunNotFound('no agent run #%s' % run_id)
        steps = []
        for s in con.execute('SELECT * FROM agent_steps WHERE run_id=? ORDER BY idx', (run_id,)):
            key = s['key']
            _k, title, what, kind = STAGES[STAGE_KEYS.index(key)]
            steps.append({'key': key, 'title': title, 'what': what, 'kind': kind, 'status': s['status'], 'headline': s['headline'],
                          'started_ts': s['started_ts'], 'finished_ts': s['finished_ts'], 'detail': json.loads(s['detail_json'] or 'null')})
        q = _queue_counts(con, r['queue_run_id'])
        _human_steps(steps, q, r['status'])
        return {'id': r['id'], 'status': r['status'], 'source': r['source'], 'requested_by': r['requested_by'], 'started_ts': r['started_ts'],
                'finished_ts': r['finished_ts'], 'error': r['error'], 'params': json.loads(r['params_json'] or '{}'),
                'summary': json.loads(r['summary_json'] or 'null'), 'queue': q, 'steps': steps,
                'labels': {'twin': tw.INTERPRETATION}}
    finally:
        con.close()


def list_runs(*, limit: int = 20, path=None) -> list[dict]:
    con = _connect(path)
    try:
        return [{'id': r['id'], 'status': r['status'], 'source': r['source'], 'requested_by': r['requested_by'], 'started_ts': r['started_ts'],
                 'finished_ts': r['finished_ts'], 'summary': json.loads(r['summary_json'] or 'null'), 'error': r['error']}
                for r in con.execute('SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?', (int(limit),))]
    finally:
        con.close()


def _case_status(con, case_id) -> str | None:
    if case_id is None:
        return None
    row = con.execute('SELECT status FROM cases WHERE id=?', (case_id,)).fetchone()
    return row['status'] if row else None


def _customer_row(con, r: sqlite3.Row, bundle: Mapping | None) -> dict:
    return {'ref': r['customer_ref'], 'first_name': r['first_name'], 'error': r['error'], 'probability': r['probability'],
            'above_alert_line': None if r['flagged'] is None else bool(r['flagged']), 'monthly': r['monthly'],
            'reasons': json.loads(r['reasons_json'] or '[]'), 'prioritised': bool(r['prioritised']), 'rank': r['rank'],
            'decision': r['decision'], 'decision_text': DECISION_TEXT.get(r['decision']), 'action_name': r['action_name'],
            'action_label': option_label(r['action_name'], bundle), 'risk_after': r['risk_after'], 'case_id': r['case_id'],
            'case_status': _case_status(con, r['case_id'])}


def run_customers(run_id: int, *, scope: str = 'all', bundle: Mapping | None = None, path=None) -> list[dict]:
    """scope: all | prioritised | above (above the alert line) | rejected (rows that could not be used)."""
    where = {'all': 'error IS NULL', 'prioritised': 'prioritised=1', 'above': 'flagged=1', 'rejected': 'error IS NOT NULL'}.get(scope)
    if where is None:
        raise ValueError('scope must be one of all, prioritised, above, rejected')
    con = _connect(path)
    try:
        get_run_row = con.execute('SELECT id FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if get_run_row is None:
            raise RunNotFound('no agent run #%s' % run_id)
        rows = con.execute('SELECT * FROM agent_customers WHERE run_id=? AND %s ORDER BY probability DESC' % where, (run_id,)).fetchall()
        return [_customer_row(con, r, bundle) for r in rows]
    finally:
        con.close()


def customer_detail(run_id: int, ref: str, *, bundle: Mapping | None = None, path=None) -> dict:
    con = _connect(path)
    try:
        r = con.execute('SELECT * FROM agent_customers WHERE run_id=? AND customer_ref=?', (run_id, ref)).fetchone()
        if r is None:
            raise RunNotFound('customer %s is not in agent run #%s' % (ref, run_id))
        d = _customer_row(con, r, bundle)
        d['customer'] = json.loads(r['customer_json']) if r['customer_json'] else None
        d['options'] = json.loads(r['options_json'] or '[]')
        d['twin_label'] = tw.INTERPRETATION
    finally:
        con.close()
    d['case'] = None
    if d['case_id'] is not None:
        c = pl.get_case(d['case_id'], path=path)
        d['case'] = {k: c.get(k) for k in ('id', 'status', 'first_name', 'message', 'message_source', 'approved_by', 'note', 'events',
                                           'approvals_needed', 'action_name')}
    return d
