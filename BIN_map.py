# 文件名: BIN_map.py
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端，避免与Tkinter冲突
import matplotlib.pyplot as plt
import os
import warnings
import numpy as np
from matplotlib.colors import ListedColormap, BoundaryNorm
import matplotlib as mpl
from PIL import Image
import shutil
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

# 抑制警告
warnings.filterwarnings('ignore')

# 设置中文字体支持
def setup_chinese_font():
    import platform
    system = platform.system()
    if system == 'Windows':
        matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei']
    elif system == 'Darwin':  
        matplotlib.rcParams['font.sans-serif'] = ['Arial Unicode MS']
    else:  
        matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']
    matplotlib.rcParams['axes.unicode_minus'] = False  

setup_chinese_font()

STANDARD_GREEN = '#00FF00'  
STANDARD_RED = '#FF0000'    
LIGHT_GRAY = '#D3D3D3'      

# ==========================================
#  核心逻辑 (保持不变)
# ==========================================

def rotate_coordinates(df, notch_direction):
    df_rotated = df.copy()
    if notch_direction == 6:  
        return df_rotated
    elif notch_direction == 9:  
        df_rotated['X_COORD'] = -df['Y_COORD']
        df_rotated['Y_COORD'] = df['X_COORD']
    elif notch_direction == 12:  
        df_rotated['X_COORD'] = -df['X_COORD']
        df_rotated['Y_COORD'] = -df['Y_COORD']
    elif notch_direction == 3:  
        df_rotated['X_COORD'] = df['Y_COORD']
        df_rotated['Y_COORD'] = -df['X_COORD']
    return df_rotated

def get_color_settings(mode, specific_bin, unique_bins=None):
    settings = {}
    if mode == 1:  
        colors = [STANDARD_RED, STANDARD_GREEN]
        settings['cmap'] = ListedColormap(colors)
        settings['norm'] = BoundaryNorm([-0.5, 0.5, 1.5], 2)
        settings['value_mapper'] = lambda x: np.where(x == 1, 1, 0)
        settings['legend_info'] = None

    elif mode == 2:  
        colors = [LIGHT_GRAY, STANDARD_RED]
        settings['cmap'] = ListedColormap(colors)
        settings['norm'] = BoundaryNorm([-0.5, 0.5, 1.5], 2)
        targets = specific_bin if isinstance(specific_bin, list) else [specific_bin]
        settings['value_mapper'] = lambda x: np.where(np.isin(x, targets), 1, 0)
        settings['legend_info'] = None

    elif mode == 0:  
        if unique_bins is None:
            raise ValueError("Mode 0 requires unique_bins list")
        sorted_others = sorted([b for b in unique_bins if b != 1])
        colors = [STANDARD_GREEN]
        if sorted_others:
            palette_colors = []
            for name in ['tab20', 'tab20b', 'tab20c']:
                try:
                    cmap = mpl.colormaps[name]
                except AttributeError:
                    cmap = plt.cm.get_cmap(name)
                palette_colors.extend(cmap.colors)
            for i in range(len(sorted_others)):
                colors.append(palette_colors[i % len(palette_colors)])
        
        settings['cmap'] = ListedColormap(colors)
        settings['norm'] = BoundaryNorm(np.arange(-0.5, len(colors) + 0.5, 1), len(colors))
        
        bin_to_idx = {1: 0}
        for i, b in enumerate(sorted_others):
            bin_to_idx[b] = i + 1
            
        def map_values(x):
            flat_x = x.ravel()
            flat_y = np.zeros_like(flat_x, dtype=int) 
            for b_val, idx in bin_to_idx.items():
                if b_val != 1: 
                    flat_y[flat_x == b_val] = idx
            return flat_y.reshape(x.shape)

        settings['value_mapper'] = map_values
        legend_info = {b: colors[idx] for b, idx in bin_to_idx.items()}
        settings['legend_info'] = legend_info

    return settings

