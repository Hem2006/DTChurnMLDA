"""Sign-in, the four roles and the hand-off chain with separation of duties. Business numbers stay null (real config):
every offer therefore needs a person, which is exactly the path under test."""
import pytest
from fastapi.testclient import TestClient

from app import auth
from app import pipeline as pl
from app import workflow as wf
from app.main import app
from test_message import OK
from test_twin import FIBER

PIN = 'evergreen'
U = {r: {'id': r[:3], 'name': r.title() + ' Test', 'role': r, 'title': r} for r in auth.ROLES}


# ------------------------------------------------------------------ tokens and sign-in
def test_token_roundtrip_and_tamper():
    cfg = auth.load_users()
    t = auth.make_token(cfg['users']['dana'])
    assert auth.verify_token(t)['role'] == 'analyst'
    body, sig = t.split('.')
    forged = auth._b64(auth._unb64(body).replace(b'analyst', b'admin000')) + '.' + sig
    assert auth.verify_token(forged) is None
    assert auth.verify_token(t + 'x') is None and auth.verify_token('') is None and auth.verify_token(None) is None
    assert auth.verify_token(auth.make_token(cfg['users']['dana'], now=1000, hours=1), now=1000 + 3601) is None   # expired


def test_login_wrong_pin_and_lockout():
    cfg = auth.load_users()
    auth._fails.clear()
    for _ in range(auth.MAX_FAILS):
        with pytest.raises(auth.AuthError) as e:
            auth.login(cfg, 'dana', 'nope', now=500)
        assert e.value.status == 401
    with pytest.raises(auth.AuthError) as e:
        auth.login(cfg, 'dana', cfg['pin'], now=501)                       # even the right PIN is refused while locked
    assert e.value.status == 429
    assert auth.login(cfg, 'dana', cfg['pin'], now=500 + auth.LOCK_SECONDS + 1)['user']['role'] == 'analyst'
    auth._fails.clear()


def test_four_roles_in_users_file():
    cfg = auth.load_users()
    assert sorted(u['role'] for u in cfg['users'].values()) == sorted(auth.ROLES)


# ------------------------------------------------------------------ the chain, function level
@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / 'wf.sqlite')


@pytest.fixture(scope='module')
def bundle():
    return pl.load_bundle()


def _queue(bundle, db, ref='wf-1', first='Maria', state=OK):
    pl.ingest([{'customer_ref': ref, 'first_name': first, 'customer': FIBER, 'account_state': state, 'error': None}], bundle, client=None, path=db)
    return pl.list_cases(path=db)[0]['id']


def test_full_chain_and_stages(bundle, db):
    rules = bundle['guardrails']
    cid = _queue(bundle, db)
    assert wf.stage_of(pl.get_case(cid, path=db)) == 'analyst'
    wf.submit(cid, U['analyst'], 'Month-to-month on fibre, price-sensitive', rules, path=db)
    assert wf.stage_of(pl.get_case(cid, path=db)) == 'manager'
    c = wf.approve(cid, U['manager'], rules, path=db)
    assert c['status'] == 'ready' and wf.stage_of(c) == 'compliance' and c['approved_uid'] == 'man'
    assert wf.export(U['compliance'], path=db) == []                          # not cleared yet -> nothing leaves
    c = wf.clear(cid, U['compliance'], 'wording fine', rules, path=db)
    assert wf.stage_of(c) == 'crm_ready'
    rows = wf.export(U['compliance'], path=db)
    assert len(rows) == 1 and rows[0]['customer_ref'] == 'wf-1'
    done = pl.get_case(cid, path=db)
    assert done['status'] == 'exported'
    who = [(e['event'], e['actor']) for e in done['events']]
    assert ('submitted_to_manager', 'Analyst Test') in who and ('cleared_by_compliance', 'Compliance Test') in who
    assert ('exported', 'Compliance Test') in who
    assert wf.duty_violations(path=db) == 0


def test_manager_cannot_approve_before_submit(bundle, db):
    cid = _queue(bundle, db)
    with pytest.raises(pl.CaseError):
        wf.approve(cid, U['manager'], bundle['guardrails'], path=db)


