"""Service-incident engine: bad experiences -> frustration -> model score -> segment -> self-healing plan.

Flow for one customer:
  1. Events are logged (outage, dropped call, unanswered support ...) or SIMULATED from a storyline.
  2. `frustration()` turns the events into a 0-1 score (time-decayed, late-night / work-impact / unresolved / repeat-contact
     modifiers). The weights are arbitrary ASSUMPTIONS in incidents.yaml, not findings: the Telco data has no service events.
  3. When the score reaches `trigger_level` the self-healing run starts: the calibrated churn model scores the customer, the
     SIMULATED uplift model says whether contact helps, and the customer lands in one of four segments:
        persuadable  at risk AND a contact is expected to help
        sure_thing   not flagged: stays anyway; fix the problem, do not spend an offer
        lost_cause   at risk but a contact is not expected to help
        sleeping_dog a contact is expected to make things WORSE; leave them alone
  4. A recovery plan is built. Internal tasks (engineering check, priority callback ...) are created automatically; anything that
     contacts the customer or offers something is decided by app.recommend / app.guardrails and may wait for a named approver.

The served model is never changed and never saw service events. Segments come from the model score and the SIMULATED uplift;
frustration decides WHEN to act and how urgently, not what the model thinks. Nothing is sent from here.
"""
from __future__ import annotations

import json
import math
import sqlite3
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

import churn_serving as cs
from app import guardrails as gr
from app import message as msg
from app import recommend as rec
from app import twin as tw
from app import uplift as up

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / 'incidents.yaml'
SIMULATED = 'SIMULATED'
NOTE = ('SIMULATED behaviour: frustration weights and segment cut-offs are arbitrary assumptions in incidents.yaml, and uplift is '
        'SIMULATED. The churn model never saw service events. None of this is a finding about real customers.')
SEGMENTS = ('persuadable', 'sure_thing', 'lost_cause', 'sleeping_dog')
LEVELS = ('calm', 'annoyed', 'angry')
REF_RE = msg.REF_RE


class IncidentError(ValueError):
    """Bad input to the incident engine (HTTP 422)."""


# ------------------------------------------------------------------ config
def load_config(path=None) -> dict:
    p = Path(path or CONFIG_FILE)
    cfg = yaml.safe_load(p.read_text(encoding='utf8')) or {}
    kinds = cfg.get('event_kinds') or {}
    if not kinds or any(not isinstance(v.get('weight'), (int, float)) or v['weight'] < 0 for v in kinds.values()):
        raise IncidentError('incidents.yaml: every event_kinds entry needs a non-negative weight')
    lv = cfg.get('levels') or {}
    if not (0 < lv.get('annoyed', -1) < lv.get('angry', -1) <= 1):
        raise IncidentError('incidents.yaml: levels need 0 < annoyed < angry <= 1')
    if cfg.get('trigger_level') not in ('annoyed', 'angry'):
        raise IncidentError('incidents.yaml: trigger_level must be annoyed or angry')
    for k in ('severity_scale', 'half_life_days', 'score_scale'):
        if not isinstance(cfg.get(k), (int, float)) or cfg[k] <= 0:
            raise IncidentError('incidents.yaml: %s must be a positive number' % k)
    if not isinstance((cfg.get('segmentation') or {}).get('uplift_margin'), (int, float)):
        raise IncidentError('incidents.yaml: segmentation.uplift_margin is required')
    bump = cfg.get('frustration_risk_bump')
    if bump is not None and (isinstance(bump, bool) or not isinstance(bump, (int, float)) or not 0 <= bump <= 1):
        raise IncidentError('incidents.yaml: frustration_risk_bump must be null or between 0 and 1')
    for name, s in (cfg.get('storylines') or {}).items():
        for e in s.get('events', []):
            if e.get('kind') not in kinds:
                raise IncidentError('incidents.yaml: storyline %s uses unknown kind %r' % (name, e.get('kind')))
    for k, v in kinds.items():
        if v.get('task') and v['task'] not in (cfg.get('tasks') or {}):
            raise IncidentError('incidents.yaml: event kind %s points at unknown task %r' % (k, v['task']))
    cfg['storylines'] = cfg.get('storylines') or {}
    cfg['tasks'] = cfg.get('tasks') or {}
    return cfg


