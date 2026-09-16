# 文件名: probability.py
import sys
import os
import threading
import queue
import pandas as pd
import numpy as np
from pathlib import Path
from PIL import Image
import warnings
import scipy.stats as stats

# 设置 Matplotlib 后端为 Agg (防卡死、防内存泄漏)
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

# GUI Imports
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

warnings.filterwarnings('ignore')

# 设置中文字体
def setup_chinese_font():
    import platform
    system = platform.system()
    if system == 'Windows':
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
    elif system == 'Darwin':
        plt.rcParams['font.sans-serif'] = ['Arial Unicode MS']
    else:
        plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
setup_chinese_font()

class ProbabilityLogic:
    """后端逻辑类"""
    def __init__(self, log_callback=None):
        self.log_callback = log_callback

    def log(self, message):
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def forward(self, x):
        x_safe = np.array(x)
        epsilon = 1e-7
        x_safe = np.clip(x_safe, epsilon, 1 - epsilon)
        return stats.norm.ppf(x_safe)

    def inverse(self, y):
        return stats.norm.cdf(y)

    def create_overlay_probability_plot(self, df, sheet_name, output_path=None, x_range=None, lsl=None, usl=None, unit="", highlight_columns=None, marker_size=5):
        try:
            if df.empty: return False
            df = df.dropna(axis=1, how='all')
            if df.shape[1] == 0: return False

            fig, ax = plt.subplots(figsize=(10, 8))
            
            highlight_list = []
            if highlight_columns is not None:
                if isinstance(highlight_columns, str):
                    import re
                    highlight_list = [col.strip() for col in re.split(r'[,;\s]+', highlight_columns) if col.strip()]
                elif isinstance(highlight_columns, list):
                    highlight_list = [str(col).strip() for col in highlight_columns if str(col).strip()]
            
            color_list = list(plt.cm.tab10.colors)
            marker_list = ['o', 's', '^', 'v', 'D', 'X', 'p', '*', 'h', '+']
            n_colors = len(color_list)
            
            valid_columns_count = 0
            all_data_max_p = 0
            all_data_min_p = 1
            
            for column_name in sorted(df.columns, key=str):
                try:
                    data = df[column_name].dropna()
                    n = len(data)
                    if n < 1: continue
                    
                    valid_columns_count += 1
                    sorted_data = np.sort(data.values)
                    cdf = (np.arange(1, n + 1) - 0.3) / (n + 0.4)
                    
                    marker_idx = (valid_columns_count - 1) // n_colors
                    color_idx = (valid_columns_count - 1) % n_colors
                    m = marker_list[marker_idx % len(marker_list)]
                    c = color_list[color_idx]
                    
                    # 核心Bug修复：如果 highlight_columns 为 None，说明未开启该功能，全部正常高亮显示
                    if highlight_columns is None:
                        is_highlighted = True
                    else:
                        is_highlighted = str(column_name).strip() in highlight_list
                    
                    if is_highlighted:
                        ax.plot(sorted_data, cdf, marker=m, color=c, alpha=1.0, 
                                linewidth=3.0, markersize=marker_size, label=str(column_name), linestyle='-', zorder=10)
                    else:
                        ax.plot(sorted_data, cdf, marker=m, color='#808080', alpha=0.6, 
                                linewidth=1.0, markersize=marker_size, label=str(column_name), linestyle='-', zorder=1)
                    
                    all_data_max_p = max(all_data_max_p, cdf.max())
                    all_data_min_p = min(all_data_min_p, cdf.min())
                except Exception as e:
                    self.log(f"  处理列 '{column_name}' 时出错: {str(e)}")
                    continue
            
            if valid_columns_count == 0:
                plt.close(); return False
            
            ax.set_yscale('function', functions=(self.forward, self.inverse))
            major_ticks = [0.001, 0.01, 0.05, 0.10, 0.20, 0.50, 0.80, 0.90, 0.95, 0.99, 0.999]
            if all_data_max_p > 0.999: major_ticks.append(0.9999)
            if all_data_min_p < 0.001: major_ticks.insert(0, 0.0001)
            ax.set_yticks(major_ticks)
            
            def prob_formatter(x, pos):
                if x < 0.01: return f"{x*100:.2f}%"
                if x < 0.1:  return f"{x*100:.1f}%"
                if x > 0.99: return f"{x*100:.2f}%"
                return f"{x*100:.0f}%"
            ax.yaxis.set_major_formatter(mtick.FuncFormatter(prob_formatter))
            
            y_bottom_limit = min(0.001, all_data_min_p * 0.5) 
            y_top_limit = max(0.999, 1 - (1 - all_data_max_p) * 0.5)
            ax.set_ylim(bottom=max(1e-6, y_bottom_limit), top=min(1 - 1e-6, y_top_limit))

            ax.grid(True, which='major', linestyle='-', alpha=0.5, color='gray')
            ax.grid(True, axis='x', linestyle=':', alpha=0.3)

            trans = ax.get_xaxis_transform()
            y_text_pos = 1.02 
            spec_font = {'color': 'red', 'fontweight': 'bold', 'fontsize': 14}

            if lsl is not None and not pd.isna(lsl):
                ax.axvline(x=lsl, color='red', linestyle='--', linewidth=2, alpha=0.8)
                ax.text(lsl, y_text_pos, f'LSL {lsl} ', ha='right', va='bottom', transform=trans, **spec_font)
            if usl is not None and not pd.isna(usl):
                ax.axvline(x=usl, color='red', linestyle='--', linewidth=2, alpha=0.8)
                ax.text(usl, y_text_pos, f' USL {usl}', ha='left', va='bottom', transform=trans, **spec_font)

            unit_str = f" ({unit})" if unit else ""
            ax.set_xlabel(f'{sheet_name}{unit_str}', fontsize=12, fontweight='bold')
            ax.set_title(f'Probability Plot - {sheet_name}{unit_str}', fontsize=16, fontweight='bold', pad=35)
            ax.set_ylabel('Cumulative Probability', fontsize=12, fontweight='bold')
            
            ncol = 2 if len(df.columns) > 10 else 1
            ax.legend(bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=10, ncol=ncol)
            
            if x_range: ax.set_xlim(x_range[0], x_range[1])
            plt.tight_layout()
            
            if output_path: plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close()
            return True
        except Exception as e:
            self.log(f"  生成图表时出错: {str(e)}")
            plt.close()
            return False

    def stitch_images_2x2(self, image_list, output_folder, file_stem, prefix_name, strict=False):
        if not image_list: return
        self.log(f"正在执行图片拼接 (2x2)...")
        chunks = [image_list[i:i + 4] for i in range(0, len(image_list), 4)]
        
        for i, chunk in enumerate(chunks):
            try:
                images = [Image.open(p) for p in chunk]
                base_w, base_h = images[0].size
                resized_images = [img.resize((base_w, base_h), Image.Resampling.LANCZOS) if img.size != (base_w, base_h) else img for img in images]
                
                new_im = Image.new('RGB', (base_w * 2, base_h * 2), 'white')
                positions = [(0, 0), (base_w, 0), (0, base_h), (base_w, base_h)]
                for idx, img in enumerate(resized_images): new_im.paste(img, positions[idx])
                
                save_name = f"{prefix_name}_{file_stem}_Combined_Page{i+1}.png"
                new_im.save(output_folder / save_name, quality=95)
                self.log(f"  -> 生成拼接图: {save_name}")
            except Exception as e:
                if strict:
                    raise
                self.log(f"  拼接第 {i+1} 页时失败: {e}")

    def process_file(self, file_path, settings, highlight_columns=None, marker_size=5, selected_sheets=None):
        self.log(f"\n正在读取文件: {Path(file_path).name} ...")
        try:
            xls = pd.ExcelFile(file_path)
            all_sheets = xls.sheet_names
            
            # 过滤选中的测试项
            if selected_sheets:
                sheets_to_process = [s for s in all_sheets if s in selected_sheets]
                skipped = len(all_sheets) - len(sheets_to_process)
                self.log(f"检测到 {len(all_sheets)} 个测试项，将处理用户选择的 {len(sheets_to_process)} 个 (跳过 {skipped} 个)")
            else:
                sheets_to_process = all_sheets
                self.log(f"检测到 {len(all_sheets)} 个测试项 (Sheet)")
            
            success_count = 0
            generated_images = [] 
            
            prefix_mode = {"auto": "Auto", "spec": "Spec", "manual": "Manual"}.get(settings['mode'], "")
            
            for sheet in sheets_to_process:
                self.log(f"-> 正在处理: {sheet}")
                df = pd.read_excel(xls, sheet_name=sheet, header=0)
                if len(df) < 5:
                    self.log(f"  警告: 数据行数不足，跳过")
                    continue
                
                lsl_val, usl_val, unit_val = None, None, ""
                try:
                    valid_unit = df.iloc[0].dropna()
                    if not valid_unit.empty and pd.notna(valid_unit.iloc[0]): unit_val = str(valid_unit.iloc[0]).strip()
                    valid_lsl = df.iloc[1].dropna()
                    if not valid_lsl.empty: lsl_val = pd.to_numeric(valid_lsl.iloc[0], errors='coerce')
                    valid_usl = df.iloc[2].dropna()
                    if not valid_usl.empty: usl_val = pd.to_numeric(valid_usl.iloc[0], errors='coerce')
                except Exception as e:
                    self.log(f"  提取Spec/Unit失败: {e}")

                current_x_range = None
                if settings['mode'] == 'manual':
                    current_x_range = settings['range']
                elif settings['mode'] == 'spec':
                    if lsl_val is not None and usl_val is not None and usl_val > lsl_val:
                        spec_width = usl_val - lsl_val
                        total_width = spec_width / 0.9 
                        current_x_range = [lsl_val - (total_width * 0.05), usl_val + (total_width * 0.05)]
                        self.log(f"  [X轴] 规格锁定: {current_x_range[0]:.2f} ~ {current_x_range[1]:.2f}")
                    else:
                        self.log(f"  [X轴] 无规格，自动回退")

                clean_df = df.iloc[4:].copy().apply(pd.to_numeric, errors='coerce')
                safe_sheet_name = "".join([c if c.isalnum() or c in (' ', '_', '-') else '_' for c in sheet])
                output_file = Path(file_path).parent / f"{prefix_mode}_{Path(file_path).stem}_{safe_sheet_name}_prob.png"
                
                if self.create_overlay_probability_plot(clean_df, sheet, output_file, current_x_range, lsl=lsl_val, usl=usl_val, unit=unit_val, highlight_columns=highlight_columns, marker_size=marker_size):
                    self.log(f"  ✓ 已保存: {output_file.name}")
                    success_count += 1
                    generated_images.append(output_file)
            
            if generated_images:
                self.stitch_images_2x2(generated_images, Path(file_path).parent, Path(file_path).stem, prefix_mode)
            return success_count
        except Exception as e:
            self.log(f"处理文件失败: {e}")
            return 0

