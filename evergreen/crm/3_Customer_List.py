import pandas as pd
import streamlit as st

from src import auth
from src import crm
from src import data as D
from src import ui

b = ui.crm_setup('Customer list', '📋')
user = auth.current()
st.markdown('Upload a file of customers (CSV). You get them ranked by risk of leaving, with a suggested next step for each one at high risk.')

with st.expander('What should the file look like?'):
    st.markdown('One row per customer. It needs the standard customer columns (same layout as the sample). Optional columns that make results better:\n'
                '- `customerID`: how you want customers labelled\n- `first_name`: needed to draft messages\n'
                '- `vulnerable`, `escalated` (yes/no), `contacts_this_month`, `discount_pct_used_this_quarter`: anything missing is treated as *not known*, which means a manager must approve.')
    sample = ui.customers().reset_index().head(5)[['customerID'] + D.INPUTS].assign(first_name='')
    st.download_button('Download a template (5 example rows)', sample.to_csv(index=False).encode('utf-8'), file_name='customer_list_template.csv', mime='text/csv')

f = st.file_uploader('Customer file (CSV)', type='csv', key='cl_file')
use_sample = st.checkbox('Or try it with 150 sample customers', key='cl_sample')
raw = None
if f is not None:
    try:
        raw = pd.read_csv(f)
    except Exception as e:
        st.error('That file could not be read as a CSV: %s' % e)
elif use_sample:
    raw = ui.customers().reset_index().head(150).assign(first_name=lambda d: ['Alex'] * len(d))
    st.caption('Sample customers all use the placeholder first name "Alex".')

if raw is not None:
    limit = 300
    key = (getattr(f, 'name', 'sample'), len(raw))
    if st.session_state.get('cl_key') != key:
        bar = st.progress(0.0, text='Checking customers...')
        try:
            st.session_state['cl_res'] = crm.run_batch(raw, b, limit, progress=lambda x: bar.progress(x, text='Checking customers...'))
            st.session_state['cl_key'] = key
        except ValueError as e:
            bar.empty()
            st.error(str(e))
            st.stop()
        bar.empty()
    res = st.session_state['cl_res']
    rows = res['rows']
    c = st.columns(3)
    c[0].metric('Customers checked', res['n'] - len(res['unreadable']))
    c[1].metric('At high risk of leaving', res['at_risk'])
    c[2].metric('Rows we could not read', len(res['unreadable']))
    if res.get('capped'):
        st.info('Showing the %d highest-risk customers.' % limit)
    if len(res['unreadable']):
        with st.expander('Rows we could not read (%d)' % len(res['unreadable']), expanded=True):
            st.caption('Nothing was dropped silently: each of these needs fixing in the file.')
            st.dataframe(res['unreadable'], hide_index=True, width='stretch')
    if len(rows):
        counts = rows['next step'].value_counts()
        st.subheader('Customers at high risk, most likely to leave first')
        view = st.dataframe(rows.drop(columns=['first name']), hide_index=True, width='stretch',
                            column_config={'chance of leaving': st.column_config.ProgressColumn('chance of leaving', min_value=0, max_value=1, format='percent')})
        st.write(' · '.join('**%d** %s' % (v, k.lower()) for k, v in counts.items()))
        a, bcol = st.columns(2)
        a.download_button('Download this list (CSV)', rows.drop(columns=['first name']).to_csv(index=False).encode('utf-8'), file_name='action_list.csv', mime='text/csv')
        recommended = sum(1 for r, fn in res['records'].values() if r['decision'].startswith('recommend'))
        have_name = sum(1 for r, fn in res['records'].values() if r['decision'].startswith('recommend') and fn)
        st.divider()
        st.markdown('**Send recommended customers for approval:** %d customers have a suggested offer, %d of them have a first name in the file.' % (recommended, have_name))
        if st.button('Draft messages and send for approval', type='primary', disabled=not have_name or not auth.can(user, 'submit')):
            r = crm.queue_batch(res['records'], b, submitted_by=user['username'])
            st.success('%d message(s) sent for approval.' % r['queued'])
            if r['no_first_name']:
                st.warning('%d customer(s) skipped: no first name in the file.' % r['no_first_name'])
            if r['already_open']:
                st.warning('%d customer(s) skipped: they already have a message waiting.' % r['already_open'])
            if r['failed']:
                st.error('%d message(s) could not be created.' % r['failed'])
        if not auth.can(user, 'submit'):
            st.caption('Your role can check lists but cannot send messages for approval.')
        if not have_name and recommended:
            st.caption('Add a `first_name` column to the file to draft messages here.')
    else:
        st.success('No customer in this file is at high risk of leaving.')
    st.caption('Suggested offers show what the model reacts to; they do not prove an offer will keep a customer. Nothing is sent from here.')
