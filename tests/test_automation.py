"""Actions + n8n: propose -> approve -> signed webhook -> n8n reports back. A fake n8n records what the app sends."""
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import automation as au
from app import twin as tw
from app.main import app

SECRET = 'test-secret'
OFFER = {'Contract': 'One year', 'discount_pct': 5}


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('CHURN_DB', str(tmp_path / 'q.sqlite'))
    for k in ('N8N_WEBHOOK_URL', 'N8N_SHARED_SECRET', 'EVERGREEN_PUBLIC_URL'):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def n8n(monkeypatch):
    """A fake n8n webhook. .status can be changed mid-test; .calls holds (headers, raw body) for every request."""
    calls, state = [], {'status': 200}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            calls.append((dict(self.headers), body))
            self.send_response(state['status']); self.end_headers(); self.wfile.write(b'{}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv('N8N_WEBHOOK_URL', 'http://127.0.0.1:%d/webhook/evergreen-action' % srv.server_port)
    monkeypatch.setenv('N8N_SHARED_SECRET', SECRET)
    monkeypatch.setenv('EVERGREEN_PUBLIC_URL', 'http://evergreen.test:8000')
    class F:
        pass
    f = F(); f.calls, f.state = calls, state
    yield f
    srv.shutdown(); srv.server_close()


@pytest.fixture(scope='module')
def high(client):
    """Month-to-month customers the model scores high: a one-year contract + 5% clearly lowers their risk."""
    return [n['id'] for n in client.get('/galaxy').json()['nodes'] if n['c']['Contract'] == 'Month-to-month' and n['p'] > 0.5][:6]


def propose(client, node, changes=OFFER, who='Riya'):
    return client.post('/actions', json={'id': node, 'changes': changes, 'requested_by': who})


def test_not_configured_means_approved_actions_wait(client, high):
    st = client.get('/automation/status').json()
    assert st['configured'] is False and 'N8N_WEBHOOK_URL' in st['hint']
    a = propose(client, high[0]).json()
    assert a['status'] == 'pending_approval' and a['requested_by'] == 'Riya'
    b = client.post('/actions/%d/approve' % a['id'], json={'decided_by': 'Morgan'}).json()
    assert b['status'] == 'approved' and b['decided_by'] == 'Morgan'
    assert [e['event'] for e in b['events']] == ['proposed', 'approved', 'waiting']
    assert client.post('/actions/%d/callback' % a['id'], json={'status': 'done'}, headers={au.SECRET_HEADER: SECRET}).status_code == 401


def test_server_recomputes_the_numbers_and_labels_them(client, high):
    node = client.get('/galaxy').json()['nodes'][high[0]]
    a = propose(client, high[0]).json()
    assert a['risk_before'] == pytest.approx(node['p'], abs=1e-4) and a['risk_after'] < a['risk_before']
    assert a['interpretation'] == tw.INTERPRETATION and a['customer_ref'] == node['ref']
    assert a['guardrail_status'] == 'needs_human_approval' and a['approvals_needed']       # account state is unknown in the galaxy
    assert len(a['drivers']) == 3


def test_full_round_trip_with_a_signed_webhook(client, n8n, high):
    assert client.get('/automation/status').json()['configured'] is True
    a = propose(client, high[0]).json()
    assert n8n.calls == []                                                                  # nothing leaves before a person approves
    b = client.post('/actions/%d/approve' % a['id'], json={'decided_by': 'Morgan', 'note': 'ok'}).json()
    assert b['status'] == 'sent' and b['attempts'] == 1
    (headers, raw), = n8n.calls
    assert headers[au.SIGNATURE_HEADER] == 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    p = json.loads(raw)
    assert p['event'] == 'action.approved' and p['action_id'] == a['id'] and p['customer_ref'] == a['customer_ref']
    assert p['proposed_change'] == OFFER and p['approved_by'] == 'Morgan' and p['requested_by'] == 'Riya'
    assert p['labels']['interpretation'] == tw.INTERPRETATION
    assert p['callback']['url'] == 'http://evergreen.test:8000/actions/%d/callback' % a['id'] and SECRET not in raw.decode()
    # refs only: no name or contact fields anywhere in what leaves the app
    assert set(p['customer']) == {'Contract', 'tenure', 'InternetService', 'MonthlyCharges'}
    assert not {'first_name', 'name', 'email', 'phone'} & set(json.dumps(p).lower().replace('"', ' ').split())
    # n8n reports back
    assert client.post('/actions/%d/callback' % a['id'], json={'status': 'done'}).status_code == 401
    bad = client.post('/actions/%d/callback' % a['id'], json={'status': 'done'}, headers={au.SECRET_HEADER: 'nope'})
    assert bad.status_code == 401
    ok = client.post('/actions/%d/callback' % a['id'], json={'status': 'done', 'detail': 'Task created', 'external_ref': 'TASK-9'},
                     headers={au.SECRET_HEADER: SECRET})
    assert ok.status_code == 200 and ok.json()['status'] == 'done' and ok.json()['external_ref'] == 'TASK-9'
    again = client.post('/actions/%d/callback' % a['id'], json={'status': 'failed'}, headers={au.SECRET_HEADER: SECRET})
    assert again.status_code == 409                                                         # a report is accepted once
    assert client.get('/actions').json()['summary']['done'] == 1


def test_a_report_is_only_accepted_for_an_action_that_was_sent(client, n8n, high):
    a = propose(client, high[0]).json()
    r = client.post('/actions/%d/callback' % a['id'], json={'status': 'done'}, headers={au.SECRET_HEADER: SECRET})
    assert r.status_code == 409 and 'pending_approval' in r.json()['detail']


def test_a_timed_out_delivery_can_still_be_reported_by_n8n(client, n8n, high):
    n8n.state['status'] = 500
    a = propose(client, high[0]).json()
    assert client.post('/actions/%d/approve' % a['id'], json={'decided_by': 'Morgan'}).json()['status'] == 'delivery_failed'
    r = client.post('/actions/%d/callback' % a['id'], json={'status': 'done', 'detail': 'n8n did get it'}, headers={au.SECRET_HEADER: SECRET})
    assert r.status_code == 200 and r.json()['status'] == 'done'


def test_failed_delivery_can_be_retried(client, n8n, high):
    n8n.state['status'] = 500
    a = propose(client, high[0]).json()
    b = client.post('/actions/%d/approve' % a['id'], json={'decided_by': 'Morgan'}).json()
    assert b['status'] == 'delivery_failed' and b['last_error']
    n8n.state['status'] = 200
    c = client.post('/actions/%d/retry' % a['id']).json()
    assert c['status'] == 'sent' and c['attempts'] == 2 and c['last_error'] is None
    assert client.post('/actions/%d/retry' % a['id']).status_code == 409                    # already sent


def test_unreachable_n8n_is_a_delivery_failure_not_a_crash(client, monkeypatch, high):
    monkeypatch.setenv('N8N_WEBHOOK_URL', 'http://127.0.0.1:9/webhook/x')
    monkeypatch.setenv('N8N_SHARED_SECRET', SECRET)
    a = propose(client, high[0]).json()
    assert client.post('/actions/%d/approve' % a['id'], json={'decided_by': 'Morgan'}).json()['status'] == 'delivery_failed'


def test_reject_needs_a_reason_and_is_final(client, n8n, high):
    a = propose(client, high[0]).json()
    assert client.post('/actions/%d/reject' % a['id'], json={'decided_by': 'Morgan'}).status_code == 422
    r = client.post('/actions/%d/reject' % a['id'], json={'decided_by': 'Morgan', 'reason': 'vulnerable customer'}).json()
    assert r['status'] == 'rejected' and r['decided_note'] == 'vulnerable customer'
    assert client.post('/actions/%d/approve' % a['id'], json={'decided_by': 'Morgan'}).status_code == 409
    assert n8n.calls == []
    assert propose(client, high[0]).status_code == 200                                               # a rejected action no longer blocks a new one


def test_one_open_action_per_customer_and_bad_proposals(client, high):
    assert propose(client, high[0]).status_code == 200
    dup = propose(client, high[0])
    assert dup.status_code == 409 and 'already has an open action' in dup.json()['detail']
    assert propose(client, high[1], changes={'tenure': 3}).status_code == 422                # the twin refuses non-levers
    assert propose(client, high[1], changes={'discount_pct': 99}).status_code == 422
    assert propose(client, 99999).status_code == 422
    assert client.post('/actions', json={'id': high[1], 'changes': {}, 'requested_by': 'x'}).status_code == 422
    assert client.post('/actions', json={'id': high[1], 'changes': OFFER, 'requested_by': '  '}).status_code == 409
    assert client.get('/actions/9999').status_code == 404


def test_guardrails_decide_the_status_and_can_block(client, monkeypatch, high):
    a = propose(client, high[2], changes={'discount_pct': 10}).json()
    assert a['guardrail_status'] == 'needs_human_approval'                                  # galaxy never knows the account state
    assert any('discount' in w for w in a['approvals_needed'])                              # reasons are the guardrails' own words
    blocked = au.gr.GuardrailResult()
    blocked.block('account is flagged vulnerable: no contact; escalate to a human', escalate=True)
    monkeypatch.setattr(au.gr, 'check_action', lambda *x, **k: blocked)
    r = propose(client, high[3])
    assert r.status_code == 409 and 'guardrails' in r.json()['detail']
    assert [x['node_id'] for x in client.get('/actions').json()['actions']] == [high[2]]


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ftp://example.com/x', 'javascript:alert(1)', ''])
def test_only_http_webhook_urls_are_ever_followed(monkeypatch, url):
    monkeypatch.setenv('N8N_WEBHOOK_URL', url)
    monkeypatch.setenv('N8N_SHARED_SECRET', SECRET)
    assert au.config()['configured'] is False


def test_secret_is_required_for_n8n_to_count_as_configured(monkeypatch):
    monkeypatch.setenv('N8N_WEBHOOK_URL', 'http://127.0.0.1:5678/webhook/x')
    assert au.config()['configured'] is False
    assert au.secret_ok(SECRET) is False and au.secret_ok(None) is False


def test_digest_lists_refs_only_and_marks_open_actions(client, high):
    top = client.get('/automation/digest?top=5').json()
    risks = [c['risk'] for c in top['top_at_risk']]
    assert len(risks) == 5 and risks == sorted(risks, reverse=True)
    assert all(set(c) == {'customer_ref', 'risk', 'contract', 'tenure', 'monthly_charges', 'has_open_action'} for c in top['top_at_risk'])
    assert top['interpretation'] == tw.INTERPRETATION and top['threshold_is_placeholder'] is True
    node = client.get('/galaxy').json()['nodes']
    flagged_id = next(n['id'] for n in node if n['ref'] == top['top_at_risk'][0]['customer_ref'])
    assert propose(client, flagged_id).status_code == 200
    assert client.get('/automation/digest?top=5').json()['top_at_risk'][0]['has_open_action'] is True
    assert client.get('/automation/digest?top=5').json()['actions']['pending_approval'] == 1


def test_list_filters(client, high):
    a = propose(client, high[0]).json()
    assert [x['id'] for x in client.get('/actions?status=pending_approval').json()['actions']] == [a['id']]
    assert client.get('/actions?status=done').json()['actions'] == []
    assert client.get('/actions?status=bogus').status_code == 409


def test_an_edit_the_model_says_does_nothing_is_not_proposed(client, high):
    node = client.get('/galaxy').json()['nodes'][high[0]]
    same = {'PaperlessBilling': node['c']['PaperlessBilling']}                               # "change" a field to the value it already has
    r = propose(client, high[0], changes=same)
    assert r.status_code == 409 and 'no reduction' in r.json()['detail']
    assert client.get('/actions').json()['actions'] == []
