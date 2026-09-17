"""Tkinter entry point for the local incremental Wafer pipeline."""

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from atlas_pipeline.pipeline import run_pipeline, scan_root
from atlas_pipeline.archives import prepare_archives, preview_archives, set_csv_ignored
from atlas_pipeline.input_changes import approve_replacement, describe_change, preview_replacements
from atlas_pipeline.cleaning import retest_run


CONFIG_PATH = Path(__file__).resolve().parent / "config" / "products.yaml"
MODES = ("增量处理", "强制重算全部", "仅从缓存重建报表")


class AutomaticYieldGUI:
    def __init__(self, parent):
        self.parent = parent
        self.root_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=MODES[0])
        self.verify_var = tk.BooleanVar(value=False)
        self.summary_var = tk.StringVar(value="选择根目录后：①预览压缩文件 → 确认解压整理；②扫描身份 → 执行清洗与汇总。")
        self.events = queue.Queue()
        self.busy = False
        self.plan = None
        self.archive_plan = None
        self.csv_rows = {}
        self.replacement_rows = {}
        self.replacement_window = None
        self._build()
        self.root_var.trace_add("write", self._invalidate)
        self.mode_var.trace_add("write", self._invalidate)
        self.verify_var.trace_add("write", self._invalidate)

    def _build(self):
        settings = ttk.LabelFrame(self.parent, text="数据清洗与良率汇总", padding=10)
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
        ttk.Label(settings, text="递归查找Lot子目录中的ZIP/GZ，全部CSV平铺到Lot根目录；非CSV移出Lot备份，可恢复。\n下一步用YAML确认身份，不按命名过滤提取；运行前关闭Excel报表。").grid(
            row=3, column=0, columnspan=3, sticky=tk.W, pady=5
        )
        archives = ttk.LabelFrame(self.parent, text="① CSV准备（独立步骤，不计算良率）", padding=5)
        archives.pack(fill=tk.X, padx=10, pady=3)
        self.archive_preview_button = ttk.Button(archives, text="预览压缩文件 / 待整理文件",
                                                command=lambda: self._start(False, archive_preview=True))
        self.archive_preview_button.pack(side=tk.LEFT, padx=2)
        self.prepare_button = ttk.Button(archives, text="确认解压并整理 CSV", command=self._confirm_prepare,
                                         state=tk.DISABLED)
        self.prepare_button.pack(side=tk.LEFT, padx=2)
        buttons = ttk.LabelFrame(self.parent, text="② 数据清洗与良率汇总（先检查身份，再执行）", padding=5)
        buttons.pack(fill=tk.X, padx=10, pady=3)
        self.preview_button = ttk.Button(buttons, text="扫描 CSV / 确认身份", command=lambda: self._start(False))
        self.preview_button.pack(side=tk.LEFT, padx=2)
        self.run_button = ttk.Button(buttons, text="执行（复核后处理）", command=lambda: self._start(True), state=tk.DISABLED)
        self.run_button.pack(side=tk.LEFT, padx=2)
        self.ignore_button = ttk.Button(buttons, text="忽略 / 恢复所选CSV", command=self._toggle_ignore)
        self.ignore_button.pack(side=tk.LEFT, padx=2)
        self.replacement_button = ttk.Button(buttons, text="预览 / 确认替换所选片输入",
                                             command=self._preview_replacement, state=tk.DISABLED)
        self.replacement_button.pack(side=tk.LEFT, padx=6)
        ttk.Label(self.parent, textvariable=self.summary_var, wraplength=950).pack(fill=tk.X, padx=10, pady=5)
        self.body = ttk.Panedwindow(self.parent, orient=tk.VERTICAL)
        self.body.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.tables = ttk.Notebook(self.body)
        self.body.add(self.tables, weight=1)
        archive_frame = ttk.Frame(self.tables)
        table_frame = ttk.Frame(self.tables)
        self.tables.add(archive_frame, text="压缩文件 / 整理预览")
        self.tables.add(table_frame, text="身份校验 / 良率处理预览")
        archive_columns = ("product", "lot", "kind", "path", "csvs", "action")
        self.archive_table = ttk.Treeview(archive_frame, columns=archive_columns, show="headings", height=10)
        for name, title, width in zip(archive_columns, ("分类/产品", "Lot", "类型", "文件路径（含嵌套目录）", "CSV数", "操作"),
                                      (160, 100, 85, 430, 55, 200)):
            self.archive_table.heading(name, text=title)
            self.archive_table.column(name, width=width, minwidth=45)
        self.archive_table.grid(row=0, column=0, sticky=tk.NSEW)
        archive_vertical = ttk.Scrollbar(archive_frame, orient=tk.VERTICAL, command=self.archive_table.yview)
        archive_vertical.grid(row=0, column=1, sticky=tk.NS)
        archive_horizontal = ttk.Scrollbar(archive_frame, orient=tk.HORIZONTAL, command=self.archive_table.xview)
        archive_horizontal.grid(row=1, column=0, sticky=tk.EW)
        self.archive_table.configure(yscrollcommand=archive_vertical.set, xscrollcommand=archive_horizontal.set)
        archive_frame.rowconfigure(0, weight=1)
        archive_frame.columnconfigure(0, weight=1)
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
        self.table.bind("<<TreeviewSelect>>", lambda _: self._replacement_state())
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        log_frame = ttk.LabelFrame(self.body, text="Log（可拖动上方分隔线调整高度）")
        self.body.add(log_frame, weight=2)
        self.log = scrolledtext.ScrolledText(log_frame, height=23, state=tk.DISABLED, font=("Consolas", 9))
        self.log.pack(fill=tk.BOTH, expand=True)

    def _browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.root_var.set(folder)

    def _invalidate(self, *_args):
        self.plan = None
        self.archive_plan = None
        self.prepare_button.configure(state=tk.DISABLED)
        self.run_button.configure(state=tk.DISABLED)
        self.replacement_rows.clear()
        self.replacement_button.configure(state=tk.DISABLED)
        if self.replacement_window is not None and self.replacement_window.winfo_exists():
            self.replacement_window.destroy()
        if not self.busy:
            self.summary_var.set("设置已变化，请重新预览 / 扫描。")

    def _confirm_prepare(self):
        if self.busy or self.archive_plan is None:
            return
        count = sum(saved["action"] != "保留" for item in self.archive_plan.lots for saved in item.files)
        if messagebox.askyesno("确认解压及整理",
                              f"以当前预览为准：CSV直接放到Lot根目录。\n"
                              f"待移出Lot并备份的原文件：{count} 个（包括压缩包及其他格式）。\n"
                              "备份位于产品/.atlas/ingest_backups，非永久删除；已生成的清洗结果不动。\n"
                              "同名不同内容会拒绝覆盖。确认执行？", default="no"):
            self._start(False, prepare=True)

    def _append(self, message):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _toggle_ignore(self):
        entries = [self.csv_rows[iid] for iid in self.table.selection() if iid in self.csv_rows]
        if not entries:
            messagebox.showwarning("提示", "请先扫描，再选中待确认CSV或已忽略CSV行。")
            return
        names = "\n".join(("恢复 " if item[2] else "忽略 ") + item[1] for item in entries)
        if messagebox.askyesno("人工确认（不删除文件）", names + "\n\n忽略表示确认不参与分析；可能漏算真实测试数据。确定执行？", default="no"):
            self._start(False, ignore_entries=entries)

    def _replacement_state(self):
        enabled = not self.busy and self.plan is not None and any(
            iid in self.replacement_rows for iid in self.table.selection())
        self.replacement_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _preview_replacement(self):
        if self.busy or self.plan is None:
            return
        selected = self.table.selection()
        if not selected or any(iid not in self.replacement_rows for iid in selected):
            return messagebox.showwarning("提示", "请只选择已登记且仍有可识别CSV的Wafer行；身份/压缩包错误不能绕过。")
        self._start(False, replacement_targets=[self.replacement_rows[iid] for iid in selected])

    def _show_replacements(self, reviews):
        if self.replacement_window is not None and self.replacement_window.winfo_exists():
            self.replacement_window.destroy()
        window = self.replacement_window = tk.Toplevel(self.parent)
        if self.parent.winfo_toplevel().state() == "withdrawn":
            window.withdraw()
        window.title("人工确认输入替换（当前预览未修改任何数据）")
        window.geometry("1180x650")
        preview_root = Path(self.root_var.get()).resolve()
        ttk.Label(window, text="确认后仅重算这几片；旧清洗结果和登记信息备份到产品/.atlas/input_history。\n"
                  "原始CSV不改名、不移动；此操作无法找回工程师已覆盖/删除的原始文件。", padding=8).pack(fill=tk.X)
        area = ttk.Frame(window)
        area.pack(fill=tk.BOTH, expand=True, padx=8)
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        columns = ("wafer", "version", "path", "run", "size", "sha", "change")
        table = ttk.Treeview(area, columns=columns, show="headings")
        for column, label, width in zip(columns, ("产品/Lot/Wafer", "输入版本", "相对路径", "测试轮次", "字节数", "SHA256", "变化"),
                                        (200, 65, 340, 90, 85, 230, 100)):
            table.heading(column, text=label)
            table.column(column, width=width)
        table.grid(row=0, column=0, sticky=tk.NSEW)
        vertical = ttk.Scrollbar(area, command=table.yview)
        vertical.grid(row=0, column=1, sticky=tk.NS)
        horizontal = ttk.Scrollbar(area, orient=tk.HORIZONTAL, command=table.xview)
        horizontal.grid(row=1, column=0, sticky=tk.EW)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        summary = []
        for review in reviews:
            label = f"{review.product.name}/{review.lot}/W{review.wafer.zfill(2)}"
            change = describe_change(review.previous, review.files)
            for version, sources in (("历史", review.previous["source_files"]),
                                     ("当前", [vars(source) for source in review.files])):
                for source in sources:
                    path = source["relative_path"]
                    state = "缺失/退出" if version == "历史" and path in change["removed"] else (
                        "新增" if version == "当前" and path in change["added"] else (
                        "内容变化" if path in change["changed"] else "保留"))
                    run = retest_run(Path(path).name)
                    table.insert("", tk.END, values=(label, version, path,
                        "RT" + str(run) if run else "初测/分段", source["size"], source["sha256"], state))
            summary.append(f"{label}：历史{len(review.previous['source_files'])}份 → 当前{len(review.files)}份")
        summary_text = f"共{len(reviews)}片，详细输入清单见上表。\n" + "\n".join(summary[:4])
        if len(summary) > 4:
            summary_text += f"\n其余{len(summary) - 4}片请在上表核对。"
        ttk.Label(window, text=summary_text, padding=8).pack(fill=tk.X)
        ttk.Label(window, text="当前文件数量不等于数据完整性证明。请核对初测、RT和分段文件；确认后缺失的历史文件将退出当前输入集合。",
                  foreground="#a32222", wraplength=1100, padding=8).pack(fill=tk.X)
        reason = tk.StringVar(master=window, value="测厂数据更正")
        reason_frame = ttk.Frame(window)
        reason_frame.pack(fill=tk.X, padx=8, pady=5)
        ttk.Label(reason_frame, text="替换原因：").pack(side=tk.LEFT)
        ttk.Entry(reason_frame, textvariable=reason).pack(side=tk.LEFT, fill=tk.X, expand=True)
        acknowledged = tk.BooleanVar(master=window, value=False)
        ttk.Checkbutton(window, text="我确认这是每片完整且有意替换的输入集合（包括初测/RT/分段文件）",
                        variable=acknowledged).pack(anchor=tk.W, padx=8, pady=5)
        actions = ttk.Frame(window)
        actions.pack(fill=tk.X, padx=8, pady=8)
        def confirm():
            if self.busy or not acknowledged.get():
                return
            if Path(self.root_var.get()).resolve() != preview_root:
                return messagebox.showwarning("预览已过期", "根目录已变化，请重新扫描和预览")
            try:
                approvals = [approve_replacement(review, reason.get()) for review in reviews]
            except ValueError as exc:
                return messagebox.showwarning("提示", str(exc))
            if messagebox.askyesno("确认替换并重算", summary_text +
                    "\n\n只处理以上片，旧版本保留，报表替换对应行。确认执行？", default="no"):
                window.destroy()
                self._start(True, replacement_approvals=approvals)
        button = ttk.Button(actions, text="确认替换并仅重算这些片", command=confirm, state=tk.DISABLED)
        button.pack(side=tk.LEFT)
        acknowledged.trace_add("write", lambda *_: button.configure(state=tk.NORMAL if acknowledged.get() else tk.DISABLED))
        ttk.Button(actions, text="取消（不修改数据）", command=window.destroy).pack(side=tk.LEFT, padx=8)
        if window.state() != "withdrawn":
            window.grab_set()

    def _start(self, execute, prepare=False, ignore_entries=None, archive_preview=False,
               replacement_targets=None, replacement_approvals=None):
        if self.busy:
            return
        if execute and self.plan is None and replacement_approvals is None:
            return
        if prepare and self.archive_plan is None:
            messagebox.showwarning("提示", "请先预览压缩文件。")
            return
        approved_archive_plan = self.archive_plan
        strict_targets = {product.folder: {task.key for task in product.tasks if task.action != "SKIP"}
                          for product in self.plan.products} if execute and self.plan else {}
        root = self.root_var.get().strip()
        if not root:
            messagebox.showwarning("提示", "请先选择数据根目录。")
            return
        mode = self.mode_var.get()
        if prepare or ignore_entries or replacement_approvals is not None:
            self.mode_var.set(MODES[0])
            mode = MODES[0]
        force, cache_only, verify = mode == MODES[1], mode == MODES[2], bool(self.verify_var.get())
        self.busy = True
        self.plan = None
        self.run_button.configure(state=tk.DISABLED)
        self.preview_button.configure(state=tk.DISABLED)
        self.prepare_button.configure(state=tk.DISABLED)
        self.archive_preview_button.configure(state=tk.DISABLED)
        self.ignore_button.configure(state=tk.DISABLED)
        self.replacement_button.configure(state=tk.DISABLED)
        self.browse_button.configure(state=tk.DISABLED)
        self.mode_combo.configure(state=tk.DISABLED)
        self.root_entry.configure(state=tk.DISABLED)
        self.verify_checkbox.configure(state=tk.DISABLED)
        self.summary_var.set("替换预览中（完整校验所选片，只读）…" if replacement_targets else (
            "压缩文件预览中（只读）…" if archive_preview else (
            "解压及整理CSV中…" if prepare else ("处理中…" if execute else "扫描中…"))))
        def worker():
            try:
                if replacement_targets:
                    self.events.put(("replacement_review", preview_replacements(root, CONFIG_PATH, replacement_targets)))
                    return
                if archive_preview:
                    self.events.put(("archive_plan", preview_archives(root)))
                    return
                if prepare:
                    prepared = prepare_archives(root, verify,
                                                progress=lambda message: self.events.put(("log", message)),
                                                approved_plan=approved_archive_plan)
                    self.events.put(("prepared", prepared))
                for product, relative, currently_ignored in ignore_entries or []:
                    set_csv_ignored(product, relative, not currently_ignored)
                    self.events.put(("log", f"人工确认：{'恢复' if currently_ignored else '忽略'} {relative}（文件保留）"))
                if execute:
                    result = run_pipeline(root, CONFIG_PATH, force, verify, cache_only,
                                          progress=lambda message: self.events.put(("log", message)),
                                          replacement_approvals=replacement_approvals, strict_targets=strict_targets)
                    if replacement_approvals is not None:
                        try:
                            for approval in replacement_approvals:
                                strict_targets.setdefault(Path(approval.product), set()).add(approval.key)
                            self.events.put(("refreshed_plan", scan_root(root, CONFIG_PATH, strict_targets=strict_targets)))
                        except Exception as exc:
                            self.events.put(("log", "处理已结束，重新扫描失败：" + str(exc)))
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
            elif event == "prepared":
                self.archive_plan = None
                self._append(f"解压整理：压缩包 {value.prepared} | 新写入CSV {value.csv_count} | 备份文件 {value.backup_count} | 失败Lot {value.failed}")
                self._append(f"解压日志：{value.log_path}")
                if value.failed:
                    messagebox.showwarning("部分Lot整理失败", "\n".join(value.errors) + "\n\n日志：" + value.log_path)
            elif event == "archive_plan":
                self.archive_plan = value
                self._show_archives(value)
                completed = True
            elif event in ("plan", "refreshed_plan"):
                self.plan = value
                self._show_plan(value)
                if event == "plan":
                    completed = True
            elif event == "replacement_review":
                self.summary_var.set("只读替换预览完成，请人工确认完整输入集合；尚未修改数据。")
                self._show_replacements(value)
                completed = True
            elif event == "result":
                message = (f"完成：处理 {value.processed} | 跳过 {value.skipped} | "
                           f"错误 {value.failed} | 不可用 {value.unavailable} | 更新报表 {len(value.reports)}")
                self.summary_var.set(message)
                self._append(message)
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
            self.prepare_button.configure(state=tk.NORMAL if self.archive_plan else tk.DISABLED)
            self.archive_preview_button.configure(state=tk.NORMAL)
            self.ignore_button.configure(state=tk.NORMAL)
            self.browse_button.configure(state=tk.NORMAL)
            self.mode_combo.configure(state="readonly")
            self.root_entry.configure(state=tk.NORMAL)
            self.verify_checkbox.configure(state=tk.NORMAL)
            self.run_button.configure(state=tk.NORMAL if self.plan else tk.DISABLED)
            self._replacement_state()
        elif self.busy:
            self.parent.after(100, self._poll)

    def _show_plan(self, plan):
        self.tables.select(1)
        self.csv_rows.clear()
        self.replacement_rows.clear()
        for item in self.table.get_children():
            self.table.delete(item)
        pending = skipped = errors = 0
        for product in plan.products:
            for item in product.pending_files:
                iid = self.table.insert("", tk.END, values=(product.category, product.folder.name, item["lot"], "-", "CSV",
                                        "IGNORED" if item["ignored"] else "PENDING_CSV",
                                        item["relative_path"] + " — " + item["reason"]))
                self.csv_rows[iid] = (product.folder, item["relative_path"], item["ignored"])
            for task in product.tasks:
                reason = task.error or ("已验证内容与测试轮次一一对应，执行时自动迁移并重算该片" if task.input_change.get("renames") else "")
                iid = self.table.insert("", tk.END, values=(product.category, product.folder.name, task.lot,
                                                     "W" + task.wafer.zfill(2), len(task.files), task.action, reason))
                previous = product.snapshot["wafers"].get(task.key, {})
                if previous.get("source_files") and task.files and not task.input_change.get("hard_error"):
                    self.replacement_rows[iid] = (product.folder, task.key)
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

    def _show_archives(self, plan):
        for iid in self.archive_table.get_children():
            self.archive_table.delete(iid)
        archives = removed = errors = 0
        for item in plan.lots:
            for saved in item.files:
                archives += saved["kind"] == "压缩包"
                removed += saved["action"] != "保留"
                self.archive_table.insert("", tk.END, values=(
                    item.category + "/" + item.product.name, item.lot.name, saved["kind"],
                    saved["relative_path"], len(saved["csv_names"]) if saved["kind"] == "压缩包" else "-",
                    saved["action"]))
                if saved["kind"] == "压缩包":
                    self._append("发现压缩包：" + saved["relative_path"])
            for error in item.errors:
                errors += 1
                self.archive_table.insert("", tk.END, values=(
                    item.category + "/" + item.product.name, item.lot.name, "ERROR", error, "-", "保留原文件"))
        self.tables.select(0)
        self.summary_var.set(f"只读预览：Lot {len(plan.lots)} | 压缩包 {archives} | 待备份原文件 {removed} | 错误Lot {errors}。请确认后解压；此时未修改任何文件。")


def create_ui(parent):
    return AutomaticYieldGUI(parent)
