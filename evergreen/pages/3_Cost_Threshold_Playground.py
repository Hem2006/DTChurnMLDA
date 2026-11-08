import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import threshold as T
from src import ui

b = ui.setup('Cost Threshold Playground')
t_in = b['threshold']
ui.threshold_banner(b)
st.markdown('Flag a customer when `p x success_rate x customer_value > offer_cost`, i.e. when **p > t\\* = offer_cost / (success_rate x customer_value)**. '
            'Move the sliders to see how the threshold, the confusion matrix and the profit curve respond.')

biz = b['business']
configured = all(v is not None for v in biz.values())
src = st.radio('Numbers to use', ['Playground (type your own)', 'Configured business numbers'] if configured else ['Playground (type your own)'], horizontal=True)
if src.startswith('Config'):
    c, v, s = biz['offer_cost'], biz['customer_value'], biz['success_rate']
    st.info('Using config/business.yaml: offer cost %g, customer value %g, success rate %g.' % (c, v, s))
else:
    st.caption('**Illustrative playground values: not your business numbers.** Nothing here is saved or shown anywhere else.')
    k = st.columns(3)
    c = k[0].slider('Offer cost', 1.0, 200.0, 20.0, 1.0)
    v = k[1].slider('Customer value (kept if saved)', 50.0, 3000.0, 600.0, 10.0)
    s = k[2].slider('Success rate', 0.05, 1.0, 0.3, 0.01)

try:
    t_star = T.cost_threshold(c, v, s)
except ValueError as e:
    st.error(str(e))
    st.stop()
tp = ui.test_predictions()
y, p = tp['y'].values, tp['p_served'].values

m = st.columns(4)
m[0].metric('t* (these numbers)', '%.3f' % t_star, help='Values at or above 1 mean no customer is worth flagging.')
m[1].metric('Threshold in force', '%.3f' % t_in['threshold'], help=t_in['source'])
m[2].metric('Source in force', 'placeholder' if t_in['is_placeholder'] else 'cost-based')
use = m[3].selectbox('Evaluate at', ['t* (playground)', 'threshold in force'])
t = min(t_star, 1.0) if use.startswith('t*') else t_in['threshold']
if t_star >= 1:
    st.warning('t* >= 1: with these numbers an offer never pays for itself, so nobody is flagged.')

flag = p >= t
tn, fp, fn, tpos = int(((~flag) & (y == 0)).sum()), int((flag & (y == 0)).sum()), int(((~flag) & (y == 1)).sum()), int((flag & (y == 1)).sum())
l, r = st.columns(2)
with l:
    st.subheader('Confusion matrix at %.3f' % t)
    fig = go.Figure(go.Heatmap(z=[[tn, fp], [fn, tpos]], x=['not flagged', 'flagged'], y=['stayed', 'churned'], text=[[tn, fp], [fn, tpos]], texttemplate='%{text}',
                               colorscale=[[0, '#f1f6f3'], [1, ui.GREEN]], showscale=False))
    fig.update_layout(height=300, yaxis_autorange='reversed')
    st.plotly_chart(fig, width='stretch')
    prec = tpos / max(1, tpos + fp)
    rec = tpos / max(1, tpos + fn)
    st.write('Flag rate **%.1f%%**  ·  precision **%.2f**  ·  recall **%.2f**' % (100 * flag.mean(), prec, rec))
with r:
    st.subheader('Illustrative profit curve')
    grid = np.linspace(0, 1, 101)
    curve = pd.DataFrame(T.profit_curve(y, p, c, v, s, grid))
    fig = go.Figure(go.Scatter(x=curve.threshold, y=curve.profit, mode='lines', line=dict(color=ui.GREEN)))
    fig.add_vline(x=min(t_star, 1), line_dash='dash', line_color=ui.DARK, annotation_text='t*')
    fig.add_vline(x=t_in['threshold'], line_dash='dot', line_color=ui.AMBER, annotation_text='in force', annotation_position='bottom right')
    fig.update_layout(height=300, xaxis_title='threshold', yaxis_title='profit on the %d test customers' % len(y))
    st.plotly_chart(fig, width='stretch')
    best = curve.loc[curve.profit.idxmax()]
    st.caption('profit(t) = sum over flagged of (success_rate x customer_value x churned - offer_cost). Computed with the numbers above, which you chose; it is not a business claim. '
               'Best threshold on this grid: %.2f (profit %.0f); t* = %.3f.' % (best.threshold, best.profit, t_star))