# ------------------------------------------------------------------ store
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect(path=None) -> sqlite3.Connection:
    con = sqlite3.connect(path or msg.db_path())
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS inc_customers (
            ref TEXT PRIMARY KEY, first_name TEXT, customer_json TEXT NOT NULL, account_state_json TEXT, updated_ts TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inc_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL, occurred_at TEXT NOT NULL, kind TEXT NOT NULL,
            severity INTEGER NOT NULL, work_impact INTEGER NOT NULL DEFAULT 0, resolved INTEGER NOT NULL DEFAULT 0,
            note TEXT, source TEXT NOT NULL, logged_ts TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS inc_events_ref ON inc_events (ref);
        CREATE TABLE IF NOT EXISTS inc_recoveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL, ts TEXT NOT NULL, updated_ts TEXT NOT NULL, status TEXT NOT NULL,
            level TEXT NOT NULL, frustration REAL NOT NULL, segment TEXT, plan_json TEXT NOT NULL, closed_by TEXT, note TEXT);
        CREATE INDEX IF NOT EXISTS inc_recoveries_ref ON inc_recoveries (ref);""")
    return con


def _check_ref(ref) -> str:
    if not isinstance(ref, str) or not REF_RE.match(ref):
        raise IncidentError('customer_ref must be 1-64 characters: letters, digits, - or _')
    return ref


def _check_actor(name, what) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise IncidentError('%s must be a name of 1-80 characters' % what)
    return name.strip()


def register_customer(ref: str, customer: Mapping, bundle: dict, *, first_name: str | None = None,
                      account_state: Mapping | None = None, path=None) -> dict:
    """Store (or update) the customer's model inputs and account state. The row must be valid and coherent."""
    _check_ref(ref)
    tw.CustomerTwin(dict(customer), bundle['meta'])           # coherence + the serving validator (as /score does)
    if first_name is not None:
        first_name = msg.validate_first_name(first_name)
    with _connect(path) as con:
        con.execute("""INSERT INTO inc_customers (ref, first_name, customer_json, account_state_json, updated_ts) VALUES (?,?,?,?,?)
                       ON CONFLICT(ref) DO UPDATE SET first_name=excluded.first_name, customer_json=excluded.customer_json,
                       account_state_json=excluded.account_state_json, updated_ts=excluded.updated_ts""",
                    (ref, first_name, json.dumps(dict(customer)), json.dumps(dict(account_state)) if account_state is not None else None,
                     _now().isoformat()))
    return {'customer_ref': ref, 'first_name': first_name}


def _customer(con, ref: str) -> sqlite3.Row:
    row = con.execute('SELECT * FROM inc_customers WHERE ref=?', (ref,)).fetchone()
    if row is None:
        raise IncidentError('customer %s is not registered: register the customer (model inputs and account state) first' % ref)
    return row


def log_event(ref: str, kind: str, cfg: Mapping, *, severity: int = 3, occurred_at: str | datetime | None = None, work_impact: bool = False,
              resolved: bool = False, note: str | None = None, source: str = 'logged', logged_at: datetime | None = None, path=None) -> int:
    _check_ref(ref)
    if kind not in cfg['event_kinds']:
        raise IncidentError('unknown event kind %r (allowed: %s)' % (kind, sorted(cfg['event_kinds'])))
    if isinstance(severity, bool) or not isinstance(severity, int) or not 1 <= severity <= 5:
        raise IncidentError('severity must be a whole number from 1 to 5')
    when = occurred_at if isinstance(occurred_at, datetime) else (datetime.fromisoformat(occurred_at) if occurred_at else _now())
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)      # hour of day is kept as supplied (customer-local)
    if note is not None and len(note) > 300:
        raise IncidentError('note is limited to 300 characters')
    with _connect(path) as con:
        _customer(con, ref)
        cur = con.execute('INSERT INTO inc_events (ref, occurred_at, kind, severity, work_impact, resolved, note, source, logged_ts) VALUES (?,?,?,?,?,?,?,?,?)',
                          (ref, when.isoformat(), kind, severity, int(bool(work_impact)), int(bool(resolved)), note, source, (logged_at or _now()).isoformat()))
        return cur.lastrowid


