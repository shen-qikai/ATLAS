"""Synthetic-only tests of catalog, cache, identity, and actual plot adapters."""

from dataclasses import replace
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from matplotlib.figure import Figure
from PIL import Image

from atlas_pipeline.plot_data import PlotDataService, numeric_frame
from atlas_pipeline.plot_jobs import PlotOptions, parse_bin_tasks, run_plot_job, safe_name, validate_options
import test_incremental_pipeline as fixtures


class DirectPlotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PipelineIntegrationTests()
        self.fixture.setUp()
        self.fixture._write_csv()
        self.fixture._run()
        self.service = PlotDataService(self.fixture.config_path)

    def tearDown(self):
        self.service.clear_cache()
        self.fixture.tearDown()

    def refs(self):
        return self.service.catalog(self.fixture.root).wafers

    def test_catalog_does_not_read_csv_or_hash_or_write_files(self):
        before = set(self.fixture.root.rglob("*"))
        with patch("atlas_pipeline.plot_data.pd.read_csv", side_effect=AssertionError("CSV read")), \
             patch("atlas_pipeline.plot_data.file_hash", side_effect=AssertionError("hash")):
            refs = self.refs()
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0].available)
        self.assertEqual(refs[0].label, "WL111_LOT001_W01")
        self.assertEqual(refs[0].record["metrics"]["effective_die_count"], 2)
        self.assertEqual(list(refs[0].tests), ["VOUT", "UPPER_ONLY", "LOWER_ONLY"])
        self.assertEqual(before, set(self.fixture.root.rglob("*")))

    def test_column_cache_reuses_subsets_and_returns_independent_frames(self):
        ref = self.refs()[0]
        frame, metadata = self.service.load_columns(ref, ["VOUT", "X_COORD"])
        self.assertEqual(frame["VOUT"].tolist(), ["2", "12"])
        self.assertEqual(metadata["VOUT"][:3], ["V", "0", "10"])
        frame.iloc[0, 0] = "999"
        metadata["VOUT"][0] = "wrong"
        reused, meta = self.service.load_columns(ref, ["VOUT"])
        self.assertEqual(reused["VOUT"].tolist(), ["2", "12"])
        self.assertEqual(meta["VOUT"][0], "V")
        self.assertEqual((self.service.csv_reads, self.service.cache_hits), (1, 1))
        previous_bytes = self.service.cache_bytes
        self.service.load_columns(ref, ["VOUT", "Y_COORD", "SOFT_BIN"])
        self.assertEqual(self.service.csv_reads, 2)
        self.assertGreater(self.service.cache_bytes, previous_bytes)
        self.assertEqual(self.service.cache_bytes, sum(entry.bytes for entry in self.service._cache.values()))
        self.service.load_columns(ref, ["VOUT", "X_COORD", "Y_COORD", "SOFT_BIN"])
        self.assertEqual(self.service.csv_reads, 2)

    def test_oversize_columns_do_not_exceed_cache_limit(self):
        service = PlotDataService(self.fixture.config_path, max_cache_bytes=1)
        ref = self.refs()[0]
        service.load_columns(ref, ["VOUT"])
        service.load_columns(ref, ["VOUT"])
        self.assertEqual(service.cache_bytes, 0)
        self.assertEqual(service.csv_reads, 2)

    def test_lru_eviction_and_clear(self):
        self.fixture._write_csv(wafer=2)
        self.fixture._run()
        refs = self.refs()
        self.service.load_columns(refs[0], ["VOUT"])
        self.service.max_cache_bytes = self.service.cache_bytes
        self.service.load_columns(refs[1], ["VOUT"])
        self.assertEqual(len(self.service._cache), 1)
        self.assertGreaterEqual(self.service.cache_bytes, 0)
        self.service.clear_cache()
        self.assertEqual((self.service.cache_bytes, len(self.service._cache)), (0, 0))

    def test_new_retest_is_stale_until_incremental_processing_and_invalidates_cache(self):
        old = self.refs()[0]
        self.service.load_columns(old, ["VOUT"])
        self.fixture._write_csv(suffix="RT1", rows=[[1, "TRUE", 1, 0, 1, 4, 4, 3, "def"]],
                                 ending="2026-09-16 11:00:00")
        self.assertEqual(self.refs()[0].status, "stale")
        with self.assertRaises(ValueError):
            self.service.validate_refs([old])
        self.fixture._run()
        updated = self.refs()[0]
        frame, _ = self.service.load_columns(updated, ["VOUT"])
        self.assertEqual(frame["VOUT"].tolist(), ["2", "4"])
        self.assertEqual(len(self.service._cache), 1)
        self.assertEqual(self.service.cache_bytes, sum(entry.bytes for entry in self.service._cache.values()))
        with self.assertRaises(ValueError):
            self.service.validate_refs([old])

    def test_source_change_missing_and_unknown_file_block_plotting(self):
        old = self.refs()[0]
        source = next(self.fixture.lot.glob("*.csv"))
        saved = source.read_bytes()
        source.unlink()
        self.assertEqual(self.refs()[0].status, "missing")
        with self.assertRaises(ValueError):
            self.service.validate_refs([old])
        source.write_bytes(saved)
        self.assertEqual(self.refs()[0].status, "stale")
        self.fixture._run()
        (self.fixture.lot / "unknown.csv").write_text("synthetic", encoding="utf-8")
        self.assertEqual(self.refs()[0].status, "invalid")

    def test_configuration_change_and_unprocessed_wafer_visible(self):
        old = self.refs()[0]
        self.fixture._write_csv(wafer=2)
        self.assertEqual([ref.status for ref in self.refs()], ["current", "unprocessed"])
        self.fixture.document["products"]["WL111"]["wafer"] = {"expected_die_count": 100}
        self.fixture._save_config()
        self.assertEqual(self.refs()[0].status, "stale")
        with self.assertRaises(ValueError):
            self.service.validate_refs([old])

    def test_first_read_verifies_cleaned_hash_even_if_size_and_mtime_unchanged(self):
        ref = self.refs()[0]
        path = ref.folder / ref.record["cleaned_path"]
        original_stat = path.stat()
        data = path.read_bytes().replace(b",12,", b",92,")
        path.write_bytes(data)
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertTrue(self.refs()[0].available)
        with self.assertRaisesRegex(ValueError, "内容"):
            self.service.load_columns(ref, ["VOUT"])

    def test_full_verification_detects_same_stamp_raw_changes(self):
        ref = self.refs()[0]
        source = next(self.fixture.lot.glob("*.csv"))
        stat = source.stat()
        source.write_bytes(source.read_bytes().replace(b",12,", b",92,"))
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.service.validate_refs([ref])
        with self.assertRaisesRegex(ValueError, "输入内容"):
            self.service.validate_refs([ref], verify_hashes=True)

    def test_out_of_scope_cleaned_path_rejected(self):
        ref = self.refs()[0]
        malicious = replace(ref, record={**ref.record, "cleaned_path": str(self.fixture.config_path)})
        with self.assertRaisesRegex(ValueError, "范围之外"):
            self.service.load_columns(malicious, ["VOUT"])

    def test_numeric_conversion_preserves_existing_marker_behavior_and_excludes_infinity(self):
        import pandas as pd
        frame = pd.DataFrame({"A": ["2", ">3", "<4", "nan", "inf", "-inf", "bad"]})
        self.assertEqual(numeric_frame(frame)["A"].dropna().tolist(), [2])
        self.assertEqual(numeric_frame(frame, strip_markers=True)["A"].dropna().tolist(), [2, 3, 4])

    def test_bin_task_parser_and_safe_filenames(self):
        self.assertEqual(parse_bin_tasks(True, True, True, "6,9,6+9,6"),
                         [(0, None), (1, None), (2, 6), (2, 9), (2, [6, 9])])
        for text in ("", "6+", "6.5", "-1", "a"):
            with self.assertRaises(ValueError):
                parse_bin_tasks(False, False, True, text)
        self.assertNotIn("/", safe_name("VOUT/VIN"))
        self.assertNotEqual(safe_name("VOUT/VIN"), safe_name("VOUT_VIN"))

    def _two_lots(self):
        lot2 = self.fixture.product / "LOT002"
        lot2.mkdir()
        self.fixture._write_csv(lot=lot2)
        self.fixture._run()
        return self.refs()

    def test_probability_identity_grouping_highlight_and_spec_separation(self):
        refs = self._two_lots()
        from probability import ProbabilityLogic
        captured = []
        def capture(logic, frame, name, path, *args):
            captured.append((frame, args))
            Image.new("RGB", (20, 20), "white").save(path)
            return True
        options = PlotOptions("probability", tests=["VOUT"], reference_tokens=[refs[0].token],
                              highlight_tokens=[refs[1].token])
        with patch.object(ProbabilityLogic, "create_overlay_probability_plot", capture):
            result = run_plot_job(self.service, refs, options)
        self.assertEqual(set(captured[0][0].columns), {ref.label for ref in refs})
        self.assertEqual(captured[0][1][-2], [refs[1].label])
        self.assertEqual(json.loads((result.output_folder / "manifest.json").read_text(encoding="utf-8"))["status"], "complete")
        self.assertEqual(self.service.csv_reads, 2)
        captured.clear()
        options.grouping = "lot"
        with patch.object(ProbabilityLogic, "create_overlay_probability_plot", capture):
            run_plot_job(self.service, refs, options)
        self.assertEqual(len(captured[0][0].columns), 2)
        self.assertTrue(any("参考" in col for col in captured[0][0].columns))
        self.assertEqual(self.service.csv_reads, 2)
        metadata = [list(row) for row in fixtures.METADATA]
        metadata[2][5] = "20"
        self.fixture._write_csv(lot=self.fixture.product / "LOT002", metadata=metadata)
        self.fixture._run()
        captured.clear()
        with patch.object(ProbabilityLogic, "create_overlay_probability_plot", capture):
            result = run_plot_job(self.service, self.refs(), options)
        self.assertEqual(len(captured), 2)
        self.assertTrue(any("规格组" in note for note in result.notes))

    def test_actual_all_three_renderers_need_no_merged_excel(self):
        refs = self.refs()
        original_save = Figure.savefig
        def small_save(figure, path, *args, **kwargs):
            kwargs["dpi"] = 35
            return original_save(figure, path, *args, **kwargs)
        options = [PlotOptions("probability", tests=["VOUT"]),
                   PlotOptions("bin", bin_tasks=[(0, None), (1, None), (2, [1, 2])], combine_bin_indices=[0, 1]),
                   PlotOptions("test", tests=["VOUT"], modes=["auto", "usl_lsl"], combine_modes=["auto", "usl_lsl"])]
        with patch.object(Figure, "savefig", small_save), \
             patch("pandas.ExcelFile", side_effect=AssertionError("merged Excel must not be read")):
            for option in options:
                result = run_plot_job(self.service, refs, option)
                self.assertGreater(result.image_count, 1)
                for path in result.output_folder.rglob("*.png"):
                    with Image.open(path) as image:
                        self.assertGreater(min(image.size), 10)
                        image.verify()
        self.assertEqual(self.service.csv_reads, 2)  # VOUT then only missing X/Y/BIN; Test map hits cache.

    def test_multiple_test_items_read_each_wafer_once(self):
        refs = self._two_lots()
        def fake(loaded, output, options, log):
            self.assertEqual(len(loaded), 2)
            self.assertEqual(set(loaded[0][1].columns), {"X_COORD", "Y_COORD", "VOUT", "UPPER_ONLY"})
            Image.new("RGB", (20, 20)).save(output / "synthetic.png")
        with patch("atlas_pipeline.plot_jobs._render_test", fake):
            run_plot_job(self.service, refs, PlotOptions("test", tests=["VOUT", "UPPER_ONLY"]))
        self.assertEqual(self.service.csv_reads, 2)

    def test_renderer_failure_keeps_outputs_marked_failed(self):
        with patch("atlas_pipeline.plot_jobs._render_probability", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                run_plot_job(self.service, self.refs(), PlotOptions("probability", tests=["VOUT"]))
        manifest = next((self.fixture.product / "plots").rglob("manifest.json"))
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["status"], "failed")

    def test_input_changes_during_render_are_not_marked_complete(self):
        def fake(loaded, output, options, log):
            Image.new("RGB", (20, 20)).save(output / "synthetic.png")
            self.fixture._write_csv(suffix="RT1", ending="2026-09-16 11:00:00")
        with patch("atlas_pipeline.plot_jobs._render_probability", fake):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                run_plot_job(self.service, self.refs(), PlotOptions("probability", tests=["VOUT"]))
        manifest = next((self.fixture.product / "plots").rglob("manifest.json"))
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["status"], "failed")

    def test_plot_directory_is_ignored_by_incremental_scanning(self):
        from atlas_pipeline.pipeline import scan_root
        output = self.fixture.product / "plots"
        output.mkdir()
        (output / "unmatched.csv").write_text("synthetic", encoding="utf-8")
        plan = scan_root(self.fixture.root, self.fixture.config_path)
        self.assertEqual(plan.products[0].tasks[0].action, "SKIP")
        self.assertEqual(plan.products[0].errors, [])

    def test_invalid_plot_options_fail_before_data_reads_or_outputs(self):
        refs = self.refs()
        invalid = [PlotOptions("unknown"), PlotOptions("probability", tests=["NOT_A_TEST"]),
                   PlotOptions("probability", tests=["VOUT"], x_mode="manual", x_range=[10, 0]),
                   PlotOptions("probability", tests=["VOUT"], marker_size=0),
                   PlotOptions("test", tests=["VOUT"], modes=["manual"], minimum=0, maximum=float("inf")),
                   PlotOptions("test", tests=["VOUT"], modes=["auto", "auto"]),
                   PlotOptions("bin", bin_tasks=[(2, -1)]), PlotOptions("bin", combine_bin_indices=[5]),
                   PlotOptions("bin", sort_method="manual", manual_order="W01")]
        for options in invalid:
            with self.assertRaises(ValueError):
                run_plot_job(self.service, refs, options)
        self.assertEqual(self.service.csv_reads, 0)
        self.assertFalse((self.fixture.product / "plots").exists())

    def test_duplicate_refs_and_mixed_products_are_rejected(self):
        refs = self.refs()
        with self.assertRaises(ValueError):
            validate_options(PlotOptions("bin"), refs + refs)
        other = replace(refs[0], folder=self.fixture.root / "LDO" / "WL222")
        with self.assertRaisesRegex(ValueError, "一个产品"):
            self.service.validate_refs(refs + [other])

    def test_manual_sort_accepts_complete_identity(self):
        refs = self._two_lots()
        options = PlotOptions("bin", sort_method="manual", manual_order=refs[1].label + "," + refs[0].label)
        validate_options(options, refs)
        with self.assertRaises(ValueError):
            validate_options(replace(options, manual_order=refs[0].label + "," + refs[0].label), refs)

    def test_custom_coordinate_and_bin_columns_are_adapted_without_recleaning(self):
        headers = ["SITE_NUM", "PASSFG", "XC", "YC", "SBIN"] + fixtures.HEADERS[5:]
        self.fixture.document["products"]["WL111"]["columns"] = {"x": "XC", "y": "YC", "soft_bin": "SBIN"}
        self.fixture._save_config()
        self.fixture._write_csv(columns=headers)
        self.fixture._run()
        refs = self.refs()
        from test_map import save_single_map
        def capture(frame, test, label, output, *args, **kwargs):
            self.assertEqual(list(frame.columns), ["X_COORD", "Y_COORD", "VOUT"])
            self.assertEqual(frame["VOUT"].tolist(), [2, 12])  # Failed BIN remains included, as before.
            Image.new("RGB", (20, 20)).save(Path(output) / "synthetic.png")
        with patch("test_map.save_single_map", capture), patch("test_map.save_composite"):
            run_plot_job(self.service, refs, PlotOptions("test", tests=["VOUT"]))
        self.assertEqual(self.service.csv_reads, 1)

    def test_missing_test_item_is_explicit_and_wafers_are_kept_separate(self):
        refs = self._two_lots()
        headers = [col for col in fixtures.HEADERS if col != "UPPER_ONLY"]
        index = fixtures.HEADERS.index("UPPER_ONLY")
        metadata = [row[:index] + row[index + 1:] for row in fixtures.METADATA]
        rows = [row[:index] + row[index + 1:] for row in fixtures.INITIAL_ROWS]
        self.fixture._write_csv(lot=self.fixture.product / "LOT002", columns=headers, metadata=metadata, rows=rows)
        self.fixture._run()
        captured = []
        def single(frame, test, label, output, *args, **kwargs):
            Image.new("RGB", (20, 20)).save(Path(output) / "synthetic.png")
        def composite(data, test, *args, **kwargs):
            captured.append((test, data))
        with patch("test_map.save_single_map", single), patch("test_map.save_composite", composite):
            result = run_plot_job(self.service, self.refs(), PlotOptions("test", tests=["VOUT", "UPPER_ONLY"]))
        self.assertEqual([(test, len(data)) for test, data in captured], [("VOUT", 2), ("UPPER_ONLY", 1)])
        self.assertEqual(sum(len(frame) for frame in captured[0][1].values()), 4)
        self.assertTrue(any("没有该测试项" in note for note in result.notes))

    def test_strict_stitching_error_is_not_marked_complete(self):
        from probability import ProbabilityLogic
        def capture(logic, frame, name, path, *args):
            Image.new("RGB", (20, 20)).save(path)
            return True
        with patch.object(ProbabilityLogic, "create_overlay_probability_plot", capture), \
             patch("probability.Image.open", side_effect=OSError("synthetic image read failure")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                run_plot_job(self.service, self.refs(), PlotOptions("probability", tests=["VOUT"]))


if __name__ == "__main__":
    unittest.main()