# ==========================================
# GUI 部分 (模块化适配版)
# ==========================================
class ProbabilityApp:
    def __init__(self, parent_frame):
        self.parent = parent_frame
        
        self.log_queue = queue.Queue()
        self.folder_path = tk.StringVar()
        self.excel_files = []
        
        self.x_mode = tk.StringVar(value="auto")
        self.manual_range = tk.StringVar()
        
        self.use_highlight_var = tk.BooleanVar(value=False)
        self.marker_size_var = tk.IntVar(value=5)
        
        self.setup_ui()
        self.check_log_queue()
        
    def setup_ui(self):
        # 挂载到 self.parent
        frame_top = ttk.LabelFrame(self.parent, text="第一步：文件选择", padding=10)
        frame_top.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(frame_top, text="Excel文件夹路径:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame_top, textvariable=self.folder_path, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(frame_top, text="浏览...", command=self.browse_folder).grid(row=0, column=2)
        
        ttk.Label(frame_top, text="文件列表 (单选):").grid(row=1, column=0, sticky="nw", pady=5)
        self.listbox_files = tk.Listbox(frame_top, height=5, exportselection=False)
        self.listbox_files.grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)
        self.listbox_files.bind('<<ListboxSelect>>', self.on_file_select)
        
        # --- 测试项选择 ---
        ttk.Label(frame_top, text="测试项选择 (多选，留空则全部画图):").grid(row=2, column=0, sticky="nw", pady=(10,0))
        f_sheets = ttk.Frame(frame_top)
        f_sheets.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(10,0))
        scroll_s = ttk.Scrollbar(f_sheets)
        scroll_s.pack(side="right", fill="y")
        self.listbox_sheets = tk.Listbox(f_sheets, selectmode='multiple', height=5, yscrollcommand=scroll_s.set, exportselection=False)
        self.listbox_sheets.pack(side="left", fill="x", expand=True)
        scroll_s.config(command=self.listbox_sheets.yview)
        ttk.Label(frame_top, text="(点击上方文件列表加载测试项)").grid(row=3, column=1, columnspan=2, sticky="w", pady=(0,5))
        
        frame_settings = ttk.LabelFrame(self.parent, text="第二步：参数设置", padding=10)
        frame_settings.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(frame_settings, text="X轴范围模式:").grid(row=0, column=0, sticky="w")
        f_modes = ttk.Frame(frame_settings)
        f_modes.grid(row=0, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(f_modes, text="1. 自动范围", variable=self.x_mode, value="auto", command=self.update_ui_state).pack(side="left", padx=5)
        ttk.Radiobutton(f_modes, text="2. 规格锁定", variable=self.x_mode, value="spec", command=self.update_ui_state).pack(side="left", padx=5)
        ttk.Radiobutton(f_modes, text="3. 手动输入", variable=self.x_mode, value="manual", command=self.update_ui_state).pack(side="left", padx=5)
        
        ttk.Label(frame_settings, text="数据点大小 (1-15):").grid(row=1, column=0, sticky="w", pady=5)
        self.spin_marker = ttk.Spinbox(frame_settings, from_=1, to=15, textvariable=self.marker_size_var, width=5)
        self.spin_marker.grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(frame_settings, text="默认5").grid(row=1, column=2, sticky="w")
        
        ttk.Label(frame_settings, text="手动范围 (min,max):").grid(row=2, column=0, sticky="w", pady=5)
        self.entry_manual = ttk.Entry(frame_settings, textvariable=self.manual_range, width=20, state='disabled')
        self.entry_manual.grid(row=2, column=1, sticky="w", padx=5)
        ttk.Label(frame_settings, text="(仅模式3有效)").grid(row=2, column=2, sticky="w")
        
        # --- 新增：智能列名选择 UI ---
        ttk.Checkbutton(frame_settings, text="启用部分数据高亮 (不勾选则所有线彩色)", 
                        variable=self.use_highlight_var, command=self.update_ui_state).grid(row=3, column=0, sticky="nw", pady=10)
        
        f_cols = ttk.Frame(frame_settings)
        f_cols.grid(row=3, column=1, columnspan=2, sticky="w", pady=10)
        
        self.btn_load_cols = ttk.Button(f_cols, text="从选中文件提取列名 ->", command=self.load_columns_from_file, state='disabled')
        self.btn_load_cols.pack(side="left", padx=5)
        
        scroll_c = ttk.Scrollbar(f_cols)
        scroll_c.pack(side="right", fill="y")
        self.listbox_cols = tk.Listbox(f_cols, selectmode='multiple', height=4, yscrollcommand=scroll_c.set, state='disabled', exportselection=False)
        self.listbox_cols.pack(side="left", fill="x", expand=True)
        scroll_c.config(command=self.listbox_cols.yview)
        
        # 按钮区域
        frame_btns = ttk.Frame(self.parent, padding=5)
        frame_btns.pack(fill="x", padx=10)
        self.btn_run_single = ttk.Button(frame_btns, text="处理选中文件", command=lambda: self.start_processing(batch=False))
        self.btn_run_single.pack(side="left", padx=5, expand=True, fill="x")
        self.btn_run_batch = ttk.Button(frame_btns, text="批量处理所有文件", command=lambda: self.start_processing(batch=True))
        self.btn_run_batch.pack(side="left", padx=5, expand=True, fill="x")

        frame_log = ttk.LabelFrame(self.parent, text="运行日志", padding=10)
        frame_log.pack(fill="both", expand=True, padx=10, pady=5)
        self.text_log = ScrolledText(frame_log, height=8, state='disabled')
        self.text_log.pack(fill="both", expand=True)

    def log_to_queue(self, msg):
        self.log_queue.put(msg)

    def check_log_queue(self):
        while not self.log_queue.empty():
            try:
                msg = self.log_queue.get_nowait()
                self.text_log.config(state='normal')
                self.text_log.insert(tk.END, msg + "\n")
                self.text_log.see(tk.END)
                self.text_log.config(state='disabled')
            except queue.Empty:
                break
        self.parent.after(100, self.check_log_queue)

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_path.set(path)
            self.refresh_file_list(path)

    def refresh_file_list(self, folder):
        self.listbox_files.delete(0, tk.END)
        self.excel_files = []
        files = [f for f in Path(folder).glob("*") if f.suffix.lower() in ('.xlsx', '.xls') and not f.name.startswith('~$')]
        for f in files:
            self.listbox_files.insert(tk.END, f.name)
            self.excel_files.append(f)
        self.log_to_queue(f"找到 {len(files)} 个Excel文件")

    def load_columns_from_file(self):
        """读取选中的Excel文件的第一个sheet的列名"""
        sel = self.listbox_files.curselection()
        if not sel:
            return messagebox.showwarning("提示", "请先在上方文件列表中选中一个文件！")
            
        file_path = self.excel_files[sel[0]]
        self.log_to_queue(f"正在读取 {file_path.name} 的列名...")
        
        try:
            # 仅读取表头以加快速度
            df = pd.read_excel(file_path, sheet_name=0, nrows=0)
            cols = df.columns.tolist()
            
            self.listbox_cols.config(state='normal')
            self.listbox_cols.delete(0, tk.END)
            for c in cols:
                self.listbox_cols.insert(tk.END, str(c))
                
            self.log_to_queue("列名加载成功，请在列表中使用 Ctrl 或 Shift 多选需要高亮的列。")
        except Exception as e:
            messagebox.showerror("读取错误", f"无法读取列名：{e}")

    def on_file_select(self, event):
        """选中文件时自动加载该文件的所有sheet名称"""
        sel = self.listbox_files.curselection()
        if not sel:
            return
        file_path = self.excel_files[sel[0]]
        try:
            xls = pd.ExcelFile(file_path)
            sheets = xls.sheet_names
            self.listbox_sheets.delete(0, tk.END)
            for s in sheets:
                self.listbox_sheets.insert(tk.END, s)
            self.log_to_queue(f"加载 {file_path.name} 的 {len(sheets)} 个测试项")
        except Exception as e:
            self.log_to_queue(f"读取测试项失败: {e}")

    def update_ui_state(self):
        self.entry_manual.config(state='normal' if self.x_mode.get() == "manual" else 'disabled')
        if self.use_highlight_var.get():
            self.btn_load_cols.config(state='normal')
            self.listbox_cols.config(state='normal')
        else:
            self.btn_load_cols.config(state='disabled')
            self.listbox_cols.config(state='disabled')

    def start_processing(self, batch=False):
        if not self.folder_path.get(): return messagebox.showwarning("提示", "请先选择文件夹")
        target_files = []
        if batch:
            if not self.excel_files: return messagebox.showwarning("提示", "当前文件夹没有Excel文件")
            target_files = self.excel_files
        else:
            sel = self.listbox_files.curselection()
            if not sel: return messagebox.showwarning("提示", "请在列表中选择一个文件，或使用批量处理")
            target_files = [self.excel_files[sel[0]]]

        settings = {'mode': self.x_mode.get(), 'range': None}
        if settings['mode'] == 'manual':
            try:
                raw = self.manual_range.get().strip().replace('，', ',')
                parts = raw.split(',') if ',' in raw else raw.split()
                settings['range'] = [float(parts[0]), float(parts[1])]
            except:
                return messagebox.showerror("错误", "手动范围格式错误，请使用 min,max 格式")

        # 获取选中的高亮列名
        highlight_cols = None
        if self.use_highlight_var.get():
            sel_idxs = self.listbox_cols.curselection()
            if not sel_idxs:
                return messagebox.showwarning("提示", "您开启了部分高亮功能，但尚未在列表中选择任何列名！")
            highlight_cols = [self.listbox_cols.get(i) for i in sel_idxs]
        
        # 获取选中的测试项 (sheet)，为空则全部处理
        selected_sheets = None
        sel_sheet_idxs = self.listbox_sheets.curselection()
        if sel_sheet_idxs:
            selected_sheets = [self.listbox_sheets.get(i) for i in sel_sheet_idxs]
        
        self.btn_run_single.config(state='disabled')
        self.btn_run_batch.config(state='disabled')
        self.log_to_queue("-" * 40)
        marker_size = self.marker_size_var.get()
        sheet_info = f" - 测试项: {len(selected_sheets)}个" if selected_sheets else " - 测试项: 全部"
        self.log_to_queue(f"开始任务: {'批量' if batch else '单文件'} - 模式: {settings['mode']} - 数据点大小: {marker_size}{sheet_info}")
        
        threading.Thread(target=self.run_worker, args=(target_files, settings, highlight_cols, marker_size, selected_sheets), daemon=True).start()

    def run_worker(self, files, settings, highlight_columns=None, marker_size=5, selected_sheets=None):
        logic = ProbabilityLogic(log_callback=self.log_to_queue)
        total = sum([logic.process_file(f, settings, highlight_columns, marker_size, selected_sheets) for f in files])
        self.log_to_queue("-" * 40)
        self.log_to_queue(f"全部完成! 共生成 {total} 张图表")
        self.parent.after(0, self.task_finished)

    def task_finished(self):
        self.btn_run_single.config(state='normal')
        self.btn_run_batch.config(state='normal')
        messagebox.showinfo("完成", "处理完成！")

# ==========================================
# 统一入口与独立测试
# ==========================================
def create_excel_ui(parent):
    """Optional legacy Excel entry point; direct plotting no longer requires merge."""
    return ProbabilityApp(parent)


def create_ui(parent, service=None, root_var=None):
    from direct_plot_ui import DirectPlotApp
    return DirectPlotApp(parent, "probability", service, root_var)

if __name__ == "__main__":
    root = tk.Tk()
    root.title("[独立运行] - 概率分布叠加图")
    root.geometry("1200x850")
    root.minsize(1000, 650)
    app = create_ui(root)
    root.mainloop()
