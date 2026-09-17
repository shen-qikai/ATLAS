"""Optional hidden, synthetic full-app test of the new three plotting pages."""

from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matplotlib.figure import Figure
from atlas_pipeline.curve_cache import PROBABILITY_CURVES
from atlas_pipeline.plot_data import SHARED_PLOT_DATA
from atlas_pipeline.plot_workspace import read_json, versions
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
        PROBABILITY_CURVES.clear()
        root = tk.Tk()
        root.withdraw()
        app = MainApp(root)
        assert len(app.notebook.tabs()) == 4
        pages = [app.probability_ui, app.bin_map_ui, app.test_map_ui]
        assert len({id(page.service) for page in pages}) == 1
        assert all(str(page.run_button["state"]) == "disabled" for page in pages)
        app.plot_root_var.set(str(fixture.root))
        with patch("direct_plot_ui.messagebox.showinfo"), patch("direct_plot_ui.messagebox.showerror") as errors, \
             patch("direct_plot_ui.messagebox.showwarning"), patch.object(tk.Variable, "get", checked_get), \
             patch.object(Figure, "savefig", small_save), \
             patch("direct_plot_ui.filedialog.askdirectory", return_value=str(fixture.product / "plots/probability")) as location_dialog, \
             patch("direct_plot_ui.simpledialog.askstring", side_effect=["Lot1与Lot2对比", "Lot1与Lot2对比", "增大标记结果"]):
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
                    page.x_modes["spec"].set(True)
                page.start()
                assert page.busy
                assert str(page.run_button["state"]) == "disabled"
                wait(root, page)
                assert page.last_result is not None, [call.args for call in errors.call_args_list]
                assert page.last_result.image_count > 1
                assert str(page.run_button["state"]) == "normal"
                assert str(page.save_button["state"]) == "normal"
                assert page.preview_result is not None
                assert read_json(page.last_result.output_folder / "manifest.json")["options"]["output_dpi"] == 90
                assert versions(page.task) == []
                assert page.preview_window.state() == "withdrawn"
            assert not errors.called, [call.args for call in errors.call_args_list]
            assert SHARED_PLOT_DATA.csv_reads == 4  # Each Wafer: VOUT then X/Y/BIN, Test map reuses both.
            assert SHARED_PLOT_DATA.cache_hits >= 2
            probability = app.probability_ui
            task = probability.task
            viewer = probability.preview_window
            draft = probability.last_result.output_folder
            assert (draft / "auto_VOUT_spec1_prob.png").exists()
            assert (draft / "spec_VOUT_spec1_prob.png").exists()
            probability._select_tests(True)
            assert str(probability.save_button["state"]) == "disabled"
            probability._select_tests(False)
            assert not probability.test_list.curselection()
            probability.test_list.selection_set(probability.test_names.index("VOUT"))
            probability.marker_size.set("7")
            assert str(probability.save_button["state"]) == "disabled"
            probability.start()
            wait(root, probability)
            assert probability.preview_window is viewer
            assert probability.last_result.output_folder == draft
            assert probability.task == task
            assert versions(task) == []
            with patch("direct_plot_ui.filedialog.askdirectory", return_value=""):
                probability._save()
                assert not probability.busy and versions(task) == []
            with patch("direct_plot_ui.simpledialog.askstring", return_value=None):
                probability._save()
                assert not probability.busy and versions(task) == []
            probability._save()
            wait(root, probability)
            saved = probability.last_result.output_folder
            assert saved == fixture.product / "plots/probability/Lot1与Lot2对比"
            assert location_dialog.call_args.kwargs["initialdir"] == str(fixture.product / "plots/probability")
            manifest = read_json(saved / "manifest.json")
            assert manifest["options"]["output_dpi"] == 300
            assert manifest["options"]["x_modes"] == ["auto", "spec"]
            immutable = (saved / "manifest.json").read_bytes()
            probability._save()
            wait(root, probability)
            assert probability.last_result.output_folder == saved
            assert len(versions(task)) == 1
            probability.marker_size.set("8")
            probability.start()
            wait(root, probability)
            external_parent = fixture.folder / "engineer_selected_output"
            external_parent.mkdir()
            location_dialog.return_value = str(external_parent)
            probability._save()
            wait(root, probability)
            assert probability.last_result.output_folder == external_parent / "增大标记结果"
            probability._view_current()
            assert probability.image_files and all(external_parent in path.parents for path in probability.image_files)
            completed_preview = probability.preview_result
            assert (saved / "manifest.json").read_bytes() == immutable
            probability._history()
            history = next(child for child in probability.parent.winfo_children()
                           if isinstance(child, tk.Toplevel) and "历史版本" in child.title())
            table = next(child for child in history.winfo_children() if isinstance(child, ttk.Treeview))
            table.selection_set(next(iid for iid in table.get_children() if table.item(iid, "values")[0] == "Lot1与Lot2对比"))
            actions = next(child for child in history.winfo_children() if isinstance(child, ttk.Frame))
            restore = next(child for child in actions.winfo_children() if str(child["text"]).startswith("恢复"))
            restore.invoke()
            root.update()
            assert probability.marker_size.get() == "7"
            assert probability.x_modes["auto"].get() and probability.x_modes["spec"].get()
            assert len(probability.selector.selected_refs()) == 2
            assert len(probability.selector.references) == 1
            assert str(probability.save_button["state"]) == "disabled"
            assert not history.winfo_exists()
            # A new task is independent; selecting the old task restores its latest draft options.
            probability.task_name.set("新的对比任务")
            probability._new_task()
            assert probability.task != task
            probability.task_var.set(task.folder.name)
            probability._choose_task()
            root.update()
            assert probability.task == task
            assert probability.marker_size.get() == "8"
            assert str(probability.save_button["state"]) == "disabled"
            assert SHARED_PLOT_DATA.csv_reads == 4
            assert PROBABILITY_CURVES.computations == 2
            assert PROBABILITY_CURVES.hits >= 8
            assert not errors.called, [call.args for call in errors.call_args_list]
            # Late results from a worker must not reload a task from the old root.
            release = threading.Event()
            def delayed_preview(*args, **kwargs):
                assert release.wait(10), "Root-change test timed out"
                return completed_preview
            with patch("direct_plot_ui.run_preview", delayed_preview):
                probability.start()
                assert probability.busy
                app.plot_root_var.set(str(fixture.folder))
                root.update()
                release.set()
                wait(root, probability)
            assert all(not page.selector.selected_refs() for page in pages)
            assert all(str(page.run_button["state"]) == "disabled" for page in pages)
            assert all(page.task is None for page in pages)
            assert all(str(page.save_button["state"]) == "disabled" for page in pages)
            assert all(page.last_result is None for page in pages)
            assert all(str(page.view_button["state"]) == "disabled" for page in pages)
            assert not errors.called, [call.args for call in errors.call_args_list]
        print("Hidden full-app: draft viewers, named/custom save, cancel/reuse/external view/history restore: OK")
    finally:
        if root is not None:
            root.destroy()
        SHARED_PLOT_DATA.clear_cache()
        PROBABILITY_CURVES.clear()
        SHARED_PLOT_DATA.config_path = previous_config
        fixture.tearDown()


if __name__ == "__main__":
    main()
