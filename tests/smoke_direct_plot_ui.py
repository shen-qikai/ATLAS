"""Optional hidden, synthetic full-app test of the new three plotting pages."""

from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matplotlib.figure import Figure
from atlas_pipeline.plot_data import SHARED_PLOT_DATA
from main import MainApp
from test_incremental_pipeline import PipelineIntegrationTests


def wait(root, target, seconds=30):
    deadline = time.monotonic() + seconds
    while target.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    assert not target.busy, "GUI worker timed out"
    root.update()


def main():
    fixture = PipelineIntegrationTests()
    fixture.setUp()
    previous_config = SHARED_PLOT_DATA.config_path
    root = None
    original_get = tk.Variable.get
    original_save = Figure.savefig
    def checked_get(variable):
        assert threading.current_thread() is threading.main_thread(), "Tk read from a worker"
        return original_get(variable)
    def small_save(figure, path, *args, **kwargs):
        kwargs["dpi"] = 35
        return original_save(figure, path, *args, **kwargs)
    try:
        fixture._write_csv()
        lot2 = fixture.product / "LOT002"
        lot2.mkdir()
        fixture._write_csv(lot=lot2)
        fixture._run()
        SHARED_PLOT_DATA.config_path = fixture.config_path
        SHARED_PLOT_DATA.clear_cache()
        root = tk.Tk()
        root.withdraw()
        app = MainApp(root)
        assert len(app.notebook.tabs()) == 8
        pages = [app.probability_ui, app.bin_map_ui, app.test_map_ui]
        assert len({id(page.service) for page in pages}) == 1
        assert all(str(page.run_button["state"]) == "disabled" for page in pages)
        app.plot_root_var.set(str(fixture.root))
        with patch("direct_plot_ui.messagebox.showinfo"), patch("direct_plot_ui.messagebox.showerror") as errors, \
             patch("direct_plot_ui.messagebox.showwarning"), patch.object(tk.Variable, "get", checked_get), \
             patch.object(Figure, "savefig", small_save):
            for page in pages:
                page.selector.refresh()
                wait(root, page.selector)
                assert len(page.selector.rows) == 2
                page.selector.select_all()
                root.update()
                assert len(page.selector.selected_refs()) == 2
                assert str(page.run_button["state"]) == "normal"
                if page.kind != "bin":
                    page.test_list.selection_set(page.test_names.index("VOUT"))
                if page.kind == "probability":
                    first = next(iter(page.selector.rows))
                    page.selector.tree.selection_set(first)
                    page.selector.set_role(True)
                    page.selector.select_all()
                    root.update()
                    page.highlight_enabled.set(True)
                    page._highlight_analysis()
                page.start()
                assert page.busy
                assert str(page.run_button["state"]) == "disabled"
                wait(root, page)
                assert page.last_result is not None, [call.args for call in errors.call_args_list]
                assert page.last_result.image_count > 1
                assert str(page.run_button["state"]) == "normal"
            assert not errors.called, [call.args for call in errors.call_args_list]
            assert SHARED_PLOT_DATA.csv_reads == 4  # Each Wafer: VOUT then X/Y/BIN, Test map reuses both.
            assert SHARED_PLOT_DATA.cache_hits >= 2
            # Changing the shared root invalidates all three pages' selections.
            app.plot_root_var.set(str(fixture.folder))
            root.update()
            assert all(not page.selector.selected_refs() for page in pages)
            assert all(str(page.run_button["state"]) == "disabled" for page in pages)
        print("Hidden full-app selector + three asynchronous direct plot jobs + shared cache: OK")
    finally:
        if root is not None:
            root.destroy()
        SHARED_PLOT_DATA.clear_cache()
        SHARED_PLOT_DATA.config_path = previous_config
        fixture.tearDown()


if __name__ == "__main__":
    main()
