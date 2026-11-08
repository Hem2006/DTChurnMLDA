"""Retention agent (app/agent.py): the full flow ingest -> score -> prioritise -> twin -> decide -> queue -> approval -> hand-off.
Uses the real bundle and the real configuration (guardrail limits null, account state unknown), so every offer needs approval."""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import agent as ag
from app import pipeline as pl
from app.main import app

TECH_WORDS = ('auc', 'shap', 'log-odds', 'log_odds', 'xgboost', 'calibrat', 'brier', 'f1')


@pytest.fixture(scope='module')
def real_bundle():
    return pl.load_bundle()


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / 'agent.sqlite')


@pytest.fixture()
def finished(real_bundle, db):
    recs = ag.demo_records(real_bundle['meta'], sample=40, seed=1)
    return ag.run(recs, real_bundle, path=db, source='test', max_twin=3)


# ---------------------------------------------------------------- plain language
@pytest.mark.parametrize('feature,value,expected', [
    ('Contract', 'Month-to-month', 'Month-to-month contract: can leave at any time'),
    ('tenure', np.float64(1.0), 'Only 1 month with us'),
    ('tenure', 8, 'Only 8 months with us'),
    ('MonthlyCharges', np.float64(100.15), 'High monthly bill ($100.15)'),
    ('InternetService', 'Fiber optic', 'Fiber optic internet'),
    ('PaymentMethod', 'Electronic check', 'Pays by electronic check'),
    ('TechSupport', 'No', 'No tech support'),
    ('SeniorCitizen', 1, 'Senior customer'),
    ('Dependents', 'No', 'No dependents on the account'),
])
def test_plain_reasons(feature, value, expected):
    assert ag.plain_reason(feature, value) == expected


def test_option_labels_cover_every_scenario(real_bundle):
    for name in real_bundle['scenarios']:
        assert ag.option_label(name, real_bundle) == ag.OPTION_LABELS[name]
    assert ag.option_label('unknown_x', real_bundle) == 'unknown x' and ag.option_label(None) is None


# ---------------------------------------------------------------- sources
def test_demo_records_are_holdout_customers_with_names(real_bundle):
    from app.galaxy import load_holdout
    recs = ag.demo_records(real_bundle['meta'], sample=25, seed=3)
    assert len(recs) == 25 and all(r['error'] is None and r['first_name'] and r['account_state'] is None for r in recs)
    assert recs == ag.demo_records(real_bundle['meta'], sample=25, seed=3)              # reproducible
    import pandas as pd
    raw = pd.read_csv(ag.CSV, dtype=str)
    X, _ = load_holdout()
    holdout_ids = set(raw.loc[X.index, 'customerID'])
    assert {r['customer_ref'] for r in recs} <= holdout_ids


def test_rows_records_keep_bad_rows_with_a_reason(real_bundle):
    import pandas as pd
    rows = pd.read_csv(ag.CSV, dtype=str, keep_default_na=False, nrows=3).to_dict('records')
    rows[1]['tenure'] = 'abc'
    recs = ag.rows_records(rows, real_bundle['meta'])
    assert recs[0]['error'] is None and 'tenure' in recs[1]['error']
    with pytest.raises(ValueError, match='no rows'):
        ag.rows_records([], real_bundle['meta'])


# ---------------------------------------------------------------- the run
def test_full_run_goes_through_every_stage(finished, db):
    r = finished
    assert r['status'] == 'done', r['error']
    st = {s['key']: s for s in r['steps']}
    assert [s['key'] for s in r['steps']] == ag.STAGE_KEYS
    for k in ('ingest', 'score', 'prioritise', 'twin', 'decide', 'queue'):
        assert st[k]['status'] == 'done' and st[k]['headline'], k
    assert st['ingest']['detail'] == {'read': 40, 'usable': 40, 'rejected': 0, 'rejected_examples': []}
    above = st['score']['detail']['above_alert_line']
    assert st['prioritise']['detail']['prioritised'] == min(3, above)
    assert st['prioritise']['detail']['deferred'] == max(0, above - 3)
    # real config: account state unknown and guardrail limits null -> every offer waits for a person
    assert r['queue'] == {'pending_approval': 3}
    assert st['approve']['status'] == 'waiting' and st['approve']['detail']['waiting'] == 3
    assert st['handoff']['status'] == 'waiting'
    assert st['twin']['detail']['label'] == 'model sensitivity, not a causal estimate'


