"""Customer Galaxy: layout, per-customer twin and whole-galaxy campaigns. All numbers come from the served model; no business numbers."""
import time
import warnings

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import churn_serving as cs
from app import galaxy as gx
from app import twin as tw
from app.main import app


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope='module')
def galaxy(client):
    r = client.get('/galaxy')
    assert r.status_code == 200
    return r.json()


def test_galaxy_is_the_holdout_split_scored_by_the_served_model(client, galaxy, test_frame):
    X, y = test_frame
    nodes = galaxy['nodes']
    assert len(nodes) == len(X) == galaxy['summary']['n']
    b = app.state.bundle
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        p = cs.predict(X[b['meta']['input_columns']], b)['churn_probability'].to_numpy()
    assert np.allclose([n['p'] for n in nodes], p, atol=1e-4)
    assert sum(n['churned'] for n in nodes) == galaxy['summary']['actual_churners'] == int(y.sum())
    assert galaxy['threshold_is_placeholder'] is True                      # no business numbers were invented
    assert galaxy['twin_label'] == tw.INTERPRETATION


def test_higher_risk_sits_closer_to_the_core(galaxy):
    nodes = galaxy['nodes']
    r = np.array([np.hypot(*n['pos'][:2]) for n in nodes])
    p = np.array([n['p'] for n in nodes])
    assert np.corrcoef(r, p)[0, 1] < -0.9
    assert all(len(n['nb']) == gx.LOOKALIKES and n['id'] not in n['nb'] for n in nodes[:50])
    assert all(0 <= n['u'] <= 1 for n in nodes)


def test_twin_with_no_change_reproduces_the_node(client, galaxy):
    n = galaxy['nodes'][3]
    j = client.post('/galaxy/twin', json={'id': 3, 'changes': {}}).json()
    assert j['risk_after'] == pytest.approx(n['p'], abs=1e-4) and j['risk_drop'] == pytest.approx(0, abs=1e-9)
    assert np.allclose(j['pos_after'], n['pos'], atol=1e-3)                  # same customer -> same place in the galaxy
    assert j['interpretation'] == tw.INTERPRETATION and j['drivers'] and len(j['drivers']) <= 7


def test_twin_discount_lowers_risk_and_moves_the_node_outwards(client, galaxy):
    ids = [n['id'] for n in galaxy['nodes'] if n['c']['Contract'] == 'Month-to-month' and n['p'] > 0.5][:1]
    j = client.post('/galaxy/twin', json={'id': ids[0], 'changes': {'Contract': 'Two year'}}).json()
    assert j['risk_after'] < j['baseline_risk']
    assert np.hypot(*j['pos_after'][:2]) > np.hypot(*galaxy['nodes'][ids[0]]['pos'][:2])
    assert any('selection' in w for w in j['warnings'])                       # the contract caveat is carried through


def test_twin_rejects_bad_input(client):
    assert client.post('/galaxy/twin', json={'id': 99999, 'changes': {}}).status_code == 422
    assert client.post('/galaxy/twin', json={'id': 0, 'changes': {'tenure': 3}}).status_code == 422
    assert client.post('/galaxy/twin', json={'id': 0, 'changes': {'discount_pct': 90}}).status_code == 422


def test_campaign_sums_and_is_fast(client, galaxy):
    t = time.time()
    j = client.post('/galaxy/campaign', json={'scenario': 'contract_one_year'}).json()
    assert time.time() - t < 8
    s = galaxy['summary']
    assert j['before']['expected_churners'] == pytest.approx(s['expected_churners'], rel=1e-6)
    assert j['affected'] == len(j['touched']) == len(j['p_after']) == len(j['pos_after']) and j['affected'] > 0
    assert j['after']['expected_churners'] < j['before']['expected_churners']
    assert j['before']['flagged'] == s['flagged']
    nodes = galaxy['nodes']
    assert all(nodes[i]['c']['Contract'] == 'Month-to-month' for i in j['touched'])      # scenario rule: not already on a longer contract
    assert 'not a causal estimate' in j['note'] and j['interpretation'] == tw.INTERPRETATION
    assert any('selection' in w for w in j['warnings'])                                  # contract caveat travels with the campaign
    assert client.post('/galaxy/campaign', json={'scenario': 'price_cut_10'}).json()['warnings'] == []


def test_campaign_matches_the_single_customer_twin(client, galaxy):
    j = client.post('/galaxy/campaign', json={'changes': {'discount_pct': 10}, 'flagged_only': True}).json()
    for k in (0, len(j['touched']) // 2):
        i = j['touched'][k]
        one = client.post('/galaxy/twin', json={'id': i, 'changes': {'discount_pct': 10}}).json()
        assert j['p_after'][k] == pytest.approx(one['risk_after'], abs=1e-4)
        assert np.allclose(j['pos_after'][k], one['pos_after'], atol=1e-3)
    assert all(galaxy['nodes'][i]['p'] >= galaxy['threshold'] for i in j['touched'])


def test_campaign_filters_and_errors(client):
    j = client.post('/galaxy/campaign', json={'scenario': 'add_tech_support', 'contracts': ['Two year']}).json()
    assert j['targeted'] < 1409 and j['affected'] + j['skipped'] == j['targeted']
    assert client.post('/galaxy/campaign', json={}).status_code == 422                                    # neither scenario nor changes
    assert client.post('/galaxy/campaign', json={'scenario': 'nope'}).status_code == 422
    assert client.post('/galaxy/campaign', json={'changes': {'Contract': 'Forever'}}).status_code == 422
    assert client.post('/galaxy/campaign', json={'changes': {'tenure': 1}}).status_code == 422
    assert client.post('/galaxy/campaign', json={'changes': {}}).status_code == 422
    assert client.post('/galaxy/campaign', json={'scenario': 'price_cut_10', 'changes': {'discount_pct': 5}}).status_code == 422


def test_trusted_twin_path_only_skips_the_repeat_validation():
    meta = app.state.bundle['meta'] if hasattr(app.state, 'bundle') else cs.load_bundle()['meta']
    row = {c: v for c, v in zip(meta['input_columns'], [0, 'Yes', 'No', 5, 'Yes', 'No', 'DSL', 'No', 'No', 'No', 'No', 'No', 'No',
                                                     'Month-to-month', 'Yes', 'Mailed check', 50.0])}
    t = tw.CustomerTwin(row, meta)
    after = t.apply({'Contract': 'One year', 'discount_pct': 10})
    assert after.row['MonthlyCharges'] == pytest.approx(45.0) and after.row['tenure'] == 5
    with pytest.raises(tw.TwinError):
        t.apply({'TechSupport': 'No internet service'})                                                    # coherence still enforced
    bad = dict(row, InternetService='No')
    with pytest.raises(tw.TwinError):
        tw.CustomerTwin(bad, meta, _validated=True)                                                        # coherence check is not skipped
