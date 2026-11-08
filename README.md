# Evergreen churn service

Calibrated XGBoost churn model (isotonic, 5-fold ensemble) behind a FastAPI service, plus a digital-twin what-if tool.
Rules for this project are in `CLAUDE.md`.

## Setup and tests

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```

## Run the API

```bash
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Docs at http://localhost:8000/docs. The model bundle is loaded once at startup.

| Endpoint | Purpose |
|---|---|
| `GET /health` | model loaded, library versions match, threshold + `threshold_source` |
| `POST /predict` | `{"customers": [ ...17 input fields... ]}` -> calibrated probability, `flagged`, `threshold_source`, SHAP reasons (flagged customers only). Bad input -> 422 |
| `GET /twin/scenarios` | the scenario library (`scenarios.yaml`) |
| `POST /twin/run` | `{"customer": {...}, "scenarios": [optional names]}` -> ranked scenario results + `not_applicable` list with reasons |
| `POST /recommend` | `{"customer": {...}, "account_state": {...}}` -> decision (`recommend` / `recommend_pending_approval` / `no_action` / `escalate_to_human`), the best permitted action and reasons. Combines risk, twin scenarios, **SIMULATED** uplift, guardrails and the cost threshold |
| `POST /message` | `{"customer", "first_name", "account_state", "approved_by"?}` -> a retention message for the offer `/recommend` approved. The LLM only phrases it; validated in code; logged to SQLite |
| `POST /simulate` | `{"customer": {...}, "changes": {"Contract": "One year", "discount_pct": 5}}` -> one result; same code path as `/twin/run` |
| `GET /meta` | form options (input columns, levels), threshold, labels and the saved evaluation from `churn_model_meta.json` (used by the web app) |
| `POST /score` | `{"rows": [raw CSV rows]}` -> each row validated on its own; scored rows plus `rejected` rows with the reason |
| `POST /explain` | `{"customer": {...}}` -> all 17 SHAP contributions, sorted |

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/twin/run -H 'content-type: application/json' -d @customer.json
```

## Web app (React, `web/`)

The frontend is a React + Vite single-page app styled after the whitepace SaaS theme. Pages: Overview, Score a customer,
What-if lab, Recommend & message, Score a file, Review queue, Model & fairness. It only calls the API above; every number
it shows comes from the API.

```bash
cd web && npm ci && npm run build        # writes web/dist; the API then serves it at http://localhost:8000/
cd web && npm run dev                    # hot-reload dev server on :5173, proxies API calls to :8000
cd web && npm test                       # vitest unit tests for the shared helpers
```

The Docker image builds the frontend in a Node stage, so one container serves the app and the API on port 8000.
If `CHURN_API_KEY` is set, the Review queue page asks for the key and keeps it for the browser tab only.

## Run with Docker

```bash
docker build -t evergreen-churn .
docker run --rm -p 8000:8000 evergreen-churn
curl -s localhost:8000/health            # the web app is at http://localhost:8000/
```

The review queue lives in SQLite (`CHURN_DB`, default `/srv/message_log.sqlite` in the container). Mount a volume for it
(`-v evergreen-data:/data -e CHURN_DB=/data/queue.sqlite`) or cases are lost when the container is replaced, and set
`CHURN_API_KEY` before exposing the queue beyond your own machine.

If your network intercepts TLS (antivirus "web shield", corporate proxy) `pip` (and `npm`) inside the build fail with
`CERTIFICATE_VERIFY_FAILED`. Pass your CA bundle as a build secret; it is used only during `npm ci` and `pip install` and is not kept in the image:
`docker build --secret id=cacert,src=ca-bundle.pem -t evergreen-churn .` (certifi's bundle plus the interceptor's root certificate).

The image contains the model artifacts, `config.yaml` and `scenarios.yaml`. To change business numbers without rebuilding, mount
your own file: `-v $PWD/config.yaml:/srv/config.yaml:ro`.

## Business numbers (`config.yaml`)

`offer_cost`, `customer_value`, `success_rate` are **null** until real figures exist. While null, the threshold is the labelled
PLACEHOLDER (F1-optimal on calibrated out-of-fold scores) and every response says `threshold_is_placeholder: true`.
With all three set, `threshold = offer_cost / (success_rate * customer_value)`.

Twin extras: `twin_effect_realization` (null by default; e.g. 0.3 multiplies every `risk_drop`, both raw and adjusted are returned) and
`scenario_costs` (per-scenario cost per customer; include the revenue a discount gives up). `expected_net_value` is null, with a reason,
until `success_rate`, `customer_value` and the scenario's cost are all configured.

## The twin is a sensitivity tool, not a causal estimate

Every twin result says `"interpretation": "model sensitivity, not a causal estimate"`. It shows how the model's score moves when inputs
change, nothing more. In particular, Contract changes mostly reflect *selection* (loyal customers choose long contracts), so the
real-world effect is likely much smaller; those results carry a warning. `confidence: "low"` means the plain logistic-regression
reference model disagrees in sign or by more than 0.10 on the risk drop. Only actionable fields can be edited; tenure, direct
MonthlyCharges edits and incoherent combinations (e.g. TechSupport=Yes with no internet) are rejected with 422.


## Fairness, uplift, guardrails, messages, dashboard, tracking

| Part | Run | Notes |
|---|---|---|
| Fairness audit | `python -m app.fairness` -> `fairness_report.md` | Fairlearn; recall / precision / flag rate by SeniorCitizen, Partner, Dependents and gender (audit only, not a model input); gaps over 0.10 highlighted with sample sizes |
| SIMULATED uplift | `python -m app.uplift` -> `churn_uplift_sim.joblib` | T-learner (two XGBoost models). The data has no treatment column, so treatment and effect are simulated from `uplift_simulation` in `config.yaml` (arbitrary assumptions, not findings). Labelled SIMULATED everywhere |
| Guardrails | `guardrails.yaml`, `app/guardrails.py` | Enforced in code on every `/recommend`. Vulnerable or escalated accounts are never contacted and go to a human. **The two numeric limits are null until you set them; while null (or while account state is not supplied) actions come back `needs_human_approval`, never auto-permitted.** Pressure-language list also gates messages |
| Messages | `POST /message`, `app/message.py` | Reads `ANTHROPIC_API_KEY` (model `claude-opus-5-5`, override with `CHURN_MESSAGE_MODEL`). No key, API error or a draft that mentions anything outside the approved offer -> deterministic template. Every message, its reasons and offer go to `message_log.sqlite` (`CHURN_DB` to move it) |
| Dashboard (legacy) | `streamlit run dashboard/app.py`; superseded by the React web app | Overview, customer lookup, twin lab, recommendation, editable message preview, review queue, fairness |
| MLflow | `train.py` logs every run (`--no-mlflow` to skip); `python -m app.tracking` back-fills the saved artifacts; `mlflow ui --backend-store-uri sqlite:///mlflow.db` | Tracking store is `mlflow.db` unless `MLFLOW_TRACKING_URI` is set |
| Drift | `python -m app.drift --build`, then `python -m app.drift customers.csv` | PSI of tenure, MonthlyCharges, Contract vs the training split; < 0.10 stable, 0.10-0.25 moderate, > 0.25 major (convention) |

