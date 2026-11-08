"""Evergreen churn dashboard.  Run:  streamlit run dashboard/app.py

Three standing labels (CLAUDE.md): the twin is MODEL SENSITIVITY, not causal; uplift is SIMULATED; the threshold is a PLACEHOLDER
until config.yaml has offer cost, customer value and success rate.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import churn_serving as cs  # noqa: E402
from app import dash_logic as dl  # noqa: E402
from app import fairness as fa  # noqa: E402
from app import message as msg  # noqa: E402
from app import pipeline as pl  # noqa: E402
from app import recommend as rec  # noqa: E402
from app import twin as tw  # noqa: E402
from app import uplift as up  # noqa: E402

st.set_page_config(page_title='Evergreen churn', layout='wide')


@st.cache_resource
def bundle():
    return dl.load_bundle(ROOT)


@st.cache_data
def customers():
    return dl.load_customers(ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv')


@st.cache_data
def test_ids():
    return dl.test_split_ids(ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv')


@st.cache_data
def fairness_result():
    X, y = fa.load_test_split(ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv')
    return fa.audit(bundle(), X, y)


B, ALL = bundle(), customers()
META = B['meta']
threshold, source, placeholder = cs.get_threshold(META, B['config'])

st.sidebar.title('Evergreen churn')
page = st.sidebar.radio('Page', ['Overview', 'Build a customer', 'Score a file', 'Customer lookup', 'Digital-twin lab', 'Recommendation', 'Message', 'Review queue', 'Fairness'])
if placeholder:
    st.sidebar.warning('Threshold %.3f is a PLACEHOLDER (F1-optimal). Set offer_cost, customer_value and success_rate in config.yaml for a cost-based one.' % threshold)
else:
    st.sidebar.success('Threshold %.3f from offer_cost / (success_rate x customer_value).' % threshold)
st.sidebar.caption('Twin = model sensitivity, not causal. Uplift = SIMULATED.')


@st.cache_data
def flagged_ids(pool_all: bool):
    ids = list(ALL.index) if pool_all else test_ids()
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        pred = cs.predict(ALL.loc[ids], B)
    return list(pred.index[pred['flagged'].values])


def pick_customer(key):
    pool = st.radio('Customers', ['Held-out test split (1,409)', 'All customers (includes training rows)'], key=key + '_pool', horizontal=True)
    ids = test_ids() if pool.startswith('Held') else list(ALL.index)
    if st.checkbox('Show only flagged (high-risk) customers', key=key + '_flagged', help='Low-risk customers get "no action"; pick a flagged one to see offers.'):
        ids = flagged_ids(not pool.startswith('Held'))
    cid = st.selectbox('customerID', ids, key=key + '_id')
    return cid, dl.to_model_row(ALL.loc[cid], META)


def account_state_inputs(key):
    st.caption('Operational state is NOT in the model data. "unknown" is never assumed; it forces human approval.')
    c = st.columns(4)
    tri = ['unknown', 'no', 'yes']
    v = c[0].selectbox('Vulnerable', tri, key=key + 'v')
    e = c[1].selectbox('Escalated', tri, key=key + 'e')
    contacts = c[2].number_input('Contacts this month', min_value=-1, value=-1, help='-1 = unknown', key=key + 'c')
    disc = c[3].number_input('Discount % used this quarter', min_value=-1.0, max_value=100.0, value=-1.0, help='-1 = unknown', key=key + 'd')
    return {'vulnerable': None if v == 'unknown' else v == 'yes', 'escalated': None if e == 'unknown' else e == 'yes',
            'contacts_this_month': None if contacts < 0 else int(contacts), 'discount_pct_used_this_quarter': None if disc < 0 else float(disc)}


if page == 'Overview':
    st.title('Overview')
    pool = st.radio('Customers', ['Held-out test split (1,409)', 'All customers (includes training rows: optimistic)'], horizontal=True)
    df = ALL.loc[test_ids()] if pool.startswith('Held') else ALL
    o = dl.overview(B, df)
    c = st.columns(4)
    c[0].metric('Customers', '{:,}'.format(o['n']))
    c[1].metric('Flagged', '{:,}'.format(o['n_flagged']))
    c[2].metric('Share flagged', '{:.1%}'.format(o['share_flagged']))
    if o['revenue_at_risk'] is None:
        c[3].metric('Revenue at risk', 'n/a')
        st.info(o['revenue_at_risk_reason'])
    else:
        c[3].metric('Revenue at risk', '{:,.0f}'.format(o['revenue_at_risk']))
        st.caption(o['revenue_at_risk_definition'])
    st.caption('Flag threshold %.3f (%s%s).' % (o['threshold'], o['threshold_source'], ', PLACEHOLDER' if o['threshold_is_placeholder'] else ''))
    st.subheader('Flagged share by contract')
    st.bar_chart(o['by_contract']['flagged_share'])
    st.subheader('Distribution of calibrated churn probability')
    st.bar_chart(pd.cut(o['probabilities'], bins=[i / 20 for i in range(21)]).value_counts().sort_index().rename(lambda x: '%.2f' % x.right))

elif page == 'Build a customer':
    st.title('Build a customer')
    st.caption('Describe any customer, real or hypothetical, and see the score, the reasons and what the system would do. Nothing is saved.')
    start = st.selectbox("Start from (optional: copies a real customer's answers into the form)", ['(typical defaults)'] + list(ALL.index[:200]))
    base = dl.to_model_row(ALL.loc[start], META) if start != '(typical defaults)' else {
        'SeniorCitizen': 0, 'Partner': 'No', 'Dependents': 'No', 'tenure': 12, 'PhoneService': 'Yes', 'MultipleLines': 'No', 'InternetService': 'Fiber optic',
        'OnlineSecurity': 'No', 'OnlineBackup': 'No', 'DeviceProtection': 'No', 'TechSupport': 'No', 'StreamingTV': 'No', 'StreamingMovies': 'No',
        'Contract': 'Month-to-month', 'PaperlessBilling': 'Yes', 'PaymentMethod': 'Electronic check', 'MonthlyCharges': 75.0}
    lv, k = META['categorical_levels'], 'bc_' + start + '_'

    def sel(container, col, label, key, disabled=False, options=None):
        opts = options or lv[col]
        return container.selectbox(label, opts, index=opts.index(base[col]) if base[col] in opts else 0, key=k + key, disabled=disabled)

    a = st.columns(4)
    cust = {'SeniorCitizen': int(a[0].checkbox('Senior citizen', value=bool(base['SeniorCitizen']), key=k + 'sc')),
            'Partner': sel(a[1], 'Partner', 'Partner', 'p'),
            'Dependents': sel(a[2], 'Dependents', 'Dependents', 'd'),
            'tenure': a[3].slider('Tenure (months)', 0, 72, int(base['tenure']), key=k + 't')}
    b = st.columns(4)
    cust['Contract'] = sel(b[0], 'Contract', 'Contract', 'ct')
    cust['PaymentMethod'] = sel(b[1], 'PaymentMethod', 'Payment method', 'pm')
    cust['PaperlessBilling'] = sel(b[2], 'PaperlessBilling', 'Paperless billing', 'pb')
    cust['MonthlyCharges'] = b[3].number_input('Monthly charges', 0.0, 200.0, float(base['MonthlyCharges']), step=1.0, key=k + 'mc')
    c_ = st.columns(3)
    cust['PhoneService'] = sel(c_[0], 'PhoneService', 'Phone service', 'ph')
    cust['MultipleLines'] = sel(c_[1], 'MultipleLines', 'Multiple lines', 'ml', disabled=cust['PhoneService'] == 'No',
                                options=[x for x in lv['MultipleLines'] if x != dl.NO_PHONE])
    cust['InternetService'] = sel(c_[2], 'InternetService', 'Internet service', 'is')
    no_net = cust['InternetService'] == 'No'
    for col, w in zip(dl.ADDONS, st.columns(6)):
        cust[col] = sel(w, col, col, col, disabled=no_net, options=['No', 'Yes'])
    cust = dl.make_coherent(cust)
    if no_net or cust['PhoneService'] == 'No':
        st.caption('Fields that do not apply (no internet / no phone) are set automatically.')
    v = dl.customer_view(B, cust)
    p = v['prediction']
    m = st.columns(3)
    m[0].metric('Calibrated churn probability', '{:.1%}'.format(p['churn_probability']))
    m[1].metric('Flagged', 'yes' if p['flagged'] else 'no')
    m[2].metric('Threshold', '{:.3f}{}'.format(p['threshold'], ' (placeholder)' if p['threshold_is_placeholder'] else ''))
    st.progress(min(1.0, float(p['churn_probability'])))
    st.subheader('What drives this score')
    st.bar_chart(v['contributions'])
    st.caption('Positive = pushes toward churn. Log-odds of the base XGBoost score, averaged over the 5 calibration folds.')
    try:
        run = tw.run_scenarios(cust, B)
        st.subheader('What the model says would move it (model sensitivity, NOT causal)')
        st.dataframe(dl.scenario_table(run).drop(columns=['interpretation']), hide_index=True)
        state = account_state_inputs('bc')
        r = rec.recommend(cust, B, state)
        badge = {'recommend': st.success, 'recommend_pending_approval': st.warning, 'no_action': st.info, 'escalate_to_human': st.error}[r['decision']]
        badge('Decision: %s' % r['decision'].replace('_', ' '))
        for x in r['reasons']:
            st.write('- ' + x)
    except (tw.TwinError, ValueError) as e:
        st.error(str(e))

elif page == 'Score a file':
    st.title('Score a file')
    st.caption('Upload a CSV with the 17 model columns (the Telco layout; an optional customerID column becomes the row label). '
               'Rows that fail validation are listed with the reason, never silently dropped.')
    f = st.file_uploader('Customer CSV', type='csv', key='sf_file')
    use_sample = st.checkbox('...or try 300 customers from the sample data', value=False, key='sf_sample')
    frame = None
    if f is not None:
        frame = pd.read_csv(f)
    elif use_sample:
        frame = ALL.iloc[:300].reset_index()
    if frame is not None:
        try:
            res = dl.score_file(B, frame)
        except ValueError as e:
            st.error(str(e))
        else:
            sc, rej = res['scored'], res['rejected']
            m = st.columns(3)
            m[0].metric('Scored', '{:,}'.format(len(sc)))
            m[1].metric('Rejected', '{:,}'.format(len(rej)))
            m[2].metric('Flagged at served threshold', '{:,}'.format(int(sc['flagged'].sum())) if len(sc) else '0')
            if len(sc):
                st.download_button('Download all scored rows (CSV)', sc.to_csv().encode('utf-8'), file_name='scored_customers.csv', mime='text/csv')
                t = st.slider('Explore a different cut-off (exploration only: the served threshold is %.3f)' % threshold, 0.05, 0.95, min(0.95, max(0.05, round(threshold, 2))), 0.01)
                above = sc[sc['churn_probability'] >= t].sort_values('churn_probability', ascending=False)
                st.write('%d of %d customers (%.1f%%) at or above %.2f.' % (len(above), len(sc), 100 * len(above) / len(sc), t))
                st.dataframe(above.head(500))
            if len(rej):
                st.subheader('Rejected rows')
                st.dataframe(rej, hide_index=True)

elif page == 'Customer lookup':
    st.title('Customer lookup')
    cid, cust = pick_customer('lk')
    v = dl.customer_view(B, cust)
    p = v['prediction']
    c = st.columns(3)
    c[0].metric('Calibrated churn probability', '{:.1%}'.format(p['churn_probability']))
    c[1].metric('Flagged', 'yes' if p['flagged'] else 'no')
    c[2].metric('Threshold', '{:.3f}{}'.format(p['threshold'], ' (placeholder)' if p['threshold_is_placeholder'] else ''))
    st.subheader('Why (SHAP reasons, flagged customers only)')
    if v['reasons'] is None:
        st.write('Not flagged, so no retention reasons are produced.')
    else:
        st.table(pd.DataFrame(v['reasons']).rename(columns={'log_odds_push': 'push toward churn (log-odds)'}))
    with st.expander('All contributions (log-odds of the base XGBoost score, averaged over the 5 calibration folds)'):
        st.bar_chart(v['contributions'])
    with st.expander('Customer inputs'):
        st.json(cust)

elif page == 'Digital-twin lab':
    st.title('Digital-twin what-if lab')
    st.error('MODEL SENSITIVITY, NOT A CAUSAL ESTIMATE. This shows how the model\'s score moves when inputs are edited, not what an action would achieve.')
    cid, cust = pick_customer('tw')
    run = tw.run_scenarios(cust, B)
    st.metric('Baseline risk', '{:.1%}'.format(run['baseline_risk']))
    t = dl.scenario_table(run)
    st.subheader('Scenario comparison (ranked by %s)' % run['ranked_by'])
    st.dataframe(t.drop(columns=['interpretation']), hide_index=True)
    if B['config'].get('twin_effect_realization') is None:
        st.caption('twin_effect_realization is not set: only raw risk drops are shown.')
    for r in run['results']:
        if r['warnings']:
            st.warning('%s: %s' % (r['name'], ' '.join(r['warnings'])))
        if r['confidence'] == 'low':
            st.warning('%s: LOW CONFIDENCE. %s' % (r['name'], r['confidence_reason']))
    if run['results'] and run['results'][0]['expected_net_value'] is None:
        st.info(run['results'][0]['expected_net_value_reason'])
    if run['not_applicable']:
        st.subheader('Not applicable')
        st.table(pd.DataFrame(run['not_applicable'])[['name', 'reason']])
    st.subheader('Custom what-if')
    c = st.columns(4)
    ch = {}
    contract = c[0].selectbox('Contract', META['categorical_levels']['Contract'], index=META['categorical_levels']['Contract'].index(cust['Contract']))
    tech = c[1].selectbox('TechSupport', META['categorical_levels']['TechSupport'], index=META['categorical_levels']['TechSupport'].index(cust['TechSupport']))
    pm = c[2].selectbox('PaymentMethod', META['categorical_levels']['PaymentMethod'], index=META['categorical_levels']['PaymentMethod'].index(cust['PaymentMethod']))
    disc = c[3].slider('discount_pct', 0, 30, 0)
    for k, v, cur in (('Contract', contract, cust['Contract']), ('TechSupport', tech, cust['TechSupport']), ('PaymentMethod', pm, cust['PaymentMethod'])):
        if v != cur:
            ch[k] = v
    if disc:
        ch['discount_pct'] = disc
    try:
        r = tw.simulate(cust, B, ch)
        st.write('Risk %.1f%% -> %.1f%% (drop %.3f) | confidence: %s | %s' % (100 * r['baseline_risk'], 100 * r['risk_after'], r['risk_drop'], r['confidence'], r['interpretation']))
        for w in r['warnings']:
            st.warning(w)
        if r['confidence'] == 'low':
            st.warning(r['confidence_reason'])
    except tw.TwinError as e:
        st.error(str(e))

elif page == 'Recommendation':
    st.title('Recommendation')
    cid, cust = pick_customer('rc')
    state = account_state_inputs('rc')
    r = rec.recommend(cust, B, state)
    badge = {'recommend': st.success, 'recommend_pending_approval': st.warning, 'no_action': st.info, 'escalate_to_human': st.error}[r['decision']]
    badge('Decision: %s' % r['decision'].replace('_', ' '))
    if r['action']:
        a = r['action']
        st.subheader('Action: %s' % a['name'])
        st.json({'changes': a['changes'], 'guardrails': a['guardrails'], 'confidence': a['confidence']})
    st.subheader('Reasons')
    for x in r['reasons']:
        st.write('- ' + x)
    if r['simulated_uplift']:
        u = r['simulated_uplift']
        st.warning('SIMULATED uplift: %+.3f (control churn %.3f vs treated %.3f). %s' % (u['simulated_uplift'], u['simulated_p_churn_control'],
                                                                                         u['simulated_p_churn_treated'], u['note']))
    elif B['uplift'] is None:
        st.caption('SIMULATED uplift model not built (run: python -m app.uplift).')
    else:
        st.caption('SIMULATED uplift is only calculated for flagged customers (and not for escalated accounts).')
    if r['candidates']:
        st.subheader('Candidates (model sensitivity, not causal)')
        st.dataframe(pd.DataFrame([{'scenario': c['name'], 'risk drop': c['risk_drop'], 'expected net value': c['expected_net_value'],
                                    'worthwhile': c['worthwhile'], 'guardrails': c['guardrails']['status'], 'confidence': c['confidence']} for c in r['candidates']]),
                     hide_index=True)

elif page == 'Message':
    st.title('Message preview')
    cid, cust = pick_customer('ms')
    state = account_state_inputs('ms')
    first = st.text_input('Customer first name', value='')
    r = rec.recommend(cust, B, state)
    if r['decision'] not in ('recommend', 'recommend_pending_approval'):
        st.info('No message: decision is "%s". %s' % (r['decision'], ' '.join(r['reasons'][:2])))
    elif not first:
        st.info('Enter the customer\'s first name (it is not in the model data).')
    else:
        try:
            offer = msg.offer_from_action(r['action'])
            d = msg.draft_message(first, offer, r['shap_reasons'], B['guardrails'])
            st.caption('Offer (fixed, from the recommendation; the writer cannot change it): **%s**. Draft source: %s.' % (offer['description'], d['source']))
            if d['rejected_reason']:
                st.warning('The model draft was rejected: %s' % d['rejected_reason'])
            text = st.text_area('Message (editable)', value=d['message'], height=160)
            problems = msg.validate_message(text, offer, B['guardrails'])
            if text and first.lower() not in text.lower():
                problems.append('does not address the customer by first name')
            if problems:
                st.error('This text breaks the rules and cannot be saved: ' + '; '.join(problems))
            else:
                st.success('Passes the offer, number and pressure-language checks.')
            approver = st.text_input('Approved by (required when the decision is pending approval)')
            can_save = not problems and (r['decision'] == 'recommend' or approver)
            if st.button('Save to message log', disabled=not can_save):
                lid = msg.log_message({'first_name': msg.validate_first_name(first), 'offer': offer, 'shap_reasons': r['shap_reasons'], 'message': text,
                                       'source': d['source'] if text == d['message'] else 'human_edited', 'model': d['model'],
                                       'decision': r['decision'], 'guardrail_status': r['action']['guardrails']['status'], 'approved_by': approver or None})
                st.success('Logged as #%d. Nothing is sent from this dashboard.' % lid)
        except ValueError as e:
            st.error(str(e))

elif page == 'Review queue':
    st.title('Review queue')
    st.caption('Customers go in as a file (or through the API / `python -m app.pipeline run`), come out as cases. Anything the guardrails mark '
               'needs-approval waits here for a named person. Nothing is sent: ready messages are exported to a file for the CRM team.')
    counts = pl.summary()
    cols = st.columns(5)
    for col, s_ in zip(cols, ['pending_approval', 'needs_first_name', 'ready', 'exported', 'escalated']):
        col.metric(s_.replace('_', ' '), counts.get(s_, 0))

    with st.expander('Add customers from a CSV file'):
        st.caption('Column names are mapped by pipeline.yaml (default: the Telco layout). Flagged customers take about a second each.')
        up_file = st.file_uploader('Customer CSV', type='csv')
        if up_file is not None and st.button('Score and queue'):
            try:
                recs = pl.read_csv_source(up_file, pl.load_mapping(), META)
                with st.spinner('Scoring %d customers...' % len(recs)):
                    res = pl.ingest(recs, B, source=up_file.name)
                st.success('Queued: %s. Skipped (already open): %d.' % (res['counts'], len(res['skipped_open_case'])))
            except ValueError as e:
                st.error(str(e))

    status = st.selectbox('Show', ['pending_approval', 'needs_first_name', 'ready', 'exported', 'escalated', 'rejected', 'invalid', 'no_action', 'all'])
    cases = pl.list_cases(None if status == 'all' else status, limit=500)
    if not cases:
        st.info('No cases with status "%s".' % status)
    else:
        st.dataframe(pd.DataFrame([{'case': c['id'], 'customer': c['customer_ref'], 'status': c['status'], 'offer': c['action_name'],
                                    'churn probability': c['probability'], 'guardrails': c['guardrail_status']} for c in cases]), hide_index=True)
        cid = st.selectbox('Open case', [c['id'] for c in cases])
        case = pl.get_case(cid)
        st.subheader('Case #%d: %s' % (cid, case['status'].replace('_', ' ')))
        if case['error']:
            st.error(case['error'])
        for x in case['reasons']:
            st.write('- ' + x)
        if case['approvals_needed']:
            st.warning('Needs human approval: ' + '; '.join(case['approvals_needed']))
        sc = case['scenarios']
        if sc['rows']:
            st.subheader('Twin scenarios (run automatically; ranked by %s)' % sc['ranked_by'])
            st.caption('MODEL SENSITIVITY, NOT A CAUSAL ESTIMATE. Risk now %.1f%%. The chosen scenario is the first permitted/approvable one in this order.' % (100 * sc['baseline_risk']))
            st.dataframe(pd.DataFrame(sc['rows']), hide_index=True)
            for n in sc['not_applicable']:
                st.caption('%s: not applicable (%s)' % (n['scenario'], n['reason']))
        if case['message']:
            st.text_area('Message', value=case['message'], height=140, disabled=True, key='q_msg_%d' % cid)
            st.caption('Source: %s. Passed the offer, number and pressure-language checks.' % case['message_source'])
        with st.expander('History'):
            st.table(pd.DataFrame(case['events']))
        who = st.text_input('Your name', key='q_who')
        name = st.text_input('Customer first name (not in the model data)', value=case['first_name'] or '', key='q_name_%d' % cid)
        c = st.columns(3)
        try:
            if case['status'] == 'pending_approval' and c[0].button('Approve offer', disabled=not who):
                pl.approve(cid, who, B['guardrails'], first_name=name or None)
                st.rerun()
            if case['status'] in ('pending_approval', 'needs_first_name') and name and name != case['first_name'] and c[1].button('Save first name'):
                pl.provide_first_name(cid, name, B['guardrails'])
                st.rerun()
            if case['status'] in ('pending_approval', 'needs_first_name', 'ready'):
                reason = st.text_input('Reason (to reject)', key='q_why_%d' % cid)
                if c[2].button('Reject', disabled=not (who and reason)):
                    pl.reject(cid, who, reason)
                    st.rerun()
        except (ValueError, pl.CaseError) as e:
            st.error(str(e))
    st.divider()
    ready = counts.get('ready', 0)
    if st.button('Export %d ready message(s) to an outbox file' % ready, disabled=not ready):
        out = ROOT / 'outbox' / ('outbox_%s.csv' % pd.Timestamp.now().strftime('%Y%m%d_%H%M%S'))
        rows = pl.export_outbox(out)
        st.success('%d message(s) written to %s and marked exported. Send them from your CRM; customer_ref links back to the contact.' % (len(rows), out))

elif page == 'Fairness':
    st.title('Fairness audit')
    res = fairness_result()
    st.caption('Held-out test split, n = %d, threshold %.3f%s. gender is audited only; it is not a model input.' % (
        res['n'], res['threshold'], ' (PLACEHOLDER)' if res['threshold_is_placeholder'] else ''))
    gaps = res['gaps']
    st.subheader('Gaps (max - min across groups); over %.2f flagged' % fa.GAP_FLAG)
    piv = gaps.pivot(index='attribute', columns='metric', values='gap')[fa.METRICS]
    st.dataframe(piv.style.apply(lambda col: ['background-color: #f8d7da' if v > fa.GAP_FLAG else '' for v in col], axis=0).format('{:.3f}'))
    for _, g in gaps[gaps.flagged].iterrows():
        st.warning('%s %s gap %.3f (%s vs %s; smallest group n = %d)' % (g.attribute, g.metric, g.gap, g.highest, g.lowest, g.min_group_n))
    st.subheader('By group (with sample sizes)')
    st.dataframe(res['by_group'], hide_index=True)
    st.caption('No mitigation applied. Small groups make gaps noisy. See fairness_report.md.')
