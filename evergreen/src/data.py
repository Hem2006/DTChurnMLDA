"""Telco data loading, cleaning and the one canonical train/test split (stratified 80/20, seed 42)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data' / 'telco.csv'
SEED = 42

# The 17 model inputs. gender and TotalCharges are deliberately absent; gender is kept in the frame for the fairness audit only.
INPUTS = ['SeniorCitizen', 'Partner', 'Dependents', 'tenure', 'PhoneService', 'MultipleLines', 'InternetService', 'OnlineSecurity',
          'OnlineBackup', 'DeviceProtection', 'TechSupport', 'StreamingTV', 'StreamingMovies', 'Contract', 'PaperlessBilling',
          'PaymentMethod', 'MonthlyCharges']
NUMERIC = ['SeniorCitizen', 'tenure', 'MonthlyCharges']
CATEGORICAL = [c for c in INPUTS if c not in NUMERIC]
ADDONS = ['OnlineSecurity', 'OnlineBackup', 'DeviceProtection', 'TechSupport', 'StreamingTV', 'StreamingMovies']
AUDIT_ATTRS = ['SeniorCitizen', 'Partner', 'Dependents', 'gender']
NO_INTERNET, NO_PHONE = 'No internet service', 'No phone service'


def load_raw(path: Path | str = DATA) -> pd.DataFrame:
    if not Path(path).exists():
        raise FileNotFoundError('%s not found. Put the IBM Telco Customer Churn CSV there (7,043 rows); the project never fabricates data.' % path)
    return pd.read_csv(path)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Blank TotalCharges -> NaN (it is not a model input anyway); Churn -> 0/1 in column `y`."""
    df = df.copy()
    df['TotalCharges'] = pd.to_numeric(df['TotalCharges'], errors='coerce')
    df['y'] = df['Churn'].map({'Yes': 1, 'No': 0})
    if df['y'].isna().any():
        raise ValueError('Churn column has values other than Yes/No')
    return df


def load_clean(path: Path | str = DATA) -> pd.DataFrame:
    return clean(load_raw(path))


def split(df: pd.DataFrame | None = None):
    """(train, test) frames, stratified 80/20, seed 42. Frames keep customerID, gender, all inputs and y."""
    df = load_clean() if df is None else df
    return train_test_split(df, test_size=0.2, random_state=SEED, stratify=df['y'])


def levels(df: pd.DataFrame) -> dict:
    return {c: sorted(df[c].unique().tolist()) for c in CATEGORICAL}
