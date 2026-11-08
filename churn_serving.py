# churn_serving.py - input validation, scoring and explanations for the saved churn model.
import json
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

NON_NEGATIVE = {'tenure', 'MonthlyCharges', 'TotalCharges'}
BINARY = {'SeniorCitizen'}


def installed_versions():
    import sklearn, xgboost, lightgbm, shap, scipy
    return {'python': sys.version.split()[0], 'scikit-learn': sklearn.__version__, 'xgboost': xgboost.__version__,
            'lightgbm': lightgbm.__version__, 'shap': shap.__version__, 'pandas': pd.__version__,
            'numpy': np.__version__, 'scipy': scipy.__version__, 'joblib': joblib.__version__}


def check_versions(meta, strict=False):
    """Compare library versions recorded at training time with the ones installed now."""
    now, then = installed_versions(), meta.get('versions', {})
    diffs = {k: (then[k], now.get(k)) for k in then if k != 'python' and then[k] != now.get(k)}
    if diffs:
        msg = 'Library versions differ from training time (saved, installed): %s. joblib files may not load or may score differently.' % diffs
        if strict:
            raise RuntimeError(msg)
        warnings.warn(msg)
    return diffs


EXPECTED_VALUE_SOURCE = 'expected_value: offer_cost / (success_rate * customer_value)'
CONFIG_KEYS = ('offer_cost', 'customer_value', 'success_rate')
EXTRA_CONFIG_KEYS = ('twin_effect_realization', 'scenario_costs', 'uplift_simulation')


def load_config(path='config.yaml'):
    """Read the business numbers. A missing file means 'not configured' (all null)."""
    import yaml
    p = Path(path)
    raw = (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}
    unknown = set(raw) - set(CONFIG_KEYS) - set(EXTRA_CONFIG_KEYS)
    if unknown:
        raise ValueError('Unknown keys in %s: %s' % (p, sorted(unknown)))
    return {k: raw.get(k) for k in CONFIG_KEYS + EXTRA_CONFIG_KEYS}


def get_threshold(meta, config=None):
    """Return (threshold, threshold_source, is_placeholder).
    offer_cost / (success_rate * customer_value) when all three config numbers are set, else the labelled placeholder from the meta file."""
    config = config or {}
    vals = {k: config.get(k) for k in CONFIG_KEYS}
    if all(v is not None for v in vals.values()):
        bad = [k for k, v in vals.items() if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or v <= 0]
        if bad:
            raise ValueError('config values must be positive numbers: %s' % bad)
        if vals['success_rate'] > 1:
            raise ValueError('success_rate must be a share between 0 and 1')
        return float(vals['offer_cost'] / (vals['success_rate'] * vals['customer_value'])), EXPECTED_VALUE_SOURCE, False
    return float(meta['threshold']), meta['threshold_source'], True


def load_bundle(directory='.', strict_versions=False):
    d = Path(directory)
    meta = json.loads((d / 'churn_model_meta.json').read_text())
    check_versions(meta, strict=strict_versions)
    ref = d / 'churn_logreg_ref.joblib'
    return {'meta': meta, 'config': load_config(d / 'config.yaml'), 'model': joblib.load(d / 'churn_model.joblib'),
            'explainer_pipeline': joblib.load(d / 'churn_xgb_explainer.joblib'),
            'logreg_ref': joblib.load(ref) if ref.exists() else None}


def prepare(df, meta):
    """Validate a frame of raw customers and return exactly the columns the model expects. Raises ValueError listing every problem."""
    if not isinstance(df, pd.DataFrame):
        raise TypeError('Expected a pandas DataFrame, got %s' % type(df).__name__)
    if len(df) == 0:
        raise ValueError('No rows to score')
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    need = meta['input_columns']
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError('Missing required columns: %s' % missing)
    extra = [c for c in df.columns if c not in need]
    if extra:
        warnings.warn('Ignoring columns the model does not use: %s' % extra)
    df = df[need]
    problems, out = [], pd.DataFrame(index=df.index)
    for c in meta['numeric_columns']:
        s = df[c]
        if not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
            s = s.astype('string').str.strip().replace('', pd.NA)       # whitespace-only strings become missing
        num = pd.to_numeric(s, errors='coerce').astype(float)
        if c == 'TotalCharges' and 'tenure' in df.columns:
            ten = pd.to_numeric(df['tenure'], errors='coerce')
            num = num.where(~(num.isna() & (ten == 0)), 0.0)            # a brand-new customer has been billed nothing
        bad = num.isna()
        if bad.any():
            problems.append('%s: %d missing or non-numeric value(s), first at index %r' % (c, int(bad.sum()), bad.idxmax()))
        if c in NON_NEGATIVE and (num < 0).any():
            problems.append('%s: negative values' % c)
        if c in BINARY and (~num.isin([0, 1]) & num.notna()).any():
            problems.append('%s: must be 0 or 1' % c)
        out[c] = num
    for c in meta['categorical_columns']:
        s = df[c]
        if s.isna().any():
            problems.append('%s: %d missing value(s)' % (c, int(s.isna().sum())))
        elif not all(isinstance(v, str) for v in s):
            problems.append('%s: expected text values, got other types' % c)
        else:
            s = s.str.strip()
            unseen = sorted(set(s) - set(meta['categorical_levels'][c]))
            if unseen:
                warnings.warn('%s: unseen categories %s are encoded as all zeros' % (c, unseen))
        out[c] = s
    if problems:
        raise ValueError('Invalid input: ' + '; '.join(problems))
    return out[need]