def list_events(ref: str, *, path=None) -> list[dict]:
    with _connect(path) as con:
        rows = con.execute('SELECT * FROM inc_events WHERE ref=? ORDER BY occurred_at, id', (ref,)).fetchall()
    return [{**dict(r), 'work_impact': bool(r['work_impact']), 'resolved': bool(r['resolved'])} for r in rows]


def resolve_event(event_id: int, *, path=None) -> None:
    with _connect(path) as con:
        if con.execute('UPDATE inc_events SET resolved=1 WHERE id=?', (event_id,)).rowcount == 0:
            raise IncidentError('event %s not found' % event_id)


def reset_simulated(ref: str, *, path=None) -> int:
    """Remove this customer's SIMULATED events only (logged events are never touched) and close their open recovery as discarded."""
    with _connect(path) as con:
        n = con.execute("DELETE FROM inc_events WHERE ref=? AND source='simulated'", (ref,)).rowcount
        con.execute("UPDATE inc_recoveries SET status='discarded', updated_ts=?, note='simulated events were reset' WHERE ref=? AND status='open'",
                    (_now().isoformat(), ref))
    return n


# ------------------------------------------------------------------ frustration (SIMULATED behaviour model)
def _late_night(hour: int, m: Mapping) -> bool:
    s, e = m['start_hour'], m['end_hour']
    return hour >= s or hour < e if s > e else s <= hour < e


def _when(ev: Mapping) -> datetime:
    t = datetime.fromisoformat(ev['occurred_at'])
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def event_strength(ev: Mapping, cfg: Mapping, now: datetime) -> dict:
    """One event's contribution: weight x severity x modifiers x time decay, with the reasons spelled out."""
    t = _when(ev)
    age_days = max(0.0, (now - t).total_seconds() / 86400)
    mods = cfg['modifiers']
    parts, factor = [], 1.0
    if _late_night(t.hour, mods['late_night']):
        factor *= mods['late_night']['multiplier']
        parts.append('late night')
    if ev.get('work_impact'):
        factor *= mods['work_impact']
        parts.append('hit their work')
    if not ev.get('resolved'):
        factor *= mods['unresolved']
        parts.append('unresolved')
    decay = 0.5 ** (age_days / cfg['half_life_days'])
    strength = cfg['event_kinds'][ev['kind']]['weight'] * (ev['severity'] / cfg['severity_scale']) * factor * decay
    return {'id': ev.get('id'), 'kind': ev['kind'], 'occurred_at': t.isoformat(), 'strength': strength, 'modifiers': parts,
            'age_days': round(age_days, 2)}


def frustration(events: list[Mapping], cfg: Mapping, now: datetime | None = None) -> dict:
    """SIMULATED frustration from a customer's events: score in [0,1], level calm/annoyed/angry, and the events that drove it."""
    now = now or _now()
    rows = [event_strength(e, cfg, now) for e in events if _when(e) <= now]
    total = sum(r['strength'] for r in rows)
    rc, notes = cfg['repeat_contact'], []
    window = [e for e in events if e['kind'] in rc['kinds'] and 0 <= (now - _when(e)).total_seconds() <= rc['window_days'] * 86400]
    if len(window) >= rc['min_events']:
        total *= rc['multiplier']
        notes.append('%d contacts or failed calls in %d days: the customer keeps coming back' % (len(window), rc['window_days']))
    score = 1 - math.exp(-cfg['score_scale'] * total)
    lv = cfg['levels']
    level = 'angry' if score >= lv['angry'] else 'annoyed' if score >= lv['annoyed'] else 'calm'
    drivers = sorted(rows, key=lambda r: -r['strength'])[:5]
    return {'label': SIMULATED, 'score': score, 'level': level, 'n_events': len(rows), 'drivers': drivers, 'notes': notes,
            'thresholds': dict(lv), 'note': NOTE}


