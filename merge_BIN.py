# 文件名: merge_BIN.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
import glob
import re
import warnings
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from pathlib import Path

# 抑制pandas的DtypeWarning
warnings.filterwarnings('ignore', category=pd.errors.DtypeWarning)

# ==========================================
#  核心逻辑函数 (完全保留)
# ==========================================

def find_header_line(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if line.strip().startswith('SITE_NUM'):
                    return i
    except UnicodeDecodeError:
        with open(filename, 'r', encoding='gbk') as f:
            for i, line in enumerate(f):
                if line.strip().startswith('SITE_NUM'):
                    return i
    except Exception as e:
        print(f"读取文件查找表头时出错: {e}")
        return 0
    return 0

def create_output_folder_in_parent(base_path, folder_name):
    abs_base_path = os.path.abspath(base_path)
    parent_dir = os.path.dirname(abs_base_path)
    folder_path = os.path.join(parent_dir, folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    return folder_path

def get_first_csv_headers(folder_path):
    csv_files = glob.glob(os.path.join(folder_path, "*.csv"))
    if not csv_files:
        return []
    first_file = csv_files[0]
    try:
        header_row = find_header_line(first_file)
        df = pd.read_csv(first_file, skiprows=header_row, nrows=0)
        return df.columns.tolist()
    except Exception as e:
        print(f"无法读取参考文件表头 ({os.path.basename(first_file)}): {e}")
        return []

def resolve_column_selection(user_input, all_headers):
    if not user_input:
        return []
    raw_selections = [item.strip() for item in user_input.split(',') if item.strip()]
    final_columns = []
    for item in raw_selections:
        if '-' in item and item not in all_headers:
            parts = item.split('-')
            if len(parts) == 2:
                start_col = parts[0].strip()
                end_col = parts[1].strip()
                if start_col in all_headers and end_col in all_headers:
                    try:
                        start_idx = all_headers.index(start_col)
                        end_idx = all_headers.index(end_col)
                        if start_idx <= end_idx:
                            range_cols = all_headers[start_idx : end_idx + 1]
                            final_columns.extend(range_cols)
                            print(f"  已解析范围 '{item}': 包含 {len(range_cols)} 个列")
                        else:
                            print(f"  警告: 范围 '{item}' 的起始列在结束列之后，已跳过。")
                    except ValueError:
                        print(f"  警告: 解析范围 '{item}' 时出错，已跳过。")
                else:
                    print(f"  警告: 范围 '{item}' 中的起始或结束列不存在于文件中，尝试作为单列名处理。")
                    final_columns.append(item)
            else:
                final_columns.append(item)
        else:
            final_columns.append(item)
    seen = set()
    unique_columns = []
    for col in final_columns:
        if col not in seen:
            unique_columns.append(col)
            seen.add(col)
    return unique_columns

def convert_value_to_numeric(value):
    try:
        return pd.to_numeric(value)
    except:
        return value

def extract_lot_wafer_pairs_sorted(folder_path):
    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"文件夹不存在: {folder_path}")
    
    files = os.listdir(folder_path)
    lot_wafer_pairs = set()
    
    for f in files:
        if not f.endswith('.csv'): 
            continue
        parts = f.split('_')
        if len(parts) >= 3 and '#' in parts[2]:
            lot_id = parts[1]
            wafer_part = parts[2]
            wafer_num = wafer_part.split('#')[0]
            if wafer_num:  # 放宽为只要非空即可，支持 P264004960 等非纯数字 WaferID
                lot_wafer_pairs.add((lot_id, wafer_num))
    
    def sort_key(pair):
        lot_id, wafer_num = pair
        # 纯数字 WaferID 按数值排前，含字母的按字符串排后
        if wafer_num.isdigit():
            wafer_sort = (0, int(wafer_num), '')
        else:
            wafer_sort = (1, 0, wafer_num)
        try:
            lot_num = float(lot_id[1:]) if lot_id.startswith('N') else float(lot_id)
            return (lot_num,) + wafer_sort
        except:
            return (lot_id,) + wafer_sort
    
    return sorted(lot_wafer_pairs, key=sort_key)

def extract_columns_from_csv(file_path, selected_columns):
    try:
        header_row = find_header_line(file_path)
        df = pd.read_csv(file_path, skiprows=header_row)
        base_columns = ['X_COORD', 'Y_COORD']
        all_columns = base_columns + selected_columns
        unique_columns = []
        for col in all_columns:
            if col not in unique_columns: unique_columns.append(col)
        
        missing_columns = [col for col in unique_columns if col not in df.columns]
        if missing_columns:
            available_columns = [col for col in unique_columns if col in df.columns]
            if not available_columns:
                print(f"  文件 {os.path.basename(file_path)} 中没有可用的列，跳过。")
                return None
            unique_columns = available_columns
            
        extracted_df = df[unique_columns].copy()
        try:
            extracted_df = extracted_df.map(convert_value_to_numeric)
        except AttributeError:
            extracted_df = extracted_df.applymap(convert_value_to_numeric)
        return extracted_df
    except Exception as e:
        print(f"  处理文件 {os.path.basename(file_path)} 时出错: {e}")
        return None

def process_folder_to_excel_map_combine(folder_path, selected_columns):
    csv_files = [f for f in os.listdir(folder_path) if f.endswith('.csv')]
    if not csv_files:
        print("指定文件夹中没有找到CSV文件")
        return False
    print(f"找到 {len(csv_files)} 个CSV文件")
    
    try:
        lot_wafer_pairs = extract_lot_wafer_pairs_sorted(folder_path)
        print(f"提取到 {len(lot_wafer_pairs)} 个Lot-Wafer组合")
        for lot_id, wafer_num in lot_wafer_pairs:
            print(f"  {lot_id}_W{wafer_num}")
    except Exception as e:
        print(f"提取Lot-Wafer组合失败: {e}")
        return False
    
    output_dir = create_output_folder_in_parent(folder_path, "Map")
    print(f"输出目录: {output_dir}")
    
    if selected_columns:
        first_col = selected_columns[0]
        last_col = selected_columns[-1]
        excel_filename = f"Map_{first_col}_{last_col}.xlsx"
    else:
        excel_filename = "Map_Wafer_Data.xlsx"
        
    excel_filepath = os.path.join(output_dir, excel_filename)
    
    try:
        with pd.ExcelWriter(excel_filepath, engine='openpyxl') as writer:
            processed_count = 0
            for lot_id, wafer_num in lot_wafer_pairs:
                search_pattern = f'_{lot_id}_{wafer_num}#'
                matching_files = [f for f in csv_files if search_pattern in f]
                if not matching_files: 
                    print(f"警告: 未找到匹配文件 for {lot_id}_W{wafer_num}")
                    continue
                
                csv_file = matching_files[0]
                file_path = os.path.join(folder_path, csv_file)
                print(f"正在处理 {lot_id}_W{wafer_num} (文件: {csv_file})...")
                
                df = extract_columns_from_csv(file_path, selected_columns)
                if df is not None:
                    sheet_name = extract_lot_wafer_name(csv_file)
                    if len(sheet_name) > 31: 
                        sheet_name = sheet_name[:31]
                    invalid_chars = [':', '\\', '/', '?', '*', '[', ']']
                    for char in invalid_chars:
                        sheet_name = sheet_name.replace(char, '_')
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    processed_count += 1
                else:
                    print(f"警告: 无法提取 {lot_id}_W{wafer_num} 的数据")
        
        print(f"\nExcel文件已生成: {excel_filepath}")
        print(f"成功处理 {processed_count} 个文件")
        return True
    except Exception as e:
        print(f"生成Excel文件时发生错误: {e}")
        return False

def extract_lot_wafer_name(filename):
    name_without_ext = Path(filename).stem
    if '_' in name_without_ext:
        parts = name_without_ext.split('_')
        if len(parts) >= 3:
            lot_id = parts[1]
            wafer_part = parts[2]
            if '#' in wafer_part:
                wafer_num = wafer_part.split('#')[0]
                if wafer_num:
                    return f"{lot_id}_W{wafer_num}"
        lot_id = parts[1] if len(parts) >= 2 else "Unknown_Lot"
        wafer_name = "Unknown_Wafer"
        if len(parts) >= 3:
            wafer_part = parts[2]
            numbers = re.findall(r'\d+', wafer_part)
            if numbers: 
                wafer_name = f"W{numbers[0]}"
        return f"{lot_id}_{wafer_name}"
    return name_without_ext

def get_wafer_number_from_col(column_name):
    match = re.search(r'W(\d+)', column_name)
    if match: return int(match.group(1))
    return 0

def process_probability_data(folder_path, test_item_names):
    csv_files = [f for f in os.listdir(folder_path) if f.endswith('.csv')]
    if not csv_files:
        print(f"错误：在文件夹 '{folder_path}' 中没有找到CSV文件")
        return False
    print(f"找到 {len(csv_files)} 个CSV文件")
    
    # 与Map功能一致：先按Lot提取，再按Wafer提取
    try:
        lot_wafer_pairs = extract_lot_wafer_pairs_sorted(folder_path)
        print(f"提取到 {len(lot_wafer_pairs)} 个Lot-Wafer组合")
        for lot_id, wafer_num in lot_wafer_pairs:
            print(f"  {lot_id}_W{wafer_num}")
    except Exception as e:
        print(f"提取Lot-Wafer组合失败: {e}")
        return False
    
    output_folder_path = create_output_folder_in_parent(folder_path, "Probability")
    print(f"输出文件夹: {output_folder_path}")
    
    all_extracted_data = {}
    for test_item_name in test_item_names:
        print(f"\n正在处理测试项: {test_item_name}")
        extracted_data = {}
        for lot_id, wafer_num in lot_wafer_pairs:
            search_pattern = f'_{lot_id}_{wafer_num}#'
            matching_files = [f for f in csv_files if search_pattern in f]
            if not matching_files:
                print(f"  警告: 未找到匹配文件 for {lot_id}_W{wafer_num}")
                continue
            
            csv_file_name = matching_files[0]
            csv_file_path = os.path.join(folder_path, csv_file_name)
            try:
                header_row = find_header_line(csv_file_path)
                df = pd.read_csv(csv_file_path, skiprows=header_row)
                if test_item_name not in df.columns and header_row > 0:
                    df = pd.read_csv(csv_file_path)
                if test_item_name not in df.columns: continue
                
                column_name = extract_lot_wafer_name(csv_file_name)
                if column_name in extracted_data:
                    print(f"  警告：列名 '{column_name}' 已存在，将被覆盖")
                extracted_data[column_name] = df[test_item_name].apply(convert_value_to_numeric)
            except Exception as e:
                print(f"  处理文件 {csv_file_name} 时出错: {e}")
                continue
        
        if not extracted_data:
            print(f"警告：测试项 '{test_item_name}' 没有成功提取任何数据")
            continue
        
        def sort_by_lot_wafer(col_name):
            """按 Lot + Wafer 排序：Lot ID 优先，然后 Wafer 编号"""
            parts = col_name.split('_')
            lot_id = parts[0] if len(parts) >= 1 else col_name
            wafer_num = 0
            if len(parts) >= 2:
                wafer_str = parts[1]
                match = re.search(r'(\d+)', wafer_str)
                if match:
                    wafer_num = int(match.group(1))
            # Lot 排序：提取数字部分
            lot_num = 0.0
            try:
                lot_str = lot_id
                if lot_str.startswith('N'):
                    lot_str = lot_str[1:]
                lot_num = float(lot_str)
            except:
                pass
            return (lot_num, wafer_num, lot_id)
        
        sorted_columns = sorted(extracted_data.keys(), key=sort_by_lot_wafer)
        sorted_data = {col: extracted_data[col] for col in sorted_columns}
        result_df = pd.DataFrame(sorted_data)
        all_extracted_data[test_item_name] = result_df
        print(f"测试项 '{test_item_name}' 处理完成，整合了 {len(extracted_data)} 列数据")
    
    if not all_extracted_data:
        print("错误：没有成功提取任何测试项的数据")
        return False
    
    first_item = test_item_names[0]
    last_item = test_item_names[-1]
    output_file_name = f"Probability_{first_item}_{last_item}.xlsx"
    output_file = os.path.join(output_folder_path, output_file_name)
    
    try:
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            for test_item_name, result_df in all_extracted_data.items():
                sheet_name = test_item_name[:31]
                result_df.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"已保存 Sheet: {sheet_name}")
        print(f"\n成功保存Excel文件: {output_file}")
        return True
    except Exception as e:
        print(f"保存Excel文件时出错: {e}")
        return False

