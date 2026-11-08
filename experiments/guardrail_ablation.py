"""Guardrail ablation for the paper (Section on pipeline behaviour, Table IX). Read-only: nothing is queued, drafted or sent.

    .venv/Scripts/python -m experiments.guardrail_ablation          # -> experiments/results/guardrail_ablation.json

The twin scenarios are run once per flagged test customer (the slow part), then the recommender's choice rule is replayed under
several guardrail configurations with app.guardrails.check_action. The replay is checked against app.recommend.recommend on a sample.

Configurations with numeric limits use HYPOTHETICAL limits and SIMULATED account states, chosen only to exercise every rule;
guardrails.yaml stays null and nothing here is a policy number.
"""
from __future__ import annotations

import json
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np

import churn_serving as cs
from app import dash_logic, guardrails as gr, recommend as rec, twin as tw
from app.fairness import load_test_split

warnings.filterwarnings('ignore')
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'experiments' / 'results'
HYPO_LIMITS = {'max_contacts_per_month': 2, 'max_discount_pct_per_customer_per_quarter': 15}


def rule_of(reason: str) -> str:
    r = reason.lower()
    if 'vulnerable' in r and 'not supplied' not in r:
        return 'vulnerable_account'
    if 'escalated' in r and 'not supplied' not in r:
        return 'escalated_account'
    if 'not supplied' in r:
        return 'unknown_state'
    if 'max_discount' in r or 'quarterly cap' in r:
        return 'discount_cap_unset' if 'not configured' in r else 'discount_cap_exceeded'
    if 'max_contacts' in r or 'contact limit' in r:
        return 'contact_cap_unset' if 'not configured' in r else 'contact_cap_exceeded'
    return 'other: ' + reason


def decide(cands, state, rules, enabled=True):
    """Replay of recommend.recommend steps 1, 4 and 5 for a flagged customer (no net values are configured, so every candidate is 'worthwhile'
    unknown and the pool is all applicable scenarios in risk-drop order)."""
    if not enabled:
        return ('recommend', cands[0]['name'], Counter()) if cands else ('no_action', None, Counter())
    base = gr.check_action({}, state, rules)
    if base.escalate_to_human:
        return 'escalate_to_human', None, Counter(rule_of(v) for v in base.violations)
    checks = [(c, gr.check_action({'discount_pct': c['discount_pct']}, state, rules)) for c in cands]
    blocked = Counter(rule_of(v) for _, k in checks if k.status == 'blocked' for v in k.violations)
    for c, k in checks:
        if k.status == 'permitted':
            return 'recommend', c['name'], blocked
    for c, k in checks:
        if k.status == 'needs_human_approval':
            return 'recommend_pending_approval', c['name'], blocked + Counter(rule_of(a) for a in k.approvals_needed)
    return 'no_action', None, blocked


def simulated_states(n, seed=42):
    """SIMULATED account states (arbitrary shares, only to exercise every rule): 5% vulnerable, 3% escalated, 10% not supplied,
    contacts this month uniform 0-2, discount used this quarter uniform over {0, 5, 10, 15}."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        u = rng.random()
        if u < 0.10:
            out.append(None); continue
        out.append({'vulnerable': bool(u < 0.15), 'escalated': bool(0.15 <= u < 0.18),
                    'contacts_this_month': int(rng.integers(0, 3)), 'discount_pct_used_this_quarter': int(rng.choice([0, 5, 10, 15]))})
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    bundle = dash_logic.load_bundle()
    X_test, y_test = load_test_split()
    cols = bundle['meta']['input_columns']
    pred = cs.predict(X_test[cols], bundle)
    flagged = np.where(pred['flagged'].values)[0]
    print('flagged', len(flagged), 'of', len(X_test), flush=True)

    cache = []
    for j, i in enumerate(flagged):
        cust = X_test[cols].iloc[i].to_dict()
        run = tw.run_scenarios(cust, bundle)
        cache.append({'row': int(i), 'customer': cust,
                      'cands': [{'name': r['name'], 'discount_pct': r['changes'].get('discount_pct', 0), 'risk_drop': r['risk_drop']}
                                for r in run['results'] if r['name'] != 'do_nothing'],
                      'not_applicable': [n['name'] for n in run['not_applicable']]})
        if j % 50 == 0:
            print(j, '%.0fs' % (time.time() - t0), flush=True)

    shipped = bundle['guardrails']
    hypo = {**shipped, **HYPO_LIMITS}
    clean = {'vulnerable': False, 'escalated': False, 'contacts_this_month': 0, 'discount_pct_used_this_quarter': 0}
    sim = simulated_states(len(cache))
    configs = {
        'off': ('guardrails disabled (counterfactual)', None, False, lambda k: None),
        'shipped_state_unknown': ('guardrails.yaml as shipped (limits null), account state not supplied', shipped, True, lambda k: None),
        'shipped_state_clean': ('limits null, clean account state supplied', shipped, True, lambda k: clean),
        'shipped_state_simulated': ('limits null, SIMULATED account states', shipped, True, lambda k: sim[k]),
        'hypothetical_limits_state_clean': ('HYPOTHETICAL limits (2 contacts/month, 15%/quarter), clean state', hypo, True, lambda k: clean),
        'hypothetical_limits_state_simulated': ('HYPOTHETICAL limits, SIMULATED account states', hypo, True, lambda k: sim[k]),
    }
    res = {'n_test': int(len(X_test)), 'n_flagged': int(len(cache)), 'threshold': float(pred['threshold'].iloc[0]),
           'threshold_is_placeholder': bool(pred['threshold_is_placeholder'].iloc[0]), 'hypothetical_limits': HYPO_LIMITS,
           'simulated_state_rule': simulated_states.__doc__.strip(), 'configs': {}}
    for key, (label, rules, enabled, state_of) in configs.items():
        dec, chosen, rules_hit, cust_rule = Counter(), Counter(), Counter(), Counter()
        for k, c in enumerate(cache):
            d, name, hits = decide(c['cands'], state_of(k), rules, enabled)
            dec[d] += 1; chosen[name] += 1; rules_hit.update(hits)
            cust_rule.update(set(hits))
        res['configs'][key] = {'label': label, 'decisions': dict(dec), 'chosen_action': {str(k): v for k, v in chosen.items()},
                               'rule_triggers_total': dict(rules_hit), 'customers_touched_by_rule': dict(cust_rule)}
        print(key, dict(dec), dict(cust_rule), flush=True)

    # Cross-check the replay against the real recommender on a sample (shipped rules, state unknown and simulated).
    mism = 0
    sample = list(range(0, len(cache), max(1, len(cache) // 25)))
    for k in sample:
        for st in (None, sim[k]):
            real = rec.recommend(cache[k]['customer'], bundle, account_state=st)
            d, name, _ = decide(cache[k]['cands'], st, shipped)
            mism += (real['decision'] != d) or ((real['action'] or {}).get('name') != name)
    res['replay_check'] = {'sampled_customers': len(sample), 'comparisons': 2 * len(sample), 'mismatches': int(mism)}
    res['seconds'] = round(time.time() - t0)
    print('replay check', res['replay_check'], flush=True)
    (OUT / 'guardrail_ablation.json').write_text(json.dumps(res, indent=2))


if __name__ == '__main__':
    main()