# ------------------------------------------------------------------ segmentation (model score + SIMULATED uplift)
def classify(p_churn: float, flagged: bool, uplift: float | None, margin: float) -> tuple[str | None, str]:
    """persuadable / sure_thing / lost_cause / sleeping_dog from the model's flag and the SIMULATED uplift."""
    if uplift is None:
        return None, 'The SIMULATED uplift model is not built (python -m app.uplift), so the customer cannot be segmented.'
    if uplift <= -margin:
        return 'sleeping_dog', 'SIMULATED uplift %+.3f: under the simulation assumptions a contact makes this customer MORE likely to leave.' % uplift
    if not flagged:
        return 'sure_thing', 'Churn probability %.3f is below the threshold: the model expects them to stay without an offer.' % p_churn
    if uplift >= margin:
        return 'persuadable', 'Flagged at %.3f and SIMULATED uplift is %+.3f: a contact is expected to help.' % (p_churn, uplift)
    return 'lost_cause', 'Flagged at %.3f but SIMULATED uplift is only %+.3f: a contact is not expected to change the outcome.' % (p_churn, uplift)


def _score(customer: Mapping, bundle: dict, frus: float, cfg: Mapping) -> tuple[dict, dict | None]:
    df = pd.DataFrame([dict(customer)])
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        pred = cs.predict(df, bundle).iloc[0]
        p = float(pred['churn_probability'])
        threshold = float(pred['threshold'])
        bump = cfg.get('frustration_risk_bump')
        p_used = min(1.0, p + bump * frus) if bump is not None else p
        risk = {'churn_probability': p, 'used_for_segment': p_used, 'frustration_bump_applied': bump is not None,
                'flagged': p_used >= threshold, 'threshold': threshold, 'threshold_source': pred['threshold_source'],
                'threshold_is_placeholder': bool(pred['threshold_is_placeholder'])}
        ub = bundle.get('uplift')
        uplift = None
        if ub is not None:
            u = up.score_uplift(df, ub, bundle['meta']).iloc[0]
            uplift = {'label': up.SIMULATED, 'simulated_uplift': float(u['simulated_uplift']), 'note': up.NOTE}
    return risk, uplift


def _tasks(events: list[Mapping], level: str, cfg: Mapping) -> list[dict]:
    """Internal work items, one per task kind, from open events first. These need no approval: nothing reaches the customer."""
    seen: dict[str, dict] = {}
    for e in sorted((e for e in events if not e.get('resolved')), key=_when, reverse=True):
        t = cfg['event_kinds'][e['kind']].get('task')
        if t and t not in seen:
            seen[t] = {'task': t, 'description': cfg['tasks'][t], 'why': 'Open %s event on %s' % (e['kind'].replace('_', ' '), _when(e).date().isoformat()),
                       'status': 'auto_created', 'event_ids': []}
        if t:
            seen[t]['event_ids'].append(e.get('id'))
    if level == 'angry' and not seen:
        seen['senior_review'] = {'task': 'senior_review', 'description': cfg['tasks']['senior_review'], 'why': 'Customer is angry with no open event to fix',
                                 'status': 'auto_created', 'event_ids': []}
    return list(seen.values())


def recommendation(customer: Mapping, bundle: dict, state: Mapping | None) -> dict:
    """app.recommend.recommend(), memoised on the bundle. It runs the digital twin over every scenario (seconds per customer) and depends only
    on the customer, the account state, the guardrail limits and the business numbers, never on service events, so it is safe to reuse."""
    cache = bundle.setdefault('_recommendation_cache', {})
    key = json.dumps([dict(customer), state, {k: v for k, v in bundle['guardrails'].items() if k != 'pressure_language'},
                      bundle.get('config')], sort_keys=True, default=str)
    if key not in cache:
        cache[key] = rec.recommend(dict(customer), bundle, dict(state) if state is not None else None)
    return cache[key]


