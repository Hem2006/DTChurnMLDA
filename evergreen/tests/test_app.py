"""Headless Streamlit checks: every page loads for the right role, labels are present, and the key flows work with real role logins."""
import re

import pytest
from streamlit.testing.v1 import AppTest

from conftest import ROOT
from src import auth
from src import db

PAGES = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'pages').glob('*.py'))
CRM_PAGES = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'crm').glob('*.py'))
PW = 'correct-horse-1'
# a role that is allowed to open each CRM page
PAGE_ROLE = {'1_Today': 'agent', '2_Customer': 'agent', '3_Customer_List': 'agent', '4_Approvals': 'manager', '5_Escalations': 'specialist', '6_Outbox': 'sender', '7_Admin': 'admin'}
NAMES = {'agent': 'CRM Agent', 'manager': 'Manager', 'specialist': 'Specialist', 'sender': 'Sender', 'admin': 'Admin'}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, bundle):
    monkeypatch.setenv('EVERGREEN_DB', str(tmp_path / 'app.sqlite'))
    path = tmp_path / 'users.yaml'
    auth.save_users({n: auth.new_user(label, role, PW) for n, label, role in auth.DEFAULT_USERS}, path)
    monkeypatch.setenv('EVERGREEN_USERS', str(path))
    flag = tmp_path / 'app.yaml'
    flag.write_text('demo_quick_login: false\n')
    monkeypatch.setenv('EVERGREEN_APP_CONFIG', str(flag))
    monkeypatch.setattr('src.ui.ROOT', tmp_path)                       # outbox.csv lands in the temp folder


def run(path: str, role: str | None = 'admin', timeout=180) -> AppTest:
    at = AppTest.from_file(str(ROOT / path), default_timeout=timeout)
    if role:
        at.session_state['user'] = {'username': role, 'name': NAMES[role], 'role': role}
    return at.run()


def text(at: AppTest) -> str:
    parts = [e.value for group in (at.error, at.warning, at.info, at.success, at.markdown, at.caption, at.subheader) for e in group]
    return ' '.join(parts)


def test_page_counts():
    assert len(PAGES) == 9 and len(CRM_PAGES) == 7


# ------------------------------------------------------------------ sign-in and the router
def test_app_shows_login_when_signed_out():
    at = run('app.py', role=None)
    assert not at.exception, at.exception
    assert len(at.text_input) == 2 and 'Today' not in [t.value for t in at.title]


def test_bad_login_is_refused_and_good_login_opens_today():
    at = run('app.py', role=None)
    at.text_input[0].set_value('manager'); at.text_input[1].set_value('wrong'); at.button[0].click().run()
    assert any('Wrong username or password' in e.value for e in at.error)
    at.text_input[0].set_value('manager'); at.text_input[1].set_value(PW); at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state['user']['role'] == 'manager'
    assert 'Today' in [t.value for t in at.title]


def test_no_quick_buttons_when_demo_mode_off():
    at = run('app.py', role=None)
    assert not any(b.key and b.key.startswith('quick_') for b in at.button)


@pytest.mark.parametrize('role', list(NAMES))
def test_quick_login_buttons_sign_in_each_role(role, tmp_path):
    (tmp_path / 'app.yaml').write_text('demo_quick_login: true\n')
    at = run('app.py', role=None)
    assert any('Demo mode' in w.value for w in at.warning)
    next(b for b in at.button if b.key == 'quick_' + role).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state['user']['role'] == role and 'Today' in [t.value for t in at.title]


@pytest.mark.parametrize('role, analyst', [('agent', False), ('manager', False), ('specialist', False), ('sender', False), ('admin', True)])
def test_analyst_switch_only_for_admin(role, analyst):
    at = run('app.py', role=role)
    assert not at.exception, [e.value for e in at.exception]
    assert any(t.key == 'analyst_tools' for t in at.toggle) is analyst


def test_analyst_tools_load_for_admin():
    at = run('app.py', role='admin')
    next(t for t in at.toggle if t.key == 'analyst_tools').set_value(True).run()
    assert not at.exception, [e.value for e in at.exception]


def test_sign_out_returns_to_login():
    at = run('app.py', role='agent')
    next(b for b in at.button if b.label == 'Sign out').click().run()
    assert 'user' not in at.session_state and len(at.text_input) == 2


# ------------------------------------------------------------------ pages
@pytest.mark.parametrize('page', PAGES)
def test_every_analyst_page_loads(page):
    at = run(page, 'admin')
    assert not at.exception, [e.value for e in at.exception]
    assert at.title, page
    assert any('PLACEHOLDER' in w.value for w in at.sidebar.warning)


@pytest.mark.parametrize('page', PAGES)
def test_analyst_pages_refuse_non_admins(page):
    at = run(page, 'manager')
    assert any('does not have access' in e.value for e in at.error)


