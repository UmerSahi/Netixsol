# Model Training Report

_Generated 2026-08-22 20:25_

All models are split strictly chronologically by season (no shuffling).

| Task | Model | Train seasons | Val season | Test seasons | Val metric | Test metric |
|---|---|---|---|---|---|---|
| Match winner (classification) | `logistic_regression` | <= 2022 | 2023 | [2024, 2025] | roc_auc=0.7023 | roc_auc=0.7143 |
| Player top-disposals (classification) | `random_forest` | <= 2022 | 2023 | [2024, 2025] | roc_auc=0.8967 | roc_auc=0.8953 |
| Player expected disposals (regression) | `hist_gradient_boosting` | <= 2022 | 2023 | [2024, 2025] | mae=3.9737 | mae=3.8943 |

### Match winner (classification)

- **Target column:** `home_win`
- **Feature count:** 64
- **Notes:** val_acc=0.6636, test_acc=0.6589. Draws dropped from target (undefined for binary classification).

### Player top-disposals (classification)

- **Target column:** `is_match_top_disposals`
- **Feature count:** 52
- **Notes:** Binary target = top disposal-getter within player's own team for that match.

### Player expected disposals (regression)

- **Target column:** `disposals`
- **Feature count:** 52
- **Notes:** Regression output is a predicted disposal COUNT, not a probability.