def _contact(segment: str | None, customer: Mapping, bundle: dict, state: Mapping | None) -> dict:
    """What, if anything, may reach the customer. The model never writes this; app.recommend and app.guardrails decide."""
    rules = bundle['guardrails']
    base = gr.check_action({}, state, rules)
    if base.escalate_to_human:
        return {'type': None, 'status': 'escalate_to_human', 'reasons': base.violations, 'recommendation': None}
    if segment == 'sleeping_dog':
        return {'type': None, 'status': 'no_contact', 'recommendation': None,
                'reasons': ['Sleeping dog: do not contact or offer anything. Fix the cause quietly and have a person review.']}
    if segment == 'lost_cause':
        return {'type': None, 'status': 'no_offer', 'recommendation': None,
                'reasons': ['Lost cause: no offer is expected to help. Fix the cause and have a person decide whether to reach out.']}
    if segment == 'sure_thing':
        status = {'permitted': 'ready_apology', 'needs_human_approval': 'pending_approval', 'blocked': 'blocked'}[base.status]
        return {'type': 'service_apology', 'status': status, 'recommendation': None,
                'reasons': ['Sure thing: send a plain apology and a fix update, no offer, so no discount is spent.'] + base.reasons}
    if segment == 'persuadable':
        r = recommendation(customer, bundle, state)
        status = {'recommend': 'ready_offer', 'recommend_pending_approval': 'pending_approval', 'no_action': 'no_offer',
                  'escalate_to_human': 'escalate_to_human'}[r['decision']]
        return {'type': 'retention_offer' if r['action'] else None, 'status': status, 'reasons': r['reasons'][:6],
                'recommendation': {'decision': r['decision'], 'action': r['action']['name'] if r['action'] else None,
                                   'guardrails': (r.get('guardrails') or {}).get('status'),
                                   'twin_risk_before': r['risk']['churn_probability'],
                                   'twin_risk_after': (r['risk']['churn_probability'] - r['action']['risk_drop']) if r['action'] else None,
                                   'twin_n_scenarios': len(r['candidates']), 'twin_note': tw.INTERPRETATION}}
    return {'type': None, 'status': 'unclassified', 'recommendation': None, 'reasons': ['No segment, so nothing is proposed.']}


HEADLINES = {
    'persuadable': 'Persuadable: worth a careful, approved recovery contact.',
    'sure_thing': 'Sure thing: fix the problem and apologise; do not spend an offer.',
    'lost_cause': 'Lost cause: fix the problem; an offer is not expected to help.',
    'sleeping_dog': 'Sleeping dog: do not contact; fix the cause quietly.',
}


def evaluate(ref: str, bundle: dict, cfg: Mapping, *, now: datetime | None = None, path=None, full: bool = True) -> dict:
    """Full read of one customer: events, frustration, model score, segment, and the plan that would run.
    full=False skips the customer-contact decision (the slow part, it runs the digital twin) for callers that only need the numbers."""
    now = now or _now()
    with _connect(path) as con:
        row = _customer(con, ref)
    customer = json.loads(row['customer_json'])
    state = json.loads(row['account_state_json']) if row['account_state_json'] else None
    events = list_events(ref, path=path)
    fr = frustration(events, cfg, now)
    risk, uplift = _score(customer, bundle, fr['score'], cfg)
    seg, why = classify(risk['used_for_segment'], risk['flagged'], uplift['simulated_uplift'] if uplift else None,
                        cfg['segmentation']['uplift_margin'])
    plan = {'label': SIMULATED, 'note': NOTE, 'segment': seg, 'segment_reason': why, 'headline': HEADLINES.get(seg, 'Cannot segment yet.'),
            'tasks': _tasks(events, fr['level'], cfg),
            'customer_contact': _contact(seg, customer, bundle, state) if full else {'type': None, 'status': 'not_evaluated', 'reasons': [], 'recommendation': None}}
    return {'customer_ref': ref, 'first_name': row['first_name'], 'customer': customer, 'account_state': state, 'events': events, 'frustration': fr, 'risk': risk,
            'simulated_uplift': uplift, 'segment': seg, 'plan': plan,
            'triggered': LEVELS.index(fr['level']) >= LEVELS.index(cfg['trigger_level']),
            'recovery': current_recovery(ref, path=path)}


# ------------------------------------------------------------------ self-healing run
def _public_recovery(r: sqlite3.Row) -> dict:
    d = dict(r)
    d['plan'] = json.loads(d.pop('plan_json'))
    return d


def current_recovery(ref: str, *, path=None) -> dict | None:
    with _connect(path) as con:
        r = con.execute("SELECT * FROM inc_recoveries WHERE ref=? AND status='open' ORDER BY id DESC LIMIT 1", (ref,)).fetchone()
    return _public_recovery(r) if r else None


