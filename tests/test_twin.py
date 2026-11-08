import json

import numpy as np
import pandas as pd
import pytest

import churn_serving as cs
from app import twin as tw
from conftest import ROOT

META = json.loads((ROOT / 'churn_model_meta.json').read_text())
SCENARIOS = tw.load_scenarios(ROOT / 'scenarios.yaml')

FIBER = {'SeniorCitizen': 0, 'Partner': 'No', 'Dependents': 'No', 'tenure': 5, 'PhoneService': 'Yes', 'MultipleLines': 'No',
         'InternetService': 'Fiber optic', 'OnlineSecurity': 'No', 'OnlineBackup': 'No', 'DeviceProtection': 'No', 'TechSupport': 'No',
         'StreamingTV': 'No', 'StreamingMovies': 'No', 'Contract': 'Month-to-month', 'PaperlessBilling': 'Yes',
         'PaymentMethod': 'Electronic check', 'MonthlyCharges': 80.0}
NO_NET = dict(FIBER, InternetService='No', MonthlyCharges=20.0, **{c: 'No internet service' for c in tw.ADDONS})
NO_PHONE = dict(FIBER, PhoneService='No', MultipleLines='No phone service', InternetService='DSL')


class Stub:
    """Deterministic stand-in for a model: risk depends only on Contract and TechSupport."""
    def __init__(self, by_contract, tech=0.0):
        self.by_contract, self.tech = by_contract, tech

    def predict_proba(self, X):
        p = np.array([self.by_contract[c] - (self.tech if t == 'Yes' else 0) for c, t in zip(X['Contract'], X['TechSupport'])])
        return np.column_stack([1 - p, p])


def stub_bundle(main, ref, config=None):
    cfg = {k: None for k in cs.CONFIG_KEYS + cs.EXTRA_CONFIG_KEYS}
    cfg.update(config or {})
    return {'meta': META, 'config': cfg, 'model': main, 'logreg_ref': ref, 'scenarios': SCENARIOS}


MAIN = Stub({'Month-to-month': .50, 'One year': .30, 'Two year': .20}, tech=.05)
REF_AGREE = Stub({'Month-to-month': .60, 'One year': .42, 'Two year': .30}, tech=.04)      # contract drop .18 vs .20
REF_FAR = Stub({'Month-to-month': .50, 'One year': .45, 'Two year': .40}, tech=.0)         # contract drop .05 vs .20
REF_SIGN = Stub({'Month-to-month': .40, 'One year': .50, 'Two year': .60}, tech=.0)        # opposite direction


# ---------------------------------------------------------------- coherence (no trained model needed)
def test_no_internet_forces_addons():
    t = CT(FIBER).apply({'InternetService': 'No'})
    assert all(t.row[c] == 'No internet service' for c in tw.ADDONS)


@pytest.mark.parametrize('value', ['Yes', 'No'])
def test_no_internet_customer_cannot_get_tech_support(value):
    with pytest.raises(tw.TwinError, match='TechSupport'):
        CT(NO_NET).apply({'TechSupport': value})


def test_switching_to_no_internet_with_explicit_yes_addon_rejected():
    with pytest.raises(tw.TwinError, match='forces'):
        CT(FIBER).apply({'InternetService': 'No', 'StreamingTV': 'Yes'})


def test_adding_internet_requires_explicit_addons():
    with pytest.raises(tw.TwinError, match='only be'):
        CT(NO_NET).apply({'InternetService': 'DSL'})


def test_phone_no_forces_multiple_lines():
    t = CT(FIBER).apply({'PhoneService': 'No'})
    assert t.row['MultipleLines'] == 'No phone service'


def test_phone_no_with_multiple_lines_yes_rejected():
    with pytest.raises(tw.TwinError, match='MultipleLines'):
        CT(FIBER).apply({'PhoneService': 'No', 'MultipleLines': 'Yes'})
    with pytest.raises(tw.TwinError, match='MultipleLines'):
        CT(NO_PHONE).apply({'MultipleLines': 'Yes'})


def test_direct_monthly_charges_edit_rejected():
    with pytest.raises(tw.TwinError, match='discount_pct'):
        CT(FIBER).apply({'MonthlyCharges': 10})


def test_tenure_change_rejected_and_contract_change_keeps_tenure():
    with pytest.raises(tw.TwinError, match='tenure'):
        CT(FIBER).apply({'tenure': 40})
    assert CT(FIBER).apply({'Contract': 'Two year'}).row['tenure'] == FIBER['tenure']


@pytest.mark.parametrize('changes', [{'Contract': 'Three year'}, {'InternetService': 'Cable'}, {'PaymentMethod': 'Cash'},
                                     {'TechSupport': 'yes'}, {'Contract': None}])
def test_unknown_category_rejected(changes):
    with pytest.raises(tw.TwinError, match='not an allowed value'):
        CT(FIBER).apply(changes)


