"""Raw-preserving automatic naming, using temporary synthetic data only."""

from pathlib import Path
import unittest
from unittest.mock import patch

import test_incremental_pipeline as fixtures
from atlas_pipeline.pipeline import cleaned_relative_path, scan_root
from atlas_pipeline.plot_data import PlotDataService
from atlas_pipeline.state import ProductState
from merge_BIN import extract_lot_wafer_pairs_sorted


class CleanedNamingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineIntegrationTests()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def emulate_old_output(self):
        record = self.f._records()[0]
        old = self.f.lot / "summary_cleaning_data" / "WL111_LOT001_1#_CP_summary_cleaning.csv"
        (self.f.product / record["cleaned_path"]).rename(old)
        record["cleaned_path"] = old.relative_to(self.f.product).as_posix()
        state = ProductState(self.f.product)
        try:
            key = scan_root(self.f.root, self.f.config_path).products[0].tasks[0].key
            state.save_wafer(key, record)
        finally:
            state.close()
        return old

    def test_vendor_originals_need_no_rename_and_provenance_is_preserved(self):
        self.f.document["products"]["WL111"]["naming"]["regex"] = (
            r"^VENDORX-CP_(?P<lot>[^_]+)_(?P<wafer>\d+)#_(?:CP|RT\d+)\.csv$"
        )
        self.f._save_config()
        originals = []
        for suffix, rows, time in (("CP", fixtures.INITIAL_ROWS, "2026-09-17 10:00:00"),
                                   ("RT1", fixtures.RETEST_ROWS, "2026-09-17 11:00:00")):
            original = self.f._write_csv(wafer="01", suffix=suffix, rows=rows, ending=time)
            original = original.rename(self.f.lot / f"VENDORX-CP_LOT001_01#_{suffix}.csv")
            originals.append((original, original.read_bytes(), original.stat().st_mtime_ns))
        result = self.f._run()
        self.assertEqual((result.processed, result.failed), (1, 0))
        self.assertEqual(self.f._records()[0]["cleaned_path"],
                         "LOT001/summary_cleaning_data/WL111_LOT001_W01_summary_cleaning.csv")
        output = self.f.product / self.f._records()[0]["cleaned_path"]
        for original, content, modified in originals:
            self.assertEqual(original.read_bytes(), content)
            self.assertEqual(original.stat().st_mtime_ns, modified)
            self.assertIn(original.name, output.read_text(encoding="utf-8"))
        self.assertEqual(len(list(output.parent.glob("*.csv"))), 1)
        self.assertEqual(self.f._bin_rows()[0]["总良率"], 1)
        service = PlotDataService(self.f.config_path)
        refs = service.catalog(self.f.root).wafers
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0].available)
        self.assertEqual(refs[0].label, "WL111_LOT001_W01")

    def test_existing_demo_output_is_recomputed_and_archived_then_skipped(self):
        raw = self.f._write_csv()
        content = raw.read_bytes()
        self.f._run()
        old = self.emulate_old_output()
        old_content = old.read_bytes()
        plan = scan_root(self.f.root, self.f.config_path)
        self.assertEqual(plan.products[0].tasks[0].action, "OUTPUT_NAME_CHANGED")
        self.assertTrue(old.exists())  # Preview never renames or creates files.
        self.assertFalse((self.f.product / ".atlas/retired_cleaned").exists())
        service = PlotDataService(self.f.config_path)
        self.assertEqual(service.catalog(self.f.root).wafers[0].status, "stale")
        result = self.f._run()
        self.assertEqual((result.processed, result.skipped, result.failed), (1, 0, 0))
        self.assertFalse(old.exists())
        archives = list((self.f.product / ".atlas/retired_cleaned").rglob("*.csv"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), old_content)
        self.assertIn("OLD_OUTPUT_ARCHIVED", Path(result.log_path).read_text(encoding="utf-8"))
        self.assertEqual(raw.read_bytes(), content)
        self.assertEqual(len(self.f._bin_rows()), 1)
        self.assertTrue(service.catalog(self.f.root).wafers[0].available)
        with patch("atlas_pipeline.pipeline.merge_and_clean", side_effect=AssertionError("must skip")):
            repeat = self.f._run()
        self.assertEqual((repeat.processed, repeat.skipped, repeat.failed), (0, 1, 0))

    def test_failed_recalculation_keeps_old_output_and_does_not_archive(self):
        self.f._write_csv()
        self.f._run()
        old = self.emulate_old_output()
        content = old.read_bytes()
        with patch("atlas_pipeline.pipeline.merge_and_clean", side_effect=ValueError("synthetic failure")):
            result = self.f._run()
        self.assertEqual(result.failed, 1)
        self.assertEqual(old.read_bytes(), content)
        self.assertFalse((self.f.product / ".atlas/retired_cleaned").exists())
        self.assertEqual(self.f._bin_rows(), [])

    def test_unregistered_target_is_not_overwritten(self):
        self.f._write_csv()
        target = self.f.product / cleaned_relative_path("WL111", "LOT001", "1")
        target.parent.mkdir()
        target.write_text("manual output", encoding="utf-8")
        result = self.f._run()
        self.assertEqual((result.processed, result.failed), (0, 1))
        self.assertEqual(target.read_text(encoding="utf-8"), "manual output")
        self.assertIn("拒绝覆盖", result.errors[0])

    def test_modified_old_file_is_not_moved(self):
        self.f._write_csv()
        self.f._run()
        old = self.emulate_old_output()
        old.write_text("manual edit", encoding="utf-8")
        result = self.f._run()
        self.assertEqual((result.processed, result.failed), (1, 0))
        self.assertEqual(old.read_text(encoding="utf-8"), "manual edit")
        self.assertIn("旧清理文件已被修改", Path(result.log_path).read_text(encoding="utf-8"))

    def test_split_files_are_one_output_with_padded_wafer(self):
        self.f._write_csv(wafer="02", suffix="CP_part1", rows=fixtures.INITIAL_ROWS[:1], ending=None)
        self.f._write_csv(wafer=2, suffix="CP_part2", rows=fixtures.INITIAL_ROWS[1:], ending=None)
        result = self.f._run()
        self.assertEqual((result.processed, result.failed), (1, 0))
        output = self.f.product / self.f._records()[0]["cleaned_path"]
        self.assertEqual(output.name, "WL111_LOT001_W02_summary_cleaning.csv")
        self.assertEqual(extract_lot_wafer_pairs_sorted(str(output.parent)), [("LOT001", "02")])
        self.assertEqual(len(list(output.parent.glob("*.csv"))), 1)

    def test_dotted_lot_and_output_name_validation(self):
        self.assertEqual(cleaned_relative_path("WL111", "LOT001.1", "025").as_posix(),
                         "LOT001.1/summary_cleaning_data/WL111_LOT001.1_W25_summary_cleaning.csv")
        for lot in ("../LOT", "LOT?", "LOT.", "LOT "):
            with self.subTest(lot=lot), self.assertRaises(ValueError):
                cleaned_relative_path("WL111", lot, "1")


if __name__ == "__main__":
    unittest.main()
