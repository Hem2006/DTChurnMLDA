import pandas as pd
import streamlit as st

from src import auth
from src import crm
from src import db
from src import ui

b = ui.crm_setup('Admin', '⚙️', perm='admin')
users = auth.load_users()
st.subheader('People and roles')
st.dataframe(pd.DataFrame([{'username': u, 'name': v['name'], 'role': auth.ROLE_LABEL[v['role']], 'can do': auth.ROLE_BLURB[v['role']]} for u, v in users.items()]),
             hide_index=True, width='stretch')

st.subheader('Reset a password')
c = st.columns(3)
who = c[0].selectbox('User', list(users), key='ad_who')
pw = c[1].text_input('New password (at least %d characters)' % auth.MIN_PASSWORD_LEN, type='password', key='ad_pw')
if c[2].button('Set password', disabled=len(pw) < auth.MIN_PASSWORD_LEN):
    auth.set_password(who, pw, users)
    st.success('Password updated for %s.' % who)
    st.session_state.pop('ad_pw', None)

st.subheader('Setup')
notes = crm.setup_notices(b)
if notes:
    for x in notes:
        st.warning(x)
    st.caption('These are set in `config/business.yaml` and `config/policy.yaml`. Restart the app after editing.')
else:
    st.success('Business numbers and policy limits are all set.')

st.subheader('Recent activity')
con = db.connect()
ev = pd.DataFrame([dict(r) for r in con.execute('SELECT e.ts, e.actor, e.event, e.note, c.customer_ref AS customer, e.case_id AS "case" FROM events e JOIN cases c ON c.id = e.case_id ORDER BY e.id DESC LIMIT 50')])
con.close()
st.dataframe(ev if len(ev) else pd.DataFrame({'info': ['No activity yet']}), hide_index=True, width='stretch')
