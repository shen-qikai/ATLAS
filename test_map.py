# 文件名: test_map.py
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import os
import warnings
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import re
import glob
from atlas_pipeline.plot_jobs import safe_name

warnings.filterwarnings('ignore')

# ========== 字体设置 ==========
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

# ========== 坐标旋转 ==========
def rotate_coordinates(df, notch_dir):
    if notch_dir == 6: return df.copy()
    df_r = df.copy()
    if notch_dir == 9:      
        df_r['X_COORD'], df_r['Y_COORD'] = -df['Y_COORD'], df['X_COORD']
    elif notch_dir == 12:   
        df_r['X_COORD'], df_r['Y_COORD'] = -df['X_COORD'], -df['Y_COORD']
    elif notch_dir == 3:    
        df_r['X_COORD'], df_r['Y_COORD'] = df['Y_COORD'], -df['X_COORD']
    return df_r

# ========== 解析列选择 ==========
def parse_columns(input_str, headers):
    if not input_str: return []
    cols = []
    for part in re.split(r',\s*', input_str):
        if '-' in part and part not in headers:
            a, b = part.split('-', 1)
            a, b = a.strip(), b.strip()
            if a in headers and b in headers:
                ia, ib = headers.index(a), headers.index(b)
                if ia <= ib: cols.extend(headers[ia:ib+1])
            else:
                cols.append(part)
        else:
            cols.append(part.strip())
    return list(dict.fromkeys(cols))

# ========== 数据转网格 ==========
def df_to_grid(df, val_col):
    df_dedup = df.drop_duplicates(['X_COORD','Y_COORD'], keep='last')
    pivot = df_dedup.pivot(index='Y_COORD', columns='X_COORD', values=val_col)
    xs = sorted(df['X_COORD'].unique())
    ys = sorted(df['Y_COORD'].unique())
    pivot = pivot.reindex(index=ys, columns=xs)
    X, Y = np.meshgrid(xs, ys)
    return X, Y, pivot.values

# ========== 计算颜色范围 ==========
def compute_color_limits(df, val_col, mode, meta, g_val, r_val):
    if mode == 'manual' and g_val is not None and r_val is not None:
        return g_val, r_val, None, plt.cm.RdYlGn_r
    data = df[val_col].dropna().values
    if len(data) == 0: 
        return 0, 1, None, plt.cm.RdYlGn_r
    mean, std = np.mean(data), np.std(data)
    if std == 0: 
        vmin, vmax = mean-0.01, mean+0.01
    else: 
        vmin, vmax = mean-3*std, mean+3*std

    if mode == 'usl_lsl':
        try:
            l, u = float(meta.get('lsl','NA')), float(meta.get('usl','NA'))
            vmin, vmax = min(l, u), max(l, u)
        except: 
            pass

    if mode == 'pass_fail':
        try:
            l = float(meta.get('lsl', 'NA'))
            u = float(meta.get('usl', 'NA'))
            l, u = min(l, u), max(l, u)
            from matplotlib.colors import ListedColormap, BoundaryNorm
            cmap = ListedColormap(['#FF0000', '#D3D3D3', '#FF0000'])
            pad = (u - l) * 0.1
            vmin, vmax = l - pad, u + pad
            bounds = [vmin, l, u, vmax]
            norm = BoundaryNorm(bounds, cmap.N)
            return vmin, vmax, norm, cmap
        except: 
            pass

    return vmin, vmax, None, plt.cm.RdYlGn_r

# ========== 通用绘图 ==========
def plot_wafer(ax, df, val_col, title, meta, mode, g_val, r_val, notch_dir, show_cbar=False):
    df_r = rotate_coordinates(df, notch_dir)
    X, Y, Z = df_to_grid(df_r, val_col)
    if Z is None:
        ax.text(0.5, 0.5, 'Data Error', ha='center', va='center')
        ax.axis('off')
        return None
    vmin, vmax, norm, cmap = compute_color_limits(df, val_col, mode, meta, g_val, r_val)
    
    if norm is not None:
        mesh = ax.pcolormesh(X, -Y, Z, cmap=cmap, norm=norm, shading='auto')
    else:
        mesh = ax.pcolormesh(X, -Y, Z, cmap=cmap, vmin=vmin, vmax=vmax, shading='auto')
    
    xs, ys = df_r['X_COORD'], df_r['Y_COORD']
    if len(xs) > 1 and len(ys) > 1:
        rx, ry = xs.max() - xs.min(), ys.max() - ys.min()
        ax.set_aspect(rx/ry if rx > 0 and ry > 0 else 'equal')
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_frame_on(False)
    ax.set_title(title, fontsize=9 if not show_cbar else 12, fontweight='bold')
    
    avg = df[val_col].mean()
    if show_cbar:
        lsl, usl = meta.get('lsl','NA'), meta.get('usl','NA')
        def fmt(x): 
            try: return f"{float(x):.2f}"
            except: return str(x)
        txt = f"LSL: {fmt(lsl)}\nUSL: {fmt(usl)}\nTotal: {len(df)}\nAvg: {avg:.2f}"
        fsize = 8
    else:
        txt = f"Total: {len(df)}\nAvg: {avg:.2f}"
        fsize = 6
    ax.text(0.02, 0.98, txt, transform=ax.transAxes, fontsize=fsize, va='top', family='monospace')
    
    if show_cbar:
        plt.colorbar(mesh, ax=ax, shrink=0.8)
    return mesh

