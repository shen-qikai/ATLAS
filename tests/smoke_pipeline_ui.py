"""Optional hidden Tk/CLI smoke check; all data is synthetic and temporary."""

from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import automatic_yield
from main import MainApp
from test_incremental_pipeline import PipelineIntegrationTests


def wait(root, ui):
    deadline = time.monotonic() + 20
    while ui.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    assert not ui.busy, "GUI worker timed out"


def main():
    fixture = PipelineIntegrationTests()
    fixture.setUp()
    root = None
    try:
        raw = fixture._write_csv()
        archive = fixture.lot / "outer/middle/deep/supplier.csv.zip"
        archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.write(raw, "nested/" + raw.name)
            bundle.writestr("yield.csv", "csv report")
            bundle.writestr("data.std", "std")
        raw.unlink()
        automatic_yield.CONFIG_PATH = fixture.config_path
        root = tk.Tk()
        root.withdraw()
        app = MainApp(root)
        root.update_idletasks()
        assert len(app.notebook.tabs()) == 4
        assert app.notebook.tab(app.notebook.tabs()[0], "text") == "数据清洗与良率汇总"
        ui = app.automatic_yield_ui
        assert int(ui.log["height"]) == 23
        assert ui.prepare_button.master is not ui.preview_button.master
        assert ui.archive_preview_button.master is ui.prepare_button.master
        assert str(ui.prepare_button["state"]) == "disabled"
        assert str(ui.run_button["state"]) == "disabled"
        ui.root_var.set(str(fixture.root))
        with patch("automatic_yield.messagebox.showinfo"), patch("automatic_yield.messagebox.showerror"), \
             patch("automatic_yield.messagebox.showwarning"), patch("automatic_yield.messagebox.askyesno", return_value=True):
            ui._start(False, archive_preview=True)
            wait(root, ui)
            assert ui.archive_plan is not None
            assert ui.plan is None
            assert len(ui.archive_table.get_children()) == 1
            assert "outer/middle/deep/supplier.csv.zip" in str(ui.archive_table.item(ui.archive_table.get_children()[0], "values"))
            assert not (fixture.product / ".atlas").exists()
            assert not (fixture.root / "_atlas_runs").exists()
            assert archive.is_file()
            assert str(ui.prepare_button["state"]) == "normal"
            ui._confirm_prepare()
            wait(root, ui)
            assert ui.plan is not None
            assert str(ui.run_button["state"]) == "normal"
            assert ui.plan.products[0].tasks[0].action == "INVALID"
            pending = [iid for iid, item in ui.csv_rows.items() if not item[2]]
            assert len(pending) == 1
            ui.table.selection_set(pending[0])
            ui._toggle_ignore()
            wait(root, ui)
            assert ui.plan.products[0].tasks[0].action == "NEW"
            ui._start(True)
            wait(root, ui)
            assert len(list(fixture.product.glob("*.xlsx"))) == 3
            assert (fixture.lot / "summary_cleaning_data/WL111_LOT001_W01_summary_cleaning.csv").is_file()
            assert not archive.exists()
            assert (fixture.lot / raw.name).is_file()
            assert not (fixture.lot / "imported_csv").exists()
            assert str(ui.prepare_button["state"]) == "disabled"
            assert str(ui.run_button["state"]) == "disabled"
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "run_pipeline.py"),
             str(fixture.root), "--config", str(fixture.config_path), "--preview"],
            capture_output=True, text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "SKIP" in completed.stdout
        prepared = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "run_pipeline.py"),
             str(fixture.root), "--prepare-csv", "--confirm-archive-cleanup"], capture_output=True, text=True,
        )
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        assert "LOT_READY" in prepared.stdout
        print("Hidden full-app GUI + asynchronous Preview/Execute + CLI: OK")
    finally:
        if root is not None:
            root.destroy()
        fixture.tearDown()


if __name__ == "__main__":
    main()
