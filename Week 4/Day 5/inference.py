"""
Standalone inference script for the Adult Income capstone model.

Usage:
    from inference import load_pipeline, predict

    bundle = load_pipeline("capstone_income_pipeline.joblib")
    result = predict(bundle, {"age": 42, "workclass": "Private", ...})
"""
import warnings
import joblib
import numpy as np
import pandas as pd


class InputValidationError(ValueError):
    """Raised when raw input rows don't match what the pipeline expects."""


def load_pipeline(path: str = "capstone_income_pipeline.joblib") -> dict:
    """Load the saved pipeline bundle (pipeline + threshold + metadata)."""
    return joblib.load(path)


def _to_dataframe(rows, expected_columns):
    """Accept a dict, a list of dicts, or a DataFrame; return a validated DataFrame."""
    if isinstance(rows, pd.DataFrame):
        df = rows.copy()
    elif isinstance(rows, dict):
        df = pd.DataFrame([rows])
    elif isinstance(rows, list):
        df = pd.DataFrame(rows)
    else:
        raise InputValidationError(
            f"Unsupported input type {type(rows)}; expected dict, list[dict], or DataFrame."
        )

    missing = set(expected_columns) - set(df.columns)
    if missing:
        raise InputValidationError(f"Missing required columns: {sorted(missing)}")

    # Extra columns are dropped rather than rejected -- unseen categories inside
    # existing columns are handled by the pipeline's OneHotEncoder(handle_unknown="ignore"),
    # but *extra columns the pipeline was never trained on* would break the
    # ColumnTransformer's column-name matching, so they're excluded explicitly here.
    return df[expected_columns]


def _global_importance_fallback(classifier_step, feature_names, n):
    """Global (not row-specific) fallback, clearly labeled as such in its output."""
    if hasattr(classifier_step, "feature_importances_"):
        importances = classifier_step.feature_importances_
        top_idx = np.argsort(importances)[::-1][:n]
        return [
            {
                "feature": feature_names[i] if i < len(feature_names) else f"feature_{i}",
                "importance": float(importances[i]),
                "note": "global importance fallback (not row-specific)",
            }
            for i in top_idx
        ]
    return [{"note": "no contribution explanation available (shap missing, no feature_importances_)"}]


def _top_contributing_features(pipeline, row_df, n=3):
    """
    Best-effort top-N contributing features for a single row.

    Tries SHAP TreeExplainer first (most accurate); falls back to a
    global-importance approximation only if shap is genuinely unavailable.
    A structural bug (e.g. an unexpected pipeline shape) is NOT silently
    swallowed as if it were a missing dependency -- it's logged via
    warnings.warn so it surfaces in monitoring instead.

    Note on pipeline structure: `feature_engineering` and `preprocessing`
    are separate top-level steps (not one nested step), because the
    pipeline may have been built with imblearn's Pipeline, which rejects a
    nested Pipeline-within-a-Pipeline step.
    """
    feature_engineering_step = pipeline.named_steps["feature_engineering"]
    preprocessing_step = pipeline.named_steps["preprocessing"]
    classifier_step = pipeline.named_steps["classifier"]

    ohe = preprocessing_step.named_transformers_["categorical"].named_steps["encoder"]
    numeric_cols = preprocessing_step.transformers_[0][2]
    categorical_cols = preprocessing_step.transformers_[1][2]
    feature_names = list(numeric_cols) + ohe.get_feature_names_out(categorical_cols).tolist()

    try:
        import shap
    except ImportError:
        return _global_importance_fallback(classifier_step, feature_names, n)

    try:
        row_fe = feature_engineering_step.transform(row_df)
        transformed = preprocessing_step.transform(row_fe)

        explainer = shap.TreeExplainer(classifier_step)
        shap_values = explainer.shap_values(transformed)
        values = shap_values[1] if isinstance(shap_values, list) else shap_values
        row_shap = pd.Series(np.ravel(values[0]), index=feature_names)
        top = row_shap.abs().sort_values(ascending=False).head(n)
        return [{"feature": f, "shap_value": float(row_shap[f])} for f in top.index]
    except Exception as e:
        warnings.warn(
            f"SHAP explanation failed ({type(e).__name__}: {e}); falling back to global "
            f"feature importances. This may indicate a real bug (e.g. a pipeline "
            f"structure mismatch), not just a missing dependency -- investigate if this "
            f"appears in production logs.",
            RuntimeWarning,
        )
        return _global_importance_fallback(classifier_step, feature_names, n)


def predict(bundle: dict, rows, top_n_features: int = 3):
    """
    Run inference on one or more raw input rows.

    Parameters
    ----------
    bundle : dict returned by load_pipeline()
    rows   : dict, list[dict], or DataFrame of RAW (unprocessed) input rows
    top_n_features : how many top contributing features to return per row

    Returns
    -------
    list[dict] -- one entry per input row:
        {"probability": float, "predicted_class": int, "top_features": [...]}
    """
    pipeline = bundle["pipeline"]
    threshold = bundle["threshold"]
    expected_columns = bundle["raw_input_columns"]

    df = _to_dataframe(rows, expected_columns)

    probabilities = pipeline.predict_proba(df)[:, 1]
    predictions = (probabilities >= threshold).astype(int)

    results = []
    for i in range(len(df)):
        row_df = df.iloc[[i]]
        results.append({
            "probability": float(probabilities[i]),
            "predicted_class": int(predictions[i]),
            "threshold_used": threshold,
            "top_features": _top_contributing_features(pipeline, row_df, n=top_n_features),
        })
    return results
