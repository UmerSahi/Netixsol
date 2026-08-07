"""
Unit tests for inference.py.

Run with:  python -m pytest test_inference.py -v
(or python -m unittest test_inference -v)
"""
import unittest
import pandas as pd
from inference import load_pipeline, predict, _to_dataframe, InputValidationError


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.bundle = load_pipeline("capstone_income_pipeline.joblib")
        self.expected_columns = self.bundle["raw_input_columns"]
        self.threshold = self.bundle["threshold"]
        # A minimal valid row built from the expected schema; values are
        # deliberately generic/plausible rather than copied from real data.
        self.valid_row = {col: "Private" if col in
                           ("workclass", "education", "marital-status", "occupation",
                            "relationship", "race", "sex", "native-country")
                           else 30 for col in self.expected_columns}

    def test_missing_column_raises(self):
        incomplete_row = dict(self.valid_row)
        removed_col = self.expected_columns[0]
        del incomplete_row[removed_col]
        with self.assertRaises(InputValidationError):
            _to_dataframe(incomplete_row, self.expected_columns)

    def test_unseen_category_does_not_raise(self):
        # OneHotEncoder(handle_unknown="ignore") should absorb a category the
        # encoder never saw during training, rather than the pipeline erroring out.
        weird_row = dict(self.valid_row)
        weird_row["workclass"] = "Totally-Unseen-Category-XYZ"
        try:
            result = predict(self.bundle, weird_row)
        except Exception as e:
            self.fail(f"predict() raised {type(e).__name__} on an unseen category: {e}")
        self.assertEqual(len(result), 1)

    def test_valid_input_output_schema(self):
        result = predict(self.bundle, self.valid_row)
        self.assertEqual(len(result), 1)
        row_result = result[0]
        self.assertIn("probability", row_result)
        self.assertIn("predicted_class", row_result)
        self.assertIn("threshold_used", row_result)
        self.assertIn("top_features", row_result)
        self.assertTrue(0.0 <= row_result["probability"] <= 1.0)
        self.assertIn(row_result["predicted_class"], (0, 1))

    def test_list_of_dicts_input(self):
        result = predict(self.bundle, [self.valid_row, self.valid_row])
        self.assertEqual(len(result), 2)

    def test_dataframe_input(self):
        df = pd.DataFrame([self.valid_row])
        result = predict(self.bundle, df)
        self.assertEqual(len(result), 1)

    def test_threshold_correctness(self):
        # predicted_class must be exactly the frozen bundle threshold applied to probability --
        # never an independently-defined threshold inside inference.py.
        result = predict(self.bundle, self.valid_row)
        row_result = result[0]
        self.assertEqual(row_result["threshold_used"], self.threshold)
        expected_class = int(row_result["probability"] >= self.threshold)
        self.assertEqual(row_result["predicted_class"], expected_class)

    def test_top3_contributions(self):
        result = predict(self.bundle, self.valid_row)
        top_features = result[0]["top_features"]
        self.assertTrue(len(top_features) > 0, "top_features must be non-empty for a valid input")
        self.assertLessEqual(len(top_features), 3, "at most 3 contributing features expected")


if __name__ == "__main__":
    unittest.main()
