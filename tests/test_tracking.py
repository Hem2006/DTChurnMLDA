import json
import shutil

import mlflow
import pytest

import train
from app import tracking as tr
from conftest import ROOT

META = json.loads((ROOT / 'churn_model_meta.json').read_text())


@pytest.fixture()
def uri(tmp_path, monkeypatch):
    u = 'sqlite:///' + (tmp_path / 'ml.db').as_posix()
    monkeypatch.setenv('MLFLOW_TRACKING_URI', u)
    return u


def runs(uri):
    mlflow.set_tracking_uri(uri)
    exp = mlflow.get_experiment_by_name(tr.EXPERIMENT)
    return [] if exp is None else mlflow.search_runs([exp.experiment_id], output_format='list')


def test_metrics_and_params_from_meta():
    m, p = tr.metrics_from_meta(META), tr.params_from_meta(META)
    assert m['test.roc_auc'] == META['test_metrics']['roc_auc'] and m['nested_cv_auc.xgboost'] == META['nested_cv_auc']['xgboost']['mean']
    assert m['explainer_top3_overlap.mean'] > 0.7 and m['logreg_reference.test_roc_auc'] > 0.8 and 'test_ci_low.roc_auc' in m
    assert p['selected_model'] == 'xgboost' and p['calibration_method'] == 'isotonic' and p['n_inputs'] == 17


def test_log_run_records_params_metrics_artifacts(uri, tmp_path):
    rid = tr.log_run(META, ROOT, tracking_uri=uri, tags={'k': 'v'})
    (r,) = runs(uri)
    assert r.info.run_id == rid
    assert r.data.metrics['test.roc_auc'] == pytest.approx(META['test_metrics']['roc_auc'])
    assert r.data.params['seed'] == '42' and r.data.tags['threshold_is_placeholder'] == 'true' and r.data.tags['k'] == 'v'
    assert r.data.tags['lib.xgboost'] == '3.4.1'
    arts = [a.path for a in mlflow.artifacts.list_artifacts(run_id=rid, artifact_path='artifacts')]
    assert 'artifacts/churn_model_meta.json' in arts and 'artifacts/churn_model.joblib' in arts


def _stub_train(monkeypatch, auc_shift=0.0):
    def fake(data, out, config):
        out.mkdir(parents=True, exist_ok=True)
        for f in tr.ARTIFACT_FILES:
            shutil.copy2(ROOT / f, out / f)
        m = json.loads(json.dumps(META))
        m['test_metrics']['roc_auc'] += auc_shift
        return m
    monkeypatch.setattr(train, 'train', fake)


def test_train_main_logs_run_when_reproduced(uri, tmp_path, monkeypatch):
    _stub_train(monkeypatch)
    rc = train.main(['--data', str(ROOT / 'x.csv'), '--staging', str(tmp_path / 'st'), '--reference', str(ROOT / 'churn_model_meta.json'),
                     '--config', str(ROOT / 'config.yaml'), '--no-promote'])
    (r,) = runs(uri)
    assert rc == 0 and r.data.tags['reproduced_within_tol'] == 'true' and r.data.tags['promoted'] == 'false'


def test_train_main_logs_even_when_not_reproduced_and_does_not_overwrite(uri, tmp_path, monkeypatch):
    _stub_train(monkeypatch, auc_shift=0.02)
    before = (ROOT / 'churn_model_meta.json').read_bytes()
    rc = train.main(['--data', 'x.csv', '--staging', str(tmp_path / 'st'), '--reference', str(ROOT / 'churn_model_meta.json'),
                     '--config', str(ROOT / 'config.yaml')])
    (r,) = runs(uri)
    assert rc == 2 and r.data.tags['reproduced_within_tol'] == 'false' and r.data.tags['promoted'] == 'false'
    assert float(r.data.tags['auc_diff_vs_reference']) == pytest.approx(0.02, abs=1e-4)
    assert (ROOT / 'churn_model_meta.json').read_bytes() == before


def test_no_mlflow_flag(uri, tmp_path, monkeypatch):
    _stub_train(monkeypatch)
    train.main(['--data', 'x.csv', '--staging', str(tmp_path / 'st'), '--reference', str(ROOT / 'churn_model_meta.json'),
                '--config', str(ROOT / 'config.yaml'), '--no-promote', '--no-mlflow'])
    assert runs(uri) == []
