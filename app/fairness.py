"""Fairness audit on the held-out test split (Fairlearn MetricFrame).

Audited attributes: SeniorCitizen, Partner, Dependents and gender. gender is audited only; it is NOT a model input.
A gap is max - min of a metric across the groups of one attribute. Gaps above GAP_FLAG (0.10) are highlighted.
Small groups make gaps noisy, so every row shows its sample size and groups under MIN_N are marked.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from fairlearn.metrics import MetricFrame, selection_rate
from sklearn.metrics import precision_score, recall_score
from sklearn.model_selection import train_test_split

import churn_serving as cs

ATTRIBUTES = ['SeniorCitizen', 'Partner', 'Dependents', 'gender']
METRICS = ['recall', 'precision', 'flag_rate']
GAP_FLAG = 0.10
MIN_N = 100
ROOT = Path(__file__).resolve().parent.parent


def load_test_split(csv=None):
    """The held-out 20% test split, rebuilt exactly as train.py does (seed 42, stratified). Includes unused columns such as gender."""
    df = pd.read_csv(csv or ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv').drop(columns=['customerID'])
    df['TotalCharges'] = pd.to_numeric(df['TotalCharges'], errors='coerce')
    y = df['Churn'].map({'Yes': 1, 'No': 0})
    _, X_test, _, y_test = train_test_split(df.drop(columns=['Churn']), y, test_size=0.2, random_state=42, stratify=y)
    return X_test, y_test


def audit(bundle, X_test, y_test, attributes=ATTRIBUTES) -> dict:
    """Returns {'by_group': DataFrame, 'gaps': DataFrame, 'threshold', 'threshold_source', 'n'}."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        pred = cs.predict(X_test, bundle)
    y_true, y_pred = y_test.loc[pred.index].values, pred['flagged'].astype(int).values
    groups, gaps = [], []
    for attr in attributes:
        mf = MetricFrame(metrics={'recall': recall_score,
                                  'precision': lambda yt, yp: precision_score(yt, yp, zero_division=0),
                                  'flag_rate': lambda yt, yp: selection_rate(yt, yp),
                                  'churn_rate': lambda yt, yp: float(yt.mean()),
                                  'n': lambda yt, yp: len(yt)},
                         y_true=y_true, y_pred=y_pred, sensitive_features=X_test.loc[pred.index, attr].astype(str).values)
        by = mf.by_group.reset_index().rename(columns={'sensitive_feature_0': 'group'})
        by.insert(0, 'attribute', attr)
        by['n'] = by['n'].astype(int)
        by['small_group'] = by['n'] < MIN_N
        groups.append(by)
        for m in METRICS:
            sub = by.sort_values(m)
            gap = float(sub[m].iloc[-1] - sub[m].iloc[0])
            gaps.append({'attribute': attr, 'metric': m, 'gap': gap, 'highest': sub['group'].iloc[-1], 'lowest': sub['group'].iloc[0],
                         'flagged': gap > GAP_FLAG, 'min_group_n': int(by['n'].min())})
    threshold, source, placeholder = cs.get_threshold(bundle['meta'], bundle.get('config'))
    return {'by_group': pd.concat(groups, ignore_index=True), 'gaps': pd.DataFrame(gaps), 'threshold': threshold,
            'threshold_source': source, 'threshold_is_placeholder': placeholder, 'n': int(len(y_true))}


def to_markdown(result: dict) -> str:
    g, gaps = result['by_group'], result['gaps']
    out = ['# Fairness report', '',
           'Audit on the held-out test split (n = %d) at flag threshold %.3f (%s%s). Computed with Fairlearn `MetricFrame`.'
           % (result['n'], result['threshold'], result['threshold_source'], ', PLACEHOLDER' if result['threshold_is_placeholder'] else ''),
           '`gender` is audited only; it is not a model input. A gap is max - min across the groups of one attribute; '
           'gaps **above %.2f are in bold with a warning marker**. Groups with n < %d are marked (*) and their gaps are unreliable.' % (GAP_FLAG, MIN_N), '',
           '## Gaps', '', '| Attribute | Recall gap | Precision gap | Flag-rate gap | Smallest group n |', '|---|---|---|---|---|']
    for attr in g['attribute'].unique():
        row = gaps[gaps.attribute == attr].set_index('metric')

        def cell(m):
            r = row.loc[m]
            txt = '%.3f (%s vs %s)' % (r['gap'], r['highest'], r['lowest'])
            return '**⚠ %s**' % txt if r['flagged'] else txt
        out.append('| %s | %s | %s | %s | %d |' % (attr, cell('recall'), cell('precision'), cell('flag_rate'), row['min_group_n'].iloc[0]))
    out += ['', '## By group', '', '| Attribute | Group | n | Churn rate | Flag rate | Recall | Precision |', '|---|---|---|---|---|---|---|']
    for _, r in g.iterrows():
        out.append('| %s | %s%s | %d | %.3f | %.3f | %.3f | %.3f |' % (r['attribute'], r['group'], ' (*)' if r['small_group'] else '', r['n'],
                                                                      r['churn_rate'], r['flag_rate'], r['recall'], r['precision']))
    out += ['', '## Reading this', '',
            '- Flag-rate gaps partly reflect real differences in churn rate (see the churn-rate column); recall and precision gaps are the ones to investigate.',
            '- The test split is small (1,409 rows); confidence intervals on a single group are wide. These are screening results, not proof of (un)fairness.',
            '- No mitigation has been applied. Any change to thresholds by group would need a documented policy decision.']
    return '\n'.join(out) + '\n'


def write_report(bundle, path=None, csv=None) -> dict:
    X, y = load_test_split(csv)
    res = audit(bundle, X, y)
    Path(path or ROOT / 'fairness_report.md').write_text(to_markdown(res), encoding='utf8')
    return res


if __name__ == '__main__':
    r = write_report(cs.load_bundle(ROOT))
    print(r['gaps'][r['gaps'].flagged][['attribute', 'metric', 'gap']].round(3).to_string(index=False))
