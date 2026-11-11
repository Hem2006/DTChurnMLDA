"""The hand-off chain between the four people, with separation of duties enforced here (not in the screens).

    analyst --submit--> manager --approve--> compliance --clear--> CRM export (by compliance)
                           |  \\--send back--> analyst
                           \\--reject / compliance reject--> closed
    vulnerable / escalated accounts (status 'escalated') go straight to compliance, who record a care-team hand-off.

Rules (HTTP 403 SeparationError when broken):
  * the manager who approves a case cannot be the analyst who submitted it;
  * the compliance officer who clears a message cannot be the person who submitted or approved the case;
  * only a cleared message can be exported by the signed-in workflow;
  * the admin has no case action at all.
This adds no decision logic: the offer, the guardrails and the message come from app.recommend / app.guardrails / app.message,
exactly as before. Every step is written to case_events with the person's name.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Mapping

from app import pipeline as pl

STAGES = {
    'analyst': 'With the analyst',
    'manager': 'Waiting for the manager',
    'name': 'Needs the first name',
    'compliance': 'Waiting for compliance',
    'crm_ready': 'Cleared, ready for the CRM',
    'care': 'Protected account: with compliance',
    'closed': 'Closed',
}


class SeparationError(PermissionError):
    """The same person tried to do two steps of one case (HTTP 403)."""


def stage_of(c: Mapping) -> str:
    s = c['status']
    if s == 'pending_approval':
        return 'manager' if c.get('submitted_uid') else 'analyst'
    if s == 'needs_first_name':
        return 'name'
    if s == 'ready':
        return 'crm_ready' if c.get('cleared_uid') else 'compliance'
    if s == 'escalated':
        return 'care'
    return 'closed'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _note(text, what='a note', required=False, limit=500) -> str | None:
    t = (text or '').strip()
    if required and not t:
        raise ValueError('%s is required' % what)
    if len(t) > limit:
        raise ValueError('%s is too long (max %d characters)' % (what, limit))
    return t or None


def _row(con, case_id):
    return pl._get(con, case_id)


def _distinct(row, user, others: tuple[str, ...], doing: str):
    for col in others:
        if row[col] and row[col] == user['id']:
            raise SeparationError('separation of duties: %s already did the earlier step on case #%s, so they cannot also %s it'
                                  % (user['name'], row['id'], doing))


# ------------------------------------------------------------------ analyst
def submit(case_id: int, user: Mapping, note: str | None, rules, *, first_name: str | None = None, path=None) -> dict:
    """The analyst hands a case that needs a person to the manager."""
    note = _note(note, 'a note for the manager', required=True)
    con = pl._connect(path)
    try:
        row = _row(con, case_id)
        if row['status'] != 'pending_approval' or row['submitted_uid']:
            raise pl.CaseError('case #%s is %s%s; only a case still with the analyst can be sent to the manager'
                               % (case_id, row['status'], ' (already sent)' if row['submitted_uid'] else ''))
        first = pl.msg.validate_first_name(first_name) if first_name else None
        con.execute("UPDATE cases SET submitted_by=?, submitted_uid=?, submitted_ts=?, submit_note=?, first_name=COALESCE(?, first_name), updated_ts=? "
                    "WHERE id=? AND status='pending_approval' AND submitted_uid IS NULL", (user['name'], user['id'], _now(), note, first, _now(), case_id))
        pl._event(con, case_id, 'submitted_to_manager', user['name'], note)
        con.commit()
        return pl._public(_row(con, case_id))
    finally:
        con.close()


# ------------------------------------------------------------------ manager
def send_back(case_id: int, user: Mapping, reason: str, *, path=None) -> dict:
    reason = _note(reason, 'a reason', required=True)
    con = pl._connect(path)
    try:
        row = _row(con, case_id)
        if row['status'] != 'pending_approval' or not row['submitted_uid']:
            raise pl.CaseError('case #%s is not waiting for the manager' % case_id)
        con.execute("UPDATE cases SET submitted_by=NULL, submitted_uid=NULL, submitted_ts=NULL, submit_note=NULL, note=?, updated_ts=? WHERE id=?",
                    (reason, _now(), case_id))
        pl._event(con, case_id, 'sent_back_to_analyst', user['name'], reason)
        con.commit()
        return pl._public(_row(con, case_id))
    finally:
        con.close()


def approve(case_id: int, user: Mapping, rules, *, first_name: str | None = None, client=None, path=None) -> dict:
    con = pl._connect(path)
    try:
        row = _row(con, case_id)
        if row['status'] != 'pending_approval':
            raise pl.CaseError('case #%s is %s; only pending_approval cases can be approved' % (case_id, row['status']))
        if not row['submitted_uid']:
            raise pl.CaseError('case #%s has not been sent to the manager by an analyst yet' % case_id)
        _distinct(row, user, ('submitted_uid',), 'approve')
    finally:
        con.close()
    out = pl.approve(case_id, user['name'], rules, first_name=first_name, client=client, path=path)
    con = pl._connect(path)
    try:
        con.execute('UPDATE cases SET approved_uid=? WHERE id=?', (user['id'], case_id))
        con.commit()
        return pl._public(_row(con, case_id))
    finally:
        con.close()


def name_it(case_id: int, user: Mapping, first_name: str, rules, *, client=None, path=None) -> dict:
    """Supply the customer's first name once approved (analyst or manager)."""
    return pl.provide_first_name(case_id, first_name, rules, client=client, path=path)


