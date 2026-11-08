"""One decision per customer: recommend / recommend_pending_approval / no_action / escalate_to_human, with reasons and a full case record.

Order of precedence (code, deterministic; the LLM has no say):
  1. vulnerable or open escalation        -> escalate_to_human (no contact, whatever the risk)
  2. probability below the threshold       -> no_action
  3. twin scenarios (MODEL SENSITIVITY, not causal), ranked by delta; first one that is worthwhile and permitted wins
     -> recommend; else first that only needs approval -> recommend_pending_approval; else no_action
SIMULATED uplift is advisory text only; it never picks or blocks an action.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src import explain as E
from src import guardrails as G
from src import serving as S
from src import twin as TW
from src import uplift as U

DECISIONS = ('recommend', 'recommend_pending_approval', 'no_action', 'escalate_to_human')


def _net_value(delta: float, cost, business: dict):
    s, v = business.get('success_rate'), business.get('customer_value')
    miss = [k for k, x in (('success_rate', s), ('customer_value', v), ('scenario cost', cost)) if x is None]
    if miss:
        return None, 'not computed: %s not configured' % ', '.join(miss)
    return float(delta * s * v - cost), None


def recommend(customer: dict, bundle: dict, account_state: dict | None = None, customer_ref: str | None = None) -> dict:
    df = pd.DataFrame([customer])
    pred = S.predict(df, bundle).iloc[0]
    t = bundle['threshold']
    policy = bundle['policy']
    rec = {'customer_ref': customer_ref, 'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'customer': dict(customer),
           'account_state': dict(account_state or {}), 'account_state_supplied': account_state is not None,
           'risk': {'probability': float(pred['churn_probability']), 'flagged': bool(pred['flagged']), 'threshold': t['threshold'],
                    'threshold_source': t['source'], 'threshold_is_placeholder': t['is_placeholder']},
           'labels': {'twin': TW.INTERPRETATION, 'uplift': U.SIMULATED, 'threshold': 'PLACEHOLDER' if t['is_placeholder'] else 'business-configured'},
           'reasons': [], 'shap_reasons': None, 'candidates': [], 'not_applicable': [], 'action': None, 'simulated_uplift': None,
           'guardrail': None, 'fired_by': [], 'decision': None}
    if t['is_placeholder']:
        rec['reasons'].append('Threshold is a PLACEHOLDER (F1-optimal), not cost-based: offer_cost, customer_value and success_rate are not set in config/business.yaml.')

    base = G.check_action({}, account_state, policy)
    if base.escalate_to_human:
        rec.update(decision='escalate_to_human', guardrail=base.as_dict(), fired_by=base.fired)
        rec['reasons'] = base.violations + rec['reasons']
        return rec
    if not rec['risk']['flagged']:
        rec.update(decision='no_action', guardrail=base.as_dict(), fired_by=[])
        rec['reasons'].insert(0, 'Churn probability %.3f is below the threshold %.3f: no retention action.' % (rec['risk']['probability'], t['threshold']))
        return rec

    if bundle.get('uplift') is not None:
        u = U.score(df, bundle['uplift'], bundle['meta']).iloc[0]
        rec['simulated_uplift'] = {'label': U.SIMULATED, 'note': U.NOTE, 'uplift': float(u['simulated_uplift']),
                                   'p_churn_control': float(u['simulated_p_churn_control']), 'p_churn_treated': float(u['simulated_p_churn_treated'])}
        rec['reasons'].append('SIMULATED uplift %+.3f (advisory; simulation assumptions, not a finding).' % u['simulated_uplift'])

    run = TW.run_scenarios(customer, bundle)
    rec['not_applicable'] = run['not_applicable']
    for r in run['results']:
        if r['name'] == 'do_nothing' or r['delta'] <= 0:
            continue
        chk = G.check_action({'discount_pct': r['changes'].get('discount_pct', 0)}, account_state, policy)
        ev, why = _net_value(r['delta'], r['cost_per_customer'], bundle['business'])
        rec['candidates'].append({'name': r['name'], 'changes': r['changes'], 'delta': r['delta'], 'risk_after': r['risk_after'], 'confidence': r['confidence'],
                                  'confidence_reason': r['confidence_reason'], 'warnings': r['warnings'], 'expected_net_value': ev, 'expected_net_value_reason': why,
                                  'worthwhile': None if ev is None else ev > 0, 'guardrail': chk.as_dict()})
    if any(c['expected_net_value'] is None for c in rec['candidates']):
        rec['reasons'].append('Cost-effectiveness was NOT assessed (business numbers or scenario costs are not configured). '
                              'Scenarios are ranked by the twin delta, which is model sensitivity, not a causal estimate.')
    pool = [c for c in rec['candidates'] if c['worthwhile'] is not False]
    for c in rec['candidates']:
        if c['worthwhile'] is False:
            rec['reasons'].append('%s skipped: expected net value %.2f is not positive.' % (c['name'], c['expected_net_value']))
        if c['guardrail']['status'] == 'blocked':
            rec['reasons'].append('%s blocked by guardrails: %s' % (c['name'], '; '.join(c['guardrail']['violations'])))
    chosen = next((c for c in pool if c['guardrail']['status'] == 'permitted'), None)
    decision = 'recommend'
    if chosen is None:
        chosen, decision = next((c for c in pool if c['guardrail']['status'] == 'needs_human_approval'), None), 'recommend_pending_approval'
    if chosen is None:
        rec.update(decision='no_action', guardrail=base.as_dict(), fired_by=sorted({r for c in rec['candidates'] for r in c['guardrail']['fired']}))
        rec['reasons'].append('No scenario is both worthwhile and permitted by the guardrails.')
        return rec
    rec.update(decision=decision, action=chosen, guardrail=chosen['guardrail'], fired_by=chosen['guardrail']['fired'])
    rec['reasons'].insert(0, 'Best %s action: %s (twin delta %.3f, model sensitivity).' % ('permitted' if decision == 'recommend' else 'unapproved', chosen['name'], chosen['delta']))
    if chosen['guardrail']['approvals_needed']:
        rec['reasons'].append('Needs human approval: ' + '; '.join(chosen['guardrail']['approvals_needed']))
    if chosen['confidence'] == 'low':
        rec['reasons'].append('Low confidence: ' + chosen['confidence_reason'])
    rec['reasons'].extend('Warning: ' + w for w in chosen['warnings'])
    rec['shap_reasons'] = E.top_reasons(df, bundle)[0]
    return rec
