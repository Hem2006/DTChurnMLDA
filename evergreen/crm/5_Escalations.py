import json

import pandas as pd
import streamlit as st

from src import auth
from src import crm
from src import db
from src import ui

b = ui.crm_setup('Escalations', '🆘', perm='view_escalations')
user = auth.current()
st.markdown('Customers here are **not to be contacted by this system**: they are marked vulnerable or have an open complaint. A specialist reviews each one and closes it with a note.')
STATUS = {'escalated': 'Open', 'escalation_resolved': 'Closed'}
choice = st.radio('Show', list(STATUS), format_func=lambda s: STATUS[s], horizontal=True, key='es_status')
cases = db.list_cases(choice)
if not cases:
    st.info('Nothing here.')
else:
    st.dataframe(pd.DataFrame([{'case': x['id'], 'customer': x['customer_ref'], 'chance of leaving': x['probability'], 'recorded by': x['submitted_by'], 'recorded': x['created_at']} for x in cases]),
                 hide_index=True, width='stretch', column_config={'chance of leaving': st.column_config.ProgressColumn('chance of leaving', min_value=0, max_value=1, format='percent')})
    cid = st.selectbox('Open a case', [x['id'] for x in cases], format_func=lambda i: 'Case %d: %s' % (i, next(x['customer_ref'] for x in cases if x['id'] == i)))
    case = db.get_case(cid)
    rec = json.loads(case['record_json'])
    st.subheader('Case %d: %s' % (case['id'], case['customer_ref']))
    for w in rec['reasons']:
        st.write('- ' + w)
    if case['status'] == 'escalation_resolved':
        st.success('Closed. %s' % case['decision_note'])
    with st.expander('History'):
        st.table(pd.DataFrame(case['events'])[['ts', 'actor', 'event', 'note']])
    if case['status'] == 'escalated':
        if auth.can(user, 'resolve_escalation'):
            note = st.text_area('What was done? (required to close)', key='es_note_%d' % cid)
            if st.button('Close this escalation', type='primary', disabled=not note.strip(), key='es_ok_%d' % cid):
                try:
                    db.resolve_escalation(cid, user['username'], note)
                    st.rerun()
                except db.CaseError as e:
                    st.error(str(e))
        else:
            st.caption('Only specialists can close escalations.')
