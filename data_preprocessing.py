
data_preprocessing.py
data_preprocessing.py
# 文件名: data_preprocessing.py
import os
import re
import zipfile
import gzip       # <-- 新增：用于处理 .gz 文件
import shutil     # <-- 新增：用于复制文件流
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

class DataPreprocessingGUI:
    def __init__(self, parent):
        self.parent = parent
        
        # --- 变量初始化 ---
        self.folder_path_var = tk.StringVar()
        self.product_name_var = tk.StringVar(value="MyProduct")
        self.delete_zip_var = tk.BooleanVar(value=False)
        self.template_var = tk.StringVar()
        self.custom_regex_var = tk.StringVar()
        
        # 预设正则模板 
        self.templates = {
            "测试厂1 (标准带横杠 例: DPJ579-06-B4...)": r"_(?P<lot>[A-Za-z0-9]+)-(?P<wafer>\d+)-.*\.csv$",
            "测试厂1 (编号与字母连写 例: DPJ579-06B4...)": r"_(?P<lot>[A-Za-z0-9]+)-(?P<wafer>\d+)[A-Za-z].*\.csv$",
            "测试厂2 (例: Lot123_W05_...)": r"(?P<lot>[A-Za-z0-9]+)_W(?P<wafer>\d+)_.*\.csv$",
            "测试厂3 (JCAP 例: ...H0KM33-25-E1_P1_日期)": r"_(?P<lot>[A-Za-z0-9]+)-(?P<wafer>\d+)-[A-Za-z0-9]+_P\d+_\d{4}-\d{1,2}-\d{1,2}_[\d_]+\.csv$",
            "自定义正则表达式": "CUSTOM"
        }
        
        self.rename_tasks = []
        self.create_widgets()

    def create_widgets(self):
        # --- 顶部：公共设置区 ---
        top_frame = ttk.LabelFrame(self.parent, text="通用设置", padding="10")
        top_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(top_frame, text="数据文件夹:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(top_frame, textvariable=self.folder_path_var, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(top_frame, text="选择文件夹", command=self.browse_folder).grid(row=0, column=2)
        
        ttk.Label(top_frame, text="产品名称:").grid(row=0, column=3, padx=(20, 5))
        ttk.Entry(top_frame, textvariable=self.product_name_var, width=15).grid(row=0, column=4)

        # --- 中部：左右双功能区 ---
        main_container = ttk.Frame(self.parent)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # ==========================================
        # 1. 左侧：解压缩功能 (支持 ZIP 和 GZ)
        # ==========================================
        left_frame = ttk.LabelFrame(main_container, text="功能 1：ZIP / GZ 批量解压", padding="10")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        self.zip_listbox = tk.Listbox(left_frame, height=15, selectmode=tk.EXTENDED)
        self.zip_listbox.pack(fill=tk.BOTH, expand=True, pady=5)

        ttk.Checkbutton(left_frame, text="解压后自动删除原压缩文件", variable=self.delete_zip_var).pack(anchor=tk.W)
        self.btn_unzip = ttk.Button(left_frame, text="开始解压全部文件", command=self.start_unzip)
        self.btn_unzip.pack(fill=tk.X, pady=5)

        # ==========================================
        # 2. 右侧：重命名功能
        # ==========================================
        right_frame = ttk.LabelFrame(main_container, text="功能 2：文件名规范化 (保留原名防冲突)", padding="10")
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))

        ttk.Label(right_frame, text="匹配模板:").pack(anchor=tk.W)
        
        template_container = ttk.Frame(right_frame)
        template_container.pack(fill=tk.X, pady=2)
        
        self.combo_template = ttk.Combobox(template_container, textvariable=self.template_var, 
                                           values=list(self.templates.keys()), state="readonly")
        self.combo_template.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.combo_template.current(0)
        self.combo_template.bind("<<ComboboxSelected>>", self.on_template_change)
        
        self.entry_custom = ttk.Entry(right_frame, textvariable=self.custom_regex_var)

        self.preview_text = scrolledtext.ScrolledText(right_frame, height=12, state='disabled', font=("Consolas", 9))
        self.preview_text.pack(fill=tk.BOTH, expand=True, pady=5)

        btn_grid = ttk.Frame(right_frame)
        btn_grid.pack(fill=tk.X)
        ttk.Button(btn_grid, text="生成预览", command=self.generate_preview).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.btn_rename = ttk.Button(btn_grid, text="执行改名", command=self.execute_rename, state=tk.DISABLED)
        self.btn_rename.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_path_var.set(folder)
            self.refresh_zip_list()

    def refresh_zip_list(self):
        """扫描文件夹并显示 .zip 和 .gz 文件"""
        self.zip_listbox.delete(0, tk.END)
        folder = self.folder_path_var.get()
        if os.path.exists(folder):
            # 同时匹配 .zip 和 .gz 结尾的文件
            zips = [f for f in os.listdir(folder) if f.lower().endswith(('.zip', '.gz'))]
            for z in zips:
                self.zip_listbox.insert(tk.END, z)

    def on_template_change(self, event):
        selected = self.template_var.get()
        if self.templates[selected] == "CUSTOM":
            self.entry_custom.pack(fill=tk.X, pady=(0, 5), before=self.preview_text)
        else:
            self.entry_custom.pack_forget()

    # --- 解压逻辑 (支持 ZIP 和 GZ) ---
    def start_unzip(self):
        folder = self.folder_path_var.get()
        zips = [self.zip_listbox.get(i) for i in range(self.zip_listbox.size())]
        if not zips:
            messagebox.showinfo("提示", "列表为空，没有可解压的文件。")
            return
        
        self.btn_unzip.config(state=tk.DISABLED)
        threading.Thread(target=self.run_unzip, args=(folder, zips), daemon=True).start()

    def run_unzip(self, folder, zips):
        count = 0
        delete_zip = self.delete_zip_var.get()
        for z_name in zips:
            z_path = os.path.join(folder, z_name)
            try:
                # 处理 .zip 文件
                if z_name.lower().endswith('.zip'):
                    with zipfile.ZipFile(z_path, 'r') as zip_ref:
                        zip_ref.extractall(folder)
                
                # 处理 .gz 文件
                elif z_name.lower().endswith('.gz'):
                    # 输出文件名为去掉最后的 .gz 扩展名
                    out_name = z_name[:-3]
                    out_path = os.path.join(folder, out_name)
                    
                    with gzip.open(z_path, 'rb') as f_in:
                        with open(out_path, 'wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
                
                # 如果勾选了自动删除原文件
                if delete_zip:
                    os.remove(z_path)
                count += 1
            except Exception as e:
                print(f"解压 {z_name} 失败: {e}")
        
        self.parent.after(0, lambda: self.finish_unzip(count))

    def finish_unzip(self, count):
        messagebox.showinfo("完成", f"解压处理完毕，共成功处理 {count} 个压缩文件。")
        self.btn_unzip.config(state=tk.NORMAL)
        self.refresh_zip_list()

    # --- 改名预览逻辑 ---
    def generate_preview(self):
        self.preview_text.config(state='normal')
        self.preview_text.delete(1.0, tk.END)
        self.rename_tasks = []
        
        folder = self.folder_path_var.get()
        product = self.product_name_var.get().strip()
        if not os.path.exists(folder):
            messagebox.showwarning("提示", "请先选择有效的文件夹！")
            return
        if not product:
            messagebox.showwarning("提示", "产品名称不能为空！")
            return

        selected_temp = self.template_var.get()
        if self.templates[selected_temp] == "CUSTOM":
            pattern_str = self.custom_regex_var.get().strip()
            if not pattern_str:
                messagebox.showwarning("错误", "自定义正则不能为空！")
                return
        else:
            pattern_str = self.templates[selected_temp]

        try:
            regex = re.compile(pattern_str, re.IGNORECASE)
        except Exception as e:
            messagebox.showerror("正则错误", f"正则表达式语法有误:\n{e}")
            return

        files = [f for f in os.listdir(folder) if f.lower().endswith('.csv')]
        match_count = 0
        
        for old_name in files:
            match = regex.search(old_name)
            if match:
                try:
                    lot = match.group('lot')
                    wafer = match.group('wafer')
                except IndexError:
                    self.preview_text.insert(tk.END, f"正则错误: 必须包含 (?P<lot>...) 和 (?P<wafer>...) 捕获组。\n\n")
                    return
                
                prefix = f"{product}_{lot}_{wafer}#"
                
                if old_name.startswith(prefix):
                    self.preview_text.insert(tk.END, f"{old_name}\n   -> [已是标准格式，跳过]\n\n")
                    continue
                
                new_name = f"{prefix}_{old_name}"
                
                self.rename_tasks.append((old_name, new_name))
                self.preview_text.insert(tk.END, f"{old_name}\n   -> {new_name}\n\n")
                match_count += 1
            else:
                self.preview_text.insert(tk.END, f"{old_name} (未匹配)\n\n")

        self.preview_text.config(state='disabled')
        if self.rename_tasks:
            self.btn_rename.config(state=tk.NORMAL)
            self.preview_text.config(state='normal')
            self.preview_text.insert(tk.END, f"--------------------\n总计 {match_count} 个文件准备重命名。请核对后点击“执行改名”。")
            self.preview_text.config(state='disabled')

    def execute_rename(self):
        folder = self.folder_path_var.get()
        success = 0
        for old, new in self.rename_tasks:
            try:
                if os.path.exists(os.path.join(folder, new)):
                    continue
                os.rename(os.path.join(folder, old), os.path.join(folder, new))
                success += 1
            except Exception as e: 
                print(f"改名失败 {old}: {e}")
                
        messagebox.showinfo("完成", f"重命名执行完毕，成功修改 {success} 个文件。")
        self.btn_rename.config(state=tk.DISABLED)
        self.preview_text.config(state='normal')
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.config(state='disabled')

def create_ui(parent):
    return DataPreprocessingGUI(parent)
