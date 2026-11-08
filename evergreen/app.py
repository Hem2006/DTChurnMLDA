"""EVERGREEN: finds customers likely to leave, suggests one safe offer, and routes messages through approval.   streamlit run app.py

Sign-in required. Five roles (agent, manager, specialist, sender, admin) each see only the pages their role needs; admins can also switch on the analyst tools.
"""
import streamlit as st

from src import auth

st.set_page_config(page_title='Evergreen', page_icon='🌲', layout='wide')

# (file, title, icon, permission needed)
CRM = [('crm/1_Today.py', 'Today', '🌲', None), ('crm/2_Customer.py', 'Customer', '🔎', 'lookup'), ('crm/3_Customer_List.py', 'Customer list', '📋', 'lookup'),
       ('crm/4_Approvals.py', 'Approvals', '✅', 'view_own'), ('crm/5_Escalations.py', 'Escalations', '🆘', 'view_escalations'),
       ('crm/6_Outbox.py', 'Outbox', '📤', 'view_approved'), ('crm/7_Admin.py', 'Admin', '⚙️', 'admin')]
ANALYST = [('home.py', 'Model overview', '📊'), ('pages/1_Customer_Explorer.py', 'Customer Explorer', '🔬'), ('pages/2_What-If_Lab.py', 'What-If Lab', '🧪'),
           ('pages/3_Cost_Threshold_Playground.py', 'Cost Threshold Playground', '🎚️'), ('pages/4_Calibration_Lab.py', 'Calibration Lab', '📐'),
           ('pages/5_Guardrail_Stress-Tester.py', 'Guardrail Stress-Tester', '🛡️'),
           ('pages/7_Fairness_Dashboard.py', 'Fairness Dashboard', '⚖️'), ('pages/8_Uplift_View.py', 'Uplift View', '📈'),
           ('pages/9_Model_Arena_and_Paper_Mode.py', 'Model Arena + Paper Mode', '🏟️'), ('pages/10_Batch_and_Drift.py', 'Batch + Drift', '📦')]

user = auth.current()
if not user:
    auth.login_form()
    st.stop()

allowed = [(f, t, i) for f, t, i, perm in CRM if perm is None or auth.can(user, perm) or (f.endswith('4_Approvals.py') and auth.can(user, 'view_all'))]
nav = {'': [st.Page(f, title=t, icon=i, default=(n == 0)) for n, (f, t, i) in enumerate(allowed)]}

with st.sidebar:
    st.caption('Signed in as **%s** (%s)' % (user['name'], auth.ROLE_LABEL[user['role']]))
    if auth.can(user, 'analyst') and st.toggle('Analyst tools', key='analyst_tools', help='Model quality, calibration, fairness, what-if lab and other technical pages.'):
        nav['Analyst tools'] = [st.Page(f, title=t, icon=i) for f, t, i in ANALYST]
    with st.expander('Change my password'):
        with st.form('pw'):
            old, new = st.text_input('Current password', type='password'), st.text_input('New password (10+ characters)', type='password')
            if st.form_submit_button('Change'):
                try:
                    auth.set_password(user['username'], new, old_password=old)
                    st.success('Password changed.')
                except ValueError as e:
                    st.error(str(e))
    if st.button('Sign out'):
        for k in ('user', 'analyst_tools', '_cid'):
            st.session_state.pop(k, None)
        st.rerun()
st.navigation(nav).run()
