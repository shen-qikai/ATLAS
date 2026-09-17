"""Tkinter UI for ATLAS config-driven FT data preprocessing."""

from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from preprocessing_core import (
    ExecutionError,
    PreprocessingService,
    ProductProfileStore,
    ProfileConfigError,
    extract_archive,
)


CONFIG_PATH = Path(__file__).resolve().parent / "config" / "products.yaml"


class DataPreprocessingGUI:
    def __init__(self, parent):
        self.parent = parent
        self.folder_path_var = tk.StringVar()
        self.product_name_var = tk.StringVar()
        self.delete_zip_var = tk.BooleanVar(value=False)
        self.scan_summary_var = tk.StringVar(value="请选择数据文件夹。")
        self.product_status_var = tk.StringVar(value="请选择配置库中的产品。")
        self.preview_summary_var = tk.StringVar(value="Preview 尚未执行。")
        self.rule_summary_var = tk.StringVar(value="Naming Rule: -")

        self.profile_store = None
        self.service = None
        self.current_preview = None
        self.config_error = ""
        try:
            self.profile_store = ProductProfileStore.from_yaml(CONFIG_PATH)
            self.service = PreprocessingService(self.profile_store)
        except (OSError, ProfileConfigError) as exc:
            self.config_error = str(exc)

        self.create_widgets()
        self.product_name_var.trace_add("write", self._on_product_text_changed)
        self.folder_path_var.trace_add("write", self._on_folder_changed)
        self._update_action_states()

        if self.config_error:
            self.parent.after(
                0,
                lambda: messagebox.showerror(
                    "Product Profile 配置错误",
                    f"无法加载 {CONFIG_PATH}:\n\n{self.config_error}",
                ),
            )

    def create_widgets(self):
        top_frame = ttk.LabelFrame(self.parent, text="入口设置", padding="10")
        top_frame.pack(fill=tk.X, padx=10, pady=5)
        top_frame.columnconfigure(1, weight=1)

        ttk.Label(top_frame, text="数据文件夹:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(top_frame, textvariable=self.folder_path_var, width=55).grid(
            row=0, column=1, padx=5, sticky=tk.EW
        )
        ttk.Button(top_frame, text="选择文件夹", command=self.browse_folder).grid(
            row=0, column=2
        )

        ttk.Label(top_frame, text="产品:").grid(row=0, column=3, padx=(20, 5))
        product_names = self.profile_store.product_names if self.profile_store else ()
        self.combo_product = ttk.Combobox(
            top_frame,
            textvariable=self.product_name_var,
            values=product_names,
            width=18,
            state="normal" if product_names else "disabled",
        )
        self.combo_product.grid(row=0, column=4, sticky=tk.EW)
        self.combo_product.bind("<KeyRelease>", self._filter_products)
        self.combo_product.bind("<<ComboboxSelected>>", self._on_product_selected)

        ttk.Label(top_frame, textvariable=self.product_status_var).grid(
            row=1, column=3, columnspan=2, padx=(20, 0), sticky=tk.W
        )
        ttk.Label(top_frame, textvariable=self.scan_summary_var).grid(
            row=1, column=0, columnspan=3, sticky=tk.W, pady=(5, 0)
        )
        ttk.Label(top_frame, text="自动增量良率可直接读取厂商原名；这里的改名是可选操作，会修改原始文件名。").grid(
            row=2, column=0, columnspan=5, sticky=tk.W, pady=(5, 0)
        )

        main_container = ttk.Panedwindow(self.parent, orient=tk.HORIZONTAL)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left_frame = ttk.LabelFrame(
            main_container, text="功能 1：ZIP / GZ 批量解压", padding="10"
        )
        right_frame = ttk.LabelFrame(
            main_container, text="功能 2：配置驱动的文件名规范化", padding="10"
        )
        main_container.add(left_frame, weight=1)
        main_container.add(right_frame, weight=3)

        self.zip_listbox = tk.Listbox(left_frame, height=15, selectmode=tk.EXTENDED)
        self.zip_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        ttk.Checkbutton(
            left_frame,
            text="解压后删除原压缩文件",
            variable=self.delete_zip_var,
        ).pack(anchor=tk.W)
        self.btn_unzip = ttk.Button(
            left_frame, text="开始解压全部文件", command=self.start_unzip
        )
        self.btn_unzip.pack(fill=tk.X, pady=5)

        ttk.Label(right_frame, textvariable=self.rule_summary_var).pack(
            anchor=tk.W, pady=(0, 5)
        )

        columns = ("status", "original", "product", "lot", "wafer", "target", "reason")
        self.preview_table = ttk.Treeview(
            right_frame, columns=columns, show="headings", height=15
        )
        headings = {
            "status": "Status",
            "original": "Original File",
            "product": "Product",
            "lot": "Lot",
            "wafer": "Wafer",
            "target": "New Filename",
            "reason": "Reason",
        }
        widths = {
            "status": 85,
            "original": 220,
            "product": 85,
            "lot": 90,
            "wafer": 60,
            "target": 280,
            "reason": 170,
        }
        for column in columns:
            self.preview_table.heading(column, text=headings[column])
            self.preview_table.column(column, width=widths[column], minwidth=55)

        vertical_scroll = ttk.Scrollbar(
            right_frame, orient=tk.VERTICAL, command=self.preview_table.yview
        )
        horizontal_scroll = ttk.Scrollbar(
            right_frame, orient=tk.HORIZONTAL, command=self.preview_table.xview
        )
        self.preview_table.configure(
            yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set
        )
        self.preview_table.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        vertical_scroll.place(relx=1.0, rely=0.05, relheight=0.72, anchor=tk.NE)
        horizontal_scroll.pack(fill=tk.X)

        ttk.Label(right_frame, textvariable=self.preview_summary_var).pack(
            anchor=tk.W, pady=5
        )
        btn_grid = ttk.Frame(right_frame)
        btn_grid.pack(fill=tk.X)
        self.btn_preview = ttk.Button(
            btn_grid,
            text="生成 Preview",
            command=self.generate_preview,
            state=tk.DISABLED,
        )
        self.btn_preview.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.btn_rename = ttk.Button(
            btn_grid,
            text="Execute（执行改名）",
            command=self.execute_rename,
            state=tk.DISABLED,
        )
        self.btn_rename.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_path_var.set(folder)
            self.refresh_scan()

    def _on_folder_changed(self, *_args):
        self._invalidate_preview("数据文件夹已变化，请重新 Preview。")
        self._update_action_states()

    def _on_product_text_changed(self, *_args):
        self._update_product_status()
        self._invalidate_preview("Product 已变化，请重新 Preview。")
        self._update_action_states()

    def _filter_products(self, _event=None):
        if not self.profile_store:
            return
        query = self.product_name_var.get().strip().casefold()
        filtered = [
            name
            for name in self.profile_store.product_names
            if name.casefold().startswith(query)
        ]
        self.combo_product.configure(values=filtered)

    def _on_product_selected(self, _event=None):
        self._update_product_status()
        self._update_action_states()

    def _update_product_status(self):
        if not self.profile_store:
            self.product_status_var.set("Product Profile 配置不可用。")
            self.rule_summary_var.set("Naming Rule: -")
            return
        resolved = self.profile_store.resolve_name(self.product_name_var.get())
        if not resolved:
            typed = self.product_name_var.get().strip()
            if typed:
                self.product_status_var.set(f"⚠ 未找到产品 {typed}")
            else:
                self.product_status_var.set("请选择配置库中的产品。")
            self.rule_summary_var.set("Naming Rule: -")
            return
        profile = self.profile_store.get(resolved)
        self.product_status_var.set(f"✓ 已选择 Product Profile: {resolved}")
        self.rule_summary_var.set(
            f"Naming Rule: {resolved} / v{profile.naming_version} / {profile.naming_regex}"
        )

    def refresh_scan(self):
        self.zip_listbox.delete(0, tk.END)
        if not self.service:
            return
        folder = self.folder_path_var.get().strip()
        try:
            scan = self.service.scan_folder(folder)
        except (OSError, FileNotFoundError):
            self.scan_summary_var.set("请选择有效的数据文件夹。")
            return

        for archive in scan.raw_archives:
            self.zip_listbox.insert(tk.END, archive)
        self.scan_summary_var.set(
            "扫描结果："
            f"Raw Archive {len(scan.raw_archives)} | "
            f"待规范 CSV {len(scan.raw_csv)} | "
            f"已规范 CSV {len(scan.normalized_csv)} | "
            f"Unknown {len(scan.unknown_files)}"
        )

    def _invalidate_preview(self, message="Preview 尚未执行。"):
        self.current_preview = None
        if hasattr(self, "btn_rename"):
            self.btn_rename.config(state=tk.DISABLED)
        if hasattr(self, "preview_summary_var"):
            self.preview_summary_var.set(message)

    def _update_action_states(self):
        if not hasattr(self, "btn_preview"):
            return
        product_valid = bool(
            self.profile_store
            and self.profile_store.resolve_name(self.product_name_var.get())
        )
        folder_text = self.folder_path_var.get().strip()
        folder_valid = bool(folder_text and Path(folder_text).is_dir())
        preview_enabled = bool(self.service and product_valid and folder_valid)
        self.btn_preview.config(state=tk.NORMAL if preview_enabled else tk.DISABLED)
        execute_enabled = bool(
            preview_enabled
            and self.current_preview
            and self.current_preview.validation_passed
        )
        self.btn_rename.config(state=tk.NORMAL if execute_enabled else tk.DISABLED)

    def _clear_preview_table(self):
        for row in self.preview_table.get_children():
            self.preview_table.delete(row)

    def start_unzip(self):
        folder = self.folder_path_var.get().strip()
        archives = [self.zip_listbox.get(i) for i in range(self.zip_listbox.size())]
        if not archives:
            messagebox.showinfo("提示", "列表为空，没有可解压的文件。")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning("提示", "请先选择有效的数据文件夹。")
            return

        delete_original = bool(self.delete_zip_var.get())
        self.btn_unzip.config(state=tk.DISABLED)
        threading.Thread(
            target=self.run_unzip,
            args=(folder, archives, delete_original),
            daemon=True,
        ).start()

    def run_unzip(self, folder, archives, delete_original):
        archive_count = 0
        file_count = 0
        errors = []
        for archive_name in archives:
            try:
                file_count += extract_archive(
                    Path(folder) / archive_name,
                    folder,
                    delete_original=delete_original,
                )
                archive_count += 1
            except (OSError, ExecutionError) as exc:
                errors.append(f"{archive_name}: {exc}")
        self.parent.after(
            0,
            lambda: self.finish_unzip(archive_count, file_count, errors),
        )

    def finish_unzip(self, archive_count, file_count, errors):
        self.btn_unzip.config(state=tk.NORMAL)
        self.refresh_scan()
        self._invalidate_preview("解压后文件列表已变化，请生成 Preview。")
        message = f"成功处理 {archive_count} 个压缩包，解出 {file_count} 个文件。"
        if errors:
            messagebox.showwarning(
                "解压完成（含失败项）", message + "\n\n" + "\n".join(errors)
            )
        else:
            messagebox.showinfo("解压完成", message)

    def generate_preview(self):
        self._clear_preview_table()
        self._invalidate_preview()
        if not self.service or not self.profile_store:
            messagebox.showerror("错误", self.config_error or "Product Profile 配置不可用。")
            return

        folder = self.folder_path_var.get().strip()
        resolved_product = self.profile_store.resolve_name(self.product_name_var.get())
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning("提示", "请先选择有效的数据文件夹。")
            return
        if not resolved_product:
            typed = self.product_name_var.get().strip() or "(空)"
            messagebox.showwarning(
                "Product 无效",
                f"未找到产品 {typed}。\n请选择已有 Product，或先新增 Product Profile。",
            )
            return

        try:
            preview = self.service.build_preview(folder, resolved_product)
        except (OSError, ProfileConfigError) as exc:
            messagebox.showerror("Preview 失败", str(exc))
            return

        self.current_preview = preview
        for item in preview.items:
            status_symbol = (
                "✓ MATCHED" if item.status == "MATCHED" else f"✗ {item.status}"
            )
            self.preview_table.insert(
                "",
                tk.END,
                values=(
                    status_symbol,
                    item.original_filename,
                    item.product,
                    item.lot or "-",
                    item.wafer or "-",
                    item.standard_filename or "-",
                    item.error or "-",
                ),
            )

        validation = "PASS" if preview.validation_passed else "FAILED"
        self.preview_summary_var.set(
            f"Total Files: {preview.total_files} | "
            f"Matched: {preview.matched_files} | "
            f"Failed: {preview.failed_files} | "
            f"Validation: {validation}"
        )
        self.btn_rename.config(
            state=tk.NORMAL if preview.validation_passed else tk.DISABLED
        )
        self._update_action_states()
        self.refresh_scan()

    def execute_rename(self):
        if not self.current_preview or not self.current_preview.validation_passed:
            messagebox.showwarning("禁止执行", "必须先完成并通过 Preview Validation。")
            self.btn_rename.config(state=tk.DISABLED)
            return

        try:
            result = self.service.execute(self.current_preview)
        except (ExecutionError, OSError) as exc:
            self._invalidate_preview("执行失败；文件状态可能已变化，请重新 Preview。")
            messagebox.showerror("执行失败", str(exc))
            return

        message = f"重命名执行完毕，成功修改 {result.renamed_files} 个文件。"
        if result.log_path:
            message += f"\n日志：{result.log_path}"
        if result.log_warning:
            message += f"\n\n警告：{result.log_warning}"
        messagebox.showinfo("完成", message)
        self._clear_preview_table()
        self._invalidate_preview("执行完成。请重新扫描或选择下一批数据。")
        self.refresh_scan()


def create_ui(parent):
    return DataPreprocessingGUI(parent)
