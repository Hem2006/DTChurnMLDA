"""Manage logins from the command line.

    python -m src.users init            # create the five accounts with random passwords -> config/initial_passwords.txt
    python -m src.users passwd <user>   # set a new password (prompted, not echoed)
    python -m src.users list
"""
from __future__ import annotations

import getpass
import secrets
import sys

from src import auth


def init(force: bool = False) -> dict:
    p = auth.users_path()
    if p.exists() and not force:
        raise SystemExit('%s already exists (use --force to overwrite and issue new passwords)' % p)
    users, lines = {}, ['Initial passwords for Evergreen. Change them after first sign-in (sidebar > Change password), then delete this file.', '']
    for user, name, role in auth.DEFAULT_USERS:
        pw = secrets.token_urlsafe(10)
        users[user] = auth.new_user(name, role, pw)
        lines.append('%-11s %-13s password: %s' % (user, '(%s)' % auth.ROLE_LABEL[role], pw))
    auth.save_users(users)
    out = p.parent / 'initial_passwords.txt'
    out.write_text('\n'.join(lines) + '\n', encoding='utf8')
    print('Created %d users in %s\nInitial passwords written to %s (not shown here; keep it private and delete it after first use).' % (len(users), p, out))
    return users


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else 'list'
    if cmd == 'init':
        init('--force' in a)
    elif cmd == 'passwd' and len(a) > 1:
        pw = getpass.getpass('New password for %s: ' % a[1])
        auth.set_password(a[1], pw)
        print('Password updated.')
    elif cmd == 'list':
        for u, v in auth.load_users().items():
            print('%-11s %-13s %s' % (u, auth.ROLE_LABEL[v['role']], v['name']))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
