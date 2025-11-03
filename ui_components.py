"""
UI组件模块
负责创建和管理GUI界面组件
"""
import tkinter as tk
from tkinter import ttk
import os
import sys


class UIComponents:
    """UI组件创建器"""
    
    def __init__(self, root, app_path):
        """
        初始化UI组件
        
        Args:
            root: Tkinter根窗口
            app_path: 应用路径
        """
        self.root = root
        self.app_path = app_path
        self.components = {}
    
    def create_main_window(self):
        """创建主窗口"""
        self.root.title("批量文件压缩工具 v2.0")
        self.root.geometry("900x750")
        self.root.resizable(True, True)
        
        # 设置窗口图标
        icon_path = os.path.join(self.app_path, 'icon.ico')
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)
        
        self.components['style'] = ttk.Style()
    
    def create_menu_bar(self, menu_callbacks):
        """
        创建菜单栏
        
        Args:
            menu_callbacks: 菜单回调函数字典
        """
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="选择源文件夹", command=menu_callbacks.get('browse_source'), accelerator="Ctrl+O")
        file_menu.add_command(label="选择目标文件夹", command=menu_callbacks.get('browse_target'), accelerator="Ctrl+D")
        file_menu.add_separator()
        file_menu.add_command(label="打开输出文件夹", command=menu_callbacks.get('open_output_folder'), accelerator="Ctrl+E")
        file_menu.add_separator()
        file_menu.add_command(label="查看历史记录", command=menu_callbacks.get('show_history'))
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=menu_callbacks.get('quit'), accelerator="Ctrl+Q")
        
        # 编辑菜单
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="编辑", menu=edit_menu)
        edit_menu.add_command(label="保存设置", command=menu_callbacks.get('save_config'), accelerator="Ctrl+S")
        edit_menu.add_command(label="刷新文件列表", command=menu_callbacks.get('refresh_files'), accelerator="F5")
        
        # 工具菜单
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="工具", menu=tools_menu)
        tools_menu.add_command(label="开始压缩", command=menu_callbacks.get('start_compress'), accelerator="Ctrl+R")
        tools_menu.add_command(label="暂停压缩", command=menu_callbacks.get('pause_compress'), accelerator="Ctrl+P")
        tools_menu.add_command(label="恢复压缩", command=menu_callbacks.get('resume_compress'), accelerator="Ctrl+R")
        tools_menu.add_command(label="停止压缩", command=menu_callbacks.get('stop_compress'), accelerator="Ctrl+T")
        tools_menu.add_separator()
        tools_menu.add_command(label="压缩预览", command=menu_callbacks.get('preview_compress'))
        
        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="使用说明", command=menu_callbacks.get('show_help'))
        help_menu.add_separator()
        help_menu.add_command(label="关于", command=menu_callbacks.get('show_about'))
        
        # 绑定快捷键
        self._bind_shortcuts(menu_callbacks)
    
    def _bind_shortcuts(self, callbacks):
        """绑定快捷键"""
        self.root.bind('<Control-o>', lambda e: callbacks.get('browse_source')())
        self.root.bind('<Control-d>', lambda e: callbacks.get('browse_target')())
        self.root.bind('<Control-e>', lambda e: callbacks.get('open_output_folder')())
        self.root.bind('<Control-q>', lambda e: callbacks.get('quit')())
        self.root.bind('<Control-s>', lambda e: callbacks.get('save_config')())
        self.root.bind('<F5>', lambda e: callbacks.get('refresh_files')())
        self.root.bind('<Control-r>', lambda e: callbacks.get('start_compress')())
        self.root.bind('<Control-p>', lambda e: callbacks.get('pause_compress')())
        self.root.bind('<Control-t>', lambda e: callbacks.get('stop_compress')())

