"""Optional hidden full-app replacement UI check; synthetic data only."""

import json
import os
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import automatic_yield
from atlas_pipeline.archives import prepare_archives, preview_archives
from atlas_pipeline.state import read_snapshot
from main import MainApp
from test_incremental_pipeline import PipelineIntegrationTests, RETEST_ROWS


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def wait(root, ui):
    deadline = time.monotonic() + 30
    while ui.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    assert not ui.busy, "GUI worker timed out"
    root.update()


def select_first(root, ui):
    iid = next(iid for iid, (_, key) in ui.replacement_rows.items() if json.loads(key)[1] == "1")
    ui.table.selection_set(iid)
    root.update()
    assert str(ui.replacement_button["state"]) == "normal"


def confirm_widgets(ui):
    window = ui.replacement_window
    widgets = list(descendants(window))
    confirm = next(item for item in widgets if isinstance(item, ttk.Button) and str(item["text"]).startswith("确认替换"))
    acknowledge = next(item for item in widgets if isinstance(item, ttk.Checkbutton))
    reason = next(item for item in widgets if isinstance(item, ttk.Entry))
    return window, confirm, acknowledge, reason


def main():
    fixture = PipelineIntegrationTests()
    fixture.setUp()
    previous_config = automatic_yield.CONFIG_PATH
    root = None
    original_get = tk.Variable.get
    def checked_get(variable):
        assert threading.current_thread() is threading.main_thread(), "Tk read from worker"
        return original_get(variable)
    try:
        archive = fixture.lot / "supplier.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for wafer in (1, 2):
                path = fixture._write_csv(wafer=wafer)
                bundle.writestr(path.name, path.read_bytes())
                path.unlink()
        prepare_archives(fixture.root, approved_plan=preview_archives(fixture.root))
        fixture._run()
        before = read_snapshot(fixture.product)
        other_key = next(key for key, record in before["wafers"].items() if record["wafer"] == "2")
        first_key = next(key for key, record in before["wafers"].items() if record["wafer"] == "1")
        old_output = fixture.product / before["wafers"][first_key]["cleaned_path"]
        old_bytes = old_output.read_bytes()
        corrected = fixture._write_csv(rows=RETEST_ROWS)
        corrected = corrected.rename(corrected.with_name(corrected.stem + "_CORRECTED.csv"))
        automatic_yield.CONFIG_PATH = fixture.config_path
        root = tk.Tk()
        root.withdraw()
        app = MainApp(root)
        ui = app.automatic_yield_ui
        with patch.object(tk.Variable, "get", checked_get), \
             patch("automatic_yield.messagebox.showinfo"), patch("automatic_yield.messagebox.showwarning"), \
             patch("automatic_yield.messagebox.showerror") as errors, \
             patch("automatic_yield.messagebox.askyesno", return_value=True) as consent:
            ui.root_var.set(str(fixture.root))
            ui._start(False)
            wait(root, ui)
            select_first(root, ui)
            ui.replacement_button.invoke()
            wait(root, ui)
            window, confirm, acknowledge, reason = confirm_widgets(ui)
            assert window.state() == "withdrawn"
            assert str(confirm["state"]) == "disabled"
            table = next(item for item in descendants(window) if isinstance(item, ttk.Treeview))
            rows = [table.item(iid, "values") for iid in table.get_children()]
            assert len(rows) == 2
            assert {row[1] for row in rows} == {"历史", "当前"}
            assert all(len(row[5]) == 64 for row in rows)
            assert read_snapshot(fixture.product) == before
            assert not (fixture.product / ".atlas/input_history").exists()
            cancel = next(item for item in descendants(window) if isinstance(item, ttk.Button) and str(item["text"]).startswith("取消"))
            cancel.invoke()
            assert read_snapshot(fixture.product) == before
            ui._start(False)
            wait(root, ui)
            select_first(root, ui)
            ui.replacement_button.invoke()
            wait(root, ui)
            window, confirm, acknowledge, reason = confirm_widgets(ui)
            acknowledge.invoke()
            reason.delete(0, "end")
            reason.insert(0, "Synthetic approved supplier correction")
            consent.return_value = False
            confirm.invoke()
            assert not ui.busy and window.winfo_exists()
            assert read_snapshot(fixture.product) == before
            consent.return_value = True
            confirm.invoke()
            wait(root, ui)
            assert not window.winfo_exists()
            after = read_snapshot(fixture.product)
            first = after["wafers"][first_key]
            assert first["status"] == "current" and first["metrics"]["effective_die_count"] == 1
            assert after["wafers"][other_key] == before["wafers"][other_key]
            assert (fixture.product / first["input_history"] / "previous_cleaned.csv").read_bytes() == old_bytes
            audit = json.loads((fixture.product / first["input_history"] / "record.json").read_text(encoding="utf-8"))
            assert audit["event"]["approval"]["reason"] == "Synthetic approved supplier correction"
            assert all(task.action == "SKIP" for task in ui.plan.products[0].tasks)
            # Approval expiry also detects a same-size/time overwrite after the dialog opens.
            select_first(root, ui)
            ui.replacement_button.invoke()
            wait(root, ui)
            window, confirm, acknowledge, reason = confirm_widgets(ui)
            stamp = corrected.stat()
            corrected.write_bytes(corrected.read_bytes().replace(b",4,4,3,", b",5,4,3,"))
            os.utime(corrected, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            acknowledge.invoke()
            confirm.invoke()
            wait(root, ui)
            assert read_snapshot(fixture.product) == after
            assert any(task.action == "INPUT_CHANGED" for task in ui.plan.products[0].tasks)
            # Normal execution must honor the strict content change discovered by that scan.
            ui.run_button.invoke()
            wait(root, ui)
            assert read_snapshot(fixture.product)["wafers"][first_key]["metrics"]["tests"]["VOUT"]["mean"] == 5
            assert not errors.called, [call.args for call in errors.call_args_list]
        print("Hidden full-app replacement UI: preview/cancel/consent, selected-only, history, stale SHA and strict recheck: OK")
    finally:
        if root is not None:
            root.destroy()
        automatic_yield.CONFIG_PATH = previous_config
        fixture.tearDown()


if __name__ == "__main__":
    main()