@pytest.mark.parametrize('page', CRM_PAGES)
def test_crm_pages_load_and_use_plain_language(page):
    at = run(page, PAGE_ROLE[page.replace('\\', '/').split('/')[-1][:-3]])
    assert not at.exception, [e.value for e in at.exception]
    shown = text(at) + ' ' + ' '.join(m.label for m in at.metric)
    for jargon in ('SHAP', 'log-odds', 'calibrat', 'isotonic', 'Brier', 'ECE', 'PSI', 'XGBoost', 'threshold'):
        assert not re.search(r'%s' % re.escape(jargon.lower()), shown.lower()), (page, jargon)


@pytest.mark.parametrize('page, role', [('crm/2_Customer.py', 'specialist'), ('crm/4_Approvals.py', 'sender'), ('crm/5_Escalations.py', 'agent'), ('crm/6_Outbox.py', 'agent'),
                                         ('crm/7_Admin.py', 'manager'), ('crm/3_Customer_List.py', 'sender')])
def test_pages_refuse_the_wrong_role(page, role):
    at = run(page, role)
    assert any('does not have access' in e.value for e in at.error)


def test_signed_out_page_is_refused():
    at = run('crm/2_Customer.py', role=None)
    assert any('sign in' in w.value.lower() for w in at.warning)


def test_what_if_lab_is_labelled_not_causal():
    at = run('pages/2_What-If_Lab.py')
    assert any('NOT A CAUSAL ESTIMATE' in e.value for e in at.error)


def test_uplift_view_is_labelled_simulated_permanently(bundle):
    if bundle['uplift'] is None:
        pytest.skip('uplift not built')
    at = run('pages/8_Uplift_View.py')
    assert any('SIMULATED' in e.value for e in at.error) and not at.exception


def test_analyst_explorer_flagged_customer_pending_approval():
    at = run('pages/1_Customer_Explorer.py')
    next(c for c in at.checkbox if c.key == 'ce_flagged').check().run()
    assert not at.exception
    assert any('pending human approval' in w.value for w in at.warning)


def test_stress_tester_vulnerable_escalates_and_red_team_rejects():
    at = run('pages/5_Guardrail_Stress-Tester.py')
    next(c for c in at.checkbox if c.key == 'gs_flagged').check().run()
    next(s for s in at.selectbox if s.key == 'gs_v').select('yes').run()
    assert any('Escalate to a human' in e.value for e in at.error)
    next(s for s in at.selectbox if s.label == 'Load an attack').select('Pressure').run()
    assert any('REJECTED' in e.value for e in at.error)


def test_batch_and_drift_pages():
    at = run('pages/10_Batch_and_Drift.py')
    next(c for c in at.checkbox if c.key == 'b_sample').check().run()
    assert any(m.label == 'Scored' for m in at.metric)
    next(c for c in at.checkbox if c.key == 'd_sample').check().run()
    assert any(m.label == 'PSI: tenure' for m in at.metric) and not at.exception


# ------------------------------------------------------------------ the approval flow, person by person
def submit_as(role='agent', first='Maria'):
    at = run('crm/2_Customer.py', role)
    next(c for c in at.checkbox if c.key == 'cr_hi').check().run()
    assert any(m.label == 'Chance of leaving' for m in at.metric)
    assert any('manager must approve' in m.value for m in at.markdown)              # account state left as "not sure"
    next(t for t in at.text_input if t.key == 'cr_first').set_value(first).run()
    assert any('Message checks passed' in s.value for s in at.success)
    next(b for b in at.button if b.label == 'Send for approval').click().run()
    return at


def test_full_flow_agent_manager_sender(tmp_path):
    submit_as('agent')
    cases = db.list_cases()
    assert len(cases) == 1 and cases[0]['status'] == 'pending_approval' and cases[0]['submitted_by'] == 'agent'
    cid = cases[0]['id']

    ag = run('crm/4_Approvals.py', 'agent')                                          # the agent sees it but cannot decide
    assert not any(b.key == 'ap_ok_%d' % cid for b in ag.button)

    mg = run('crm/4_Approvals.py', 'manager')
    assert not next(b for b in mg.button if b.key == 'ap_ok_%d' % cid).disabled
    next(b for b in mg.button if b.key == 'ap_ok_%d' % cid).click().run()
    c = db.get_case(cid)
    assert c['status'] == 'approved' and c['decided_by'] == 'manager'

    sd = run('crm/6_Outbox.py', 'sender')
    next(b for b in sd.button if b.label.startswith('Export 1')).click().run()
    assert db.get_case(cid)['status'] == 'exported' and (tmp_path / 'outbox.csv').exists()
    sd = run('crm/6_Outbox.py', 'sender')
    next(b for b in sd.button if b.label.startswith('Mark all 1')).click().run()
    assert db.get_case(cid)['status'] == 'sent'
    assert [e['actor'] for e in db.get_case(cid)['events']] == ['agent', 'manager', 'sender', 'sender']


