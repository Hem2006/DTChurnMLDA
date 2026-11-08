import pytest
from fastapi.testclient import TestClient

from app import guardrails as gr
from app import recommend as rec
from app import twin as tw
from app import uplift as up
from app.main import app
from conftest import ROOT
from test_twin import FIBER, NO_NET

RULES = {'max_discount_pct_per_customer_per_quarter': 15, 'max_contacts_per_month': 2, 'pressure_language': gr.load_guardrails()['pressure_language']}
OK = {'vulnerable': False, 'escalated': False, 'contacts_this_month': 0, 'discount_pct_used_this_quarter': 0}
SAFE = dict(FIBER, tenure=70, Contract='Two year', MonthlyCharges=40.0, Partner='Yes', Dependents='Yes')      # low-risk customer
COSTED = {'offer_cost': 20, 'customer_value': 800, 'success_rate': .3,
          'scenario_costs': {'price_cut_10': 5.0, 'contract_one_year': 3.0, 'add_tech_support': 4.0, 'combined_package': 9.0}}


@pytest.fixture(scope='module')
def full(bundle):
    b = dict(bundle, scenarios=tw.load_scenarios(ROOT / 'scenarios.yaml'), guardrails=RULES, uplift=up.load(ROOT / up.ARTIFACT))
    assert b['uplift'] is not None, 'run: python -m app.uplift'
    return b


def with_config(b, **kw):
    return dict(b, config={**b['config'], **kw})


def test_recommend_permitted_action_with_reasons(full):
    r = rec.recommend(FIBER, full, OK)
    assert r['decision'] == 'recommend' and r['action']['guardrails']['status'] == 'permitted'
    assert r['action']['name'] != 'do_nothing' and r['risk']['flagged'] and r['reasons']
    assert r['interpretation'] == tw.INTERPRETATION
    assert r['shap_reasons'] and r['ranked_by'] == 'risk_drop'
    assert any('Cost-effectiveness was NOT assessed' in x for x in r['reasons'])
    assert any('PLACEHOLDER' in x for x in r['reasons'])


def test_uplift_always_labelled_simulated(full):
    r = rec.recommend(FIBER, full, OK)
    u = r['simulated_uplift']
    assert u['label'] == 'SIMULATED' and 'SIMULATED' in u['note']
    assert any('SIMULATED' in x for x in r['reasons'])


def test_recommend_works_without_uplift_artifact(full):
    r = rec.recommend(FIBER, dict(full, uplift=None), OK)
    assert r['simulated_uplift'] is None and r['decision'] == 'recommend'


def test_not_flagged_means_no_action(full):
    r = rec.recommend(SAFE, full, OK)
    assert not r['risk']['flagged'] and r['decision'] == 'no_action' and r['action'] is None and 'below the threshold' in r['reasons'][0]


@pytest.mark.parametrize('flag', ['vulnerable', 'escalated'])
def test_vulnerable_or_escalated_escalates_to_human_even_if_high_risk(full, flag):
    r = rec.recommend(FIBER, full, {**OK, flag: True})
    assert r['decision'] == 'escalate_to_human' and r['action'] is None and r['candidates'] == []
    assert r['guardrails']['escalate_to_human'] and not r['guardrails']['contact_allowed']


def test_missing_account_state_gives_pending_approval_never_permitted(full):
    r = rec.recommend(FIBER, full, None)
    assert r['decision'] == 'recommend_pending_approval' and r['action']['guardrails']['status'] == 'needs_human_approval'
    assert r['account_state_supplied'] is False and any('Needs human approval' in x for x in r['reasons'])


def test_unconfigured_limits_pending_approval(full):
    r = rec.recommend(FIBER, dict(full, guardrails={**RULES, 'max_contacts_per_month': None, 'max_discount_pct_per_customer_per_quarter': None}), OK)
    assert r['decision'] == 'recommend_pending_approval'


def test_discount_cap_skips_discount_scenarios(full):
    state = {**OK, 'discount_pct_used_this_quarter': 14}
    r = rec.recommend(FIBER, full, state)
    blocked = {c['name'] for c in r['candidates'] if c['guardrails']['status'] == 'blocked'}
    assert {'price_cut_10', 'combined_package'} <= blocked
    assert r['action']['name'] not in blocked and r['action']['changes'].get('discount_pct', 0) == 0
    assert any('blocked by guardrails' in x for x in r['reasons'])


def test_contact_limit_blocks_everything(full):
    r = rec.recommend(FIBER, full, {**OK, 'contacts_this_month': 2})
    assert r['decision'] == 'no_action' and r['action'] is None and 'No scenario is both worthwhile and permitted' in r['reasons'][-1]


def test_no_internet_customer_gets_only_applicable_scenarios(full):
    r = rec.recommend(dict(NO_NET, tenure=1, SeniorCitizen=1), full, OK)     # high-risk customer with no internet
    assert r['risk']['flagged']
    names = {c['name'] for c in r['candidates']}
    assert 'add_tech_support' not in names and 'combined_package' not in names
    assert {x['name'] for x in r['not_applicable']} >= {'add_tech_support', 'combined_package'}


def test_cost_threshold_is_used_and_ev_ranks(full):
    b = with_config(full, **COSTED)
    r = rec.recommend(FIBER, b, OK)
    assert r['ranked_by'] == 'expected_net_value' and r['risk']['threshold_source'].startswith('expected_value')
    assert not any('PLACEHOLDER' in x for x in r['reasons'])
    evs = [c['expected_net_value'] for c in r['candidates']]
    assert evs == sorted(evs, reverse=True)
    assert r['action']['expected_net_value'] == max(evs) and r['action']['expected_net_value'] > 0


def test_non_worthwhile_scenarios_are_skipped(full):
    b = with_config(full, **{**COSTED, 'scenario_costs': {k: 10_000.0 for k in COSTED['scenario_costs']}})
    r = rec.recommend(FIBER, b, OK)
    assert r['decision'] == 'no_action' and all(c['worthwhile'] is False for c in r['candidates'])
    assert any('not positive' in x for x in r['reasons'])


def test_contract_warning_and_low_confidence_surface_in_reasons(full):
    r = rec.recommend(FIBER, full, OK)
    for c in r['candidates']:
        if 'Contract' in c['changes']:
            assert any('selection' in w for w in c['warnings'])
    if r['action']['changes'].get('Contract'):
        assert any(x.startswith('Warning:') and 'selection' in x for x in r['reasons'])


# ---- API
@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        yield c


def test_api_recommend(client):
    r = client.post('/recommend', json={'customer': FIBER, 'account_state': OK})
    assert r.status_code == 200
    j = r.json()
    assert j['decision'] in rec.DECISIONS and j['simulated_uplift']['label'] == 'SIMULATED' and j['interpretation'] == tw.INTERPRETATION


def test_api_recommend_escalates(client):
    j = client.post('/recommend', json={'customer': FIBER, 'account_state': {'vulnerable': True}}).json()
    assert j['decision'] == 'escalate_to_human' and j['action'] is None


def test_api_recommend_no_state_needs_approval(client):
    j = client.post('/recommend', json={'customer': FIBER}).json()
    assert j['decision'] == 'recommend_pending_approval'


@pytest.mark.parametrize('body', [{'customer': FIBER, 'account_state': {'contacts_this_month': -1}},
                                  {'customer': FIBER, 'account_state': {'discount_pct_used_this_quarter': 150}},
                                  {'customer': FIBER, 'account_state': {'unknown': 1}},
                                  {'customer': dict(FIBER, Contract='x')}, {}])
def test_api_recommend_422(client, body):
    assert client.post('/recommend', json=body).status_code == 422
