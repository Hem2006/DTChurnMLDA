import json

import pytest

import churn_serving as cs
from conftest import ROOT

META = json.loads((ROOT / 'churn_model_meta.json').read_text())


def test_expected_value_threshold_with_made_up_numbers():
    # made-up numbers, test only: offer 20, value 800, success 30% -> 0.083
    t, src, placeholder = cs.get_threshold(META, {'offer_cost': 20, 'customer_value': 800, 'success_rate': 0.30})
    assert t == pytest.approx(0.083, abs=0.0005)
    assert src == cs.EXPECTED_VALUE_SOURCE and placeholder is False


def test_null_config_gives_labelled_placeholder():
    t, src, placeholder = cs.get_threshold(META, {'offer_cost': None, 'customer_value': None, 'success_rate': None})
    assert (t, src, placeholder) == (META['threshold'], META['threshold_source'], True)
    assert 'placeholder' in src


@pytest.mark.parametrize('missing', ['offer_cost', 'customer_value', 'success_rate'])
def test_partially_filled_config_stays_placeholder(missing):
    cfg = {'offer_cost': 20, 'customer_value': 800, 'success_rate': 0.3, missing: None}
    assert cs.get_threshold(META, cfg)[2] is True


@pytest.mark.parametrize('cfg', [{'offer_cost': -1, 'customer_value': 800, 'success_rate': .3},
                                 {'offer_cost': 20, 'customer_value': 0, 'success_rate': .3},
                                 {'offer_cost': 20, 'customer_value': 800, 'success_rate': 1.5},
                                 {'offer_cost': '20', 'customer_value': 800, 'success_rate': .3}])
def test_bad_config_values_rejected(cfg):
    with pytest.raises(ValueError):
        cs.get_threshold(META, cfg)


def test_shipped_config_is_all_null():
    cfg = cs.load_config(ROOT / 'config.yaml')
    assert all(v is None for k, v in cfg.items() if k != 'uplift_simulation')


def test_missing_config_file_means_not_configured(tmp_path):
    assert all(v is None for v in cs.load_config(tmp_path / 'nope.yaml').values())


def test_unknown_config_key_rejected(tmp_path):
    p = tmp_path / 'c.yaml'
    p.write_text('offer_cost: 1\nbogus: 2\n')
    with pytest.raises(ValueError):
        cs.load_config(p)


def test_predict_reports_threshold_source_placeholder(bundle, test_frame):
    X, _ = test_frame
    out = cs.predict(X.iloc[:20], bundle)
    assert (out['threshold_source'] == bundle['meta']['threshold_source']).all()
    assert out['threshold_is_placeholder'].all() and out['threshold_note'].str.startswith('PLACEHOLDER').all()
    assert (out['threshold'] == bundle['meta']['threshold']).all()


def test_predict_uses_configured_threshold(bundle, test_frame):
    X, _ = test_frame
    b = dict(bundle, config={'offer_cost': 20, 'customer_value': 800, 'success_rate': 0.30})
    out = cs.predict(X.iloc[:200], b)
    assert (out['threshold_source'] == cs.EXPECTED_VALUE_SOURCE).all() and not out['threshold_is_placeholder'].any()
    assert out['threshold'].iloc[0] == pytest.approx(0.0833, abs=1e-3)
    assert (out['flagged'] == (out['churn_probability'] >= out['threshold'])).all()
    assert 'threshold_note' not in out
