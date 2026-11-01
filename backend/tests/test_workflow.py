import shutil
import unittest
import uuid
from pathlib import Path

import pandas as pd

from app.main import (FILES, AnalyzeRequest, CleanRequest, Credentials, DemoRequest, ExperimentRequest,
                      run_analysis, clean, db, experiment, export_dataset, profile,
                      load_demo, register, report, upload)
from app.detectors.engine import analyze as run_detectors, compare_datasets


class DetectorTests(unittest.TestCase):
    def test_missing_duplicates_outlier_and_consistency(self):
        df = pd.DataFrame({"amount": [1, 2, 3, 4, 5, 6, 7, 8, 1000, None], "kind": ["a", "a", "b", "b", "a", "b", "a", "b", "a", " ? "]})
        df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
        findings = run_detectors(df)
        categories = {f["category"] for f in findings}
        self.assertIn("missing", categories)
        self.assertIn("duplicates", categories)
        self.assertIn("outliers", categories)
        self.assertIn("consistency", categories)
        self.assertEqual(profile(df)["missing"], 2)
        self.assertEqual(profile(df)["rows"], 11)

    def test_target_leakage_candidate_and_distribution_comparison(self):
        root = Path(__file__).resolve().parents[2] / "sample_datasets"
        train = pd.read_csv(root / "classification_train.csv")
        findings = run_detectors(train, target="label")
        self.assertIn("leakage", {f["category"] for f in findings})
        shifted = pd.read_csv(root / "classification_shifted.csv")
        comparison = compare_datasets(train, shifted)
        self.assertGreater(comparison["shared_columns"], 1)
        self.assertTrue(any(column["changed"] for column in comparison["columns"]))

    def test_exact_overlap_normalizes_integer_and_float_rows(self):
        original = pd.DataFrame({"id": [1, 2, 3], "value": [10, 20, 30]})
        cleaned = pd.DataFrame({"id": [1.0, 2.0, 4.0], "value": [10.0, 20.0, 40.0]})
        self.assertEqual(compare_datasets(original, cleaned)["exact_shared_records"], 2)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.email = f"test-{uuid.uuid4().hex}@example.test"
        self.auth = register(Credentials(email=self.email, password="local-test-password-123"))
        with db() as c:
            row = c.execute("SELECT * FROM users WHERE email=?", (self.email,)).fetchone()
            self.user = row; self.user_id = row["id"]
        self.dataset_id = None

    def tearDown(self):
        if self.dataset_id:
            with db() as c:
                c.execute("DELETE FROM operations WHERE user_id=?", (self.user_id,))
                c.execute("DELETE FROM experiments WHERE user_id=?", (self.user_id,))
                c.execute("DELETE FROM datasets WHERE user_id=?", (self.user_id,))
        with db() as c: c.execute("DELETE FROM users WHERE id=?", (self.user_id,))
        shutil.rmtree(FILES / self.user_id, ignore_errors=True)

    def test_upload_analyze_clean_experiment_export_report(self):
        source = pd.read_csv(Path(__file__).resolve().parents[2] / "sample_datasets" / "customer_quality.csv")
        payload = source.to_csv(index=False).encode()
        result = upload(payload, "review.csv", self.user)
        self.dataset_id = result["dataset"]["id"]
        self.assertGreater(result["profile"]["rows"], 20)

        analysis = run_analysis(self.dataset_id, AnalyzeRequest(target="segment"), self.user)
        categories = {f["category"] for f in analysis["findings"]}
        self.assertTrue({"missing", "duplicates", "outliers", "consistency", "imbalance"}.issubset(categories))
        self.assertIn("health_score", analysis)

        cleaned = clean(self.dataset_id, CleanRequest(action="impute", strategy="median", columns=["annual_spend"]), self.user)
        self.assertTrue(cleaned["profile"]["missing"] < result["profile"]["missing"])

        model = experiment(ExperimentRequest(target="segment"), self.dataset_id, self.user)
        self.assertIn("f1_macro", model["metrics"])

        csv_response = export_dataset(self.dataset_id, self.user)
        self.assertIn("text/csv", csv_response.media_type)
        self.assertIn(b"customer_code", csv_response.body[:300])

        pdf_response = report(self.dataset_id, self.user)
        self.assertEqual(pdf_response.media_type, "application/pdf")
        self.assertEqual(pdf_response.body[:4], b"%PDF")

    def test_demo_sample_loads_through_profile_pipeline(self):
        result = load_demo(DemoRequest(name="student_performance.csv"), self.user)
        self.dataset_id = result["dataset"]["id"]
        self.assertEqual(result["profile"]["rows"], 100)
        analysis = run_analysis(self.dataset_id, AnalyzeRequest(target="result"), self.user)
        categories = {f["category"] for f in analysis["findings"]}
        self.assertIn("missing", categories)
        self.assertIn("imbalance", categories)


if __name__ == "__main__": unittest.main()
