"""Drift check: population stability index (PSI) of tenure, MonthlyCharges and Contract against the TRAINING data.

PSI = sum_i (cur_i - ref_i) * ln(cur_i / ref_i) over bins (numeric: reference deciles; categorical: one bin per category plus 'other').
The usual rule of thumb (a convention, not a law): < 0.10 stable, 0.10-0.25 moderate shift, > 0.25 major shift.
Drift only says the inputs moved; it does not say the model is wrong.

    python -m app.drift --build            # write drift_reference.json from the training split
    python -m app.drift file.csv           # compare a CSV of customers with it
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = 'drift_reference.json'
FEATURES = {'tenure': 'numeric', 'MonthlyCharges': 'numeric', 'Contract': 'categorical'}
N_BINS = 10
EPS = 1e-4
STABLE, MAJOR = 0.10, 0.25


def status(psi: float) -> str:
    return 'stable' if psi < STABLE else ('moderate shift' if psi <= MAJOR else 'MAJOR shift')


def psi_from_shares(ref: np.ndarray, cur: np.ndarray) -> float:
    ref, cur = np.clip(np.asarray(ref, float), EPS, None), np.clip(np.asarray(cur, float), EPS, None)
    ref, cur = ref / ref.sum(), cur / cur.sum()
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def build_reference(train_df: pd.DataFrame, features=FEATURES) -> dict:
    ref = {'n': int(len(train_df)), 'bins': N_BINS, 'features': {}}
    for f, kind in features.items():
        s = train_df[f]
        if kind == 'numeric':
            edges = np.unique(np.quantile(s.astype(float), np.linspace(0, 1, N_BINS + 1)[1:-1]))
            counts = np.bincount(np.searchsorted(edges, s.astype(float), side='right'), minlength=len(edges) + 1)
            ref['features'][f] = {'kind': 'numeric', 'edges': edges.tolist(), 'shares': (counts / counts.sum()).tolist()}
        else:
            vc = s.value_counts(normalize=True)
            ref['features'][f] = {'kind': 'categorical', 'categories': list(vc.index), 'shares': vc.tolist()}
    return ref


def check(current: pd.DataFrame, ref: dict) -> pd.DataFrame:
    """PSI per feature of `current` against the reference."""
    rows = []
    for f, spec in ref['features'].items():
        if f not in current.columns:
            raise ValueError('current data is missing column %r' % f)
        s = current[f]
        if s.isna().any():
            raise ValueError('%s has %d missing values; fix the data before checking drift' % (f, int(s.isna().sum())))
        if spec['kind'] == 'numeric':
            x = pd.to_numeric(s, errors='raise').astype(float).values
            counts = np.bincount(np.searchsorted(np.asarray(spec['edges']), x, side='right'), minlength=len(spec['edges']) + 1)
            cur, refs = counts / counts.sum(), np.asarray(spec['shares'])
        else:
            cats = spec['categories']
            cur = np.array([(s == c).mean() for c in cats] + [(~s.isin(cats)).mean()])
            refs = np.array(spec['shares'] + [0.0])
        p = psi_from_shares(refs, cur)
        rows.append({'feature': f, 'psi': p, 'status': status(p), 'n_current': int(len(s))})
    return pd.DataFrame(rows)


def load_training_frame(csv=None) -> pd.DataFrame:
    df = pd.read_csv(csv or ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv')
    y = df['Churn'].map({'Yes': 1, 'No': 0})
    train, _, _, _ = train_test_split(df, y, test_size=0.2, random_state=42, stratify=y)
    return train


def save_reference(ref: dict, path=None):
    Path(path or ROOT / REFERENCE).write_text(json.dumps(ref, indent=2))


def load_reference(path=None) -> dict:
    return json.loads(Path(path or ROOT / REFERENCE).read_text())


if __name__ == '__main__':
    if sys.argv[1:] == ['--build']:
        save_reference(build_reference(load_training_frame()))
        print('wrote', ROOT / REFERENCE)
    elif len(sys.argv) == 2:
        print(check(pd.read_csv(sys.argv[1]), load_reference()).round(4).to_string(index=False))
    else:
        print(__doc__)