def process(ref: str, bundle: dict, cfg: Mapping, *, now: datetime | None = None, path=None) -> dict:
    """Re-evaluate the customer. If they are at or past trigger_level, open (or refresh) their recovery. Safe to call after every event."""
    ev = evaluate(ref, bundle, cfg, now=now, path=path, full=False)
    created = False
    if ev['triggered']:
        ev = evaluate(ref, bundle, cfg, now=now, path=path)
        stamp, plan = (now or _now()).isoformat(), json.dumps(ev['plan'])
        with _connect(path) as con:
            cur = con.execute("SELECT id FROM inc_recoveries WHERE ref=? AND status='open'", (ref,)).fetchone()
            if cur:
                con.execute('UPDATE inc_recoveries SET updated_ts=?, level=?, frustration=?, segment=?, plan_json=? WHERE id=?',
                            (stamp, ev['frustration']['level'], ev['frustration']['score'], ev['segment'], plan, cur['id']))
            else:
                con.execute('INSERT INTO inc_recoveries (ref, ts, updated_ts, status, level, frustration, segment, plan_json) VALUES (?,?,?,?,?,?,?,?)',
                            (ref, stamp, stamp, 'open', ev['frustration']['level'], ev['frustration']['score'], ev['segment'], plan))
                created = True
        ev['recovery'] = current_recovery(ref, path=path)
    ev['recovery_created'] = created
    return ev


def list_recoveries(status: str | None = None, *, path=None) -> list[dict]:
    with _connect(path) as con:
        rows = con.execute('SELECT * FROM inc_recoveries' + (' WHERE status=?' if status else '') + ' ORDER BY id DESC LIMIT 200',
                           (status,) if status else ()).fetchall()
    return [_public_recovery(r) for r in rows]


def close_recovery(recovery_id: int, closed_by: str, note: str | None = None, *, path=None) -> dict:
    who = _check_actor(closed_by, 'closed_by')
    with _connect(path) as con:
        r = con.execute('SELECT * FROM inc_recoveries WHERE id=?', (recovery_id,)).fetchone()
        if r is None:
            raise IncidentError('recovery %s not found' % recovery_id)
        if r['status'] != 'open':
            raise IncidentError('recovery %s is already %s' % (recovery_id, r['status']))
        con.execute("UPDATE inc_recoveries SET status='closed', closed_by=?, note=?, updated_ts=? WHERE id=?",
                    (who, (note or '')[:500] or None, _now().isoformat(), recovery_id))
        r = con.execute('SELECT * FROM inc_recoveries WHERE id=?', (recovery_id,)).fetchone()
    return _public_recovery(r)


def list_customers(bundle: dict, cfg: Mapping, *, path=None) -> list[dict]:
    with _connect(path) as con:
        refs = [r['ref'] for r in con.execute('SELECT ref FROM inc_customers ORDER BY updated_ts DESC LIMIT 200')]
    out = []
    for ref in refs:
        e = evaluate(ref, bundle, cfg, path=path, full=False)
        out.append({'customer_ref': ref, 'first_name': e['first_name'], 'frustration': e['frustration']['score'], 'level': e['frustration']['level'],
                    'segment': e['segment'], 'n_events': e['frustration']['n_events'], 'open_recovery': e['recovery'] is not None})
    return out


# ------------------------------------------------------------------ the simulator
def simulate(ref: str, storyline: str, cfg: Mapping, *, now: datetime | None = None, seed: int = 0, path=None) -> list[int]:
    """Play a storyline into a customer's log as SIMULATED events. Times get a small seeded jitter (up to 40 minutes)."""
    story = cfg['storylines'].get(storyline)
    if story is None:
        raise IncidentError('unknown storyline %r (available: %s)' % (storyline, sorted(cfg['storylines'])))
    now = now or _now()
    rng = np.random.default_rng(seed)
    ids = []
    for e in story['events']:
        day = (now - timedelta(days=e['day'])).replace(hour=e['hour'], minute=0, second=0, microsecond=0)
        when = day + timedelta(minutes=int(rng.integers(0, 40)))
        if when > now:
            when = now
        ids.append(log_event(ref, e['kind'], cfg, severity=e['severity'], occurred_at=when, work_impact=e.get('work_impact', False),
                             note=e.get('note'), source='simulated', path=path))
    return ids
