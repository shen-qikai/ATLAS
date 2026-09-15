# 文件名: merge_cleaning.py
import os
import sys
import re
import csv
import glob
import time
import threading
import warnings
import pandas as pd
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

# 抑制pandas的DtypeWarning
warnings.filterwarnings('ignore', category=pd.errors.DtypeWarning)

class WaferMergeToolGUI:
    def __init__(self, parent_frame):
        self.parent = parent_frame
        
        # === 变量初始化 ===
        self.folder_path_var = tk.StringVar()
        self.cleaning_mode_var = tk.StringVar(value="1")
        self.generate_only_data_var = tk.BooleanVar(value=False)
        self.generate_summary_var = tk.BooleanVar(value=True)
        
        self.header_key_var = tk.StringVar(value="SITE_NUM")
        self.pass_fail_col_var = tk.StringVar(value="PASSFG")
        
        self.x_col_var = tk.StringVar(value="X_COORD")
        self.y_col_var = tk.StringVar(value="Y_COORD")
        
        self.pro_mode_var = tk.BooleanVar(value=False)
        self.listbox_items = [] # 存储 listbox 每一行对应的真实文件名
        
        self.create_widgets()
        
    def create_widgets(self):
        # --- 第一步：文件路径设置 ---
        input_frame = ttk.LabelFrame(self.parent, text="第一步：文件路径设置", padding="10")
        input_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(input_frame, text="数据文件夹路径:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(input_frame, textvariable=self.folder_path_var, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(input_frame, text="浏览...", command=self.browse_folder).grid(row=0, column=2)
        
        # --- [新增/优化] 文件列表与专业模式 ---
        # 调整为 fill=tk.BOTH, expand=True 让它能够随窗口缩放
        list_frame = ttk.LabelFrame(self.parent, text="文件预览与专业选项 (勾选项 = RT文件)", padding="10")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        pro_cb = ttk.Checkbutton(list_frame, text="开启专业模式 (手动多选指定RT文件)", 
                                 variable=self.pro_mode_var, command=self.toggle_pro_mode)
        pro_cb.pack(anchor=tk.W, pady=(0, 5))
        
        # 带滚动条的 Listbox，高度增加到 15 行
        scroll = ttk.Scrollbar(list_frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, yscrollcommand=scroll.set, 
                                     height=15, bg='#ffffff', font=("Consolas", 10))
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=self.file_listbox.yview)
        
        self.file_listbox.bind('<<ListboxSelect>>', self.on_listbox_select)
        self.file_listbox.config(state=tk.DISABLED) 
        
        # --- 第二步：关键列名设置 ---
        param_frame = ttk.LabelFrame(self.parent, text="第二步：关键列名设置", padding="10")
        param_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(param_frame, text="表头起始关键词:").grid(row=0, column=0, sticky=tk.W, pady=2)
        ttk.Entry(param_frame, textvariable=self.header_key_var, width=20).grid(row=0, column=1, padx=5, sticky=tk.W, pady=2)
        
        ttk.Label(param_frame, text="Pass/Fail 判定列:").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Entry(param_frame, textvariable=self.pass_fail_col_var, width=20).grid(row=1, column=1, padx=5, sticky=tk.W, pady=2)

        ttk.Label(param_frame, text="X 坐标列名:").grid(row=2, column=0, sticky=tk.W, pady=2)
        ttk.Entry(param_frame, textvariable=self.x_col_var, width=20).grid(row=2, column=1, padx=5, sticky=tk.W, pady=2)

        ttk.Label(param_frame, text="Y 坐标列名:").grid(row=3, column=0, sticky=tk.W, pady=2)
        ttk.Entry(param_frame, textvariable=self.y_col_var, width=20).grid(row=3, column=1, padx=5, sticky=tk.W, pady=2)

        # --- 第三步：数据清理方式 ---
        mode_frame = ttk.LabelFrame(self.parent, text="第三步：数据清理方式", padding="10")
        mode_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Radiobutton(mode_frame, text="1. 基于坐标去重 (Coord Cleaning) - 保留最后出现的重复坐标", 
                        variable=self.cleaning_mode_var, value="1").pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(mode_frame, text="2. 基于RT条件清理 (RT Cleaning) - 无RT全留，有RT则去Fail", 
                        variable=self.cleaning_mode_var, value="2").pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(mode_frame, text="3. 不进行清理 (仅合并)", 
                        variable=self.cleaning_mode_var, value="3").pack(anchor=tk.W, pady=2)
        
        ttk.Separator(mode_frame, orient='horizontal').pack(fill='x', pady=5)
        ttk.Checkbutton(mode_frame, text="生成纯数据文件 (Generate Only Data)", 
                        variable=self.generate_only_data_var).pack(anchor=tk.W, pady=2)
        
        ttk.Separator(mode_frame, orient='horizontal').pack(fill='x', pady=5)
        ttk.Checkbutton(mode_frame, text="输出整合文件 (生成汇总文件+清理结果)", 
                        variable=self.generate_summary_var).pack(anchor=tk.W, pady=2)
        
        # --- 执行按钮与日志 ---
        btn_frame = ttk.Frame(self.parent, padding="10")
        btn_frame.pack(fill=tk.X, padx=10)
        
        self.start_btn = ttk.Button(btn_frame, text="开始汇总清理", command=self.start_processing)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="清除日志", command=self.clear_log).pack(side=tk.LEFT, padx=5)
        
        log_frame = ttk.LabelFrame(self.parent, text="处理日志", padding="5")
        log_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # 适当减少日志框高度以腾出空间
        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', height=15)
        self.log_text.pack(fill=tk.X, expand=True)

    def browse_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.folder_path_var.set(folder_selected)
            self.load_file_list()

    def load_file_list(self):
        folder_path = self.folder_path_var.get().strip()
        if not os.path.exists(folder_path): return
        
        self.file_listbox.config(state=tk.NORMAL)
        self.file_listbox.delete(0, tk.END)
        self.listbox_items = []
        
        files = [f for f in os.listdir(folder_path) if f.lower().endswith('.csv')]
        wafer_ids = self.extract_wafer_ids_sorted(folder_path)
        
        for wid in wafer_ids:
            self.file_listbox.insert(tk.END, f"--- Wafer: {wid} ---")
            self.file_listbox.itemconfig(tk.END, fg='blue', bg='#f0f0f0')
            self.listbox_items.append(None)
            
            w_files = [f for f in files if f'_{wid}#' in f or f.endswith(f'_{wid}#.csv')]
            for f in w_files:
                self.file_listbox.insert(tk.END, f"    {f}")
                self.listbox_items.append(f)
                
        self.auto_select_rt_files()
        
        if not self.pro_mode_var.get():
            self.file_listbox.config(state=tk.DISABLED)

    def auto_select_rt_files(self):
        self.file_listbox.selection_clear(0, tk.END)
        for i, item in enumerate(self.listbox_items):
            if item and 'RT' in item.upper():
                self.file_listbox.selection_set(i)
                self.file_listbox.itemconfig(i, fg='green')

    def toggle_pro_mode(self):
        if self.pro_mode_var.get():
            self.file_listbox.config(state=tk.NORMAL)
        else:
            self.file_listbox.config(state=tk.NORMAL)
            self.auto_select_rt_files() 
            self.file_listbox.config(state=tk.DISABLED)

    def on_listbox_select(self, event):
        selections = self.file_listbox.curselection()
        for idx in selections:
            if self.listbox_items[idx] is None:
                self.file_listbox.selection_clear(idx)

    def get_rt_files_set(self):
        rt_set = set()
        if self.pro_mode_var.get():
            for idx in self.file_listbox.curselection():
                if self.listbox_items[idx] is not None:
                    rt_set.add(self.listbox_items[idx])
        else:
            for item in self.listbox_items:
                if item and 'RT' in item.upper():
                    rt_set.add(item)
        return rt_set

    def log(self, message):
        def _update():
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')
        self.parent.after(0, _update)

    def clear_log(self):
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')

    def start_processing(self):
        folder_path = self.folder_path_var.get().strip()
        header_key = self.header_key_var.get().strip()
        pass_fail_col = self.pass_fail_col_var.get().strip()
        x_col = self.x_col_var.get().strip()
        y_col = self.y_col_var.get().strip()

        if not folder_path or not os.path.exists(folder_path) or not header_key:
            messagebox.showwarning("提示", "路径和表头参数设置错误！")
            return
        
        self.start_btn.config(state='disabled')
        self.clear_log()
        rt_files_set = self.get_rt_files_set()
        
        thread = threading.Thread(target=self.run_process, 
                                  args=(folder_path, header_key, pass_fail_col, x_col, y_col, rt_files_set))
        thread.daemon = True
        thread.start()

    def run_process(self, folder_path, header_key, pass_fail_col, x_col, y_col, rt_files_set):
        try:
            self.log("=== 开始合并汇总处理 ===")
            mode = self.cleaning_mode_var.get()
            generate_only_data = self.generate_only_data_var.get()
            generate_summary = self.generate_summary_var.get()
            
            folder_name_summary = "summary_data"
            folder_name_cleaning = "summary_cleaning_data"
            
            if generate_summary or mode not in ('1', '2'):
                output_folder_summary = self.create_output_folder(folder_path, folder_name_summary, False)
            output_folder_summary_cleaning = self.create_output_folder(folder_path, folder_name_cleaning, False)

            files = os.listdir(folder_path)
            wafer_ids = self.extract_wafer_ids_sorted(folder_path)
            self.log(f"检测到 {len(wafer_ids)} 个 Wafer ID")

            last_processed_folder = output_folder_summary if (generate_summary or mode not in ('1', '2')) else output_folder_summary_cleaning

            for wafer_id in wafer_ids:
                self.log(f"\n--- 处理晶圆 {wafer_id} ---")
                wafer_files = []
                for f in files:
                    if f.endswith(f'_{wafer_id}#.csv') or f'_{wafer_id}#_' in f:
                        try:
                            ending_time = self.find_ending_time(os.path.join(folder_path, f))
                        except:
                            ending_time = datetime.min
                        wafer_files.append({'filename': f, 'ending_time': ending_time})
                
                if not wafer_files: continue
                wafer_files.sort(key=lambda x: x['ending_time'] if x['ending_time'] else datetime.min)

                all_data = []
                is_first_file = True
                wafer_filenames = [fw['filename'] for fw in wafer_files]
                
                for file_info in wafer_files:
                    f_name = file_info['filename']
                    ending_time = file_info['ending_time']
                    full_file_path = os.path.join(folder_path, f_name)
                    
                    self.log(f"  读取: {f_name}")
                    try:
                        header_line = self.find_header_line(full_file_path, header_key)
                        data_start_line = self.find_data_start_line(full_file_path)
                        df = self.safe_read_csv(full_file_path, header_line, data_start_line, is_first_file)
                        
                        is_first_file = False
                        df['source_file'] = f_name
                        df['ending_time'] = ending_time
                        all_data.append(df)
                    except Exception as e:
                        self.log(f"  读取文件 {f_name} 失败: {e}")
                        continue
                
                if not all_data: continue
                
                summary_df = pd.concat(all_data, ignore_index=True)
                first_file = wafer_files[0]['filename']
                
                if f'_{wafer_id}#' in first_file:
                    base_part = first_file.split(f'_{wafer_id}#')[0]
                    out_name_summary = f'{base_part}_{wafer_id}#_summary.csv'
                    out_name_summary_cleaning = f'{base_part}_{wafer_id}#_summary_cleaning.csv'
                else:
                    out_name_summary = f'Wafer_{wafer_id}_summary.csv'
                    out_name_summary_cleaning = f'Wafer_{wafer_id}_summary_cleaning.csv'

                full_out_path_summary_cleaning = os.path.join(output_folder_summary_cleaning, out_name_summary_cleaning)

                if not generate_summary and mode in ('1', '2'):
                    self.log(f"  [跳过] 不输出整合文件夹，直接进行清理...")
                    if mode == '1':
                        success = self.data_cleaning_coord_logic(
                            None, full_out_path_summary_cleaning, x_col, y_col, df=summary_df, header_key=header_key)
                    elif mode == '2':
                        success = self.data_cleaning_rt_logic_new(
                            None, full_out_path_summary_cleaning, pass_fail_col, rt_files_set, wafer_filenames, df=summary_df, header_key=header_key)
                    if success: last_processed_folder = output_folder_summary_cleaning
                else:
                    full_out_path_summary = os.path.join(output_folder_summary, out_name_summary)
                    summary_df.to_csv(full_out_path_summary, index=False)
                    self.log(f"  已生成汇总文件: {out_name_summary}")

                    if mode == '1': 
                        success = self.data_cleaning_coord_logic(full_out_path_summary, full_out_path_summary_cleaning, x_col, y_col)
                        if success: last_processed_folder = output_folder_summary_cleaning
                    elif mode == '2': 
                        success = self.data_cleaning_rt_logic_new(full_out_path_summary, full_out_path_summary_cleaning, pass_fail_col, rt_files_set, wafer_filenames)
                        if success: last_processed_folder = output_folder_summary_cleaning
                    elif mode == '3':
                        last_processed_folder = output_folder_summary

            if generate_only_data:
                self.log("\n=== 正在生成纯数据文件 ===")
                folder_name_onlydata = "summary_clean_onlydata"
                output_folder_onlydata = self.create_output_folder(last_processed_folder, folder_name_onlydata, True)
                self.process_only_data_logic(last_processed_folder, output_folder_onlydata, header_key)

            self.log("\n=== 全部处理完成! ===")
            self.parent.after(0, lambda: messagebox.showinfo("完成", "所有文件处理完成！"))

        except Exception as e:
            self.log(f"\n发生严重错误: {str(e)}")
            self.parent.after(0, lambda: messagebox.showerror("错误", f"程序发生错误:\n{str(e)}"))
        finally:
            self.parent.after(0, lambda: self.start_btn.config(state='normal'))

    def create_output_folder(self, base_path, folder_name, create_in_parent):
        if create_in_parent:
            parent_dir = os.path.dirname(base_path)
            folder_path = os.path.join(parent_dir, folder_name)
        else:
            folder_path = os.path.join(base_path, folder_name)
        if not os.path.exists(folder_path): os.makedirs(folder_path)
        return folder_path

    def extract_wafer_ids_sorted(self, folder_path):
        files = [f for f in os.listdir(folder_path) if f.lower().endswith('.csv')]
        wafer_ids = set()
        for f in files:
            name_without_ext = os.path.splitext(f)[0]
            hash_pos = name_without_ext.find('#')
            if hash_pos == -1: continue
            before_hash = name_without_ext[:hash_pos]
            parts = before_hash.split('_')
            if len(parts) >= 2: wafer_ids.add(f"{parts[-2]}_{parts[-1]}")
            elif len(parts) == 1: wafer_ids.add(parts[0])
        def sort_key(x):
            x_parts = x.split('_')
            return (x_parts[0], int(x_parts[1]) if len(x_parts)>=2 and x_parts[1].isdigit() else 0)
        return sorted(wafer_ids, key=sort_key)

    def find_data_start_line(self, filename):
        with open(filename, encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f):
                if 'TRUE' in line.upper() or 'FALSE' in line.upper(): return i
        raise ValueError("未找到数据行")

    def find_header_line(self, filename, target_header):
        with open(filename, encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f):
                if line.strip().startswith(target_header): return i
        return 0

    def find_ending_time(self, filename):
        time_pattern = r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2})'
        try:
            with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.reader(f)
                for row in reader:
                    for i, cell in enumerate(row):
                        cell_text = cell.strip().lower()
                        if 'ending time' in cell_text or 'end time' in cell_text:
                            match = re.search(time_pattern, cell)
                            if match: return pd.to_datetime(match.group(1)).replace(tzinfo=None).to_pydatetime()
        except: pass
        return datetime.min

    def safe_read_csv(self, filename, header_line, data_start_line, is_first_file):
        data = []
        with open(filename, 'r', encoding='utf-8', errors='replace') as f:
            lines = list(csv.reader(f))
        start_line = header_line if is_first_file else data_start_line
        col = lines[header_line] 
        for i in range(start_line, len(lines)):
            data.append(lines[i])
        for row in data:
            while len(row) < len(col): row.append('')
        return pd.DataFrame(data[1:] if is_first_file else data, columns=col)

    def _find_test_data_boundary_in_df(self, df, target_col_hint='PASSFG'):
        """在 DataFrame 中找到第一条含 TRUE/FALSE 的测试数据行索引"""
        cols_to_check = []
        if target_col_hint in df.columns:
            cols_to_check.append(target_col_hint)
        # 同时扫描所有 object 类型的列作为备选
        for col in df.select_dtypes(include=['object']).columns:
            if col not in cols_to_check:
                cols_to_check.append(col)
        for col in cols_to_check:
            col_vals = df[col].astype(str).str.upper()
            mask = col_vals.isin(['TRUE', 'FALSE'])
            if mask.any():
                return int(mask.idxmax())
        return 0

    def data_cleaning_coord_logic(self, input_file, output_file, x_col, y_col, df=None, header_key=None):
        try:
            if df is not None:
                start_idx = self._find_test_data_boundary_in_df(df)
                header = df.iloc[:start_idx]
                test_data = df.iloc[start_idx:]
            else:
                df = pd.read_csv(input_file, low_memory=False)
                try: start_idx = self.find_data_start_line(input_file) - 1
                except: start_idx = 0 
                header = df.iloc[:start_idx]
                test_data = df.iloc[start_idx:]
            before_count = len(test_data)
            if x_col in test_data.columns and y_col in test_data.columns:
                test_data = test_data.drop_duplicates(subset=[x_col, y_col], keep='last')
            after_count = len(test_data)
            pd.concat([header, test_data], ignore_index=True).to_csv(output_file, index=False)
            self.log(f"  坐标去重完成: {os.path.basename(output_file)}")
            self.log(f"  数据清理：{before_count} → {after_count}")
            return True
        except Exception as e:
            self.log(f"  坐标去重失败: {e}")
            return False

    def data_cleaning_rt_logic_new(self, input_file, output_file, target_col, rt_files_set, wafer_filenames, df=None, header_key=None):
        try:
            if df is not None:
                start_idx = self._find_test_data_boundary_in_df(df, target_col)
                header = df.iloc[:start_idx]
                test_data = df.iloc[start_idx:]
            else:
                df = pd.read_csv(input_file, low_memory=False)
                try: start_idx = self.find_data_start_line(input_file) - 1
                except: start_idx = 0 
                header = df.iloc[:start_idx]
                test_data = df.iloc[start_idx:]
            before_count = len(test_data)
            wafer_has_rt = any(f in rt_files_set for f in wafer_filenames)
            if wafer_has_rt:
                if target_col not in test_data.columns:
                    raise ValueError(f"警告：未找到判定列 '{target_col}'，无法执行过滤！")
                is_rt_file = test_data['source_file'].isin(rt_files_set)
                fail_keywords = ['FALSE', 'FAIL', 'F', '0']
                is_fail = test_data[target_col].astype(str).str.upper().isin(fail_keywords)
                is_pass = ~is_fail
                test_data = test_data[is_rt_file | is_pass]
                self.log(f"  [RT检测命中] 已剔除初测中 {is_fail.sum()} 条 Fail 记录。")
            else:
                self.log(f"  [无RT文件] 晶圆数据全量保留。")
            after_count = len(test_data)
            pd.concat([header, test_data], ignore_index=True).to_csv(output_file, index=False)
            self.log(f"  清理完成: {os.path.basename(output_file)}")
            self.log(f"  数据清理：{before_count} → {after_count}")
            return True
        except Exception as e:
            self.log(f"  清理失败: {e}")
            return False

    def process_only_data_logic(self, source_folder, output_folder, header_key):
        files = [f for f in os.listdir(source_folder) if f.endswith('.csv')]
        for f in files:
            source_path = os.path.join(source_folder, f)
            try:
                hl = self.find_header_line(source_path, header_key)
                dl = self.find_data_start_line(source_path)
                df = self.safe_read_csv(source_path, hl, dl, False)
                nm, ext = os.path.splitext(f)
                df.to_csv(os.path.join(output_folder, f"{nm}_onlydata{ext}"), index=False)
            except Exception as e:
                self.log(f"  处理纯数据失败: {e}")

def create_ui(parent):
    return WaferMergeToolGUI(parent)

if __name__ == "__main__":
    root = tk.Tk()
    root.title("[独立运行] - 数据清理工具 v2.1")
    root.geometry("900x950")
    app = WaferMergeToolGUI(root)
    root.mainloop()