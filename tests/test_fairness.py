import json

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import precision_score, recall_score

import churn_serving as cs
from app import fairness as fa
from conftest import ROOT


@pytest.fixture(scope='module')
def result(bundle, test_frame):
    X, y = test_frame
    return fa.audit(bundle, X, y)


def test_covers_four_attributes_and_all_groups(result, test_frame):
    X, _ = test_frame
    g = result['by_group']
    assert set(g.attribute) == {'SeniorCitizen', 'Partner', 'Dependents', 'gender'}
    for a in g.attribute.unique():
        assert g[g.attribute == a]['n'].sum() == len(X) == result['n']


def test_gender_is_audited_but_not_a_model_input(bundle):
    assert 'gender' not in bundle['meta']['input_columns']
    assert 'gender' in fa.ATTRIBUTES


def test_matches_manual_sklearn_metrics(result, bundle, test_frame):
    X, y = test_frame
    flag = cs.predict(X, bundle)['flagged'].astype(int)
    for attr, grp in [('Dependents', 'Yes'), ('SeniorCitizen', '1'), ('gender', 'Female')]:
        m = X[attr].astype(str) == grp
        row = result['by_group'].query('attribute == @attr and group == @grp').iloc[0]
        assert row['recall'] == pytest.approx(recall_score(y[m], flag[m]))
        assert row['precision'] == pytest.approx(precision_score(y[m], flag[m]))
        assert row['flag_rate'] == pytest.approx(flag[m].mean())
        assert row['n'] == m.sum()


def test_matches_numbers_saved_in_meta(result, bundle):
    """The audit on the served model reproduces the subgroup table train.py stored (same threshold)."""
    for s in bundle['meta']['subgroups']:
        row = result['by_group'].query('attribute == @s["attribute"] and group == @s["group"]').iloc[0]
        assert row['recall'] == pytest.approx(s['recall'], abs=1e-9) and row['n'] == s['n']


def test_gaps_over_010_are_flagged(result):
    g = result['gaps']
    assert (g.flagged == (g.gap > 0.10)).all()
    dep = g.query('attribute == "Dependents" and metric == "recall"').iloc[0]
    assert dep.flagged and dep.highest == 'No' and dep.lowest == 'Yes'


def test_gap_arithmetic_synthetic(bundle):
    """Hand-built case: group A recall 1.0, group B recall 0.5 -> gap 0.5, flagged."""
    X = pd.DataFrame({'a': ['A'] * 4 + ['B'] * 4})
    y = pd.Series([1, 1, 0, 0, 1, 1, 0, 0])
    stub_pred = pd.DataFrame({'flagged': [True, True, False, False, True, False, False, False]}, index=X.index)
    from unittest import mock
    with mock.patch.object(cs, 'predict', return_value=stub_pred):
        r = fa.audit(bundle, X, y, attributes=['a'])
    rec = r['gaps'].query('metric == "recall"').iloc[0]
    assert rec.gap == pytest.approx(0.5) and rec.flagged and r['by_group'].small_group.all()


def test_report_markdown_highlights_and_shows_sample_sizes(result):
    md = fa.to_markdown(result)
    assert '# Fairness report' in md and '⚠' in md and 'n = 1409' in md
    assert 'PLACEHOLDER' in md and 'not a model input' in md
    for n in result['by_group']['n']:
        assert str(n) in md


def test_report_file_written(bundle, tmp_path):
    p = tmp_path / 'r.md'
    fa.write_report(bundle, p)
    assert p.read_text(encoding='utf8').startswith('# Fairness report')
