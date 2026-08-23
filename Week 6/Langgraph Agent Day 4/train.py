"""
train.py
========
Run this once before first use: python train.py

No configuration needed -- data_layer.py and model_training.py auto-detect
the `data/` and `artifacts/` folders next to this file. If you keep your
CSVs somewhere else, set AFL_DATA_DIR before running this (see README.md).
"""
from model_training import (
    train_match_winner_model,
    train_top_disposals_model,
    train_expected_disposals_model,
)

if __name__ == "__main__":
    print("Training match winner model...")
    print(train_match_winner_model())
    print()
    print("Training player top-disposals model...")
    print(train_top_disposals_model())
    print()
    print("Training player expected-disposals model...")
    print(train_expected_disposals_model())
    print()
    print("All models trained and saved to ./artifacts")
