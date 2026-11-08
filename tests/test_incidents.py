from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import guardrails as gr
from app import incidents as inc
from app import twin as tw
from app import uplift as up
from app.main import app
from conftest import ROOT
from test_twin import FIBER, NO_NET

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
RULES = {'max_discount_pct_per_customer_per_quarter': 15, 'max_contacts_per_month': 2, 'pressure_language': gr.load_guardrails()['pressure_language']}
OK = {'vulnerable': False, 'escalated': False, 'contacts_this_month': 0, 'discount_pct_used_this_quarter': 0}
SAFE = dict(FIBER, tenure=70, Contract='Two year', MonthlyCharges=40.0, Partner='Yes', Dependents='Yes')


@pytest.fixture(scope='module')
def cfg():
    return inc.load_config(ROOT / 'incidents.yaml')


@pytest.fixture(scope='module')
def full(bundle):
    b = dict(bundle, scenarios=tw.load_scenarios(ROOT / 'scenarios.yaml'), guardrails=RULES, uplift=up.load(ROOT / up.ARTIFACT))
    assert b['uplift'] is not None, 'run: python -m app.uplift'
    return b


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / 'inc.sqlite')


def ev(kind, days_ago=1, hour=12, severity=3, work=False, resolved=False):
    t = (NOW - timedelta(days=days_ago)).replace(hour=hour)
    return {'kind': kind, 'occurred_at': t.isoformat(), 'severity': severity, 'work_impact': work, 'resolved': resolved}


# ------------------------------------------------------------------ config
def test_shipped_config_loads_and_is_labelled(cfg):
    assert set(cfg['storylines']) >= {'late_night_outage', 'ignored_by_support'}
    assert 'SIMULATED' in inc.NOTE
    assert cfg['frustration_risk_bump'] is None            # no invented effect on the served model's score


def test_config_rejects_bad_values(cfg, tmp_path):
    import yaml
    for edit in ({'trigger_level': 'furious'}, {'levels': {'annoyed': .9, 'angry': .5}}, {'half_life_days': 0},
                 {'frustration_risk_bump': 3}, {'event_kinds': {'outage': {'weight': -1}}}):
        p = tmp_path / 'c.yaml'
        p.write_text(yaml.safe_dump({**cfg, **edit}))
        with pytest.raises(inc.IncidentError):
            inc.load_config(p)


# ------------------------------------------------------------------ frustration
def test_no_events_is_calm(cfg):
    f = inc.frustration([], cfg, NOW)
    assert f['score'] == 0 and f['level'] == 'calm' and f['label'] == 'SIMULATED'


def test_late_night_work_impact_unresolved_counts_more(cfg):
    base = inc.frustration([ev('outage', hour=14)], cfg, NOW)['score']
    night = inc.frustration([ev('outage', hour=23)], cfg, NOW)['score']
    work = inc.frustration([ev('outage', hour=23, work=True)], cfg, NOW)['score']
    fixed = inc.frustration([ev('outage', hour=23, work=True, resolved=True)], cfg, NOW)['score']
    assert base < night < work and fixed < work


def test_late_night_window_wraps_midnight(cfg):
    m = cfg['modifiers']['late_night']
    assert inc._late_night(23, m) and inc._late_night(2, m) and not inc._late_night(6, m) and not inc._late_night(14, m)


def test_old_events_fade(cfg):
    assert inc.frustration([ev('outage', days_ago=60)], cfg, NOW)['score'] < inc.frustration([ev('outage', days_ago=1)], cfg, NOW)['score'] / 3


def test_repeat_contact_multiplier(cfg):
    three = [ev('support_call', days_ago=d) for d in (1, 2, 3)]
    two = three[:2]
    f3, f2 = inc.frustration(three, cfg, NOW), inc.frustration(two, cfg, NOW)
    assert f3['notes'] and not f2['notes']


def test_future_events_ignored(cfg):
    assert inc.frustration([ev('outage', days_ago=-3)], cfg, NOW)['n_events'] == 0


def test_levels_are_ordered_by_score(cfg):
    calm = inc.frustration([ev('support_call')], cfg, NOW)
    angry = inc.frustration([ev('outage', hour=23, work=True, severity=5), ev('support_unanswered', severity=5), ev('complaint', severity=5)], cfg, NOW)
    assert calm['level'] == 'calm' and angry['level'] == 'angry' and angry['drivers'][0]['strength'] >= angry['drivers'][-1]['strength']


# ------------------------------------------------------------------ segments
@pytest.mark.parametrize('p,flag,u,seg', [(.8, True, .15, 'persuadable'), (.1, False, .01, 'sure_thing'), (.8, True, .0, 'lost_cause'),
                                          (.8, True, -.1, 'sleeping_dog'), (.1, False, -.1, 'sleeping_dog')])
def test_four_segments(p, flag, u, seg):
    assert inc.classify(p, flag, u, .02)[0] == seg