@pytest.mark.parametrize('changes', [{'Colour': 'red'}, {'gender': 'Male'}, {'SeniorCitizen': 1}, {'Partner': 'Yes'}, {'TotalCharges': 1}])
def test_unknown_or_non_actionable_field_rejected(changes):
    with pytest.raises(tw.TwinError):
        CT(FIBER).apply(changes)


@pytest.mark.parametrize('d', [-1, 30.5, 31, 100, 'ten', None, True, float('nan')])
def test_discount_bounds(d):
    with pytest.raises(tw.TwinError, match='discount_pct'):
        CT(FIBER).apply({'discount_pct': d})


@pytest.mark.parametrize('d', [0, 10, 30])
def test_discount_applies_to_monthly_charges_not_compounding(d):
    t = CT(FIBER).apply({'discount_pct': 10}).apply({'discount_pct': d})
    assert t.row['MonthlyCharges'] == pytest.approx(80.0 * (1 - d / 100))


def test_incoherent_customer_rejected_at_construction():
    with pytest.raises(tw.TwinError):
        CT(dict(NO_NET, TechSupport='Yes'))
    with pytest.raises(tw.TwinError, match='exactly'):
        CT({k: v for k, v in FIBER.items() if k != 'Contract'})


def test_original_twin_is_immutable():
    t = CT(FIBER)
    before = t.as_dict()
    t2 = t.apply({'Contract': 'One year', 'TechSupport': 'Yes', 'discount_pct': 20})
    assert t2 is not t and t.as_dict() == before and t2.row['Contract'] == 'One year'
    with pytest.raises(TypeError):
        t.row['Contract'] = 'Two year'
    with pytest.raises(AttributeError):
        t.foo = 1
    src = dict(FIBER)
    t3 = CT(src)
    src['Contract'] = 'Two year'
    assert t3.row['Contract'] == 'Month-to-month'           # snapshot is a copy


def CT(customer):
    return tw.CustomerTwin(customer, META)


# ---------------------------------------------------------------- not_applicable
def na(result):
    return {r['name']: r for r in result['not_applicable']}


def test_not_applicable_cases_have_reasons_and_are_never_dropped():
    b = stub_bundle(MAIN, REF_AGREE)
    r = tw.run_scenarios(NO_NET, b)
    assert {'add_tech_support', 'combined_package'} <= set(na(r))
    assert 'InternetService' in na(r)['add_tech_support']['reason']
    assert {x['name'] for x in r['results']} | set(na(r)) == set(SCENARIOS)           # nothing silently skipped
    r = tw.run_scenarios(dict(FIBER, Contract='One year'), b)
    assert 'Contract' in na(r)['contract_one_year']['reason']
    r = tw.run_scenarios(dict(FIBER, Contract='Two year'), b)
    assert {'contract_one_year', 'combined_package'} <= set(na(r))
    r = tw.run_scenarios(dict(FIBER, TechSupport='Yes'), b)
    assert 'TechSupport' in na(r)['add_tech_support']['reason']
    assert all(x['interpretation'] == tw.INTERPRETATION for x in r['not_applicable'])


def test_unknown_scenario_name_rejected():
    with pytest.raises(tw.TwinError, match='Unknown scenario'):
        tw.run_scenarios(FIBER, stub_bundle(MAIN, REF_AGREE), ['nope'])


def test_subset_of_scenarios():
    r = tw.run_scenarios(FIBER, stub_bundle(MAIN, REF_AGREE), ['do_nothing', 'contract_one_year'])
    assert {x['name'] for x in r['results']} == {'do_nothing', 'contract_one_year'}


# ---------------------------------------------------------------- honesty features
def test_every_result_carries_interpretation():
    r = tw.run_scenarios(FIBER, stub_bundle(MAIN, REF_AGREE))
    assert r['interpretation'] == 'model sensitivity, not a causal estimate'
    assert r['results'] and all(x['interpretation'] == 'model sensitivity, not a causal estimate' for x in r['results'])


def test_contract_change_carries_selection_warning_others_do_not():
    r = {x['name']: x for x in tw.run_scenarios(FIBER, stub_bundle(MAIN, REF_AGREE))['results']}
    for n in ('contract_one_year', 'combined_package'):
        assert any('selection' in w and 'much smaller' in w for w in r[n]['warnings'])
    for n in ('do_nothing', 'price_cut_10', 'add_tech_support'):
        assert r[n]['warnings'] == []
    custom = tw.simulate(FIBER, stub_bundle(MAIN, REF_AGREE), {'Contract': 'Two year'})
    assert custom['warnings'] and custom['interpretation'] == tw.INTERPRETATION


def test_risk_numbers_from_stub():
    r = tw.simulate(FIBER, stub_bundle(MAIN, REF_AGREE, {'offer_cost': 20, 'customer_value': 800, 'success_rate': .3}), {'Contract': 'One year'})
    assert (r['baseline_risk'], r['risk_after']) == (pytest.approx(.5), pytest.approx(.3))
    assert r['risk_drop'] == pytest.approx(.2)
    assert r['flagged_before'] is True and r['flagged_after'] is True        # threshold 0.083 here
    assert r['twin_effect_realization'] is None and r['risk_drop_adjusted'] is None


