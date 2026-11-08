# Uses whichever python is on PATH (activate your venv first). On Windows without make, run the python commands directly.
PY ?= python

run:            ## start the app on http://localhost:8501
	$(PY) -m streamlit run app.py

train:          ## full training: nested CV, calibration, bootstrap CIs, MLflow (about 30-45 min), then the SIMULATED uplift model
	$(PY) -m src.train
	$(PY) -m src.uplift

train-quick:    ## fast smoke training into models/ (smaller searches, 200 bootstrap resamples); not for reporting
	$(PY) -m src.train --quick --boot 200 --no-mlflow
	$(PY) -m src.uplift

test:
	$(PY) -m pytest -q

docker:
	docker build -t evergreen .

.PHONY: run train train-quick test docker
