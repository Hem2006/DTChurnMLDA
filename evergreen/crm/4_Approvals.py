import json

import pandas as pd
import streamlit as st

from src import auth
from src import crm
from src import db
from src import ui

user = auth.current()
b = ui.crm_setup('Approvals', '✅', perm='view_all' if auth.can(user, 'view_all') else 'view_own')
approver = auth.can(user, 'approve')
mine_only = not auth.can(user, 'view_all')
if approver:
    st.caption('You are signed in as **%s**. Your name is recorded with every decision. You cannot decide a case you submitted yourself.' % user['name'])
elif mine_only:
    st.caption('These are the messages **you** submitted. A manager decides them.')
else:
    st.caption('View only. Managers approve messages.')
STATUS = {'pending_approval': 'Waiting for approval', 'approved': 'Approved', 'rejected': 'Rejected', 'exported': 'Exported', 'sent': 'Sent'}

n = crm.queue_counts()
c = st.columns(4)
c[0].metric('Waiting for approval', n['waiting'])
c[1].metric('Approved, not yet exported', n['approved'])
c[2].metric('Exported / sent', n['sent_files'])
c[3].metric('Rejected', n['rejected'])

choice = st.radio('Show', list(STATUS), format_func=lambda s: STATUS[s], horizontal=True, key='ap_status')
cases = db.list_cases(choice, submitted_by=user['username'] if mine_only else None)
if not cases:
    st.info('Nothing here.')
else:
    st.dataframe(pd.DataFrame([{'case': x['id'], 'customer': x['customer_ref'], 'first name': x['first_name'], 'offer': (json.loads(x['offer_json'])['description'] if x['offer_json'] else ''),
                                'chance of leaving': x['probability'], 'submitted by': x['submitted_by'], 'created': x['created_at']} for x in cases]), hide_index=True, width='stretch',
                 column_config={'chance of leaving': st.column_config.ProgressColumn('chance of leaving', min_value=0, max_value=1, format='percent')})
    cid = st.selectbox('Open a case', [x['id'] for x in cases], format_func=lambda i: 'Case %d: %s' % (i, next(x['customer_ref'] for x in cases if x['id'] == i)))
    case = db.get_case(cid)
    rec = json.loads(case['record_json'])
    st.subheader('Case %d: %s' % (case['id'], case['customer_ref']))
    bd = crm.band(rec['risk']['probability'], rec['risk']['threshold'])
    st.write('**%s** (%s)' % (crm.BAND_LABEL[bd], crm.one_in(rec['risk']['probability'])))
    if case['offer_json']:
        st.write('**Offer:** %s' % json.loads(case['offer_json'])['description'])
    needs = [crm.RULE_PLAIN.get(f, f) for f in rec.get('fired_by', [])]
    if needs:
        st.warning('Please check before approving:\n\n' + '\n'.join('- ' + x for x in needs))
    if case['message']:
        st.text_area('Message', case['message'], height=130, disabled=True, key='ap_msg_%d' % cid)
    st.caption('Submitted by %s.' % (case['submitted_by'] or 'unknown'))
    if case['status'] == 'rejected':
        st.write('Rejected by %s: %s' % (case['decided_by'], case['decision_note']))
    elif case['status'] in ('approved', 'exported', 'sent'):
        st.write('Approved by %s on %s.' % (case['decided_by'], case['decided_at'][:10]))
    with st.expander('History'):
        st.table(pd.DataFrame(case['events'])[['ts', 'actor', 'event', 'note']])
    if case['status'] == 'pending_approval' and approver:
        own = (case['submitted_by'] or '').lower() == user['username']
        if own:
            st.info('You submitted this case, so another manager has to decide it.')
        why = st.text_input('Reason (only needed to reject)', key='ap_why_%d' % cid, disabled=own)
        a, r = st.columns(2)
        try:
            if a.button('Approve', type='primary', disabled=own, key='ap_ok_%d' % cid):
                db.approve(cid, user['username'])
                st.rerun()
            if r.button('Reject', disabled=own or not why.strip(), key='ap_no_%d' % cid):
                db.reject(cid, user['username'], why)
                st.rerun()
        except db.CaseError as e:
            st.error(str(e))