# ==========================================
#  GUI 部分 (已模块化改造)
# ==========================================

class PrintLogger:
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, message):
        self.text_widget.after(0, self._append_text, message)

    def _append_text(self, message):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state='disabled')

    def flush(self):
        pass

class DataToolApp:
    def __init__(self, parent_frame):
        # 原来的 self.root 变成了 self.parent
        self.parent = parent_frame
        
        # 变量
        self.folder_path = tk.StringVar()
        self.column_input = tk.StringVar()
        self.mode = tk.StringVar(value="1") 
        
        # 将所有组件放入 parent 容器中
        main_frame = ttk.Frame(self.parent, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_frame, text="Wafer 数据提取工具", font=("Helvetica", 14, "bold")).pack(pady=(0, 15))
        
        mode_frame = ttk.LabelFrame(main_frame, text="选择功能模式", padding="10")
        mode_frame.pack(fill=tk.X, pady=(0, 15))
        ttk.Radiobutton(mode_frame, text="1. Wafer Map 数据提取 (分Sheet保存，含坐标)", 
                        variable=self.mode, value="1", command=self.update_ui_text).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(mode_frame, text="2. 概率分布数据提取 (横向合并，仅测试项)", 
                        variable=self.mode, value="2", command=self.update_ui_text).pack(anchor=tk.W, pady=2)
        
        folder_frame = ttk.LabelFrame(main_frame, text="输入路径", padding="10")
        folder_frame.pack(fill=tk.X, pady=(0, 15))
        folder_entry = ttk.Entry(folder_frame, textvariable=self.folder_path)
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        ttk.Button(folder_frame, text="浏览...", command=self.browse_folder).pack(side=tk.LEFT)
        
        col_frame = ttk.LabelFrame(main_frame, text="输入列名/测试项", padding="10")
        col_frame.pack(fill=tk.X, pady=(0, 15))
        self.col_label = ttk.Label(col_frame, text="列名 (多个用逗号分隔，连续用 '-' 连接):", foreground="gray")
        self.col_label.pack(anchor=tk.W, pady=(0, 5))
        ttk.Entry(col_frame, textvariable=self.column_input).pack(fill=tk.X)
        self.hint_label = ttk.Label(col_frame, text="示例: OS_VCC, OS_EQ1 (自动提取X/Y坐标)", font=("Helvetica", 8), foreground="gray")
        self.hint_label.pack(anchor=tk.W, pady=(5, 0))
        
        self.run_btn = ttk.Button(main_frame, text="开始提取处理", command=self.start_processing)
        self.run_btn.pack(fill=tk.X, pady=(0, 15))
        
        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', height=10, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 保存原始 stdout，但不在此处全局重定向（避免覆盖其他模块的输出）
        self.logger = PrintLogger(self.log_text)
        self.original_stdout = sys.stdout
        
        self.update_ui_text()

    def update_ui_text(self):
        if self.mode.get() == "1":
            self.col_label.config(text="额外提取列名 (已自动包含 X_COORD, Y_COORD):")
            self.hint_label.config(text="示例: SOFT_BIN, HARD_BIN 或 OS_VCC-OS_EQ1")
        else:
            self.col_label.config(text="提取测试项名称:")
            self.hint_label.config(text="示例: Test1, Test2 或 TestA-TestD (必需输入)")

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_path.set(path)

    def start_processing(self):
        folder = self.folder_path.get().strip()
        col_str = self.column_input.get().strip()
        
        if not folder:
            messagebox.showerror("错误", "请先选择文件夹路径！")
            return
        if not os.path.exists(folder):
            messagebox.showerror("错误", "文件夹路径不存在！")
            return
        if self.mode.get() == "2" and not col_str:
            messagebox.showerror("错误", "概率分布模式必须输入测试项名称！")
            return

        self.run_btn.config(state='disabled')
        self.log_text.configure(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state='disabled')
        
        threading.Thread(target=self.run_logic, args=(folder, col_str), daemon=True).start()

    def run_logic(self, folder, col_str):
        # 临时重定向 stdout 到本模块的日志框（避免跨模块输出污染）
        original_stdout = sys.stdout
        sys.stdout = self.logger
        try:
            print("正在分析文件结构...")
            all_headers = get_first_csv_headers(folder)
            selected_cols = resolve_column_selection(col_str, all_headers)
            
            if self.mode.get() == "1":
                if not selected_cols and col_str:
                    print("警告: 未能解析出有效列名，将仅处理基础数据。")
                print(f"开始 Wafer Map 提取模式...")
                success = process_folder_to_excel_map_combine(folder, selected_cols)
            else:
                if not selected_cols:
                    print("错误: 未能解析出有效的测试项名称，无法继续。")
                    success = False
                else:
                    print(f"开始概率分布数据提取模式...")
                    success = process_probability_data(folder, selected_cols)
            
            if success:
                messagebox.showinfo("完成", "数据处理成功！")
            else:
                messagebox.showwarning("未完成", "处理中出现错误，请查看日志。")
                
        except Exception as e:
            print(f"\n发生未捕获的异常: {e}")
            messagebox.showerror("异常", f"程序发生错误: {e}")
        finally:
            # 恢复原始 stdout（避免污染其他模块的日志）
            sys.stdout = original_stdout
            self.parent.after(0, lambda: self.run_btn.config(state='normal'))

# ==========================================
# 统一入口与独立测试
# ==========================================
def create_ui(parent):
    """供 main.py 调用的接口"""
    return DataToolApp(parent)

if __name__ == "__main__":
    # 独立运行时的测试窗口
    root = tk.Tk()
    root.title("[独立运行] - BIN数据提取")
    root.geometry("700x600")
    app = DataToolApp(root)
    root.mainloop()