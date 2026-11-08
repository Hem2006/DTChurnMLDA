"""Evaluation helpers: ECE (10 equal-width bins), top-decile lift, bootstrap CIs, DeLong test for two correlated ROC AUCs."""
from __future__ import annotations

import numpy as np
from scipy import stats
from sklearn.metrics import (accuracy_score, average_precision_score, brier_score_loss, f1_score, precision_score,
                             recall_score, roc_auc_score)

ECE_BINS = 10
ECE_BINNING = '10 equal-width bins on [0, 1]; ECE = sum over bins of (n_bin / n) * |mean(y) - mean(p)|'


def reliability_table(y, p, bins: int = ECE_BINS):
    """Per equal-width bin: count, mean predicted probability, observed churn rate. Empty bins are omitted."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.any():
            rows.append({'bin': b, 'lo': b / bins, 'hi': (b + 1) / bins, 'n': int(m.sum()), 'mean_pred': float(p[m].mean()), 'observed': float(y[m].mean())})
    return rows


def ece(y, p, bins: int = ECE_BINS) -> float:
    n = len(y)
    return float(sum(r['n'] / n * abs(r['observed'] - r['mean_pred']) for r in reliability_table(y, p, bins)))


def top_decile_lift(y, p) -> float:
    """Churn rate in the 10% highest-scored customers divided by the overall churn rate."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    k = max(1, int(round(0.1 * len(y))))
    top = np.argsort(-p, kind='stable')[:k]
    return float(y[top].mean() / y.mean())


def point_metrics(y, p, t: float) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    pred = (p >= t).astype(int)
    return {'roc_auc': roc_auc_score(y, p), 'pr_auc': average_precision_score(y, p), 'brier': brier_score_loss(y, p),
            'precision': precision_score(y, pred, zero_division=0), 'recall': recall_score(y, pred, zero_division=0),
            'f1': f1_score(y, pred, zero_division=0), 'accuracy': accuracy_score(y, pred), 'flag_rate': float(pred.mean()),
            'top_decile_lift': top_decile_lift(y, p), 'ece': ece(y, p)}


def bootstrap(y, p, fn, B: int = 2000, seed: int = 42) -> tuple[dict, dict]:
    """Percentile bootstrap (B resamples of the test rows). fn(y, p) -> dict of floats. Returns (point, {metric: [lo, hi]})."""
    y, p = np.asarray(y), np.asarray(p)
    rng = np.random.default_rng(seed)
    point = {k: float(v) for k, v in fn(y, p).items()}
    rows = []
    for _ in range(B):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() == y[i].max():
            continue
        rows.append(fn(y[i], p[i]))
    keys = list(point)
    arr = np.array([[r[k] for k in keys] for r in rows], float)
    ci = {k: [float(np.quantile(arr[:, j], 0.025)), float(np.quantile(arr[:, j], 0.975))] for j, k in enumerate(keys)}
    return point, ci


# ------------------------------------------------------------------ DeLong (Sun & Xu 2014 fast algorithm)
def _midrank(x):
    order = np.argsort(x)
    z = x[order]
    n = len(x)
    out = np.zeros(n)
    i = 0
    while i < n:
        j = i
        while j < n and z[j] == z[i]:
            j += 1
        out[i:j] = 0.5 * (i + j - 1) + 1
        i = j
    res = np.empty(n)
    res[order] = out
    return res


def _fast_delong(preds, m):
    """preds: (k classifiers, n samples) with the m positives first. Returns (aucs, covariance matrix)."""
    n = preds.shape[1] - m
    pos, neg = preds[:, :m], preds[:, m:]
    k = preds.shape[0]
    tx, ty, tz = (np.empty((k, m)), np.empty((k, n)), np.empty((k, m + n)))
    for r in range(k):
        tx[r], ty[r], tz[r] = _midrank(pos[r]), _midrank(neg[r]), _midrank(preds[r])
    aucs = tz[:, :m].sum(axis=1) / m / n - (m + 1.0) / 2.0 / n
    v01 = (tz[:, :m] - tx) / n
    v10 = 1.0 - (tz[:, m:] - ty) / m
    cov = np.atleast_2d(np.cov(v01)) / m + np.atleast_2d(np.cov(v10)) / n
    return aucs, cov


def delong_test(y, p1, p2) -> dict:
    """Two-sided DeLong test of AUC(p1) vs AUC(p2) on the same labels."""
    y = np.asarray(y).astype(int)
    order = np.argsort(-y, kind='stable')
    m = int(y.sum())
    preds = np.vstack([np.asarray(p1, float)[order], np.asarray(p2, float)[order]])
    aucs, cov = _fast_delong(preds, m)
    diff = aucs[0] - aucs[1]
    var = cov[0, 0] + cov[1, 1] - 2 * cov[0, 1]
    z = diff / np.sqrt(var) if var > 0 else 0.0
    return {'auc_1': float(aucs[0]), 'auc_2': float(aucs[1]), 'diff': float(diff), 'se': float(np.sqrt(max(var, 0))), 'z': float(z),
            'p_value': float(2 * stats.norm.sf(abs(z)))}
