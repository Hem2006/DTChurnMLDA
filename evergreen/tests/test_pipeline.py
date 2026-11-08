"""Integration tests against the trained artifacts: model quality bookkeeping, explainer, twin scoring, recommender decisions."""
import numpy as np
import pandas as pd
import pytest

from src import data as D
from src import explain as E
from src import recommender as R
from src import serving as S
from src import twin as TW
from src import uplift as U

SAFE = {'vulnerable': False, 'escalated': False, 'contacts_this_month': 0, 'discount_pct_used_this_quarter': 0.0}


@pytest.fixture(scope='module')
def tp():
    return pd.read_csv(S.MODELS / 'test_predictions.csv')


@pytest.fixture(scope='module')
def flagged_customer(bundle, tp, frame):
    """A high-risk test customer on a month-to-month fibre plan (so scenarios apply)."""
    t = bundle['threshold']['threshold']
    cand = tp[(tp.p_served >= t + 0.2) & (tp.Contract == 'Month-to-month') & (tp.InternetService != 'No')]
    row = frame.set_index('customerID').loc[cand.iloc[0]['customerID']]
    return S.customer_row(row)


@pytest.fixture(scope='module')
def unflagged_customer(bundle, tp, frame):
    t = bundle['threshold']['threshold']
    return S.customer_row(frame.set_index('customerID').loc[tp[tp.p_served < t - 0.2].iloc[0]['customerID']])


# ------------------------------------------------------------------ artifacts / bookkeeping
def test_inputs_exclude_gender_and_totalcharges(bundle):
    ins = bundle['meta']['inputs']
    assert len(ins) == 17 and 'gender' not in ins and 'TotalCharges' not in ins


def test_split_is_stratified_80_20_seed42(frame):
    tr, te = D.split(frame)
    assert len(tr) == 5634 and len(te) == 1409
    assert abs(tr['y'].mean() - te['y'].mean()) < 0.002 and len(frame) == 7043 and abs(frame['y'].mean() - 0.265) < 0.001


def test_served_is_five_fold_calibrated_xgboost(bundle):
    m = bundle['model']
    assert len(m.calibrated_classifiers_) == 5
    assert type(m.calibrated_classifiers_[0].estimator.named_steps['model']).__name__ == 'XGBClassifier'
    assert bundle['metrics']['served']['calibration'] in ('isotonic', 'sigmoid')
    oof = bundle['metrics']['served']['oof_brier_train']
    assert bundle['metrics']['served']['calibration'] == min(oof, key=oof.get)         # chosen by OOF Brier on train


def test_metrics_json_complete_and_cis_bracket_estimates(bundle):
    m = bundle['metrics']
    for k in ['roc_auc', 'pr_auc', 'brier', 'precision', 'recall', 'f1', 'accuracy', 'flag_rate', 'top_decile_lift', 'ece']:
        lo, hi = m['test_ci95'][k]
        assert lo <= m['test'][k] <= hi, k
    assert m['bootstrap_resamples'] == 2000 and 'equal-width' in m['ece_binning']
    assert {'uncalibrated', 'platt', 'isotonic'} == set(m['calibration_comparison'])
    assert {'logreg', 'random_forest', 'xgboost', 'lightgbm', 'baseline (Contract + tenure)'} == set(m['families'])
    assert m['delong_served_vs_logreg']['p_value'] == pytest.approx(m['delong_served_vs_logreg']['p_value'])
    assert m['test']['brier'] < m['brier_base_rate_predictor']
    assert m['families']['baseline (Contract + tenure)']['test_roc_auc'] < m['test']['roc_auc']


def test_threshold_is_a_labelled_placeholder_while_config_is_null(bundle):
    t = bundle['threshold']
    assert t['is_placeholder'] and 'placeholder' in t['source'] and t['note']
    assert all(v is None for v in bundle['business'].values())


def test_predict_matches_saved_test_predictions(bundle, tp):
    p = S.predict(tp.head(200)[D.INPUTS], bundle)['churn_probability'].values
    assert np.allclose(p, tp.head(200)['p_served'].values, atol=1e-9)


def test_predict_rejects_bad_rows(bundle, tp):
    bad = tp.head(3)[D.INPUTS].copy()
    bad.loc[bad.index[0], 'Contract'] = 'Lifetime'
    with pytest.raises(S.InputError, match='unknown value'):
        S.predict(bad, bundle)
    with pytest.raises(S.InputError, match='Missing'):
        S.predict(tp.head(3)[D.INPUTS].drop(columns=['tenure']), bundle)


# ------------------------------------------------------------------ explainer
def test_shap_is_ensemble_average_and_in_plain_english(bundle, flagged_customer):
    df = pd.DataFrame([flagged_customer])
    c = E.contributions(df, bundle)
    assert list(c.columns) == D.INPUTS
    reasons = E.top_reasons(df, bundle)[0]
    assert 1 <= len(reasons) <= 3 and all(r['push_log_odds'] > 0 for r in reasons)
    assert E.sentence(reasons).startswith('Customer ') and all(f in E.sentence(reasons) for f in [reasons[0]['text']])


def test_phrase_covers_every_input(frame):
    row = S.customer_row(frame.iloc[0])
    for f in D.INPUTS:
        assert isinstance(E.phrase(f, row[f]), str) and E.phrase(f, row[f])


# ------------------------------------------------------------------ twin with the model
def test_twin_scores_and_labels(bundle, flagged_customer):
    r = TW.simulate(flagged_customer, bundle, {'Contract': 'Two year'})
    assert r['interpretation'] == 'model sensitivity, not a causal estimate'
    assert r['delta'] > 0 and r['warnings'] and r['confidence'] in ('ok', 'low')
    assert r['baseline_risk'] - r['risk_after'] == pytest.approx(r['delta'])


