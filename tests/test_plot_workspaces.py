"""Synthetic task/draft/version safety and probability calculation-cache checks."""

from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image

import test_incremental_pipeline as fixtures
from atlas_pipeline.curve_cache import ProbabilityCurveCache, PROBABILITY_CURVES
from atlas_pipeline.plot_data import PlotDataService
from atlas_pipeline.plot_jobs import PlotOptions, run_plot_job, validate_options
from atlas_pipeline.plot_workspace import (
    create_task, export_destination, load_task, read_json, run_preview, save_version, task_list, versions,
)


def fake_render(loaded, output, options, log):
    Image.new("RGB", (20, 20), (options.output_dpi % 255, 0, 0)).save(output / "synthetic.png")


class PlotWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineIntegrationTests()
        self.f.setUp()
        self.f._write_csv()
        self.f._run()
        self.service = PlotDataService(self.f.config_path)
        self.options = PlotOptions("probability", tests=["VOUT"], x_modes=["auto", "spec"])
        PROBABILITY_CURVES.clear()

    def tearDown(self):
        self.service.clear_cache()
        PROBABILITY_CURVES.clear()
        self.f.tearDown()

    def refs(self):
        return self.service.catalog(self.f.root).wafers

    def task(self):
        return create_task(self.f.product, "probability", "VOUT波动排查")

    def preview(self, task, options=None, refs=None):
        with patch("atlas_pipeline.plot_jobs._render_probability", fake_render):
            return run_preview(self.service, refs or self.refs(), options or self.options, task)

    def save(self, preview):
        with patch("atlas_pipeline.plot_jobs._render_probability", fake_render):
            return save_version(self.service, preview)

    def test_named_tasks_reload_without_altering_existing_outputs(self):
        task = self.task()
        self.assertIn("VOUT波动排查", task.folder.name)
        self.assertEqual(load_task(task.folder, self.f.product, "probability"), task)
        self.assertEqual(task_list(self.f.product, "probability"), [task])
        self.assertEqual(versions(task), [])
        self.assertEqual(self.f._run().skipped, 1)

    def test_invalid_task_name_kind_and_scope_rejected(self):
        for name in ("", " " * 3, "a" * 81):
            with self.assertRaises(ValueError):
                create_task(self.f.product, "probability", name)
        with self.assertRaises(ValueError):
            create_task(self.f.product, "../test", "name")
        task = self.task()
        with self.assertRaises(ValueError):
            load_task(task.folder, self.f.root / "LDO", "probability")

    def test_preview_replaces_one_draft_keeps_no_saved_versions(self):
        task = self.task()
        first = self.preview(task)
        second = self.preview(task, replace(self.options, marker_size=8))
        self.assertEqual(first.output_folder, second.output_folder)
        self.assertEqual({p.name for p in first.output_folder.parent.iterdir()}, {"current"})
        self.assertEqual(versions(task), [])
        manifest = read_json(second.output_folder / "manifest.json")
        self.assertEqual((manifest["purpose"], manifest["options"]["output_dpi"]), ("preview", 90))
        self.assertEqual(read_json(task.folder / "task.json")["snapshot"]["options"]["marker_size"], 8)

    def test_options_are_captured_not_shared_with_caller(self):
        task = self.task()
        preview = self.preview(task)
        self.options.tests.append("UPPER_ONLY")
        self.options.x_modes.append("manual")
        self.assertEqual(preview.options.tests, ["VOUT"])
        self.assertEqual(preview.options.x_modes, ["auto", "spec"])
        self.assertEqual(self.save(preview).output_folder.name, "v001")

    def test_failed_preview_preserves_prior_draft_and_task_state(self):
        task = self.task()
        first = self.preview(task)
        before = (first.output_folder / "manifest.json").read_bytes()
        metadata = (task.folder / "task.json").read_bytes()
        with patch("atlas_pipeline.plot_jobs._render_probability", side_effect=RuntimeError("failed renderer")):
            with self.assertRaises(RuntimeError):
                run_preview(self.service, self.refs(), replace(self.options, marker_size=9), task)
        self.assertEqual((first.output_folder / "manifest.json").read_bytes(), before)
        self.assertEqual((task.folder / "task.json").read_bytes(), metadata)
        self.assertEqual({p.name for p in first.output_folder.parent.iterdir()}, {"current"})

    def test_explicit_save_creates_immutable_high_resolution_versions(self):
        task = self.task()
        first = self.preview(task)
        saved = self.save(first)
        self.assertEqual(saved.output_folder.name, "v001")
        original = (saved.output_folder / "synthetic.png").read_bytes()
        data = read_json(saved.output_folder / "manifest.json")
        self.assertEqual((data["purpose"], data["options"]["output_dpi"]), ("archive", 300))
        second = self.preview(task, replace(self.options, marker_size=9))
        self.assertEqual(self.save(second).output_folder.name, "v002")
        self.assertEqual((saved.output_folder / "synthetic.png").read_bytes(), original)
        self.assertEqual([p.name for p, _ in versions(task)], ["v002", "v001"])
        self.assertEqual(read_json(task.folder / "latest.json")["version"], "v002")

    def test_identical_save_reuses_existing_version_without_rendering(self):
        task = self.task()
        preview = self.preview(task)
        first = self.save(preview)
        with patch("atlas_pipeline.plot_workspace.run_plot_job", side_effect=AssertionError("duplicate render")):
            second = save_version(self.service, preview)
        self.assertEqual(first.output_folder, second.output_folder)
        self.assertEqual(len(versions(task)), 1)

    def test_named_save_is_directly_under_selected_parent_and_preserves_draft(self):
        task = self.task()
        preview = self.preview(task)
        before = (preview.output_folder / "manifest.json").read_bytes()
        output = export_destination(self.f.product / "plots/probability", "Lot1与Lot2对比")
        with patch("atlas_pipeline.plot_jobs._render_probability", fake_render):
            saved = save_version(self.service, preview, destination=output)
        self.assertEqual(saved.output_folder, output)
        self.assertTrue((output / "synthetic.png").exists())
        self.assertEqual(read_json(output / "manifest.json")["version"], "v001")
        self.assertEqual(versions(load_task(task.folder, task.product, task.kind))[0][0], output)
        self.assertEqual(read_json(task.folder / "latest.json")["folder"], str(output))
        self.assertFalse((task.folder / "v001").exists())
        self.assertEqual((preview.output_folder / "manifest.json").read_bytes(), before)

    def test_named_save_outside_product_and_repeat_save_reuses_same_folder(self):
        task = self.task()
        preview = self.preview(task)
        parent = self.f.folder / "engineer_exports"
        parent.mkdir()
        output = export_destination(parent, "VOUT分布")
        with patch("atlas_pipeline.plot_jobs._render_probability", fake_render):
            first = save_version(self.service, preview, destination=output)
        with patch("atlas_pipeline.plot_workspace.run_plot_job", side_effect=AssertionError("duplicate render")):
            second = save_version(self.service, preview, destination=output)
        self.assertEqual(first.output_folder, second.output_folder)
        self.assertEqual(len(versions(task)), 1)
        self.assertEqual(read_json(output / "manifest.json")["options"]["output_dpi"], 300)

    def test_same_preview_can_be_saved_to_another_name_without_rerendering(self):
        task = self.task()
        preview = self.preview(task)
        original = self.save(preview)
        output = self.f.product / "plots/probability/工程汇报用"
        with patch("atlas_pipeline.plot_workspace.run_plot_job", side_effect=AssertionError("duplicate render")):
            saved = save_version(self.service, preview, destination=output)
        self.assertEqual(saved.output_folder, output)
        self.assertEqual((output / "synthetic.png").read_bytes(), (original.output_folder / "synthetic.png").read_bytes())
        self.assertEqual([record["version"] for _, record in versions(task)], ["v002", "v001"])

    def test_custom_folder_collision_preserves_existing_files(self):
        preview = self.preview(self.task())
        output = self.f.product / "plots/probability/已有资料"
        output.mkdir()
        (output / "notes.txt").write_text("synthetic user note", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "换一个"):
            save_version(self.service, preview, destination=output)
        self.assertEqual((output / "notes.txt").read_text(encoding="utf-8"), "synthetic user note")
        self.assertEqual(versions(preview.task), [])
        self.assertTrue(preview.output_folder.exists())
        saved = self.save(preview)
        changed = self.preview(preview.task, replace(self.options, marker_size=9))
        with self.assertRaises(ValueError):
            save_version(self.service, changed, destination=saved.output_folder)

    def test_custom_save_failure_keeps_preview_and_does_not_publish_folder(self):
        task = self.task()
        preview = self.preview(task)
        output = self.f.product / "plots/probability/失败导出"
        with patch("atlas_pipeline.plot_jobs._render_probability", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                save_version(self.service, preview, destination=output)
        self.assertFalse(output.exists())
        self.assertEqual(versions(task), [])
        self.assertTrue(preview.output_folder.exists())

    def test_custom_save_with_missing_previous_export_and_other_plot_types(self):
        task = self.task()
        preview = self.preview(task)
        with patch("atlas_pipeline.plot_jobs._render_probability", fake_render):
            saved = save_version(self.service, preview, destination=self.f.product / "plots/probability/旧导出")
        moved = saved.output_folder.with_name("手工移走的导出")
        saved.output_folder.rename(moved)
        self.assertEqual(versions(task), [])
        with patch("atlas_pipeline.plot_jobs._render_probability", fake_render):
            saved = save_version(self.service, preview, destination=self.f.product / "plots/probability/新导出")
        self.assertEqual(read_json(saved.output_folder / "manifest.json")["version"], "v002")
        for kind in ("bin", "test"):
            task = create_task(self.f.product, kind, "工程检查")
            options = PlotOptions(kind, tests=["VOUT"] if kind == "test" else [])
            with patch("atlas_pipeline.plot_jobs._render_" + kind, fake_render):
                preview = run_preview(self.service, self.refs(), options, task)
                output = self.f.product / "plots" / kind / "检查结果"
                saved = save_version(self.service, preview, destination=output)
            self.assertEqual(saved.output_folder, output)
            self.assertEqual(versions(task)[0][0], output)

    def test_export_folder_names_and_repository_policy(self):
        parent = self.f.folder
        for name in ("", ".", "..", "../other", "a/b", "a\\b", "CON", "a:", "a."):
            with self.assertRaises(ValueError):
                export_destination(parent, name)
        with self.assertRaises(ValueError):
            export_destination(Path(__file__).resolve().parents[1], "generated_plots")

    def test_old_preview_object_cannot_save_new_draft(self):
        task = self.task()
        first = self.preview(task)
        self.preview(task, replace(self.options, marker_size=9))
        with self.assertRaisesRegex(ValueError, "替换"):
            save_version(self.service, first)
        self.assertEqual(versions(task), [])

    def test_mutated_preview_parameters_cannot_be_misrecorded(self):
        preview = self.preview(self.task())
        preview.options.tests.append("UPPER_ONLY")
        with self.assertRaisesRegex(ValueError, "记录变化"):
            save_version(self.service, preview)

    def test_changed_input_order_cannot_save_a_different_legend_than_preview(self):
        self.f._write_csv(wafer=2)
        self.f._run()
        preview = self.preview(self.task())
        preview.refs.reverse()
        with self.assertRaisesRegex(ValueError, "记录变化"):
            save_version(self.service, preview)

    def test_input_change_after_preview_blocks_save(self):
        task = self.task()
        preview = self.preview(task)
        self.f._write_csv(suffix="RT1", ending="2026-09-17 13:00:00")
        with self.assertRaises(ValueError):
            save_version(self.service, preview)
        self.assertEqual(versions(task), [])

    def test_modified_preview_images_or_unregistered_files_preserved(self):
        task = self.task()
        preview = self.preview(task)
        path = preview.output_folder / "synthetic.png"
        path.write_bytes(b"user edit")
        with self.assertRaisesRegex(ValueError, "内容变化"):
            save_version(self.service, preview)
        with self.assertRaises(ValueError):
            self.preview(task)
        self.assertEqual(path.read_bytes(), b"user edit")

    def test_nested_manifest_named_file_is_not_silently_deleted(self):
        task = self.task()
        preview = self.preview(task)
        path = preview.output_folder / "manual/manifest.json"
        path.parent.mkdir()
        path.write_bytes(b"user data")
        with self.assertRaises(ValueError):
            self.preview(task)
        self.assertEqual(path.read_bytes(), b"user data")

    def test_failed_high_resolution_export_does_not_publish_version(self):
        task = self.task()
        preview = self.preview(task)
        with patch("atlas_pipeline.plot_jobs._render_probability", side_effect=RuntimeError("export failure")):
            with self.assertRaises(RuntimeError):
                save_version(self.service, preview)
        self.assertEqual(versions(task), [])
        self.assertFalse(any(p.name.startswith(".saving") for p in task.folder.iterdir()))
        self.assertTrue(preview.output_folder.exists())

    def test_three_task_types_are_isolated(self):
        task = self.task()
        with self.assertRaisesRegex(ValueError, "不一致"):
            run_preview(self.service, self.refs(), PlotOptions("bin"), task)
        self.assertEqual(task_list(self.f.product, "bin"), [])

    def test_probability_multiple_ranges_share_computation_and_stitch_separately(self):
        captured = []
        from probability import ProbabilityLogic
        def capture(logic, frame, name, path, *args, **kwargs):
            captured.append((Path(path).name, args[0], kwargs))
            Image.new("RGB", (20, 20)).save(path)
            return True
        with patch.object(ProbabilityLogic, "create_overlay_probability_plot", capture):
            result = run_plot_job(self.service, self.refs(), self.options)
        self.assertEqual([item[0] for item in captured], ["auto_VOUT_spec1_prob.png", "spec_VOUT_spec1_prob.png"])
        self.assertIsNone(captured[0][1])
        np.testing.assert_allclose(captured[1][1], [-10 / 18, 10 + 10 / 18])
        self.assertIs(captured[0][2]["prepared_curves"], captured[1][2]["prepared_curves"])
        self.assertEqual(PROBABILITY_CURVES.computations, 1)
        self.assertEqual(result.image_count, 4)
        self.assertTrue((result.output_folder / "auto_selected_Combined_Page1.png").exists())
        self.assertTrue((result.output_folder / "spec_selected_Combined_Page1.png").exists())

    def test_repeated_lot_comparison_reuses_only_unchanged_member_set(self):
        for lot_name in ("LOT002", "LOT003"):
            lot = self.f.product / lot_name
            lot.mkdir()
            self.f._write_csv(lot=lot)
        self.f._run()
        refs = self.refs()
        from probability import ProbabilityLogic
        def capture(logic, frame, name, path, *args, **kwargs):
            Image.new("RGB", (20, 20)).save(path)
            return True
        options = replace(self.options, grouping="lot")
        with patch.object(ProbabilityLogic, "create_overlay_probability_plot", capture):
            run_plot_job(self.service, refs[:2], options)
            self.assertEqual(PROBABILITY_CURVES.computations, 2)
            run_plot_job(self.service, refs[1:], options)
            self.assertEqual((PROBABILITY_CURVES.computations, PROBABILITY_CURVES.hits), (3, 1))
        self.assertEqual(self.service.csv_reads, 3)

    def test_cache_does_not_average_wafer_distributions_or_sample_dies(self):
        cache = ProbabilityCurveCache()
        values, probabilities = cache.curve("full-lot", [pd.Series(["2", "12", "bad", "inf"]),
                                                       pd.Series(["4", "4", "4"])])
        self.assertEqual(values.tolist(), [2, 4, 4, 4, 12])
        np.testing.assert_allclose(probabilities, (np.arange(1, 6) - 0.3) / 5.4)
        self.assertFalse(values.flags.writeable)

    def test_probability_cache_changes_for_partial_lot_and_updated_cleaned_data(self):
        self.f._write_csv(wafer=2)
        self.f._run()
        options = replace(self.options, grouping="lot", x_modes=["auto"])
        captured = []
        from probability import ProbabilityLogic
        def capture(logic, frame, name, path, *args, **kwargs):
            captured.append(next(iter(kwargs["prepared_curves"].values()))[0].tolist())
            Image.new("RGB", (20, 20)).save(path)
            return True
        with patch.object(ProbabilityLogic, "create_overlay_probability_plot", capture):
            refs = self.refs()
            run_plot_job(self.service, refs, options)
            run_plot_job(self.service, refs[:1], options)
            run_plot_job(self.service, refs, options)
            self.assertEqual(captured, [[2, 2, 12, 12], [2, 12], [2, 2, 12, 12]])
            self.assertEqual((PROBABILITY_CURVES.computations, PROBABILITY_CURVES.hits), (2, 1))
            self.f._write_csv(suffix="RT1", rows=fixtures.RETEST_ROWS, ending="2026-09-17 13:00:00")
            self.f._run()
            run_plot_job(self.service, self.refs(), options)
            self.assertEqual(captured[-1], [2, 2, 4, 12])
            self.assertEqual(PROBABILITY_CURVES.computations, 3)

    def test_legacy_positional_single_range_options_are_compatible(self):
        options = PlotOptions("probability", ["VOUT"], ["auto"], "manual", [0, 10])
        validate_options(options, self.refs())
        self.assertEqual(options.x_range, [0, 10])
        self.assertIsNone(options.x_modes)

    def test_actual_preview_and_saved_png_use_requested_resolution(self):
        task = self.task()
        preview = run_preview(self.service, self.refs(), replace(self.options, x_modes=["auto"]), task)
        saved = save_version(self.service, preview)
        with Image.open(preview.output_folder / "auto_VOUT_spec1_prob.png") as image:
            width, height = image.size
            self.assertAlmostEqual(image.info["dpi"][0], 90, places=1)
        with Image.open(saved.output_folder / "auto_VOUT_spec1_prob.png") as image:
            self.assertAlmostEqual(image.info["dpi"][0], 300, places=1)
            self.assertGreater(image.width, width * 3)
            self.assertGreater(image.height, height * 3)
        self.assertEqual((PROBABILITY_CURVES.computations, PROBABILITY_CURVES.hits), (1, 1))

    def test_cache_eviction_and_oversize_never_change_values(self):
        cache = ProbabilityCurveCache(max_bytes=32)
        first = pd.Series([12, 2])
        cache.curve("one", [first])
        cache.curve("two", [first])
        result = cache.curve("one", [first])
        self.assertEqual((cache.computations, cache.hits, cache.bytes), (3, 0, 32))
        self.assertEqual(result[0].tolist(), [2, 12])
        cache = ProbabilityCurveCache(max_bytes=1)
        cache.curve("large", [first])
        self.assertEqual(cache.bytes, 0)

    def test_empty_duplicate_invalid_ranges_fail_before_output(self):
        refs = self.refs()
        for modes in ([], ["auto", "auto"], ["wrong"]):
            with self.assertRaises(ValueError):
                validate_options(replace(self.options, x_modes=modes), refs)
        with self.assertRaises(ValueError):
            validate_options(replace(self.options, x_modes=["auto", "manual"], x_range=[2, 1]), refs)


if __name__ == "__main__":
    unittest.main()
