import numpy as np
import pandas as pd
import pytest

from app import drift as dr
from conftest import ROOT


@pytest.fixture(scope='module')
def train_df():
    return dr.load_training_frame()


@pytest.fixture(scope='module')
def ref(train_df):
    return dr.build_reference(train_df)


def test_psi_formula_hand_computed():
    # ref 50/50, cur 70/30 -> (0.2)ln(1.4) + (-0.2)ln(0.6)
    assert dr.psi_from_shares([.5, .5], [.7, .3]) == pytest.approx(.2 * np.log(1.4) - .2 * np.log(.6))
    assert dr.psi_from_shares([.3, .7], [.3, .7]) == pytest.approx(0)
    assert dr.psi_from_shares([.5, .5], [1, 0]) > 1                      # empty bin is floored, not infinite
    assert np.isfinite(dr.psi_from_shares([.5, .5], [1, 0]))


def test_status_thresholds():
    assert [dr.status(x) for x in (0, .099, .1, .25, .2501)] == ['stable', 'stable', 'moderate shift', 'moderate shift', 'MAJOR shift']


def test_reference_covers_the_three_features(ref):
    assert set(ref['features']) == {'tenure', 'MonthlyCharges', 'Contract'} and ref['n'] == 5634
    assert abs(sum(ref['features']['tenure']['shares']) - 1) < 1e-9 and abs(sum(ref['features']['Contract']['shares']) - 1) < 1e-9


def test_training_data_against_itself_is_zero(train_df, ref):
    r = dr.check(train_df, ref)
    assert (r.psi < 1e-6).all() and (r.status == 'stable').all()


def test_held_out_split_is_stable(train_df, ref):
    full = pd.read_csv(ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv')
    test = full.loc[~full.customerID.isin(train_df.customerID)]
    r = dr.check(test, ref)
    assert len(test) == 1409 and (r.psi < 0.1).all()


def test_shifted_tenure_is_flagged_major(train_df, ref):
    cur = train_df.copy()
    cur['tenure'] = (cur['tenure'] * 0.3).round()
    r = dr.check(cur, ref).set_index('feature')
    assert r.loc['tenure', 'status'] == 'MAJOR shift' and r.loc['Contract', 'psi'] < 1e-6 and r.loc['MonthlyCharges', 'psi'] < 1e-6


def test_shifted_charges_flagged(train_df, ref):
    cur = train_df.copy()
    cur['MonthlyCharges'] = cur['MonthlyCharges'] * 1.4
    assert dr.check(cur, ref).set_index('feature').loc['MonthlyCharges', 'status'] == 'MAJOR shift'


def test_contract_mix_shift_and_unseen_category(train_df, ref):
    cur = train_df.copy()
    cur['Contract'] = 'Month-to-month'
    assert dr.check(cur, ref).set_index('feature').loc['Contract', 'status'] == 'MAJOR shift'
    cur['Contract'] = 'Three year'                                         # unseen -> 'other' bucket
    p = dr.check(cur, ref).set_index('feature').loc['Contract', 'psi']
    assert np.isfinite(p) and p > 0.25


def test_missing_column_or_values_rejected(train_df, ref):
    with pytest.raises(ValueError, match='missing column'):
        dr.check(train_df.drop(columns=['tenure']), ref)
    bad = train_df.copy()
    bad.loc[bad.index[0], 'MonthlyCharges'] = np.nan
    with pytest.raises(ValueError, match='missing values'):
        dr.check(bad, ref)


def test_reference_roundtrip(ref, tmp_path):
    dr.save_reference(ref, tmp_path / 'r.json')
    assert dr.load_reference(tmp_path / 'r.json') == ref
