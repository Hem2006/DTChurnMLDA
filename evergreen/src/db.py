"""SQLite case queue and message log. One open case per customer (enforced by a partial unique index, not just by app code).

Statuses: pending_approval -> approved -> exported -> sent, or pending_approval -> rejected. Escalated cases are closed by a specialist (escalation_resolved). 'escalated' and 'no_action' are closed on creation.
Open = pending_approval or approved. Nothing is sent from here: export writes rows to outbox.csv for whoever sends them.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.data import ROOT

OPEN = ('pending_approval', 'approved')
OUTBOX_FIELDS = ['case_id', 'customer_ref', 'first_name', 'offer', 'message', 'approved_by', 'approved_at', 'exported_at']


class CaseError(ValueError):
    pass


class DuplicateOpenCase(CaseError):
    pass


def db_path() -> Path:
    return Path(os.environ.get('EVERGREEN_DB', ROOT / 'evergreen.sqlite'))


def connect(path=None):
    con = sqlite3.connect(path or db_path())
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_ref TEXT NOT NULL, status TEXT NOT NULL, decision TEXT NOT NULL,
        offer_name TEXT, offer_json TEXT, first_name TEXT, message TEXT, message_source TEXT, probability REAL, guardrail_status TEXT,
        approvals_json TEXT, record_json TEXT NOT NULL, created_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT, decision_note TEXT, exported_at TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS one_open_case_per_customer ON cases(customer_ref) WHERE status IN ('pending_approval','approved');
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, ts TEXT NOT NULL, actor TEXT, event TEXT NOT NULL, note TEXT);
    CREATE TABLE IF NOT EXISTS message_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, customer_ref TEXT, first_name TEXT, offer_name TEXT,
        message TEXT NOT NULL, source TEXT NOT NULL, model TEXT, rejected_reason TEXT, rejected_text TEXT);
    """)
    if 'submitted_by' not in {r['name'] for r in con.execute('PRAGMA table_info(cases)')}:
        con.execute('ALTER TABLE cases ADD COLUMN submitted_by TEXT')
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _event(con, case_id, actor, event, note=None):
    con.execute('INSERT INTO events (case_id, ts, actor, event, note) VALUES (?,?,?,?,?)', (case_id, _now(), actor, event, note))


