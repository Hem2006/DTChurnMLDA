"""Combine risk, the digital twin's ranked scenarios, SIMULATED uplift, guardrails and the cost threshold into one recommendation.

The twin numbers are model sensitivity, not causal estimates, and the uplift is SIMULATED; both are labelled in the output and
neither is presented as a finding. The LLM never chooses the action: this module does, deterministically.
"""
from __future__ import annotations

import warnings

import pandas as pd

import churn_serving as cs
from app import guardrails as gr
from app import twin as tw
from app import uplift as up

DECISIONS = ('recommend', 'recommend_pending_approval', 'no_action', 'escalate_to_human')


def _summary(r: dict, check: gr.GuardrailResult) -> dict:
    return {'name': r['name'], 'changes': r['changes'], 'risk_drop': r['risk_drop'], 'risk_drop_adjusted': r['risk_drop_adjusted'],
            'expected_net_value': r['expected_net_value'], 'expected_net_value_reason': r['expected_net_value_reason'],
            'confidence': r['confidence'], 'confidence_reason': r['confidence_reason'], 'warnings': r['warnings'],
            'guardrails': check.as_dict()}


def recommend(customer: dict, bundle: dict, account_state: dict | None = None) -> dict:
    meta, config = bundle['meta'], bundle.get('config') or {}
    rules = bundle['guardrails']
    df = pd.DataFrame([customer])
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        pred = cs.predict(df, bundle).iloc[0]
    risk = {'churn_probability': float(pred['churn_probability']), 'flagged': bool(pred['flagged']), 'threshold': float(pred['threshold']),
            'threshold_source': pred['threshold_source'], 'threshold_is_placeholder': bool(pred['threshold_is_placeholder'])}
    out = {'interpretation': tw.INTERPRETATION, 'risk': risk, 'reasons': [], 'action': None, 'candidates': [], 'shap_reasons': None,
           'simulated_uplift': None, 'not_applicable': [], 'ranked_by': None, 'account_state_supplied': account_state is not None}
    if risk['threshold_is_placeholder']:
        out['reasons'].append('Threshold is a PLACEHOLDER (F1-optimal), not a cost-based threshold: offer_cost, customer_value and success_rate are not set in config.yaml.')

    # 1. Vulnerable / escalated accounts: no contact at all, hand to a human, whatever the risk.
    base = gr.check_action({}, account_state, rules)
    if base.escalate_to_human:
        out.update(decision='escalate_to_human', action=None, guardrails=base.as_dict())
        out['reasons'] = base.violations + out['reasons']
        return out

    # 2. Not flagged -> nothing to do.
    if not risk['flagged']:
        out.update(decision='no_action', guardrails=base.as_dict())
        out['reasons'].insert(0, 'Churn probability %.3f is below the threshold %.3f: no retention action.' % (risk['churn_probability'], risk['threshold']))
        return out

    # 3. SIMULATED uplift (advisory; never the sole basis for an action).
    ub = bundle.get('uplift')
    if ub is not None:
        u = up.score_uplift(df, ub, meta).iloc[0]
        out['simulated_uplift'] = {'label': up.SIMULATED, 'note': up.NOTE, 'simulated_uplift': float(u['simulated_uplift']),
                                   'simulated_p_churn_control': float(u['simulated_p_churn_control']),
                                   'simulated_p_churn_treated': float(u['simulated_p_churn_treated'])}
        if u['simulated_uplift'] <= 0:
            out['reasons'].append('SIMULATED uplift is not positive for this customer (%.3f): under the simulation assumptions an offer would not help. '
                                  'This is a simulation output, not a finding.' % u['simulated_uplift'])
        else:
            out['reasons'].append('SIMULATED uplift is %+.3f (simulation assumptions, not a finding).' % u['simulated_uplift'])

    # 4. Twin scenarios, ranked; each checked against guardrails.
    run = tw.run_scenarios(customer, bundle)
    out['not_applicable'] = run['not_applicable']
    out['ranked_by'] = run['ranked_by']
    cands = []
    for r in run['results']:
        if r['name'] == 'do_nothing':
            continue
        chk = gr.check_action({'discount_pct': r['changes'].get('discount_pct', 0)}, account_state, rules)
        c = _summary(r, chk)
        c['worthwhile'] = None if r['expected_net_value'] is None else r['expected_net_value'] > 0
        cands.append(c)
    out['candidates'] = cands
    if run['ranked_by'] != 'expected_net_value':
        out['reasons'].append('Cost-effectiveness was NOT assessed: expected net value needs success_rate, customer_value and a cost per scenario '
                              '(config.yaml). Scenarios are ranked by the twin\'s risk drop, which is model sensitivity, not a causal estimate.')

    pool = [c for c in cands if c['worthwhile'] is not False]
    for c in cands:
        if c['worthwhile'] is False:
            out['reasons'].append('%s skipped: expected net value %.2f is not positive.' % (c['name'], c['expected_net_value']))
    for c in cands:
        if c['guardrails']['status'] == 'blocked':
            out['reasons'].append('%s blocked by guardrails: %s' % (c['name'], '; '.join(c['guardrails']['violations'])))
    chosen = next((c for c in pool if c['guardrails']['status'] == 'permitted'), None)
    decision = 'recommend'
    if chosen is None:
        chosen = next((c for c in pool if c['guardrails']['status'] == 'needs_human_approval'), None)
        decision = 'recommend_pending_approval'
    if chosen is None:
        out.update(decision='no_action', guardrails=base.as_dict())
        out['reasons'].append('No scenario is both worthwhile and permitted by the guardrails.')
        return out

    out['decision'], out['action'], out['guardrails'] = decision, chosen, chosen['guardrails']
    out['reasons'].insert(0, 'Best %s action: %s (twin risk drop %.3f, model sensitivity).' % (
        'permitted' if decision == 'recommend' else 'unapproved', chosen['name'], chosen['risk_drop']))
    if chosen['guardrails']['approvals_needed']:
        out['reasons'].append('Needs human approval before use: ' + '; '.join(chosen['guardrails']['approvals_needed']))
    if chosen['confidence'] == 'low':
        out['reasons'].append('Low confidence: ' + chosen['confidence_reason'])
    out['reasons'].extend('Warning: ' + w for w in chosen['warnings'])
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        sr = cs.top_reasons(df, bundle)
    out['shap_reasons'] = sr.iloc[0]['reasons'] if len(sr) else None
    return out
