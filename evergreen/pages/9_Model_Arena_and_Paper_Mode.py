import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import metrics as M
from src import ui

b = ui.setup('Model Arena + Paper Mode')
m = b['metrics']
fam = m['families']
LABEL = {'logreg': 'Logistic regression (class-weighted)', 'random_forest': 'Random forest', 'xgboost': 'XGBoost (tuned, uncalibrated)', 'lightgbm': 'LightGBM',
         'baseline (Contract + tenure)': 'Baseline: Contract + tenure'}
arena, paper = st.tabs(['Model Arena', 'Paper Mode'])

with arena:
    rows = []
    for k, f in fam.items():
        lo, hi = f['test_roc_auc_ci95']
        rows.append({'model': LABEL[k], 'nested-CV AUC (mean)': f.get('nested_cv_auc_mean'), 'nested-CV sd': f.get('nested_cv_auc_sd'), 'test AUC': f['test_roc_auc'],
                     'test CI low': lo, 'test CI high': hi})
    df = pd.DataFrame(rows).sort_values('test AUC', ascending=False)
    fig = go.Figure(go.Scatter(x=df['test AUC'], y=df['model'], mode='markers', marker=dict(size=11, color=ui.GREEN),
                               error_x=dict(type='data', symmetric=False, array=df['test CI high'] - df['test AUC'], arrayminus=df['test AUC'] - df['test CI low'], color=ui.DARK)))
    fig.update_layout(height=360, xaxis_title='held-out test ROC-AUC with 95%% bootstrap CI (%d resamples)' % m['bootstrap_resamples'], yaxis_autorange='reversed')
    st.plotly_chart(fig, width='stretch')
    st.dataframe(df.style.format({c: '{:.4f}' for c in df.columns if c != 'model'}, na_rep='-'), hide_index=True, width='stretch')
    st.caption('Nested CV: 5 outer folds on the training split, hyper-parameters tuned by randomized search in a 3-fold inner loop (grid search for logistic). The test split is used once.')

    lr, xg = fam['logreg'], fam['xgboost']
    d = m['delong_served_vs_logreg']
    lo, hi = m['auc_diff_served_minus_logreg_ci95']
    overlap = lr['test_roc_auc_ci95'][0] <= xg['test_roc_auc'] <= lr['test_roc_auc_ci95'][1]
    within = lo <= 0 <= hi
    st.subheader('Is the complex model really better?')
    c = st.columns(3)
    c[0].metric('AUC difference (served - logistic)', '%+.4f' % d['diff'])
    c[1].metric('DeLong p-value', '%.3f' % d['p_value'], help='Two-sided DeLong test, same test customers, served calibrated XGBoost vs the logistic reference.')
    c[2].metric('Paired-bootstrap 95%% CI of the difference', '[%+.4f, %+.4f]' % (lo, hi))
    nested_gap = xg['nested_cv_auc_mean'] - lr['nested_cv_auc_mean']
    pooled = (xg['nested_cv_auc_sd'] ** 2 + lr['nested_cv_auc_sd'] ** 2) ** 0.5
    if within and d['p_value'] > 0.05:
        st.info('**Logistic regression is within noise of the served model** on the held-out test split: the paired CI includes 0 and DeLong p = %.3f. '
                'In nested CV the gap is %+.4f AUC (fold sd about %.4f).' % (d['p_value'], nested_gap, pooled))
    else:
        msg = ('On the held-out test split the served model scores **%+.4f AUC** above logistic regression, and the difference is detectable '
               '(DeLong p = %.3f; paired-bootstrap CI [%+.4f, %+.4f]).' % (d['diff'], d['p_value'], lo, hi))
        if abs(nested_gap) < pooled:
            msg += (' But in nested CV the gap is only %+.4f AUC, smaller than the fold-to-fold spread (sd about %.4f), so across folds the two are within noise. '
                    'Read it as: a small edge for XGBoost, not a large one.' % (nested_gap, pooled))
        else:
            msg += ' Nested CV agrees: the gap is %+.4f AUC (fold sd about %.4f).' % (nested_gap, pooled)
        st.info(msg)
    st.caption('This sentence is generated from the saved numbers, not hard-coded. Statistical tie is not proof of equivalence; with 1,409 test customers small differences cannot be detected.')
    with st.expander('Per-fold nested-CV AUC'):
        st.dataframe(pd.DataFrame({LABEL[k]: f['nested_cv_folds'] for k, f in fam.items() if 'nested_cv_folds' in f}).style.format('{:.4f}'))
    with st.expander('Chosen hyper-parameters'):
        st.json({LABEL[k]: f['best_params'] for k, f in fam.items() if 'best_params' in f})