def _add_custom_legend(ax, df, legend_info):
    legend_elements = []
    total_count = len(df)
    bin_counts = df['SOFT_BIN'].value_counts()
    
    rates = []
    for bin_val, color in legend_info.items():
        count = bin_counts.get(bin_val, 0)
        rate = (count / total_count) * 100
        if rate >= 0.1: 
            rates.append((bin_val, rate, color))
            
    rates.sort(key=lambda x: x[1], reverse=True)
    
    for bin_val, rate, color in rates: 
        legend_elements.append(plt.Line2D([0], [0], marker='s', color='w', 
                                        markerfacecolor=color, markersize=8,
                                        label=f'BIN {int(bin_val)} ({rate:.2f}%)'))
    
    if legend_elements:
        ncols = 1 if len(legend_elements) <= 20 else 2
        ax.legend(handles=legend_elements, loc='center left', 
                 bbox_to_anchor=(1.02, 0.5), frameon=True, 
                 fontsize=8, ncol=ncols)

def create_wafer_map_pcolormesh(df, ax=None, wafer_number=None, notch_direction=6, mode=1, specific_bin=None, small_font=False, global_settings=None, add_legend=True):
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 8))
    try:
        df_rotated = rotate_coordinates(df, notch_direction)
        if global_settings:
            settings = global_settings
        else:
            unique_bins = df_rotated['SOFT_BIN'].unique()
            settings = get_color_settings(mode, specific_bin, unique_bins)
            
        df_dedup = df_rotated.drop_duplicates(subset=['X_COORD', 'Y_COORD'], keep='last')
        pivot_data = df_dedup.pivot(index='Y_COORD', columns='X_COORD', values='SOFT_BIN')
        
        all_x = sorted(df_rotated['X_COORD'].unique())
        all_y = sorted(df_rotated['Y_COORD'].unique())
        pivot_data = pivot_data.reindex(index=all_y, columns=all_x)
        
        raw_values = pivot_data.to_numpy()
        mapped_values = settings['value_mapper'](raw_values).astype(float)
        mapped_values[np.isnan(raw_values)] = np.nan

        X, Y = np.meshgrid(all_x, all_y)
        ax.pcolormesh(X, -Y, mapped_values, cmap=settings['cmap'], norm=settings['norm'], edgecolors='none', shading='auto')
        
        if len(all_x) > 1 and len(all_y) > 1:
            x_range, y_range = all_x[-1] - all_x[0], all_y[-1] - all_y[0]
            ax.set_aspect(x_range / y_range if x_range > 0 and y_range > 0 else 'equal')
        else:
            ax.set_aspect('equal')
            
        ax.set_xticks([]); ax.set_yticks([]); ax.set_frame_on(False)
        
        if wafer_number is not None:
            fig_width_inches = ax.figure.get_figwidth()
            base_fs = max(4, fig_width_inches * 1.5) if small_font else max(12, min(18, int(fig_width_inches * 0.8)))
            stats_fs = max(3, base_fs - 1) if small_font else max(10, base_fs - 2)

            if isinstance(wafer_number, str) and '_' in wafer_number:
                title_text = str(wafer_number)
            else:
                title_text = f'W{wafer_number}'

            if mode == 2 and not isinstance(specific_bin, list) and specific_bin:
                title_text += f'_BIN{specific_bin}'
            
            ax.text(0.02, 0.98, title_text, transform=ax.transAxes, fontsize=base_fs, fontweight='bold', ha='left', va='top')
            
            total_points = len(df_rotated)
            if mode == 2:
                targets = specific_bin if isinstance(specific_bin, list) else [specific_bin]
                count = df_rotated['SOFT_BIN'].isin(targets).sum()
            else:
                count = (df_rotated['SOFT_BIN'] == 1).sum()
                
            ratio = count / total_points * 100 if total_points > 0 else 0
            ax.text(0.02, 0.90, f'{ratio:.2f}%', transform=ax.transAxes, fontsize=stats_fs, ha='left', va='top')
        
        if add_legend and mode == 0 and not small_font and settings['legend_info']:
            _add_custom_legend(ax, df_rotated, settings['legend_info'])
            
        return ax
    except Exception as e:
        print(f"绘图出错: {e}")
        return ax