def test_manager_cannot_approve_own_submission():
    submit_as('manager')
    cid = db.list_cases()[0]['id']
    ap = run('crm/4_Approvals.py', 'manager')
    assert next(b for b in ap.button if b.key == 'ap_ok_%d' % cid).disabled
    assert any('another manager' in i.value for i in ap.info)


def test_manager_rejects_with_reason():
    submit_as('agent')
    cid = db.list_cases()[0]['id']
    mg = run('crm/4_Approvals.py', 'manager')
    assert next(b for b in mg.button if b.key == 'ap_no_%d' % cid).disabled           # reason missing
    next(t for t in mg.text_input if t.key == 'ap_why_%d' % cid).set_value('Customer asked not to be contacted').run()
    next(b for b in mg.button if b.key == 'ap_no_%d' % cid).click().run()
    c = db.get_case(cid)
    assert c['status'] == 'rejected' and c['decided_by'] == 'manager' and 'asked not' in c['decision_note']


def test_second_submission_for_same_customer_is_refused():
    at = submit_as('agent')
    next(b for b in at.button if b.label == 'Send for approval').click().run()
    assert len(db.list_cases()) == 1 and any('already has a message waiting' in e.value for e in at.error)


def test_admin_cannot_submit():
    ad = run('crm/2_Customer.py', 'admin')
    next(c for c in ad.checkbox if c.key == 'cr_hi').check().run()
    assert not any(b.label == 'Send for approval' for b in ad.button)


def test_specialist_resolves_escalation_with_a_note():
    ag = run('crm/2_Customer.py', 'agent')
    next(c for c in ag.checkbox if c.key == 'cr_hi').check().run()
    next(s for s in ag.selectbox if s.key == 'cr_v').select('Yes').run()
    assert any('Hand to a specialist' in e.value for e in ag.error)
    next(b for b in ag.button if b.label.startswith('Record that')).click().run()
    esc = db.list_cases('escalated')
    assert len(esc) == 1 and esc[0]['submitted_by'] == 'agent'
    cid = esc[0]['id']
    sp = run('crm/5_Escalations.py', 'specialist')
    assert next(b for b in sp.button if b.key == 'es_ok_%d' % cid).disabled              # needs a note
    next(t for t in sp.text_area if t.key == 'es_note_%d' % cid).set_value('Care team called the customer.').run()
    next(b for b in sp.button if b.key == 'es_ok_%d' % cid).click().run()
    assert db.get_case(cid)['status'] == 'escalation_resolved'


def test_agent_list_flow_queues_cases_with_submitter():
    at = run('crm/3_Customer_List.py', 'agent')
    next(c for c in at.checkbox if c.key == 'cl_sample').check().run()
    assert any(m.label == 'Customers checked' and m.value == '150' for m in at.metric)
    next(b for b in at.button if b.label.startswith('Draft messages')).click().run()
    cases = db.list_cases('pending_approval')
    assert cases and {c['submitted_by'] for c in cases} == {'agent'}
    ad = run('crm/3_Customer_List.py', 'admin')                                       # admin can check lists but not submit
    next(c for c in ad.checkbox if c.key == 'cl_sample').check().run()
    assert next(b for b in ad.button if b.label.startswith('Draft messages')).disabled


def test_agent_sees_only_own_cases_in_approvals():
    submit_as('agent')
    db.create_case({'customer_ref': 'other', 'decision': 'recommend_pending_approval', 'risk': {'probability': 0.5, 'threshold': 0.3},
                    'guardrail': {'status': 'x', 'approvals_needed': []}, 'reasons': [], 'fired_by': []},
                   {'name': 'o', 'description': 'x'}, 'Bob', 'hi', 'template', submitted_by='manager')
    mine = run('crm/4_Approvals.py', 'agent')
    assert any('messages **you** submitted' in c.value for c in mine.caption)
    assert set(mine.dataframe[0].value['submitted by']) == {'agent'}
    mgr = run('crm/4_Approvals.py', 'manager')
    assert set(mgr.dataframe[0].value['submitted by']) == {'agent', 'manager'}


def test_admin_page_lists_five_people_and_resets_password():
    at = run('crm/7_Admin.py', 'admin')
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.dataframe[0].value) == 5
    next(s for s in at.selectbox if s.key == 'ad_who').select('agent').run()
    next(t for t in at.text_input if t.key == 'ad_pw').set_value('Reset-pass-12345').run()
    next(b for b in at.button if b.label == 'Set password').click().run()
    assert auth.login('agent', 'Reset-pass-12345', db_path=None)
