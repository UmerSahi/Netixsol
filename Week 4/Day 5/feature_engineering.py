"""
Feature engineering, kept in its own importable module (not defined inline
in the notebook) so that joblib can unpickle the saved pipeline's
FunctionTransformer in a separate process later -- e.g. from inference.py
run as a standalone script or batch job days after this notebook's kernel
has shut down. A function defined only in a notebook's __main__ namespace
cannot be resolved by pickle outside that same running kernel.
"""
import numpy as np
import pandas as pd


def engineer_features(df):
    df = df.copy()

    df["age_bucket"] = pd.cut(
        df["age"], bins=[0, 25, 35, 50, 65, 100],
        labels=["18-25", "26-35", "36-50", "51-65", "65+"]
    )

    df["hours_bucket"] = pd.cut(
        df["hours-per-week"], bins=[0, 20, 40, 60, 100],
        labels=["Part-Time", "Full-Time", "Overtime", "Extreme"]
    )

    df["has_capital_gain"] = (df["capital-gain"] > 0).astype(int)
    df["log_capital_gain"] = np.log1p(df["capital-gain"].fillna(0))

    higher = ["Bachelors", "Masters", "Doctorate", "Prof-school"]
    df["higher_education"] = df["education"].isin(higher).astype(int)

    df["education_hours"] = df["education-num"] * df["hours-per-week"]

    return df