# ========== 单图保存 ==========
def save_single_map(df, col, wid, out_dir, fname, meta, mode, g_val, r_val, notch_dir):
    fig, ax = plt.subplots(figsize=(8, 8))
    unit = f"({meta.get('unit','')})" if meta.get('unit') else ""
    title = f"{col} {unit} {wid}"
    plot_wafer(ax, df, col, title, meta, mode, g_val, r_val, notch_dir, show_cbar=(mode != 'pass_fail'))
    safe = safe_name(wid)
    plt.savefig(os.path.join(out_dir, f"{os.path.splitext(fname)[0]}_{safe_name(col)}_{safe}_notch{notch_dir}_{mode}.png"),
                dpi=300, bbox_inches='tight')
    plt.close()

# ========== 整合图 ==========
def save_composite(wafer_dict, col, out_dir, fname, metas, mode, g_val, r_val, notch_dir, sort_meth, manual_ord,
                   display_labels=None):
    n = len(wafer_dict)
    if n == 0: return
    if sort_meth == 'manual' and manual_ord:
        order = [w.strip() for w in manual_ord.split(',') if w.strip() in wafer_dict]
        ids = order + sorted(set(wafer_dict) - set(order))
    else:
        ids = sorted(wafer_dict)
    if n <= 4: 
        r, c = 1, n
    else: 
        c = int(np.ceil(np.sqrt(n)))
        r = int(np.ceil(n / c))
    fig = plt.figure(figsize=(max(15, c*5), max(15, r*5)))
    
    meta0 = next(iter(metas.values()))
    unit = f"({meta0.get('unit','')})" if meta0.get('unit') else ""
    lsl, usl = meta0.get('lsl','NA'), meta0.get('usl','NA')
    def fmt(x): 
        try: return f"{float(x):.2f}"
        except: return str(x)
    title = f"{col} {unit}"
    if lsl != 'NA' or usl != 'NA':
        title += f" | LSL: {fmt(lsl)} | USL: {fmt(usl)}"
    fig.suptitle(title, fontsize=28, fontweight='bold', y=0.95)
    
    all_vals = np.concatenate([df[col].dropna().values for df in wafer_dict.values()])
    if len(all_vals) > 0:
        g_mean, g_std = np.mean(all_vals), np.std(all_vals)
        gvmin, gvmax = (g_mean - 0.01, g_mean + 0.01) if g_std == 0 else (g_mean - 3*g_std, g_mean + 3*g_std)
    else: 
        gvmin, gvmax = 0, 1
    if mode == 'manual' and g_val is not None:
        gvmin, gvmax = g_val, r_val
    
    mesh = None
    for i, wid in enumerate(ids):
        ax = fig.add_subplot(r, c, i+1)
        title = display_labels.get(wid, wid) if display_labels else wid
        mesh = plot_wafer(ax, wafer_dict[wid], col, title, metas[wid], mode, gvmin, gvmax, notch_dir, show_cbar=False)
    
    plt.subplots_adjust(left=0.05, right=0.92 if mode != 'pass_fail' else 0.98, top=0.90, wspace=0.15, hspace=0.25)
    if mesh and mode != 'pass_fail':
        cax = fig.add_axes([0.93, 0.15, 0.015, 0.7])
        cbar = plt.colorbar(mesh, cax=cax)
        cbar.set_label({'auto': 'Global Sigma', 'manual': 'Manual', 'usl_lsl': 'USL/LSL'}.get(mode, ''), fontsize=14)
    plt.savefig(os.path.join(out_dir, f"{os.path.splitext(fname)[0]}_{safe_name(col)}_composite_{r}x{c}_notch{notch_dir}_{mode}.png"),
                dpi=300, bbox_inches='tight')
    plt.close()

