import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import data as D
from src import twin as TW
from src import ui

b = ui.setup('What-If Lab')
ui.twin_banner()
cid, cust = ui.pick_customer('wi')
lv = b['meta']['levels']
base = TW.CustomerTwin(cust, b['meta'])
no_net = cust['InternetService'] == 'No'

st.subheader('Edit the levers')
st.caption('Editable: Contract, add-on services, PaymentMethod, PaperlessBilling and a 0-30%% discount. **Locked:** tenure, demographics, gender, TotalCharges, and MonthlyCharges (only via the discount). '
           'This customer has %s internet service.' % ('NO' if no_net else cust['InternetService']))
c = st.columns(4)
changes = {}
for col, widget, key in ((c[0], 'Contract', 'wi_contract'), (c[1], 'PaymentMethod', 'wi_pay'), (c[2], 'PaperlessBilling', 'wi_paper')):
    v = col.selectbox(widget, lv[widget], index=lv[widget].index(cust[widget]), key=key + cid)
    if v != cust[widget]:
        changes[widget] = v
disc = c[3].slider('Discount %', 0, TW.MAX_DISCOUNT_PCT, 0, key='wi_disc' + cid)
if disc:
    changes['discount_pct'] = disc
a = st.columns(6)
for col, name in zip(a, D.ADDONS):
    opts = ['No', 'Yes']
    v = col.selectbox(name, opts, index=opts.index(cust[name]) if cust[name] in opts else 0, key='wi_' + name + cid, disabled=no_net,
                      help='No internet service: add-ons cannot be changed' if no_net else None)
    if not no_net and v != cust[name]:
        changes[name] = v

try:
    r = TW.simulate(base, b, changes, name='custom')
except TW.TwinError as e:
    st.error(str(e))
    st.stop()
m = st.columns(4)
m[0].metric('Risk now', '%.1f%%' % (100 * r['baseline_risk']))
m[1].metric('Risk after edit', '%.1f%%' % (100 * r['risk_after']), delta='%.1f pts' % (-100 * r['delta']), delta_color='inverse')
m[2].metric('Delta = p(x) - p(x\')', '%+.3f' % r['delta'])
m[3].metric('Logistic reference delta', '%+.3f' % r['reference']['delta'])
fig = go.Figure(go.Bar(x=['Now', 'After edit'], y=[100 * r['baseline_risk'], 100 * r['risk_after']], marker_color=[ui.GREY, ui.GREEN], text=['%.1f%%' % (100 * r['baseline_risk']), '%.1f%%' % (100 * r['risk_after'])]))
fig.add_hline(y=100 * b['threshold']['threshold'], line_dash='dash', line_color=ui.DARK, annotation_text='threshold (%s)' % ('placeholder' if b['threshold']['is_placeholder'] else 'cost-based'))
fig.update_layout(height=300, yaxis_title='churn probability (%)', yaxis_range=[0, 100])
st.plotly_chart(fig, width='stretch')
if r['confidence'] == 'low':
    st.warning('**LOW CONFIDENCE:** %s' % r['confidence_reason'])
else:
    st.caption('Confidence ok: ' + r['confidence_reason'])
for w in r['warnings']:
    st.warning(w)

st.subheader('Scenario library, ranked by model-sensitivity delta')
run = TW.run_scenarios(base, b)
rows = [{'scenario': x['name'], 'changes': str(x['changes']), 'risk after': x['risk_after'], 'delta': x['delta'], 'logistic delta': x['reference']['delta'],
         'confidence': x['confidence'], 'flagged after': x['flagged_after']} for x in run['results']]
st.dataframe(pd.DataFrame(rows).style.format({'risk after': '{:.3f}', 'delta': '{:+.3f}', 'logistic delta': '{:+.3f}'}), hide_index=True, width='stretch')
for x in run['results']:
    if x['confidence'] == 'low':
        st.warning('%s: low confidence. %s' % (x['name'], x['confidence_reason']))
if run['not_applicable']:
    with st.expander('Not applicable to this customer'):
        st.table(pd.DataFrame(run['not_applicable']))
st.caption(TW.INTERPRETATION.capitalize() + '. A negative or tiny delta means the model is insensitive to that edit; a large one may reflect selection, not an effect the business could produce.')