def create_case(record: dict, offer: dict | None = None, first_name: str | None = None, message: str | None = None,
                message_source: str | None = None, path=None, submitted_by: str | None = None) -> int:
    """Store a recommendation. recommend* decisions need an offer + message and open as pending_approval (a named human approves every one).
    escalate_to_human / no_action are stored closed. Raises DuplicateOpenCase if the customer already has an open case."""
    ref, decision = record.get('customer_ref'), record['decision']
    if not ref:
        raise CaseError('customer_ref is required to queue a case')
    if decision.startswith('recommend'):
        if not (offer and first_name and message):
            raise CaseError('a recommended case needs an offer, a first name and a validated message')
        status = 'pending_approval'
    else:
        status = 'escalated' if decision == 'escalate_to_human' else 'no_action'
    con = connect(path)
    try:
        try:
            cur = con.execute("""INSERT INTO cases (customer_ref, status, decision, offer_name, offer_json, first_name, message, message_source, probability,
                guardrail_status, approvals_json, record_json, created_at, submitted_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (ref, status, decision, offer['name'] if offer else None, json.dumps(offer) if offer else None, first_name, message, message_source,
                               record['risk']['probability'], (record.get('guardrail') or {}).get('status'),
                               json.dumps((record.get('guardrail') or {}).get('approvals_needed', [])), json.dumps(record, default=str), _now(), submitted_by))
        except sqlite3.IntegrityError:
            raise DuplicateOpenCase('customer %s already has an open case; decide it first' % ref) from None
        _event(con, cur.lastrowid, submitted_by, 'created', status)
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def list_cases(status: str | None = None, path=None, submitted_by: str | None = None) -> list[dict]:
    con = connect(path)
    where, args = [], []
    if status:
        where.append('status = ?'); args.append(status)
    if submitted_by:
        where.append('submitted_by = ?'); args.append(submitted_by)
    q = 'SELECT * FROM cases' + (' WHERE ' + ' AND '.join(where) if where else '')
    args = tuple(args)
    rows = [dict(r) for r in con.execute(q + ' ORDER BY id DESC', args)]
    con.close()
    return rows


def get_case(case_id: int, path=None) -> dict:
    con = connect(path)
    r = con.execute('SELECT * FROM cases WHERE id = ?', (case_id,)).fetchone()
    if r is None:
        con.close()
        raise CaseError('no case %s' % case_id)
    out = dict(r)
    out['events'] = [dict(e) for e in con.execute('SELECT * FROM events WHERE case_id = ? ORDER BY id', (case_id,))]
    con.close()
    return out


def _decide(case_id, actor, new_status, note, path):
    if not actor or not str(actor).strip():
        raise CaseError('a named approver is required')
    con = connect(path)
    try:
        r = con.execute('SELECT status, submitted_by FROM cases WHERE id = ?', (case_id,)).fetchone()
        if r is None:
            raise CaseError('no case %s' % case_id)
        if r['status'] != 'pending_approval':
            raise CaseError('case %s is %s, not pending_approval' % (case_id, r['status']))
        if r['submitted_by'] and r['submitted_by'].strip().lower() == actor.strip().lower():
            raise CaseError('you submitted this case, so someone else has to decide it')
        con.execute('UPDATE cases SET status=?, decided_by=?, decided_at=?, decision_note=? WHERE id=?', (new_status, actor.strip(), _now(), note, case_id))
        _event(con, case_id, actor.strip(), new_status, note)
        con.commit()
    finally:
        con.close()


def approve(case_id: int, approver: str, path=None):
    _decide(case_id, approver, 'approved', None, path)


def reject(case_id: int, approver: str, reason: str, path=None):
    if not reason or not str(reason).strip():
        raise CaseError('a rejection reason is required')
    _decide(case_id, approver, 'rejected', reason.strip(), path)


def _transition(case_id, actor, from_status, to_status, note, path, what):
    if not actor or not str(actor).strip():
        raise CaseError('a signed-in user is required')
    con = connect(path)
    try:
        r = con.execute('SELECT status FROM cases WHERE id = ?', (case_id,)).fetchone()
        if r is None:
            raise CaseError('no case %s' % case_id)
        if r['status'] != from_status:
            raise CaseError('case %s is %s, cannot %s' % (case_id, r['status'], what))
        con.execute('UPDATE cases SET status=?, decision_note=COALESCE(?, decision_note) WHERE id=?', (to_status, note, case_id))
        _event(con, case_id, actor.strip(), to_status, note)
        con.commit()
    finally:
        con.close()


def resolve_escalation(case_id: int, actor: str, note: str, path=None):
    """A specialist closes an escalated (do-not-contact) case with a note."""
    if not note or not note.strip():
        raise CaseError('a note is required to close an escalation')
    _transition(case_id, actor, 'escalated', 'escalation_resolved', note.strip(), path, 'resolve')


def mark_sent(case_id: int, actor: str, path=None):
    """The sender records that an exported message went out."""
    _transition(case_id, actor, 'exported', 'sent', None, path, 'mark as sent')


def export_outbox(outbox: Path | str, path=None, actor: str | None = None) -> list[dict]:
    """Append every approved case to outbox.csv and mark it exported. Nothing is sent."""
    con = connect(path)
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM cases WHERE status = 'approved' ORDER BY id")]
        if not rows:
            return []
        out, now = [], _now()
        for r in rows:
            out.append({'case_id': r['id'], 'customer_ref': r['customer_ref'], 'first_name': r['first_name'], 'offer': r['offer_name'], 'message': r['message'],
                        'approved_by': r['decided_by'], 'approved_at': r['decided_at'], 'exported_at': now})
        p = Path(outbox)
        p.parent.mkdir(parents=True, exist_ok=True)
        new = not p.exists()
        with open(p, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=OUTBOX_FIELDS)
            if new:
                w.writeheader()
            w.writerows(out)
        for r in rows:
            con.execute("UPDATE cases SET status='exported', exported_at=? WHERE id=?", (now, r['id']))
            _event(con, r['id'], actor, 'exported', str(p))
        con.commit()
        return out
    finally:
        con.close()


def log_message(customer_ref, first_name, offer_name, draft: dict, path=None) -> int:
    con = connect(path)
    try:
        cur = con.execute('INSERT INTO message_log (ts, customer_ref, first_name, offer_name, message, source, model, rejected_reason, rejected_text) VALUES (?,?,?,?,?,?,?,?,?)',
                          (_now(), customer_ref, first_name, offer_name, draft['message'], draft['source'], draft.get('model'), draft.get('rejected_reason'), draft.get('rejected_text')))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def summary(path=None) -> dict:
    con = connect(path)
    out = {r['status']: r['n'] for r in con.execute('SELECT status, COUNT(*) n FROM cases GROUP BY status')}
    con.close()
    return out
