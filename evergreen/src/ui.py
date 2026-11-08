"""Shared Streamlit helpers: cached loading, one Plotly look, the standing honesty labels, and the customer picker."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import auth  # noqa: E402
from src import data as D  # noqa: E402
from src import serving as S  # noqa: E402
from src import threshold as T  # noqa: E402

GREEN, DARK, AMBER, RED, BLUE, GREY = '#1b7f5c', '#12493a', '#d98c1f', '#c0392b', '#2f6fa3', '#8a9a93'
COLORWAY = [GREEN, BLUE, AMBER, RED, '#7d5ba6', GREY]
pio.templates['evergreen'] = go.layout.Template(layout=go.Layout(
    colorway=COLORWAY, font=dict(family='Inter, Segoe UI, sans-serif', size=13, color='#1d2b25'), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
    margin=dict(l=40, r=20, t=50, b=40), xaxis=dict(gridcolor='#e3ebe7', zerolinecolor='#cfdad4'), yaxis=dict(gridcolor='#e3ebe7', zerolinecolor='#cfdad4'),
    legend=dict(orientation='h', yanchor='bottom', y=1.02, x=0)))
pio.templates.default = 'evergreen'

DECISION_STYLE = {'recommend': ('success', 'Recommend'), 'recommend_pending_approval': ('warning', 'Recommend: pending human approval'),
                  'no_action': ('info', 'No action'), 'escalate_to_human': ('error', 'Escalate to a human')}
RULE_TEXT = {'vulnerable': 'Account flagged vulnerable', 'open_escalation': 'Open escalation on the account', 'unknown_vulnerable': 'Vulnerable flag unknown',
             'unknown_escalated': 'Escalation flag unknown', 'contact_cap_unset': 'Contact cap not set in policy', 'contacts_unknown': 'Contacts this month unknown',
             'contact_cap': 'Monthly contact cap reached', 'discount_cap_unset': 'Discount cap not set in policy', 'discount_unknown': 'Discount used this quarter unknown',
             'discount_cap': 'Quarterly discount cap exceeded'}


@st.cache_resource(show_spinner='Loading model...')
def bundle() -> dict:
    return S.load_bundle()


@st.cache_data
def customers() -> pd.DataFrame:
    return D.load_clean().set_index('customerID')


@st.cache_data
def test_ids() -> list:
    return list(D.split()[1]['customerID'])


@st.cache_data
def test_predictions() -> pd.DataFrame:
    return pd.read_csv(S.MODELS / 'test_predictions.csv')


@st.cache_data(show_spinner=False)
def all_scores() -> pd.Series:
    """Calibrated probability for every customer (training rows included, so optimistic)."""
    return S.predict(customers()[D.INPUTS], bundle())['churn_probability']


def setup(title: str, icon: str = '🌲', perm: str = 'analyst') -> dict:
    """First call on every page: page config, the model, the sidebar banner. Returns the cached bundle."""
    try:                                      # app.py (the router) already set it; a page run on its own (tests) needs it
        st.set_page_config(page_title='Evergreen: %s' % title, page_icon=icon, layout='wide')
    except st.errors.StreamlitAPIException:
        pass
    auth.require(perm)
    try:
        b = bundle()
    except FileNotFoundError as e:
        st.error(str(e))
        st.stop()
    t = b['threshold']
    with st.sidebar:
        st.caption('Decision threshold in force')
        (st.warning if t['is_placeholder'] else st.success)('%.3f (%s)' % (t['threshold'], 'PLACEHOLDER' if t['is_placeholder'] else 'cost-based'))
        st.caption('Twin = model sensitivity, not causal. Uplift = SIMULATED. No business value is claimed.')
    st.title(title)
    return b


def threshold_banner(b: dict):
    t = b['threshold']
    if t['is_placeholder']:
        st.warning('**Threshold %.3f is a PLACEHOLDER** (F1-optimal on out-of-fold predictions), not a business decision. %s' % (t['threshold'], 'Set the three numbers in config/business.yaml.'))
    else:
        st.success('Threshold %.3f = offer_cost / (success_rate x customer_value) from config/business.yaml.' % t['threshold'])


def twin_banner():
    st.error('**MODEL SENSITIVITY, NOT A CAUSAL ESTIMATE.** This shows how the model\'s score moves when inputs are edited, not what an action would achieve.')


def simulated_banner():
    st.error('**SIMULATED.** The Telco data has no treatment column. Treatment and its effect are generated from assumptions in config/uplift_sim.yaml. '
             'Advisory only; these are not findings about real retention offers.')


def crm_setup(title: str, icon: str = '🌲', perm: str = 'lookup') -> dict:
    """First call on CRM pages: no model jargon, just the page title."""
    try:
        st.set_page_config(page_title='Evergreen: %s' % title, page_icon=icon, layout='wide')
    except st.errors.StreamlitAPIException:
        pass
    auth.require(perm)
    try:
        b = bundle()
    except FileNotFoundError as e:
        st.error('The system is not set up yet. %s' % e)
        st.stop()
    st.title(title)
    return b


def pick_customer_simple(key: str) -> tuple[str, dict]:
    """Plain customer picker for CRM pages."""
    all_c = customers()
    c1, c2 = st.columns([3, 2])
    ids = list(all_c.index)
    if c2.checkbox('Only customers at high risk of leaving', key=key + '_hi'):
        sc = all_scores()
        ids = [i for i in ids if sc[i] >= bundle()['threshold']['threshold']]
    cur = st.session_state.get('_cid')
    cid = c1.selectbox('Customer ID (type to search)', ids, index=ids.index(cur) if cur in ids else 0, key=key + '_id')
    st.session_state['_cid'] = cid
    return cid, S.customer_row(all_c.loc[cid])


def pick_customer(key: str = 'cust', flagged_option: bool = True) -> tuple[str, dict]:
    """Customer selector whose choice is shared across pages."""
    all_c = customers()
    c1, c2 = st.columns([3, 2])
    pool = c2.radio('Pool', ['Held-out test (1,409)', 'All customers'], horizontal=True, key=key + '_pool', help='Training customers were seen by the model, so their scores are optimistic.')
    ids = test_ids() if pool.startswith('Held') else list(all_c.index)
    if flagged_option and c2.checkbox('Only flagged (high-risk) customers', key=key + '_flagged', help='Lower-risk customers get "no action"; flagged ones show offers and guardrails.'):
        sc = all_scores()
        ids = [i for i in ids if sc[i] >= bundle()['threshold']['threshold']]
    cur = st.session_state.get('_cid')
    idx = ids.index(cur) if cur in ids else 0
    cid = c1.selectbox('Customer (type to search by ID)', ids, index=idx, key=key + '_id')
    st.session_state['_cid'] = cid
    return cid, S.customer_row(all_c.loc[cid])


def account_state_inputs(key: str, defaults: dict | None = None) -> dict:
    """Operational account state is NOT in the data. 'unknown' is never assumed safe."""
    d = defaults or {}
    st.caption('Account state is not in the model data. **Unknown is never treated as safe**: it forces human approval.')
    c = st.columns(4)
    tri = ['unknown', 'no', 'yes']

    def tri_val(x):
        return tri[0] if x is None else ('yes' if x else 'no')
    v = c[0].selectbox('Vulnerable', tri, index=tri.index(tri_val(d.get('vulnerable'))), key=key + '_v')
    e = c[1].selectbox('Open escalation', tri, index=tri.index(tri_val(d.get('escalated'))), key=key + '_e')
    n = c[2].number_input('Contacts this month (-1 = unknown)', -1, 50, -1 if d.get('contacts_this_month') is None else int(d['contacts_this_month']), key=key + '_c')
    u = c[3].number_input('Discount % used this quarter (-1 = unknown)', -1.0, 100.0, -1.0 if d.get('discount_pct_used_this_quarter') is None else float(d['discount_pct_used_this_quarter']), key=key + '_d')
    return {'vulnerable': None if v == 'unknown' else v == 'yes', 'escalated': None if e == 'unknown' else e == 'yes',
            'contacts_this_month': None if n < 0 else int(n), 'discount_pct_used_this_quarter': None if u < 0 else float(u)}


def gauge(prob: float, threshold: float, title: str = 'Calibrated churn probability') -> go.Figure:
    fig = go.Figure(go.Indicator(mode='gauge+number', value=100 * prob, number={'suffix': '%', 'valueformat': '.1f'}, title={'text': title},
                                 gauge={'axis': {'range': [0, 100]}, 'bar': {'color': RED if prob >= threshold else GREEN},
                                        'steps': [{'range': [0, 100 * threshold], 'color': '#e5f1ec'}, {'range': [100 * threshold, 100], 'color': '#f6e3e0'}],
                                        'threshold': {'line': {'color': DARK, 'width': 4}, 'thickness': 0.8, 'value': 100 * threshold}}))
    fig.update_layout(height=260, margin=dict(l=20, r=20, t=60, b=10))
    return fig


def decision_card(rec: dict):
    kind, label = DECISION_STYLE[rec['decision']]
    getattr(st, kind)('**Decision: %s**' % label)
    fired = rec.get('fired_by') or []
    if fired:
        st.markdown('**Guardrails that fired:** ' + ' · '.join('`%s`' % RULE_TEXT.get(f, f) for f in fired))
    elif rec['decision'] == 'recommend':
        st.markdown('**Guardrails:** all checks passed with verified account state.')
    for r in rec['reasons']:
        st.write('- ' + r)


def md_table(df: pd.DataFrame, floatfmt: str = '{:.3f}') -> str:
    cols = list(df.columns)
    fmt = lambda v: floatfmt.format(v) if isinstance(v, float) else str(v)
    lines = ['| ' + ' | '.join(map(str, cols)) + ' |', '|' + '|'.join('---' for _ in cols) + '|']
    lines += ['| ' + ' | '.join(fmt(v) for v in row) + ' |' for row in df.itertuples(index=False)]
    return '\n'.join(lines)
