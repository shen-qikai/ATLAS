"""Optional hidden Tk/CLI smoke check; all data is synthetic and temporary."""

from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
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
        fixture._write_csv()
        automatic_yield.CONFIG_PATH = fixture.config_path
        root = tk.Tk()
        root.withdraw()
        app = MainApp(root)
        root.update_idletasks()
        assert len(app.notebook.tabs()) == 8
        ui = app.automatic_yield_ui
        assert str(ui.run_button["state"]) == "disabled"
        ui.root_var.set(str(fixture.root))
        with patch("automatic_yield.messagebox.showinfo"), patch("automatic_yield.messagebox.showerror"), \
             patch("automatic_yield.messagebox.showwarning"):
            ui._start(False)
            wait(root, ui)
            assert ui.plan is not None
            assert str(ui.run_button["state"]) == "normal"
            assert ui.plan.products[0].tasks[0].action == "NEW"
            ui._start(True)
            wait(root, ui)
            assert len(list(fixture.product.glob("*.xlsx"))) == 3
            assert str(ui.run_button["state"]) == "disabled"
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "run_pipeline.py"),
             str(fixture.root), "--config", str(fixture.config_path), "--preview"],
            capture_output=True, text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "SKIP" in completed.stdout
        print("Hidden full-app GUI + asynchronous Preview/Execute + CLI: OK")
    finally:
        if root is not None:
            root.destroy()
        fixture.tearDown()


if __name__ == "__main__":
    main()
