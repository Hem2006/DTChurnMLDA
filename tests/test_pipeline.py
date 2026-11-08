"""Review-queue pipeline. Guardrail limits used here are made-up TEST values; the real ones are null in guardrails.yaml."""
import csv
import sqlite3

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import message as msg
from app import pipeline as pl
from app.main import app
from conftest import CSV, ROOT
from test_message import OK, FakeClient
from test_twin import FIBER, META, NO_NET

TEST_LIMITS = {'max_contacts_per_month': 2, 'max_discount_pct_per_customer_per_quarter': 15}
VULNERABLE = dict(OK, vulnerable=True)


@pytest.fixture(scope='module')
def real_bundle():
    return pl.load_bundle()


@pytest.fixture()
def b_null(real_bundle):
    """The real configuration: guardrail limits null, so anything that would be contacted needs a human."""
    assert real_bundle['guardrails']['max_contacts_per_month'] is None
    return real_bundle


@pytest.fixture()
def b_limits(real_bundle):
    return dict(real_bundle, guardrails={**real_bundle['guardrails'], **TEST_LIMITS})


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / 'q.sqlite')


def record(ref, customer=FIBER, first_name=None, state=OK, error=None):
    return {'customer_ref': ref, 'first_name': first_name, 'customer': None if error else dict(customer), 'account_state': state, 'error': error}


def one(bundle, db, **kw):
    res = pl.ingest([record(**kw)], bundle, path=db)
    return res, pl.list_cases(path=db)[0]


# ---------------------------------------------------------------- source mapping
def test_default_mapping_reads_the_telco_csv():
    recs = pl.read_csv_source(CSV, pl.load_mapping(), META, limit=5)
    assert len(recs) == 5 and all(r['error'] is None for r in recs)
    assert recs[0]['customer_ref'] == '7590-VHVEG' and recs[0]['account_state'] is None and recs[0]['first_name'] is None
    assert set(recs[0]['customer']) == set(META['input_columns'])
    assert isinstance(recs[0]['customer']['tenure'], int) and isinstance(recs[0]['customer']['MonthlyCharges'], float)


def test_mapping_renames_and_reads_account_state():
    row = {'id': 'a-1', 'who': 'Maria', 'months': '5', 'vuln': 'Yes', 'esc': '', 'contacts': '1', 'disc': 'x',
           **{k: str(v) for k, v in FIBER.items() if k != 'tenure'}}
    m = pl.load_mapping(ROOT / 'pipeline.yaml')
    m.update(id_column='id', first_name_column='who', columns={'months': 'tenure'},
             account_state={'vulnerable': 'vuln', 'escalated': 'esc', 'contacts_this_month': 'contacts', 'discount_pct_used_this_quarter': 'disc'})
    r = pl.records_from_frame(pd.DataFrame([row]), m, META)[0]
    assert r['error'] is None and r['first_name'] == 'Maria' and r['customer']['tenure'] == 5
    assert r['account_state'] == {'vulnerable': True, 'escalated': None, 'contacts_this_month': 1, 'discount_pct_used_this_quarter': None}


def test_missing_columns_fail_the_whole_source_but_bad_rows_only_themselves():
    df = pd.read_csv(CSV, dtype=str, keep_default_na=False, nrows=3)
    with pytest.raises(ValueError, match='missing column'):
        pl.records_from_frame(df.drop(columns=['Contract']), pl.load_mapping(), META)
    df.loc[1, 'tenure'] = 'abc'
    df.loc[2, 'customerID'] = 'bad id!'
    recs = pl.records_from_frame(df, pl.load_mapping(), META)
    assert recs[0]['error'] is None and 'tenure' in recs[1]['error'] and 'usable id' in recs[2]['error']


def test_unknown_mapping_key_rejected(tmp_path):
    p = tmp_path / 'm.yaml'
    p.write_text('idcolumn: x\n')
    with pytest.raises(ValueError, match='unknown key'):
        pl.load_mapping(p)


def test_sql_source(tmp_path):
    df = pd.read_csv(CSV, nrows=3)
    f = tmp_path / 'crm.sqlite'
    con = sqlite3.connect(f)
    df.to_sql('customers', con, index=False)
    con.close()
    recs = pl.read_sql_source(f, 'select * from customers', pl.load_mapping(), META)
    assert [r['customer_ref'] for r in recs] == list(df['customerID']) and all(r['error'] is None for r in recs)