def reject(case_id: int, user: Mapping, reason: str, *, path=None) -> dict:
    """Manager: a case waiting for them. Compliance: a message waiting to be cleared."""
    con = pl._connect(path)
    try:
        row = _row(con, case_id)
        st = stage_of(pl._public(row))
        if user['role'] == 'manager' and st != 'manager':
            raise pl.CaseError('case #%s is not waiting for the manager' % case_id)
        if user['role'] == 'compliance' and st != 'compliance':
            raise pl.CaseError('case #%s is not waiting for compliance' % case_id)
        if user['role'] == 'manager':
            _distinct(row, user, ('submitted_uid',), 'reject')
        else:
            _distinct(row, user, ('submitted_uid', 'approved_uid'), 'reject')
    finally:
        con.close()
    return pl.reject(case_id, user['name'], reason, path=path)


# ------------------------------------------------------------------ compliance
def clear(case_id: int, user: Mapping, note: str | None, rules, *, message: str | None = None, path=None) -> dict:
    """Compliance has checked the approved message (optionally corrected it; the same validator applies) and releases it for the CRM."""
    note = _note(note, 'a note')
    con = pl._connect(path)
    try:
        row = _row(con, case_id)
        if row['status'] != 'ready' or row['cleared_uid']:
            raise pl.CaseError('case #%s is %s; only an uncleared ready message can be cleared' % (case_id, row['status']))
        _distinct(row, user, ('submitted_uid', 'approved_uid'), 'clear')
    finally:
        con.close()
    if message is not None and message.strip():
        pl.edit_message(case_id, message.strip(), rules, edited_by=user['name'], path=path)
    con = pl._connect(path)
    try:
        cur = con.execute("UPDATE cases SET cleared_by=?, cleared_uid=?, cleared_ts=?, clear_note=?, updated_ts=? "
                          "WHERE id=? AND status='ready' AND cleared_uid IS NULL", (user['name'], user['id'], _now(), note, _now(), case_id))
        if not cur.rowcount:
            raise pl.CaseError('case #%s was changed by someone else; reload' % case_id)
        pl._event(con, case_id, 'cleared_by_compliance', user['name'], note)
        con.commit()
        return pl._public(_row(con, case_id))
    finally:
        con.close()


def export(user: Mapping, *, path=None) -> list[dict]:
    return pl.export_outbox(path=path, cleared_only=True, actor=user['name'])


def care_handoff(case_id: int, user: Mapping, note: str, *, path=None) -> dict:
    """A vulnerable / escalated account: compliance passes it to the care team. This system never contacts the customer."""
    note = _note(note, 'a note for the care team', required=True)
    con = pl._connect(path)
    try:
        row = _row(con, case_id)
        cur = con.execute("UPDATE cases SET status='care_handoff', care_by=?, care_uid=?, care_note=?, updated_ts=? WHERE id=? AND status='escalated'",
                          (user['name'], user['id'], note, _now(), case_id))
        if not cur.rowcount:
            raise pl.CaseError('case #%s is %s; only escalated accounts go to the care team' % (case_id, row['status']))
        pl._event(con, case_id, 'care_handoff', user['name'], note)
        con.commit()
        return pl._public(_row(con, case_id))
    finally:
        con.close()


# ------------------------------------------------------------------ reading
def board(*, limit: int = 500, path=None) -> dict:
    """Every case with its stage, newest first, plus the count per stage."""
    cases = pl.list_cases(limit=limit, path=path)
    counts: dict[str, int] = {k: 0 for k in STAGES}
    for c in cases:
        c['stage'] = stage_of(c)
        counts[c['stage']] += 1
    return {'stages': STAGES, 'counts': counts, 'cases': cases}


def audit(limit: int = 200, *, path=None) -> list[dict]:
    con = pl._connect(path)
    try:
        con.execute('CREATE TABLE IF NOT EXISTS auth_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, event TEXT NOT NULL, actor TEXT, detail TEXT)')
        a = [{'ts': r['ts'], 'event': r['event'], 'actor': r['actor'], 'detail': r['detail'], 'case_id': r['case_id'], 'customer_ref': r['customer_ref']}
             for r in con.execute('SELECT e.ts, e.event, e.actor, e.detail, e.case_id, c.customer_ref FROM case_events e '
                                  'LEFT JOIN cases c ON c.id = e.case_id ORDER BY e.id DESC LIMIT ?', (int(limit),))]
        b = [{'ts': r['ts'], 'event': r['event'], 'actor': r['actor'], 'detail': r['detail'], 'case_id': None, 'customer_ref': None}
             for r in con.execute('SELECT * FROM auth_events ORDER BY id DESC LIMIT ?', (int(limit),))]
        return sorted(a + b, key=lambda x: x['ts'], reverse=True)[:int(limit)]
    finally:
        con.close()


def log_auth(event: str, actor: str | None, detail: str | None = None, *, path=None) -> None:
    con = pl._connect(path)
    try:
        con.execute('CREATE TABLE IF NOT EXISTS auth_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, event TEXT NOT NULL, actor TEXT, detail TEXT)')
        con.execute('INSERT INTO auth_events (ts, event, actor, detail) VALUES (?,?,?,?)', (_now(), event, actor, detail))
        con.commit()
    finally:
        con.close()


def duty_violations(*, path=None) -> int:
    """Cases where one person did two of the steps. Always 0 while the rules above are enforced; the admin screen shows it."""
    con = pl._connect(path)
    try:
        return int(con.execute(
            'SELECT COUNT(*) FROM cases WHERE (submitted_uid IS NOT NULL AND submitted_uid = approved_uid) '
            'OR (cleared_uid IS NOT NULL AND (cleared_uid = approved_uid OR cleared_uid = submitted_uid))').fetchone()[0])
    finally:
        con.close()