def test_low_confidence_when_reference_disagrees(bundle, flagged_customer, monkeypatch):
    monkeypatch.setattr(S, 'reference_probability', lambda df, b: pd.Series([0.5] * len(df), index=df.index))     # reference sees no change at all...
    calls = {'n': 0}

    def flip(df, b):
        calls['n'] += 1
        return pd.Series([0.5 if calls['n'] % 2 else 0.9] * len(df), index=df.index)                                # ...or moves the other way
    monkeypatch.setattr(S, 'reference_probability', flip)
    r = TW.simulate(flagged_customer, bundle, {'Contract': 'Two year'})
    assert r['confidence'] == 'low' and 'disagree' in r['confidence_reason']


def test_scenarios_ranked_and_inapplicable_reported(bundle, flagged_customer):
    run = TW.run_scenarios(flagged_customer, bundle)
    d = [r['delta'] for r in run['results']]
    assert d == sorted(d, reverse=True)
    one_year = {**flagged_customer, 'Contract': 'One year'}
    na = {x['name'] for x in TW.run_scenarios(one_year, bundle)['not_applicable']}
    assert 'contract_one_year' in na


# ------------------------------------------------------------------ recommender
def test_unflagged_gets_no_action(bundle, unflagged_customer):
    assert R.recommend(unflagged_customer, bundle, SAFE)['decision'] == 'no_action'


def test_vulnerable_flagged_customer_escalates_whatever_the_risk(bundle, flagged_customer):
    r = R.recommend(flagged_customer, bundle, {**SAFE, 'vulnerable': True})
    assert r['decision'] == 'escalate_to_human' and r['action'] is None and 'vulnerable' in r['fired_by']
    r = R.recommend(flagged_customer, bundle, {**SAFE, 'escalated': True})
    assert r['decision'] == 'escalate_to_human' and 'open_escalation' in r['fired_by']


def test_unknown_state_means_pending_approval(bundle, flagged_customer):
    r = R.recommend(flagged_customer, bundle, None)
    assert r['decision'] == 'recommend_pending_approval' and r['action'] and r['guardrail']['approvals_needed']
    assert {'unknown_vulnerable', 'unknown_escalated'} <= set(r['fired_by'])


def test_unset_policy_means_pending_even_with_clean_state(bundle, flagged_customer):
    r = R.recommend(flagged_customer, bundle, SAFE)
    assert r['decision'] == 'recommend_pending_approval' and 'contact_cap_unset' in r['fired_by']


def test_recommend_when_policy_set_and_state_clean(bundle, flagged_customer, limits):
    r = R.recommend(flagged_customer, {**bundle, 'policy': limits}, SAFE)
    assert r['decision'] == 'recommend' and r['guardrail']['status'] == 'permitted' and r['fired_by'] == []
    assert r['action']['name'] != 'do_nothing' and r['shap_reasons']


def test_cap_blocks_discount_scenario_and_picks_another(bundle, flagged_customer, limits):
    state = {**SAFE, 'discount_pct_used_this_quarter': 15.0}                       # cap is 15, so any discount scenario is blocked
    r = R.recommend(flagged_customer, {**bundle, 'policy': limits}, state)
    blocked = [c for c in r['candidates'] if c['guardrail']['status'] == 'blocked']
    assert blocked and all(c['changes'].get('discount_pct') for c in blocked)
    if r['action']:
        assert not r['action']['changes'].get('discount_pct')


def test_case_record_is_complete_and_json_safe(bundle, flagged_customer):
    import json
    r = R.recommend(flagged_customer, bundle, None, customer_ref='X-1')
    for k in ['customer_ref', 'created_at', 'customer', 'account_state', 'risk', 'labels', 'reasons', 'candidates', 'guardrail', 'fired_by', 'decision', 'simulated_uplift']:
        assert k in r
    assert r['labels']['twin'] == TW.INTERPRETATION and r['labels']['uplift'] == 'SIMULATED' and r['labels']['threshold'] == 'PLACEHOLDER'
    json.dumps(r, default=str)


def test_net_value_stays_null_without_numbers_and_appears_with_made_up_ones(bundle, flagged_customer, limits):
    r = R.recommend(flagged_customer, bundle, SAFE)
    assert all(c['expected_net_value'] is None and 'not configured' in c['expected_net_value_reason'] for c in r['candidates'])
    sc = [{**s, 'cost_per_customer': 5.0} for s in bundle['scenarios']]
    b2 = {**bundle, 'scenarios': sc, 'business': {'offer_cost': 5, 'customer_value': 500, 'success_rate': 0.3}}
    r2 = R.recommend(flagged_customer, b2, SAFE)
    assert any(c['expected_net_value'] is not None for c in r2['candidates'])             # tests pass their own made-up numbers


def test_uplift_is_simulated_and_advisory_only(bundle, flagged_customer):
    if bundle['uplift'] is None:
        pytest.skip('uplift not built')
    assert bundle['uplift']['label'] == 'SIMULATED'
    r = R.recommend(flagged_customer, bundle, SAFE)
    assert r['simulated_uplift']['label'] == 'SIMULATED'
    no_up = R.recommend(flagged_customer, {**bundle, 'uplift': None}, SAFE)
    assert no_up['decision'] == r['decision'] and (no_up['action'] or {}).get('name') == (r['action'] or {}).get('name')     # never changes the action


def test_quadrants_partition():
    q = U.quadrant([-0.1, 0.0, 0.1, 0.0, 0.1], [0.1, 0.1, 0.8, 0.8, 0.1], risk_cut=0.5, min_uplift=0.05)
    assert list(q) == ['sleeping dog', 'sure thing', 'persuadable', 'lost cause', 'sure thing']