def test_no_uplift_model_means_no_segment():
    seg, why = inc.classify(.8, True, None, .02)
    assert seg is None and 'uplift' in why


# ------------------------------------------------------------------ store + self-healing
def test_events_need_a_registered_customer(cfg, db):
    with pytest.raises(inc.IncidentError, match='not registered'):
        inc.log_event('c1', 'outage', cfg, path=db)


def test_register_validates_row_and_ref(full, db):
    with pytest.raises(ValueError):
        inc.register_customer('c1', dict(FIBER, Contract='Forever'), full, path=db)
    with pytest.raises(inc.IncidentError):
        inc.register_customer('bad ref!', FIBER, full, path=db)


def test_event_validation(full, cfg, db):
    inc.register_customer('c1', FIBER, full, path=db)
    for kw in ({'kind': 'meteor'}, {'kind': 'outage', 'severity': 9}, {'kind': 'outage', 'severity': True}):
        with pytest.raises(inc.IncidentError):
            inc.log_event('c1', cfg=cfg, path=db, **kw)


def test_quiet_customer_does_not_trigger(full, cfg, db):
    inc.register_customer('c1', FIBER, full, account_state=OK, path=db)
    assert inc.evaluate('c1', full, cfg, now=NOW, path=db)['customer'] == FIBER      # the twin panel runs on exactly these inputs
    r = inc.process('c1', full, cfg, now=NOW, path=db)
    assert not r['triggered'] and r['recovery'] is None and not r['recovery_created']


def test_storyline_triggers_one_recovery_that_is_refreshed_not_duplicated(full, cfg, db):
    inc.register_customer('c1', FIBER, full, account_state=OK, path=db)
    inc.simulate('c1', 'late_night_outage', cfg, now=NOW, seed=1, path=db)
    r = inc.process('c1', full, cfg, now=NOW, path=db)
    assert r['frustration']['level'] == 'angry' and r['triggered'] and r['recovery_created']
    assert r['segment'] in inc.SEGMENTS and r['plan']['label'] == 'SIMULATED' and r['simulated_uplift']['label'] == 'SIMULATED'
    again = inc.process('c1', full, cfg, now=NOW + timedelta(hours=1), path=db)
    assert not again['recovery_created'] and len(inc.list_recoveries('open', path=db)) == 1


def test_internal_tasks_are_auto_created_for_open_events_only(full, cfg, db):
    inc.register_customer('c1', FIBER, full, account_state=OK, path=db)
    inc.simulate('c1', 'late_night_outage', cfg, now=NOW, seed=1, path=db)
    tasks = {t['task']: t for t in inc.evaluate('c1', full, cfg, now=NOW, path=db)['plan']['tasks']}
    assert {'engineering_check', 'priority_callback'} <= set(tasks) and all(t['status'] == 'auto_created' for t in tasks.values())
    for e in inc.list_events('c1', path=db):
        inc.resolve_event(e['id'], path=db)
    left = inc.evaluate('c1', full, cfg, now=NOW, path=db)['plan']['tasks']
    assert [t['task'] for t in left] in ([], ['senior_review'])      # nothing open to fix; an angry customer still gets a senior look


def test_simulator_is_seeded_and_reset_keeps_logged_events(full, cfg, db):
    inc.register_customer('a', FIBER, full, path=db)
    inc.register_customer('b', FIBER, full, path=db)
    inc.simulate('a', 'flaky_line', cfg, now=NOW, seed=7, path=db)
    inc.simulate('b', 'flaky_line', cfg, now=NOW, seed=7, path=db)
    assert [e['occurred_at'] for e in inc.list_events('a', path=db)] == [e['occurred_at'] for e in inc.list_events('b', path=db)]
    inc.log_event('a', 'complaint', cfg, occurred_at=NOW, path=db)
    assert all(e['source'] == 'simulated' for e in inc.list_events('a', path=db)[:-1])
    inc.reset_simulated('a', path=db)
    left = inc.list_events('a', path=db)
    assert len(left) == 1 and left[0]['source'] == 'logged'
    with pytest.raises(inc.IncidentError):
        inc.simulate('a', 'nope', cfg, path=db)


def test_reset_discards_open_recovery(full, cfg, db):
    inc.register_customer('c1', FIBER, full, account_state=OK, path=db)
    inc.simulate('c1', 'late_night_outage', cfg, now=datetime.now(timezone.utc), path=db)
    assert inc.process('c1', full, cfg, path=db)['recovery'] is not None
    inc.reset_simulated('c1', path=db)
    assert inc.current_recovery('c1', path=db) is None


def test_close_recovery_needs_a_named_person_and_only_once(full, cfg, db):
    inc.register_customer('c1', FIBER, full, account_state=OK, path=db)
    inc.simulate('c1', 'late_night_outage', cfg, now=datetime.now(timezone.utc), path=db)
    rid = inc.process('c1', full, cfg, path=db)['recovery']['id']
    with pytest.raises(inc.IncidentError):
        inc.close_recovery(rid, '  ', path=db)
    assert inc.close_recovery(rid, 'Dana', 'called back', path=db)['status'] == 'closed'
    with pytest.raises(inc.IncidentError, match='already'):
        inc.close_recovery(rid, 'Dana', path=db)


