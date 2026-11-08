# ---- stage 1: build the React frontend (web/) ----
FROM node:24.11.1-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# Optional build secret `cacert`: a CA bundle for networks that intercept TLS (never stored in the image).
RUN --mount=type=secret,id=cacert \
    if [ -f /run/secrets/cacert ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/cacert; fi; \
    npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- stage 2: the API, which also serves the built frontend ----
FROM python:3.13-slim

# libgomp is required by xgboost / lightgbm wheels on slim images
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
# Optional build secret `cacert`: a CA bundle for networks that intercept TLS (never stored in the image).
#   docker build --secret id=cacert,src=ca-bundle.pem -t evergreen-churn .
RUN --mount=type=secret,id=cacert \
    if [ -f /run/secrets/cacert ]; then export PIP_CERT=/run/secrets/cacert; fi; \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY churn_serving.py config.yaml scenarios.yaml guardrails.yaml pipeline.yaml ./
COPY churn_model.joblib churn_xgb_explainer.joblib churn_logreg_ref.joblib churn_uplift_sim.joblib churn_model_meta.json ./
COPY --from=web /web/dist ./web/dist

ENV CHURN_DIR=/srv PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
