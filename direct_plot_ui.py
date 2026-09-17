"""Direct-data plotting pages: shared selector at left, legacy options at right."""

import math
import os
from datetime import datetime
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from tkinter.scrolledtext import ScrolledText
from PIL import Image, ImageTk

from atlas_pipeline.plot_data import SHARED_PLOT_DATA
from atlas_pipeline.plot_jobs import PlotOptions, parse_bin_tasks, validate_options
from atlas_pipeline.curve_cache import PROBABILITY_CURVES
from atlas_pipeline.plot_workspace import (
    checked, create_task, export_destination, read_json, run_preview, save_version, task_list, versions,
)
from plot_selection import WaferSelector


class DirectPlotApp:
    def __init__(self, parent, kind, service=None, root_var=None):
        self.parent, self.kind = parent, kind
        self.service = service if service is not None else SHARED_PLOT_DATA
        self.events = queue.Queue()
        self.busy = False
        self.running_task = None
        self.setting_widgets = []
        self.test_names = []
        self.highlight_refs = []
        self.last_result = None
        self.task = None
        self.preview_result = None
        self.tasks = {}
        self.task_var = tk.StringVar()
        self.task_name = tk.StringVar(value={"bin": "BIN分析", "test": "测试项分析", "probability": "概率分布分析"}[kind])
        self.preview_window = None
        self.notch = tk.IntVar(value=6)
        self.sort_method = tk.StringVar(value="alphabetical")
        self.manual_order = tk.StringVar()
        self.minimum = tk.StringVar()
        self.maximum = tk.StringVar()
        self.x_mode = tk.StringVar(value="auto")
        self.x_modes = {mode: tk.BooleanVar(value=mode == "auto") for mode in ("auto", "spec", "manual")}
        self.marker_size = tk.StringVar(value="5")
        self.x_range = tk.StringVar()
        self.grouping = tk.StringVar(value="wafer")
        self.highlight_enabled = tk.BooleanVar(value=False)
        self.verify_hashes = tk.BooleanVar(value=False)
        self.test_expression = tk.StringVar()
        self.modes = {mode: tk.BooleanVar(value=mode == "auto")
                      for mode in ("auto", "manual", "usl_lsl", "pass_fail")}
        self.combine_modes = {mode: tk.BooleanVar(value=False) for mode in self.modes}
        self.bin_modes = [tk.BooleanVar(value=False), tk.BooleanVar(value=True), tk.BooleanVar(value=False)]
        self.bin_text = tk.StringVar()
        self.bin_combine_vars = []
        self.output_var = tk.StringVar(value="反复调整只更新草稿；保存高清图片时可选择位置和文件夹名称")
        panes = ttk.Panedwindow(parent, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)
        self.selector = WaferSelector(panes, self.service, self._selection_changed,
                                      self.log, root_var, roles=kind == "probability")
        self.selector.configure(width=355)
        panes.add(self.selector, weight=0)
        right = ttk.Frame(panes)
        panes.add(right, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(2, weight=1)
        # Scroll settings rather than hiding existing controls on smaller screens.
        canvas_frame = ttk.Frame(right)
        canvas_frame.grid(row=0, column=0, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        canvas = tk.Canvas(canvas_frame, highlightthickness=0, height=510)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        settings = ttk.LabelFrame(canvas, text="第二列：绘图功能与参数", padding=10)
        window = canvas.create_window((0, 0), window=settings, anchor="nw")
        settings.columnconfigure(0, weight=1)
        settings.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        self.settings = settings
        self.row = 0
        self._task_ui()
        if kind != "bin":
            self._tests_ui()
        if kind == "probability":
            self._probability_ui()
        elif kind == "test":
            self._test_ui()
        else:
            self._bin_ui()
        if kind != "probability":
            self._map_common_ui()
        self._add(self._check(settings, "完整校验选中片的文件内容（更严格，加载更慢）", self.verify_hashes))
        self._add(ttk.Label(settings, text="普通模式检查文件集合、大小和修改时间；缓存未命中时校验清洗文件 SHA256。",
                            wraplength=470, foreground="#666666"))
        buttons = ttk.Frame(right)
        buttons.grid(row=1, column=0, sticky="ew", pady=6)
        self.run_button = ttk.Button(buttons, text="更新预览（不归档）", command=self.start, state="disabled")
        self.run_button.pack(side="left", fill="x", expand=True)
        self.save_button = ttk.Button(buttons, text="保存高清版本", command=self._save, state="disabled")
        self.save_button.pack(side="left", padx=4)
        self.cache_button = ttk.Button(buttons, text="清空明细缓存", command=self._clear_cache)
        self.cache_button.pack(side="left", padx=4)
        logs = ttk.LabelFrame(right, text="运行日志", padding=5)
        logs.grid(row=2, column=0, sticky="nsew")
        self.logbox = ScrolledText(logs, height=7, state="disabled", wrap="word")
        self.logbox.pack(fill="both", expand=True)
        output = ttk.Frame(right)
        output.grid(row=3, column=0, sticky="ew", pady=4)
        output.columnconfigure(0, weight=1)
        ttk.Entry(output, textvariable=self.output_var, state="readonly").grid(row=0, column=0, sticky="ew")
        self.open_button = ttk.Button(output, text="打开目录", command=self._open_output, state="disabled")
        self.open_button.grid(row=0, column=1, padx=4)
        self.view_button = ttk.Button(output, text="查看图片", command=self._view_current, state="disabled")
        self.view_button.grid(row=0, column=2, padx=4)
        variables = [self.notch, self.sort_method, self.manual_order, self.minimum, self.maximum,
                     self.marker_size, self.x_range, self.grouping, self.highlight_enabled,
                     self.verify_hashes, self.test_expression, self.bin_text]
        variables += list(self.x_modes.values()) + list(self.modes.values()) + list(self.combine_modes.values()) + self.bin_modes
        for variable in variables:
            variable.trace_add("write", self._invalidate_preview)
        if self.kind != "bin":
            self.test_list.bind("<<ListboxSelect>>", self._invalidate_preview)
        if self.kind == "probability":
            self.highlight_list.bind("<<ListboxSelect>>", self._invalidate_preview)
        self.parent.after(100, self._poll)

    def _add(self, widget):
        widget.grid(row=self.row, column=0, sticky="ew", pady=4)
        self.row += 1
        return widget

    def _track(self, widget):
        self.setting_widgets.append(widget)
        return widget

    def _check(self, parent, label, variable, command=None):
        return self._track(ttk.Checkbutton(parent, text=label, variable=variable, command=command))

    def _entry_row(self, label, variable, width=25):
        frame = ttk.Frame(self.settings)
        ttk.Label(frame, text=label).pack(side="left", padx=(0, 6))
        self._track(ttk.Entry(frame, textvariable=variable, width=width)).pack(side="left", fill="x", expand=True)
        self._add(frame)

    def _choices(self, label, variable, choices):
        frame = ttk.LabelFrame(self.settings, text=label, padding=4)
        for index, (value, text) in enumerate(choices):
            self._track(ttk.Radiobutton(frame, text=text, variable=variable, value=value)).grid(
                row=index // 2, column=index % 2, sticky="w", padx=5, pady=2)
        self._add(frame)

    def _tests_ui(self):
        self._add(ttk.Label(self.settings, text="测试项（多选；先在左侧选片）"))
        frame = ttk.Frame(self.settings)
        self.test_list = self._track(tk.Listbox(frame, height=6, selectmode="extended", exportselection=False))
        self.test_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, command=self.test_list.yview)
        scroll.pack(side="right", fill="y")
        self.test_list.configure(yscrollcommand=scroll.set)
        self._add(frame)
        actions = ttk.Frame(self.settings)
        self._track(ttk.Button(actions, text="全选测试项", command=lambda: self._select_tests(True))).pack(side="left")
        self._track(ttk.Button(actions, text="清空测试项", command=lambda: self._select_tests(False))).pack(side="left", padx=4)
        self._add(actions)
        self._entry_row("手动列名/范围（填写则覆盖列表）：", self.test_expression)
        self._add(ttk.Label(self.settings, text="逗号分隔或起始列-结束列；完整列名优先识别，避免测试项名称内的横线被误拆。",
                            wraplength=470, foreground="#666666"))

    def _probability_ui(self):
        ranges = ttk.LabelFrame(self.settings, text="X 轴范围（可多选，每种范围分别出图）", padding=4)
        for mode, label in (("auto", "自动规格"), ("spec", "锁定规格"), ("manual", "手动范围")):
            self._check(ranges, label, self.x_modes[mode]).pack(side="left", padx=4)
        self._add(ranges)
        self._entry_row("手动范围 min,max：", self.x_range)
        self._entry_row("数据点大小（1–15）：", self.marker_size, 6)
        self._choices("曲线分组", self.grouping, [("wafer", "每片 Wafer 一条曲线（默认）"), ("lot", "按 Lot 合并 die 分布")])
        self._add(self._check(self.settings, "启用部分曲线高亮（不勾选则全部彩色）", self.highlight_enabled))
        self._add(ttk.Label(self.settings, text="高亮对象（按 Lot 分组时，该对象所在的 Lot/参考或分析组高亮）", wraplength=470))
        self.highlight_list = self._track(tk.Listbox(self.settings, height=4, selectmode="extended", exportselection=False))
        self._add(self.highlight_list)
        self._add(self._track(ttk.Button(self.settings, text="选中所有分析组作为高亮", command=self._highlight_analysis)))
        self._add(ttk.Label(self.settings, text="参考/分析组在左侧设置；每个测试项自动生成 2×2 拼图。规格不同会自动拆图。",
                            wraplength=470, foreground="#666666"))

    def _test_ui(self):
        frame = ttk.LabelFrame(self.settings, text="颜色模式（可多选）", padding=4)
        labels = {"auto": "自动 3σ", "manual": "手动", "usl_lsl": "USL/LSL", "pass_fail": "通过/失败"}
        for index, (mode, label) in enumerate(labels.items()):
            self._check(frame, label, self.modes[mode]).grid(row=index // 2, column=index % 2, sticky="w", padx=5)
        self._add(frame)
        self._entry_row("手动 Min：", self.minimum)
        self._entry_row("手动 Max：", self.maximum)
        combine = ttk.LabelFrame(self.settings, text="要拼接的模式（选两种以上；同时勾选上方对应模式）", padding=4)
        for index, (mode, label) in enumerate(labels.items()):
            self._check(combine, label, self.combine_modes[mode]).grid(row=index // 2, column=index % 2, sticky="w", padx=5)
        self._add(combine)

    def _bin_ui(self):
        for index, label in enumerate(["模式 0：所有 BIN 多彩", "模式 1：BIN1 绿、其他红", "模式 2：指定 BIN 高亮"]):
            self._add(self._check(self.settings, label, self.bin_modes[index], self._refresh_bin_combine))
        self._entry_row("指定 BIN（6,9 分图；6+9 同图）：", self.bin_text)
        self.bin_combine_frame = ttk.LabelFrame(self.settings, text="要拼接的 BIN 模式（选择两种以上）", padding=4)
        self._add(self.bin_combine_frame)
        self.bin_text.trace_add("write", lambda *_: self._refresh_bin_combine())
        self._refresh_bin_combine()

    def _refresh_bin_combine(self):
        for widget in self.bin_combine_frame.winfo_children():
            if widget in self.setting_widgets:
                self.setting_widgets.remove(widget)
            widget.destroy()
        self.bin_combine_vars = []
        try:
            tasks = parse_bin_tasks(*(var.get() for var in self.bin_modes), self.bin_text.get())
        except ValueError:
            ttk.Label(self.bin_combine_frame, text="先填写合法 BIN 编号").pack(anchor="w")
            return
        for mode, bins in tasks:
            var = tk.BooleanVar(value=False)
            label = "多彩" if mode == 0 else "BIN1 绿" if mode == 1 else f"高亮 {bins}"
            self._check(self.bin_combine_frame, label, var).pack(anchor="w")
            self.bin_combine_vars.append(var)
            var.trace_add("write", self._invalidate_preview)

    def _map_common_ui(self):
        self._choices("Notch 方位", self.notch, [(6, "默认"), (9, "顺 90°"), (12, "顺 180°"), (3, "顺 270°")])
        self._choices("Wafer 排序", self.sort_method, [("alphabetical", "完整身份字符顺序"), ("manual", "手动指定顺序")])
        self._entry_row("手动顺序（完整片名，逗号分隔）：", self.manual_order)

    def _selection_changed(self, refs):
        if not hasattr(self, "run_button"):
            return
        self.run_button.configure(state="normal" if refs and not self.busy else "disabled")
        self._invalidate_preview()
        folder = self.selector.products.get(self.selector.product_var.get())
        if self.task is not None and self.task.product != folder:
            self.task = None
            self.task_var.set("")
            self.last_result = None
            self.open_button.configure(state="disabled")
            self.view_button.configure(state="disabled")
            if self.preview_window is not None and self.preview_window.winfo_exists():
                self.preview_window.destroy()
        self._refresh_tasks(folder)
        if self.kind == "bin":
            return
        selected = {self.test_list.get(i) for i in self.test_list.curselection()}
        # Preserve file column order rather than alphabetic order so ranges retain
        # the established meaning. Append new revision columns deterministically.
        self.test_names = list(dict.fromkeys(name for ref in refs for name in ref.tests))
        self.test_list.delete(0, "end")
        for index, name in enumerate(self.test_names):
            self.test_list.insert("end", name)
            if name in selected:
                self.test_list.selection_set(index)
        if self.kind == "probability":
            highlighted = {self.highlight_refs[i].token for i in self.highlight_list.curselection()
                           if i < len(self.highlight_refs)}
            self.highlight_refs = refs
            self.highlight_list.delete(0, "end")
            for index, ref in enumerate(refs):
                role = "参考" if ref.token in self.selector.references else "分析"
                self.highlight_list.insert("end", f"{role} | {ref.label}")
                if ref.token in highlighted:
                    self.highlight_list.selection_set(index)

    def _highlight_analysis(self):
        self._invalidate_preview()
        self.highlight_list.selection_clear(0, "end")
        for index, ref in enumerate(self.highlight_refs):
            if ref.token not in self.selector.references:
                self.highlight_list.selection_set(index)

    def _select_tests(self, select):
        self._invalidate_preview()
        self.test_list.selection_clear(0, "end")
        if select:
            self.test_list.selection_set(0, "end")

    def _tests(self):
        raw = self.test_expression.get().strip().replace("，", ",")
        if raw:
            from test_map import parse_columns
            tests = parse_columns(raw, self.test_names)
        else:
            tests = [self.test_list.get(index) for index in self.test_list.curselection()]
        unknown = [test for test in tests if test not in self.test_names]
        if unknown:
            raise ValueError(f"所选片没有这些测试项：{', '.join(unknown)}")
        if not tests:
            raise ValueError("请至少选择一个测试项")
        return tests

    @staticmethod
    def _finite(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("范围必须是有限数值")
        return value

    def _options(self):
        options = PlotOptions(kind=self.kind, verify_hashes=self.verify_hashes.get())
        if self.kind != "bin":
            options.tests = self._tests()
        if self.kind == "probability":
            options.x_modes = [mode for mode, variable in self.x_modes.items() if variable.get()]
            if not options.x_modes:
                raise ValueError("请至少选择一种X轴范围")
            options.x_mode = options.x_modes[0]
            options.marker_size = int(self.marker_size.get())
            if not 1 <= options.marker_size <= 15:
                raise ValueError("数据点大小必须为 1–15")
            options.grouping = self.grouping.get()
            options.reference_tokens = [ref.token for ref in self.selector.selected_refs()
                                        if ref.token in self.selector.references]
            if "manual" in options.x_modes:
                pieces = self.x_range.get().replace("，", ",").split(",")
                if len(pieces) != 2:
                    raise ValueError("手动范围格式为 min,max")
                options.x_range = [self._finite(piece) for piece in pieces]
                if options.x_range[0] >= options.x_range[1]:
                    raise ValueError("手动最小值必须小于最大值")
            if self.highlight_enabled.get():
                options.highlight_tokens = [self.highlight_refs[index].token for index in self.highlight_list.curselection()]
                if not options.highlight_tokens:
                    raise ValueError("启用高亮后，请选择高亮对象")
        elif self.kind == "test":
            options.modes = [mode for mode, var in self.modes.items() if var.get()]
            if not options.modes:
                raise ValueError("请至少选择一个颜色模式")
            options.combine_modes = [mode for mode, var in self.combine_modes.items() if var.get()]
            if any(mode not in options.modes for mode in options.combine_modes):
                raise ValueError("拼接模式必须也在上方颜色模式中启用")
            if "manual" in options.modes:
                options.minimum, options.maximum = self._finite(self.minimum.get()), self._finite(self.maximum.get())
                if options.minimum >= options.maximum:
                    raise ValueError("手动 Min 必须小于 Max")
        else:
            options.bin_tasks = parse_bin_tasks(*(var.get() for var in self.bin_modes), self.bin_text.get())
            options.combine_bin_indices = [index for index, var in enumerate(self.bin_combine_vars) if var.get()]
        if self.kind != "probability":
            options.notch, options.sort_method = self.notch.get(), self.sort_method.get()
            options.manual_order = self.manual_order.get().replace("，", ",")
        return options

    def _task_ui(self):
        frame = ttk.LabelFrame(self.settings, text="分析任务（Lot组合与参数可在任务内反复调整）", padding=4)
        frame.columnconfigure(0, weight=1)
        self.task_combo = self._track(ttk.Combobox(frame, textvariable=self.task_var, state="readonly"))
        self.task_combo.grid(row=0, column=0, sticky="ew", columnspan=2)
        self.task_combo.bind("<<ComboboxSelected>>", self._choose_task)
        name = self._track(ttk.Entry(frame, textvariable=self.task_name))
        name.grid(row=1, column=0, sticky="ew", pady=4)
        self._track(ttk.Button(frame, text="新建任务", command=self._new_task)).grid(row=1, column=1, padx=4)
        self._track(ttk.Button(frame, text="历史版本 / 恢复参数", command=self._history)).grid(
            row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="名称用于新建任务；预览90 DPI，保存300 DPI并选择位置与文件夹名，均使用全部有效数据。",
                  wraplength=470, foreground="#666666").grid(row=3, column=0, columnspan=2, sticky="w")
        self._add(frame)

    def _refresh_tasks(self, folder=None):
        if folder is None:
            folder = self.selector.products.get(self.selector.product_var.get())
        try:
            tasks = task_list(folder, self.kind) if folder is not None else []
            self.tasks = {task.folder.name: task for task in tasks}
            self.task_combo["values"] = list(self.tasks)
            if self.task is not None and self.task.product == folder:
                self.task_var.set(self.task.folder.name)
            elif self.task is None:
                self.task_var.set("")
        except Exception as exc:
            self.log("读取任务列表失败：" + str(exc))

    def _new_task(self):
        if self.busy or self.selector.busy:
            return
        folder = self.selector.products.get(self.selector.product_var.get())
        if folder is None:
            return messagebox.showwarning("提示", "请先刷新索引并选择产品")
        try:
            self.task = create_task(folder, self.kind, self.task_name.get())
            self._invalidate_preview()
            self.last_result = None
            self.open_button.configure(state="disabled")
            self.view_button.configure(state="disabled")
            self._refresh_tasks(folder)
            self.output_var.set("当前任务：" + self.task.name + "；调整选片和参数后更新预览")
        except Exception as exc:
            messagebox.showerror("新建任务失败", str(exc))

    def _choose_task(self, _event=None):
        if self.busy:
            return
        task = self.tasks.get(self.task_var.get())
        if task is None:
            return
        try:
            data = read_json(task.folder / "task.json")
            if data.get("snapshot"):
                self._restore_snapshot(data["snapshot"])
            self.task = task
            self._invalidate_preview()
            self.last_result = None
            self.open_button.configure(state="disabled")
            self.view_button.configure(state="disabled")
            self.task_name.set(task.name)
            self.output_var.set("已恢复任务：" + task.name + "；请更新预览")
        except Exception as exc:
            self._refresh_tasks()
            messagebox.showerror("恢复任务失败", str(exc))

    def _restore_snapshot(self, payload):
        selected = {entry["key"]: entry for entry in payload["inputs"]}
        available = {ref.key: (iid, ref) for iid, ref in self.selector.rows.items() if ref.available}
        missing = set(selected) - set(available)
        options = PlotOptions(**payload["options"])
        if options.kind != self.kind or missing:
            raise ValueError("部分历史片当前不可用/类型不一致，请先处理数据；不会静默恢复部分选片")
        tests = {name for key in selected for name in available[key][1].tests}
        if any(name not in tests for name in options.tests):
            raise ValueError("历史测试项当前不存在，请先确认数据")
        token_map = {selected[key]["token"]: available[key][1].token for key in selected}
        self.selector.references = {token_map[token] for token in options.reference_tokens if token in token_map}
        self.selector.tree.selection_set([available[key][0] for key in selected])
        self.selector._changed()
        self.verify_hashes.set(options.verify_hashes)
        self.test_expression.set("")
        if self.kind != "bin":
            self.test_list.selection_clear(0, "end")
            for test in options.tests:
                self.test_list.selection_set(self.test_names.index(test))
        if self.kind == "probability":
            modes = options.x_modes if options.x_modes is not None else [options.x_mode]
            for mode, variable in self.x_modes.items():
                variable.set(mode in modes)
            self.marker_size.set(str(options.marker_size))
            self.grouping.set(options.grouping)
            self.x_range.set(",".join(str(value) for value in options.x_range or []))
            self.highlight_enabled.set(options.highlight_tokens is not None)
            self.highlight_list.selection_clear(0, "end")
            tokens = {token_map[token] for token in options.highlight_tokens or [] if token in token_map}
            for index, ref in enumerate(self.highlight_refs):
                if ref.token in tokens:
                    self.highlight_list.selection_set(index)
        elif self.kind == "test":
            for mode, variable in self.modes.items():
                variable.set(mode in options.modes)
                self.combine_modes[mode].set(mode in options.combine_modes)
            self.minimum.set("" if options.minimum is None else str(options.minimum))
            self.maximum.set("" if options.maximum is None else str(options.maximum))
        else:
            self.bin_modes[0].set(any(mode == 0 for mode, _ in options.bin_tasks))
            self.bin_modes[1].set(any(mode == 1 for mode, _ in options.bin_tasks))
            self.bin_modes[2].set(any(mode == 2 for mode, _ in options.bin_tasks))
            parts = ["+".join(str(value) for value in bins) if isinstance(bins, list) else str(bins)
                     for mode, bins in options.bin_tasks if mode == 2]
            self.bin_text.set(",".join(parts))
            self._refresh_bin_combine()
            for index in options.combine_bin_indices:
                if index < len(self.bin_combine_vars):
                    self.bin_combine_vars[index].set(True)
        if self.kind != "probability":
            self.notch.set(options.notch)
            self.sort_method.set(options.sort_method)
            self.manual_order.set(options.manual_order)
        for key, entry in selected.items():
            if entry["sha256"] != available[key][1].record["cleaned_sha256"]:
                self.log("历史数据已更新，将使用当前数据重新预览：" + entry["label"])
        for iid, ref in self.selector.rows.items():
            self.selector.tree.set(iid, "role", self.selector._role(ref))

    def _invalidate_preview(self, *_):
        self.preview_result = None
        if hasattr(self, "save_button"):
            self.save_button.configure(state="disabled")

    def _set_busy(self, value):
        self.busy = value
        self.selector.set_locked(value)
        self.run_button.configure(state="normal" if not value and self.selector.selected_refs() else "disabled")
        self.save_button.configure(state="normal" if not value and self.preview_result else "disabled")
        self.cache_button.configure(state="disabled" if value else "normal")
        for widget in self.setting_widgets:
            widget.configure(state="disabled" if value else ("readonly" if widget is self.task_combo else "normal"))

    def start(self):
        if self.busy or self.selector.busy:
            return
        refs = self.selector.selected_refs()
        if not refs:
            return messagebox.showwarning("提示", "请在左侧选择可用 Wafer")
        try:
            options = self._options()
            validate_options(options, refs)
            if self.task is None:
                self._new_task()
            if self.task is None:
                return
            task = self.task
        except (ValueError, tk.TclError) as exc:
            return messagebox.showerror("参数错误", str(exc))
        self._invalidate_preview()
        self.running_task = task
        self._set_busy(True)
        self.output_var.set("正在更新任务预览：" + task.name)
        def worker():
            try:
                result = run_preview(self.service, refs, options, task, self.log)
                self.events.put(("preview", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _save(self):
        if self.busy or self.preview_result is None:
            return
        preview = self.preview_result
        parent = filedialog.askdirectory(parent=self.parent, title="选择高清图片保存位置（将在这里创建文件夹）",
                                         initialdir=str(preview.task.product / "plots" / self.kind), mustexist=True)
        if not parent:
            return
        name = simpledialog.askstring("高清图片文件夹名称", "请输入便于记忆的文件夹名称：\n将创建在：" + parent,
            initialvalue=preview.task.name + "_" + datetime.now().strftime("%Y%m%d_%H%M%S"), parent=self.parent)
        if name is None:
            return
        try:
            destination = export_destination(parent, name)
        except ValueError as exc:
            return messagebox.showerror("保存位置错误", str(exc))
        if self.preview_result is not preview or self.busy:
            return messagebox.showwarning("预览已变化", "请先更新预览，再保存")
        self.running_task = preview.task
        self._set_busy(True)
        self.output_var.set("正在保存高清图片：" + str(destination))
        def worker():
            try:
                self.events.put(("saved", save_version(self.service, preview, self.log, destination=destination)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _history(self):
        if self.task is None:
            return messagebox.showwarning("提示", "请先选择分析任务")
        history_task = self.task
        try:
            records = versions(history_task)
        except Exception as exc:
            return messagebox.showerror("读取历史失败", str(exc))
        window = tk.Toplevel(self.parent)
        if self.parent.winfo_toplevel().state() == "withdrawn":
            window.withdraw()
        window.title(history_task.name + " — 历史版本")
        window.geometry("1050x420")
        columns = ("version", "time", "lots", "wafers", "tests", "mode", "images", "path")
        table = ttk.Treeview(window, columns=columns, show="headings")
        for column, label, width in zip(columns, ("文件夹 / 版本", "保存时间", "Lot", "片数", "测试项", "范围/模式", "图片数", "保存位置"),
                                        (180, 160, 130, 45, 120, 130, 55, 260)):
            table.heading(column, text=label)
            table.column(column, width=width)
        table.pack(fill="both", expand=True, padx=8, pady=8)
        entries = {}
        for folder, record in records:
            payload = record["snapshot"]
            options = payload["options"]
            iid = table.insert("", "end", values=(folder.name, record["created_at"],
                ", ".join(dict.fromkeys(entry["lot"] for entry in payload["inputs"])),
                len(payload["inputs"]), ", ".join(options["tests"]) or "BIN",
                ", ".join(options.get("x_modes") or [options["x_mode"]]) if self.kind == "probability"
                else str(options["bin_tasks"]) if self.kind == "bin" else ", ".join(options["modes"]),
                record["image_count"], str(folder)))
            entries[iid] = (folder, record)
        actions = ttk.Frame(window)
        actions.pack(fill="x", padx=8, pady=8)
        def entry():
            selected = table.selection()
            return entries.get(selected[0]) if selected else None
        def view():
            selected = entry()
            if selected:
                self._show_images(selected[0], "历史 " + selected[0].name, history_task)
        def restore():
            selected = entry()
            if not selected or self.busy:
                return
            try:
                if self.task != history_task:
                    raise ValueError("当前产品或任务已切换，请回到原任务后恢复历史参数")
                self._restore_snapshot(selected[1]["snapshot"])
                self._invalidate_preview()
                self.output_var.set("已恢复 " + selected[0].name + " 参数；更新预览后可另存新版本")
                window.destroy()
            except Exception as exc:
                messagebox.showerror("恢复失败", str(exc))
        ttk.Button(actions, text="查看图片", command=view).pack(side="left")
        ttk.Button(actions, text="恢复选片与参数（不覆盖历史）", command=restore).pack(side="left", padx=8)
        table.bind("<Double-1>", lambda _: view())

    def _view_current(self):
        if self.last_result is not None:
            self._show_images(self.last_result.output_folder, "当前结果")

    def _show_images(self, folder, title, task=None):
        task = task if task is not None else self.task
        try:
            if task is None:
                raise ValueError("请先选择分析任务")
            folder = checked(Path(folder), Path(folder).parent)
            files = [checked(path, folder) for path in sorted(folder.rglob("*.png"))]
        except Exception as exc:
            return messagebox.showerror("读取图片失败", str(exc))
        if not files:
            return messagebox.showwarning("提示", "没有找到图片")
        if self.preview_window is None or not self.preview_window.winfo_exists():
            self.preview_window = tk.Toplevel(self.parent)
            if self.parent.winfo_toplevel().state() == "withdrawn":
                self.preview_window.withdraw()
            self.preview_window.geometry("1100x780")
            panes = ttk.Panedwindow(self.preview_window, orient="horizontal")
            panes.pack(fill="both", expand=True)
            self.image_list = tk.Listbox(panes, width=34, exportselection=False)
            panes.add(self.image_list, weight=0)
            self.image_label = ttk.Label(panes, anchor="center")
            panes.add(self.image_label, weight=1)
            self.image_list.bind("<<ListboxSelect>>", self._display_image)
        self.preview_window.title(task.name + " — " + title + "（缩略预览）")
        self.image_product = folder
        self.image_files = files
        self.image_list.delete(0, "end")
        for path in files:
            self.image_list.insert("end", path.relative_to(folder).as_posix())
        self.image_list.selection_set(0)
        self.preview_window.update_idletasks()
        self._display_image()

    def _display_image(self, _event=None):
        selected = self.image_list.curselection()
        if not selected:
            return
        try:
            path = checked(self.image_files[selected[0]], self.image_product)
            with Image.open(path) as source:
                image = source.copy()
            image.thumbnail((max(250, self.image_label.winfo_width() - 10),
                             max(250, self.image_label.winfo_height() - 10)))
            photo = ImageTk.PhotoImage(image, master=self.preview_window)
            self.image_label.configure(image=photo)
            self.image_label.image = photo
        except Exception as exc:
            self.log("读取图片失败：" + str(exc))

    def log(self, message):
        self.events.put(("log", message))

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", value + "\n")
                    self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                else:
                    if kind in ("preview", "saved"):
                        if self.task != self.running_task:
                            self.log("任务已完成，但当前产品/任务已切换，未加载旧结果：" + str(value.output_folder))
                            self._set_busy(False)
                            continue
                        if kind == "preview":
                            self.preview_result = value
                        self.last_result = value
                        self.output_var.set(str(value.output_folder))
                        self.open_button.configure(state="normal")
                        self.view_button.configure(state="normal")
                        self.log(f"明细缓存（本次启动累计）：读取 CSV {self.service.csv_reads} 次，复用内存数据 {self.service.cache_hits} 次，"
                                 f"占用 {self.service.cache_bytes / 1024 / 1024:.1f} MiB")
                        if kind == "preview":
                            self._show_images(value.output_folder, "当前草稿 / 90 DPI")
                            self.log("预览完成；继续调整会替换草稿，尚未归档")
                        else:
                            messagebox.showinfo("保存完成", f"高清版本 {value.output_folder.name}：{value.image_count} 张图片\n{value.output_folder}")
                    else:
                        self._invalidate_preview()
                        self.log(value)
                        messagebox.showerror("绘图失败", value)
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.parent.after(100, self._poll)

    def _clear_cache(self):
        self.service.clear_cache()
        PROBABILITY_CURVES.clear()
        self.log("明细与概率计算缓存已清空；没有删除磁盘数据或图片")

    def _open_output(self):
        if self.last_result and self.last_result.output_folder.is_dir():
            if os.name == "nt":
                os.startfile(str(self.last_result.output_folder))
            else:
                messagebox.showinfo("输出目录", str(self.last_result.output_folder))