# ------------------------------------------------------------------ what may reach the customer
def test_vulnerable_or_escalated_never_contacted(full):
    for seg in inc.SEGMENTS:
        c = inc._contact(seg, FIBER, full, dict(OK, vulnerable=True))
        assert c['status'] == 'escalate_to_human' and c['type'] is None


def test_sleeping_dog_and_lost_cause_get_no_offer(full):
    assert inc._contact('sleeping_dog', FIBER, full, OK)['status'] == 'no_contact'
    assert inc._contact('lost_cause', FIBER, full, OK)['status'] == 'no_offer'


def test_sure_thing_gets_apology_without_offer_and_unknown_state_needs_approval(full):
    ok = inc._contact('sure_thing', FIBER, full, OK)
    assert ok['status'] == 'ready_apology' and ok['type'] == 'service_apology' and ok['recommendation'] is None
    assert inc._contact('sure_thing', FIBER, full, None)['status'] == 'pending_approval'


def test_persuadable_uses_the_recommender_and_its_guardrails(full):
    c = inc._contact('persuadable', FIBER, full, None)
    assert c['recommendation']['decision'] == 'recommend_pending_approval' and c['status'] == 'pending_approval'
    c = inc._contact('persuadable', FIBER, full, OK)
    assert c['status'] == 'ready_offer' and c['recommendation']['action'] != 'do_nothing'


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CHURN_DB', str(tmp_path / 'api.sqlite'))
    monkeypatch.delenv('CHURN_API_KEY', raising=False)
    with TestClient(app) as c:
        yield c


def test_api_end_to_end(client):
    assert 'late_night_outage' in client.get('/incidents/config').json()['storylines']
    r = client.post('/incidents/customers', json={'customer_ref': 'c-1', 'first_name': 'Sam', 'customer': FIBER, 'account_state': OK})
    assert r.status_code == 200
    r = client.post('/incidents/simulate', json={'customer_ref': 'c-1', 'storyline': 'late_night_outage'})
    j = r.json()
    assert r.status_code == 200 and j['frustration']['level'] == 'angry' and j['recovery']['status'] == 'open'
    assert j['plan']['note'].startswith('SIMULATED') and j['plan']['tasks']
    assert client.get('/incidents/customers').json()['customers'][0]['customer_ref'] == 'c-1'
    rid = j['recovery']['id']
    assert client.post('/incidents/recoveries/%d/close' % rid, json={'closed_by': 'Dana'}).json()['status'] == 'closed'
    assert client.get('/incidents/recoveries', params={'status': 'open'}).json()['recoveries'] == []


def test_api_rejects_bad_input(client):
    assert client.post('/incidents/events', json={'customer_ref': 'ghost', 'kind': 'outage'}).status_code == 422
    client.post('/incidents/customers', json={'customer_ref': 'c-2', 'customer': NO_NET})
    assert client.post('/incidents/events', json={'customer_ref': 'c-2', 'kind': 'outage', 'severity': 9}).status_code == 422
    assert client.post('/incidents/events', json={'customer_ref': 'c-2', 'kind': 'outage', 'occurred_at': 'yesterday-ish'}).status_code == 422
    assert client.post('/incidents/simulate', json={'customer_ref': 'c-2', 'storyline': 'nope'}).status_code == 422


def test_api_key_protects_incidents(client, monkeypatch):
    monkeypatch.setenv('CHURN_API_KEY', 'k')
    assert client.get('/incidents/customers').status_code == 401
    assert client.get('/incidents/customers', headers={'X-API-Key': 'k'}).status_code == 200


# ------------------------------------------------------------------ speed: the slow twin run is memoised and skipped for calm customers
def test_recommendation_is_memoised_per_customer_state_and_limits(full):
    b = dict(full)
    first = inc.recommendation(FIBER, b, OK)
    assert inc.recommendation(FIBER, b, OK) is first                                # same object: not recomputed
    assert inc.recommendation(FIBER, b, None) is not first                           # different account state, different answer
    assert inc.recommendation(FIBER, dict(b, guardrails=dict(RULES, max_contacts_per_month=9)), OK) is not first


def test_light_evaluate_skips_the_contact_decision_but_process_fills_it_in_when_triggered(full, cfg, db):
    inc.register_customer('c1', FIBER, full, account_state=OK, path=db)
    light = inc.evaluate('c1', full, cfg, now=NOW, path=db, full=False)
    assert light['plan']['customer_contact']['status'] == 'not_evaluated' and light['segment'] in inc.SEGMENTS
    inc.simulate('c1', 'late_night_outage', cfg, now=NOW, seed=1, path=db)
    assert inc.process('c1', full, cfg, now=NOW, path=db)['plan']['customer_contact']['status'] != 'not_evaluated'