# ---------------------------------------------------------------- ingest
def test_real_config_sends_a_flagged_customer_to_a_human_and_drafts_nothing(b_null, db):
    res, c = one(b_null, db, ref='c1', first_name='Maria')
    assert res['counts'] == {'pending_approval': 1}
    assert c['decision'] == 'recommend_pending_approval' and c['message'] is None and c['guardrail_status'] == 'needs_human_approval'
    assert any('max_contacts_per_month is not configured' in a for a in c['approvals_needed'])
    assert msg.read_log(db) == []                                         # no message drafted, none logged


def test_permitted_case_gets_a_validated_message_and_log_entry(b_limits, db):
    res, c = one(b_limits, db, ref='c1', first_name='Maria')
    assert res['counts'] == {'ready': 1} and c['status'] == 'ready' and c['decision'] == 'recommend'
    assert c['message_source'] == 'template' and 'Maria' in c['message']
    offer = msg.offer_from_action(pl.get_case(c['id'], path=db)['recommendation']['action'])
    assert msg.validate_message(c['message'], offer, b_limits['guardrails']) == []
    log = msg.read_log(db)
    assert len(log) == 1 and log[0]['customer_ref'] == 'c1' and log[0]['message'] == c['message'] and log[0]['approved_by'] is None


def test_permitted_case_without_a_name_waits_for_one(b_limits, db):
    _, c = one(b_limits, db, ref='c1')
    assert c['status'] == 'needs_first_name' and c['message'] is None
    out = pl.provide_first_name(c['id'], 'Sam', b_limits['guardrails'], path=db)
    assert out['status'] == 'ready' and 'Sam' in out['message']
    with pytest.raises(ValueError):
        pl.provide_first_name(c['id'], 'Sam1', b_limits['guardrails'], path=db)


def test_vulnerable_account_is_escalated_and_never_drafted(b_limits, db):
    res, c = one(b_limits, db, ref='v1', first_name='Maria', state=VULNERABLE)
    assert res['counts'] == {'escalated': 1} and c['message'] is None and msg.read_log(db) == []
    with pytest.raises(pl.CaseError):
        pl.approve(c['id'], 'boss', b_limits['guardrails'], path=db)


def test_not_flagged_customer_is_no_action(b_limits, db):
    safe = dict(FIBER, Contract='Two year', tenure=70, InternetService='No', MonthlyCharges=20.0, **{k: 'No internet service' for k in
                ('OnlineSecurity', 'OnlineBackup', 'DeviceProtection', 'TechSupport', 'StreamingTV', 'StreamingMovies')})
    res, c = one(b_limits, db, ref='s1', customer=safe, first_name='Maria')
    assert res['counts'] == {'no_action': 1} and c['message'] is None and 'below the threshold' in ' '.join(c['reasons'])


def test_bad_rows_become_invalid_cases_and_do_not_stop_the_batch(b_limits, db):
    incoherent = dict(NO_NET, TechSupport='Yes')                          # InternetService=No with tech support: twin refuses
    recs = [record('bad1', error='tenure is blank'), record('bad2', customer=incoherent), record('ok1', first_name='Maria')]
    res = pl.ingest(recs, b_limits, path=db)
    assert res['counts'] == {'invalid': 2, 'ready': 1}
    by = {c['customer_ref']: c for c in pl.list_cases(path=db)}
    assert by['bad1']['error'] == 'tenure is blank' and by['bad2']['error'] and by['ok1']['status'] == 'ready'


def test_customer_with_an_open_case_is_not_queued_twice(b_limits, db):
    pl.ingest([record('c1', first_name='Maria')], b_limits, path=db)
    res = pl.ingest([record('c1', first_name='Maria'), record('c2', first_name='Sam'), record('c2', first_name='Sam')], b_limits, path=db)
    assert res['skipped_open_case'] == ['c1', 'c2'] and res['counts'] == {'ready': 1}
    c1 = [c for c in pl.list_cases(path=db) if c['customer_ref'] == 'c1'][0]
    pl.reject(c1['id'], 'boss', 'duplicate test', path=db)
    assert pl.ingest([record('c1', first_name='Maria')], b_limits, path=db)['counts'] == {'ready': 1}      # no longer open


def test_llm_text_that_breaks_the_rules_falls_back_to_the_template(b_limits, db):
    bad = FakeClient('Hi Maria, last chance for 99% off everything!')
    pl.ingest([record('c1', first_name='Maria')], b_limits, client=bad, path=db)
    c = pl.list_cases(path=db)[0]
    assert len(bad.calls) == 1 and c['status'] == 'ready' and c['message_source'] == 'template_fallback' and '99' not in c['message']


