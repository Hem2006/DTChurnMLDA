"""Load the trained artifacts, validate customer input, score. Every other module goes through here."""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import joblib
import pandas as pd
import yaml

from src import guardrails as G
from src import threshold as T
from src.data import ADDONS, INPUTS, NO_INTERNET, NO_PHONE, ROOT

MODELS = Path(os.environ.get('EVERGREEN_MODELS', ROOT / 'models'))      # override for experiments / tests
CONFIG = ROOT / 'config'
NON_NEGATIVE = {'tenure', 'MonthlyCharges'}


class InputError(ValueError):
    """A customer row the model must not score (missing/invalid fields or incoherent services)."""


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf8')) or {}


def load_bundle(models_dir: Path | str = MODELS, config_dir: Path | str = CONFIG) -> dict:
    """Plain dict: meta, metrics, model (served), variants, logreg_ref, business, threshold, policy, scenarios, uplift (None until built)."""
    d, c = Path(models_dir), Path(config_dir)
    for f in ('meta.json', 'served_model.joblib'):
        if not (d / f).exists():
            raise FileNotFoundError('%s is missing: run `python -m src.train` first (see README)' % (d / f))
    meta = json.loads((d / 'meta.json').read_text())
    _check_versions(meta)
    business = T.load_business(c / 'business.yaml')
    b = {'meta': meta, 'metrics': json.loads((d / 'metrics.json').read_text()), 'model': joblib.load(d / 'served_model.joblib'),
         'variants': joblib.load(d / 'calibrated_variants.joblib'), 'xgb_uncalibrated': joblib.load(d / 'xgb_uncalibrated.joblib'),
         'logreg_ref': joblib.load(d / 'logreg_ref.joblib'), 'business': business,
         'threshold': T.resolve(business, meta['placeholder_threshold']), 'policy': G.validate_policy(load_yaml(c / 'policy.yaml')),
         'scenarios': load_yaml(c / 'scenarios.yaml').get('scenarios', []), 'models_dir': d, 'config_dir': c, 'uplift': None}
    up = d / 'uplift.joblib'
    if up.exists():
        b['uplift'] = joblib.load(up)
    return b


def _check_versions(meta: dict):
    from src.train import versions
    now, then = versions(), meta.get('versions', {})
    diff = {k: (v, now.get(k)) for k, v in then.items() if v != now.get(k)}
    if diff:
        warnings.warn('Library versions differ from training time (saved, installed): %s' % diff)


def check_coherent(row: dict, levels: dict) -> None:
    problems = []
    for c, allowed in levels.items():
        if row.get(c) not in allowed:
            problems.append('%s=%r is not one of %s' % (c, row.get(c), allowed))
    if not problems:
        if row['InternetService'] == 'No':
            bad = [c for c in ADDONS if row[c] != NO_INTERNET]
            if bad:
                problems.append("InternetService=No requires %s = '%s'" % (bad, NO_INTERNET))
        else:
            bad = [c for c in ADDONS if row[c] == NO_INTERNET]
            if bad:
                problems.append("%s can only be '%s' when InternetService=No" % (bad, NO_INTERNET))
        if row['PhoneService'] == 'No' and row['MultipleLines'] != NO_PHONE:
            problems.append("PhoneService=No requires MultipleLines='%s'" % NO_PHONE)
        if row['PhoneService'] == 'Yes' and row['MultipleLines'] == NO_PHONE:
            problems.append("MultipleLines='%s' only when PhoneService=No" % NO_PHONE)
    if problems:
        raise InputError('; '.join(problems))


def prepare(df: pd.DataFrame, meta: dict, coherence: bool = True) -> pd.DataFrame:
    """Validate a frame and return exactly the 17 model inputs. Raises InputError listing every problem."""
    if not isinstance(df, pd.DataFrame):
        raise TypeError('expected a DataFrame')
    if len(df) == 0:
        raise InputError('no rows to score')
    missing = [c for c in meta['inputs'] if c not in df.columns]
    if missing:
        raise InputError('Missing required columns: %s' % missing)
    out, problems = df[meta['inputs']].copy(), []
    for c in meta['numeric']:
        num = pd.to_numeric(out[c], errors='coerce')
        if num.isna().any():
            problems.append('%s: %d missing/non-numeric value(s)' % (c, int(num.isna().sum())))
        elif c in NON_NEGATIVE and (num < 0).any():
            problems.append('%s: negative values' % c)
        elif c == 'SeniorCitizen' and not num.isin([0, 1]).all():
            problems.append('SeniorCitizen must be 0 or 1')
        out[c] = num.astype(float)
    for c in meta['categorical']:
        if out[c].isna().any():
            problems.append('%s: missing value(s)' % c)
        elif not out[c].map(lambda v: isinstance(v, str)).all():
            problems.append('%s: expected text' % c)
        else:
            out[c] = out[c].str.strip()
            unseen = sorted(set(out[c]) - set(meta['levels'][c]))
            if unseen:
                problems.append('%s: unknown value(s) %s' % (c, unseen))
    if problems:
        raise InputError('Invalid input: ' + '; '.join(problems))
    if coherence:
        for i, row in out.iterrows():
            try:
                check_coherent(row.to_dict(), meta['levels'])
            except InputError as e:
                raise InputError('row %r: %s' % (i, e)) from None
    return out


def predict(df: pd.DataFrame, bundle: dict, model=None) -> pd.DataFrame:
    """Calibrated probability and a flag at the threshold in force. Always carries threshold_source / is_placeholder."""
    X = prepare(df, bundle['meta'])
    t = bundle['threshold']
    p = (model or bundle['model']).predict_proba(X)[:, 1]
    return pd.DataFrame({'churn_probability': p, 'flagged': p >= t['threshold'], 'threshold': t['threshold'], 'threshold_source': t['source'],
                         'threshold_is_placeholder': t['is_placeholder']}, index=df.index)


def reference_probability(df: pd.DataFrame, bundle: dict) -> pd.Series:
    """Class-weighted logistic-regression probability (the twin's cross-check; uncalibrated, so only differences are meaningful)."""
    return pd.Series(bundle['logreg_ref'].predict_proba(prepare(df, bundle['meta']))[:, 1], index=df.index)


def customer_row(frame_row: pd.Series) -> dict:
    """A frame row as a plain dict of the 17 model inputs with python types."""
    return {c: (frame_row[c].item() if hasattr(frame_row[c], 'item') else frame_row[c]) for c in INPUTS}
