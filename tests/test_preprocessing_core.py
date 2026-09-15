from pathlib import Path
import gzip
import tempfile
import unittest
import zipfile

from preprocessing_core import (
    ExecutionError,
    PreprocessingService,
    ProductProfileStore,
    extract_archive,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "products.yaml"


class ProductProfileTests(unittest.TestCase):
    def setUp(self):
        self.store = ProductProfileStore.from_yaml(CONFIG_PATH)

    def test_product_profiles_and_examples_load(self):
        self.assertEqual(
            self.store.product_names,
            ("WS1201", "WS1203", "WS1234", "WS1256"),
        )
        profile = self.store.get("ws1234")
        self.assertEqual(profile.naming_version, 1)
        self.assertEqual(profile.examples[0].expected_lot, "DPJ580")

    def test_missing_product_is_rejected(self):
        self.assertIsNone(self.store.resolve_name("WS9999"))


class PreprocessingServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = ProductProfileStore.from_yaml(CONFIG_PATH)
        self.service = PreprocessingService(self.store)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write(self, name: str, content: str = "synthetic,data\n") -> Path:
        path = self.folder / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_preview_uses_legacy_compatible_output_name(self):
        original = "ABC_DPJ579-01-X.csv"
        self._write(original)

        preview = self.service.build_preview(self.folder, "WS1234")

        self.assertTrue(preview.validation_passed)
        self.assertEqual(preview.matched_files, 1)
        self.assertEqual(
            preview.items[0].standard_filename,
            "WS1234_DPJ579_01#_ABC_DPJ579-01-X.csv",
        )

    def test_unmatched_file_blocks_execution(self):
        self._write("unknown.csv")

        preview = self.service.build_preview(self.folder, "WS1234")

        self.assertFalse(preview.validation_passed)
        self.assertEqual(preview.items[0].status, "UNMATCHED")
        self.assertEqual(preview.items[0].error, "Regex did not match")

    def test_normalized_file_is_not_processed_again(self):
        self._write("WS1234_DPJ579_01#_ABC_DPJ579-01-X.csv")

        scan = self.service.scan_folder(self.folder)
        preview = self.service.build_preview(self.folder, "WS1234")

        self.assertEqual(scan.raw_csv, ())
        self.assertEqual(len(scan.normalized_csv), 1)
        self.assertFalse(preview.validation_passed)

    def test_existing_output_collision_blocks_execution(self):
        original = "ABC_DPJ579-01-X.csv"
        self._write(original)
        self._write(f"WS1234_DPJ579_01#_{original}")

        preview = self.service.build_preview(self.folder, "WS1234")

        self.assertFalse(preview.validation_passed)
        self.assertEqual(preview.items[0].status, "COLLISION")

    def test_execute_renames_and_writes_mapping_log(self):
        original = "ABC_DPJ579-01-X.csv"
        target = "WS1234_DPJ579_01#_ABC_DPJ579-01-X.csv"
        self._write(original)
        preview = self.service.build_preview(self.folder, "WS1234")

        result = self.service.execute(preview)

        self.assertEqual(result.renamed_files, 1)
        self.assertFalse((self.folder / original).exists())
        self.assertTrue((self.folder / target).exists())
        log_text = (self.folder / "atlas_preprocessing.log").read_text(encoding="utf-8")
        self.assertIn(f"{original} -> {target}", log_text)
        self.assertIn("result: SUCCESS", log_text)

    def test_folder_change_after_preview_blocks_execution(self):
        self._write("ABC_DPJ579-01-X.csv")
        preview = self.service.build_preview(self.folder, "WS1234")
        self._write("ABC_DPJ579-02-X.csv")

        with self.assertRaises(ExecutionError):
            self.service.execute(preview)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_gz_extracts_without_deleting_source_by_default(self):
        archive = self.folder / "sample.csv.gz"
        with gzip.open(archive, "wb") as compressed:
            compressed.write(b"synthetic,data\n")

        count = extract_archive(archive, self.folder)

        self.assertEqual(count, 1)
        self.assertTrue(archive.exists())
        self.assertEqual((self.folder / "sample.csv").read_bytes(), b"synthetic,data\n")

    def test_zip_refuses_to_overwrite_existing_file(self):
        existing = self.folder / "sample.csv"
        existing.write_text("existing", encoding="utf-8")
        archive = self.folder / "sample.zip"
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("sample.csv", "replacement")

        with self.assertRaises(ExecutionError):
            extract_archive(archive, self.folder)

        self.assertEqual(existing.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