Install everything for development with `pip install -r requirements-dev.txt` (the Docker image only needs `requirements.txt`).

## Running the whole flow (no hand-typing)

```
customer export (CSV / SQLite / API)  ->  score + recommend  ->  review queue  ->  validated message  ->  outbox CSV  ->  CRM sends
```

| Step | Command | What it does |
|---|---|---|
| Map the export | edit `pipeline.yaml` | Names of the id, first-name and account-state columns in the telco's own file. Defaults fit the Telco CSV |
| Load customers | `python -m app.pipeline run --source customers.csv` (or `--sqlite crm.db --query "select ..."`, or `POST /cases/ingest`) | Runs each customer through `/recommend`'s logic and stores a case. Bad rows become `invalid` cases with the reason; the rest carry on. A customer with an open case is not queued twice |
| Review | dashboard **Review queue**, `python -m app.pipeline queue --status pending_approval`, or `GET /cases` | Cases the guardrails mark `needs_human_approval` wait for a named person: `approve ID --by NAME`, or `reject ID --by NAME --reason ...` |
| Message | automatic on approval | The offer is the one already chosen; the words are the template or a validated Claude draft (`--llm`). Needs the customer's first name (`name ID Maria`) |
| Hand over | `python -m app.pipeline export` (or `POST /outbox/export`) | Writes `outbox/outbox_<time>.csv` (customer_ref, first name, message, offer) and marks the cases `exported`. **Nothing is sent from here**; the CRM sends and joins contact details on `customer_ref` |

Run `run` and `export` from cron / Windows Task Scheduler (for example `run` nightly, `export` each morning after review).
The queue lives in the same SQLite file as the message log (`CHURN_DB`). Set `CHURN_API_KEY` to require an `X-API-Key` header on the `/cases` and `/outbox` endpoints before exposing them beyond localhost.
Until `guardrails.yaml` limits and `config.yaml` business numbers are filled in, every flagged customer lands in `pending_approval`, and the same is true for any customer whose account state (vulnerable, escalated, contacts, discount used) is not in the export. That is intended.
