"""
train.py
========
Run this once before first use: python train.py

No configuration needed -- data_layer.py and model_training.py auto-detect
the `data/` and `artifacts/` folders next to this file. If you keep your
CSVs somewhere else, set AFL_DATA_DIR before running this (see README.md).

CAPSTONE ADDITIONS (v2): trains the full generalized set of player-stat
regressors (disposals, goals, kicks, marks, handballs, tackles) instead of
only disposals, so predict_top_player / predict_player_stat_value can
answer "predict Geelong's top goalkicker" etc. with a real trained model.
"""
from model_training import (
    train_match_winner_model,
    train_top_disposals_model,
    train_all_player_stat_regressors,
    PLAYER_STAT_TARGETS,
)

if __name__ == "__main__":
    print("Training match winner model...")
    print(train_match_winner_model())
    print()
    print("Training player top-disposals model...")
    print(train_top_disposals_model())
    print()
    print(f"Training player stat regressors ({', '.join(PLAYER_STAT_TARGETS)})...")
    results = train_all_player_stat_regressors()
    for stat_col, meta in results.items():
        print(f"  {stat_col}: {meta}")
    print()
    print("All models trained and saved to ./artifacts")