def test_separation_of_duties_same_person(bundle, db):
    rules = bundle['guardrails']
    cid = _queue(bundle, db)
    wf.submit(cid, U['analyst'], 'note', rules, path=db)
    same = dict(U['manager'], id=U['analyst']['id'])                          # the submitter tries to approve their own case
    with pytest.raises(wf.SeparationError):
        wf.approve(cid, same, rules, path=db)
    with pytest.raises(wf.SeparationError):
        wf.reject(cid, same, 'no', path=db)
    assert pl.get_case(cid, path=db)['status'] == 'pending_approval'
    wf.approve(cid, U['manager'], rules, path=db)
    for clash in ('ana', 'man'):                                              # neither earlier actor may clear
        with pytest.raises(wf.SeparationError):
            wf.clear(cid, dict(U['compliance'], id=clash), None, rules, path=db)
    assert pl.get_case(cid, path=db)['status'] == 'ready' and not pl.get_case(cid, path=db)['cleared_uid']


def test_send_back_and_resubmit(bundle, db):
    rules = bundle['guardrails']
    cid = _queue(bundle, db)
    wf.submit(cid, U['analyst'], 'first try', rules, path=db)
    wf.send_back(cid, U['manager'], 'please check the twin warning', path=db)
    c = pl.get_case(cid, path=db)
    assert wf.stage_of(c) == 'analyst' and c['note'] == 'please check the twin warning'
    wf.submit(cid, U['analyst'], 'second try', rules, path=db)
    assert wf.stage_of(pl.get_case(cid, path=db)) == 'manager'
    with pytest.raises(pl.CaseError):
        wf.submit(cid, U['analyst'], 'again', rules, path=db)                 # already with the manager


def test_note_required_and_message_edit_validated(bundle, db):
    rules = bundle['guardrails']
    cid = _queue(bundle, db)
    with pytest.raises(ValueError):
        wf.submit(cid, U['analyst'], '   ', rules, path=db)
    wf.submit(cid, U['analyst'], 'ok', rules, path=db)
    wf.approve(cid, U['manager'], rules, path=db)
    with pytest.raises(ValueError):                                           # pressure language is refused, same validator as ever
        wf.clear(cid, U['compliance'], None, rules, message='Maria, act now: last chance for this offer!', path=db)
    assert not pl.get_case(cid, path=db)['cleared_uid']


def test_compliance_reject_and_care_handoff(bundle, db):
    rules = bundle['guardrails']
    cid = _queue(bundle, db)
    wf.submit(cid, U['analyst'], 'ok', rules, path=db)
    wf.approve(cid, U['manager'], rules, path=db)
    wf.reject(cid, U['compliance'], 'tone is off', path=db)
    assert pl.get_case(cid, path=db)['status'] == 'rejected'
    pl.ingest([{'customer_ref': 'vuln-1', 'first_name': 'Sam', 'customer': FIBER, 'account_state': dict(OK, vulnerable=True), 'error': None}],
              bundle, client=None, path=db)
    esc = [c for c in pl.list_cases(path=db) if c['customer_ref'] == 'vuln-1'][0]
    assert esc['status'] == 'escalated' and wf.stage_of(esc) == 'care'
    with pytest.raises(pl.CaseError):
        wf.care_handoff(cid, U['compliance'], 'x', path=db)                   # only escalated accounts
    with pytest.raises(ValueError):
        wf.care_handoff(esc['id'], U['compliance'], ' ', path=db)
    out = wf.care_handoff(esc['id'], U['compliance'], 'Phone the customer via the care team', path=db)
    assert out['status'] == 'care_handoff' and out['care_uid'] == 'com'


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    p = str(tmp_path / 'old.sqlite')
    con = sqlite3.connect(p)
    con.execute('CREATE TABLE cases (id INTEGER PRIMARY KEY, run_id TEXT, ts TEXT, updated_ts TEXT, customer_ref TEXT, first_name TEXT, status TEXT, '
                'decision TEXT, action_name TEXT, probability REAL, threshold REAL, threshold_is_placeholder INTEGER, guardrail_status TEXT, '
                'reasons_json TEXT, approvals_json TEXT, recommendation_json TEXT, message TEXT, message_source TEXT, message_log_id INTEGER, '
                'approved_by TEXT, error TEXT, note TEXT, exported_ts TEXT)')
    con.commit()
    con.close()
    pl._connect(p).close()
    cols = {r[1] for r in sqlite3.connect(p).execute('PRAGMA table_info(cases)')}
    assert set(pl.WORKFLOW_COLUMNS) <= cols


# ------------------------------------------------------------------ API: roles enforced from the token
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CHURN_DB', str(tmp_path / 'api.sqlite'))
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('CHURN_API_KEY', raising=False)
    monkeypatch.delenv('CHURN_REQUIRE_LOGIN', raising=False)
    auth._fails.clear()
    with TestClient(app) as c:
        app.state.llm_client = None
        yield c
        app.state.llm_client = 'auto'


def _as(client, uid):
    r = client.post('/auth/login', json={'user_id': uid, 'pin': PIN})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['token']}


