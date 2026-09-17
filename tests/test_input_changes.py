"""Synthetic input rename/replacement, stale-confirmation and history safety checks."""

from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import zipfile

import test_incremental_pipeline as fixtures
from atlas_pipeline.archives import prepare_archives, preview_archives
from atlas_pipeline.input_changes import approve_replacement, preview_replacements
from atlas_pipeline.pipeline import scan_root
from atlas_pipeline.plot_data import PlotDataService
from atlas_pipeline.state import ProductState, read_snapshot


class InputChangeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineIntegrationTests()
        self.f.setUp()
        self.raw = self.f._write_csv()
        self.f._run()

    def tearDown(self):
        self.f.tearDown()

    def record(self, wafer="1"):
        return next(record for record in self.f._records() if record["wafer"] == wafer)

    def key(self, wafer="1"):
        return next(key for key, record in read_snapshot(self.f.product)["wafers"].items() if record["wafer"] == wafer)

    def task(self, wafer="1"):
        return next(task for task in scan_root(self.f.root, self.f.config_path).products[0].tasks if task.wafer == wafer)

    def approval(self, wafers=("1",)):
        reviews = preview_replacements(self.f.root, self.f.config_path,
                                       [(self.f.product, self.key(wafer)) for wafer in wafers])
        return [approve_replacement(review, "Synthetic supplier correction") for review in reviews]

    def corrected_name(self, wafer=1, rows=None):
        path = self.f._write_csv(wafer=wafer, rows=rows or fixtures.RETEST_ROWS)
        return path.rename(path.with_name(path.stem + "_CORRECTED.csv"))

    def events(self):
        database = self.f.product / ".atlas/state.sqlite"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            return [json.loads(payload) for payload, in connection.execute("SELECT payload FROM input_revisions ORDER BY rowid")]

    def history(self, record=None):
        return self.f.product / (record or self.record())["input_history"]

    def prepared_bundle(self, wafers=(1,)):
        contents = []
        for wafer in wafers:
            path = self.f._write_csv(wafer=wafer)
            contents.append((path.name, path.read_bytes()))
            path.unlink()
        archive = self.f.lot / "supplier.csv.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, content in contents:
                bundle.writestr("folder/" + name, content)
        prepared = prepare_archives(self.f.root, approved_plan=preview_archives(self.f.root))
        self.assertEqual(prepared.failed, 0)
        self.f._run()
        return read_snapshot(self.f.product)["archives"]["LOT001/supplier.csv.zip"]

    def test_same_content_rename_is_read_only_in_preview_then_automatically_migrated(self):
        old = self.record()
        before = (self.f.product / old["cleaned_path"]).read_bytes()
        changed = self.raw.rename(self.raw.with_name(self.raw.stem + "_VENDOR.csv"))
        original = changed.read_bytes()
        snapshot = read_snapshot(self.f.product)
        task = self.task()
        self.assertEqual(task.action, "INPUT_RENAMED")
        self.assertEqual(read_snapshot(self.f.product), snapshot)
        self.assertFalse((self.f.product / ".atlas/input_history").exists())
        result = self.f._run()
        self.assertEqual((result.processed, result.failed), (1, 0))
        self.assertEqual(changed.read_bytes(), original)
        record = self.record()
        self.assertEqual(record["metrics"], old["metrics"])
        self.assertEqual(record["source_files"][0]["relative_path"], "LOT001/" + changed.name)
        self.assertEqual((self.history() / "previous_cleaned.csv").read_bytes(), before)
        self.assertIn(changed.name, (self.f.product / record["cleaned_path"]).read_text(encoding="utf-8"))
        self.assertEqual(self.events()[0]["kind"], "auto_rename")
        self.assertEqual(self.f._run().skipped, 1)
        self.assertEqual(len(self.events()), 1)

    def test_initial_and_retest_aliases_migrate_together_without_role_changes(self):
        rt = self.f._write_csv(suffix="RT1", rows=fixtures.RETEST_ROWS, ending="2026-09-16 11:00:00")
        self.f._run()
        old = self.record()["metrics"]
        for path in (self.raw, rt):
            path.rename(path.with_name(path.stem + "_VENDOR.csv"))
        self.assertEqual(len(self.task().input_change["renames"]), 2)
        self.assertEqual(self.f._run().processed, 1)
        self.assertEqual(self.record()["metrics"], old)

    def test_identical_bytes_with_changed_retest_role_still_need_confirmation(self):
        self.raw.rename(self.raw.with_name("WL111_LOT001_1#_RT1.csv"))
        self.assertEqual(self.task().action, "INVALID")
        self.assertEqual(self.f._run(force=True).processed, 0)

    def test_ambiguous_duplicate_content_does_not_guess_rename_mapping(self):
        self.f.document["products"]["WL111"]["cleaning"] = {"mode": "rt"}
        self.f._save_config()
        duplicate = self.raw.with_name(self.raw.stem + "_PART.csv")
        duplicate.write_bytes(self.raw.read_bytes())
        self.f._run()
        for path in (self.raw, duplicate):
            path.rename(path.with_name(path.stem + "_VENDOR.csv"))
        self.assertEqual(self.task().action, "INVALID")
        self.assertFalse(self.task().input_change["renames"])

    def test_removed_alias_and_changed_retained_file_do_not_qualify_as_pure_rename(self):
        rt = self.f._write_csv(suffix="RT1", rows=fixtures.RETEST_ROWS, ending="2026-09-16 11:00:00")
        self.f._run()
        self.raw.rename(self.raw.with_name(self.raw.stem + "_VENDOR.csv"))
        stamp = rt.stat()
        rt.write_bytes(rt.read_bytes().replace(b",4,4,3,", b",5,4,3,"))
        os.utime(rt, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(self.task().action, "INVALID")

    def test_changed_name_and_content_requires_explicit_approval(self):
        old = self.record()
        before = (self.f.product / old["cleaned_path"]).read_bytes()
        changed = self.corrected_name()
        self.assertEqual(self.task().action, "INVALID")
        snapshot = read_snapshot(self.f.product)
        approvals = self.approval()
        self.assertEqual(read_snapshot(self.f.product), snapshot)
        self.assertFalse((self.f.product / ".atlas/input_history").exists())
        result = self.f._run(replacement_approvals=approvals)
        self.assertEqual((result.processed, result.failed), (1, 0))
        self.assertEqual(len(self.f._bin_rows()), 1)
        self.assertEqual(self.f._bin_rows()[0]["有效die数量"], 1)
        self.assertEqual((self.history() / "previous_cleaned.csv").read_bytes(), before)
        audit = json.loads((self.history() / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["previous_record"], old)
        self.assertEqual(audit["event"]["approval"]["reason"], "Synthetic supplier correction")
        self.assertEqual(self.record()["source_files"][0]["relative_path"], "LOT001/" + changed.name)
        self.assertTrue(PlotDataService(self.f.config_path).catalog(self.f.root).wafers[0].available)

    def test_confirmation_can_intentionally_reduce_input_set_but_force_cannot(self):
        self.f._write_csv(suffix="RT1", rows=fixtures.RETEST_ROWS, ending="2026-09-16 11:00:00")
        self.f._run()
        self.raw.unlink()
        self.assertEqual(self.f._run(force=True).processed, 0)
        self.assertEqual(self.f._run(replacement_approvals=self.approval()).processed, 1)
        self.assertEqual(len(self.record()["source_files"]), 1)

    def test_confirmation_only_processes_selected_wafer_not_other_existing_or_new_wafer(self):
        self.f._write_csv(wafer=2)
        self.f._run()
        other = self.record("2")
        other_bytes = (self.f.product / other["cleaned_path"]).read_bytes()
        self.corrected_name()
        approvals = self.approval()
        self.f._write_csv(wafer=3)
        result = self.f._run(replacement_approvals=approvals)
        self.assertEqual((result.processed, result.skipped, result.failed), (1, 0, 0))
        self.assertEqual(self.record("2"), other)
        self.assertEqual((self.f.product / other["cleaned_path"]).read_bytes(), other_bytes)
        self.assertEqual(len(self.f._records()), 2)

    def test_same_filename_correction_keeps_previous_cleaned_version(self):
        previous = self.record()
        old = (self.f.product / previous["cleaned_path"]).read_bytes()
        self.f._write_csv(rows=fixtures.RETEST_ROWS)
        self.assertEqual(self.f._run().processed, 1)
        self.assertEqual((self.history() / "previous_cleaned.csv").read_bytes(), old)
        self.assertEqual(self.events()[0]["kind"], "input_changed")

    def test_manual_review_reads_changed_bytes_even_if_size_and_time_are_preserved(self):
        stamp = self.raw.stat()
        self.raw.write_bytes(self.raw.read_bytes().replace(b",2,4,2,abc", b",3,4,2,abc"))
        os.utime(self.raw, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(self.task().action, "SKIP")
        self.assertEqual(self.f._run(replacement_approvals=self.approval()).processed, 1)
        self.assertEqual(self.record()["metrics"]["tests"]["VOUT"]["mean"], 7.5)

    def test_stale_input_approval_is_rejected_without_changing_record_or_reports(self):
        changed = self.corrected_name()
        approvals = self.approval()
        before = read_snapshot(self.f.product)
        reports = [path.read_bytes() for path in self.f.product.glob("*.xlsx")]
        stamp = changed.stat()
        changed.write_bytes(changed.read_bytes().replace(b",4,4,3,", b",5,4,3,"))
        os.utime(changed, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        result = self.f._run(replacement_approvals=approvals)
        self.assertEqual((result.processed, result.failed), (0, 1))
        self.assertIn("过期", result.errors[0])
        self.assertEqual(read_snapshot(self.f.product), before)
        self.assertEqual([path.read_bytes() for path in self.f.product.glob("*.xlsx")], reports)

    def test_adding_or_removing_files_after_review_invalidates_approval(self):
        self.corrected_name()
        approvals = self.approval()
        self.f._write_csv(suffix="RT1", rows=fixtures.RETEST_ROWS, ending="2026-09-16 11:00:00")
        self.assertEqual(self.f._run(replacement_approvals=approvals).processed, 0)

    def test_configuration_and_history_changes_invalidate_approval(self):
        self.corrected_name()
        approvals = self.approval()
        self.f.document["products"]["WL111"]["wafer"] = {"expected_die_count": 2}
        self.f._save_config()
        self.assertEqual(self.f._run(replacement_approvals=approvals).processed, 0)
        approvals = self.approval()
        state = ProductState(self.f.product)
        changed = self.record()
        changed["updated_at"] = "synthetic competing revision"
        state.save_wafer(self.key(), changed)
        state.close()
        self.assertEqual(self.f._run(replacement_approvals=approvals).processed, 0)

    def test_approval_is_one_use_and_reusing_it_cannot_publish_another_revision(self):
        self.corrected_name()
        approvals = self.approval()
        self.assertEqual(self.f._run(replacement_approvals=approvals).processed, 1)
        self.assertEqual(self.f._run(replacement_approvals=approvals).processed, 0)
        self.assertEqual(len(self.events()), 1)

    def test_unknown_identity_or_unprepared_archives_cannot_be_overridden(self):
        self.corrected_name()
        approvals = self.approval()
        bad = self.f.lot / "unknown.csv"
        bad.write_text("unmatched synthetic", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.approval()
        self.assertEqual(self.f._run(replacement_approvals=approvals).processed, 0)
        bad.unlink()
        with zipfile.ZipFile(self.f.lot / "new.zip", "w") as archive:
            archive.writestr("notes.txt", "synthetic")
        with self.assertRaises(ValueError):
            self.approval()

    def test_no_current_input_or_unregistered_wafer_cannot_be_confirmed(self):
        self.raw.unlink()
        with self.assertRaises(ValueError):
            self.approval()
        self.f._write_csv(wafer=2)
        new = next(task for task in scan_root(self.f.root, self.f.config_path).products[0].tasks)
        with self.assertRaises(ValueError):
            preview_replacements(self.f.root, self.f.config_path, [(self.f.product, new.key)])

    def test_approval_targets_and_modes_are_explicit_and_scoped(self):
        self.corrected_name()
        approval = self.approval()[0]
        for args in ({"replacement_approvals": []}, {"replacement_approvals": [approval, approval]},
                     {"replacement_approvals": [approval], "force": True},
                     {"replacement_approvals": [approval], "rebuild_reports": True},
                     {"replacement_approvals": [replace(approval, product=str(self.f.folder))]}):
            with self.assertRaises(ValueError):
                self.f._run(**args)
        for reason in ("", "x" * 301):
            review = preview_replacements(self.f.root, self.f.config_path, [(self.f.product, self.key())])[0]
            with self.assertRaises(ValueError):
                approve_replacement(review, reason)

    def test_input_change_during_cleaning_does_not_overwrite_previous_output(self):
        self.corrected_name()
        approvals = self.approval()
        previous = self.record()
        before = (self.f.product / previous["cleaned_path"]).read_bytes()
        from atlas_pipeline.pipeline import merge_and_clean
        def changing(*args, **kwargs):
            result = merge_and_clean(*args, **kwargs)
            self.f._write_csv(suffix="RT1", rows=fixtures.RETEST_ROWS, ending="2026-09-16 11:00:00")
            return result
        with patch("atlas_pipeline.pipeline.merge_and_clean", changing):
            self.assertEqual(self.f._run(replacement_approvals=approvals).processed, 0)
        self.assertEqual((self.f.product / previous["cleaned_path"]).read_bytes(), before)
        self.assertEqual(self.events(), [])

    def test_invalid_corrected_csv_keeps_old_file_and_does_not_commit_revision(self):
        changed = self.corrected_name()
        changed.write_text("bad csv", encoding="utf-8")
        old = self.record()
        before = (self.f.product / old["cleaned_path"]).read_bytes()
        self.assertEqual(self.f._run(replacement_approvals=self.approval()).processed, 0)
        self.assertEqual((self.f.product / old["cleaned_path"]).read_bytes(), before)
        self.assertEqual(self.events(), [])

    def test_database_revision_failure_restores_old_cleaned_bytes(self):
        old = self.record()
        output = self.f.product / old["cleaned_path"]
        before, stamp = output.read_bytes(), output.stat()
        self.corrected_name()
        with patch.object(ProductState, "save_input_revision", side_effect=OSError("synthetic DB failure")):
            result = self.f._run(replacement_approvals=self.approval())
        self.assertEqual(result.processed, 0)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(output.stat().st_mtime_ns, stamp.st_mtime_ns)
        self.assertEqual(self.record()["source_files"], old["source_files"])
        self.assertEqual(self.events(), [])
        self.assertEqual(len(list((self.f.product / ".atlas/input_history").rglob("failed_new_cleaned.csv"))), 1)

    def test_archive_revision_validation_failure_does_not_publish_new_output(self):
        previous = self.record()
        output = self.f.product / previous["cleaned_path"]
        before, stamp = output.read_bytes(), output.stat()
        self.corrected_name()
        with patch("atlas_pipeline.pipeline.revised_archives", side_effect=ValueError("synthetic bad binding")):
            result = self.f._run(replacement_approvals=self.approval())
        self.assertEqual(result.processed, 0)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(output.stat().st_mtime_ns, stamp.st_mtime_ns)
        self.assertEqual(self.record()["source_files"], previous["source_files"])
        self.assertEqual(self.events(), [])

    def test_actual_sqlite_transaction_failure_rolls_back_files_and_archive_bindings(self):
        self.prepared_bundle()
        self.f._write_csv(rows=fixtures.RETEST_ROWS)
        self.f._run()
        original_id = self.events()[0]["id"]
        before = read_snapshot(self.f.product)
        output = self.f.product / self.record()["cleaned_path"]
        old_bytes = output.read_bytes()
        self.f._write_csv(rows=fixtures.INITIAL_ROWS)
        real_save = ProductState.save_input_revision
        def duplicate_id(state, key, record, files, event, updates):
            event["id"] = original_id
            return real_save(state, key, record, files, event, updates)
        with patch.object(ProductState, "save_input_revision", duplicate_id):
            result = self.f._run(replacement_approvals=self.approval())
        after = read_snapshot(self.f.product)
        self.assertEqual(result.processed, 0)
        self.assertEqual(output.read_bytes(), old_bytes)
        self.assertEqual(after["files"], before["files"])
        self.assertEqual(after["archives"], before["archives"])
        self.assertEqual(len(self.events()), 1)

    def test_changed_archive_origin_metadata_invalidates_confirmation(self):
        self.prepared_bundle()
        self.corrected_name()
        approvals = self.approval()
        state = ProductState(self.f.product)
        archive = read_snapshot(self.f.product)["archives"]["LOT001/supplier.csv.zip"]
        archive["sha256"] = "f" * 64
        state.save_archive("LOT001/supplier.csv.zip", archive)
        state.close()
        before = read_snapshot(self.f.product)
        self.assertEqual(self.f._run(replacement_approvals=approvals).processed, 0)
        self.assertEqual(read_snapshot(self.f.product), before)

    def test_hand_edited_previous_cleaned_file_is_preserved_and_blocks_overwrite(self):
        output = self.f.product / self.record()["cleaned_path"]
        output.write_bytes(b"synthetic engineer edits")
        self.corrected_name()
        self.assertEqual(self.f._run(replacement_approvals=self.approval()).processed, 0)
        self.assertEqual(output.read_bytes(), b"synthetic engineer edits")

    def test_prepared_archive_alias_updates_binding_without_losing_original_member(self):
        original = self.prepared_bundle()
        renamed = self.raw.rename(self.raw.with_name(self.raw.stem + "_VENDOR.csv"))
        self.assertEqual(self.task().action, "INPUT_RENAMED")
        self.assertEqual(self.f._run().processed, 1)
        archive = read_snapshot(self.f.product)["archives"]["LOT001/supplier.csv.zip"]
        self.assertEqual(archive["sha256"], original["sha256"])
        self.assertEqual(archive["members"][0]["relative_path"], "LOT001/" + renamed.name)
        self.assertEqual(archive["members"][0]["archive_member"], original["members"][0]["archive_member"])
        self.assertEqual(self.f._run(verify_hashes=True).skipped, 1)
        self.assertTrue(PlotDataService(self.f.config_path).catalog(self.f.root).wafers[0].available)

    def test_same_name_archive_correction_retires_original_binding_not_original_package(self):
        original = self.prepared_bundle()
        self.f._write_csv(rows=fixtures.RETEST_ROWS)
        self.assertEqual(self.f._run().processed, 1)
        archive = read_snapshot(self.f.product)["archives"]["LOT001/supplier.csv.zip"]
        self.assertEqual(archive["sha256"], original["sha256"])
        self.assertTrue((self.f.product / archive["source_backup"]).is_file())
        self.assertEqual(archive["members"], [])
        self.assertEqual(archive["retired_members"][0]["sha256"], original["members"][0]["sha256"])
        self.assertTrue(PlotDataService(self.f.config_path).catalog(self.f.root).wafers[0].available)
        self.assertEqual(self.f._run(verify_hashes=True).skipped, 1)

    def test_multiple_confirmed_wafers_in_shared_archive_keep_each_binding_revision(self):
        original = self.prepared_bundle((1, 2))
        for wafer in (1, 2):
            self.corrected_name(wafer)
        result = self.f._run(replacement_approvals=self.approval(("1", "2")))
        self.assertEqual((result.processed, result.failed), (2, 0))
        archive = read_snapshot(self.f.product)["archives"]["LOT001/supplier.csv.zip"]
        self.assertEqual(archive["sha256"], original["sha256"])
        self.assertEqual(archive["members"], [])
        self.assertEqual(len(archive["retired_members"]), 2)
        self.assertEqual(len(self.f._bin_rows()), 2)
        self.assertEqual(len(self.events()), 2)
        self.assertTrue(all(ref.available for ref in PlotDataService(self.f.config_path).catalog(self.f.root).wafers))


if __name__ == "__main__":
    unittest.main()
