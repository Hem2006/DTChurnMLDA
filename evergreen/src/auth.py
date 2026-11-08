"""Logins and roles.

Users live in config/users.yaml (override with EVERGREEN_USERS): username, display name, role, salt and a PBKDF2-SHA256 password hash. Plain passwords are
never stored. Five failed attempts lock a username for 15 minutes. Create the accounts with `python -m src.users init`.

This is a pilot-grade login (one app, a small YAML file). For a company roll-out replace login() with the company SSO and keep the roles/permissions as they are.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from src import db
from src.data import ROOT

ITERATIONS = 240_000
MIN_PASSWORD_LEN = 10
MAX_FAILS, LOCK_MINUTES = 5, 15

ROLES = ('agent', 'manager', 'specialist', 'sender', 'admin')
ROLE_LABEL = {'agent': 'CRM agent', 'manager': 'Manager', 'specialist': 'Specialist', 'sender': 'Sender', 'admin': 'Admin'}
ROLE_BLURB = {'agent': 'Looks up customers, drafts messages and submits them for approval.',
              'manager': 'Approves or rejects messages. Cannot approve their own submissions.',
              'specialist': 'Handles customers who must not be contacted (vulnerable or open complaint).',
              'sender': 'Exports approved messages to the outbox file and marks them sent.',
              'admin': 'Manages users and setup. Can view everything, cannot approve.'}
PERMS = {
    'agent': {'lookup', 'submit', 'view_own'},
    'manager': {'lookup', 'submit', 'approve', 'view_all', 'view_escalations'},
    'specialist': {'view_escalations', 'resolve_escalation'},
    'sender': {'view_approved', 'export', 'mark_sent'},
    'admin': {'lookup', 'view_all', 'view_escalations', 'view_approved', 'admin', 'analyst'},
}
DEFAULT_USERS = [('agent', 'CRM Agent', 'agent'), ('manager', 'Manager', 'manager'), ('specialist', 'Specialist', 'specialist'),
                 ('sender', 'Sender', 'sender'), ('admin', 'Admin', 'admin')]


def app_config_path() -> Path:
    return Path(os.environ.get('EVERGREEN_APP_CONFIG', ROOT / 'config' / 'app.yaml'))


def demo_enabled() -> bool:
    p = app_config_path()
    return bool(p.exists() and (yaml.safe_load(p.read_text(encoding='utf8')) or {}).get('demo_quick_login'))


def quick_login(username: str, users: dict | None = None) -> dict:
    """Passwordless sign-in for demos. Refused unless demo_quick_login is true in config/app.yaml."""
    if not demo_enabled():
        raise LoginError('Quick sign-in is switched off.')
    users = load_users() if users is None else users
    if username not in users:
        raise LoginError('Unknown user.')
    return {'username': username, 'name': users[username]['name'], 'role': users[username]['role']}


def users_path() -> Path:
    return Path(os.environ.get('EVERGREEN_USERS', ROOT / 'config' / 'users.yaml'))


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    return salt, hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS).hex()


def load_users(path=None) -> dict:
    p = Path(path or users_path())
    if not p.exists():
        raise FileNotFoundError('%s not found: run `python -m src.users init` to create the five logins' % p)
    users = (yaml.safe_load(p.read_text(encoding='utf8')) or {}).get('users', {})
    for name, u in users.items():
        if u.get('role') not in ROLES:
            raise ValueError('user %s has unknown role %r' % (name, u.get('role')))
    return users


def save_users(users: dict, path=None):
    p = Path(path or users_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({'users': users}, sort_keys=True), encoding='utf8')


def new_user(name: str, role: str, password: str) -> dict:
    salt, h = hash_password(password)
    return {'name': name, 'role': role, 'salt': salt, 'hash': h}


def can(user: dict | None, perm: str) -> bool:
    return bool(user) and perm in PERMS.get(user['role'], ())


# ------------------------------------------------------------------ lockout (stored in the same SQLite file as the cases)
def _con(path=None):
    con = db.connect(path)
    con.execute('CREATE TABLE IF NOT EXISTS login_failures (username TEXT NOT NULL, ts TEXT NOT NULL)')
    return con


def _now():
    return datetime.now(timezone.utc)


def locked_until(username: str, path=None) -> datetime | None:
    since = (_now() - timedelta(minutes=LOCK_MINUTES)).isoformat()
    con = _con(path)
    try:
        rows = [r['ts'] for r in con.execute('SELECT ts FROM login_failures WHERE username = ? AND ts > ? ORDER BY ts', (username, since))]
    finally:
        con.close()
    return datetime.fromisoformat(rows[-MAX_FAILS]) + timedelta(minutes=LOCK_MINUTES) if len(rows) >= MAX_FAILS else None


def _record(username: str, ok: bool, path=None):
    con = _con(path)
    try:
        if ok:
            con.execute('DELETE FROM login_failures WHERE username = ?', (username,))
        else:
            con.execute('INSERT INTO login_failures (username, ts) VALUES (?, ?)', (username, _now().isoformat()))
        con.commit()
    finally:
        con.close()


class LoginError(ValueError):
    pass


def login(username: str, password: str, users: dict | None = None, db_path=None) -> dict:
    """Returns {'username', 'name', 'role'} or raises LoginError (one message for unknown user and wrong password)."""
    users = load_users() if users is None else users
    username = (username or '').strip().lower()
    if locked_until(username, db_path):
        raise LoginError('Too many failed attempts. Try again in %d minutes or ask an admin.' % LOCK_MINUTES)
    u = users.get(username)
    salt, h = hash_password(password or '', u['salt'] if u else '00' * 16)           # same work whether or not the user exists
    if not u or not hmac.compare_digest(h, u['hash']):
        _record(username, False, db_path)
        raise LoginError('Wrong username or password.')
    _record(username, True, db_path)
    return {'username': username, 'name': u['name'], 'role': u['role']}


def set_password(username: str, new_password: str, users: dict | None = None, old_password: str | None = None, path=None):
    """Change a password. Pass old_password when a user changes their own; admins resetting someone else leave it out."""
    users = load_users(path) if users is None else users
    if username not in users:
        raise ValueError('no such user')
    if len(new_password or '') < MIN_PASSWORD_LEN:
        raise ValueError('Use at least %d characters.' % MIN_PASSWORD_LEN)
    if old_password is not None:
        _, h = hash_password(old_password, users[username]['salt'])
        if not hmac.compare_digest(h, users[username]['hash']):
            raise ValueError('Current password is wrong.')
    users[username]['salt'], users[username]['hash'] = hash_password(new_password)
    save_users(users, path)


# ------------------------------------------------------------------ Streamlit glue
def current():
    import streamlit as st
    return st.session_state.get('user')


def require(perm: str | None) -> dict:
    """Page guard: returns the signed-in user, or explains and stops the page. perm=None means any signed-in user."""
    import streamlit as st
    u = current()
    if not u:
        st.warning('Please sign in first.')
        st.stop()
    if perm is not None and not can(u, perm):
        st.error('Your role (%s) does not have access to this page.' % ROLE_LABEL[u['role']])
        st.stop()
    return u


def login_form():
    import streamlit as st
    st.markdown('## 🌲 Evergreen')
    st.caption('Sign in to continue.')
    with st.form('login'):
        name = st.text_input('Username')
        pw = st.text_input('Password', type='password')
        ok = st.form_submit_button('Sign in', type='primary')
    if ok:
        try:
            st.session_state['user'] = login(name, pw)
            st.rerun()
        except FileNotFoundError as e:
            st.error(str(e))
        except LoginError as e:
            st.error(str(e))
    if demo_enabled():
        st.divider()
        st.warning('**Demo mode:** one-click sign-in, no password. Switch off with `demo_quick_login: false` in config/app.yaml.')
        try:
            users = load_users()
        except FileNotFoundError:
            return
        cols = st.columns(len(users))
        for col, (name, u) in zip(cols, users.items()):
            if col.button('%s: %s' % (ROLE_LABEL[u['role']], name), key='quick_' + name, width='stretch'):
                st.session_state['user'] = quick_login(name, users)
                st.rerun()
