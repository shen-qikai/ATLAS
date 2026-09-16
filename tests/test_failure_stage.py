"""Synthetic regression tests for die failure rates and opaque cache identity."""

import json
import sqlite3
import unittest
from pathlib import Path

from openpyxl import load_workbook
import test_incremental_pipeline as fixtures
from atlas_pipeline.pipeline import scan_root, wafer_key
from atlas_pipeline.state import read_snapshot, ProductState


class FailureStageTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineIntegrationTests()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def report_row(self, sheet="测试项失效率"):
        book = load_workbook(self.f.product / "WL111_测试项失效率.xlsx", read_only=True, data_only=True)
        try:
            rows = list(book[sheet].values)
            return dict(zip(rows[0], rows[4] if sheet == "测试项失效率" else rows[1]))
        finally:
            book.close()

    def test_fail_rate_total_die_denominator_and_missing_are_separate(self):
        rows = [list(row) for row in fixtures.INITIAL_ROWS]
        rows.append([1, "TRUE", 2, 0, 1, "", "", "", ""])
        self.f.document["products"]["WL111"]["yield"] = {"denominator": "ValidOnly"}
        self.f._save_config()
        self.f._write_csv(rows=rows)
        self.f._run()
        row = self.report_row()
        self.assertAlmostEqual(row["VOUT"], 1 / 3)
        self.assertEqual(row["晶圆总die数量"], 3)
        self.assertNotIn("Stage", row)
        self.assertEqual(self.report_row("测试项失效die数")["VOUT"], 1)
        self.assertEqual(self.report_row("测试项缺测die数")["VOUT"], 1)

    def test_boundary_is_pass_and_no_spec_is_not_zero_failure(self):
        rows = [[1, "TRUE", 0, 0, 1, 0, 5, 1, "abc"],
                [1, "TRUE", 1, 0, 1, 10, 4, 2, "def"]]
        self.f._write_csv(rows=rows)
        self.f._run()
        row = self.report_row()
        self.assertEqual(row["VOUT"], 0)
        self.assertEqual(row["UPPER_ONLY"], 0)
        self.assertEqual(row["LOWER_ONLY"], 0)
        self.assertEqual(row["TEXT"], "No_SPEC")

    def test_repeated_rt_records_count_final_dies_not_records(self):
        self.f.document["products"]["WL111"]["cleaning"] = {"mode": "rt"}
        self.f._save_config()
        self.f._write_csv()
        self.f._write_csv(suffix="RT1", rows=[[1, "FALSE", 0, 0, 2, 12, 6, 0, "def"]],
                          ending="2026-09-16 11:00:00")
        self.f._run()
        row = self.report_row()
        self.assertEqual(row["记录数"], 2)
        self.assertEqual(row["晶圆总die数量"], 1)
        self.assertEqual(row["VOUT"], 1)

    def test_filename_cp_does_not_assert_configured_ft_and_identity_is_neutral(self):
        self.f.document["pipeline_defaults"]["automation"]["test_stage"] = "FT"
        self.f._save_config()
        self.f._write_csv()
        result = self.f._run()
        self.assertEqual((result.processed, result.failed), (1, 0))
        snapshot = read_snapshot(self.f.product)
        self.assertEqual(next(iter(snapshot["wafers"])), wafer_key("LOT001", "1", "DATA"))
        self.assertNotIn("Stage=", Path(result.log_path).read_text(encoding="utf-8"))

    def test_legacy_cp_key_reused_and_cached_reports_upgrade_without_raw_recalculation(self):
        self.f._write_csv()
        self.f._run()
        snapshot = read_snapshot(self.f.product)
        key, record = next(iter(snapshot["wafers"].items()))
        record["stage"] = "CP"
        record["metrics"].pop("wafer_total_die_count")
        for test in record["metrics"]["tests"].values():
            for field in ("failed_die_count", "valid_die_count", "missing_die_count"):
                test.pop(field)
        legacy_key = wafer_key("LOT001", "1", "CP")
        with sqlite3.connect(self.f.product / ".atlas/state.sqlite") as connection:
            connection.execute("UPDATE wafers SET key=?, payload=? WHERE key=?",
                               (legacy_key, json.dumps(record), key))
        old = self.f.product / "WL111_测试项良率.xlsx"
        (self.f.product / "WL111_测试项失效率.xlsx").rename(old)
        original = old.read_bytes()
        plan = scan_root(self.f.root, self.f.config_path)
        self.assertEqual(plan.products[0].tasks[0].action, "SKIP")
        self.assertEqual(plan.products[0].tasks[0].key, legacy_key)
        result = self.f._run()
        self.assertEqual((result.processed, result.skipped), (0, 1))
        self.assertEqual(list(read_snapshot(self.f.product)["wafers"]), [legacy_key])
        self.assertEqual(old.read_bytes(), original)
        self.assertEqual(self.report_row()["VOUT"], 0.5)

    def test_ambiguous_legacy_identifiers_are_not_silently_combined(self):
        self.f._write_csv()
        self.f._run()
        snapshot = read_snapshot(self.f.product)
        record = dict(next(iter(snapshot["wafers"].values())))
        record["stage"] = "CP"
        state = ProductState(self.f.product)
        try:
            state.save_wafer(wafer_key("LOT001", "1", "CP"), record)
        finally:
            state.close()
        plan = scan_root(self.f.root, self.f.config_path)
        self.assertTrue(plan.products[0].errors)
        self.assertTrue(all(task.action == "INVALID" for task in plan.products[0].tasks))


if __name__ == "__main__":
    unittest.main()