def test_prioritised_are_the_most_at_risk_and_linked_to_cases(finished, db, real_bundle):
    allc = ag.run_customers(finished['id'], path=db, bundle=real_bundle)
    assert len(allc) == 40
    probs = [c['probability'] for c in allc]
    assert probs == sorted(probs, reverse=True)
    pri = ag.run_customers(finished['id'], scope='prioritised', path=db)
    assert [c['rank'] for c in pri] == [1, 2, 3]
    assert [c['ref'] for c in pri] == [c['ref'] for c in allc[:3]]
    for c in pri:
        assert c['above_alert_line'] and c['case_id'] and c['case_status'] == 'pending_approval'
        assert c['decision'] == 'recommend_pending_approval' and c['action_label'] and c['reasons']
        assert c['risk_after'] < c['probability']
    for c in allc[3:]:
        assert c['case_id'] is None and c['decision'] is None


def test_customer_detail_shows_twin_options_and_case(finished, db, real_bundle):
    pri = ag.run_customers(finished['id'], scope='prioritised', path=db)
    d = ag.customer_detail(finished['id'], pri[0]['ref'], path=db, bundle=real_bundle)
    assert set(d['customer']) == set(real_bundle['meta']['input_columns'])
    assert d['twin_label'] == 'model sensitivity, not a causal estimate'
    tried = [o for o in d['options'] if 'not_applicable' not in o]
    assert tried and sum(o['chosen'] for o in tried) == 1
    assert all(o['label'] and o['allowed'] == 'needs_human_approval' for o in tried)
    assert d['case']['status'] == 'pending_approval' and d['case']['events']
    with pytest.raises(ag.RunNotFound):
        ag.customer_detail(finished['id'], 'nobody', path=db)


def test_plain_language_has_no_model_jargon(finished, db, real_bundle):
    text = ' '.join(s['headline'] or '' for s in finished['steps'])
    for c in ag.run_customers(finished['id'], path=db, bundle=real_bundle):
        text += ' '.join(x['text'] for x in c['reasons']) + (c['action_label'] or '') + (c['decision_text'] or '')
    low = text.lower()
    assert not [w for w in TECH_WORDS if w in low], text


def test_approval_and_handoff_are_live_from_the_queue(finished, db, real_bundle):
    pri = ag.run_customers(finished['id'], scope='prioritised', path=db)
    pl.approve(pri[0]['case_id'], 'Test Manager', real_bundle['guardrails'], path=db)        # demo names exist -> message drafted
    pl.reject(pri[1]['case_id'], 'Test Manager', 'not now', path=db)
    st = {s['key']: s for s in ag.get_run(finished['id'], path=db)['steps']}
    assert st['approve']['detail'] == {'waiting': 1, 'approved': 1, 'rejected': 1, 'needs_first_name': 0}
    assert st['handoff']['detail'] == {'ready': 1, 'exported': 0}
    pl.export_outbox(path=db)
    st = {s['key']: s for s in ag.get_run(finished['id'], path=db)['steps']}
    assert st['handoff']['detail'] == {'ready': 0, 'exported': 1} and st['handoff']['status'] == 'done'


def test_rerun_skips_customers_with_an_open_case(finished, db, real_bundle):
    recs = ag.demo_records(real_bundle['meta'], sample=40, seed=1)
    again = ag.run(recs, real_bundle, path=db, source='test', max_twin=3)
    q = {s['key']: s for s in again['steps']}['queue']['detail']
    assert len(q['skipped_open_case']) == 3 and q['counts'] == {}