def test_realization_default_null_and_when_set_shows_raw_and_adjusted():
    b = stub_bundle(MAIN, REF_AGREE, {'twin_effect_realization': 0.3})
    r = tw.simulate(FIBER, b, {'Contract': 'One year'})
    assert r['risk_drop'] == pytest.approx(.2) and r['risk_drop_adjusted'] == pytest.approx(.06) and r['twin_effect_realization'] == 0.3
    assert tw.simulate(FIBER, stub_bundle(MAIN, REF_AGREE), {'Contract': 'One year'})['risk_drop_adjusted'] is None


# ---------------------------------------------------------------- agreement with logistic regression
def test_agreement_ok():
    r = tw.simulate(FIBER, stub_bundle(MAIN, REF_AGREE), {'Contract': 'One year'})
    assert r['confidence'] == 'ok' and r['reference_logreg']['risk_drop'] == pytest.approx(.18)


def test_agreement_low_when_gap_over_010():
    r = tw.simulate(FIBER, stub_bundle(MAIN, REF_FAR), {'Contract': 'One year'})
    assert r['confidence'] == 'low' and 'differs by' in r['confidence_reason'] and '0.10' in r['confidence_reason']


def test_agreement_low_when_sign_differs():
    r = tw.simulate(FIBER, stub_bundle(MAIN, REF_SIGN), {'Contract': 'One year'})
    assert r['confidence'] == 'low' and 'direction' in r['confidence_reason']


def test_agreement_unknown_without_reference_model():
    r = tw.simulate(FIBER, stub_bundle(MAIN, None), {'Contract': 'One year'})
    assert r['confidence'] == 'unknown'


# ---------------------------------------------------------------- expected net value and ranking
def test_net_value_null_with_reason_when_numbers_missing():
    r = tw.simulate(FIBER, stub_bundle(MAIN, REF_AGREE), {'Contract': 'One year'}, name='contract_one_year')
    assert r['expected_net_value'] is None and 'success_rate' in r['expected_net_value_reason'] and 'cost_per_customer' in r['expected_net_value_reason']
    assert tw.run_scenarios(FIBER, stub_bundle(MAIN, REF_AGREE))['ranked_by'] == 'risk_drop'


def test_net_value_formula_and_ranking():
    cfg = {'offer_cost': 20, 'customer_value': 800, 'success_rate': .3,
           'scenario_costs': {'price_cut_10': 5.0, 'contract_one_year': 20.0, 'add_tech_support': 1.0, 'combined_package': 30.0}}
    r = tw.run_scenarios(FIBER, stub_bundle(MAIN, REF_AGREE, cfg))
    by = {x['name']: x for x in r['results']}
    assert by['contract_one_year']['expected_net_value'] == pytest.approx(.2 * .3 * 800 - 20)      # 28
    assert by['do_nothing']['expected_net_value'] == pytest.approx(0)
    assert r['ranked_by'] == 'expected_net_value'
    evs = [x['expected_net_value'] for x in r['results']]
    assert evs == sorted(evs, reverse=True)


def test_net_value_uses_adjusted_drop_when_realization_set():
    cfg = {'customer_value': 800, 'success_rate': .3, 'twin_effect_realization': .5, 'scenario_costs': {'contract_one_year': 20.0}}
    r = tw.simulate(FIBER, stub_bundle(MAIN, REF_AGREE, cfg), {'Contract': 'One year'}, name='contract_one_year', cost_per_customer=None)
    assert r['expected_net_value'] == pytest.approx(.1 * .3 * 800 - 20) and r['expected_net_value_basis'] == 'adjusted_risk_drop'


def test_ranked_by_risk_drop_when_costs_unknown():
    r = tw.run_scenarios(FIBER, stub_bundle(MAIN, REF_AGREE))
    drops = [x['risk_drop'] for x in r['results']]
    assert drops == sorted(drops, reverse=True) and r['results'][0]['name'] == 'combined_package'


# ---------------------------------------------------------------- real served model
def test_real_model_scores_twin(bundle, test_frame):
    X, _ = test_frame
    row = X.iloc[0][META['input_columns']].to_dict()
    b = dict(bundle, scenarios=SCENARIOS)
    row = {k: (v.item() if hasattr(v, 'item') else v) for k, v in row.items()}
    r = tw.run_scenarios(row, b)
    assert r['interpretation'] == tw.INTERPRETATION and 0 <= r['baseline_risk'] <= 1
    t = tw.CustomerTwin(row, META)
    assert t.score(b)['probability'] == pytest.approx(r['baseline_risk'])
    dn = next(x for x in r['results'] if x['name'] == 'do_nothing')
    assert dn['risk_drop'] == 0 and dn['confidence'] == 'ok'
    assert all(x['reference_logreg'] is not None for x in r['results'])