# ========== 多模式拼接图 ==========
def create_combined_maps(wafer_data, test_col, out_base, fname, all_meta, modes_to_combine, 
                         sort_meth, manual_ord, notch_dir, log_func, display_labels=None, strict=False):
    mode_names = {'auto': '自动3σ', 'manual': '手动', 'usl_lsl': 'USL/LSL', 'pass_fail': 'Pass/Fail'}
    if len(modes_to_combine) <= 1: return

    if sort_meth == 'manual' and manual_ord:
        order = [w.strip() for w in manual_ord.split(',') if w.strip() in wafer_data]
        ids = order + sorted(set(wafer_data) - set(order))
    else:
        ids = sorted(wafer_data)

    for wid in ids:
        try:
            fig, axes = plt.subplots(1, len(modes_to_combine), figsize=(6 * len(modes_to_combine), 6))
            if len(modes_to_combine) == 1: axes = [axes]
            for i, mode in enumerate(modes_to_combine):
                safe = safe_name(wid)
                img_path = os.path.join(out_base, mode, f"{os.path.splitext(fname)[0]}_{safe_name(test_col)}_{safe}_notch{notch_dir}_{mode}.png")
                if os.path.exists(img_path):
                    axes[i].imshow(plt.imread(img_path))
                    axes[i].set_title(mode_names.get(mode, mode), fontsize=14, fontweight='bold')
                elif strict:
                    raise FileNotFoundError(img_path)
                axes[i].axis('off')
            unit = f"({next(iter(all_meta.values())).get('unit','')})" if all_meta else ""
            title = display_labels.get(wid, wid) if display_labels else wid
            fig.suptitle(f"{test_col} {unit} - {title}", fontsize=16, fontweight='bold', y=0.98)
            plt.tight_layout()
            plt.savefig(os.path.join(out_base, f"{os.path.splitext(fname)[0]}_{safe_name(test_col)}_{safe_name(wid)}_combined_subplots.png"), dpi=300, bbox_inches='tight')
            plt.close()
        except Exception as e:
            if strict:
                raise
            log_func(f"    -> 子图拼接失败 ({wid}): {e}")

    try:
        mode_imgs = []
        for mode in modes_to_combine:
            files = glob.glob(os.path.join(out_base, mode, f"{os.path.splitext(fname)[0]}_{safe_name(test_col)}_composite_*_notch{notch_dir}_{mode}.png"))
            if files: mode_imgs.append((plt.imread(files[0]), mode_names.get(mode, mode)))
            elif strict:
                raise FileNotFoundError(f"缺少 {mode} 整合图")
        if len(mode_imgs) > 1:
            fig, axes = plt.subplots(1, len(mode_imgs), figsize=(10 * len(mode_imgs), 10))
            if len(mode_imgs) == 1: axes = [axes]
            for i, (img, title) in enumerate(mode_imgs):
                axes[i].imshow(img); axes[i].set_title(title, fontsize=20, fontweight='bold'); axes[i].axis('off')
            unit = f"({next(iter(all_meta.values())).get('unit','')})" if all_meta else ""
            fig.suptitle(f"{test_col} {unit}", fontsize=28, fontweight='bold', y=0.95)
            plt.tight_layout()
            plt.savefig(os.path.join(out_base, f"{os.path.splitext(fname)[0]}_{safe_name(test_col)}_combined_composites.png"), dpi=300, bbox_inches='tight')
            plt.close()
    except Exception as e:
        if strict:
            raise
        log_func(f"    -> 整图拼接失败: {e}")