def predict(df, bundle):
    """Calibrated churn probability and a flag at the configured threshold.
    threshold_source says where the threshold came from; threshold_is_placeholder is True unless config.yaml supplies all three business numbers."""
    meta = bundle['meta']
    threshold, source, placeholder = get_threshold(meta, bundle.get('config'))
    p = bundle['model'].predict_proba(prepare(df, meta))[:, 1]
    out = pd.DataFrame({'churn_probability': p, 'flagged': p >= threshold, 'threshold': threshold,
                        'threshold_source': source, 'threshold_is_placeholder': placeholder}, index=df.index)
    if placeholder:
        out['threshold_note'] = meta['threshold_note']
    return out


def _raw_name(feature, columns):
    body = feature.split('__', 1)[1]
    if feature.startswith('cat__'):
        hits = [c for c in columns if body.startswith(c + '_')]
        return max(hits, key=len)
    return body


def _shap_one(pipe, explainer, X, columns):
    """SHAP for one fitted prep+XGBoost pipeline, summed to raw columns. Returns (contrib DataFrame, base value, margin Series)."""
    prep, model = pipe.named_steps['prep'], pipe.named_steps['model']
    Xt = prep.transform(X)
    Xt = Xt.toarray() if hasattr(Xt, 'toarray') else np.asarray(Xt)
    sv = explainer.shap_values(Xt)
    names = prep.get_feature_names_out()
    raw = [_raw_name(n, columns) for n in names]
    contrib = pd.DataFrame(sv, columns=names, index=X.index).T.groupby(raw).sum().T
    margin = pd.Series(model.predict(Xt, output_margin=True), index=X.index)
    return contrib, float(np.ravel(explainer.expected_value)[0]), margin


def _explainers(bundle):
    """TreeExplainers for the 5 base pipelines of the served ensemble. Built once per bundle and cached on it."""
    if '_explainers' not in bundle:
        import shap
        pipes = [cc.estimator for cc in bundle['model'].calibrated_classifiers_]
        bundle['_explainers'] = [(p, shap.TreeExplainer(p.named_steps['model'])) for p in pipes]
    return bundle['_explainers']


def explain_per_pipeline(df, bundle):
    """SHAP of each of the 5 base pipelines of the served ensemble: a list of (contributions, base value, margin)."""
    X = prepare(df, bundle['meta'])
    cols = bundle['meta']['input_columns']
    return [_shap_one(pipe, ex, X, cols) for pipe, ex in _explainers(bundle)]


def explain(df, bundle):
    """Per-customer SHAP contributions for the SERVED model: the 5 base pipelines of the calibrated ensemble are each explained
    and the SHAP values are averaged per raw input column. Units are log-odds of the (uncalibrated) base XGBoost score;
    the isotonic calibration is a monotone map applied on top. Returns (contributions DataFrame, base value, margin Series),
    with contributions + base value = margin (all averaged over the base pipelines)."""
    parts = explain_per_pipeline(df, bundle)
    cols = bundle['meta']['input_columns']
    n = len(parts)
    contrib = sum(c.reindex(columns=cols, fill_value=0.0) for c, _, _ in parts) / n
    return contrib, sum(b for _, b, _ in parts) / n, sum(m for _, _, m in parts) / n


def explain_single_pipeline(df, bundle):
    """LEGACY: SHAP on the one uncalibrated explainer pipeline (what explain() did before it was fixed). Kept only to measure
    agreement with the ensemble explanation; do not use for serving."""
    import shap
    X = prepare(df, bundle['meta'])
    if '_legacy_explainer' not in bundle:
        bundle['_legacy_explainer'] = shap.TreeExplainer(bundle['explainer_pipeline'].named_steps['model'])
    return _shap_one(bundle['explainer_pipeline'], bundle['_legacy_explainer'], X, bundle['meta']['input_columns'])


def top_k_overlap(contrib_a, contrib_b, k=3):
    """Per-row share of the top-k (highest push toward churn) raw columns that two contribution frames agree on."""
    ta = contrib_a.apply(lambda r: set(r.sort_values(ascending=False).index[:k]), axis=1)
    tb = contrib_b.apply(lambda r: set(r.sort_values(ascending=False).index[:k]), axis=1)
    return pd.Series([len(ta[i] & tb[i]) / k for i in contrib_a.index], index=contrib_a.index)


def top_reasons(df, bundle, k=3):
    """Reasons for FLAGGED customers only. Returns a DataFrame indexed by flagged customer with the calibrated churn probability
    and a list of the k raw columns pushing risk up most (log-odds push of the base XGBoost score, with the column's value).
    Unflagged customers get no row."""
    pred = predict(df, bundle)
    flagged = pred.index[pred['flagged'].values]
    if len(flagged) == 0:
        return pd.DataFrame({'churn_probability': pd.Series(dtype=float), 'reasons': pd.Series(dtype=object)})
    sub = df.loc[flagged]
    contrib, _, _ = explain(sub, bundle)
    X = prepare(sub, bundle['meta'])
    rows = []
    for i in contrib.index:
        top = contrib.loc[i].sort_values(ascending=False).head(k)
        rows.append([{'feature': f, 'value': X.loc[i, f], 'log_odds_push': round(float(v), 3)} for f, v in top.items() if v > 0])
    return pd.DataFrame({'churn_probability': pred.loc[flagged, 'churn_probability'].values, 'reasons': rows}, index=contrib.index)
