"""Sign-in and roles for the four people in the retention flow.

analyst     runs the agent, studies the digital twin, hands a case to the manager
manager     approves / sends back / rejects the analyst's cases
compliance  clears the approved message, releases it to the CRM, handles vulnerable accounts
admin       sees the system, the business numbers, the people and the audit trail; touches no case

Accounts are DEMO accounts from users.yaml with one shared PIN. A successful sign-in returns a signed, expiring token
(HMAC-SHA256); the server reads the person and role from that token, never from anything the browser claims.
CHURN_AUTH_SECRET fixes the signing secret across restarts (otherwise a random one is made per process).
CHURN_REQUIRE_LOGIN=1 makes the case / agent / admin endpoints refuse requests without a valid token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ROLES = ('analyst', 'manager', 'compliance', 'admin')
ROLE_BLURB = {
    'analyst': 'Runs the retention agent, studies each customer\'s digital twin and sends cases to the manager.',
    'manager': 'Approves, sends back or rejects the analyst\'s cases. Cannot approve a case they sent themselves.',
    'compliance': 'Checks the approved message, releases it to the CRM and looks after vulnerable accounts.',
    'admin': 'Watches the system: business numbers, people, audit trail and model details. Cannot act on a case.',
}
TOKEN_HOURS = 12
MAX_FAILS, LOCK_SECONDS = 5, 60

_secret: bytes | None = None
_fails: dict[str, list[float]] = {}


class AuthError(Exception):
    """Wrong credentials or too many tries (HTTP 401 / 429)."""

    def __init__(self, msg, status=401):
        super().__init__(msg)
        self.status = status


def login_required() -> bool:
    return os.environ.get('CHURN_REQUIRE_LOGIN', '').strip().lower() in ('1', 'true', 'yes', 'on')


def load_users(path=None) -> dict:
    raw = yaml.safe_load(Path(path or ROOT / 'users.yaml').read_text(encoding='utf8')) or {}
    users = {}
    for u in raw.get('users') or []:
        if u.get('role') not in ROLES:
            raise ValueError('user %r has an unknown role %r' % (u.get('id'), u.get('role')))
        if not u.get('id') or not u.get('name') or u['id'] in users:
            raise ValueError('every user needs a unique id and a name')
        users[u['id']] = {'id': u['id'], 'name': u['name'], 'role': u['role'], 'title': u.get('title') or u['role']}
    if not users:
        raise ValueError('users.yaml has no users')
    pin = os.environ.get('CHURN_DEMO_PIN') or str(raw.get('demo_pin') or '')
    if not pin:
        raise ValueError('users.yaml needs a demo_pin (or set CHURN_DEMO_PIN)')
    return {'users': users, 'pin': pin}


def public_accounts(cfg: dict) -> list[dict]:
    return [{**u, 'blurb': ROLE_BLURB[u['role']]} for u in cfg['users'].values()]


def _key() -> bytes:
    global _secret
    env = os.environ.get('CHURN_AUTH_SECRET')
    if env:
        return env.encode()
    if _secret is None:
        _secret = secrets.token_bytes(32)
    return _secret


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def make_token(user: dict, *, now: float | None = None, hours: float = TOKEN_HOURS) -> str:
    body = _b64(json.dumps({'uid': user['id'], 'name': user['name'], 'role': user['role'], 'title': user['title'],
                            'exp': int((now or time.time()) + hours * 3600)}, separators=(',', ':')).encode())
    return body + '.' + _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def verify_token(token: str | None, *, now: float | None = None) -> dict | None:
    """The signed-in person, or None for anything missing, forged or expired."""
    try:
        body, sig = (token or '').split('.')
        if not hmac.compare_digest(_b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest()), sig):
            return None
        p = json.loads(_unb64(body))
        if p['exp'] < (now or time.time()) or p['role'] not in ROLES:
            return None
        return {'id': p['uid'], 'name': p['name'], 'role': p['role'], 'title': p['title']}
    except Exception:
        return None


def login(cfg: dict, user_id: str, pin: str, *, now: float | None = None) -> dict:
    t = now or time.time()
    recent = [x for x in _fails.get(user_id, []) if t - x < LOCK_SECONDS]
    if len(recent) >= MAX_FAILS:
        raise AuthError('too many wrong PINs; wait a minute and try again', 429)
    user = cfg['users'].get(user_id)
    if user is None or not hmac.compare_digest(str(pin or '').encode(), cfg['pin'].encode()):
        _fails[user_id] = recent + [t]
        raise AuthError('wrong account or PIN')
    _fails.pop(user_id, None)
    return {'token': make_token(user, now=t), 'user': user}
