"""Population stability index (PSI) of tenure, MonthlyCharges and Contract against the TRAINING data.

PSI = sum_i (cur_i - ref_i) * ln(cur_i / ref_i). Numeric features use the training deciles as bins; Contract uses one bin per category.
Rule of thumb (a convention, not a law): < 0.10 stable, 0.10-0.25 moderate shift, > 0.25 major shift. Drift only says inputs moved,
not that the model is wrong.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = {'tenure': 'numeric', 'MonthlyCharges': 'numeric', 'Contract': 'categorical'}
N_BINS = 10
EPS = 1e-4
STABLE, MAJOR = 0.10, 0.25


def status(psi: float) -> str:
    return 'stable' if psi < STABLE else ('moderate shift' if psi <= MAJOR else 'MAJOR shift')


def psi_from_shares(ref, cur) -> float:
    ref, cur = np.clip(np.asarray(ref, float), EPS, None), np.clip(np.asarray(cur, float), EPS, None)
    ref, cur = ref / ref.sum(), cur / cur.sum()
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def build_reference(train: dict | pd.DataFrame) -> dict:
    """Reference bins from training values (a DataFrame or dict of columns)."""
    ref = {}
    for f, kind in FEATURES.items():
        s = pd.Series(train[f])
        if kind == 'numeric':
            edges = np.unique(np.quantile(s.astype(float), np.linspace(0, 1, N_BINS + 1)[1:-1]))
            counts = np.bincount(np.searchsorted(edges, s.astype(float), side='right'), minlength=len(edges) + 1)
            ref[f] = {'kind': 'numeric', 'edges': edges.tolist(), 'shares': (counts / counts.sum()).tolist()}
        else:
            vc = s.value_counts(normalize=True)
            ref[f] = {'kind': 'categorical', 'categories': list(vc.index), 'shares': vc.tolist()}
    return ref


def check(current: pd.DataFrame, ref: dict) -> pd.DataFrame:
    rows = []
    for f, spec in ref.items():
        if f not in current.columns:
            raise ValueError('current data is missing column %r' % f)
        s = current[f]
        if s.isna().any():
            raise ValueError('%s has %d missing values; fix the data before checking drift' % (f, int(s.isna().sum())))
        if spec['kind'] == 'numeric':
            counts = np.bincount(np.searchsorted(spec['edges'], pd.to_numeric(s).astype(float), side='right'), minlength=len(spec['edges']) + 1)
            cur = counts / counts.sum()
        else:
            cats = spec['categories']
            counts = np.array([(s == c).sum() for c in cats] + [(~s.isin(cats)).sum()], float)
            cur = counts / counts.sum()
            spec = {**spec, 'shares': list(spec['shares']) + [0.0]}
        psi = psi_from_shares(spec['shares'], cur)
        rows.append({'feature': f, 'psi': psi, 'status': status(psi)})
    return pd.DataFrame(rows)
