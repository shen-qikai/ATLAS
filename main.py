# 文件名: main.py
# Git 学习演示：这是一条没有业务影响的注释。
import tkinter as tk
from tkinter import ttk

# 导入所有 6 个子功能模块
import data_preprocessing
import merge_cleaning
import merge_BIN
import BIN_map
import test_map
import probability
import wafer_yield_summary  # <-- 新引入的模块
import automatic_yield
from atlas_pipeline.plot_data import SHARED_PLOT_DATA

class MainApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ATLAS - Automated Test Learning & Analytics System v1.4.0")
        self.root.geometry("1250x850")
        self.root.minsize(1000, 650)
        self.plot_root_var = tk.StringVar()
        
        # 优化全局UI主题样式
        style = ttk.Style()
        try:
            style.theme_use('clam') # 'clam' 主题更具现代感
        except:
            pass
            
        # 设置标签页的字体和间距
        style.configure("TNotebook.Tab", font=("Microsoft YaHei", 10, "bold"), padding=[12, 8])
        style.configure("TNotebook", background="#E8E8E8")

        # ==========================================
        # 1. 创建顶部的标签页控制器 (Notebook)
        # ==========================================
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ==========================================
        # 2. 为每个功能创建单独的容器 (Frame)
        # ==========================================
        self.tab_data_preprocessing      = ttk.Frame(self.notebook)
        self.tab_cleaning      = ttk.Frame(self.notebook)
        self.tab_yield_summary  = ttk.Frame(self.notebook)
        self.tab_merge_bin     = ttk.Frame(self.notebook)
        self.tab_bin_map       = ttk.Frame(self.notebook)
        self.tab_test_map      = ttk.Frame(self.notebook)
        self.tab_probability   = ttk.Frame(self.notebook)
        self.tab_automatic_yield = ttk.Frame(self.notebook)
 	 # <-- 为新功能创建 Frame

        # ==========================================
        # 3. 将容器加入到标签页中
        # ==========================================
        self.notebook.add(self.tab_data_preprocessing,      text="0. 数据预处理")
        self.notebook.add(self.tab_automatic_yield, text="自动增量良率")
        self.notebook.add(self.tab_cleaning,      text="1. 数据汇总清理 (Cleaning)")
        self.notebook.add(self.tab_yield_summary, text="2. 良率与BIN汇总报表 (Yield Summary)")
        self.notebook.add(self.tab_merge_bin,     text="3. 数据提取整合（可选导出）")
        self.notebook.add(self.tab_bin_map,       text="4. 晶圆 BIN Map 绘图")
        self.notebook.add(self.tab_test_map,      text="5. 测试项热力图 (Test Map)")
        self.notebook.add(self.tab_probability,   text="6. 概率分布叠加图 (Probability)")
        # <-- 添加到UI

        # ==========================================
        # 4. 调用各个模块的接口，把它们的UI画在对应的标签页里
        # ==========================================
        # 为了防止某些模块尚未开发完成导致报错，建议用 try...except 保护
        try: data_preprocessing.create_ui(self.tab_data_preprocessing)
        except Exception as e: print("加载 data_preprocessing 失败:", e)

        self.automatic_yield_ui = automatic_yield.create_ui(self.tab_automatic_yield)

        try: merge_cleaning.create_ui(self.tab_cleaning)
        except Exception as e: print("加载 merge_cleaning 失败:", e)
            
        try: merge_BIN.create_ui(self.tab_merge_bin)
        except Exception as e: print("加载 merge_BIN 失败:", e)
            
        try: self.bin_map_ui = BIN_map.create_ui(self.tab_bin_map, SHARED_PLOT_DATA, self.plot_root_var)
        except Exception as e: print("加载 BIN_map 失败:", e)
            
        try: self.test_map_ui = test_map.create_ui(self.tab_test_map, SHARED_PLOT_DATA, self.plot_root_var)
        except Exception as e: print("加载 test_map 失败:", e)
            
        try: self.probability_ui = probability.create_ui(self.tab_probability, SHARED_PLOT_DATA, self.plot_root_var)
        except Exception as e: print("加载 probability 失败:", e)
            
        # 调用新模块的标准接口
        try: 
            wafer_yield_summary.create_ui(self.tab_yield_summary)
        except Exception as e: 
            print("加载 wafer_yield_summary 失败:", e)

if __name__ == "__main__":
    root = tk.Tk()
    
    # 尝试加载高分辨率支持 (仅针对Windows)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
        
    app = MainApp(root)
    root.mainloop()
