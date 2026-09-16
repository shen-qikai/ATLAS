"""Tkinter entry point for the local incremental Wafer pipeline."""

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from atlas_pipeline.pipeline import run_pipeline, scan_root


CONFIG_PATH = Path(__file__).resolve().parent / "config" / "products.yaml"
MODES = ("增量处理", "强制重算全部", "仅从缓存重建报表")


class AutomaticYieldGUI:
    def __init__(self, parent):
        self.parent = parent
        self.root_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=MODES[0])
        self.verify_var = tk.BooleanVar(value=False)
        self.summary_var = tk.StringVar(value="选择根目录：LDO / DCDC / Load_switch → Product → Lot → CSV")
        self.events = queue.Queue()
        self.busy = False
        self.plan = None
        self._build()
        self.root_var.trace_add("write", self._invalidate)
        self.mode_var.trace_add("write", self._invalidate)
        self.verify_var.trace_add("write", self._invalidate)

    def _build(self):
        settings = ttk.LabelFrame(self.parent, text="自动增量良率", padding=10)
        settings.pack(fill=tk.X, padx=10, pady=5)
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="数据根目录:").grid(row=0, column=0, sticky=tk.W)
        self.root_entry = ttk.Entry(settings, textvariable=self.root_var)
        self.root_entry.grid(row=0, column=1, sticky=tk.EW, padx=5)
        self.browse_button = ttk.Button(settings, text="选择根目录", command=self._browse)
        self.browse_button.grid(row=0, column=2)
        ttk.Label(settings, text="运行方式:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.mode_combo = ttk.Combobox(settings, textvariable=self.mode_var, values=MODES, state="readonly", width=24)
        self.mode_combo.grid(row=1, column=1, sticky=tk.W, padx=5)
        self.verify_checkbox = ttk.Checkbutton(settings, text="完整校验文件内容（重新读取历史文件计算指纹）",
                                               variable=self.verify_var)
        self.verify_checkbox.grid(row=2, column=0, columnspan=3, sticky=tk.W)
        ttk.Label(settings, text="默认 BIN1=Good；只输出清理CSV；运行前关闭产品报表的Excel窗口。").grid(
            row=3, column=0, columnspan=3, sticky=tk.W, pady=5
        )
        buttons = ttk.Frame(self.parent)
        buttons.pack(fill=tk.X, padx=10)
        self.preview_button = ttk.Button(buttons, text="扫描 / Preview", command=lambda: self._start(False))
        self.preview_button.pack(side=tk.LEFT, padx=2)
        self.run_button = ttk.Button(buttons, text="执行（复核后处理）", command=lambda: self._start(True), state=tk.DISABLED)
        self.run_button.pack(side=tk.LEFT, padx=2)
        ttk.Label(self.parent, textvariable=self.summary_var, wraplength=950).pack(fill=tk.X, padx=10, pady=5)
        table_frame = ttk.Frame(self.parent)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        columns = ("category", "product", "lot", "wafer", "files", "action", "reason")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=12)
        for name, title, width in zip(columns, ("Category", "Product", "Lot", "Wafer", "Files", "Action", "说明"),
                                      (90, 100, 130, 60, 50, 145, 280)):
            self.table.heading(name, text=title)
            self.table.column(name, width=width, minwidth=45)
        self.table.grid(row=0, column=0, sticky=tk.NSEW)
        vertical = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        vertical.grid(row=0, column=1, sticky=tk.NS)
        horizontal = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky=tk.EW)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(self.parent, height=9, state=tk.DISABLED, font=("Consolas", 9))
        self.log.pack(fill=tk.X, padx=10, pady=5)

    def _browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.root_var.set(folder)

    def _invalidate(self, *_args):
        self.plan = None
        self.run_button.configure(state=tk.DISABLED)
        if not self.busy:
            self.summary_var.set("设置已变化，请重新扫描。")

    def _append(self, message):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _start(self, execute):
        if self.busy:
            return
        if execute and self.plan is None:
            return
        root = self.root_var.get().strip()
        if not root:
            messagebox.showwarning("提示", "请先选择数据根目录。")
            return
        mode = self.mode_var.get()
        force, cache_only, verify = mode == MODES[1], mode == MODES[2], bool(self.verify_var.get())
        self.busy = True
        self.plan = None
        self.run_button.configure(state=tk.DISABLED)
        self.preview_button.configure(state=tk.DISABLED)
        self.browse_button.configure(state=tk.DISABLED)
        self.mode_combo.configure(state=tk.DISABLED)
        self.root_entry.configure(state=tk.DISABLED)
        self.verify_checkbox.configure(state=tk.DISABLED)
        self.summary_var.set("处理中…" if execute else "扫描中…")
        def worker():
            try:
                if execute:
                    result = run_pipeline(root, CONFIG_PATH, force, verify, cache_only,
                                          progress=lambda message: self.events.put(("log", message)))
                    self.events.put(("result", result))
                else:
                    self.events.put(("plan", scan_root(root, CONFIG_PATH, force, verify, cache_only)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        self.parent.after(100, self._poll)

    def _poll(self):
        completed = False
        while not self.events.empty():
            event, value = self.events.get()
            if event == "log":
                self._append(value)
            elif event == "plan":
                self.plan = value
                self._show_plan(value)
                completed = True
            elif event == "result":
                message = (f"完成：处理 {value.processed} | 跳过 {value.skipped} | "
                           f"错误 {value.failed} | 不可用 {value.unavailable} | 更新报表 {len(value.reports)}")
                self.summary_var.set(message)
                self._append(f"日志：{value.log_path}")
                if value.failed or value.unavailable:
                    messagebox.showwarning("完成（需检查异常）", message + "\n\n日志：" + value.log_path)
                else:
                    messagebox.showinfo("完成", message + "\n\n日志：" + value.log_path)
                completed = True
            elif event == "error":
                self._append(value)
                self.summary_var.set("操作失败，请查看错误。")
                messagebox.showerror("操作失败", value)
                completed = True
        if completed:
            self.busy = False
            self.preview_button.configure(state=tk.NORMAL)
            self.browse_button.configure(state=tk.NORMAL)
            self.mode_combo.configure(state="readonly")
            self.root_entry.configure(state=tk.NORMAL)
            self.verify_checkbox.configure(state=tk.NORMAL)
            self.run_button.configure(state=tk.NORMAL if self.plan else tk.DISABLED)
        elif self.busy:
            self.parent.after(100, self._poll)

    def _show_plan(self, plan):
        for item in self.table.get_children():
            self.table.delete(item)
        pending = skipped = errors = 0
        for product in plan.products:
            for task in product.tasks:
                self.table.insert("", tk.END, values=(product.category, product.folder.name, task.lot,
                                                     "W" + task.wafer.zfill(2), len(task.files), task.action, task.error))
                if task.action == "SKIP":
                    skipped += 1
                elif task.action != "INVALID":
                    pending += 1
                else:
                    errors += 1
            for record in product.unavailable:
                self.table.insert("", tk.END, values=(product.category, product.folder.name, record["lot"],
                                                     record["wafer"], "-", record["status"].upper(), record["error"]))
            for error in product.errors:
                errors += 1
                self.table.insert("", tk.END, values=(product.category, product.folder.name, "-", "-", "-", "ERROR", error))
            if not product.tasks and not product.errors:
                self.table.insert("", tk.END, values=(product.category, product.folder.name, "-", "-", "-",
                                                     "REPORT" if product.reports_needed else "SKIP", "产品指标缓存"))
        self.summary_var.set(f"产品 {len(plan.products)} | 待处理Wafer {pending} | 跳过 {skipped} | 校验错误 {errors}。执行时再次复核；错误Lot不生成部分结果。")


def create_ui(parent):
    return AutomaticYieldGUI(parent)
