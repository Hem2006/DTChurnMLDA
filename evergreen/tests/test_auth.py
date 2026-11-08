"""Logins, roles, lockout, and the role rules of the case queue."""
import pytest

from src import auth
from src import db

PW = 'correct-horse-1'


@pytest.fixture
def users(tmp_path, monkeypatch):
    u = {name: auth.new_user(label, role, PW) for name, label, role in auth.DEFAULT_USERS}
    path = tmp_path / 'users.yaml'
    auth.save_users(u, path)
    monkeypatch.setenv('EVERGREEN_USERS', str(path))
    return u


def test_five_default_accounts_one_per_role():
    assert [r for _, _, r in auth.DEFAULT_USERS] == list(auth.ROLES) and len(auth.DEFAULT_USERS) == 5


def test_passwords_are_hashed_not_stored(users, tmp_path):
    raw = (tmp_path / 'users.yaml').read_text()
    assert PW not in raw and all(len(u['hash']) == 64 for u in users.values())
    assert users['agent']['salt'] != users['manager']['salt']


def test_login_ok_and_wrong_password(users, tmp_path):
    db_path = tmp_path / 'a.sqlite'
    u = auth.login('Manager', PW, users, db_path)                       # username is case-insensitive
    assert u == {'username': 'manager', 'name': 'Manager', 'role': 'manager'}
    for bad in [('manager', 'nope'), ('ghost', PW), ('manager', '')]:
        with pytest.raises(auth.LoginError, match='Wrong username or password'):
            auth.login(*bad, users, db_path)


def test_lockout_after_five_failures_then_success_is_blocked(users, tmp_path):
    p = tmp_path / 'a.sqlite'
    for _ in range(auth.MAX_FAILS):
        with pytest.raises(auth.LoginError):
            auth.login('agent', 'bad', users, p)
    with pytest.raises(auth.LoginError, match='Too many failed'):
        auth.login('agent', PW, users, p)                               # even the right password is refused while locked
    assert auth.login('manager', PW, users, p)['role'] == 'manager'     # other users unaffected


def test_successful_login_clears_failures(users, tmp_path):
    p = tmp_path / 'a.sqlite'
    for _ in range(3):
        with pytest.raises(auth.LoginError):
            auth.login('agent', 'bad', users, p)
    auth.login('agent', PW, users, p)
    for _ in range(3):
        with pytest.raises(auth.LoginError):
            auth.login('agent', 'bad', users, p)
    assert auth.login('agent', PW, users, p)


def test_change_password(users, tmp_path):
    path = tmp_path / 'users.yaml'
    with pytest.raises(ValueError, match='Current password'):
        auth.set_password('agent', 'a-brand-new-pass', users, old_password='wrong', path=path)
    with pytest.raises(ValueError, match='at least'):
        auth.set_password('agent', 'short', users, old_password=PW, path=path)
    auth.set_password('agent', 'a-brand-new-pass', users, old_password=PW, path=path)
    reloaded = auth.load_users(path)
    assert auth.login('agent', 'a-brand-new-pass', reloaded, tmp_path / 'b.sqlite')
    with pytest.raises(auth.LoginError):
        auth.login('agent', PW, reloaded, tmp_path / 'b.sqlite')


def test_permissions_matrix():
    u = lambda r: {'role': r}
    assert auth.can(u('agent'), 'submit') and not auth.can(u('agent'), 'approve')
    assert auth.can(u('manager'), 'approve') and auth.can(u('manager'), 'submit')
    assert not auth.can(u('admin'), 'approve') and not auth.can(u('admin'), 'submit') and auth.can(u('admin'), 'admin') and auth.can(u('admin'), 'analyst')
    assert auth.can(u('specialist'), 'resolve_escalation') and not auth.can(u('specialist'), 'lookup')
    assert auth.can(u('sender'), 'export') and auth.can(u('sender'), 'mark_sent') and not auth.can(u('sender'), 'approve')
    assert not auth.can(None, 'lookup')
    for role in ('agent', 'manager', 'specialist', 'sender'):
        assert not auth.can(u(role), 'analyst') and not auth.can(u(role), 'admin')


