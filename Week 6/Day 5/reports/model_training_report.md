# Model Training Report

_Generated 2026-08-24 00:41_

All models are split strictly chronologically by season (no shuffling).

| Task | Model | Train seasons | Val season | Test seasons | Val metric | Test metric |
|---|---|---|---|---|---|---|
| Match winner (classification) | `logistic_regression` | <= 2022 | 2023 | [2024, 2025] | roc_auc=0.7023 | roc_auc=0.7143 |
| Player top-disposals (classification) | `random_forest` | <= 2022 | 2023 | [2024, 2025] | roc_auc=0.8967 | roc_auc=0.8953 |
| Player expected disposals (regression) | `hist_gradient_boosting` | <= 2022 | 2023 | [2024, 2025] | mae=3.9737 | mae=3.8943 |
| Player expected goals (regression) | `hist_gradient_boosting` | <= 2022 | 2023 | [2024, 2025] | mae=0.5270 | mae=0.5372 |
| Player expected kicks (regression) | `hist_gradient_boosting` | <= 2022 | 2023 | [2024, 2025] | mae=2.9905 | mae=2.9825 |
| Player expected marks (regression) | `hist_gradient_boosting` | <= 2022 | 2023 | [2024, 2025] | mae=1.8440 | mae=1.8883 |
| Player expected handballs (regression) | `hist_gradient_boosting` | <= 2022 | 2023 | [2024, 2025] | mae=2.4654 | mae=2.4546 |
| Player expected tackles (regression) | `hist_gradient_boosting` | <= 2022 | 2023 | [2024, 2025] | mae=1.4501 | mae=1.4542 |

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
- **Notes:** Regression output is a predicted disposals COUNT, not a probability.

### Player expected goals (regression)

- **Target column:** `goals`
- **Feature count:** 52
- **Notes:** Regression output is a predicted goals COUNT, not a probability.

### Player expected kicks (regression)

- **Target column:** `kicks`
- **Feature count:** 52
- **Notes:** Regression output is a predicted kicks COUNT, not a probability.

### Player expected marks (regression)

- **Target column:** `marks`
- **Feature count:** 52
- **Notes:** Regression output is a predicted marks COUNT, not a probability.

### Player expected handballs (regression)

- **Target column:** `handballs`
- **Feature count:** 52
- **Notes:** Regression output is a predicted handballs COUNT, not a probability.

### Player expected tackles (regression)

- **Target column:** `tackles`
- **Feature count:** 52
- **Notes:** Regression output is a predicted tackles COUNT, not a probability.
