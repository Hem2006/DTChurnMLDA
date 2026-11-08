"""Actions: the galaxy proposes, a named person approves, n8n carries it out, n8n reports back.

  pending_approval  proposed from a customer's twin; nothing happens until a named person decides
  rejected          turned down (terminal)
  approved          approved; waiting for n8n (n8n is not configured, or has not been reached yet)
  sent              the signed webhook reached n8n; waiting for its report
  done / failed     n8n reported back through the callback
  delivery_failed   approved but the webhook could not be delivered (retry is possible)

Rules this module keeps:
  * The numbers in a proposal are recomputed on the server from the served model and the twin (the client only names the customer and
    the edit), and carry the "model sensitivity, not a causal estimate" label.
  * Only edits the model says lower the risk can be proposed (model sensitivity, so "nothing to act on" is still not a causal claim).
  * Guardrails apply (app.guardrails): a blocked edit cannot even be proposed; with the account state unknown (as it is in the galaxy)
    the proposal needs a human, which is what the approval step is.
  * The payload to n8n has the customer REF only: no name, email or phone. n8n joins contact details in the systems it talks to.
  * n8n never gets to choose what to offer. It receives the approved edit and carries it out. It sends no customer message from here;
    customer wording still comes from app.message (validated in code) if a workflow asks for it.
  * A timed-out delivery is reported as delivery_failed, but n8n may still have received it (its report is then accepted), and 'send again'
    can run a workflow twice: workflows should be safe to repeat, or dedupe on action_id.
  * The webhook URL comes from the environment only (N8N_WEBHOOK_URL), never from a request. Outbound calls are signed with
    HMAC-SHA256 (N8N_SHARED_SECRET) and the callback must present the same secret. With either unset, n8n is "not configured":
    approved actions simply wait and the UI says so.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from app import galaxy as gx
from app import guardrails as gr
from app import message as msg
from app import twin as tw

STATUSES = ('pending_approval', 'rejected', 'approved', 'sent', 'done', 'failed', 'delivery_failed')
OPEN = ('pending_approval', 'approved', 'sent', 'delivery_failed')
SCHEMA = 1
MIN_RISK_DROP = 0.0005          # an edit must lower the model's risk by at least 0.05 points to be worth an approver's time
SECRET_HEADER = 'X-Evergreen-Secret'
SIGNATURE_HEADER = 'X-Evergreen-Signature'
NOTE = ('Model sensitivity, not a causal estimate. The suggested change is what the served model reacts to; whether it keeps the '
        'customer is not known. Account state (vulnerable, contacts, discount used) is unknown here, so a person must approve.')


class ActionError(Exception):
    """The action is not in a state that allows this (HTTP 409)."""


class ActionNotFound(ActionError):
    """HTTP 404."""


# ------------------------------------------------------------------ config
def config() -> dict:
    url = (os.environ.get('N8N_WEBHOOK_URL') or '').strip()
    secret = os.environ.get('N8N_SHARED_SECRET') or ''
    if url and urlparse(url).scheme not in ('http', 'https'):
        url = ''                                                           # file:, ftp: ... are never followed
    return {'url': url, 'secret': secret, 'configured': bool(url and secret),
            'public_url': (os.environ.get('EVERGREEN_PUBLIC_URL') or 'http://localhost:8000').rstrip('/'),
            'timeout': float(os.environ.get('N8N_TIMEOUT', '15'))}


def status() -> dict:
    c = config()
    host = urlparse(c['url']).netloc if c['url'] else None
    return {'configured': c['configured'], 'webhook_host': host, 'has_secret': bool(c['secret']), 'callback_base': c['public_url'],
            'hint': None if c['configured'] else 'n8n is not connected: set N8N_WEBHOOK_URL and N8N_SHARED_SECRET (see n8n/README.md). '
                                                  'Approved actions wait until it is.'}


def secret_ok(presented: str | None) -> bool:
    s = config()['secret']
    return bool(s and presented and hmac.compare_digest(presented, s))


def sign(body: bytes, secret: str) -> str:
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ store
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path=None) -> sqlite3.Connection:
    con = sqlite3.connect(path or msg.db_path())
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, updated_ts TEXT NOT NULL, node_id INTEGER NOT NULL,
            customer_ref TEXT NOT NULL, status TEXT NOT NULL, changes_json TEXT NOT NULL, changes_text TEXT NOT NULL,
            risk_before REAL NOT NULL, risk_after REAL NOT NULL, drivers_json TEXT, guardrail_status TEXT, approvals_json TEXT,
            customer_json TEXT, requested_by TEXT NOT NULL, decided_by TEXT, decided_note TEXT, detail TEXT, external_ref TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT);
        CREATE INDEX IF NOT EXISTS actions_status ON actions (status);
        CREATE INDEX IF NOT EXISTS actions_node ON actions (node_id);
        CREATE TABLE IF NOT EXISTS action_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, action_id INTEGER NOT NULL, ts TEXT NOT NULL, event TEXT NOT NULL, actor TEXT, detail TEXT);""")
    return con