def test_every_queued_case_carries_the_twin_comparison_automatically(b_null, db):
    _, c = one(b_null, db, ref='c1')
    sc = pl.get_case(c['id'], path=db)['scenarios']
    assert sc['interpretation'] == 'model sensitivity, not a causal estimate' and sc['ranked_by'] == 'risk_drop'
    names = [r['scenario'] for r in sc['rows']]
    assert {'price_cut_10', 'contract_one_year', 'add_tech_support', 'combined_package'} <= set(names) and 'do_nothing' not in names
    assert sum(r['chosen'] for r in sc['rows']) == 1 and [r['scenario'] for r in sc['rows'] if r['chosen']] == [c['action_name']]
    for r in sc['rows']:
        assert r['risk_after'] == pytest.approx(r['risk_now'] - r['risk_drop']) and r['expected_net_value'] is None
    assert pl.scenario_comparison({'recommendation': {'risk': {'churn_probability': .1}}})['rows'] == []      # no-action cases: nothing to show


# ---------------------------------------------------------------- approval
def test_approval_flow(b_null, db):
    _, c = one(b_null, db, ref='c1')
    g = b_null['guardrails']
    for who in ('', '   ', None):
        with pytest.raises(ValueError):
            pl.approve(c['id'], who, g, path=db)
    out = pl.approve(c['id'], 'Dana', g, path=db)                         # approved, but there is still no name
    assert out['status'] == 'needs_first_name' and out['approved_by'] == 'Dana' and out['message'] is None
    out = pl.provide_first_name(c['id'], 'Maria', g, path=db)
    assert out['status'] == 'ready' and 'Maria' in out['message']
    assert msg.read_log(db)[0]['approved_by'] == 'Dana'
    with pytest.raises(pl.CaseError, match='only pending_approval'):
        pl.approve(c['id'], 'Dana', g, path=db)                           # cannot approve twice
    events = [e['event'] for e in pl.get_case(c['id'], path=db)['events']]
    assert events == ['created', 'approved', 'first_name_set', 'message_drafted']


def test_approve_with_first_name_in_one_step(b_null, db):
    _, c = one(b_null, db, ref='c1')
    assert pl.approve(c['id'], 'Dana', b_null['guardrails'], first_name='Maria', path=db)['status'] == 'ready'


def test_reject_and_unknown_case(b_null, db):
    _, c = one(b_null, db, ref='c1')
    with pytest.raises(ValueError):
        pl.reject(c['id'], 'Dana', ' ', path=db)
    out = pl.reject(c['id'], 'Dana', 'customer called in', path=db)
    assert out['status'] == 'rejected' and out['note'] == 'customer called in'
    with pytest.raises(pl.CaseError):
        pl.reject(c['id'], 'Dana', 'again', path=db)
    with pytest.raises(pl.CaseNotFound):
        pl.get_case(999, path=db)


def test_edit_message_is_held_to_the_same_rules(b_limits, db):
    _, c = one(b_limits, db, ref='c1', first_name='Maria')
    g = b_limits['guardrails']
    for bad in ('Hi Maria, you get 99% off!', 'Hi Maria, act now to keep your churn low.', 'Thanks for staying with us.'):
        with pytest.raises(ValueError, match='rejected'):
            pl.edit_message(c['id'], bad, g, edited_by='Dana', path=db)
    offer = pl.get_case(c['id'], path=db)['recommendation']['action']
    good = 'Hi Maria, thanks for being with us. We would like to offer you %s. Reply if you are interested.' % msg.offer_from_action(offer)['description']
    out = pl.edit_message(c['id'], good, g, edited_by='Dana', path=db)
    assert out['message'] == good and out['message_source'] == 'human_edited'


# ---------------------------------------------------------------- outbox
def test_export_writes_only_ready_messages_and_marks_them(b_null, b_limits, db, tmp_path):
    pl.ingest([record('pend', first_name='Maria')], b_null, path=db)                          # pending_approval: must not be exported
    pl.ingest([record('r1', first_name='Maria'), record('r2', first_name='Sam')], b_limits, path=db)
    out = tmp_path / 'sub' / 'outbox.csv'
    rows = pl.export_outbox(out, path=db)
    assert [r['customer_ref'] for r in rows] == ['r1', 'r2']
    written = list(csv.DictReader(open(out, encoding='utf-8')))
    assert list(written[0]) == pl.OUTBOX_COLUMNS and [w['first_name'] for w in written] == ['Maria', 'Sam']
    assert {'email', 'phone', 'address'}.isdisjoint(written[0])                               # no contact data in the outbox
    assert pl.summary(path=db) == {'exported': 2, 'pending_approval': 1}
    assert pl.export_outbox(tmp_path / 'again.csv', path=db) == [] and not (tmp_path / 'again.csv').exists()


