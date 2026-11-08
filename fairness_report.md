# Fairness report

Audit on the held-out test split (n = 1409) at flag threshold 0.315 (placeholder_f1_optimal_on_calibrated_oof, PLACEHOLDER). Computed with Fairlearn `MetricFrame`.
`gender` is audited only; it is not a model input. A gap is max - min across the groups of one attribute; gaps **above 0.10 are in bold with a warning marker**. Groups with n < 100 are marked (*) and their gaps are unreliable.

## Gaps

| Attribute | Recall gap | Precision gap | Flag-rate gap | Smallest group n |
|---|---|---|---|---|
| SeniorCitizen | **⚠ 0.164 (1 vs 0)** | **⚠ 0.104 (1 vs 0)** | **⚠ 0.303 (1 vs 0)** | 222 |
| Partner | **⚠ 0.184 (No vs Yes)** | 0.070 (No vs Yes) | **⚠ 0.248 (No vs Yes)** | 673 |
| Dependents | **⚠ 0.256 (No vs Yes)** | 0.042 (No vs Yes) | **⚠ 0.276 (No vs Yes)** | 431 |
| gender | 0.043 (Male vs Female) | 0.020 (Female vs Male) | 0.007 (Female vs Male) | 687 |

## By group

| Attribute | Group | n | Churn rate | Flag rate | Recall | Precision |
|---|---|---|---|---|---|---|
| SeniorCitizen | 0 | 1187 | 0.233 | 0.323 | 0.714 | 0.514 |
| SeniorCitizen | 1 | 222 | 0.441 | 0.626 | 0.878 | 0.619 |
| Partner | No | 736 | 0.337 | 0.489 | 0.819 | 0.564 |
| Partner | Yes | 673 | 0.187 | 0.241 | 0.635 | 0.494 |
| Dependents | No | 978 | 0.310 | 0.455 | 0.805 | 0.548 |
| Dependents | Yes | 431 | 0.165 | 0.179 | 0.549 | 0.506 |
| gender | Female | 687 | 0.281 | 0.374 | 0.736 | 0.553 |
| gender | Male | 722 | 0.251 | 0.367 | 0.779 | 0.532 |

## Reading this

- Flag-rate gaps partly reflect real differences in churn rate (see the churn-rate column); recall and precision gaps are the ones to investigate.
- The test split is small (1,409 rows); confidence intervals on a single group are wide. These are screening results, not proof of (un)fairness.
- No mitigation has been applied. Any change to thresholds by group would need a documented policy decision.
