# 文件名: wafer_yield_summary.py
import pandas as pd
import numpy as np
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import filedialog, messagebox
from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment

class WaferYieldSummaryApp:
    def __init__(self, parent_frame):
        self.parent = parent_frame
        
        self.selected_folder = ""
        self.file_list = []

        # 创建一个主 Canvas 加 Scrollbar 
        self.canvas = tk.Canvas(self.parent, borderwidth=0, highlightthickness=0)
        self.scrollbar_main = tk.Scrollbar(self.parent, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar_main.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar_main.pack(side="right", fill="y")

        container = self.scrollable_frame

        # --- 1. 路径选择 ---
        tk.Label(container, text="1. 选择测试数据文件夹 (CSV)", font=('微软雅黑', 11, 'bold')).pack(pady=(15, 5))
        tk.Button(container, text=" 浏览文件夹 ", command=self.select_directory, bg="#007bff", fg="white").pack()
        self.lbl_path = tk.Label(container, text="未选择路径", fg="gray", wraplength=600)
        self.lbl_path.pack(pady=5)

        # --- 2. 文件列表 ---
        tk.Label(container, text="2. 选择要处理的晶圆 (可多选)", font=('微软雅黑', 11, 'bold')).pack(pady=5)
        list_frame = tk.Frame(container)
        list_frame.pack()
        self.scrollbar_list = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.file_listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, width=100, height=10, yscrollcommand=self.scrollbar_list.set)
        self.scrollbar_list.config(command=self.file_listbox.yview)
        self.file_listbox.pack(side=tk.LEFT)
        self.scrollbar_list.pack(side=tk.RIGHT, fill=tk.Y)

        # --- 3. 参数汇总配置区 ---
        param_frame = tk.LabelFrame(container, text=" 功能 A：参数良率/失效率汇总 ", font=('微软雅黑', 10, 'bold'), padx=20, pady=10, fg="#28a745")
        param_frame.pack(pady=15, fill="x", padx=40)

        tk.Label(param_frame, text="表现形式:").grid(row=0, column=0, sticky="w")
        self.report_mode = tk.StringVar(value="YieldRate")
        tk.Radiobutton(param_frame, text="良率 (%)", variable=self.report_mode, value="YieldRate").grid(row=0, column=1, padx=20)
        tk.Radiobutton(param_frame, text="失效率 (%)", variable=self.report_mode, value="FailureRate").grid(row=0, column=2)

        tk.Label(param_frame, text="分母模式:").grid(row=1, column=0, sticky="w", pady=5)
        self.denom_mode = tk.StringVar(value="TotalCount")
        tk.Radiobutton(param_frame, text="全文件总行数 (包含空数据)", variable=self.denom_mode, value="TotalCount").grid(row=1, column=1, padx=20)
        tk.Radiobutton(param_frame, text="有效非空数字量", variable=self.denom_mode, value="ValidOnly").grid(row=1, column=2)

        self.btn_param = tk.Button(param_frame, text="生成 [参数汇总] 报表", command=self.start_param_thread, 
                                  bg="#28a745", fg="white", font=('微软雅黑', 10, 'bold'), state=tk.DISABLED, width=30)
        self.btn_param.grid(row=2, column=0, columnspan=3, pady=10)

        # --- 4. BIN 汇总配置区 ---
        bin_frame = tk.LabelFrame(container, text=" 功能 B：BIN 分类汇总 ", font=('微软雅黑', 10, 'bold'), padx=20, pady=10, fg="#e67e22")
        bin_frame.pack(pady=10, fill="x", padx=40)

        tk.Label(bin_frame, text="BIN 列表头名:").grid(row=0, column=0, sticky="w")
        self.bin_col_entry = tk.Entry(bin_frame, width=20)
        self.bin_col_entry.insert(0, "SOFT_BIN")
        self.bin_col_entry.grid(row=0, column=1, padx=20, sticky="w")

        self.btn_bin = tk.Button(bin_frame, text="生成 [BIN汇总] 报表", command=self.start_bin_thread, 
                                bg="#e67e22", fg="white", font=('微软雅黑', 10, 'bold'), state=tk.DISABLED, width=30)
        self.btn_bin.grid(row=1, column=0, columnspan=2, pady=10)

        # --- 5. 测试项均值配置区 ---
        avg_frame = tk.LabelFrame(container, text=" 功能 C：测试项均值计算 ", font=('微软雅黑', 10, 'bold'), padx=20, pady=10, fg="#9b59b6")
        avg_frame.pack(pady=10, fill="x", padx=40)

        tk.Label(avg_frame, text="按文件名解析: 前缀_LotID_WaferID_后缀.csv", font=('微软雅黑', 8), fg="gray").grid(row=0, column=0, columnspan=2, pady=5)

        self.btn_avg = tk.Button(avg_frame, text="生成 [测试项均值] 报表", command=self.start_avg_thread, 
                                bg="#9b59b6", fg="white", font=('微软雅黑', 10, 'bold'), state=tk.DISABLED, width=30)
        self.btn_avg.grid(row=1, column=0, columnspan=2, pady=10)

    def select_directory(self):
        path = filedialog.askdirectory()
        if path:
            self.selected_folder = os.path.abspath(path)
            self.lbl_path.config(text=self.selected_folder, fg="green")
            self.file_list = [f for f in os.listdir(self.selected_folder) if f.lower().endswith('.csv')]
            self.file_listbox.delete(0, tk.END)
            for f in self.file_list: self.file_listbox.insert(tk.END, f)
            self.file_listbox.select_set(0, tk.END)
            self.btn_param.config(state=tk.NORMAL)
            self.btn_bin.config(state=tk.NORMAL)
            self.btn_avg.config(state=tk.NORMAL)

    def get_selected_files(self):
        indices = self.file_listbox.curselection()
        if not indices:
            messagebox.showwarning("警告", "请先选择晶圆文件！")
            return None
        return [self.file_list[i] for i in indices]

    # ==========================================
    # 高效 Excel 样式渲染器
    # ==========================================
    def optimized_excel_style(self, file_path, sheet_name, min_pct_row):
        """精准只处理需要格式化的区域，跳过庞大的非数据区"""
        wb = load_workbook(file_path)
        ws = wb[sheet_name]
        font_style = Font(name='微软雅黑', size=10)
        center_align = Alignment(horizontal='center', vertical='center')
        
        # 1. 统一应用表头和基础样式
        for row in ws.iter_rows():
            for cell in row:
                cell.font = font_style
                cell.alignment = center_align

        # 2. 精准应用百分比（跳过元数据行和Wafer_ID列）
        for row in ws.iter_rows(min_row=min_pct_row, min_col=2):
            for cell in row:
                if isinstance(cell.value, (float, int)):
                    cell.number_format = '0.00%'
        
        # 3. 自动调整列宽
        for col in ws.columns:
            # 只取前10行计算列宽，避免遍历全表拖慢速度
            sample_cells = [c.value for c in col[:10] if c.value is not None]
            max_len = max([len(str(val)) for val in sample_cells] + [10])
            ws.column_dimensions[col[0].column_letter].width = max_len + 2
            
        wb.save(file_path)

    # ==========================================
    # 功能 A：参数汇总 (多线程版)
    # ==========================================
    def start_param_thread(self):
        selected = self.get_selected_files()
        if not selected: return
        self.btn_param.config(state=tk.DISABLED, text="正在飞速计算中...")
        threading.Thread(target=self.run_param_report, args=(selected,), daemon=True).start()

    def process_single_param_file(self, args):
        """独立工作函数，用于并发读取计算"""
        f, folder, mode, denom = args
        path = os.path.join(folder, f)
        try: df_raw = pd.read_csv(path, header=None, encoding='utf-8')
        except: df_raw = pd.read_csv(path, header=None, encoding='gbk')
        
        h, u, lsl, usl = df_raw.iloc[0], df_raw.iloc[1], df_raw.iloc[2], df_raw.iloc[3]
        data_df = df_raw.iloc[5:].copy()
        data_df.columns = h
        
        wafer_id = f.rsplit('.', 1)[0]
        row_data = {"Wafer_ID": wafer_id}
        meta_info = {}
        all_columns = []  # 此文件中出现的所有测试项（保持表头原始顺序）
        seen = set()

        for i, col in enumerate(h):
            if pd.isna(col) or str(col).strip() == "": continue
            if col not in seen:
                seen.add(col)
                all_columns.append(col)
            
            l = pd.to_numeric(lsl[i], errors='coerce')
            u_val = pd.to_numeric(usl[i], errors='coerce')
            
            has_spec = not (np.isnan(l) and np.isnan(u_val))
            
            if has_spec:
                meta_info[col] = [u[i], l, u_val]
                
                # 向量化运算，极速判定
                vals = pd.to_numeric(data_df[col], errors='coerce')
                valid_vals = vals.dropna()
                
                total = len(valid_vals) if denom == "ValidOnly" else len(data_df)
                if total > 0:
                    if mode == "YieldRate":
                        count = ((valid_vals >= l) & (valid_vals <= u_val)).sum()
                    else:
                        count = ((valid_vals < l) | (valid_vals > u_val)).sum()
                    row_data[col] = float(count / total)
                else:
                    row_data[col] = "No_data"
            else:
                # 无 USL/LSL 规格，无法计算良率/失效率
                row_data[col] = "No_SPEC"
                meta_info[col] = ["No_SPEC", "No_SPEC", "No_SPEC"]  # 无规格标记
                
        return row_data, meta_info, all_columns

    def run_param_report(self, selected):
        start_time = time.time()
        mode = self.report_mode.get()
        denom = self.denom_mode.get()
        
        # 构建并发任务
        tasks = [(f, self.selected_folder, mode, denom) for f in selected]
        results = []
        meta_info = {}
        global_all_columns = {}  # 用 dict 维护首次出现的顺序（有序集合）
        
        try:
            # 启动线程池并发读取处理
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                for row_data, meta, cols in executor.map(self.process_single_param_file, tasks):
                    results.append(row_data)
                    for col in cols:  # 按该 wafer 的表头顺序加入，保持首次出现顺序
                        global_all_columns[col] = None
                    if not meta_info and meta:
                        meta_info = meta  # 只需要获取一次元数据

            # 按首次出现顺序排列（即原始 CSV 表头顺序）
            global_cols_sorted = list(global_all_columns.keys())
            
            # 对每个 wafer 补充缺失的列，填 "NA"
            for row_data in results:
                for col in global_cols_sorted:
                    if col not in row_data:
                        row_data[col] = "NA"
            
            # 先在 meta_info 中补充缺失的列（列序与 global_cols_sorted 一致）
            for col in global_cols_sorted:
                if col not in meta_info:
                    meta_info[col] = ["", "", ""]
            
            # 按统一列序构建 meta DataFrame
            df_meta = pd.DataFrame(meta_info, index=['单位','下限','上限'])[global_cols_sorted]
            
            # 按统一列序排列数据
            df_data = pd.DataFrame(results)
            df_data = df_data[["Wafer_ID"] + global_cols_sorted].set_index("Wafer_ID")
            df_final = pd.concat([df_meta, df_data])
            
            mode_str = "良率" if mode == "YieldRate" else "失效率"
            denom_str = "按总行数" if denom == "TotalCount" else "按有效数据"
            out_path = os.path.join(os.path.dirname(self.selected_folder), f"晶圆参数汇总_{mode_str}_{denom_str}.xlsx")
            
            with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
                df_final.to_excel(writer, sheet_name="参数汇总")
                
            self.optimized_excel_style(out_path, "参数汇总", min_pct_row=5)  # 5行以下才渲染百分比
            
            elapsed = time.time() - start_time
            self.parent.after(0, lambda: messagebox.showinfo("极速完成", f"参数报表已生成！\n\n耗时: {elapsed:.2f} 秒\n\n保存路径：\n{out_path}"))
        except Exception as e:
            self.parent.after(0, lambda: messagebox.showerror("错误", str(e)))
        finally:
            self.parent.after(0, lambda: self.btn_param.config(state=tk.NORMAL, text="生成 [参数汇总] 报表"))

    # ==========================================
    # 功能 B：BIN 汇总 (多线程版)
    # ==========================================
    def start_bin_thread(self):
        selected = self.get_selected_files()
        if not selected: return
        self.btn_bin.config(state=tk.DISABLED, text="正在飞速计算中...")
        threading.Thread(target=self.run_bin_report, args=(selected,), daemon=True).start()

    def process_single_bin_file(self, args):
        """独立工作函数，只读取目标列，瞬间完成"""
        f, folder, bin_col = args
        path = os.path.join(folder, f)
        
        # 只提取需要的 BIN 列，极大降低 I/O 负担
        try: df = pd.read_csv(path, header=0, skiprows=[1,2,3,4], usecols=[bin_col], encoding='utf-8')
        except: 
            try: df = pd.read_csv(path, header=0, skiprows=[1,2,3,4], usecols=[bin_col], encoding='gbk')
            except: return None, None # 列可能不存在
            
        wafer_id = f.rsplit('.', 1)[0]
        total = len(df)
        
        # 向量化清理与统计
        s = df[bin_col].dropna().astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        counts = s.value_counts().to_dict()
        
        row_data = {"Wafer_ID": wafer_id, "_total_": total}
        for b, count in counts.items():
            row_data[f"BIN_{b}"] = count
            
        unique_bins = set(counts.keys())
        return row_data, unique_bins

    def run_bin_report(self, selected):
        start_time = time.time()
        bin_col = self.bin_col_entry.get().strip()
        
        tasks = [(f, self.selected_folder, bin_col) for f in selected]
        all_row_data = []
        global_bins = set()

        try:
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                for row_data, unique_bins in executor.map(self.process_single_bin_file, tasks):
                    if row_data is not None:
                        all_row_data.append(row_data)
                        global_bins.update(unique_bins)

            # 自然排序 BIN 名称
            def sort_bin_keys(b):
                try: return (0, int(b))
                except: return (1, b)
            sorted_bins = sorted(list(global_bins), key=sort_bin_keys)

            # 转换为百分比
            final_results = []
            for row in all_row_data:
                res = {"Wafer_ID": row["Wafer_ID"]}
                total = row.pop("_total_")
                for b in sorted_bins:
                    bin_key = f"BIN_{b}"
                    res[bin_key] = row.get(bin_key, 0) / total if total > 0 else 0
                final_results.append(res)

            df_bin = pd.DataFrame(final_results).set_index("Wafer_ID")
            out_path = os.path.join(os.path.dirname(self.selected_folder), "晶圆BIN分类汇总.xlsx")
            
            with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
                df_bin.to_excel(writer, sheet_name="BIN汇总")
                
            self.optimized_excel_style(out_path, "BIN汇总", min_pct_row=2) # 2行以下渲染百分比
            
            elapsed = time.time() - start_time
            self.parent.after(0, lambda: messagebox.showinfo("极速完成", f"BIN报表已生成！\n\n耗时: {elapsed:.2f} 秒\n\n保存路径：\n{out_path}"))
        except Exception as e:
            self.parent.after(0, lambda: messagebox.showerror("错误", str(e)))
        finally:
            self.parent.after(0, lambda: self.btn_bin.config(state=tk.NORMAL, text="生成 [BIN汇总] 报表"))

    # ==========================================
    # 功能 C：测试项均值计算
    # ==========================================
    def start_avg_thread(self):
        selected = self.get_selected_files()
        if not selected: return
        self.btn_avg.config(state=tk.DISABLED, text="正在飞速计算中...")
        threading.Thread(target=self.run_avg_report, args=(selected,), daemon=True).start()

    def process_single_avg_file(self, args):
        """读取单个文件，计算所有列的平均值（数字列算均值，文本列返回 'NA'）
        每个 CSV 对应一个 wafer，Lot ID / Wafer ID 从文件名解析"""
        f, folder = args
        path = os.path.join(folder, f)
        try:
            df_raw = pd.read_csv(path, header=None, encoding='utf-8')
        except:
            df_raw = pd.read_csv(path, header=None, encoding='gbk')

        # 前5行为表头（0-4），数据从第6行（index 5）开始
        header_rows = df_raw.iloc[:5].copy()  # 保留5行表头
        data_df = df_raw.iloc[5:].copy()      # 数据行

        # 用第1行作为列名
        h = df_raw.iloc[0]
        data_df.columns = h

        # 从文件名解析 Lot ID 和 Wafer ID
        # 文件名格式: 前缀_LotID_WaferID_后缀.csv → 拆分后 index 1 = LotID, index 2 = WaferID
        base_name = f.rsplit('.', 1)[0]  # 去除 .csv
        parts = base_name.split('_')
        if len(parts) >= 3:
            lot_id = parts[1]
            wafer_str = parts[2]
        else:
            raise ValueError(f"文件名 '{f}' 格式不符合 前缀_LotID_WaferID_后缀，下划线分段不足3段: {parts}")

        # Wafer ID 去#再转整数格式化为 WXX（如 _05#_ → W05）
        if wafer_str.startswith('W') and wafer_str[1:].isdigit():
            wafer_str = wafer_str[1:]
        wafer_str = wafer_str.replace('#', '')
        try:
            wafer_num = str(int(float(wafer_str))).zfill(2)
        except:
            wafer_num = wafer_str
        lot_wafer_id = f"{lot_id}_W{wafer_num}"

        # 计算每列的平均值（所有数据行取平均）
        avg_row = {"Lot_WaferID": lot_wafer_id}
        for col in h:
            col_name = str(col).strip() if not pd.isna(col) else ""
            if col_name == "":
                avg_row[col] = "NA"
                continue
            # 尝试转数值
            vals = pd.to_numeric(data_df[col], errors='coerce')
            if vals.notna().sum() > 0:
                avg_row[col] = vals.mean()
            else:
                avg_row[col] = "NA"

        return avg_row, header_rows, list(h)

    def run_avg_report(self, selected):
        start_time = time.time()

        all_avg_rows = []
        first_header_rows = None
        first_columns = None

        try:
            # 串行处理以确保表头一致性（不同文件表头可能不同，以第一个文件为准）
            for f in selected:
                args = (f, self.selected_folder)
                avg_row, header_rows, columns = self.process_single_avg_file(args)
                all_avg_rows.append(avg_row)
                if first_header_rows is None:
                    first_header_rows = header_rows
                    first_columns = columns

            # 构建最终 DataFrame
            # 列顺序: Lot_WaferID + 所有原始列
            output_columns = ["Lot_WaferID"] + list(first_columns)

            # 构建数据行（从第6行开始），avg_row 已包含 Lot_WaferID
            data_rows = []
            for avg_row in all_avg_rows:
                row = {}
                for col in output_columns:
                    row[col] = avg_row.get(col, "NA")
                data_rows.append(row)

            df_data = pd.DataFrame(data_rows, columns=output_columns)

            # 构建带有5行表头的完整 DataFrame：
            # 前5行：Lot_WaferID 列为空，其余列为原始表头行的值
            header_data = []
            for row_idx in range(5):
                row = {"Lot_WaferID": ""}
                for col_idx, col in enumerate(first_columns):
                    val = first_header_rows.iloc[row_idx, col_idx]
                    # 保留原始值，NaN 转为空字符串
                    row[col] = "" if pd.isna(val) else val
                header_data.append(row)

            df_header = pd.DataFrame(header_data, columns=output_columns)
            df_final = pd.concat([df_header, df_data], ignore_index=True)

            out_path = os.path.join(os.path.dirname(self.selected_folder), "晶圆测试项均值汇总.xlsx")

            with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
                df_final.to_excel(writer, sheet_name="测试项均值", index=False, header=False)

            # 应用样式
            wb = load_workbook(out_path)
            ws = wb["测试项均值"]
            font_style = Font(name='微软雅黑', size=10)
            center_align = Alignment(horizontal='center', vertical='center')

            for row in ws.iter_rows():
                for cell in row:
                    cell.font = font_style
                    cell.alignment = center_align

            # 自动调整列宽
            for col in ws.columns:
                sample_cells = [c.value for c in col[:15] if c.value is not None]
                max_len = max([len(str(val)) for val in sample_cells] + [10])
                ws.column_dimensions[col[0].column_letter].width = max_len + 2

            wb.save(out_path)

            elapsed = time.time() - start_time
            self.parent.after(0, lambda: messagebox.showinfo("极速完成", f"测试项均值报表已生成！\n\n耗时: {elapsed:.2f} 秒\n\n保存路径：\n{out_path}"))
        except Exception as e:
            self.parent.after(0, lambda: messagebox.showerror("错误", str(e)))
        finally:
            self.parent.after(0, lambda: self.btn_avg.config(state=tk.NORMAL, text="生成 [测试项均值] 报表"))

# ==== 提供给 main.py 调用的标准接口 ====
def create_ui(parent_frame):
    app = WaferYieldSummaryApp(parent_frame)
    return app
