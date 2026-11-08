from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import autopilot as ap
from app import incidents as inc
from app import pipeline as pl
from app.main import app
from conftest import ROOT

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
REAL = {'SeniorCitizen': 0, 'Partner': 'No', 'Dependents': 'No', 'tenure': 5, 'PhoneService': 'Yes', 'MultipleLines': 'No',
        'InternetService': 'Fiber optic', 'OnlineSecurity': 'No', 'OnlineBackup': 'No', 'DeviceProtection': 'No', 'TechSupport': 'No',
        'StreamingTV': 'No', 'StreamingMovies': 'No', 'Contract': 'Month-to-month', 'PaperlessBilling': 'Yes',
        'PaymentMethod': 'Electronic check', 'MonthlyCharges': 80.0}


@pytest.fixture(scope='module')
def full():
    b = pl.load_bundle(ROOT)
    assert b['uplift'] is not None, 'run: python -m app.uplift'
    return b


@pytest.fixture
def pilot(full, tmp_path, monkeypatch):
    monkeypatch.setenv('CHURN_DB', str(tmp_path / 'ap.sqlite'))          # keep the real queue DB out of the tests
    p = ap.Autopilot(full, inc.load_config(ROOT / 'incidents.yaml'), path=str(tmp_path / 'ap.sqlite'))
    p.seed(24, now=NOW)
    return p


def test_seed_registers_demo_customers_with_every_segment(pilot):
    assert len(pilot.snap) == 24 and all(r.startswith(ap.PREFIX) for r in pilot.snap)
    assert {s['segment'] for s in pilot.snap.values()} == set(inc.SEGMENTS)
    assert any(s['vulnerable'] for s in pilot.snap.values())
    assert pilot.seed(24, now=NOW) == 24                                  # idempotent


def test_seed_is_deterministic(full, tmp_path):
    cfg = inc.load_config(ROOT / 'incidents.yaml')
    a = ap.Autopilot(full, cfg, path=str(tmp_path / 'a.sqlite'))
    b = ap.Autopilot(full, cfg, path=str(tmp_path / 'b.sqlite'))
    a.seed(16)
    b.seed(16)
    assert sorted(a.snap) == sorted(b.snap)


def test_tower_outage_hits_only_that_cell_and_only_people_with_service(pilot):
    cell = ap.CELLS[0]
    r = pilot.inject('tower_outage', now=NOW, cell=cell)
    hit = [ref for ref in pilot.snap if any(e['kind'] == 'outage' for e in inc.list_events(ref, path=pilot.path))]
    assert r['customers_hit'] == len(hit) and all(pilot.snap[x]['cell'] == cell for x in hit)
    assert any(f['kind'] == 'incident' for f in pilot.recent_feed())


def test_unknown_incident_and_empty_pilot_rejected(full, tmp_path, pilot):
    with pytest.raises(inc.IncidentError):
        pilot.inject('meteor')
    empty = ap.Autopilot(full, inc.load_config(ROOT / 'incidents.yaml'), path=str(tmp_path / 'e.sqlite'))
    with pytest.raises(inc.IncidentError, match='start it first'):
        empty.inject('tower_outage')


def test_support_backlog_makes_people_angry_and_automation_narrates_it(pilot):
    pilot.inject('support_backlog', now=NOW)
    kinds = {f['kind'] for f in pilot.recent_feed(200)}
    assert {'incident', 'angry', 'segment'} <= kinds
    assert pilot.stats()['angry_now'] > 0 and pilot.stats()['recoveries_opened'] > 0


def test_nobody_vulnerable_is_queued_and_each_person_has_one_open_case(pilot):
    pilot.inject('support_backlog', now=NOW)
    pilot.inject('late_night_outage', now=NOW)
    pilot.tick(now=NOW + timedelta(seconds=3))
    cases = pl.list_cases(path=pilot.path)
    refs = [c['customer_ref'] for c in cases]
    assert len(refs) == len(set(refs))
    for c in cases:
        assert not pilot.snap[c['customer_ref']]['vulnerable']
        assert c['status'] in ('pending_approval', 'needs_first_name', 'ready')


def test_technical_faults_auto_heal_but_human_issues_do_not(pilot):
    pilot.inject('tower_outage', now=NOW, cell=ap.CELLS[1])
    pilot.inject('support_backlog', now=NOW)
    fixed = pilot._autoheal(NOW + timedelta(seconds=ap.AUTOFIX_AFTER_S + 1))
    assert fixed
    for r in pilot.snap:
        for e in inc.list_events(r, path=pilot.path):
            if e['kind'] in ('support_unanswered', 'complaint', 'support_call'):
                assert not e['resolved']                                  # a callback needs a person


def test_nothing_heals_before_the_delay(pilot):
    pilot.inject('tower_outage', now=NOW, cell=ap.CELLS[2])
    assert pilot._autoheal(NOW + timedelta(seconds=1)) == []


def test_recovery_closes_itself_when_the_customer_calms(pilot):
    pilot.inject('support_backlog', now=NOW)
    assert inc.list_recoveries('open', path=pilot.path)
    pilot._process(sorted(pilot.snap), NOW + timedelta(days=200))      # long after: events have faded
    assert inc.list_recoveries('open', path=pilot.path) == []


def test_reset_removes_only_demo_data(pilot, full):
    inc.register_customer('REAL-1', REAL, full, path=pilot.path)
    pilot.inject('support_backlog', now=NOW)
    assert pilot.reset()['removed_customers'] == 24 and not pilot.snap
    with inc._connect(pilot.path) as con:
        assert [r['ref'] for r in con.execute('SELECT ref FROM inc_customers')] == ['REAL-1']


def test_state_is_labelled_and_complete(pilot):
    st = pilot.state()
    assert st['label'] == 'SIMULATED' and st['risk_threshold'] > 0 and len(st['customers']) == 24
    assert set(st['stats']['by_segment']) == set(inc.SEGMENTS)


def test_api_start_incident_stop_reset(tmp_path, monkeypatch):
    monkeypatch.setenv('CHURN_DB', str(tmp_path / 'api.sqlite'))
    monkeypatch.delenv('CHURN_API_KEY', raising=False)
    monkeypatch.setattr('app.main.TICK_SECONDS', 0.05)
    with TestClient(app) as c:
        st = c.post('/autopilot/start', json={'customers': 16}).json()
        assert st['running'] and len(st['customers']) == 16
        assert c.post('/autopilot/incident', json={'kind': 'support_backlog'}).json()['customers_hit'] > 0
        assert c.post('/autopilot/incident', json={'kind': 'nope'}).status_code == 422
        assert c.post('/autopilot/start', json={'customers': 2}).status_code == 422
        assert c.get('/autopilot/state').json()['stats']['events'] > 0
        assert c.post('/autopilot/stop').json()['running'] is False
        assert c.post('/autopilot/reset').json()['removed_customers'] == 16
