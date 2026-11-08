"""Fairness screening on the held-out test split with Fairlearn MetricFrame.

These are SCREENING results: groups are small, there are no confidence intervals here, and NO mitigation is applied.
gender is audited only; it is not a model input.
"""
from __future__ import annotations

import pandas as pd
from fairlearn.metrics import MetricFrame
from sklearn.metrics import precision_score, recall_score

from src.data import AUDIT_ATTRS

GAP_FLAG = 0.10
METRICS = ['recall', 'precision', 'flag_rate']
CAVEAT = ('Screening results only: small groups make gaps noisy, no mitigation has been applied, and a gap is a prompt to investigate, '
          'not proof of unfairness or of its absence.')


def _flag_rate(y_true, y_pred):
    return float(pd.Series(y_pred).mean())


def audit(test_preds: pd.DataFrame, threshold: float, p_col: str = 'p_served') -> dict:
    """test_preds needs y, p_col and the audit attributes. Returns by-group metrics, gaps (max - min) and group sizes."""
    y, flag = test_preds['y'].values, (test_preds[p_col].values >= threshold).astype(int)
    fns = {'recall': lambda a, b: recall_score(a, b, zero_division=0), 'precision': lambda a, b: precision_score(a, b, zero_division=0), 'flag_rate': _flag_rate}
    by_rows, gap_rows = [], []
    for attr in AUDIT_ATTRS:
        mf = MetricFrame(metrics=fns, y_true=y, y_pred=flag, sensitive_features=test_preds[attr].astype(str).values)
        n = pd.Series(test_preds[attr].astype(str).values).value_counts()
        tbl = mf.by_group.copy()
        tbl['n'] = [int(n[g]) for g in tbl.index]
        for g, r in tbl.iterrows():
            by_rows.append({'attribute': attr, 'group': g, 'n': int(r['n']), **{m: float(r[m]) for m in METRICS}})
        for m in METRICS:
            col = tbl[m]
            gap_rows.append({'attribute': attr, 'metric': m, 'gap': float(col.max() - col.min()), 'highest': str(col.idxmax()), 'lowest': str(col.idxmin()),
                             'min_group_n': int(tbl['n'].min()), 'flagged': bool(col.max() - col.min() > GAP_FLAG)})
    return {'threshold': float(threshold), 'by_group': pd.DataFrame(by_rows), 'gaps': pd.DataFrame(gap_rows), 'caveat': CAVEAT}