def test_failed_file_write_leaves_cases_ready(b_limits, db, tmp_path):
    pl.ingest([record('r1', first_name='Maria')], b_limits, path=db)
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    with pytest.raises(OSError):
        pl.export_outbox(blocker / 'outbox.csv', path=db)                                     # parent is a file
    assert pl.summary(path=db) == {'ready': 1}


# ---------------------------------------------------------------- command line
def test_cli_run_queue_approve_export(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / 'cli.sqlite')
    src = tmp_path / 'cust.csv'
    df = pd.read_csv(CSV, nrows=200)
    df[df['Contract'] == 'Month-to-month'].head(3).to_csv(src, index=False)
    assert pl.main(['--db', db, 'run', '--source', str(src)]) == 0
    assert pl.main(['--db', db, 'summary']) == 0
    out = capsys.readouterr().out
    assert '"pending_approval"' in out or '"no_action"' in out
    pending = pl.list_cases('pending_approval', path=db)
    if pending:
        cid = pending[0]['id']
        assert pl.main(['--db', db, 'approve', str(cid), '--by', 'Dana', '--first-name', 'Maria']) == 0
        assert pl.main(['--db', db, 'export', '--out', str(tmp_path / 'o.csv')]) == 0
        assert (tmp_path / 'o.csv').exists()
    assert pl.main(['--db', db, 'approve', '99999', '--by', 'Dana']) == 1                    # unknown case: error exit code, not a traceback
    assert pl.main(['--db', db, 'queue']) == 0
    monkeypatch.undo()


# ---------------------------------------------------------------- API
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CHURN_DB', str(tmp_path / 'api.sqlite'))
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('CHURN_API_KEY', raising=False)
    with TestClient(app) as c:
        app.state.llm_client = None                                                           # deterministic template
        yield c
        app.state.llm_client = 'auto'


def test_api_flow_ingest_approve_export(client):
    body = {'records': [{'customer_ref': 'api-1', 'first_name': 'Maria', 'customer': FIBER, 'account_state': OK}]}
    r = client.post('/cases/ingest', json=body)
    assert r.status_code == 200 and r.json()['counts'] == {'pending_approval': 1}               # real config: limits are null
    cid = client.get('/cases', params={'status': 'pending_approval'}).json()['cases'][0]['id']
    assert client.get('/cases/%d' % cid).json()['recommendation']['action']['guardrails']['status'] == 'needs_human_approval'
    assert client.post('/outbox/export').json()['count'] == 0                                   # nothing is ready yet
    r = client.post('/cases/%d/approve' % cid, json={'approved_by': 'Dana'})
    assert r.status_code == 200 and r.json()['status'] == 'ready' and 'Maria' in r.json()['message']
    assert client.post('/cases/%d/approve' % cid, json={'approved_by': 'Dana'}).status_code == 409
    ex = client.post('/outbox/export').json()
    assert ex['count'] == 1 and ex['messages'][0]['customer_ref'] == 'api-1' and client.get('/cases').json()['summary'] == {'exported': 1}


def test_api_errors(client):
    assert client.get('/cases/12345').status_code == 404
    assert client.post('/cases/12345/approve', json={'approved_by': 'Dana'}).status_code == 404
    assert client.post('/cases/ingest', json={'records': []}).status_code == 422
    assert client.post('/cases/ingest', json={'records': [{'customer_ref': 'bad ref!', 'customer': FIBER}]}).status_code == 422
    assert client.post('/cases/ingest', json={'records': [{'customer_ref': 'x', 'customer': dict(FIBER, Contract='Three year')}]}).status_code == 422


def test_api_reject_name_and_edit(client):
    client.post('/cases/ingest', json={'records': [{'customer_ref': 'api-2', 'customer': FIBER, 'account_state': OK}]})
    cid = client.get('/cases').json()['cases'][0]['id']
    assert client.post('/cases/%d/first-name' % cid, json={'first_name': 'Sam'}).json()['status'] == 'pending_approval'
    assert client.post('/cases/%d/message' % cid, json={'edited_by': 'D', 'message': 'x'}).status_code == 409        # not ready yet
    r = client.post('/cases/%d/reject' % cid, json={'rejected_by': 'Dana', 'reason': 'no'})
    assert r.status_code == 200 and r.json()['status'] == 'rejected'


def test_api_key_protects_the_queue_when_set(client, monkeypatch):
    monkeypatch.setenv('CHURN_API_KEY', 'test-key-123')
    assert client.get('/cases').status_code == 401
    assert client.get('/cases', headers={'X-API-Key': 'wrong'}).status_code == 401
    assert client.post('/outbox/export').status_code == 401
    assert client.get('/cases', headers={'X-API-Key': 'test-key-123'}).status_code == 200
    assert client.get('/health').status_code == 200                                            # scoring endpoints stay as they were
