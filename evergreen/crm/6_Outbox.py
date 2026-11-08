import pandas as pd
import streamlit as st

from src import auth
from src import crm
from src import db
from src import ui

b = ui.crm_setup('Outbox', '📤', perm='view_approved')
user = auth.current()
st.markdown('Approved messages are exported to a file (`outbox.csv`) for your sending tool. **Nothing is sent from here.** After the messages go out, mark them as sent.')
n = crm.queue_counts()
c = st.columns(3)
c[0].metric('Approved, not yet exported', n['approved'])
c[1].metric('Exported, not yet marked sent', n['to_send'])
c[2].metric('Exported / sent in total', n['sent_files'])

if auth.can(user, 'export'):
    if st.button('Export %d approved message(s) to outbox.csv' % n['approved'], type='primary', disabled=not n['approved']):
        out = db.export_outbox(ui.ROOT / 'outbox.csv', actor=user['username'])
        st.success('%d message(s) exported.' % len(out))
        st.rerun()
else:
    st.caption('View only. The sender exports messages.')
ob = ui.ROOT / 'outbox.csv'
if ob.exists():
    st.download_button('Download outbox.csv', ob.read_bytes(), file_name='outbox.csv', mime='text/csv')

st.subheader('Exported, waiting to be marked as sent')
cases = db.list_cases('exported')
if not cases:
    st.info('Nothing waiting.')
else:
    st.dataframe(pd.DataFrame([{'case': x['id'], 'customer': x['customer_ref'], 'first name': x['first_name'], 'approved by': x['decided_by'], 'exported': x['exported_at']} for x in cases]),
                 hide_index=True, width='stretch')
    if auth.can(user, 'mark_sent'):
        if st.button('Mark all %d as sent' % len(cases)):
            for x in cases:
                db.mark_sent(x['id'], user['username'])
            st.rerun()