def test_invalid_rows_are_kept_with_a_reason(real_bundle, db):
    recs = ag.demo_records(real_bundle['meta'], sample=12, seed=2)
    k = next(i for i, r in enumerate(recs) if r['customer']['InternetService'] != 'No')
    recs[0], recs[k] = recs[k], recs[0]
    recs[0] = dict(recs[0], customer=dict(recs[0]['customer'], InternetService='No'))       # add-ons still Yes/No -> incoherent
    recs[1] = dict(recs[1], customer=None, error='tenure is blank')
    r = ag.run(recs, real_bundle, path=db, max_twin=1)
    assert r['status'] == 'done'
    assert {s['key']: s for s in r['steps']}['ingest']['detail']['rejected'] == 2
    bad = ag.run_customers(r['id'], scope='rejected', path=db)
    assert {b['ref'] for b in bad} == {recs[0]['customer_ref'], recs[1]['customer_ref']} and all(b['error'] for b in bad)


def test_a_failing_stage_stops_the_run_and_says_so(real_bundle, db):
    def broken(*a, **k):
        raise RuntimeError('twin exploded')
    recs = ag.demo_records(real_bundle['meta'], sample=30, seed=4)
    r = ag.run(recs, real_bundle, path=db, max_twin=2, recommender=broken)
    st = {s['key']: s for s in r['steps']}
    assert r['status'] == 'failed' and 'twin exploded' in r['error']
    assert st['twin']['status'] == 'failed' and st['score']['status'] == 'done'
    assert all(st[k]['status'] == 'skipped' for k in ('decide', 'queue', 'approve', 'handoff'))


def test_only_one_run_at_a_time_and_interrupted_runs_are_closed(real_bundle, db):
    ag._ACTIVE['run_id'] = 999
    try:
        with pytest.raises(ag.AgentBusy):
            ag.start([], real_bundle, path=db)
    finally:
        ag._ACTIVE['run_id'] = None
    rid = ag.create_run(source='x', path=db)
    assert ag.mark_interrupted(path=db) == 1
    r = ag.get_run(rid, path=db)
    assert r['status'] == 'failed' and 'interrupted' in r['error']
    with pytest.raises(ag.RunNotFound):
        ag.get_run(12345, path=db)


# ---------------------------------------------------------------- API
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CHURN_DB', str(tmp_path / 'api.sqlite'))
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('CHURN_API_KEY', raising=False)
    with TestClient(app) as c:
        app.state.llm_client = None
        yield c


def _wait(client, rid, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        r = client.get('/agent/runs/%d' % rid).json()
        if r['status'] != 'running':
            return r
        time.sleep(0.3)
    raise AssertionError('agent run did not finish')


def test_api_runs_the_agent_end_to_end(client):
    stages = client.get('/agent/stages').json()
    assert [s['key'] for s in stages['stages']] == ag.STAGE_KEYS
    r = client.post('/agent/run', json={'source': 'demo', 'sample': 20, 'max_twin': 2, 'requested_by': 'Prof'}).json()
    assert r['status'] in ('running', 'done') and r['requested_by'] == 'Prof'
    r = _wait(client, r['id'])
    assert r['status'] == 'done' and r['id'] == client.get('/agent/runs').json()['runs'][0]['id']
    pri = client.get('/agent/runs/%d/customers?scope=prioritised' % r['id']).json()['customers']
    assert 1 <= len(pri) <= 2
    d = client.get('/agent/runs/%d/customers/%s' % (r['id'], pri[0]['ref'])).json()
    assert d['customer'] and d['options'] and d['case']['status'] == 'pending_approval'
    # the human step goes through the existing review-queue endpoints
    ok = client.post('/cases/%d/approve' % d['case_id'], json={'approved_by': 'Prof'}).json()
    assert ok['status'] == 'ready' and ok['message']
    assert client.get('/agent/runs/%d/customers?scope=bogus' % r['id']).status_code == 422
    assert client.get('/agent/runs/9999').status_code == 404


def test_api_rows_source_and_validation(client):
    import pandas as pd
    rows = pd.read_csv(ag.CSV, dtype=str, keep_default_na=False, nrows=15).to_dict('records')
    r = client.post('/agent/run', json={'source': 'rows', 'rows': rows, 'file_name': 'crm.csv', 'max_twin': 1}).json()
    r = _wait(client, r['id'])
    assert r['status'] == 'done' and 'crm.csv' in r['source']
    assert client.post('/agent/run', json={'source': 'rows'}).status_code == 422
    bad = [dict(rows[0])]
    del bad[0]['Contract']
    assert client.post('/agent/run', json={'source': 'rows', 'rows': bad}).status_code == 422
