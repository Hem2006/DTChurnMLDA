"""Autopilot: the service-incident engine running by itself on a watched customer base.

Nobody types anything. A telemetry SIMULATOR produces events (network outages that hit a whole cell, billing glitches, support backlogs,
background noise); the loop then does what a retention team would do by hand:

  detect (frustration reaches "angry") -> score (calibrated model) -> segment (SIMULATED uplift) -> test fixes on the digital twin
  -> auto-heal what is technical (remote reset / line test: the fault events are resolved) -> queue what needs a person
  (offers go to the review queue; vulnerable accounts are handed to a human) -> narrate every step in a feed.

Nothing is sent to a customer. Everything that contacts a customer or offers something still waits for a named approver, and the
guardrail limits are still unset, so it does. The customers are REAL Telco rows with made-up first names, cells and account state; the
events are SIMULATED. Demo data lives under the ref prefix DEMO- so a reset never touches anything else.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

import churn_serving as cs
from app import incidents as inc
from app import pipeline as pl
from app import uplift as up

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv'
PREFIX = 'DEMO-'
CELLS = ['Northgate', 'Harbour', 'Midtown', 'Eastside', 'Hillcrest', 'Airport']
NAMES = ['Maya', 'Liam', 'Sofia', 'Noah', 'Aisha', 'Ethan', 'Priya', 'Lucas', 'Mei', 'Omar', 'Chloe', 'Diego', 'Zara', 'Jonas', 'Amara', 'Felix',
         'Hana', 'Mateo', 'Ines', 'Kofi', 'Lena', 'Rohan', 'Tessa', 'Yusuf', 'Nora', 'Arjun', 'Elena', 'Idris', 'Mila', 'Tariq', 'Freya', 'Kenji',
         'Leila', 'Owen', 'Sana', 'Victor', 'Anya', 'Bruno', 'Carmen', 'Dev', 'Esme', 'Farid', 'Greta', 'Hugo', 'Isla', 'Jamal', 'Kira', 'Marco',
         'Nadia', 'Oscar', 'Petra', 'Quinn', 'Rania', 'Soren', 'Tala', 'Umar', 'Vera', 'Wes', 'Xena', 'Yara']
QUOTAS = {'persuadable': 12, 'sure_thing': 14, 'lost_cause': 8, 'sleeping_dog': 6}
AUTOFIX_KINDS = {'outage', 'dropped_call', 'slow_service', 'repeat_visit'}   # technical faults a remote reset / line test can clear
AUTOFIX_AFTER_S = 8
INCIDENTS = {
    'tower_outage': 'Tower outage',
    'late_night_outage': 'Late-night outage',
    'billing_glitch': 'Billing glitch',
    'support_backlog': 'Support backlog',
}
SEG_LABEL = {'persuadable': 'Persuadable', 'sure_thing': 'Sure thing', 'lost_cause': 'Lost cause', 'sleeping_dog': 'Sleeping dog'}


def cell_of(ref: str) -> str:
    return CELLS[int(hashlib.sha1(ref.encode()).hexdigest(), 16) % len(CELLS)]


def _connect(path=None):
    con = inc._connect(path)
    con.executescript("""CREATE TABLE IF NOT EXISTS inc_feed (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, ref TEXT, kind TEXT NOT NULL, text TEXT NOT NULL, detail TEXT);""")
    return con


class Autopilot:
    def __init__(self, bundle: dict, cfg: Mapping, *, path=None, seed: int = 7):
        self.bundle, self.cfg, self.path, self.rng_seed = bundle, cfg, path, seed
        self.lock = threading.RLock()
        self.rng = np.random.default_rng(seed)
        self.running = False
        self.ticks = 0
        self.warm = {'done': 0, 'total': 0}
        self.snap: dict[str, dict] = {}
        self.names: dict[str, str] = {}

    # ------------------------------------------------------------------ feed
    def feed(self, kind: str, text: str, ref: str | None = None, detail: str | None = None, now: datetime | None = None) -> None:
        with _connect(self.path) as con:
            con.execute('INSERT INTO inc_feed (ts, ref, kind, text, detail) VALUES (?,?,?,?,?)',
                        ((now or datetime.now(timezone.utc)).isoformat(), ref, kind, text, detail))

    def recent_feed(self, limit: int = 40) -> list[dict]:
        with _connect(self.path) as con:
            rows = con.execute('SELECT * FROM inc_feed ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ seeding
    def seed(self, n: int = 40, *, now: datetime | None = None) -> int:
        """Register n real Telco rows chosen so all four segments show up. Safe to call twice: existing demo customers are kept."""
        with self.lock:
            if self.bundle.get('uplift') is None:
                raise inc.IncidentError('the SIMULATED uplift model is not built (python -m app.uplift), so the autopilot cannot segment customers')
            with _connect(self.path) as con:
                have = [r['ref'] for r in con.execute('SELECT ref FROM inc_customers WHERE ref LIKE ?', (PREFIX + '%',))]
            if have:
                self._rebuild(have)
                return len(have)
            meta = self.bundle['meta']
            recs = [r for r in pl.read_csv_source(CSV, pl.load_mapping(), meta) if not r['error']]
            rng = np.random.default_rng(self.rng_seed)
            pool = [recs[i] for i in rng.permutation(len(recs))[:500]]
            df = pd.DataFrame([r['customer'] for r in pool])
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                pred = cs.predict(df, self.bundle)
                u = up.score_uplift(df, self.bundle['uplift'], meta)['simulated_uplift'].values
            margin = self.cfg['segmentation']['uplift_margin']
            by_seg: dict[str, list[int]] = {s: [] for s in inc.SEGMENTS}
            for i in range(len(pool)):
                seg, _ = inc.classify(float(pred['churn_probability'].iloc[i]), bool(pred['flagged'].iloc[i]), float(u[i]), margin)
                by_seg[seg].append(i)
            chosen: list[int] = []
            for seg, q in QUOTAS.items():
                chosen += by_seg[seg][:max(1, round(q * n / 40))]
            chosen += [i for s in inc.SEGMENTS for i in by_seg[s] if i not in chosen][:max(0, n - len(chosen))]
            chosen = [int(i) for i in rng.permutation(chosen)[:n]]
            names = list(rng.permutation(NAMES))
            refs = []
            for k, i in enumerate(chosen):
                r = pool[i]
                ref = (PREFIX + r['customer_ref'])[:64]
                roll = rng.random()
                if k % 13 == 5:
                    state = {'vulnerable': True, 'escalated': False, 'contacts_this_month': 0, 'discount_pct_used_this_quarter': 0}
                elif roll < .12:
                    state = None                                           # CRM did not supply it: unknown is never assumed safe
                else:
                    state = {'vulnerable': False, 'escalated': False, 'contacts_this_month': int(rng.integers(0, 2)), 'discount_pct_used_this_quarter': 0}
                inc.register_customer(ref, r['customer'], self.bundle, first_name=names[k % len(names)], account_state=state, path=self.path)
                refs.append(ref)
            self.feed('system', 'Autopilot online: watching %d customers across %d cells.' % (len(refs), len(CELLS)), now=now)
            self._rebuild(refs)
            return len(refs)

    def _rebuild(self, refs: list[str]) -> None:
        for ref in refs:
            self._snapshot(inc.evaluate(ref, self.bundle, self.cfg, path=self.path, full=False))

    def prewarm(self) -> None:
        """Run the digital twin ahead of time for every persuadable customer so an incident does not wait on it. Safe to run in a thread."""
        import json
        refs = [r for r, s in list(self.snap.items()) if s['segment'] == 'persuadable']
        self.warm = {'done': 0, 'total': len(refs)}
        for r in refs:
            with _connect(self.path) as con:
                row = con.execute('SELECT customer_json, account_state_json FROM inc_customers WHERE ref=?', (r,)).fetchone()
            if row:
                inc.recommendation(json.loads(row['customer_json']), self.bundle, json.loads(row['account_state_json']) if row['account_state_json'] else None)
            self.warm['done'] += 1

    def _snapshot(self, ev: dict) -> dict:
        ref = ev['customer_ref']
        c = ev['plan']['customer_contact']
        rec = c.get('recommendation') or {}
        s = {'ref': ref, 'name': ev['first_name'] or ref, 'cell': cell_of(ref), 'risk': ev['risk']['churn_probability'],
             'flagged': ev['risk']['flagged'], 'uplift': ev['simulated_uplift']['simulated_uplift'] if ev['simulated_uplift'] else None,
             'frustration': ev['frustration']['score'], 'level': ev['frustration']['level'], 'segment': ev['segment'],
             'contact_status': c['status'], 'offer': rec.get('action'), 'twin_after': rec.get('twin_risk_after'),
             'recovery_open': ev['recovery'] is not None, 'n_events': ev['frustration']['n_events'],
             'vulnerable': bool((ev.get('account_state') or {}).get('vulnerable'))}
        self.snap[ref] = s
        return s

    # ------------------------------------------------------------------ telemetry simulator
    def _log(self, ref, kind, now, *, severity, work=False, note=None, when=None) -> int:
        return inc.log_event(ref, kind, self.cfg, severity=severity, occurred_at=when or now, work_impact=work, note=note, source='simulated',
                             logged_at=now, path=self.path)

    def _demo_refs(self) -> list[str]:
        return sorted(self.snap)

    def inject(self, kind: str, *, now: datetime | None = None, cell: str | None = None) -> dict:
        """Start a SIMULATED incident. Returns who was hit."""
        now = now or datetime.now(timezone.utc)
        with self.lock:
            if kind not in INCIDENTS:
                raise inc.IncidentError('unknown incident %r (available: %s)' % (kind, sorted(INCIDENTS)))
            if not self.snap:
                raise inc.IncidentError('the autopilot has no customers yet: start it first')
            refs = self._demo_refs()
            if kind in ('tower_outage', 'late_night_outage'):
                cell = cell or str(self.rng.choice(CELLS))
                hit = [r for r in refs if self.snap[r]['cell'] == cell and self._has_service(r)]
                when = None
                if kind == 'late_night_outage':
                    when = (now - timedelta(days=1)).replace(hour=23, minute=20, second=0, microsecond=0)
                for r in hit:
                    sev = int(self.rng.integers(3, 5)) if kind == 'late_night_outage' else int(self.rng.integers(2, 5))
                    self._log(r, 'outage', now, severity=sev, work=bool(self.rng.random() < .35),
                              note='Cell %s lost service' % cell, when=when)
                label = 'Late-night outage' if kind == 'late_night_outage' else 'Tower outage'
                self.feed('incident', '%s in %s: %d customers lost service.' % (label, cell, len(hit)), detail=cell, now=now)
            elif kind == 'billing_glitch':
                hit = [str(x) for x in self.rng.choice(refs, size=min(8, len(refs)), replace=False)]
                for r in hit:
                    self._log(r, 'bill_dispute', now, severity=int(self.rng.integers(2, 5)), note='Charged twice')
                self.feed('incident', 'Billing glitch: %d customers were charged twice.' % len(hit), now=now)
            else:
                hit = [str(x) for x in self.rng.choice(refs, size=min(6, len(refs)), replace=False)]
                for r in hit:
                    self._log(r, 'support_unanswered', now, severity=4, note='Callback never came')
                    self._log(r, 'support_call', now, severity=3)
                    self._log(r, 'complaint', now, severity=4, note='Nobody has called me back')
                self.feed('incident', 'Support backlog: %d customers are waiting for a callback and complaining.' % len(hit), now=now)
            self._process(hit, now)
            return {'incident': kind, 'cell': cell, 'customers_hit': len(hit)}

    def _has_service(self, ref: str) -> bool:
        with _connect(self.path) as con:
            row = con.execute('SELECT customer_json FROM inc_customers WHERE ref=?', (ref,)).fetchone()
        import json
        return json.loads(row['customer_json']).get('InternetService') != 'No'

    def tick(self, *, now: datetime | None = None) -> dict:
        """One beat of the loop: background noise, follow-up calls, auto-healing, and the engine run for everyone touched."""
        now = now or datetime.now(timezone.utc)
        with self.lock:
            self.ticks += 1
            refs = self._demo_refs()
            if not refs:
                return {'tick': self.ticks, 'touched': 0}
            touched: set[str] = set()
            for r in self.rng.choice(refs, size=int(self.rng.integers(1, 4)), replace=False):
                kind = str(self.rng.choice(['slow_service', 'dropped_call', 'support_call']))
                self._log(str(r), kind, now, severity=int(self.rng.integers(1, 3)))
                touched.add(str(r))
            # Customers already in trouble keep coming back when nobody has called them.
            for r in refs:
                s = self.snap[r]
                if s['level'] != 'calm' and self.rng.random() < .18:
                    n_unanswered = sum(1 for e in inc.list_events(r, path=self.path) if e['kind'] == 'support_unanswered')
                    if n_unanswered < 2:
                        self._log(r, 'support_unanswered' if self.rng.random() < .5 else 'support_call', now, severity=3 + int(self.rng.random() < .5))
                        touched.add(r)
            fixed = self._autoheal(now)
            touched |= {r for r, _ in fixed}
            self._process(sorted(touched), now, fixed=dict(fixed))
            return {'tick': self.ticks, 'touched': len(touched), 'auto_fixed': len(fixed)}

    # ------------------------------------------------------------------ the automation
    def _autoheal(self, now: datetime) -> list[tuple[str, int]]:
        """Resolve technical fault events once the (simulated) remote reset / line test has had time to run."""
        out: dict[str, int] = {}
        for r in self._demo_refs():
            if not self.snap[r]['recovery_open'] and self.snap[r]['level'] == 'calm':
                continue
            for e in inc.list_events(r, path=self.path):
                if e['resolved'] or e['kind'] not in AUTOFIX_KINDS:
                    continue
                if (now - datetime.fromisoformat(e['logged_ts'])).total_seconds() >= AUTOFIX_AFTER_S:
                    inc.resolve_event(e['id'], path=self.path)
                    out[r] = out.get(r, 0) + 1
        return list(out.items())

    def _process(self, refs: list[str], now: datetime, fixed: Mapping[str, int] | None = None) -> None:
        fixed = fixed or {}
        for ref in refs:
            prev = dict(self.snap.get(ref) or {})
            ev = inc.process(ref, self.bundle, self.cfg, now=now, path=self.path)
            s = self._snapshot(ev)
            name = '%s (%s)' % (s['name'], s['cell'])
            if ref in fixed and prev.get('level') != 'calm':
                self.feed('autofix', 'Auto-heal: %s had %d fault%s cleared by remote reset. Frustration %d%% to %d%%.' % (
                    name, fixed[ref], '' if fixed[ref] == 1 else 's', round(prev.get('frustration', 0) * 100), round(s['frustration'] * 100)), ref, now=now)
            if ev['recovery_created']:
                self._narrate_new_recovery(ev, s, name, now)
            elif prev.get('level') == 'angry' and s['level'] != 'angry':
                self.feed('calmed', '%s has calmed down: now %s.' % (name, s['level']), ref, now=now)
            if prev.get('recovery_open') and not ev['triggered'] and ev['recovery']:
                inc.close_recovery(ev['recovery']['id'], 'autopilot', 'closed automatically: frustration fell below the trigger level', path=self.path)
                s['recovery_open'] = False

    def _narrate_new_recovery(self, ev: dict, s: dict, name: str, now: datetime) -> None:
        ref = s['ref']
        plan, contact = ev['plan'], ev['plan']['customer_contact']
        self.feed('angry', '%s is ANGRY: frustration %d%%.' % (name, round(s['frustration'] * 100)), ref, now=now)
        if s['segment']:
            self.feed('segment', 'Churn model + SIMULATED uplift: %s (risk %d%%, uplift %+.2f).' % (SEG_LABEL[s['segment']], round(s['risk'] * 100), s['uplift']), ref, now=now)
        rec = contact.get('recommendation')
        if rec and rec.get('action'):
            self.feed('twin', 'Digital twin tested %d fixes. Best: %s, risk %d%% to %d%% (model sensitivity, not causal).' % (
                rec['twin_n_scenarios'], rec['action'].replace('_', ' '), round(rec['twin_risk_before'] * 100), round(rec['twin_risk_after'] * 100)), ref, now=now)
        if plan['tasks']:
            self.feed('task', 'Auto-created: %s.' % ', '.join(t['task'].replace('_', ' ') for t in plan['tasks']), ref, now=now)
        st = contact['status']
        if st == 'escalate_to_human':
            self.feed('escalated', '%s is vulnerable or escalated: no contact, handed to a human.' % name, ref, now=now)
        elif st == 'no_contact':
            self.feed('hold', '%s is a sleeping dog: left alone, cause fixed quietly.' % name, ref, now=now)
        elif st == 'no_offer' and s['segment'] == 'lost_cause':
            self.feed('hold', '%s is a lost cause: no offer spent, fix only.' % name, ref, now=now)
        elif contact.get('type') == 'service_apology':
            self.feed('hold', '%s is a sure thing: apology only, no discount spent.' % name, ref, now=now)
        elif contact.get('type') == 'retention_offer' and st in ('pending_approval', 'ready_offer'):
            self._queue(ev, name, now)

    def _queue(self, ev: dict, name: str, now: datetime) -> None:
        ref = ev['customer_ref']
        res = pl.ingest([{'customer_ref': ref, 'first_name': ev['first_name'], 'customer': ev['customer'], 'account_state': ev['account_state'],
                          'error': None}], self.bundle, client=None, path=self.path, source='autopilot', recommender=inc.recommendation)
        if res['skipped_open_case']:
            self.feed('queued', '%s already has an open case in the review queue.' % name, ref, now=now)
        else:
            self.feed('queued', 'Sent to the review queue: %s waits for a named approver. Nothing is sent to the customer.' % name, ref, now=now)

    # ------------------------------------------------------------------ views
    def stats(self) -> dict:
        s = list(self.snap.values())
        with _connect(self.path) as con:
            n_events = con.execute('SELECT COUNT(*) FROM inc_events WHERE ref LIKE ?', (PREFIX + '%',)).fetchone()[0]
            n_rec = con.execute('SELECT COUNT(*) FROM inc_recoveries WHERE ref LIKE ?', (PREFIX + '%',)).fetchone()[0]
            kinds = {r['kind']: r['n'] for r in con.execute('SELECT kind, COUNT(*) n FROM inc_feed GROUP BY kind')}
        return {'customers': len(s), 'events': n_events, 'recoveries_opened': n_rec, 'angry_now': sum(1 for x in s if x['level'] == 'angry'),
                'annoyed_now': sum(1 for x in s if x['level'] == 'annoyed'), 'twin_tests': kinds.get('twin', 0), 'auto_fixed': kinds.get('autofix', 0), 'queued_for_human': kinds.get('queued', 0),
                'escalated': kinds.get('escalated', 0), 'automated_steps': sum(kinds.get(k, 0) for k in ('segment', 'twin', 'task', 'autofix', 'queued', 'hold', 'escalated')),
                'by_segment': {g: sum(1 for x in s if x['segment'] == g) for g in inc.SEGMENTS}}

    def state(self) -> dict:
        with self.lock:
            return {'label': inc.SIMULATED, 'note': inc.NOTE, 'running': self.running, 'tick': self.ticks, 'warm': dict(self.warm), 'stats': self.stats(),
                    'customers': sorted(self.snap.values(), key=lambda x: -x['frustration']), 'feed': self.recent_feed(),
                    'incidents': INCIDENTS, 'cells': CELLS, 'trigger_level': self.cfg['trigger_level'], 'levels': self.cfg['levels'],
                    'uplift_margin': self.cfg['segmentation']['uplift_margin'],
                    'risk_threshold': cs.get_threshold(self.bundle['meta'], self.bundle.get('config'))[0]}

    def reset(self) -> dict:
        """Remove ONLY the demo customers (ref prefix DEMO-), their events, recoveries and the feed."""
        with self.lock:
            self.running = False
            with _connect(self.path) as con:
                n = con.execute('DELETE FROM inc_customers WHERE ref LIKE ?', (PREFIX + '%',)).rowcount
                con.execute('DELETE FROM inc_events WHERE ref LIKE ?', (PREFIX + '%',))
                con.execute('DELETE FROM inc_recoveries WHERE ref LIKE ?', (PREFIX + '%',))
                con.execute('DELETE FROM inc_feed')
            self.snap.clear()
            self.ticks = 0
            self.rng = np.random.default_rng(self.rng_seed)
            return {'removed_customers': n}