# ==========================================
# GUI 部分 (已模块化)
# ==========================================
class WaferMapApp:
    def __init__(self, parent_frame):
        # 原有的 self.root 变为 self.parent
        self.parent = parent_frame
        
        self.folder = tk.StringVar()
        self.file = tk.StringVar()
        self.test_col = tk.StringVar()
        self.green = tk.StringVar()
        self.red = tk.StringVar()
        self.sort_meth = tk.StringVar(value="alphabetical")
        self.manual_ord = tk.StringVar()
        self.notch = tk.IntVar(value=6)
        self.modes = {m: tk.BooleanVar(value=(m=='auto')) for m in ['auto','manual','usl_lsl','pass_fail']}
        
        self.combine_enabled = tk.BooleanVar(value=False)
        self.combine_modes = {m: tk.BooleanVar(value=False) for m in ['auto','manual','usl_lsl','pass_fail']}
        
        self.setup_ui()

    def setup_ui(self):
        # 将组件挂载到 parent_frame
        f = ttk.Frame(self.parent, padding=10); f.pack(fill='both', expand=True)
        
        ttk.LabelFrame(f, text="1.数据文件夹", padding=5).pack(fill='x', pady=2)
        f1 = ttk.Frame(f); f1.pack(fill='x', pady=2)
        ttk.Entry(f1, textvariable=self.folder, width=50).pack(side='left', padx=5)
        ttk.Button(f1, text="浏览", command=self.browse).pack(side='left')
        
        ttk.LabelFrame(f, text="2.选择文件", padding=5).pack(fill='both', expand=True, pady=2)
        self.lb = tk.Listbox(f, height=4); self.lb.pack(fill='both', expand=True)
        self.lb.bind('<<ListboxSelect>>', self.on_select)
        
        ttk.LabelFrame(f, text="3.参数", padding=5).pack(fill='x', pady=2)
        p = ttk.Frame(f); p.pack(fill='x')
        row = 0
        ttk.Label(p, text="测试列:").grid(row=row, column=0, sticky='w', pady=2)
        ttk.Entry(p, textvariable=self.test_col, width=40).grid(row=row, column=1, columnspan=3, sticky='w', padx=5)
        ttk.Label(p, text="(逗号或范围)", foreground='gray').grid(row=row, column=4, sticky='w')
        
        row += 1
        ttk.Label(p, text="颜色模式:").grid(row=row, column=0, sticky='w', pady=2)
        mf = ttk.Frame(p); mf.grid(row=row, column=1, columnspan=3, sticky='w')
        labels = {'auto':'自动3σ','manual':'手动','usl_lsl':'USL/LSL','pass_fail':'通过/失败'}
        for m in ['auto','manual','usl_lsl','pass_fail']:
            ttk.Checkbutton(mf, text=labels[m], variable=self.modes[m]).pack(side='left', padx=3)
            
        row += 1
        ttk.Label(p, text="Min:").grid(row=row, column=0, sticky='w', pady=2)
        ttk.Entry(p, textvariable=self.green, width=10).grid(row=row, column=1, sticky='w', padx=5)
        ttk.Label(p, text="Max:").grid(row=row, column=2, sticky='w', pady=2)
        ttk.Entry(p, textvariable=self.red, width=10).grid(row=row, column=3, sticky='w', padx=5)
        ttk.Label(p, text="*仅手动", foreground='gray', font=('',8)).grid(row=row, column=4, sticky='w')
        
        row += 1
        ttk.Label(p, text="排序:").grid(row=row, column=0, sticky='w', pady=2)
        sf = ttk.Frame(p); sf.grid(row=row, column=1, columnspan=3, sticky='w')
        ttk.Radiobutton(sf, text="字符顺序", variable=self.sort_meth, value='alphabetical').pack(side='left', padx=5)
        ttk.Radiobutton(sf, text="手动", variable=self.sort_meth, value='manual').pack(side='left')
        
        row += 1
        ttk.Entry(p, textvariable=self.manual_ord, width=40).grid(row=row, column=1, columnspan=3, sticky='w', padx=5)
        ttk.Label(p, text="逗号分隔", foreground='gray', font=('',8)).grid(row=row, column=4, sticky='w')
        
        row += 1
        ttk.Label(p, text="Notch方位:").grid(row=row, column=0, sticky='w', pady=2)
        nf = ttk.Frame(p); nf.grid(row=row, column=1, columnspan=3, sticky='w')
        for val, txt in [(6, "默认"), (9, "顺90°"), (12, "顺180°"), (3, "顺270°")]:
            ttk.Radiobutton(nf, text=txt, variable=self.notch, value=val).pack(side='left', padx=5)
            
        row += 1
        ttk.Label(p, text="拼接对比:").grid(row=row, column=0, sticky='w', pady=2)
        cf = ttk.Frame(p); cf.grid(row=row, column=1, columnspan=3, sticky='w')
        ttk.Checkbutton(cf, text="生成多模式拼接图", variable=self.combine_enabled, command=self.toggle_combine_modes).pack(side='left')
        
        row += 1
        self.combine_frame = ttk.LabelFrame(p, text="选择要拼接的模式", padding=5)
        self.combine_frame.grid(row=row, column=1, columnspan=3, sticky='w', pady=5)
        self.combine_frame.grid_remove()
        cmf = ttk.Frame(self.combine_frame); cmf.pack()
        for m, lab in labels.items():
            ttk.Checkbutton(cmf, text=lab, variable=self.combine_modes[m]).pack(side='left', padx=3)
            
        ttk.Button(f, text="开始生成", command=self.start).pack(fill='x', pady=10)
        
        ttk.LabelFrame(f, text="日志", padding=5).pack(fill='both', expand=True)
        self.logbox = scrolledtext.ScrolledText(f, height=6, state='disabled', font=('Consolas',9))
        self.logbox.pack(fill='both', expand=True)

    def toggle_combine_modes(self):
        if self.combine_enabled.get(): self.combine_frame.grid()
        else: self.combine_frame.grid_remove()

    def browse(self):
        p = filedialog.askdirectory()
        if p:
            self.folder.set(p)
            self.lb.delete(0, 'end')
            for fn in os.listdir(p):
                if fn.endswith('.xlsx') and not fn.startswith('~$'):
                    self.lb.insert('end', fn)

    def on_select(self, event):
        sel = self.lb.curselection()
        if sel: self.file.set(self.lb.get(sel[0]))

    def log(self, msg):
        self.parent.after(0, lambda: self.logbox.config(state='normal') or 
                        self.logbox.insert('end', msg+'\n') or 
                        self.logbox.see('end') or 
                        self.logbox.config(state='disabled'))

    def start(self):
        if not self.folder.get() or not self.file.get() or not self.test_col.get(): return
        modes = [m for m, v in self.modes.items() if v.get()]
        if not modes: return
        g_val = r_val = None
        if 'manual' in modes:
            try: g_val, r_val = float(self.green.get()), float(self.red.get())
            except: return messagebox.showerror("错误", "手动模式需要数字")
        combine_modes = [m for m, v in self.combine_modes.items() if v.get()] if self.combine_enabled.get() else []
        threading.Thread(target=self.process, args=(modes, g_val, r_val, combine_modes), daemon=True).start()

    def process(self, modes, g_val, r_val, combine_modes):
        try:
            path = os.path.join(self.folder.get(), self.file.get())
            xl = pd.ExcelFile(path)
            headers = xl.parse(xl.sheet_names[0], nrows=0).columns.tolist()
            cols = parse_columns(self.test_col.get(), headers)
            if not cols: return
            for col in cols:
                out_base = os.path.join(self.folder.get(), f"{col}_Maps")
                os.makedirs(out_base, exist_ok=True)
                data, metas = {}, {}
                for sheet in xl.sheet_names:
                    df = xl.parse(sheet)
                    if not all(c in df.columns for c in ['X_COORD','Y_COORD',col]): continue
                    try: unit, lsl, usl = df[col].iloc[0:3]
                    except: unit = lsl = usl = ''
                    metas[sheet] = {
                        'unit': '' if pd.isna(unit) else str(unit),
                        'lsl': 'NA' if pd.isna(lsl) else str(lsl),
                        'usl': 'NA' if pd.isna(usl) else str(usl)
                    }
                    dfc = df.iloc[4:][['X_COORD','Y_COORD',col]].copy()
                    if dfc[col].dtype == object: dfc[col] = dfc[col].astype(str).str.replace('[><]', '', regex=True)
                    dfc = dfc.apply(pd.to_numeric, errors='coerce').dropna()
                    if not dfc.empty: data[sheet] = dfc
                if not data: continue
                for mode in modes:
                    mode_dir = os.path.join(out_base, mode)
                    os.makedirs(mode_dir, exist_ok=True)
                    for wid, df in data.items():
                        save_single_map(df, col, wid, mode_dir, self.file.get(), metas[wid], mode, g_val, r_val, self.notch.get())
                    save_composite(data, col, mode_dir, self.file.get(), metas, mode, g_val, r_val, self.notch.get(), self.sort_meth.get(), self.manual_ord.get())
                self.log(f"{col} 完成")
                if combine_modes:
                    avail = [m for m in combine_modes if m in modes]
                    if len(avail) >= 2: create_combined_maps(data, col, out_base, self.file.get(), metas, avail, self.sort_meth.get(), self.manual_ord.get(), self.notch.get(), self.log)
            self.log("全部完成")
        except Exception as e:
            self.log(f"错误: {e}")

# ==========================================
# 统一入口与独立测试
# ==========================================
def create_excel_ui(parent):
    """Optional legacy Excel entry point."""
    return WaferMapApp(parent)


def create_ui(parent, service=None, root_var=None):
    from direct_plot_ui import DirectPlotApp
    return DirectPlotApp(parent, "test", service, root_var)

if __name__ == "__main__":
    # 独立运行时的测试窗口
    root = tk.Tk()
    root.title("[独立运行] - 测试项热力图")
    root.geometry("1200x850")
    root.minsize(1000, 650)
    app = create_ui(root)
    root.mainloop()
