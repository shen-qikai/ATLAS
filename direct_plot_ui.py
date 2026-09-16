"""Direct-data plotting pages: shared selector at left, legacy options at right."""

import math
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

from atlas_pipeline.plot_data import SHARED_PLOT_DATA
from atlas_pipeline.plot_jobs import PlotOptions, parse_bin_tasks, run_plot_job, validate_options
from plot_selection import WaferSelector


class DirectPlotApp:
    def __init__(self, parent, kind, service=None, root_var=None):
        self.parent, self.kind = parent, kind
        self.service = service if service is not None else SHARED_PLOT_DATA
        self.events = queue.Queue()
        self.busy = False
        self.setting_widgets = []
        self.test_names = []
        self.highlight_refs = []
        self.last_result = None
        self.notch = tk.IntVar(value=6)
        self.sort_method = tk.StringVar(value="alphabetical")
        self.manual_order = tk.StringVar()
        self.minimum = tk.StringVar()
        self.maximum = tk.StringVar()
        self.x_mode = tk.StringVar(value="auto")
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
        self.output_var = tk.StringVar(value="图片保存到 产品/plots/类型/本次运行目录")
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
        self.run_button = ttk.Button(buttons, text="生成选中数据的图片", command=self.start, state="disabled")
        self.run_button.pack(side="left", fill="x", expand=True)
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
        self._track(ttk.Button(actions, text="全选测试项", command=lambda: self.test_list.selection_set(0, "end"))).pack(side="left")
        self._track(ttk.Button(actions, text="清空测试项", command=lambda: self.test_list.selection_clear(0, "end"))).pack(side="left", padx=4)
        self._add(actions)
        self._entry_row("手动列名/范围（填写则覆盖列表）：", self.test_expression)
        self._add(ttk.Label(self.settings, text="逗号分隔或起始列-结束列；完整列名优先识别，避免测试项名称内的横线被误拆。",
                            wraplength=470, foreground="#666666"))

    def _probability_ui(self):
        self._choices("X 轴范围", self.x_mode, [("auto", "自动范围"), ("spec", "规格锁定"), ("manual", "手动范围")])
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

    def _map_common_ui(self):
        self._choices("Notch 方位", self.notch, [(6, "默认"), (9, "顺 90°"), (12, "顺 180°"), (3, "顺 270°")])
        self._choices("Wafer 排序", self.sort_method, [("alphabetical", "完整身份字符顺序"), ("manual", "手动指定顺序")])
        self._entry_row("手动顺序（完整片名，逗号分隔）：", self.manual_order)

    def _selection_changed(self, refs):
        if not hasattr(self, "run_button"):
            return
        self.run_button.configure(state="normal" if refs and not self.busy else "disabled")
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
        self.highlight_list.selection_clear(0, "end")
        for index, ref in enumerate(self.highlight_refs):
            if ref.token not in self.selector.references:
                self.highlight_list.selection_set(index)

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
            options.x_mode = self.x_mode.get()
            options.marker_size = int(self.marker_size.get())
            if not 1 <= options.marker_size <= 15:
                raise ValueError("数据点大小必须为 1–15")
            options.grouping = self.grouping.get()
            options.reference_tokens = list(self.selector.references)
            if options.x_mode == "manual":
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

    def start(self):
        if self.busy or self.selector.busy:
            return
        refs = self.selector.selected_refs()
        if not refs:
            return messagebox.showwarning("提示", "请在左侧选择可用 Wafer")
        try:
            options = self._options()
            validate_options(options, refs)
        except (ValueError, tk.TclError) as exc:
            return messagebox.showerror("参数错误", str(exc))
        self.busy = True
        self.selector.set_locked(True)
        self.run_button.configure(state="disabled")
        self.cache_button.configure(state="disabled")
        for widget in self.setting_widgets:
            widget.configure(state="disabled")
        # Everything Tk-related is captured above, on the UI thread.
        def worker():
            try:
                result = run_plot_job(self.service, refs, options, self.log)
                self.events.put(("done", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

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
                    self.busy = False
                    self.selector.set_locked(False)
                    self.cache_button.configure(state="normal")
                    for widget in self.setting_widgets:
                        widget.configure(state="normal")
                    self.run_button.configure(state="normal" if self.selector.selected_refs() else "disabled")
                    if kind == "done":
                        self.last_result = value
                        self.output_var.set(str(value.output_folder))
                        self.open_button.configure(state="normal")
                        self.log(f"缓存：读取 CSV {self.service.csv_reads} 次，命中 {self.service.cache_hits} 次，"
                                 f"占用 {self.service.cache_bytes / 1024 / 1024:.1f} MiB")
                        messagebox.showinfo("完成", f"生成 {value.image_count} 张图片\n{value.output_folder}")
                    else:
                        self.log(value)
                        messagebox.showerror("绘图失败", value)
        except queue.Empty:
            pass
        self.parent.after(100, self._poll)

    def _clear_cache(self):
        self.service.clear_cache()
        self.log("三个绘图页面共享的明细缓存已清空；没有删除磁盘数据或图片")

    def _open_output(self):
        if self.last_result and self.last_result.output_folder.is_dir():
            if os.name == "nt":
                os.startfile(str(self.last_result.output_folder))
            else:
                messagebox.showinfo("输出目录", str(self.last_result.output_folder))
