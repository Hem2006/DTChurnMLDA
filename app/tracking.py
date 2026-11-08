"""MLflow logging for training runs.

Tracking store: sqlite (mlflow.db in the project) unless MLFLOW_TRACKING_URI is set. Used by train.py after every training run
(promoted or not) and by `python -m app.tracking` to back-fill the artifacts that are currently saved.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENT = 'evergreen-churn'
ARTIFACT_FILES = ['churn_model_meta.json', 'churn_model.joblib', 'churn_xgb_explainer.joblib', 'churn_logreg_ref.joblib']


def default_uri() -> str:
    return os.environ.get('MLFLOW_TRACKING_URI') or 'sqlite:///' + (ROOT / 'mlflow.db').as_posix()


def _flat(prefix: str, d: dict, out: dict):
    for k, v in d.items():
        if isinstance(v, dict):
            _flat(prefix + k + '.', v, out)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[prefix + k] = float(v)


def metrics_from_meta(meta: dict) -> dict:
    m = {}
    for k, v in meta['test_metrics'].items():
        m['test.' + k] = float(v)
    for k, (lo, hi) in meta['test_metrics_ci95'].items():
        m['test_ci_low.' + k], m['test_ci_high.' + k] = float(lo), float(hi)
    for k, v in meta['calibration_test'].items():
        m['calibration.' + k] = float(v)
    for k, v in meta['nested_cv_auc'].items():
        m['nested_cv_auc.%s' % k.split(' ')[0]] = float(v['mean'])
    m['test_auc_baseline_contract_tenure'] = float(meta['test_auc_baseline_contract_tenure'])
    if 'logreg_reference' in meta:
        m['logreg_reference.test_roc_auc'] = float(meta['logreg_reference']['test_roc_auc'])
    if 'explainer_top3_overlap' in meta:
        m['explainer_top3_overlap.mean'] = float(meta['explainer_top3_overlap']['mean_overlap'])
    return m


def params_from_meta(meta: dict) -> dict:
    return {'seed': meta['seed'], 'selected_model': meta['selected_model'], 'calibration_method': meta['calibration_method'],
            'feature_set': meta['feature_set_chosen'], 'threshold': round(meta['threshold'], 6), 'threshold_source': meta['threshold_source'],
            'n_inputs': len(meta['input_columns'])}


def log_run(meta: dict, artifact_dir=None, *, tracking_uri=None, experiment=EXPERIMENT, run_name=None, tags=None) -> str:
    """Log one training run. Returns the MLflow run id."""
    import mlflow
    mlflow.set_tracking_uri(tracking_uri or default_uri())
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params(params_from_meta(meta))
        mlflow.log_metrics(metrics_from_meta(meta))
        mlflow.set_tags({'threshold_is_placeholder': str('placeholder' in meta['threshold_source']).lower(),
                         'uplift': 'not part of this run (SIMULATED, trained separately)',
                         **{'lib.' + k: v for k, v in meta['versions'].items()}, **(tags or {})})
        if artifact_dir is not None:
            for f in ARTIFACT_FILES:
                p = Path(artifact_dir) / f
                if p.exists():
                    mlflow.log_artifact(str(p), artifact_path='artifacts')
        return run.info.run_id


if __name__ == '__main__':
    meta = json.loads((ROOT / 'churn_model_meta.json').read_text())
    rid = log_run(meta, ROOT, run_name='backfill: currently saved artifacts', tags={'source': 'backfilled_from_saved_meta'})
    print('logged run', rid, 'to', default_uri())
