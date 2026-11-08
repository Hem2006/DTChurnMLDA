import sys
from pathlib import Path

import pandas as pd
import pytest
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import churn_serving as cs  # noqa: E402

CSV = ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv'


@pytest.fixture(scope='session')
def bundle():
    return cs.load_bundle(ROOT)


@pytest.fixture(scope='session')
def test_frame():
    """The held-out 20% test split, rebuilt exactly as train.py does (seed 42, stratified)."""
    df = pd.read_csv(CSV).drop(columns=['customerID'])
    df['TotalCharges'] = pd.to_numeric(df['TotalCharges'], errors='coerce')
    y = df['Churn'].map({'Yes': 1, 'No': 0})
    X = df.drop(columns=['Churn'])
    _, X_test, _, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    return X_test, y_test