def calculate_lot_statistics(wafer_data, mode, specific_bin=None):
    total_points = target_points = 0
    for df in wafer_data.values():
        total_points += len(df)
        if mode == 2:
            targets = specific_bin if isinstance(specific_bin, list) else [specific_bin]
            target_points += df['SOFT_BIN'].isin(targets).sum()
        else: 
            target_points += (df['SOFT_BIN'] == 1).sum()
    return (target_points / total_points * 100) if total_points > 0 else 0

def create_composite_map_pcolormesh(wafer_data, folder_path, excel_file, notch_direction=6, mode=1, specific_bin=None, temp_folder=None, sort_method="alphabetical", manual_order=""):
    lot_stat = calculate_lot_statistics(wafer_data, mode, specific_bin)
    excel_base = os.path.splitext(excel_file)[0]
    suffix = get_mode_suffix(mode, specific_bin)
    sorted_wafer_ids = get_sorted_wafer_ids(wafer_data, sort_method, manual_order)
    num_wafers = len(sorted_wafer_ids)
    
    if num_wafers == 0: return
    
    target_dpi = 300
    first_img_path = None
    for wafer_id in sorted_wafer_ids:
        fpath = os.path.join(temp_folder, f"{excel_base}_W{wafer_id}_temp.png")
        if os.path.exists(fpath):
            first_img_path = fpath
            break
            
    if not first_img_path: return

    with Image.open(first_img_path) as ref_img:
        img_w, img_h = ref_img.size
    
    ph_path = os.path.join(temp_folder, "temp_placeholder.png")
    fig_ph, ax_ph = plt.subplots(figsize=(img_w/target_dpi, img_h/target_dpi))
    ax_ph.axis('off')
    ax_ph.text(0.5, 0.5, "None", transform=ax_ph.transAxes, ha='center', va='center', fontsize=20, color='gray')
    fig_ph.savefig(ph_path, dpi=target_dpi)
    plt.close(fig_ph)
    
    placeholder_img = Image.open(ph_path).resize((img_w, img_h))
    
    cols = num_wafers if num_wafers <= 4 else int(np.ceil(np.sqrt(num_wafers)))
    rows = 1 if num_wafers <= 4 else int(np.ceil(num_wafers / cols))
    
    total_w = img_w * cols
    header_h_inches = max(1.5, (total_w / target_dpi) * 0.03) 
    
    title_text = f"BIN_{','.join(map(str, specific_bin))} Loss: {lot_stat:.2f}%" if mode == 2 and isinstance(specific_bin, list) else f"BIN_{specific_bin} Loss: {lot_stat:.2f}%" if mode == 2 else f"Yield: {lot_stat:.2f}%"
        
    fig_header = plt.figure(figsize=(total_w/target_dpi, header_h_inches))
    text_ax = fig_header.add_axes([0, 0, 1, 1]); text_ax.axis('off')
    text_ax.text(0.5, 0.5, title_text, ha='center', va='center', fontsize=header_h_inches*30, fontweight='bold')
    
    header_path = os.path.join(temp_folder, "temp_header.png")
    fig_header.savefig(header_path, dpi=target_dpi, bbox_inches='tight', pad_inches=0.1)
    plt.close(fig_header)
    
    header_img = Image.open(header_path)
    header_img = header_img.resize((total_w, int(header_img.height * (total_w / header_img.width))))
    header_h = header_img.height
    
    composite_img = Image.new('RGB', (total_w, img_h * rows + header_h), 'white')
    composite_img.paste(header_img, (0, 0))
    
    for i, wafer_id in enumerate(sorted_wafer_ids):
        fpath = os.path.join(temp_folder, f"{excel_base}_W{wafer_id}_temp.png")
        img = Image.open(fpath).resize((img_w, img_h)) if os.path.exists(fpath) else placeholder_img
        composite_img.paste(img, ((i % cols) * img_w, (i // cols) * img_h + header_h))

    layout_info = f"{rows}x{cols}"
    final_name = f"{excel_base}_notch{notch_direction}{suffix}_composite_{layout_info}.png"
    composite_img.save(os.path.join(folder_path, final_name), dpi=(target_dpi, target_dpi))

# 辅助函数
def get_output_folder_path(folder_path, mode, specific_bin):
    bin_maps_dir = os.path.join(folder_path, "BIN_Maps")
    os.makedirs(bin_maps_dir, exist_ok=True)
    name = "BIN_multi_color" if mode == 0 else "BIN1_green" if mode == 1 else f"BIN_combine_{'+'.join(map(str, specific_bin))}" if isinstance(specific_bin, list) else f"BIN_{specific_bin}" if specific_bin else "BIN_unknown"
    out_dir = os.path.join(bin_maps_dir, name)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def process_sheet_data(df, sheet_name):
    req_cols = ['SOFT_BIN', 'X_COORD', 'Y_COORD']
    if any(c not in df.columns for c in req_cols): return None
    df_clean = df[req_cols].apply(pd.to_numeric, errors='coerce').dropna()
    return df_clean if not df_clean.empty else None

def extract_wafer_id(sheet_name):
    import re
    if re.match(r'^W\d+$', sheet_name.strip(), re.IGNORECASE):
        nums = re.findall(r'\d+', sheet_name)
        if nums: return int(nums[0])
    return sheet_name.strip()

def get_sorted_wafer_ids(wafer_data, sort_method="alphabetical", manual_order=""):
    if sort_method == "manual" and manual_order:
        order_list = [item.strip() for item in manual_order.split(',') if item.strip()]
        sorted_wafer_ids = [wid for wid in order_list if wid in wafer_data]
        sorted_wafer_ids.extend(sorted([wid for wid in wafer_data.keys() if wid not in sorted_wafer_ids]))
    else:
        all_ids = list(wafer_data.keys())
        if all(isinstance(wid, (int, float)) or (isinstance(wid, str) and wid.isdigit()) for wid in all_ids):
            sorted_wafer_ids = sorted(all_ids, key=lambda x: int(x) if isinstance(x, str) else x)
        else:
            sorted_wafer_ids = sorted(all_ids)
    return sorted_wafer_ids

def get_mode_suffix(mode, specific_bin):
    if mode == 0: return "_multi_color"
    elif mode == 1: return "_bin1_green"
    elif mode == 2: return f"_bin{'+'.join(map(str, specific_bin))}_highlight" if isinstance(specific_bin, list) else f"_bin{specific_bin}_highlight"
    return "_mode_unknown"

# ==========================================
#  GUI 部分 (模块化适配版)
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
    def flush(self): pass

class BinMapApp:
    def __init__(self, parent_frame):
        # 1. 接受外部传入的父容器
        self.parent = parent_frame
        
        self.folder_path = tk.StringVar()
        self.notch_var = tk.IntVar(value=6)
        self.mode_var = tk.IntVar(value=1)
        self.bin_input = tk.StringVar()
        self.selected_file = None
        
        self.sort_method = tk.StringVar(value="alphabetical")
        self.manual_order = tk.StringVar()
        
        self.mode0_selected = tk.BooleanVar(value=False)
        self.mode1_selected = tk.BooleanVar(value=True) 
        self.mode2_selected = tk.BooleanVar(value=False)
        self.mode2_bins = tk.StringVar()
        
        self.mode2_bins.trace_add("write", self.update_combine_checkboxes)
        self.combine_mode_vars = {}
        
        self.setup_ui()
        
    def setup_ui(self):
        # 2. 所有 UI 组件挂载到 self.parent
        main_frame = ttk.Frame(self.parent, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        file_frame = ttk.LabelFrame(main_frame, text="文件选择", padding="10")
        file_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(file_frame, text="文件夹路径:").grid(row=0, column=0, sticky="w")
        ttk.Entry(file_frame, textvariable=self.folder_path, width=50).grid(row=0, column=1, padx=5)
        ttk.Button(file_frame, text="浏览...", command=self.browse_folder).grid(row=0, column=2)
        
        ttk.Label(file_frame, text="Excel文件列表:").grid(row=1, column=0, sticky="nw", pady=5)
        self.file_listbox = tk.Listbox(file_frame, height=5, exportselection=False)
        self.file_listbox.grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)
        self.file_listbox.bind('<<ListboxSelect>>', self.on_file_select)
        
        config_frame = ttk.LabelFrame(main_frame, text="绘图参数", padding="10")
        config_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(config_frame, text="Notch方位:").grid(row=0, column=0, sticky="w")
        notch_frame = ttk.Frame(config_frame)
        notch_frame.grid(row=0, column=1, columnspan=2, sticky="w", pady=5)
        notch_labels = [(6, "默认"), (9, "顺90°"), (12, "顺180°"), (3, "顺270°")]
        for val, label in notch_labels:
            ttk.Radiobutton(notch_frame, text=label, variable=self.notch_var, value=val).pack(side=tk.LEFT, padx=10)
            
        ttk.Label(config_frame, text="选择模式 (可多选):").grid(row=1, column=0, sticky="nw", pady=5)
        multi_select_frame = ttk.Frame(config_frame)
        multi_select_frame.grid(row=1, column=1, columnspan=3, sticky="w")
        
        ttk.Checkbutton(multi_select_frame, text="模式 0: 多彩", variable=self.mode0_selected, command=self.update_combine_checkboxes).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(multi_select_frame, text="模式 1: BIN1绿，其它红", variable=self.mode1_selected, command=self.update_combine_checkboxes).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(multi_select_frame, text="模式 2: 指定BIN高亮", variable=self.mode2_selected, command=self.on_mode2_toggle).pack(side=tk.LEFT)
        
        self.mode2_bin_frame = ttk.Frame(config_frame)
        self.mode2_bin_frame.grid(row=2, column=1, columnspan=3, sticky="w", pady=5)
        ttk.Label(self.mode2_bin_frame, text="模式2 BIN编号:").pack(side=tk.LEFT)
        self.mode2_bin_entry = ttk.Entry(self.mode2_bin_frame, textvariable=self.mode2_bins, width=20, state='disabled')
        self.mode2_bin_entry.pack(side=tk.LEFT, padx=5)
        ttk.Label(self.mode2_bin_frame, text="例如: 6,9 (拆分多张) 或 6+9 (同图高亮)", foreground="gray", font=("", 8)).pack(side=tk.LEFT, padx=10)
        
        ttk.Label(config_frame, text="Wafer排序方式:").grid(row=3, column=0, sticky="w", pady=5)
        sort_frame = ttk.Frame(config_frame)
        sort_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=5)
        ttk.Radiobutton(sort_frame, text="默认字符顺序", variable=self.sort_method, value="alphabetical").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(sort_frame, text="手动指定顺序", variable=self.sort_method, value="manual").pack(side=tk.LEFT)
        
        ttk.Label(config_frame, text="手动排序顺序:").grid(row=4, column=0, sticky="w", pady=5)
        self.manual_order_entry = ttk.Entry(config_frame, textvariable=self.manual_order, width=50)
        self.manual_order_entry.grid(row=4, column=1, columnspan=2, sticky="w", padx=5)
        ttk.Label(config_frame, text="用逗号分隔", foreground="gray", font=("", 8)).grid(row=4, column=3, sticky="w", padx=10)
        
        self.combine_frame = ttk.LabelFrame(main_frame, text="多模式整合设置 (至少选择两个模式才有效)", padding="10")
        self.combine_frame.pack(fill=tk.X, pady=5)
        ttk.Label(self.combine_frame, text="选择需要拼接的模式:").grid(row=0, column=0, sticky="w")
        self.combine_check_frame = ttk.Frame(self.combine_frame)
        self.combine_check_frame.grid(row=0, column=1, columnspan=2, sticky="w")
        
        self.run_btn = ttk.Button(main_frame, text="开始生成图表", command=self.start_processing, state='disabled')
        self.run_btn.pack(pady=10, fill=tk.X)
        
        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state='disabled')
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 创建 PrintLogger 但不在此处全局重定向（避免覆盖其他模块的输出）
        self.logger = PrintLogger(self.log_text)
        
        self.update_combine_checkboxes()

    def update_mode2_ui(self):
        self.mode2_bin_entry.config(state='normal' if self.mode2_selected.get() else 'disabled')

    def on_mode2_toggle(self):
        self.update_mode2_ui()
        self.update_combine_checkboxes()

    def update_combine_checkboxes(self, *args):
        for widget in self.combine_check_frame.winfo_children(): widget.destroy()
        selected_modes = []
        if self.mode0_selected.get(): selected_modes.append(('mode0', '多彩模式'))
        if self.mode1_selected.get(): selected_modes.append(('mode1', 'BIN1绿色'))
        if self.mode2_selected.get():
            bin_text = self.mode2_bins.get().strip()
            if bin_text:
                parts = [x.strip() for x in bin_text.replace('，', ',').split(',')]
                for p in parts:
                    if p: selected_modes.append((f'mode2_{p}', f'高亮BIN ({p})'))
            else:
                selected_modes.append(('mode2_empty', "高亮BIN (未输入)"))
        
        self.combine_mode_vars.clear()
        for idx, (mode_key, label) in enumerate(selected_modes):
            var = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(self.combine_check_frame, text=label, variable=var)
            cb.grid(row=idx // 3, column=idx % 3, padx=(0, 15), pady=2, sticky='w')
            self.combine_mode_vars[mode_key] = var

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_path.set(path)
            self.file_listbox.delete(0, tk.END)
            try:
                for f in os.listdir(path):
                    if f.endswith('.xlsx'): self.file_listbox.insert(tk.END, f)
            except Exception as e: print(f"读取文件夹失败: {e}")

    def on_file_select(self, event):
        sel = self.file_listbox.curselection()
        if sel:
            self.selected_file = self.file_listbox.get(sel[0])
            self.run_btn.config(state='normal')

    def start_processing(self):
        if not self.folder_path.get() or not self.selected_file: return
        if not (self.mode0_selected.get() or self.mode1_selected.get() or self.mode2_selected.get()):
            messagebox.showwarning("提示", "请至少选择一个模式")
            return
            
        self.run_btn.config(state='disabled')
        self.log_text.configure(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state='disabled')
        
        sort_method = self.sort_method.get()
        manual_order = self.manual_order.get().strip()
        
        threading.Thread(target=self.run_multi_mode_logic, args=(self.folder_path.get(), self.selected_file, self.notch_var.get(), sort_method, manual_order), daemon=True).start()

    def run_multi_mode_logic(self, folder_path, excel_file, notch_dir, sort_method, manual_order):
        # 临时重定向 stdout 到本模块的日志框（避免跨模块输出污染）
        original_stdout = sys.stdout
        sys.stdout = self.logger
        try:
            print(f"正在读取Excel: {excel_file} ...")
            xl = pd.ExcelFile(os.path.join(folder_path, excel_file))
            all_wafer_data = {}
            for sheet in xl.sheet_names:
                df_clean = process_sheet_data(xl.parse(sheet), sheet)
                if df_clean is not None:
                    wid = extract_wafer_id(sheet)
                    if wid: all_wafer_data[wid] = df_clean
            
            if not all_wafer_data: return
            
            tasks = []
            if self.mode0_selected.get(): tasks.append((0, None))
            if self.mode1_selected.get(): tasks.append((1, None))
            if self.mode2_selected.get() and self.mode2_bins.get().strip():
                parts = [x.strip() for x in self.mode2_bins.get().replace('，', ',').split(',')]
                for p in parts:
                    if not p: continue
                    if '+' in p: tasks.append((2, [int(x.strip()) for x in p.split('+')]))
                    else: tasks.append((2, int(p)))
            
            mode_output_dirs = {}
            for curr_mode, curr_bin in tasks:
                output_dir = get_output_folder_path(folder_path, curr_mode, curr_bin)
                dict_key_bin = tuple(curr_bin) if isinstance(curr_bin, list) else curr_bin
                mode_output_dirs[(curr_mode, dict_key_bin)] = output_dir
                
                temp_dir = os.path.join(output_dir, "temp_stitch")
                os.makedirs(temp_dir, exist_ok=True)
                
                excel_base = os.path.splitext(excel_file)[0]
                suffix = get_mode_suffix(curr_mode, curr_bin)
                
                # 多彩模式：收集所有Wafer的BIN并集，统一颜色映射
                global_settings = None
                if curr_mode == 0:
                    all_unique_bins = set()
                    for df in all_wafer_data.values():
                        all_unique_bins.update(df['SOFT_BIN'].unique())
                    global_settings = get_color_settings(curr_mode, curr_bin, unique_bins=list(all_unique_bins))
                
                for wafer_id, df in all_wafer_data.items():
                    fig, ax = plt.subplots(figsize=(8, 8))
                    create_wafer_map_pcolormesh(df, ax, wafer_id, notch_dir, curr_mode, curr_bin, add_legend=False, global_settings=global_settings)
                    plt.savefig(os.path.join(temp_dir, f"{excel_base}_W{wafer_id}_temp.png"), dpi=300, bbox_inches='tight')
                    
                    if curr_mode == 0:
                        if global_settings['legend_info']: _add_custom_legend(ax, df, global_settings['legend_info'])
                    
                    plt.savefig(os.path.join(output_dir, f"{excel_base}_W{wafer_id}_notch{notch_dir}{suffix}.png"), dpi=300, bbox_inches='tight')
                    plt.close()
                
                create_composite_map_pcolormesh(all_wafer_data, output_dir, excel_file, notch_dir, curr_mode, curr_bin, temp_folder=temp_dir, sort_method=sort_method, manual_order=manual_order)
                try: shutil.rmtree(temp_dir)
                except: pass
            
            selected_combine_modes = [k for k, v in self.combine_mode_vars.items() if v.get()]
            if len(selected_combine_modes) >= 2:
                combine_tasks = []
                for task in tasks:
                    mode, bin_val = task
                    if mode == 0 and 'mode0' in selected_combine_modes: combine_tasks.append(task)
                    elif mode == 1 and 'mode1' in selected_combine_modes: combine_tasks.append(task)
                    elif mode == 2:
                        key = f"mode2_{'+'.join(map(str, bin_val))}" if isinstance(bin_val, list) else f"mode2_{bin_val}"
                        if key in selected_combine_modes: combine_tasks.append(task)
                if len(combine_tasks) >= 2:
                    self.create_combined_maps(all_wafer_data, folder_path, excel_file, notch_dir, combine_tasks, mode_output_dirs, sort_method, manual_order)
            
            print("任务完成！")
            
        except Exception as e:
            print(f"发生错误: {e}")
        finally:
            # 恢复原始 stdout
            sys.stdout = original_stdout
            self.parent.after(0, lambda: self.run_btn.config(state='normal'))
    
    def create_combined_maps(self, wafer_data, folder_path, excel_file, notch_dir, tasks, mode_output_dirs, sort_method, manual_order):
        try:
            mode_names = {0: "多彩", 1: "BIN1绿", 2: "BIN高亮"}
            excel_base = os.path.splitext(excel_file)[0]
            bin_maps_dir = os.path.join(folder_path, "BIN_Maps")
            
            sorted_wafer_ids = get_sorted_wafer_ids(wafer_data, sort_method, manual_order)
            for wafer_id in sorted_wafer_ids:
                mode_images, mode_titles = [], []
                for mode, bin_val in tasks:
                    safe_wafer_id = str(wafer_id).replace(':', '_').replace('/', '_').replace('\\', '_')
                    output_dir = mode_output_dirs.get((mode, tuple(bin_val) if isinstance(bin_val, list) else bin_val))
                    if not output_dir: continue
                    img_path = os.path.join(output_dir, f"{excel_base}_W{safe_wafer_id}_notch{notch_dir}{get_mode_suffix(mode, bin_val)}.png")
                    if os.path.exists(img_path):
                        mode_images.append(plt.imread(img_path))
                        title = mode_names.get(mode, f"模式{mode}")
                        if mode == 2: title += f" (BIN{'+'.join(map(str, bin_val)) if isinstance(bin_val, list) else bin_val})"
                        mode_titles.append(title)
                
                if len(mode_images) > 1:
                    fig, axes = plt.subplots(1, len(mode_images), figsize=(6 * len(mode_images), 6))
                    if len(mode_images) == 1: axes = [axes]
                    for i, (img, title) in enumerate(zip(mode_images, mode_titles)):
                        axes[i].imshow(img); axes[i].set_title(title, fontsize=14, fontweight='bold', pad=20); axes[i].axis('off')
                    fig.suptitle(f" ", fontsize=16, fontweight='bold', y=0.98)
                    plt.tight_layout()
                    plt.savefig(os.path.join(bin_maps_dir, f"{excel_base}_W{safe_wafer_id}_notch{notch_dir}_combined_subplots.png"), dpi=300, bbox_inches='tight')
                    plt.close()
            
            mode_composite_images, mode_composite_titles = [], []
            import glob
            for mode, bin_val in tasks:
                output_dir = mode_output_dirs.get((mode, tuple(bin_val) if isinstance(bin_val, list) else bin_val))
                if not output_dir: continue
                composite_files = glob.glob(os.path.join(output_dir, f"{excel_base}_notch{notch_dir}{get_mode_suffix(mode, bin_val)}_composite_*.png"))
                if composite_files:
                    mode_composite_images.append(plt.imread(composite_files[0]))
                    title = mode_names.get(mode, f"模式{mode}")
                    if mode == 2: title += f" (BIN{'+'.join(map(str, bin_val)) if isinstance(bin_val, list) else bin_val})"
                    mode_composite_titles.append(title)
            
            if len(mode_composite_images) > 1:
                fig, axes = plt.subplots(1, len(mode_composite_images), figsize=(10 * len(mode_composite_images), 10))
                if len(mode_composite_images) == 1: axes = [axes]
                for i, (img, title) in enumerate(zip(mode_composite_images, mode_composite_titles)):
                    axes[i].imshow(img); axes[i].set_title(title, fontsize=20, fontweight='bold'); axes[i].axis('off')
                fig.suptitle(f"多模式整图拼接", fontsize=28, fontweight='bold', y=0.95)
                plt.tight_layout()
                plt.savefig(os.path.join(bin_maps_dir, f"{excel_base}_notch{notch_dir}_combined_composites.png"), dpi=300, bbox_inches='tight')
                plt.close()
        except Exception as e:
            print(f"拼接图时出错: {e}")

# ==========================================
# 统一入口与独立测试
# ==========================================
def create_ui(parent):
    """供 main.py 调用的接口"""
    return BinMapApp(parent)

if __name__ == "__main__":
    # 独立运行时的测试窗口
    root = tk.Tk()
    root.title("[独立运行] - 晶圆BIN Map生成器")
    root.geometry("900x850")
    app = BinMapApp(root)
    root.mainloop()