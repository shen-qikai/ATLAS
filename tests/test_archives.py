"""Synthetic recursive archive preview/flat publication/backup integration tests."""

import gzip
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import zipfile

import test_incremental_pipeline as fixtures
from atlas_pipeline.archives import prepare_archives, preview_archives, set_csv_ignored, stamp
from atlas_pipeline.pipeline import scan_root
from atlas_pipeline.plot_data import PlotDataService
from atlas_pipeline.state import ProductState, read_snapshot


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineIntegrationTests()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def csv_bytes(self, **kwargs):
        # Never overwrite an already published root CSV when generating new fixtures.
        folder = self.f.folder / "synthetic" / "LOT001"
        folder.mkdir(parents=True, exist_ok=True)
        path = self.f._write_csv(lot=folder, **kwargs)
        name, content = path.name, path.read_bytes()
        path.unlink()
        return name, content

    def bundle(self, entries, name="supplier.csv.zip"):
        path = self.f.lot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for member, content in entries:
                bundle.writestr(member, content)
        return path

    def prepare(self, **kwargs):
        kwargs.setdefault("approved_plan", preview_archives(self.f.root))
        return prepare_archives(self.f.root, **kwargs)

    def backup_files(self):
        return list((self.f.product / ".atlas/ingest_backups").rglob("*"))

    def test_preview_recursive_read_only_and_execution_requires_preview(self):
        name, content = self.csv_bytes()
        self.bundle([("inside/" + name, content)], "outer/middle/deep/a.zip")
        (self.f.lot / "outer/info.txt").write_bytes(b"info")
        plan = preview_archives(self.f.root)
        self.assertEqual(len(plan.lots), 1)
        self.assertEqual([f["relative_path"] for f in plan.lots[0].files if f["kind"] == "压缩包"],
                         ["LOT001/outer/middle/deep/a.zip"])
        self.assertEqual(plan.lots[0].files[-1]["csv_names"], ["inside/" + name])
        self.assertFalse((self.f.product / ".atlas").exists())
        self.assertFalse((self.f.root / "_atlas_runs").exists())
        with self.assertRaisesRegex(ValueError, "先预览"):
            prepare_archives(self.f.root)
        self.assertFalse((self.f.root / "_atlas_runs").exists())

    def test_flat_all_csv_non_csv_backup_and_identity_deferred(self):
        name, content = self.csv_bytes()
        archive = self.bundle([(f"folder/{name}", content), ("badly_named.csv", content),
                               ("data.STD", b"STD"), ("notes.txt", b"notes"), ("yield.xlsx", b"xlsx")],
                              "outer/deep/supplier.csv.zip")
        original = archive.read_bytes()
        for filename in ("raw.std", "info.txt", "yield.xlsx"):
            (self.f.lot / "outer" / filename).write_bytes(filename.encode())
        result = self.prepare()
        self.assertEqual((result.prepared, result.csv_count, result.failed, result.backup_count), (1, 2, 0, 4))
        self.assertEqual({p.name for p in self.f.lot.iterdir()}, {name, "badly_named.csv"})
        self.assertFalse((self.f.lot / "imported_csv").exists())
        saved = read_snapshot(self.f.product)["archives"]["LOT001/outer/deep/supplier.csv.zip"]
        self.assertEqual((self.f.product / saved["source_backup"]).read_bytes(), original)
        self.assertTrue(saved["source_removed"])
        self.assertEqual({Path(m["relative_path"]).parent.as_posix() for m in saved["members"]}, {"LOT001"})
        plan = scan_root(self.f.root, self.f.config_path).products[0]
        self.assertEqual(plan.tasks[0].action, "INVALID")
        self.assertEqual(len(plan.pending_files), 1)
        self.assertEqual(self.f._run().processed, 0)
        self.assertEqual(self.f._bin_rows(), [])

    def test_nested_existing_csv_flattened_and_generated_results_protected(self):
        name, content = self.csv_bytes()
        source = self.f.lot / "nested/deep" / name
        source.parent.mkdir(parents=True)
        source.write_bytes(content)
        cleaned = self.f.lot / "summary_cleaning_data/result.csv"
        cleaned.parent.mkdir()
        cleaned.write_bytes(b"generated")
        (cleaned.parent / "keep.txt").write_bytes(b"output")
        report = self.f.product / "keep.xlsx"
        report.write_bytes(b"product report")
        self.assertTrue(scan_root(self.f.root, self.f.config_path).products[0].errors)
        result = self.prepare()
        self.assertEqual((result.csv_count, result.backup_count, result.failed), (1, 1, 0))
        self.assertEqual((self.f.lot / name).read_bytes(), content)
        self.assertFalse(source.parent.exists())
        self.assertEqual(cleaned.read_bytes(), b"generated")
        self.assertEqual(report.read_bytes(), b"product report")
        self.assertEqual(self.f._run().processed, 1)

    def test_preview_content_time_and_inventory_changes_rejected(self):
        name, content = self.csv_bytes()
        source = self.bundle([(name, content)])
        plan = preview_archives(self.f.root)
        info = source.stat()
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        self.assertEqual(self.prepare(approved_plan=plan).failed, 1)
        self.assertTrue(source.exists())
        self.assertFalse((self.f.lot / name).exists())
        plan = preview_archives(self.f.root)
        extra = self.f.lot / "new.txt"
        extra.write_bytes(b"new")
        self.assertEqual(self.prepare(approved_plan=plan).failed, 1)
        self.assertTrue(extra.exists())
        plan = preview_archives(self.f.root)
        previous = source.stat()
        changed = bytearray(source.read_bytes())
        changed[-1] ^= 1
        source.write_bytes(changed)
        os.utime(source, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertEqual(self.prepare(approved_plan=plan).failed, 1)
        self.assertFalse((self.f.lot / name).exists())

    def test_ignored_csv_kept_restore_and_content_change_require_confirmation(self):
        name, content = self.csv_bytes()
        self.bundle([(name, content), ("yield.csv", b"csv report")])
        self.prepare()
        pending = scan_root(self.f.root, self.f.config_path).products[0].pending_files[0]
        relative = pending["relative_path"]
        path = self.f.product / relative
        set_csv_ignored(self.f.product, relative, True)
        self.assertTrue(path.exists())
        self.assertEqual((self.f._run().processed, self.f._records()[0]["status"]), (1, "current"))
        self.assertTrue(PlotDataService(self.f.config_path).catalog(self.f.root).wafers[0].available)
        set_csv_ignored(self.f.product, relative, False)
        self.assertTrue(scan_root(self.f.root, self.f.config_path).products[0].errors)
        set_csv_ignored(self.f.product, relative, True)
        path.write_bytes(b"changed report")
        self.assertTrue(scan_root(self.f.root, self.f.config_path).products[0].errors)

    def test_ignore_inherits_only_unchanged_contents_across_archive_update(self):
        name, content = self.csv_bytes()
        self.bundle([(name, content), ("yield.csv", b"report")])
        self.prepare()
        pending = scan_root(self.f.root, self.f.config_path).products[0].pending_files[0]
        set_csv_ignored(self.f.product, pending["relative_path"], True)
        self.f._run()
        rows = [list(row) for row in fixtures.INITIAL_ROWS]
        rows[1][4] = 1
        _, changed = self.csv_bytes(rows=rows)
        self.bundle([(name, changed), ("yield.csv", b"report")])
        self.assertEqual(self.prepare().failed, 0)
        plan = scan_root(self.f.root, self.f.config_path).products[0]
        self.assertFalse(plan.errors)
        self.assertTrue(plan.pending_files[0]["ignored"])
        self.assertEqual(self.f._run().failed, 0)
        self.bundle([(name, changed), ("yield.csv", b"new report")])
        self.assertEqual(self.prepare().failed, 0)
        plan = scan_root(self.f.root, self.f.config_path).products[0]
        self.assertTrue(plan.errors)
        self.assertFalse(plan.pending_files[0]["ignored"])

    def test_repeated_preparation_and_wafer_processing_do_not_recalculate(self):
        name, content = self.csv_bytes()
        self.bundle([(name, content)])
        self.prepare()
        self.f._run()
        with patch("atlas_pipeline.archives.extract_csvs", side_effect=AssertionError("must skip")):
            result = self.prepare()
        self.assertEqual((result.prepared, result.csv_count, result.failed), (0, 0, 0))
        with patch("atlas_pipeline.pipeline.merge_and_clean", side_effect=AssertionError("must skip")):
            result = self.f._run()
        self.assertEqual((result.processed, result.skipped), (0, 1))

    def test_archive_source_removed_does_not_block_stats_or_full_plot_verification(self):
        name, content = self.csv_bytes()
        archive = self.bundle([(name, content)])
        self.prepare()
        self.assertFalse(archive.exists())
        self.assertEqual(self.f._run().processed, 1)
        service = PlotDataService(self.f.config_path)
        refs = service.catalog(self.f.root).wafers
        self.assertTrue(refs[0].available)
        service.validate_refs(refs, verify_hashes=True)
        self.assertEqual(self.f._run(verify_hashes=True).skipped, 1)

    def test_missing_published_csv_blocks_prior_results(self):
        name, content = self.csv_bytes()
        self.bundle([(name, content)])
        self.prepare()
        self.f._run()
        (self.f.lot / name).unlink()
        self.assertTrue(scan_root(self.f.root, self.f.config_path).products[0].errors)
        self.assertFalse(PlotDataService(self.f.config_path).catalog(self.f.root).wafers[0].available)

    def test_full_plot_verification_detects_same_size_time_csv_modification(self):
        name, content = self.csv_bytes()
        self.bundle([(name, content)])
        self.prepare()
        self.f._run()
        service = PlotDataService(self.f.config_path)
        refs = service.catalog(self.f.root).wafers
        path = self.f.lot / name
        previous = path.stat()
        path.write_bytes(path.read_bytes().replace(b"abc", b"xyz"))
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertTrue(service.catalog(self.f.root).wafers[0].available)
        with self.assertRaises(ValueError):
            service.validate_refs(refs, verify_hashes=True)

    def test_recursive_gzip_csv_std_and_zip_csv_gz(self):
        first, content = self.csv_bytes()
        second, content2 = self.csv_bytes(wafer=2)
        nested = self.f.lot / "a/b/c"
        nested.mkdir(parents=True)
        (nested / (first + ".gz")).write_bytes(gzip.compress(content))
        (nested / "test.std.gz").write_bytes(gzip.compress(b"STD"))
        self.bundle([("nested/" + second + ".gz", gzip.compress(content2))], "a/other.zip")
        result = self.prepare()
        self.assertEqual((result.prepared, result.csv_count, result.failed), (3, 2, 0))
        self.assertEqual({p.name for p in self.f.lot.iterdir()}, {first, second})
        self.assertEqual(self.f._run().processed, 2)

    def test_new_nested_retest_archive_invalidates_catalog_updates_one_wafer(self):
        name, content = self.csv_bytes()
        self.bundle([(name, content)])
        self.prepare()
        self.f._run()
        rt_name, rt_content = self.csv_bytes(suffix="RT1", rows=fixtures.RETEST_ROWS, ending="2026-09-17 12:00:00")
        self.bundle([(rt_name, rt_content)], "incoming/a/retest.csv.zip")
        service = PlotDataService(self.f.config_path)
        self.assertFalse(service.catalog(self.f.root).wafers[0].available)
        self.assertEqual(scan_root(self.f.root, self.f.config_path).products[0].tasks[0].action, "INVALID")
        self.assertEqual((self.prepare().prepared), 1)
        self.assertEqual(scan_root(self.f.root, self.f.config_path).products[0].tasks[0].action, "INPUT_CHANGED")
        self.assertEqual(self.f._run().processed, 1)
        self.assertEqual(self.f._bin_rows()[0]["总良率"], 1)
        output = self.f.product / self.f._records()[0]["cleaned_path"]
        self.assertNotIn("imported_csv", output.read_text(encoding="utf-8"))
        self.assertTrue(service.catalog(self.f.root).wafers[0].available)

    def test_changed_package_only_recalculates_changed_wafer_and_backs_up_old_csv(self):
        first, content = self.csv_bytes()
        second, content2 = self.csv_bytes(wafer=2)
        self.bundle([(first, content), (second, content2)])
        self.prepare()
        self.f._run()
        rows = [list(row) for row in fixtures.INITIAL_ROWS]
        rows[1][4] = 1
        _, changed = self.csv_bytes(rows=rows)
        self.bundle([(first, changed), (second, content2)])
        self.assertEqual(self.prepare().failed, 0)
        tasks = scan_root(self.f.root, self.f.config_path).products[0].tasks
        self.assertEqual([(t.wafer, t.action) for t in tasks], [("1", "INPUT_CHANGED"), ("2", "SKIP")])
        result = self.f._run()
        self.assertEqual((result.processed, result.skipped, result.failed), (1, 1, 0))
        backups = [p for p in self.backup_files() if p.is_file() and p.name == first]
        self.assertEqual([p.read_bytes() for p in backups], [content])

    def test_replaced_archive_missing_known_member_is_blocked(self):
        first, content = self.csv_bytes()
        second, content2 = self.csv_bytes(wafer=2)
        self.bundle([(first, content), (second, content2)])
        self.prepare()
        self.f._run()
        source = self.bundle([(first, content)])
        result = self.prepare()
        self.assertEqual(result.failed, 1)
        self.assertIn("缺少已登记CSV", result.errors[0])
        self.assertTrue(source.exists())
        self.assertEqual((self.f.lot / second).read_bytes(), content2)
        self.assertTrue(scan_root(self.f.root, self.f.config_path).products[0].errors)

    def test_identical_same_name_copies_one_csv_no_double_count(self):
        name, content = self.csv_bytes()
        self.bundle([("sub/" + name, content)], "a/a.zip")
        self.bundle([(name, content)], "b/b.zip")
        self.assertEqual((self.prepare().csv_count), 1)
        self.assertEqual(len(list(self.f.lot.glob("*.csv"))), 1)
        self.assertEqual(self.f._run().processed, 1)
        self.assertEqual(len(self.f._records()[0]["source_files"]), 1)

    def test_conflicting_same_name_packages_leave_entire_lot_untouched(self):
        name, content = self.csv_bytes()
        a = self.bundle([(name, content)], "a/a.zip")
        b = self.bundle([(name, b"different")], "b/b.zip")
        note = self.f.lot / "keep.txt"
        note.write_bytes(b"keep")
        result = self.prepare()
        self.assertEqual(result.failed, 1)
        self.assertIn("同名CSV", result.errors[0])
        self.assertTrue(a.exists() and b.exists() and note.exists())
        self.assertFalse((self.f.lot / name).exists())
        self.assertEqual(read_snapshot(self.f.product)["archives"], {})

    def test_unregistered_root_collision_refuses_overwrite(self):
        name, content = self.csv_bytes()
        existing = self.f.lot / name
        existing.write_bytes(b"manual data")
        source = self.bundle([(name, content)])
        self.assertEqual(self.prepare().failed, 1)
        self.assertEqual(existing.read_bytes(), b"manual data")
        self.assertTrue(source.exists())

    def test_publish_failure_rolls_back_created_files_and_preserves_originals(self):
        first, content = self.csv_bytes()
        second, content2 = self.csv_bytes(wafer=2)
        source = self.bundle([(first, content), (second, content2)])
        original_replace = os.replace

        def fail_second(src, dst):
            if Path(dst) == self.f.lot / second:
                raise OSError("simulated publish failure")
            return original_replace(src, dst)

        with patch("atlas_pipeline.archives.os.replace", side_effect=fail_second):
            result = self.prepare()
        self.assertEqual(result.failed, 1)
        self.assertTrue(source.exists())
        self.assertFalse((self.f.lot / first).exists())
        self.assertFalse((self.f.lot / second).exists())
        self.assertEqual(read_snapshot(self.f.product)["archives"], {})

    def test_corrupt_package_does_not_publish_partial_csv_or_move_other_files(self):
        name, content = self.csv_bytes()
        source = self.bundle([(name, content), ("unknown.csv.gz", b"not gzip")])
        note = self.f.lot / "keep.txt"
        note.write_bytes(b"keep")
        self.assertEqual(self.prepare().failed, 1)
        self.assertEqual(read_snapshot(self.f.product)["archives"], {})
        self.assertFalse(any(self.f.lot.glob("*.csv")))
        self.assertTrue(source.exists() and note.exists())

    def test_bad_zip_can_retry_without_removing_original(self):
        source = self.f.lot / "a.zip"
        source.write_bytes(b"bad zip")
        self.assertEqual(self.prepare().failed, 1)
        self.assertTrue(source.exists())
        name, content = self.csv_bytes()
        self.bundle([(name, content)], "a.zip")
        self.assertEqual(self.prepare().failed, 0)
        self.assertEqual(self.f._run().processed, 1)

    def test_preview_lists_all_archives_even_if_first_archive_corrupt(self):
        name, content = self.csv_bytes()
        (self.f.lot / "a_bad.zip").write_bytes(b"bad zip")
        good = self.bundle([(name, content)], "deep/b_good.zip")
        plan = preview_archives(self.f.root)
        self.assertEqual([Path(f["relative_path"]).name for f in plan.lots[0].files],
                         ["a_bad.zip", "b_good.zip"])
        self.assertIn("a_bad.zip", plan.lots[0].errors[0])
        self.assertEqual(self.prepare(approved_plan=plan).failed, 1)
        self.assertTrue(good.exists())
        self.assertFalse((self.f.lot / name).exists())

    def test_path_traversal_duplicate_entries_limits_rejected(self):
        for entries in ([("../escaped.csv", b"bad")], [("same.csv", b"one"), ("same.csv", b"two")]):
            with self.subTest(entries=entries):
                source = self.bundle(entries)
                self.assertEqual(self.prepare().failed, 1)
                self.assertTrue(source.exists())
        self.assertFalse((self.f.lot.parent / "escaped.csv").exists())
        self.bundle([("one.csv", b"123456")])
        with patch("atlas_pipeline.archives.MAX_UNPACKED_BYTES", 5):
            self.assertEqual(self.prepare().failed, 1)

    def test_unknown_gz_and_nested_archive_preserved_for_manual_handling(self):
        gz = self.f.lot / "unknown.gz"
        gz.write_bytes(gzip.compress(b"content"))
        source = self.bundle([("inside.zip", b"zip")])
        self.assertEqual(self.prepare().failed, 1)
        self.assertTrue(gz.exists() and source.exists())

    def test_identity_lot_validation_deferred_until_processing(self):
        name, content = self.csv_bytes()
        self.bundle([(name.replace("LOT001", "OTHERLOT"), content)])
        self.assertEqual(self.prepare().csv_count, 1)
        plan = scan_root(self.f.root, self.f.config_path).products[0]
        self.assertTrue(plan.errors)
        self.assertIn("不一致", plan.errors[0])

    def test_preparation_independent_of_yaml_configuration(self):
        name, content = self.csv_bytes()
        self.bundle([(name, content)])
        self.f.config_path.unlink()
        self.assertEqual(self.prepare().csv_count, 1)

    def test_legacy_only_current_members_flatten_history_backup_and_ignore_inherited(self):
        name, content = self.csv_bytes()
        active = self.f.lot / "imported_csv/archive/current/nested" / name
        active.parent.mkdir(parents=True)
        active.write_bytes(content)
        stale = self.f.lot / "imported_csv/archive/history/stale.csv"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"historical data")
        report = self.f.lot / "imported_csv/archive/current/yield.csv"
        report.write_bytes(b"csv report")
        source = self.bundle([("nested/" + name, content), ("yield.csv", b"csv report")])
        members = [{**stamp(active, self.f.product), "archive_member": "nested/" + name},
                   {**stamp(report, self.f.product), "archive_member": "yield.csv"}]
        state = ProductState(self.f.product)
        try:
            state.save_archive("LOT001/supplier.csv.zip",
                               {**stamp(source, self.f.product), "lot": "LOT001", "status": "current", "members": members})
        finally:
            state.close()
        set_csv_ignored(self.f.product, report.relative_to(self.f.product).as_posix(), True)
        # Original ZIP may no longer be available in a legacy delivery.
        source.unlink()
        self.assertEqual(self.prepare().failed, 0)
        self.assertEqual({p.name for p in self.f.lot.iterdir()}, {name, "yield.csv"})
        self.assertFalse((self.f.lot / "stale.csv").exists())
        self.assertFalse((self.f.lot / "imported_csv").exists())
        pending = scan_root(self.f.root, self.f.config_path).products[0].pending_files
        self.assertTrue(pending[0]["ignored"])
        self.assertEqual(self.f._run().processed, 1)
        self.assertEqual(len([p for p in self.backup_files() if p.is_file()]), 3)

    def test_old_cache_without_archive_tables_readable(self):
        self.f._write_csv()
        self.f._run()
        with sqlite3.connect(self.f.product / ".atlas/state.sqlite") as connection:
            connection.execute("DROP TABLE archives")
            connection.execute("DROP TABLE ignored_files")
        snapshot = read_snapshot(self.f.product)
        self.assertEqual(snapshot["archives"], {})
        self.assertEqual(snapshot["ignored_files"], {})
        self.assertEqual(self.f._run().skipped, 1)

    def test_shared_same_name_member_cannot_be_changed_by_only_one_owner(self):
        name, content = self.csv_bytes()
        self.bundle([(name, content)], "a.zip")
        self.bundle([(name, content)], "b.zip")
        self.assertEqual(self.prepare().failed, 0)
        source = self.bundle([(name, b"changed")], "a.zip")
        self.assertEqual(self.prepare().failed, 1)
        self.assertEqual((self.f.lot / name).read_bytes(), content)
        self.assertTrue(source.exists())

    def test_failed_lot_does_not_stop_another_lot(self):
        bad = self.f.product / "LOT002"
        bad.mkdir()
        (bad / "bad.zip").write_bytes(b"bad zip")
        name, content = self.csv_bytes()
        self.bundle([(name, content)])
        result = self.prepare()
        self.assertEqual((result.csv_count, result.failed), (1, 1))
        self.assertTrue((self.f.lot / name).exists())
        self.assertTrue((bad / "bad.zip").exists())

    def test_link_file_rejected_without_moving_inputs(self):
        name, content = self.csv_bytes()
        source = self.bundle([(name, content)])
        original = Path.is_symlink
        with patch("pathlib.Path.is_symlink", lambda path: path == source or original(path)):
            plan = preview_archives(self.f.root)
            self.assertTrue(plan.lots[0].errors)
            self.assertEqual(self.prepare(approved_plan=plan).failed, 1)
        self.assertTrue(source.exists())
        self.assertFalse((self.f.lot / name).exists())


if __name__ == "__main__":
    unittest.main()
