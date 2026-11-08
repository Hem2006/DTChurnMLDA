import copy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import ROOT  # noqa: F401
from test_twin import FIBER, NO_NET, NO_PHONE


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:                       # runs the lifespan: bundle loaded once
        yield c


def test_bundle_loaded_once_at_startup(client):
    b = app.state.bundle
    client.get('/health'); client.post('/predict', json={'customers': [FIBER]})
    assert app.state.bundle is b and '_explainers' in b


def test_health(client):
    r = client.get('/health')
    assert r.status_code == 200
    j = r.json()
    assert j['model_loaded'] is True and j['versions_match'] is True and j['calibration_method'] == 'isotonic'
    assert j['threshold_is_placeholder'] is True and 'placeholder' in j['threshold_source'] and j['threshold_note'].startswith('PLACEHOLDER')
    assert 0 < j['threshold'] < 1 and j['logreg_reference_loaded'] is True


def test_predict_one_and_many(client):
    r = client.post('/predict', json={'customers': [FIBER, NO_NET, NO_PHONE]})
    assert r.status_code == 200
    res = r.json()['results']
    assert len(res) == 3
    for x in res:
        assert 0 <= x['churn_probability'] <= 1 and 'placeholder' in x['threshold_source'] and x['threshold_is_placeholder'] is True
    flagged = [x for x in res if x['flagged']]
    assert flagged, 'month-to-month fiber customer with 5 months tenure should be flagged'
    for x in res:
        if x['flagged']:
            assert x['reasons'] and all(d['log_odds_push'] > 0 for d in x['reasons'])
        else:
            assert x['reasons'] is None             # reasons for flagged customers only


@pytest.mark.parametrize('mutate', [
    lambda c: c.pop('Contract'),
    lambda c: c.update(Contract='Three year'),
    lambda c: c.update(tenure=-1),
    lambda c: c.update(tenure='abc'),
    lambda c: c.update(MonthlyCharges=-5),
    lambda c: c.update(MonthlyCharges='NaN'),
    lambda c: c.update(SeniorCitizen=2),
    lambda c: c.update(InternetService=1),
    lambda c: c.update(Surprise='x'),
])
def test_predict_bad_input_422(client, mutate):
    bad = copy.deepcopy(FIBER); mutate(bad)
    r = client.post('/predict', json={'customers': [bad]})
    assert r.status_code == 422 and r.json()['detail']


def test_predict_empty_and_malformed_422(client):
    assert client.post('/predict', json={'customers': []}).status_code == 422
    assert client.post('/predict', json={}).status_code == 422
    assert client.post('/predict', content='nope', headers={'content-type': 'application/json'}).status_code == 422


def test_422_message_names_field(client):
    r = client.post('/predict', json={'customers': [dict(FIBER, Contract='Three year')]})
    assert 'Contract' in str(r.json()['detail'])


def test_twin_scenarios_listing(client):
    r = client.get('/twin/scenarios')
    assert r.status_code == 200
    j = r.json()
    assert j['interpretation'] == 'model sensitivity, not a causal estimate'
    assert [s['name'] for s in j['scenarios']] == ['do_nothing', 'price_cut_10', 'contract_one_year', 'add_tech_support', 'combined_package']
    costs = {s['name']: s['cost_per_customer'] for s in j['scenarios']}
    assert costs['price_cut_10'] is None and costs['do_nothing'] == 0


def test_twin_run_all_scenarios(client):
    r = client.post('/twin/run', json={'customer': FIBER})
    assert r.status_code == 200
    j = r.json()
    assert j['interpretation'] == 'model sensitivity, not a causal estimate'
    assert len(j['results']) == 5 and not j['not_applicable'] and j['ranked_by'] == 'risk_drop'
    for x in j['results']:
        assert x['interpretation'] == 'model sensitivity, not a causal estimate'
        assert x['expected_net_value'] is None and x['expected_net_value_reason']
        assert x['confidence'] in ('ok', 'low') and x['reference_logreg'] is not None
        assert (any('selection' in w for w in x['warnings'])) == ('Contract' in x['changes'])
    drops = [x['risk_drop'] for x in j['results']]
    assert drops == sorted(drops, reverse=True)


def test_twin_run_subset_and_not_applicable(client):
    j = client.post('/twin/run', json={'customer': NO_NET, 'scenarios': ['add_tech_support', 'price_cut_10']}).json()
    assert [x['name'] for x in j['results']] == ['price_cut_10']
    assert j['not_applicable'][0]['name'] == 'add_tech_support' and 'InternetService' in j['not_applicable'][0]['reason']


def test_twin_run_errors_422(client):
    assert client.post('/twin/run', json={'customer': FIBER, 'scenarios': ['nope']}).status_code == 422
    assert client.post('/twin/run', json={'customer': dict(FIBER, Contract='x')}).status_code == 422
    assert client.post('/twin/run', json={'customer': dict(NO_NET, TechSupport='Yes')}).status_code == 422   # incoherent customer


def test_simulate_ok_with_warning(client):
    r = client.post('/simulate', json={'customer': FIBER, 'changes': {'Contract': 'One year', 'discount_pct': 5}})
    assert r.status_code == 200
    j = r.json()
    assert j['interpretation'] == 'model sensitivity, not a causal estimate' and j['name'] == 'custom'
    assert any('selection' in w for w in j['warnings']) and j['resulting_customer']['tenure'] == FIBER['tenure']
    assert j['resulting_customer']['MonthlyCharges'] == pytest.approx(76.0)


def test_simulate_matches_twin_run_single_implementation(client):
    run = {x['name']: x for x in client.post('/twin/run', json={'customer': FIBER}).json()['results']}['combined_package']
    sim = client.post('/simulate', json={'customer': FIBER, 'changes': run['changes']}).json()
    for k in ('baseline_risk', 'risk_after', 'risk_drop', 'flagged_after', 'warnings', 'confidence', 'resulting_customer'):
        assert sim[k] == run[k]


@pytest.mark.parametrize('customer,changes', [
    (NO_NET, {'TechSupport': 'Yes'}),
    (FIBER, {'PhoneService': 'No', 'MultipleLines': 'Yes'}),
    (FIBER, {'InternetService': 'No', 'StreamingTV': 'Yes'}),
    (FIBER, {'MonthlyCharges': 10}),
    (FIBER, {'tenure': 50}),
    (FIBER, {'Contract': 'Five year'}),
    (FIBER, {'discount_pct': 45}),
    (FIBER, {'gender': 'Male'}),
])
def test_simulate_incoherent_changes_422(client, customer, changes):
    r = client.post('/simulate', json={'customer': customer, 'changes': changes})
    assert r.status_code == 422 and r.json()['detail']


def test_simulate_does_not_mutate_between_calls(client):
    a = client.post('/simulate', json={'customer': FIBER, 'changes': {}}).json()
    client.post('/simulate', json={'customer': FIBER, 'changes': {'Contract': 'Two year', 'discount_pct': 30}})
    b = client.post('/simulate', json={'customer': FIBER, 'changes': {}}).json()
    assert a['baseline_risk'] == b['baseline_risk'] and a['risk_drop'] == 0