def _event(con, action_id: int, event: str, actor: str | None = None, detail: str | None = None) -> None:
    con.execute('INSERT INTO action_events (action_id, ts, event, actor, detail) VALUES (?,?,?,?,?)', (action_id, _now(), event, actor, detail))


def _public(row: sqlite3.Row, events: list | None = None) -> dict:
    d = dict(row)
    d['changes'] = json.loads(d.pop('changes_json'))
    d['drivers'] = json.loads(d.pop('drivers_json') or 'null') or []
    d['approvals_needed'] = json.loads(d.pop('approvals_json') or 'null') or []
    d['customer'] = json.loads(d.pop('customer_json') or 'null')
    d['risk_drop'] = d['risk_before'] - d['risk_after']
    d['interpretation'] = tw.INTERPRETATION
    if events is not None:
        d['events'] = events
    return d


def _actor(name: Any, what: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ActionError('%s must be a name of 1-80 characters' % what)
    return name.strip()


def _row(con, action_id: int) -> sqlite3.Row:
    row = con.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
    if row is None:
        raise ActionNotFound('action %s does not exist' % action_id)
    return row


def get_action(action_id: int, path=None) -> dict:
    with _connect(path) as con:
        row = _row(con, action_id)
        ev = [dict(e) for e in con.execute('SELECT ts, event, actor, detail FROM action_events WHERE action_id=? ORDER BY id', (action_id,))]
    return _public(row, ev)


def list_actions(status_filter: str | None = None, limit: int = 200, path=None) -> list[dict]:
    if status_filter is not None and status_filter not in STATUSES:
        raise ActionError('unknown status %r (allowed: %s)' % (status_filter, list(STATUSES)))
    q, args = 'SELECT * FROM actions', []
    if status_filter:
        q += ' WHERE status=?'; args.append(status_filter)
    with _connect(path) as con:
        return [_public(r) for r in con.execute(q + ' ORDER BY id DESC LIMIT ?', (*args, int(limit)))]


def summary(path=None) -> dict:
    with _connect(path) as con:
        counts = {s: n for s, n in con.execute('SELECT status, COUNT(*) FROM actions GROUP BY status')}
    return {s: counts.get(s, 0) for s in STATUSES}


# ------------------------------------------------------------------ the flow
def propose(bundle: dict, node_id: int, changes: Mapping[str, Any], requested_by: str, path=None) -> dict:
    """Recompute the twin on the server and queue the edit for approval. Raises tw.TwinError (422) for a bad edit, ActionError (409) otherwise."""
    who = _actor(requested_by, 'requested_by')
    changes = dict(changes or {})
    if not changes:
        raise ActionError('propose at least one change')
    res = gx.twin_node(bundle, node_id, changes)                        # validates the edit, scores the served model
    if res['risk_drop'] <= MIN_RISK_DROP:
        raise ActionError('the model sees no reduction in churn risk for this edit (%+.1f pts): there is nothing to act on' % (-res['risk_drop'] * 100))
    g = gx.build(bundle)
    check = gr.check_action({'discount_pct': changes.get('discount_pct', 0)}, None, bundle['guardrails'])
    if check.status == 'blocked':
        raise ActionError('blocked by guardrails: ' + '; '.join(check.violations))
    node = g['payload']['nodes'][node_id]
    ref, cust = node['ref'], node['c']
    snap = {k: cust[k] for k in ('Contract', 'tenure', 'InternetService', 'MonthlyCharges')}
    text = ' · '.join(('Discount %s%%' % v) if k == 'discount_pct' else '%s → %s' % (k, v) for k, v in changes.items())
    with _connect(path) as con:
        open_ = con.execute('SELECT id, status FROM actions WHERE node_id=? AND status IN (%s) LIMIT 1' % ','.join('?' * len(OPEN)),
                            (node_id, *OPEN)).fetchone()
        if open_:
            raise ActionError('%s already has an open action (#%d, %s)' % (ref, open_['id'], open_['status']))
        cur = con.execute("""INSERT INTO actions (ts, updated_ts, node_id, customer_ref, status, changes_json, changes_text, risk_before, risk_after,
                             drivers_json, guardrail_status, approvals_json, customer_json, requested_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (_now(), _now(), node_id, ref, 'pending_approval', json.dumps(changes), text, res['baseline_risk'], res['risk_after'],
                           json.dumps(res['drivers'][:3]), check.status, json.dumps(check.approvals_needed),
                           json.dumps(snap), who))
        aid = cur.lastrowid
        _event(con, aid, 'proposed', who, text)
    return get_action(aid, path)


def _payload(row: dict, c: dict) -> dict:
    return {'event': 'action.approved', 'schema': SCHEMA, 'action_id': row['id'], 'customer_ref': row['customer_ref'],
            'kind': 'retention_offer', 'proposed_change': row['changes'], 'proposed_change_text': row['changes_text'],
            'risk': {'before': row['risk_before'], 'after': row['risk_after'], 'drop': row['risk_drop']},
            'top_reasons': [{'feature': d['feature'], 'value': d['value'], 'push': d['push']} for d in row['drivers']],
            'customer': row['customer'], 'requested_by': row['requested_by'], 'approved_by': row['decided_by'],
            'guardrail_status': row['guardrail_status'], 'labels': {'interpretation': tw.INTERPRETATION, 'note': NOTE},
            'callback': {'url': '%s/actions/%d/callback' % (c['public_url'], row['id']), 'secret_header': SECRET_HEADER}}


def _post(url: str, body: bytes, secret: str, timeout: float, event: str) -> None:
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json', SIGNATURE_HEADER: sign(body, secret), 'X-Evergreen-Event': event, 'User-Agent': 'evergreen-churn/1'})
    with urllib.request.urlopen(req, timeout=timeout) as r:            # noqa: S310 (scheme checked in config())
        if not 200 <= r.status < 300:
            raise urllib.error.HTTPError(url, r.status, 'unexpected status', r.headers, None)


def _dispatch(action_id: int, path=None) -> dict:
    """Send an approved action to n8n. Never raises for delivery problems: they become status delivery_failed with the reason.
    The row is marked 'sent' BEFORE the call so a fast n8n report can never arrive for an action we have not recorded as sent."""
    c = config()
    with _connect(path) as con:
        row = _row(con, action_id)
        if row['status'] not in ('approved', 'delivery_failed'):
            raise ActionError('action %d is %s; only an approved action can be sent' % (action_id, row['status']))
        if not c['configured']:
            _event(con, action_id, 'waiting', None, 'n8n is not configured')
            body = None
        else:
            body = json.dumps(_payload(_public(row), c)).encode()
            con.execute("UPDATE actions SET status='sent', updated_ts=?, attempts=attempts+1, last_error=NULL WHERE id=?", (_now(), action_id))
    if body is not None:
        try:
            _post(c['url'], body, c['secret'], c['timeout'], 'action.approved')
            event, err = 'sent_to_n8n', None
        except (urllib.error.URLError, OSError, ValueError) as e:      # HTTPError, timeouts, refused connections
            event, err = 'delivery_failed', str(getattr(e, 'reason', e))[:300]
        with _connect(path) as con:
            if err is not None:
                con.execute("UPDATE actions SET status='delivery_failed', updated_ts=?, last_error=? WHERE id=? AND status='sent'", (_now(), err, action_id))
            _event(con, action_id, event, None, err)
    return get_action(action_id, path)


def approve(action_id: int, decided_by: str, note: str | None = None, path=None) -> dict:
    who = _actor(decided_by, 'decided_by')
    with _connect(path) as con:
        row = _row(con, action_id)
        if row['status'] != 'pending_approval':
            raise ActionError('action %d is %s, not pending approval' % (action_id, row['status']))
        con.execute("UPDATE actions SET status='approved', decided_by=?, decided_note=?, updated_ts=? WHERE id=?", (who, note, _now(), action_id))
        _event(con, action_id, 'approved', who, note)
    return _dispatch(action_id, path)


def reject(action_id: int, decided_by: str, reason: str, path=None) -> dict:
    who = _actor(decided_by, 'decided_by')
    if not isinstance(reason, str) or not reason.strip():
        raise ActionError('a reason is required')
    with _connect(path) as con:
        row = _row(con, action_id)
        if row['status'] != 'pending_approval':
            raise ActionError('action %d is %s, not pending approval' % (action_id, row['status']))
        con.execute("UPDATE actions SET status='rejected', decided_by=?, decided_note=?, updated_ts=? WHERE id=?", (who, reason.strip(), _now(), action_id))
        _event(con, action_id, 'rejected', who, reason.strip())
    return get_action(action_id, path)


def retry(action_id: int, path=None) -> dict:
    return _dispatch(action_id, path)


def callback(action_id: int, outcome: str, detail: str | None, external_ref: str | None, path=None) -> dict:
    """n8n's report. Only an action that was actually sent can be reported on, and only once."""
    if outcome not in ('done', 'failed'):
        raise ActionError("outcome must be 'done' or 'failed'")
    with _connect(path) as con:
        row = _row(con, action_id)
        if row['status'] not in ('sent', 'delivery_failed'):
            # delivery_failed is allowed: a timeout does not prove n8n never got the webhook, and its report is the truth
            raise ActionError('action %d is %s; a report is only accepted for an action that was sent to n8n' % (action_id, row['status']))
        con.execute('UPDATE actions SET status=?, detail=?, external_ref=?, updated_ts=? WHERE id=?', (outcome, detail, external_ref, _now(), action_id))
        _event(con, action_id, 'n8n_' + outcome, 'n8n', detail)
    return get_action(action_id, path)


# ------------------------------------------------------------------ inbound: what n8n can ask for
def digest(bundle: dict, top: int = 10, path=None) -> dict:
    """Facts for a scheduled n8n report: the most at-risk customers (refs only) and the state of the action queue."""
    g = gx.build(bundle)
    nodes = g['payload']['nodes']
    flagged = sorted((n for n in nodes if n['p'] >= g['threshold']), key=lambda n: -n['p'])
    open_nodes = {a['node_id'] for a in list_actions(limit=1000, path=path) if a['status'] in OPEN}
    return {'interpretation': tw.INTERPRETATION, 'summary': g['payload']['summary'], 'threshold': g['threshold'],
            'threshold_is_placeholder': g['payload']['threshold_is_placeholder'],
            'top_at_risk': [{'customer_ref': n['ref'], 'risk': n['p'], 'contract': n['c']['Contract'], 'tenure': n['c']['tenure'],
                             'monthly_charges': n['c']['MonthlyCharges'], 'has_open_action': n['id'] in open_nodes} for n in flagged[:max(1, min(top, 50))]],
            'actions': summary(path), 'note': NOTE}
