import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import data as D  # noqa: E402

FIBER = {'SeniorCitizen': 0, 'Partner': 'No', 'Dependents': 'No', 'tenure': 5, 'PhoneService': 'Yes', 'MultipleLines': 'No', 'InternetService': 'Fiber optic',
         'OnlineSecurity': 'No', 'OnlineBackup': 'No', 'DeviceProtection': 'No', 'TechSupport': 'No', 'StreamingTV': 'No', 'StreamingMovies': 'No',
         'Contract': 'Month-to-month', 'PaperlessBilling': 'Yes', 'PaymentMethod': 'Electronic check', 'MonthlyCharges': 80.0}
NO_NET = {**FIBER, 'InternetService': 'No', 'OnlineSecurity': D.NO_INTERNET, 'OnlineBackup': D.NO_INTERNET, 'DeviceProtection': D.NO_INTERNET,
          'TechSupport': D.NO_INTERNET, 'StreamingTV': D.NO_INTERNET, 'StreamingMovies': D.NO_INTERNET, 'MonthlyCharges': 20.0}


@pytest.fixture(scope='session')
def frame():
    return D.load_clean()


@pytest.fixture(scope='session')
def meta(frame):
    return {'inputs': D.INPUTS, 'numeric': D.NUMERIC, 'categorical': D.CATEGORICAL, 'levels': D.levels(frame)}


@pytest.fixture(scope='session')
def bundle():
    from src import serving as S
    try:
        return S.load_bundle()
    except FileNotFoundError:
        pytest.skip('models/ not trained yet (python -m src.train)')


@pytest.fixture
def policy():
    from src import guardrails as G
    from src import serving as S
    return G.validate_policy(S.load_yaml(ROOT / 'config' / 'policy.yaml'))


@pytest.fixture
def limits():
    """Made-up limits for tests that need numbers (the real policy ships with them unset)."""
    from src import guardrails as G
    from src import serving as S
    raw = S.load_yaml(ROOT / 'config' / 'policy.yaml')
    return G.validate_policy({**raw, 'max_discount_pct_per_customer_per_quarter': 15, 'max_contacts_per_month': 2})
