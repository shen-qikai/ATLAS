"""Shared two-column plotting UI's left-hand Product/Lot/Wafer selector."""

import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog


STATUS_NAMES = {"current": "可用", "stale": "待更新", "unprocessed": "未处理",
                "missing": "缺失", "failed": "失败", "invalid": "输入冲突"}


class WaferSelector(ttk.LabelFrame):
    def __init__(self, parent, service, on_change=None, log=None, root_var=None, roles=False):
        super().__init__(parent, text="第一列：选择已处理数据", padding=8)
        self.service, self.on_change, self.log = service, on_change, log or (lambda _: None)
        self.root_var = root_var if root_var is not None else tk.StringVar()
        self.category_var = tk.StringVar(value="全部分类")
        self.product_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="选择根目录后点击刷新")
        self.catalog = None
        self.references = set()
        self.rows = {}
        self.products = {}
        self.events = queue.Queue()
        self.busy = False
        self.locked = False
        self.generation = 0
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        ttk.Label(self, text="LDO / DCDC / Load_switch 的父目录").grid(row=0, column=0, sticky="w")
        self.root_entry = ttk.Entry(self, textvariable=self.root_var)
        self.root_entry.grid(row=1, column=0, sticky="ew", pady=4)
        actions = ttk.Frame(self)
        actions.grid(row=2, column=0, sticky="ew")
        self.browse_button = ttk.Button(actions, text="浏览", command=self.browse)
        self.browse_button.pack(side="left")
        self.refresh_button = ttk.Button(actions, text="刷新索引", command=self.refresh)
        self.refresh_button.pack(side="left", padx=4)
        filters = ttk.Frame(self)
        filters.grid(row=3, column=0, sticky="ew", pady=6)
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text="分类").grid(row=0, column=0, padx=(0, 5))
        self.category_combo = ttk.Combobox(filters, textvariable=self.category_var, state="readonly")
        self.category_combo.grid(row=0, column=1, sticky="ew")
        self.category_combo.bind("<<ComboboxSelected>>", lambda _: self._filter_products())
        ttk.Label(filters, text="产品").grid(row=1, column=0, padx=(0, 5), pady=4)
        self.product_combo = ttk.Combobox(filters, textvariable=self.product_var, state="readonly")
        self.product_combo.grid(row=1, column=1, sticky="ew", pady=4)
        self.product_combo.bind("<<ComboboxSelected>>", lambda _: self._fill_tree())
        select = ttk.Frame(self)
        select.grid(row=4, column=0, sticky="ew", pady=(0, 4))
        self.all_button = ttk.Button(select, text="全选有效片", command=self.select_all)
        self.all_button.pack(side="left")
        self.clear_button = ttk.Button(select, text="清空", command=self.clear_selection)
        self.clear_button.pack(side="left", padx=4)
        area = ttk.Frame(self)
        area.grid(row=5, column=0, sticky="nsew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        columns = ("die", "yield", "status", "role")
        self.tree = ttk.Treeview(area, columns=columns, selectmode="extended", height=12)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.heading("#0", text="Lot / Wafer")
        self.tree.column("#0", width=120, minwidth=90)
        for col, title, width in [("die", "die数", 65), ("yield", "良率", 65),
                                  ("status", "状态", 65), ("role", "分组", 45)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=width, stretch=False)
        if not roles:
            self.tree["displaycolumns"] = ("die", "yield", "status")
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.tag_configure("unavailable", foreground="#888888")
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._changed())
        row = 6
        self.role_buttons = []
        if roles:
            role_frame = ttk.Frame(self)
            role_frame.grid(row=row, column=0, sticky="ew", pady=4)
            for text, is_reference in [("设为参考组", True), ("设为分析组", False)]:
                button = ttk.Button(role_frame, text=text, command=lambda value=is_reference: self.set_role(value))
                button.pack(side="left", padx=(0, 4))
                self.role_buttons.append(button)
            row += 1
        ttk.Label(self, textvariable=self.summary_var, wraplength=330).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Label(self, text="Ctrl / Shift 多选；选 Lot 可包含其有效片。\n灰色片不可绘图，请先执行自动增量良率。",
                  wraplength=330, foreground="#666666").grid(row=row + 1, column=0, sticky="w")
        self.root_var.trace_add("write", self._root_changed)
        self.after(100, self._poll)

    def _root_changed(self, *_):
        self.generation += 1
        self.catalog = None
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        self.product_var.set("")
        self.summary_var.set("目录已变化，请刷新索引")
        self._changed()

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.root_var.set(folder)
            self.refresh()

    def refresh(self):
        if self.busy or self.locked:
            return
        root, generation = self.root_var.get(), self.generation
        self.busy = True
        self.set_locked(self.locked)
        self.summary_var.set("正在读取索引和检查文件状态……")
        def worker():
            try:
                self.events.put((generation, self.service.catalog(root), None))
            except Exception as exc:
                self.events.put((generation, None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                generation, catalog, error = self.events.get_nowait()
                self.busy = False
                self.set_locked(self.locked)
                if generation != self.generation:
                    continue
                self.catalog = catalog
                if error:
                    self.summary_var.set(error)
                    self.log(error)
                    continue
                for message in catalog.errors:
                    self.log(message)
                categories = sorted({ref.category for ref in catalog.wafers})
                self.category_combo["values"] = ["全部分类"] + categories
                if self.category_var.get() not in self.category_combo["values"]:
                    self.category_var.set("全部分类")
                self._filter_products()
                self.log(f"索引已刷新：{len(catalog.wafers)} 片，{len(catalog.errors)} 条目录/配置提示")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _filter_products(self):
        self.products = {}
        if self.catalog:
            for ref in self.catalog.wafers:
                if self.category_var.get() in ("全部分类", ref.category):
                    self.products[f"{ref.category} / {ref.folder.name}"] = ref.folder
        self.product_combo["values"] = sorted(self.products)
        if self.product_var.get() not in self.products:
            self.product_var.set(next(iter(sorted(self.products)), ""))
        self._fill_tree()

    def _fill_tree(self):
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        folder = self.products.get(self.product_var.get())
        lots = {}
        if self.catalog:
            for index, ref in enumerate(self.catalog.wafers):
                if ref.folder != folder:
                    continue
                lot = ref.record["lot"]
                if lot not in lots:
                    lots[lot] = self.tree.insert("", "end", text=lot, open=True)
                metrics = ref.record.get("metrics", {})
                count = metrics.get("record_count", 0)
                rate = f"{metrics.get('good_count', 0) / count:.2%}" if ref.available and count else "—"
                iid = f"wafer{index}"
                self.tree.insert(lots[lot], "end", iid=iid, text=f"W{int(ref.record['wafer']):02d}",
                                 values=(metrics.get("effective_die_count", "—"), rate,
                                         STATUS_NAMES.get(ref.status, ref.status), self._role(ref)),
                                 tags=() if ref.available else ("unavailable",))
                self.rows[iid] = ref
        self._changed()

    def _role(self, ref):
        return "参考" if ref.token in self.references else "分析"

    def selected_refs(self):
        selected = set(self.tree.selection())
        refs = []
        for iid, ref in self.rows.items():
            if iid in selected or self.tree.parent(iid) in selected:
                if ref.available:
                    refs.append(ref)
        return refs

    def _changed(self):
        refs = self.selected_refs()
        selected = set(self.tree.selection())
        unavailable = [ref for iid, ref in self.rows.items() if not ref.available
                       and (iid in selected or self.tree.parent(iid) in selected)]
        message = f"已选 {len({ref.record['lot'] for ref in refs})} 个 Lot / {len(refs)} 片有效 Wafer"
        if unavailable:
            message += f"；{len(unavailable)} 片不可用，未纳入。{unavailable[0].error}"
        if not self.rows and self.catalog:
            message += ("；" + self.catalog.errors[0] if self.catalog.errors
                        else "；未找到处理记录，请先运行自动增量良率")
        elif self.catalog is None:
            message = "目录未加载，请选择根目录并刷新索引"
        self.summary_var.set(message)
        if self.on_change:
            self.on_change(refs)

    def select_all(self):
        self.tree.selection_set([iid for iid, ref in self.rows.items() if ref.available])
        self._changed()

    def clear_selection(self):
        self.tree.selection_remove(self.tree.selection())
        self._changed()

    def set_role(self, reference):
        for ref in self.selected_refs():
            if reference:
                self.references.add(ref.token)
            else:
                self.references.discard(ref.token)
        for iid, ref in self.rows.items():
            self.tree.set(iid, "role", self._role(ref))
        self._changed()

    def set_locked(self, value):
        self.locked = value
        disabled = value or self.busy
        for widget in [self.root_entry, self.browse_button, self.refresh_button,
                       self.all_button, self.clear_button] + self.role_buttons:
            widget.configure(state="disabled" if disabled else "normal")
        for widget in (self.category_combo, self.product_combo):
            widget.configure(state="disabled" if disabled else "readonly")
        self.tree.configure(selectmode="none" if disabled else "extended")
