"""Small, dependency-free statistics used by the paper experiments (tested in tests/test_experiments_stats.py)."""
from __future__ import annotations

import math

import numpy as np
from scipy import stats


def _midrank(x: np.ndarray) -> np.ndarray:
    return stats.rankdata(x, method='average')


def delong_test(y, p1, p2) -> dict:
    """Paired DeLong test (DeLong et al. 1988; fast form of Sun & Xu 2014) for two correlated ROC AUCs on the same rows.
    Returns auc1, auc2, diff (auc1 - auc2), se of the difference, z, two-sided p-value and a 95% CI for the difference."""
    y = np.asarray(y).astype(int)
    pos, neg = y == 1, y == 0
    m, n = int(pos.sum()), int(neg.sum())
    if m < 2 or n < 2:
        raise ValueError('need at least two positives and two negatives')
    aucs, v10, v01 = [], [], []
    for p in (np.asarray(p1, float), np.asarray(p2, float)):
        tx, ty, tz = _midrank(p[pos]), _midrank(p[neg]), _midrank(p)
        auc = (tz[pos].sum() - m * (m + 1) / 2) / (m * n)
        aucs.append(auc)
        v10.append((tz[pos] - tx) / n)          # structural components for positives
        v01.append(1 - (tz[neg] - ty) / m)      # ... and for negatives
    s10, s01 = np.cov(np.vstack(v10)), np.cov(np.vstack(v01))
    s = s10 / m + s01 / n
    var = s[0, 0] + s[1, 1] - 2 * s[0, 1]
    diff = aucs[0] - aucs[1]
    se = math.sqrt(max(var, 0.0))
    z = diff / se if se > 0 else 0.0
    pval = 2 * stats.norm.sf(abs(z))
    return {'auc1': float(aucs[0]), 'auc2': float(aucs[1]), 'diff': float(diff), 'se': float(se), 'z': float(z), 'p_value': float(pval),
            'ci95_diff': [float(diff - 1.959964 * se), float(diff + 1.959964 * se)]}


def top_fraction_lift(y, p, frac: float = 0.10) -> float:
    """Churn rate among the top `frac` of customers by score, divided by the overall churn rate. Ties broken by a stable sort."""
    y, p = np.asarray(y), np.asarray(p)
    k = int(math.ceil(frac * len(y)))
    top = np.argsort(-p, kind='stable')[:k]
    return float(y[top].mean() / y.mean())


def ece_quantile(y, p, bins: int = 10) -> float:
    """ECE with equal-frequency (quantile) bins, weighted by bin share - the definition train.py uses for the reported numbers."""
    y, p = np.asarray(y), np.asarray(p)
    edges = np.quantile(p, np.linspace(0, 1, bins + 1)); edges[-1] += 1e-9
    b = np.digitize(p, edges[1:-1])
    return float(sum(abs(p[b == k].mean() - y[b == k].mean()) * (b == k).mean() for k in np.unique(b)))


def ece_uniform(y, p, bins: int = 10) -> float:
    """ECE with equal-width bins on [0, 1] (Guo et al. 2017), weighted by bin share. Reported only as a sensitivity check."""
    y, p = np.asarray(y), np.asarray(p)
    b = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(p[b == k].mean() - y[b == k].mean()) * (b == k).mean() for k in np.unique(b)))