def _seed(client):
    body = {'records': [{'customer_ref': 'api-1', 'first_name': 'Maria', 'customer': FIBER, 'account_state': OK}]}
    assert client.post('/cases/ingest', json=body).status_code == 200
    return client.get('/cases').json()['cases'][0]['id']


def test_api_login(client):
    assert client.post('/auth/login', json={'user_id': 'dana', 'pin': 'wrong'}).status_code == 401
    assert client.post('/auth/login', json={'user_id': 'nobody', 'pin': PIN}).status_code == 401
    acc = client.get('/auth/accounts').json()['accounts']
    assert {a['role'] for a in acc} == set(auth.ROLES) and all('pin' not in a for a in acc)
    assert client.get('/auth/me').status_code == 401
    assert client.get('/auth/me', headers=_as(client, 'priya')).json()['role'] == 'compliance'
    assert client.get('/auth/me', headers={'Authorization': 'Bearer garbage'}).status_code == 401


def test_api_chain_and_role_gates(client):
    cid = _seed(client)
    dana, marcus, priya, sam = (_as(client, u) for u in ('dana', 'marcus', 'priya', 'sam'))
    p = '/workflow/cases/%d/' % cid
    # nobody but the right role may do each step
    assert client.post(p + 'submit', json={'note': 'x'}, headers=marcus).status_code == 403
    assert client.post(p + 'approve', json={}, headers=dana).status_code == 403
    assert client.post(p + 'clear', json={}, headers=marcus).status_code == 403
    assert client.post('/workflow/export', headers=dana).status_code == 403
    assert client.post(p + 'submit', json={'note': 'x'}).status_code == 401               # no token at all
    # the manager has nothing to approve until the analyst sends it
    assert client.post(p + 'approve', json={}, headers=marcus).status_code == 409
    assert client.post(p + 'submit', json={'note': 'price-sensitive, fibre'}, headers=dana).status_code == 200
    assert client.post(p + 'approve', json={}, headers=marcus).json()['status'] == 'ready'
    assert client.post('/workflow/export', headers=priya).json()['count'] == 0               # approved but not yet cleared
    assert client.post(p + 'clear', json={'note': 'ok'}, headers=priya).status_code == 200
    ex = client.post('/workflow/export', headers=priya).json()
    assert ex['count'] == 1 and ex['messages'][0]['approved_by'] == 'Marcus Lee'
    board = client.get('/workflow/board', headers=dana).json()
    assert board['counts']['closed'] == 1
    # admin: sees the system, cannot act on a case, cannot read the case board
    assert client.get('/workflow/board', headers=sam).status_code == 403
    assert client.post(p + 'submit', json={'note': 'x'}, headers=sam).status_code == 403
    o = client.get('/admin/overview', headers=sam).json()
    assert o['duty_violations'] == 0 and {n['key'] for n in o['numbers'] if not n['set']} >= {'offer_cost', 'customer_value', 'success_rate'}
    ev = client.get('/admin/audit', headers=sam).json()['events']
    assert {'login', 'submitted_to_manager', 'cleared_by_compliance'} <= {e['event'] for e in ev}
    assert client.get('/admin/overview', headers=dana).status_code == 403
    assert client.get('/admin/audit', headers=priya).status_code == 403


def test_agent_run_is_analyst_only(client):
    body = {'source': 'demo', 'sample': 5, 'seed': 1, 'max_twin': 1, 'requested_by': 'x'}
    assert client.post('/agent/run', json=body, headers=_as(client, 'marcus')).status_code == 403
    assert client.post('/agent/run', json=body, headers=_as(client, 'sam')).status_code == 403
    assert client.get('/agent/runs', headers=_as(client, 'sam')).status_code == 403


def test_require_login_locks_legacy_surface(client, monkeypatch):
    cid = _seed(client)                                                                    # open mode: legacy still works
    monkeypatch.setenv('CHURN_REQUIRE_LOGIN', '1')
    assert client.get('/cases').status_code == 401
    assert client.get('/galaxy').status_code == 401
    assert client.get('/health').status_code == 200 and client.get('/meta').status_code == 200 and client.get('/auth/accounts').status_code == 200
    h = _as(client, 'dana')
    assert client.get('/cases', headers=h).status_code == 200
    assert client.post('/cases/%d/approve' % cid, json={'approved_by': 'Dana'}, headers=h).status_code == 403   # legacy approve is closed
    assert client.post('/outbox/export', headers=h).status_code == 403
    assert client.get('/cases', headers=_as(client, 'sam')).status_code == 403             # admin sees no case data
