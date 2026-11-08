"""Endpoints added for the React frontend (/meta, /score, /explain) and serving the built app from web/dist."""
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import ROOT  # noqa: F401
from test_twin import FIBER, NO_NET


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        yield c


def test_meta_has_form_options_and_saved_numbers_only(client):
    j = client.get('/meta').json()
    m = json.loads((ROOT / 'churn_model_meta.json').read_text())
    assert j['input_columns'] == m['input_columns'] and j['categorical_levels'] == m['categorical_levels']
    assert j['threshold_is_placeholder'] is True and j['threshold_note'].startswith('PLACEHOLDER')
    assert j['evaluation']['test_metrics'] == m['test_metrics'] and j['evaluation']['subgroups'] == m['subgroups']
    assert j['labels']['twin'] == 'model sensitivity, not a causal estimate' and 'SIMULATED' in j['labels']['uplift']
    assert j['business_numbers_set'] == {'offer_cost': False, 'customer_value': False, 'success_rate': False}


def test_meta_gap_flag_matches_fairness_module():
    pytest.importorskip('fairlearn')
    from app import fairness
    assert main.GAP_FLAG == fairness.GAP_FLAG


def _row(c, **kw):
    """A customer as a CSV reader delivers it: every value a string."""
    return {k: str(v) for k, v in {**c, **kw}.items()}


def test_score_scores_good_rows_and_explains_bad_ones(client):
    rows = [_row(FIBER, customerID='a-1'), _row(NO_NET, customerID='a-2', first_name='Sam'),
            _row(FIBER, customerID='bad-level', Contract='Lifetime'),
            _row(FIBER, customerID='incoherent', InternetService='No'),
            _row(FIBER, customerID='bad-tenure', tenure='4.5'),
            _row(FIBER, customerID='bad-number', MonthlyCharges='abc')]
    j = client.post('/score', json={'rows': rows}).json()
    assert [r['ref'] for r in j['results']] == ['a-1', 'a-2']
    assert {r['ref'] for r in j['rejected']} == {'bad-level', 'incoherent', 'bad-tenure', 'bad-number'}
    assert all(r['reason'] for r in j['rejected'])
    p = client.post('/predict', json={'customers': [FIBER]}).json()['results'][0]
    assert j['results'][0]['churn_probability'] == pytest.approx(p['churn_probability'])
    assert j['results'][0]['customer']['tenure'] == FIBER['tenure'] and j['threshold_is_placeholder'] is True


def test_score_without_ref_column_labels_rows_by_position(client):
    j = client.post('/score', json={'rows': [_row(FIBER), _row(FIBER, Contract='x')]}).json()
    assert j['results'][0]['ref'] == 'row 1' and j['rejected'][0]['ref'] == 'row 2'


def test_score_missing_column_is_a_422(client):
    r = client.post('/score', json={'rows': [{k: v for k, v in _row(FIBER).items() if k != 'tenure'}]})
    assert r.status_code == 422 and 'Missing required columns' in r.json()['detail']


def test_explain_returns_all_inputs_sorted(client):
    j = client.post('/explain', json={'customer': FIBER}).json()
    pushes = [c['log_odds_push'] for c in j['contributions']]
    assert len(pushes) == 17 and pushes == sorted(pushes, reverse=True)
    reasons = client.post('/predict', json={'customers': [FIBER]}).json()['results'][0]['reasons']
    assert [c['feature'] for c in j['contributions'][:3]] == [r['feature'] for r in reasons]


def test_web_app_is_served_and_api_routes_still_win(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    (tmp_path / 'index.html').write_text('<!doctype html><title>Evergreen</title>')
    probe = FastAPI()
    probe.get('/health')(lambda: {'status': 'ok'})
    probe.mount('/', StaticFiles(directory=tmp_path, html=True), name='web')     # same order as app.main: API first, web last
    with TestClient(probe) as c:
        assert 'Evergreen' in c.get('/').text and c.get('/health').json() == {'status': 'ok'}


def test_main_mounts_web_last_when_built():
    if not (main.WEB_DIST / 'index.html').exists():
        pytest.skip('web/dist not built (cd web && npm run build)')
    assert app.routes[-1].name == 'web'
    with TestClient(app) as c:
        assert '<div id="root">' in c.get('/').text and c.get('/health').json()['status'] == 'ok'