def test_real_users_file_has_five_roles_and_no_plain_text():
    p = auth.users_path()
    if not p.exists():
        pytest.skip('no users file yet')
    users = auth.load_users(p)
    assert sorted(v['role'] for v in users.values()) == sorted(auth.ROLES)
    pw_file = p.parent / 'initial_passwords.txt'
    if pw_file.exists():
        for line in pw_file.read_text().splitlines()[2:]:
            assert line.split('password: ')[1] not in p.read_text()


# ------------------------------------------------------------------ queue rules tied to people
REC = {'customer_ref': 'c1', 'decision': 'recommend_pending_approval', 'risk': {'probability': 0.7}, 'guardrail': {'status': 'needs_human_approval', 'approvals_needed': []}}
OFFER = {'name': 'o', 'description': 'x'}


def test_nobody_approves_or_rejects_their_own_submission(tmp_path):
    p = tmp_path / 'q.sqlite'
    cid = db.create_case(REC, OFFER, 'Ann', 'hi', 'template', path=p, submitted_by='manager')
    with pytest.raises(db.CaseError, match='someone else'):
        db.approve(cid, 'manager', path=p)
    with pytest.raises(db.CaseError, match='someone else'):
        db.reject(cid, 'Manager', 'no', path=p)
    db.approve(cid, 'manager2', path=p)
    assert db.get_case(cid, path=p)['status'] == 'approved'


def test_full_lifecycle_with_actors_in_history(tmp_path):
    p, out = tmp_path / 'q.sqlite', tmp_path / 'outbox.csv'
    cid = db.create_case(REC, OFFER, 'Ann', 'hi', 'template', path=p, submitted_by='agent')
    db.approve(cid, 'manager', path=p)
    db.export_outbox(out, path=p, actor='sender')
    db.mark_sent(cid, 'sender', path=p)
    c = db.get_case(cid, path=p)
    assert c['status'] == 'sent'
    assert [(e['actor'], e['event']) for e in c['events']] == [('agent', 'created'), ('manager', 'approved'), ('sender', 'exported'), ('sender', 'sent')]
    with pytest.raises(db.CaseError):
        db.mark_sent(cid, 'sender', path=p)                             # cannot mark twice


def test_cannot_mark_sent_before_export_or_resolve_non_escalation(tmp_path):
    p = tmp_path / 'q.sqlite'
    cid = db.create_case(REC, OFFER, 'Ann', 'hi', 'template', path=p, submitted_by='agent')
    with pytest.raises(db.CaseError):
        db.mark_sent(cid, 'sender', path=p)
    with pytest.raises(db.CaseError):
        db.resolve_escalation(cid, 'specialist', 'done', path=p)


def test_specialist_closes_escalation_with_a_note(tmp_path):
    p = tmp_path / 'q.sqlite'
    cid = db.create_case({**REC, 'decision': 'escalate_to_human'}, path=p, submitted_by='agent')
    with pytest.raises(db.CaseError, match='note'):
        db.resolve_escalation(cid, 'specialist', '  ', path=p)
    db.resolve_escalation(cid, 'specialist', 'Called the customer, complaint handled.', path=p)
    assert db.get_case(cid, path=p)['status'] == 'escalation_resolved'


def test_agents_see_only_their_own_cases(tmp_path):
    p = tmp_path / 'q.sqlite'
    db.create_case(REC, OFFER, 'A', 'm', 't', path=p, submitted_by='agent')
    db.create_case({**REC, 'customer_ref': 'c2'}, OFFER, 'B', 'm', 't', path=p, submitted_by='manager')
    assert [c['customer_ref'] for c in db.list_cases(submitted_by='agent', path=p)] == ['c1']
    assert len(db.list_cases(path=p)) == 2


def test_quick_login_only_when_demo_flag_on(users, tmp_path, monkeypatch):
    flag = tmp_path / 'app.yaml'
    monkeypatch.setenv('EVERGREEN_APP_CONFIG', str(flag))
    with pytest.raises(auth.LoginError, match='switched off'):          # no file -> off
        auth.quick_login('agent', users)
    flag.write_text('demo_quick_login: false\n')
    with pytest.raises(auth.LoginError):
        auth.quick_login('agent', users)
    flag.write_text('demo_quick_login: true\n')
    assert auth.quick_login('manager', users) == {'username': 'manager', 'name': 'Manager', 'role': 'manager'}
    with pytest.raises(auth.LoginError, match='Unknown'):
        auth.quick_login('ghost', users)