with paper:
    st.markdown('The tables below are generated from `models/metrics.json`; re-training regenerates them. Copy the Markdown into the paper.')
    ci = m['test_ci95']
    t1 = pd.DataFrame([{'Item': 'Customers (train / test)', 'Value': '%d / %d' % (m['n_train'], m['n_test'])}, {'Item': 'Test churn rate', 'Value': '%.3f' % m['churn_rate_test']},
                       {'Item': 'Model inputs', 'Value': str(len(m['inputs']))}, {'Item': 'Served model', 'Value': 'XGBoost, 5-fold %s calibration' % m['served']['calibration']},
                       {'Item': 'Threshold', 'Value': '%.3f (%s)' % (m['threshold']['threshold'], m['threshold']['source'])}])
    names = {'roc_auc': 'ROC-AUC', 'pr_auc': 'PR-AUC', 'brier': 'Brier', 'precision': 'Precision', 'recall': 'Recall', 'f1': 'F1', 'accuracy': 'Accuracy',
             'flag_rate': 'Flag rate', 'top_decile_lift': 'Top-decile lift', 'ece': 'ECE (10 equal-width bins)'}
    t2 = pd.DataFrame([{'Metric': names[k], 'Estimate': '%.3f' % m['test'][k], '95% CI': '[%.3f, %.3f]' % tuple(ci[k])} for k in names])
    t3 = pd.DataFrame([{'Model': LABEL[k], 'Nested-CV AUC': '%.4f +/- %.4f' % (f['nested_cv_auc_mean'], f['nested_cv_auc_sd']) if 'nested_cv_auc_mean' in f else '-',
                        'Test AUC': '%.4f' % f['test_roc_auc'], '95% CI': '[%.4f, %.4f]' % tuple(f['test_roc_auc_ci95'])} for k, f in fam.items()])
    t4 = pd.DataFrame([{'Calibration': k, 'Brier': '%.4f' % v['brier'], 'Log loss': '%.4f' % v['log_loss'], 'ECE': '%.4f' % v['ece'], 'Mean prediction': '%.3f' % v['mean_prediction']}
                       for k, v in m['calibration_comparison'].items()] + [{'Calibration': 'base-rate predictor', 'Brier': '%.4f' % m['brier_base_rate_predictor'], 'Log loss': '-', 'ECE': '-', 'Mean prediction': '-'}])
    dl = m['delong_served_vs_logreg']
    t5 = pd.DataFrame([{'Comparison': 'Served vs logistic (DeLong)', 'AUC 1': '%.4f' % dl['auc_1'], 'AUC 2': '%.4f' % dl['auc_2'], 'Diff': '%+.4f' % dl['diff'], 'z': '%.2f' % dl['z'], 'p': '%.3f' % dl['p_value']}])
    tables = [('Table 1. Data and configuration', t1), ('Table 2. Held-out test metrics for the served model (%d-resample bootstrap)' % m['bootstrap_resamples'], t2),
              ('Table 3. Model comparison', t3), ('Table 4. Calibration methods on the test split. ECE: ' + m['ece_binning'], t4), ('Table 5. DeLong test of AUC', t5)]
    md = []
    for title, t in tables:
        st.markdown('**%s**' % title)
        st.dataframe(t, hide_index=True, width='stretch')
        md.append('**%s**\n\n%s\n' % (title, ui.md_table(t)))
    full = '\n'.join(md)
    st.download_button('Download all tables (Markdown)', full, file_name='evergreen_results_tables.md', mime='text/markdown')
    with st.expander('Markdown source'):
        st.code(full, language='markdown')
    st.download_button('Download metrics.json', json.dumps(m, indent=2), file_name='metrics.json', mime='application/json')
