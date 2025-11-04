"""
批量文件压缩工具 v2.0 - 主程序
模块化重构版本，支持暂停/恢复、断点续传、压缩预览和历史记录
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import time
import concurrent.futures
import math
import logging
import datetime
import json
import shutil
import tempfile
from pathlib import Path
from PIL import Image, ImageTk

# 尝试导入opencv用于视频预览
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# 导入自定义模块
from config_manager import ConfigManager
from file_processor import FileProcessor
from image_compressor import ImageCompressor
from video_compressor import VideoCompressor
from compression_history import CompressionHistory
from ffmpeg_manager import FFmpegManager
try:
    from web_server import WebServer
    HAS_WEB_SERVER = True
except ImportError:
    HAS_WEB_SERVER = False

# 延迟导入，避免循环依赖
try:
    from file_info import FileInfo
except ImportError:
    # 如果没有file_info模块，使用字典作为兼容
    class FileInfo:
        @staticmethod
        def from_dict(d):
            return d
        @staticmethod
        def to_dict(f):
            return f if isinstance(f, dict) else f.to_dict() if hasattr(f, 'to_dict') else {}

# 导入统一路径工具
from path_utils import get_v2_dir, get_app_path, get_log_dir

# 获取路径（使用统一路径工具）
app_path = get_app_path()
v2_dir = get_v2_dir()


class TextHandler(logging.Handler):
    """自定义日志处理器，将日志输出到Text组件"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.max_lines = 1000  # 最大行数，防止日志过多导致界面卡顿
    
    def emit(self, record):
        """发送日志记录到Text组件"""
        try:
            msg = self.format(record)
            # 在GUI线程中更新文本组件
            self.text_widget.after(0, self._append_text, msg)
        except Exception:
            pass
    
    def _append_text(self, msg):
        """在GUI线程中追加文本"""
        try:
            self.text_widget.insert(tk.END, msg + '\n')
            # 限制最大行数
            lines = int(self.text_widget.index('end-1c').split('.')[0])
            if lines > self.max_lines:
                self.text_widget.delete('1.0', f'{lines - self.max_lines}.0')
            # 自动滚动到底部
            self.text_widget.see(tk.END)
        except Exception:
            pass


def setup_logging(gui_text_widget=None):
    """
    设置日志系统
    
    Args:
        gui_text_widget: 可选的GUI文本组件，用于显示实时日志
    """
    # 使用统一路径工具获取日志目录
    log_dir = get_log_dir()
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    logger = logging.getLogger('FileCompressor')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    
    # 文件日志
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f'compress_{datetime.datetime.now().strftime("%Y%m%d")}.log'),
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(log_format, datefmt=date_format)
    file_handler.setFormatter(file_formatter)
    
    # 控制台日志
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # GUI日志处理器（如果提供了文本组件）
    if gui_text_widget:
        gui_handler = TextHandler(gui_text_widget)
        gui_handler.setLevel(logging.INFO)
        gui_formatter = logging.Formatter(log_format, datefmt=date_format)
        gui_handler.setFormatter(gui_formatter)
        logger.addHandler(gui_handler)
    
    logging.getLogger('PIL').setLevel(logging.WARNING)
    
    return logger


class CompressionTask:
    """压缩任务类，用于管理单个压缩任务的状态"""
    
    def __init__(self, file_index, file_info):
        self.file_index = file_index
        self.file_info = file_info
        self.status = '等待'  # 等待、进行中、暂停、已完成、失败、已跳过
        self.progress = 0.0
        self.error_message = None


class FileCompressorApp:
    """批量文件压缩工具主应用类（重构版本）"""
    
    def __init__(self, root):
        self.root = root
        
        # 初始化日志（先不添加GUI处理器，稍后在日志窗口创建后添加）
        self.logger = setup_logging()
        
        # 初始化管理器
        self.config_manager = ConfigManager()
        self.config_manager.load()
        self.file_processor = FileProcessor(self.logger)
        self.image_compressor = ImageCompressor(self.config_manager, self.logger)
        self.video_compressor = VideoCompressor(self.config_manager, self.logger)
        self.history_manager = CompressionHistory(logger=self.logger)
        self.ffmpeg_manager = FFmpegManager(self.logger)
        
        # Web服务器（如果可用）
        self.web_server = None
        self.web_server_running = False
        if HAS_WEB_SERVER:
            try:
                self.web_server = WebServer(logger=self.logger, host='0.0.0.0', port=5000)
            except Exception as e:
                self.logger.warning(f"初始化Web服务器失败: {e}")
                self.web_server = None
        
        # 日志窗口相关
        self.log_window = None
        self.log_text = None
        
        # 创建主窗口
        self._create_main_window()
        
        # UI变量
        self.source_dir = tk.StringVar(value=self.config_manager.get('source_dir', ''))
        self.target_dir = tk.StringVar(value=self.config_manager.get('target_dir', ''))
        self.progress_var = tk.DoubleVar()
        self.status_var = tk.StringVar(value="准备就绪")
        
        # 文件列表
        self.file_list = []
        self.file_index_map = {}  # 使用相对路径或索引作为键，减少内存
        self.folder_nodes = {}  # 文件夹节点映射，用于维护树形结构
        self.selected_files = set()  # 使用set而非list，节省内存
        self.excluded_files = set()  # 使用set而非list，节省内存
        self.file_filter_type = tk.StringVar(value="全部")
        
        # 内存优化标志
        self._memory_optimized = False
        
        # 压缩状态
        self.is_compressing = False
        self.is_paused = False
        self.stop_requested = False
        self.scanning_files = False
        
        # 压缩任务列表
        self.compression_tasks = {}  # file_index -> CompressionTask
        
        # 统计信息
        self.total_original_size = 0
        self.total_estimated_size = 0
        self.total_compressed_size = 0
        
        # 支持的文件格式（媒体文件）
        self.supported_image_exts = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp']
        self.supported_video_exts = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.m4v', '.webm', '.3gp']
        self.supported_exts = self.supported_image_exts + self.supported_video_exts
        
        # 是否默认排除非媒体文件
        self.auto_exclude_non_media = self.config_manager.get('auto_exclude_non_media', True)
        self.compress_start_time = 0
        self.paused_time = 0
        self.total_paused_duration = 0
        
        # 断点续传数据
        # 使用当前文件所在目录（v2目录）下的checkpoint.json
        self.checkpoint_file = os.path.join(v2_dir, 'checkpoint.json')
        
        # 创建界面
        self._create_widgets()
        
        # 绑定事件
        self._bind_events()
        
        # 加载断点续传数据（如果存在）
        self._load_checkpoint()
        
        # 自动刷新文件列表
        self.root.after(100, self._auto_refresh_file_list)
        
        # 检查FFmpeg（延迟执行，确保窗口已创建）
        self.root.after(500, self._check_ffmpeg_on_startup)
    
    def _create_main_window(self):
        """创建主窗口"""
        self.root.title("批量文件压缩工具 v2.0")
        self.root.geometry("1000x800")
        self.root.resizable(True, True)
        
        # 设置现代配色方案
        self.style = ttk.Style()
        try:
            # 尝试使用现代化的主题
            self.style.theme_use('vista')  # Windows Vista主题（如果可用）
        except:
            try:
                self.style.theme_use('clam')  # 备选主题
            except:
                pass
        
        # 自定义配色
        self._apply_custom_colors()
        
        icon_path = os.path.join(app_path, 'icon.ico')
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)
    
    def _center_window(self, window, width=None, height=None):
        """将窗口居中显示在主窗口中央"""
        window.update_idletasks()
        
        # 获取主窗口位置和尺寸
        self.root.update_idletasks()
        main_x = self.root.winfo_x()
        main_y = self.root.winfo_y()
        main_width = self.root.winfo_width()
        main_height = self.root.winfo_height()
        
        # 获取窗口尺寸
        if width is None or height is None:
            window.update_idletasks()
            width = window.winfo_width()
            height = window.winfo_height()
        
        # 计算居中位置
        center_x = main_x + (main_width // 2) - (width // 2)
        center_y = main_y + (main_height // 2) - (height // 2)
        
        # 设置窗口位置
        window.geometry(f"{width}x{height}+{center_x}+{center_y}")
    
    def _check_ffmpeg_on_startup(self):
        """启动时检查FFmpeg"""
        try:
            ffmpeg_path = self.config_manager.get('ffmpeg_path')
            is_available, found_path = self.ffmpeg_manager.check_ffmpeg(ffmpeg_path)
            
            if not is_available:
                # FFmpeg不可用，提示用户下载
                self._prompt_download_ffmpeg()
            else:
                # FFmpeg可用，更新配置路径（如果在系统PATH中找到）
                if found_path != ffmpeg_path and found_path != self.config_manager.settings.get('ffmpeg_path'):
                    self.config_manager.set('ffmpeg_path', found_path)
                    # 更新video_compressor的ffmpeg路径
                    self.video_compressor.ffmpeg_path = found_path
                    self.logger.info(f"已自动更新FFmpeg路径: {found_path}")
        except Exception as e:
            self.logger.error(f"检查FFmpeg时出错: {e}")
    
    def _prompt_download_ffmpeg(self):
        """提示用户下载FFmpeg"""
        if sys.platform != 'win32':
            # 非Windows系统，提示手动安装
            messagebox.showinfo(
                "FFmpeg未找到",
                "未检测到FFmpeg，视频压缩功能将不可用。\n\n"
                "请手动安装FFmpeg：\n"
                "1. 访问 https://ffmpeg.org/download.html\n"
                "2. 下载对应系统的FFmpeg\n"
                "3. 解压并添加到系统PATH环境变量中"
            )
            return
        
        # Windows系统，提示自动下载
        result = messagebox.askyesno(
            "FFmpeg未找到",
            "未检测到FFmpeg，视频压缩功能将不可用。\n\n"
            "是否自动从官网下载FFmpeg到bin目录？\n\n"
            "注意：\n"
            "• 下载需要网络连接\n"
            "• 下载的文件约为100MB\n"
            "• 如果下载失败，您可以手动下载并解压到bin目录"
        )
        
        if result:
            self._download_ffmpeg_with_progress()
    
    def _download_ffmpeg_with_progress(self):
        """带进度显示的FFmpeg下载"""
        # 创建下载进度窗口
        download_window = tk.Toplevel(self.root)
        download_window.title("下载FFmpeg")
        download_window.transient(self.root)
        download_window.resizable(False, False)
        self._center_window(download_window, 500, 200)
        download_window.grab_set()  # 模态窗口
        
        main_frame = ttk.Frame(download_window, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 标题
        title_label = ttk.Label(main_frame, text="正在下载FFmpeg...", font=('Segoe UI', 11, 'bold'))
        title_label.pack(pady=(0, 10))
        
        # 进度条
        progress_var = tk.DoubleVar()
        progress_bar = ttk.Progressbar(main_frame, variable=progress_var, maximum=100, length=400)
        progress_bar.pack(pady=10)
        
        # 状态标签
        status_label = ttk.Label(main_frame, text="准备下载...", font=('Segoe UI', 9))
        status_label.pack(pady=5)
        
        # 取消按钮（暂时禁用）
        cancel_button = ttk.Button(main_frame, text="取消", command=lambda: download_window.destroy())
        cancel_button.pack(pady=10)
        
        # 下载进度回调
        def update_progress(current, total):
            if total > 0:
                percent = min(current * 100 / total, 100)
                progress_var.set(percent)
                status_label.config(text=f"下载中: {FileProcessor.format_size(current)} / {FileProcessor.format_size(total)}")
                download_window.update()
        
        # 在后台线程中下载
        def download_thread():
            try:
                success, error_msg = self.ffmpeg_manager.download_ffmpeg(update_progress)
                
                # 在主线程中更新UI
                download_window.after(0, lambda: self._handle_download_result(
                    download_window, success, error_msg
                ))
            except Exception as e:
                download_window.after(0, lambda: self._handle_download_result(
                    download_window, False, str(e)
                ))
        
        # 启动下载线程
        threading.Thread(target=download_thread, daemon=True).start()
    
    def _handle_download_result(self, window, success, error_msg):
        """处理下载结果"""
        window.grab_release()
        
        if success:
            messagebox.showinfo(
                "下载成功",
                "FFmpeg已成功下载到bin目录！\n\n"
                "程序将自动更新配置并重启相关组件。"
            )
            
            # 更新配置
            ffmpeg_path = self.ffmpeg_manager.default_ffmpeg_path
            self.config_manager.set('ffmpeg_path', ffmpeg_path)
            self.config_manager.save()
            
            # 更新video_compressor
            self.video_compressor.ffmpeg_path = ffmpeg_path
            
            window.destroy()
        else:
            messagebox.showerror(
                "下载失败",
                f"FFmpeg下载失败：\n{error_msg}\n\n"
                "您可以：\n"
                "1. 检查网络连接后重试\n"
                "2. 手动下载FFmpeg：\n"
                "   - 访问 https://www.gyan.dev/ffmpeg/builds/\n"
                "   - 下载ffmpeg-release-essentials.zip\n"
                "   - 解压并将ffmpeg.exe和ffprobe.exe复制到bin目录"
            )
            window.destroy()
    
    def _apply_custom_colors(self):
        """应用自定义配色方案"""
        try:
            # 配置ttk样式
            self.style.configure('Title.TLabel', 
                                font=('Segoe UI', 11, 'bold'),
                                foreground='#2c3e50')
            self.style.configure('Heading.TLabel',
                                font=('Segoe UI', 10, 'bold'),
                                foreground='#34495e')
            self.style.configure('Info.TLabel',
                                font=('Segoe UI', 9),
                                foreground='#7f8c8d')
            
            # 按钮样式（简化，确保文字清晰）
            self.style.configure('Primary.TButton',
                                font=('Segoe UI', 9),
                                padding=6)
            # 不强制设置颜色，使用系统默认样式以确保可读性
            
            # 进度条样式
            self.style.configure('TProgressbar',
                               background='#3498db',
                               troughcolor='#ecf0f1',
                               borderwidth=0,
                               lightcolor='#3498db',
                               darkcolor='#3498db')
            
            # Treeview样式
            self.style.configure('Treeview',
                               font=('Segoe UI', 9),
                               rowheight=22,
                               fieldbackground='white')
            self.style.configure('Treeview.Heading',
                               font=('Segoe UI', 9, 'bold'),
                               background='#ecf0f1',
                               foreground='#2c3e50')
            
            # LabelFrame样式
            self.style.configure('TLabelframe',
                               font=('Segoe UI', 9, 'bold'),
                               foreground='#34495e')
            self.style.configure('TLabelframe.Label',
                               font=('Segoe UI', 9, 'bold'),
                               foreground='#34495e')
        except Exception as e:
            self.logger.warning(f"应用自定义配色失败: {e}")
    
    def _create_widgets(self):
        """创建界面组件"""
        # 创建菜单栏
        self._create_menu_bar()
        
        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 文件夹选择区域
        self._create_folder_selection(main_frame)
        
        # 压缩设置区域
        self._create_settings_frame(main_frame)
        
        # 进度显示区域（非阻塞，集成在主界面）
        self._create_progress_frame(main_frame)
        
        # 按钮区域
        self._create_button_frame(main_frame)
        
        # 文件列表区域
        self._create_file_list_frame(main_frame)
    
    def _create_menu_bar(self):
        """创建菜单栏"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="选择源文件夹", command=self.browse_source, accelerator="Ctrl+O")
        file_menu.add_command(label="选择目标文件夹", command=self.browse_target, accelerator="Ctrl+D")
        file_menu.add_separator()
        file_menu.add_command(label="打开输出文件夹", command=self.open_output_folder, accelerator="Ctrl+E")
        file_menu.add_separator()
        file_menu.add_command(label="查看历史记录", command=self.show_history)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.quit_application, accelerator="Ctrl+Q")
        
        # 编辑菜单
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="编辑", menu=edit_menu)
        edit_menu.add_command(label="保存设置", command=self.save_settings, accelerator="Ctrl+S")
        edit_menu.add_command(label="刷新文件列表", command=self.refresh_file_list, accelerator="F5")
        
        # 工具菜单
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="工具", menu=tools_menu)
        tools_menu.add_command(label="开始压缩", command=self.start_compression, accelerator="Ctrl+R")
        tools_menu.add_command(label="暂停压缩", command=self.pause_compression, accelerator="Ctrl+P")
        tools_menu.add_command(label="恢复压缩", command=self.resume_compression, accelerator="Ctrl+U")
        tools_menu.add_command(label="停止压缩", command=self.stop_compression, accelerator="Ctrl+T")
        tools_menu.add_separator()
        tools_menu.add_command(label="压缩预览", command=self.preview_compression)
        tools_menu.add_separator()
        if HAS_WEB_SERVER and self.web_server:
            tools_menu.add_command(label="启动Web服务", command=self.start_web_server)
            tools_menu.add_command(label="停止Web服务", command=self.stop_web_server)
            tools_menu.add_separator()
        tools_menu.add_command(label="实时日志", command=self.show_log_window)
        
        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_separator()
        help_menu.add_command(label="关于", command=self.show_about)
        
        # 绑定快捷键
        self.root.bind('<Control-o>', lambda e: self.browse_source())
        self.root.bind('<Control-d>', lambda e: self.browse_target())
        self.root.bind('<Control-e>', lambda e: self.open_output_folder())
        self.root.bind('<Control-q>', lambda e: self.quit_application())
        self.root.bind('<Control-s>', lambda e: self.save_settings())
        self.root.bind('<F5>', lambda e: self.refresh_file_list())
        self.root.bind('<Control-r>', lambda e: self.start_compression())
        self.root.bind('<Control-p>', lambda e: self.pause_compression())
        self.root.bind('<Control-u>', lambda e: self.resume_compression())
        self.root.bind('<Control-t>', lambda e: self.stop_compression())
    
    def _create_folder_selection(self, parent):
        """创建文件夹选择区域（现代化设计）"""
        # 源文件夹
        source_frame = ttk.LabelFrame(parent, text="源文件夹", padding="12")
        source_frame.pack(fill=tk.X, pady=(0, 12))
        
        source_entry_frame = ttk.Frame(source_frame)
        source_entry_frame.pack(fill=tk.X, pady=5)
        
        ttk.Entry(source_entry_frame, textvariable=self.source_dir, width=60, font=('Segoe UI', 9)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(source_entry_frame, text="浏览", command=self.browse_source, style='Primary.TButton').pack(side=tk.RIGHT)
        ttk.Button(source_entry_frame, text="刷新", command=self.refresh_file_list, style='Primary.TButton').pack(side=tk.RIGHT, padx=(8, 0))
        
        # 目标文件夹
        target_frame = ttk.LabelFrame(parent, text="目标文件夹", padding="12")
        target_frame.pack(fill=tk.X, pady=(0, 12))
        
        target_entry_frame = ttk.Frame(target_frame)
        target_entry_frame.pack(fill=tk.X, pady=5)
        
        ttk.Entry(target_entry_frame, textvariable=self.target_dir, width=60, font=('Segoe UI', 9)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(target_entry_frame, text="浏览", command=self.browse_target, style='Primary.TButton').pack(side=tk.RIGHT)
    
    def _create_settings_frame(self, parent):
        """创建压缩设置区域（现代化设计）"""
        settings_frame = ttk.LabelFrame(parent, text="压缩设置", padding="12")
        settings_frame.pack(fill=tk.X, pady=(0, 12))
        
        # 第一行：照片质量和分辨率设置
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill=tk.X, pady=6)
        
        ttk.Label(row1, text="照片质量:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=6)
        self.photo_quality_entry = ttk.Entry(row1, width=8, font=('Segoe UI', 9))
        self.photo_quality_entry.insert(0, str(self.config_manager.get('photo_quality', 85)))
        self.photo_quality_entry.pack(side=tk.LEFT, padx=4)
        ttk.Label(row1, text="(0-100)", font=('Segoe UI', 8), foreground='gray').pack(side=tk.LEFT)
        
        ttk.Label(row1, text="分辨率预设:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=(12, 6))
        self.resolution_preset_combo = ttk.Combobox(
            row1,
            values=list(self.config_manager.resolution_presets.keys()),
            width=20,
            font=('Segoe UI', 9),
            state="readonly"
        )
        self.resolution_preset_combo.set(self.config_manager.get('resolution_preset', '自定义'))
        self.resolution_preset_combo.pack(side=tk.LEFT, padx=4)
        self.resolution_preset_combo.bind("<<ComboboxSelected>>", self._on_resolution_preset_changed)
        
        # 分辨率自定义输入框（动态显示）
        self.resolution_custom_frame = ttk.Frame(row1)
        self.resolution_custom_frame.pack(side=tk.LEFT, padx=(4, 0))
        
        ttk.Label(self.resolution_custom_frame, text="宽:", font=('Segoe UI', 8)).pack(side=tk.LEFT)
        self.max_photo_width_entry = ttk.Entry(self.resolution_custom_frame, width=6, font=('Segoe UI', 9))
        self.max_photo_width_entry.insert(0, str(self.config_manager.get('max_photo_width', 2000)))
        self.max_photo_width_entry.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(self.resolution_custom_frame, text="x", font=('Segoe UI', 8)).pack(side=tk.LEFT)
        
        ttk.Label(self.resolution_custom_frame, text="高:", font=('Segoe UI', 8)).pack(side=tk.LEFT)
        self.max_photo_height_entry = ttk.Entry(self.resolution_custom_frame, width=6, font=('Segoe UI', 9))
        self.max_photo_height_entry.insert(0, str(self.config_manager.get('max_photo_height', 2000)))
        self.max_photo_height_entry.pack(side=tk.LEFT, padx=2)
        
        # 第二行：视频设置
        row2_video = ttk.Frame(settings_frame)
        row2_video.pack(fill=tk.X, pady=6)
        
        ttk.Label(row2_video, text="视频CRF:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=6)
        self.video_crf_entry = ttk.Entry(row2_video, width=8, font=('Segoe UI', 9))
        self.video_crf_entry.insert(0, str(self.config_manager.get('video_crf', 23)))
        self.video_crf_entry.pack(side=tk.LEFT, padx=4)
        ttk.Label(row2_video, text="(18-28)", font=('Segoe UI', 8), foreground='gray').pack(side=tk.LEFT)
        
        ttk.Label(row2_video, text="预设:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=(12, 6))
        self.video_preset_combo = ttk.Combobox(
            row2_video, 
            values=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
            width=12,
            font=('Segoe UI', 9),
            state="readonly"
        )
        self.video_preset_combo.set(self.config_manager.get('video_preset', 'medium'))
        self.video_preset_combo.pack(side=tk.LEFT, padx=4)
        
        # 第三行：编码模式和GPU设置
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill=tk.X, pady=6)
        
        # 更新分辨率显示状态
        self._update_resolution_custom_display()
        
        ttk.Label(row2, text="编码模式:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=6)
        self.encode_mode_combo = ttk.Combobox(
            row2,
            values=["CPU", "AMD GPU", "Nvidia GPU"],
            width=14,
            font=('Segoe UI', 9),
            state="readonly"
        )
        gpu_mode = self.config_manager.get('use_gpu', 'cpu')
        mode_index = 0 if gpu_mode == 'cpu' else (1 if gpu_mode == 'amd' else 2)
        self.encode_mode_combo.current(mode_index)
        self.encode_mode_combo.pack(side=tk.LEFT, padx=4)
        
        # GPU设置框架（动态显示）
        self.gpu_settings_frame = ttk.Frame(row2)
        self.gpu_settings_frame.pack(side=tk.LEFT, padx=12)
        self._update_gpu_settings_display()
        
        self.encode_mode_combo.bind("<<ComboboxSelected>>", lambda e: self._update_gpu_settings_display())
        
        # 保存设置按钮（美化）
        save_btn = ttk.Button(row2, text="保存设置", command=self.save_settings, style='Primary.TButton')
        save_btn.pack(side=tk.LEFT, padx=12)
        
        # 添加自动排除非媒体文件选项
        auto_exclude_frame = ttk.Frame(settings_frame)
        auto_exclude_frame.pack(fill=tk.X, pady=4)
        
        self.auto_exclude_var = tk.BooleanVar(value=self.auto_exclude_non_media)
        auto_exclude_check = ttk.Checkbutton(
            auto_exclude_frame,
            text="自动排除非媒体文件（默认排除不支持压缩的文件类型）",
            variable=self.auto_exclude_var,
            command=lambda: setattr(self, 'auto_exclude_non_media', self.auto_exclude_var.get())
        )
        auto_exclude_check.pack(side=tk.LEFT, padx=6)
    
    def _on_resolution_preset_changed(self, event=None):
        """分辨率预设改变时的处理"""
        preset = self.resolution_preset_combo.get()
        if preset in self.config_manager.resolution_presets:
            width, height = self.config_manager.resolution_presets[preset]
            if width is not None and height is not None:
                # 更新自定义输入框
                self.max_photo_width_entry.delete(0, tk.END)
                self.max_photo_width_entry.insert(0, str(width))
                self.max_photo_height_entry.delete(0, tk.END)
                self.max_photo_height_entry.insert(0, str(height))
        
        # 更新自定义输入框的显示状态
        self._update_resolution_custom_display()
    
    def _update_resolution_custom_display(self):
        """更新分辨率自定义输入框的显示状态"""
        # 自定义输入框始终显示，方便用户查看和修改当前分辨率值
        # 当选择预设时，输入框会自动更新为预设值，但用户可以手动修改
        pass
    
    def _update_gpu_settings_display(self):
        """更新GPU设置显示"""
        # 清除现有控件
        for widget in self.gpu_settings_frame.winfo_children():
            widget.destroy()
        
        mode = self.encode_mode_combo.get()
        
        if mode == "AMD GPU":
            ttk.Label(self.gpu_settings_frame, text="编码器:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=4)
            self.amd_encoder_entry = ttk.Entry(self.gpu_settings_frame, width=14, font=('Segoe UI', 9))
            self.amd_encoder_entry.insert(0, self.config_manager.get('amd_encoder', 'h264_amf'))
            self.amd_encoder_entry.pack(side=tk.LEFT, padx=4)
            
            ttk.Label(self.gpu_settings_frame, text="比特率:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=4)
            self.amd_bitrate_entry = ttk.Entry(self.gpu_settings_frame, width=10, font=('Segoe UI', 9))
            self.amd_bitrate_entry.insert(0, self.config_manager.get('amd_video_bitrate', '5000k'))
            self.amd_bitrate_entry.pack(side=tk.LEFT, padx=4)
        elif mode == "Nvidia GPU":
            ttk.Label(self.gpu_settings_frame, text="编码器:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=4)
            self.nvidia_encoder_entry = ttk.Entry(self.gpu_settings_frame, width=14, font=('Segoe UI', 9))
            self.nvidia_encoder_entry.insert(0, self.config_manager.get('nvidia_encoder', 'h264_nvenc'))
            self.nvidia_encoder_entry.pack(side=tk.LEFT, padx=4)
            
            ttk.Label(self.gpu_settings_frame, text="预设:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=4)
            self.nvidia_preset_entry = ttk.Entry(self.gpu_settings_frame, width=10, font=('Segoe UI', 9))
            self.nvidia_preset_entry.insert(0, self.config_manager.get('nvidia_preset', 'p4'))
            self.nvidia_preset_entry.pack(side=tk.LEFT, padx=4)
            
            ttk.Label(self.gpu_settings_frame, text="比特率:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=4)
            self.nvidia_bitrate_entry = ttk.Entry(self.gpu_settings_frame, width=10, font=('Segoe UI', 9))
            self.nvidia_bitrate_entry.insert(0, self.config_manager.get('nvidia_video_bitrate', '5000k'))
            self.nvidia_bitrate_entry.pack(side=tk.LEFT, padx=4)
    
    def _create_progress_frame(self, parent):
        """创建进度显示区域（非阻塞，现代化设计）"""
        progress_frame = ttk.LabelFrame(parent, text="压缩进度", padding="12")
        progress_frame.pack(fill=tk.X, pady=(0, 12))
        
        # 进度条（美化）
        progress_bar_frame = ttk.Frame(progress_frame)
        progress_bar_frame.pack(fill=tk.X, padx=4, pady=6)
        
        self.progress_bar = ttk.Progressbar(
            progress_bar_frame, 
            variable=self.progress_var, 
            length=100, 
            mode='determinate',
            style='TProgressbar'
        )
        self.progress_bar.pack(fill=tk.X)
        
        # 状态和统计信息（美化）
        stats_frame = ttk.Frame(progress_frame)
        stats_frame.pack(fill=tk.X, padx=4, pady=4)
        
        self.status_label = ttk.Label(
            stats_frame, 
            textvariable=self.status_var, 
            font=('Segoe UI', 9),
            style='Info.TLabel'
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # 统计信息标签
        self.stats_label = ttk.Label(
            stats_frame, 
            text="", 
            font=('Segoe UI', 9, 'bold'),
            foreground='#2c3e50'
        )
        self.stats_label.pack(side=tk.RIGHT)
        
        # 时间信息（美化）
        self.time_label = ttk.Label(
            progress_frame, 
            text="", 
            font=('Segoe UI', 8),
            foreground='#95a5a6'
        )
        self.time_label.pack(fill=tk.X, padx=4, pady=2)
    
    def _create_button_frame(self, parent):
        """创建按钮区域（现代化设计）"""
        button_frame = ttk.Frame(parent)
        button_frame.pack(fill=tk.X, pady=(0, 12))
        
        # 左侧按钮组（主要操作）
        left_buttons = ttk.Frame(button_frame)
        left_buttons.pack(side=tk.LEFT)
        
        # 主要按钮样式（简化，确保文字清晰可读）
        self.style.configure('Success.TButton',
                            font=('Segoe UI', 9, 'bold'),
                            padding=8)
        # 使用系统默认颜色，确保文字清晰
        
        # 警告按钮样式
        self.style.configure('Warning.TButton',
                            font=('Segoe UI', 9),
                            padding=8)
        # 使用系统默认颜色，确保文字清晰
        
        # 危险按钮样式
        self.style.configure('Danger.TButton',
                            font=('Segoe UI', 9),
                            padding=8)
        # 使用系统默认颜色，确保文字清晰
        
        self.start_button = ttk.Button(
            left_buttons, 
            text="开始压缩", 
            command=self.start_compression,
            style='Success.TButton'
        )
        self.start_button.pack(side=tk.LEFT, padx=6)
        
        self.pause_button = ttk.Button(
            left_buttons, 
            text="暂停", 
            command=self.pause_compression, 
            state=tk.DISABLED,
            style='Warning.TButton'
        )
        self.pause_button.pack(side=tk.LEFT, padx=4)
        
        self.resume_button = ttk.Button(
            left_buttons, 
            text="恢复", 
            command=self.resume_compression, 
            state=tk.DISABLED,
            style='Success.TButton'
        )
        self.resume_button.pack(side=tk.LEFT, padx=4)
        
        self.stop_button = ttk.Button(
            left_buttons, 
            text="停止", 
            command=self.stop_compression, 
            state=tk.DISABLED,
            style='Danger.TButton'
        )
        self.stop_button.pack(side=tk.LEFT, padx=4)
        
        # 右侧按钮组（辅助功能）
        right_buttons = ttk.Frame(button_frame)
        right_buttons.pack(side=tk.RIGHT)
        
        ttk.Button(right_buttons, text="预览", command=self.preview_compression, style='Primary.TButton').pack(side=tk.LEFT, padx=4)
        ttk.Button(right_buttons, text="历史", command=self.show_history, style='Primary.TButton').pack(side=tk.LEFT, padx=4)
        ttk.Button(right_buttons, text="打开输出", command=self.open_output_folder, style='Primary.TButton').pack(side=tk.LEFT, padx=4)
        
        # Web服务器按钮（如果可用）
        if HAS_WEB_SERVER and self.web_server:
            ttk.Separator(right_buttons, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=4)
            self.web_server_button = ttk.Button(
                right_buttons, 
                text="启动Web服务", 
                command=self.toggle_web_server,
                style='Primary.TButton'
            )
            self.web_server_button.pack(side=tk.LEFT, padx=4)
        
        # 日志按钮
        ttk.Separator(right_buttons, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=4)
        ttk.Button(right_buttons, text="实时日志", command=self.show_log_window, style='Primary.TButton').pack(side=tk.LEFT, padx=4)
    
    def _create_file_list_frame(self, parent):
        """创建文件列表区域（现代化设计）"""
        list_frame = ttk.LabelFrame(parent, text="文件列表", padding="12")
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        # 工具栏（美化）
        toolbar = ttk.Frame(list_frame)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        
        ttk.Label(toolbar, text="过滤:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=6)
        filter_combo = ttk.Combobox(
            toolbar, 
            textvariable=self.file_filter_type, 
            values=["全部", "图片", "视频", "其他"], 
            width=10, 
            state="readonly",
            font=('Segoe UI', 9)
        )
        filter_combo.pack(side=tk.LEFT, padx=4)
        filter_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_file_filter())
        
        # 分隔符
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12, pady=4)
        
        ttk.Label(toolbar, text="批量操作:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="全选", command=self._select_all_files, style='Primary.TButton', width=8).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="全不选", command=self._deselect_all_files, style='Primary.TButton', width=8).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="反选", command=self._invert_selection, style='Primary.TButton', width=8).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="排除", command=self._exclude_selected, style='Warning.TButton', width=8).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="取消排除", command=self._unexclude_all, style='Primary.TButton', width=10).pack(side=tk.LEFT, padx=3)
        
        # Treeview
        tree_frame = ttk.Frame(list_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        y_scrollbar = ttk.Scrollbar(tree_frame)
        y_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        x_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)
        x_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.file_listbox = ttk.Treeview(
            tree_frame,
            columns=("name", "size", "estimated_size", "status"),
            yscrollcommand=y_scrollbar.set,
            xscrollcommand=x_scrollbar.set,
            selectmode=tk.EXTENDED
        )
        
        self.file_listbox.heading("#0", text="路径")
        self.file_listbox.heading("name", text="文件名")
        self.file_listbox.heading("size", text="原始大小")
        self.file_listbox.heading("estimated_size", text="预估压缩后大小")
        self.file_listbox.heading("status", text="状态")
        
        self.file_listbox.column("#0", width=90)
        self.file_listbox.column("name", width=250)
        self.file_listbox.column("size", width=80, anchor=tk.E)
        self.file_listbox.column("estimated_size", width=100, anchor=tk.E)
        self.file_listbox.column("status", width=100, anchor=tk.CENTER)
        
        y_scrollbar.config(command=self.file_listbox.yview)
        x_scrollbar.config(command=self.file_listbox.xview)
        self.file_listbox.pack(fill=tk.BOTH, expand=True)
    
    def _bind_events(self):
        """绑定事件"""
        pass
    
    def _auto_refresh_file_list(self):
        """自动刷新文件列表"""
        source = self.source_dir.get()
        if source and os.path.isdir(source):
            self.root.after(500, self.refresh_file_list)
    
    # 以下是需要实现的方法（继续实现中...）
    def browse_source(self):
        """浏览选择源文件夹"""
        directory = filedialog.askdirectory()
        if directory:
            normalized_dir = FileProcessor.normalize_path(directory)
            if not normalized_dir:
                messagebox.showerror("错误", "无效的路径，请重新选择")
                return
            
            has_permission, error_msg = FileProcessor.check_path_permissions(normalized_dir, need_read=True, need_write=False)
            if not has_permission:
                messagebox.showerror("错误", error_msg)
                return
            
            self.source_dir.set(normalized_dir)
            self.config_manager.set('source_dir', normalized_dir)
            self.logger.info(f"选择源文件夹: {normalized_dir}")
            
            # 自动设置目标文件夹
            parent_dir = os.path.dirname(normalized_dir)
            default_target = os.path.join(parent_dir, f"{os.path.basename(normalized_dir)}_{self.config_manager.get('output_folder', 'compressed')}")
            self.target_dir.set(default_target)
            self.refresh_file_list()
    
    def browse_target(self):
        """浏览选择目标文件夹"""
        directory = filedialog.askdirectory()
        if directory:
            normalized_dir = FileProcessor.normalize_path(directory)
            if not normalized_dir:
                messagebox.showerror("错误", "无效的路径，请重新选择")
                return
            
            has_permission, error_msg = FileProcessor.check_path_permissions(normalized_dir, need_read=False, need_write=True)
            if not has_permission:
                messagebox.showerror("错误", error_msg)
                return
            
            self.target_dir.set(normalized_dir)
            self.config_manager.set('target_dir', normalized_dir)
            self.logger.info(f"选择目标文件夹: {normalized_dir}")
            if self.source_dir.get():
                self.refresh_file_list()
    
    def refresh_file_list(self):
        """刷新文件列表（后台线程）"""
        if not self.file_listbox:
            return
        
        if self.scanning_files:
            messagebox.showinfo("提示", "正在扫描文件中，请稍候...")
            return
        
        # 清空列表
        for item in self.file_listbox.get_children():
            self.file_listbox.delete(item)
        
        self.file_list = []
        self.file_index_map = {}
        self.folder_nodes = {}  # 重置文件夹节点映射
        self.selected_files.clear()
        self.excluded_files.clear()
        
        # 重置统计数据（修复累加问题）
        self.total_original_size = 0
        self.total_estimated_size = 0
        self.total_compressed_size = 0
        
        source = self.source_dir.get()
        target = self.target_dir.get()
        
        if not source or not os.path.isdir(source):
            messagebox.showinfo("提示", "请先选择有效的源文件夹")
            return
        
        if not target:
            default_target = os.path.join(os.path.dirname(source), 
                                        f"{os.path.basename(source)}_{self.config_manager.get('output_folder', 'compressed')}")
            self.target_dir.set(default_target)
            target = default_target
        
        self.status_var.set("正在后台扫描文件...")
        self.scanning_files = True
        
        scan_thread = threading.Thread(target=self._scan_files_thread, args=(source, target), daemon=True)
        scan_thread.start()
    
    def _scan_files_thread(self, source, target):
        """后台线程扫描文件"""
        try:
            file_paths = []
            for root_dir, _, files in os.walk(source):
                for file in files:
                    file_paths.append((root_dir, file))
            
            if len(file_paths) == 0:
                self.root.after(0, lambda: messagebox.showinfo("提示", "源文件夹中没有找到文件"))
                self.root.after(0, lambda: self.status_var.set("扫描完成，但未找到文件"))
                self.scanning_files = False
                return
            
            batch_size = 50
            processed_files = 0
            folder_nodes = {}  # 在扫描线程中维护文件夹节点状态
            batch_data = []
            
            # 内存优化：使用更紧凑的数据结构
            # 只在需要时计算estimated_size，减少初始内存占用
            calculate_size = self.config_manager.get('calculate_size', True)
            
            for root_dir, file in file_paths:
                rel_path = os.path.relpath(root_dir, source)
                file_ext = os.path.splitext(file)[1].lower()
                
                try:
                    file_size = os.path.getsize(os.path.join(root_dir, file))
                except Exception as e:
                    file_size = 0
                    self.logger.warning(f"无法获取文件大小: {os.path.join(root_dir, file)}, 错误: {e}")
                
                # 估算压缩后大小（延迟计算，可选）
                estimated_size = 0
                if calculate_size:
                    source_file = os.path.join(root_dir, file)
                    if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
                        estimated_size = self.file_processor.estimate_image_size(
                            source_file, file_ext, file_size, self.config_manager
                        )
                    elif file_ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.m4v']:
                        estimated_size = self.file_processor.estimate_video_size(
                            source_file, file_ext, file_size, self.config_manager, 
                            self.config_manager.get('ffmpeg_path')
                        )
                    else:
                        estimated_size = file_size
                
                # 检查是否为非媒体文件，默认自动排除
                initial_status = '等待压缩'
                is_non_media = file_ext not in self.supported_exts
                if self.auto_exclude_non_media and is_non_media:
                    initial_status = '已排除'
                
                # 使用紧凑的FileInfo结构（如果可用），否则使用字典
                try:
                    file_info = FileInfo(
                        source_dir=source,
                        target_dir=target,
                        rel_path=rel_path,
                        file_name=file,
                        file_ext=file_ext,
                        file_size=file_size,
                        estimated_size=estimated_size,
                        actual_size=0,
                        status=initial_status
                    )
                except:
                    # 兼容旧代码，使用字典
                    file_info = {
                        'source_dir': source,
                        'target_dir': target,
                        'rel_path': rel_path,
                        'file_name': file,
                        'file_ext': file_ext,
                        'file_size': file_size,
                        'estimated_size': estimated_size,
                        'status': initial_status,
                        'actual_size': 0
                    }
                
                # 如果是非媒体文件且自动排除，记录到排除列表
                if initial_status == '已排除':
                    # 将在_update_file_list_batch中添加到排除列表
                    pass
                
                batch_data.append((file_info, rel_path))
                processed_files += 1
                
                if processed_files % batch_size == 0 or processed_files == len(file_paths):
                    progress = (processed_files / len(file_paths)) * 100
                    # 使用实例变量维护folder_nodes状态
                    batch_data_copy = batch_data.copy()
                    self.root.after(0, lambda bd=batch_data_copy, p=progress: 
                                   self._update_file_list_batch(bd, p))
                    batch_data = []
            
            self.root.after(0, self._finish_file_scan)
            
        except Exception as e:
            error_msg = f"扫描文件时出错: {str(e)}"
            self.logger.error(error_msg)
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
            self.root.after(0, lambda: self.status_var.set(f"扫描文件出错: {str(e)}"))
        finally:
            self.scanning_files = False
    
    def _update_file_list_batch(self, batch_data, progress):
        """批量更新文件列表"""
        try:
            for file_info, rel_path in batch_data:
                # 兼容FileInfo和字典两种格式
                if hasattr(file_info, 'rel_path'):
                    rel_path = file_info.rel_path
                    file_name = file_info.file_name
                    file_size = file_info.file_size
                    estimated_size = file_info.estimated_size
                    source_path = file_info.source_path
                else:
                    rel_path = file_info.get('rel_path', rel_path)
                    file_name = file_info.get('file_name', '')
                    file_size = file_info.get('file_size', 0)
                    estimated_size = file_info.get('estimated_size', 0)
                    source_path = file_info.get('source_path', '') or \
                                 os.path.join(file_info.get('source_dir', ''), rel_path, file_name) if rel_path != '.' else \
                                 os.path.join(file_info.get('source_dir', ''), file_name)
                
                if rel_path == '.':
                    parent_node = ''
                else:
                    parent_parts = rel_path.split(os.sep)
                    current_path = ''
                    for part in parent_parts:
                        if current_path:
                            current_path = os.path.join(current_path, part)
                        else:
                            current_path = part
                        
                        # 使用实例变量维护文件夹节点状态
                        if current_path not in self.folder_nodes:
                            parent_node = self.folder_nodes.get(os.path.dirname(current_path) if os.path.dirname(current_path) else '', '')
                            folder_node = self.file_listbox.insert(parent_node, 'end', text=part, open=True)
                            self.folder_nodes[current_path] = folder_node
                    
                    parent_node = self.folder_nodes[rel_path]
                
                # 获取状态
                if hasattr(file_info, 'status'):
                    file_status = file_info.status
                else:
                    file_status = file_info.get('status', '等待压缩')
                
                # 检查是否为非媒体文件且自动排除
                file_index = len(self.file_list)
                if file_status == '已排除':
                    self.excluded_files.add(file_index)
                
                self.file_list.append(file_info)
                # 只有未排除的文件才计入总大小
                if file_status != '已排除':
                    self.total_original_size += file_size
                    self.total_estimated_size += estimated_size
                
                item_id = self.file_listbox.insert(
                    parent_node,
                    'end',
                    text='',
                    values=(
                        file_name,
                        FileProcessor.format_size(file_size),
                        FileProcessor.format_size(estimated_size) if estimated_size > 0 else '-',
                        file_status
                    )
                )
                
                # 使用相对路径作为键，减少内存占用
                self.file_index_map[source_path] = item_id
                
                # 如果已排除，设置灰色样式
                if file_status == '已排除':
                    self.file_listbox.tag_configure('excluded', foreground='gray')
                    self.file_listbox.item(item_id, tags=('excluded',))
                
                self.status_var.set(f"正在扫描文件... ({int(progress)}%)")
        except Exception as e:
            self.logger.error(f"更新文件列表批次时出错: {str(e)}")
    
    def _finish_file_scan(self):
        """完成文件扫描"""
        try:
            if self.total_original_size > 0:
                compression_rate = (1 - self.total_estimated_size / self.total_original_size) * 100
            else:
                compression_rate = 0
            
            status_msg = f"共找到 {len(self.file_list)} 个文件 | 原大小: {FileProcessor.format_size(self.total_original_size)} | 预计压缩后: {FileProcessor.format_size(self.total_estimated_size)} | 预计压缩率: {compression_rate:.2f}%"
            self.status_var.set(status_msg)
            self.logger.info(status_msg)
        except Exception as e:
            error_msg = f"完成文件扫描时出错: {str(e)}"
            self.logger.error(error_msg)
            self.status_var.set(error_msg)
    
    def save_settings(self):
        """保存设置"""
        try:
            # 更新配置
            photo_quality = int(self.photo_quality_entry.get())
            if photo_quality < 0 or photo_quality > 100:
                raise ValueError("照片质量必须在0-100之间")
            
            # 分辨率设置
            resolution_preset = self.resolution_preset_combo.get()
            max_photo_width = int(self.max_photo_width_entry.get())
            max_photo_height = int(self.max_photo_height_entry.get())
            
            if max_photo_width <= 0 or max_photo_height <= 0:
                raise ValueError("分辨率宽度和高度必须大于0")
            
            video_crf = int(self.video_crf_entry.get())
            if video_crf < 18 or video_crf > 28:
                raise ValueError("视频CRF必须在18-28之间")
            
            self.config_manager.set('photo_quality', photo_quality)
            self.config_manager.set('resolution_preset', resolution_preset)
            self.config_manager.set('max_photo_width', max_photo_width)
            self.config_manager.set('max_photo_height', max_photo_height)
            self.config_manager.set('video_crf', video_crf)
            self.config_manager.set('video_preset', self.video_preset_combo.get())
            
            mode = self.encode_mode_combo.get()
            if mode == "CPU":
                self.config_manager.set('use_gpu', 'cpu')
            elif mode == "AMD GPU":
                self.config_manager.set('use_gpu', 'amd')
                if hasattr(self, 'amd_encoder_entry'):
                    self.config_manager.set('amd_encoder', self.amd_encoder_entry.get())
                    self.config_manager.set('amd_video_bitrate', self.amd_bitrate_entry.get())
            elif mode == "Nvidia GPU":
                self.config_manager.set('use_gpu', 'nvidia')
                if hasattr(self, 'nvidia_encoder_entry'):
                    self.config_manager.set('nvidia_encoder', self.nvidia_encoder_entry.get())
                    self.config_manager.set('nvidia_preset', self.nvidia_preset_entry.get())
                    self.config_manager.set('nvidia_video_bitrate', self.nvidia_bitrate_entry.get())
            
            self.config_manager.set('source_dir', self.source_dir.get())
            self.config_manager.set('target_dir', self.target_dir.get())
            self.config_manager.set('auto_exclude_non_media', self.auto_exclude_non_media)
            self.config_manager.save()
            
            # 刷新图像和视频压缩器的配置
            self.image_compressor = ImageCompressor(self.config_manager, self.logger)
            self.video_compressor = VideoCompressor(self.config_manager, self.logger)
            
            messagebox.showinfo("成功", "设置已保存\n\n注意：如需更新文件列表，请手动点击\"刷新列表\"按钮")
            
            # 不再自动刷新目录，仅在用户手动刷新或更改源文件夹时刷新
        except ValueError as e:
            messagebox.showerror("错误", f"请输入有效的数值: {str(e)}")
        except Exception as e:
            messagebox.showerror("错误", f"保存设置时出错: {str(e)}")
    
    def start_compression(self):
        """开始压缩"""
        source = self.source_dir.get()
        target = self.target_dir.get()
        
        source = FileProcessor.normalize_path(source) if source else None
        target = FileProcessor.normalize_path(target) if target else None
        
        if not source or not os.path.isdir(source):
            messagebox.showerror("错误", "请选择有效的源文件夹")
            return
        
        if not target:
            messagebox.showerror("错误", "请选择有效的目标文件夹")
            return
        
        # 检查权限
        has_permission, error_msg = FileProcessor.check_path_permissions(source, need_read=True, need_write=False)
        if not has_permission:
            messagebox.showerror("错误", f"无法读取源文件夹:\n{error_msg}")
            return
        
        target_exists = os.path.exists(target)
        if target_exists:
            has_permission, error_msg = FileProcessor.check_path_permissions(target, need_read=False, need_write=True)
            if not has_permission:
                messagebox.showerror("错误", f"无法写入目标文件夹:\n{error_msg}")
                return
        
        # 检查FFmpeg
        if not os.path.isfile(self.config_manager.get('ffmpeg_path')):
            messagebox.showerror("错误", f"找不到FFmpeg可执行文件: {self.config_manager.get('ffmpeg_path')}")
            return
        
        if not self.file_list:
            self.refresh_file_list()
            if not self.file_list:
                messagebox.showinfo("提示", "没有找到需要压缩的文件")
                return
        
        # 确定要压缩的文件
        files_to_process = [(i, f) for i, f in enumerate(self.file_list) 
                           if i not in self.excluded_files]
        
        if self.selected_files:
            files_to_process = [(i, f) for i, f in files_to_process 
                               if i in self.selected_files]
        
        if not files_to_process:
            messagebox.showinfo("提示", "没有需要压缩的文件")
            return
        
        # 检查磁盘空间
        if self.total_estimated_size > 0:
            has_space, error_msg = FileProcessor.check_disk_space(target, self.total_estimated_size)
            if not has_space:
                response = messagebox.askyesno("磁盘空间不足", f"{error_msg}\n\n是否仍要继续压缩？")
                if not response:
                    return
        
        # 创建目标文件夹
        if not os.path.exists(target):
            try:
                os.makedirs(target, exist_ok=True)
            except (OSError, PermissionError) as e:
                messagebox.showerror("错误", f"无法创建目标文件夹: {str(e)}")
                return
        
        # 初始化压缩任务
        for file_index, file_info in files_to_process:
            self.compression_tasks[file_index] = CompressionTask(file_index, file_info)
        
        # 更新UI状态
        self.is_compressing = True
        self.is_paused = False
        self.stop_requested = False
        self.start_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL)
        self.resume_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.progress_var.set(0)
        
        # 初始化时间统计
        self.compress_start_time = time.time()
        self.paused_time = 0
        self.total_paused_duration = 0
        
        self.logger.info(f"开始压缩 - 源: {source}, 目标: {target}")
        self.logger.info(f"压缩参数 - 照片质量: {self.config_manager.get('photo_quality')}, "
                        f"视频CRF: {self.config_manager.get('video_crf')}, "
                        f"GPU模式: {self.config_manager.get('use_gpu')}")
        
        # 在新线程中执行压缩
        compression_thread = threading.Thread(target=self._compress_files_thread, args=(files_to_process,), daemon=True)
        compression_thread.start()
        
        # 定期更新进度
        self._update_progress()
    
    def pause_compression(self):
        """暂停压缩"""
        if self.is_compressing and not self.is_paused:
            self.is_paused = True
            self.paused_time = time.time()
            self.pause_button.config(state=tk.DISABLED)
            self.resume_button.config(state=tk.NORMAL)
            self.status_var.set("压缩已暂停...")
            self.logger.info("压缩已暂停")
            messagebox.showinfo("提示", "压缩已暂停，点击\"恢复\"继续")
    
    def resume_compression(self):
        """恢复压缩"""
        if self.is_compressing and self.is_paused:
            self.is_paused = False
            if self.paused_time > 0:
                pause_duration = time.time() - self.paused_time
                self.total_paused_duration += pause_duration
                self.paused_time = 0
            self.pause_button.config(state=tk.NORMAL)
            self.resume_button.config(state=tk.DISABLED)
            self.status_var.set("压缩已恢复...")
            self.logger.info("压缩已恢复")
            self._save_checkpoint()  # 保存断点
    
    def stop_compression(self):
        """停止压缩"""
        self.stop_requested = True
        self.status_var.set("正在停止压缩...")
        
        # 更新所有正在压缩的文件状态
        for task in self.compression_tasks.values():
            if task.status == '进行中':
                task.status = '已停止'
                self._update_file_status(task.file_index, '已停止')
        
        self.logger.info("用户手动停止压缩")
        self._save_checkpoint()  # 保存断点以便续传
    
    def _compress_files_thread(self, files_to_process):
        """压缩文件的主线程"""
        try:
            total_files = len(files_to_process)
            processed_files = 0
            max_workers = max(1, os.cpu_count() // 2)
            
            # 创建目标文件夹结构
            for file_index, file_info in files_to_process:
                # 兼容FileInfo和字典格式
                if hasattr(file_info, 'target_path'):
                    target_path = file_info.target_path
                else:
                    target_path = file_info.get('target_path') or \
                                 os.path.join(file_info.get('target_dir', ''), 
                                             file_info.get('rel_path', ''),
                                             file_info.get('file_name', '')) if file_info.get('rel_path', '') != '.' else \
                                 os.path.join(file_info.get('target_dir', ''), file_info.get('file_name', ''))
                
                target_dir = os.path.dirname(target_path)
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir, exist_ok=True)
            
            def process_single_file(file_index_and_info):
                nonlocal processed_files
                file_index, file_info = file_index_and_info
                task = self.compression_tasks.get(file_index)
                
                if not task:
                    return False
                
                # 检查是否暂停
                while self.is_paused and not self.stop_requested:
                    time.sleep(0.1)
                
                if self.stop_requested:
                    task.status = '已停止'
                    self.root.after(0, lambda idx=file_index: self._update_file_status(idx, '已停止'))
                    return False
                
                task.status = '进行中'
                # 兼容FileInfo和字典格式
                if hasattr(file_info, 'file_name'):
                    file_name = file_info.file_name
                    source_file = file_info.source_path
                    target_file = file_info.target_path
                    file_ext = file_info.file_ext
                else:
                    file_name = file_info.get('file_name', '')
                    source_file = file_info.get('source_path') or \
                                 os.path.join(file_info.get('source_dir', ''), 
                                             file_info.get('rel_path', ''),
                                             file_info.get('file_name', '')) if file_info.get('rel_path', '') != '.' else \
                                 os.path.join(file_info.get('source_dir', ''), file_info.get('file_name', ''))
                    target_file = file_info.get('target_path') or \
                                 os.path.join(file_info.get('target_dir', ''), 
                                             file_info.get('rel_path', ''),
                                             file_info.get('file_name', '')) if file_info.get('rel_path', '') != '.' else \
                                 os.path.join(file_info.get('target_dir', ''), file_info.get('file_name', ''))
                    file_ext = file_info.get('file_ext', '')
                
                self.root.after(0, lambda idx=file_index: self._update_file_status(idx, '压缩中'))
                self.root.after(0, lambda name=file_name: self.status_var.set(f"正在处理: {name}"))
                
                try:
                    # 检查文件是否已存在（断点续传）
                    if os.path.isfile(target_file):
                        actual_size = os.path.getsize(target_file)
                        # 更新文件信息（兼容两种格式）
                        if hasattr(file_info, 'actual_size'):
                            file_info.actual_size = actual_size
                        else:
                            file_info['actual_size'] = actual_size
                        task.status = '已完成'
                        self.root.after(0, lambda idx=file_index, size=actual_size: self._update_file_status(idx, '已完成', size))
                        return True
                    
                    # 根据文件类型处理
                    if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
                        success = self.image_compressor.compress(source_file, target_file)
                    elif file_ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.m4v']:
                        success = self.video_compressor.compress(source_file, target_file)
                    else:
                        target_dir = os.path.dirname(target_file)
                        os.makedirs(target_dir, exist_ok=True)
                        shutil.copy2(source_file, target_file)
                        success = True
                    
                    # 获取实际大小
                    if os.path.isfile(target_file):
                        actual_size = os.path.getsize(target_file)
                        # 更新文件信息（兼容两种格式）
                        if hasattr(file_info, 'actual_size'):
                            file_info.actual_size = actual_size
                        else:
                            file_info['actual_size'] = actual_size
                        task.status = '已完成' if success else '已复制'
                        self.root.after(0, lambda idx=file_index, size=actual_size, s=('已完成' if success else '已复制'): 
                                       self._update_file_status(idx, s, size))
                        return True
                    else:
                        raise FileNotFoundError(f"输出文件未创建: {target_file}")
                    
                except Exception as e:
                    self.logger.error(f"处理文件时出错 ({type(e).__name__}): {source_file}, 错误: {str(e)}")
                    task.status = '失败'
                    task.error_message = str(e)
                    try:
                        target_dir = os.path.dirname(target_file)
                        os.makedirs(target_dir, exist_ok=True)
                        shutil.copy2(source_file, target_file)
                        actual_size = os.path.getsize(target_file)
                        # 更新文件信息（兼容两种格式）
                        if hasattr(file_info, 'actual_size'):
                            file_info.actual_size = actual_size
                        else:
                            file_info['actual_size'] = actual_size
                        self.root.after(0, lambda idx=file_index, size=actual_size: self._update_file_status(idx, '已复制', size))
                    except Exception as copy_error:
                        self.root.after(0, lambda idx=file_index: self._update_file_status(idx, '处理失败'))
                    return False
            
            # 使用线程池处理
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {executor.submit(process_single_file, item): item[0] 
                                  for item in files_to_process}
                
                for future in concurrent.futures.as_completed(future_to_index):
                    if self.stop_requested:
                        # cancel_futures 仅在 Python 3.9+ 支持，需要兼容处理
                        if sys.version_info >= (3, 9):
                            executor.shutdown(wait=False, cancel_futures=True)
                        else:
                            executor.shutdown(wait=False)
                        break
                    
                    processed_files += 1
                    progress_percent = (processed_files / total_files) * 100
                    self.root.after(0, lambda p=progress_percent: self.progress_var.set(p))
            
            if not self.stop_requested:
                # 计算最终统计
                self._finish_compression()
            
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set(f"压缩出错: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("错误", f"压缩过程中出错: {str(e)}"))
        finally:
            self.is_compressing = False
            self.is_paused = False
            self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.pause_button.config(state=tk.DISABLED))
            self.root.after(0, lambda: self.resume_button.config(state=tk.DISABLED))
            self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))
    
    def _finish_compression(self):
        """完成压缩后的处理"""
        # 计算实际压缩率（兼容两种格式）
        total_original = 0
        total_compressed = 0
        for f in self.file_list:
            if hasattr(f, 'status'):
                status = f.status
                file_size = f.file_size if status in ['已完成', '已复制'] else 0
                actual_size = f.actual_size if status in ['已完成', '已复制'] else 0
            else:
                status = f.get('status', '')
                file_size = f.get('file_size', 0) if status in ['已完成', '已复制'] else 0
                actual_size = f.get('actual_size', 0) if status in ['已完成', '已复制'] else 0
            
            if status in ['已完成', '已复制']:
                total_original += file_size
                total_compressed += actual_size
        
        self.total_compressed_size = total_compressed
        
        actual_time = time.time() - self.compress_start_time - self.total_paused_duration
        time_str = self._format_time(actual_time)
        
        if total_original > 0:
            actual_compression_rate = (1 - total_compressed / total_original) * 100
        else:
            actual_compression_rate = 0
        
        # 保存历史记录
        stats = {
            'total_files': len(self.file_list),
            'completed_files': len([f for f in self.file_list 
                                   if (hasattr(f, 'status') and f.status == '已完成') or 
                                      (not hasattr(f, 'status') and f.get('status') == '已完成')]),
            'original_size': total_original,
            'compressed_size': total_compressed,
            'compression_rate': actual_compression_rate,
            'time_taken': actual_time
        }
        
        self.history_manager.add_record(
            self.source_dir.get(),
            self.target_dir.get(),
            stats,
            self.config_manager.get_all()
        )
        
        # 显示统计摘要
        status_message = f"压缩完成！ | 实际用时: {time_str} | 原大小: {FileProcessor.format_size(total_original)} | 压缩后: {FileProcessor.format_size(total_compressed)} | 实际压缩率: {actual_compression_rate:.2f}%"
        self.status_var.set(status_message)
        self.logger.info(status_message)
        
        self.root.after(100, lambda: self._show_compression_summary(
            total_time=time_str,
            compression_rate=actual_compression_rate,
            stats=stats
        ))
        
        # 清理断点文件
        self._clear_checkpoint()
        
        # 内存优化：压缩完成后清理不需要的数据
        self._cleanup_memory()
    
    def _update_file_status(self, file_index, status, actual_size=None):
        """更新文件状态"""
        if file_index >= len(self.file_list):
            return
        
        file_info = self.file_list[file_index]
        
        # 更新状态（兼容两种格式）
        if hasattr(file_info, 'status'):
            file_info.status = status
            source_path = file_info.source_path
        else:
            file_info['status'] = status
            source_path = file_info.get('source_path')
            if not source_path:
                # 构建source_path
                source_dir = file_info.get('source_dir', '')
                rel_path = file_info.get('rel_path', '')
                file_name = file_info.get('file_name', '')
                if rel_path == '.':
                    source_path = os.path.join(source_dir, file_name)
                else:
                    source_path = os.path.join(source_dir, rel_path, file_name)
        
        item_id = self.file_index_map.get(source_path)
        if item_id:
            current_values = list(self.file_listbox.item(item_id, 'values'))
            current_values[3] = status
            
            if actual_size is not None:
                current_values[2] = FileProcessor.format_size(actual_size)
                if status in ['已完成', '已复制']:
                    self.total_compressed_size += actual_size
            
            self.file_listbox.item(item_id, values=tuple(current_values))
            
            # 设置颜色
            if status == '已完成':
                self.file_listbox.tag_configure('completed', foreground='green')
                self.file_listbox.item(item_id, tags=('completed',))
            elif status == '处理失败':
                self.file_listbox.tag_configure('failed', foreground='red')
                self.file_listbox.item(item_id, tags=('failed',))
            elif status == '压缩中':
                self.file_listbox.tag_configure('processing', foreground='blue')
                self.file_listbox.item(item_id, tags=('processing',))
            elif status == '已排除':
                self.file_listbox.tag_configure('excluded', foreground='gray')
                self.file_listbox.item(item_id, tags=('excluded',))
    
    def _update_progress(self):
        """更新进度显示"""
        if self.is_compressing:
            current_progress = self.progress_var.get()
            
            # 计算实际经过的时间（排除暂停时间）
            elapsed_time = time.time() - self.compress_start_time - self.total_paused_duration
            
            if current_progress > 0 and current_progress < 100:
                estimated_total = elapsed_time * 100 / current_progress
                remaining = estimated_total - elapsed_time
                time_remaining_str = self._format_time(remaining)
            else:
                time_remaining_str = "计算中..."
            
            # 更新统计信息
            completed_count = len([f for f in self.file_list 
                                  if (hasattr(f, 'status') and f.status in ['已完成', '已复制']) or 
                                     (not hasattr(f, 'status') and f.get('status', '') in ['已完成', '已复制'])])
            total_count = len([f for i, f in enumerate(self.file_list) if i not in self.excluded_files])
            
            stats_text = f"已完成: {completed_count}/{total_count}"
            if self.total_compressed_size > 0:
                stats_text += f" | 已压缩: {FileProcessor.format_size(self.total_compressed_size)}"
            
            self.stats_label.config(text=stats_text)
            self.time_label.config(text=f"已用时: {self._format_time(elapsed_time)} | 预计剩余: {time_remaining_str}")
            
            self.root.after(1000, self._update_progress)
    
    def _format_time(self, seconds):
        """格式化时间"""
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            minutes, secs = divmod(seconds, 60)
            return f"{minutes:.0f}分{secs:.0f}秒"
        else:
            hours, remainder = divmod(seconds, 3600)
            minutes, secs = divmod(remainder, 60)
            return f"{hours:.0f}时{minutes:.0f}分{secs:.0f}秒"
    
    def _save_checkpoint(self):
        """保存断点续传数据"""
        try:
            checkpoint_dir = os.path.dirname(self.checkpoint_file)
            if not os.path.exists(checkpoint_dir):
                os.makedirs(checkpoint_dir, exist_ok=True)
            
            checkpoint_data = {
                'source_dir': self.source_dir.get(),
                'target_dir': self.target_dir.get(),
                'file_list': self.file_list,
                'excluded_files': list(self.excluded_files),
                'selected_files': list(self.selected_files),
                'compress_start_time': self.compress_start_time,
                'total_paused_duration': self.total_paused_duration,
                'timestamp': datetime.datetime.now().isoformat()
            }
            
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
            
            self.logger.info("断点数据已保存")
        except Exception as e:
            self.logger.error(f"保存断点数据失败: {e}")
    
    def _load_checkpoint(self):
        """加载断点续传数据"""
        if os.path.exists(self.checkpoint_file):
            try:
                response = messagebox.askyesno("发现断点数据", 
                    "检测到上次未完成的压缩任务，是否继续？\n选择\"是\"继续，选择\"否\"清除断点数据")
                
                if response:
                    with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                        checkpoint_data = json.load(f)
                    
                    self.source_dir.set(checkpoint_data.get('source_dir', ''))
                    self.target_dir.set(checkpoint_data.get('target_dir', ''))
                    self.file_list = checkpoint_data.get('file_list', [])
                    self.excluded_files = set(checkpoint_data.get('excluded_files', []))
                    self.selected_files = set(checkpoint_data.get('selected_files', []))
                    
                    self.logger.info("断点数据已加载，可以继续压缩")
                else:
                    self._clear_checkpoint()
            except Exception as e:
                self.logger.error(f"加载断点数据失败: {e}")
                self._clear_checkpoint()
    
    def _clear_checkpoint(self):
        """清除断点数据"""
        if os.path.exists(self.checkpoint_file):
            try:
                os.remove(self.checkpoint_file)
                self.logger.info("断点数据已清除")
            except Exception as e:
                self.logger.error(f"清除断点数据失败: {e}")
    
    def preview_compression(self):
        """压缩预览（单文件测试） - 预览文件列表中选中的文件"""
        # 检查是否有文件列表
        if not self.file_list:
            messagebox.showinfo("提示", "请先刷新文件列表")
            return
        
        # 从Treeview获取选中的文件
        if not self.file_listbox:
            messagebox.showinfo("提示", "文件列表未初始化")
            return
        
        selected_items = self.file_listbox.selection()
        if not selected_items:
            messagebox.showinfo("提示", "请先在文件列表中选择要预览的文件")
            return
        
        # 获取第一个选中的文件（如果有多个，只预览第一个）
        selected_item_id = selected_items[0]
        
        # 检查选中的是文件还是文件夹节点
        item_values = self.file_listbox.item(selected_item_id, 'values')
        item_text = self.file_listbox.item(selected_item_id, 'text')
        
        # 如果是文件夹节点（有text但values为空或第一个值为空），提示用户选择文件
        if item_text and (not item_values or not item_values[0]):
            messagebox.showinfo("提示", "请选择文件而不是文件夹")
            return
        
        # 通过item_id找到对应的文件索引
        preview_file = None
        for i, file_info in enumerate(self.file_list):
            # 兼容两种格式获取source_path
            if hasattr(file_info, 'source_path'):
                source_path = file_info.source_path
            else:
                source_path = file_info.get('source_path') or \
                             os.path.join(file_info.get('source_dir', ''), 
                                         file_info.get('rel_path', ''),
                                         file_info.get('file_name', '')) if file_info.get('rel_path', '') != '.' else \
                             os.path.join(file_info.get('source_dir', ''), file_info.get('file_name', ''))
            
            if self.file_index_map.get(source_path) == selected_item_id:
                preview_file = source_path
                break
        
        if not preview_file:
            messagebox.showinfo("提示", "无法找到选中的文件")
            return
        
        # 检查文件是否存在
        if not os.path.exists(preview_file):
            messagebox.showerror("错误", f"文件不存在: {preview_file}")
            return
        
        # 检查文件是否被排除
        file_index = None
        for i, file_info in enumerate(self.file_list):
            if hasattr(file_info, 'source_path'):
                sp = file_info.source_path
            else:
                sp = file_info.get('source_path') or \
                    os.path.join(file_info.get('source_dir', ''), 
                                file_info.get('rel_path', ''),
                                file_info.get('file_name', '')) if file_info.get('rel_path', '') != '.' else \
                    os.path.join(file_info.get('source_dir', ''), file_info.get('file_name', ''))
            if sp == preview_file:
                file_index = i
                break
        
        if file_index is not None and file_index in self.excluded_files:
            messagebox.showinfo("提示", "选中的文件已被排除，无法预览")
            return
        
        file_ext = os.path.splitext(preview_file)[1].lower()
        
        # 创建临时文件
        temp_dir = tempfile.mkdtemp(prefix='compress_preview_')
        temp_output = os.path.join(temp_dir, 'preview_' + os.path.basename(preview_file))
        
        # 判断文件类型
        is_image = file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp']
        is_video = file_ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.m4v', '.webm', '.3gp']
        
        if is_image:
            # 使用滑动对比预览
            self._preview_slide_compare(preview_file, temp_output, temp_dir, is_image=True)
        elif is_video:
            # 使用滑动对比预览
            self._preview_slide_compare(preview_file, temp_output, temp_dir, is_image=False)
        else:
            messagebox.showinfo("提示", "当前文件类型不支持预览，请选择图片或视频文件")
            # 清理临时目录
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
    
    def _preview_image(self, source_file, temp_output, temp_dir):
        """预览图片压缩效果 - 左右对比显示"""
        # 创建预览窗口
        preview_window = tk.Toplevel(self.root)
        preview_window.title("图片压缩预览 - 左右对比")
        preview_window.transient(self.root)
        self._center_window(preview_window, 1200, 700)
        
        # 窗口关闭时删除临时文件
        def on_closing():
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as e:
                self.logger.warning(f"清理临时文件失败: {e}")
            preview_window.destroy()
        
        preview_window.protocol("WM_DELETE_WINDOW", on_closing)
        
        # 主框架
        main_frame = ttk.Frame(preview_window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 文件信息
        try:
            original_size = os.path.getsize(source_file)
        except:
            original_size = 0
        
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text=f"📄 文件: {os.path.basename(source_file)}", 
                  font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT, padx=10)
        ttk.Label(info_frame, text=f"📦 原始大小: {FileProcessor.format_size(original_size)}", 
                  font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=10)
        
        # 进度提示
        progress_label = ttk.Label(main_frame, text="准备压缩...", font=('Segoe UI', 9))
        progress_label.pack(pady=5)
        
        # 图片对比区域
        image_frame = ttk.Frame(main_frame)
        image_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # 左侧：原图
        left_frame = ttk.LabelFrame(image_frame, text="原始图片", padding="5")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        left_canvas_frame = ttk.Frame(left_frame)
        left_canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        left_scrollbar_v = ttk.Scrollbar(left_canvas_frame, orient=tk.VERTICAL)
        left_scrollbar_v.pack(side=tk.RIGHT, fill=tk.Y)
        left_scrollbar_h = ttk.Scrollbar(left_canvas_frame, orient=tk.HORIZONTAL)
        left_scrollbar_h.pack(side=tk.BOTTOM, fill=tk.X)
        
        left_canvas = tk.Canvas(left_canvas_frame, bg='white',
                               yscrollcommand=left_scrollbar_v.set,
                               xscrollcommand=left_scrollbar_h.set)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scrollbar_v.config(command=left_canvas.yview)
        left_scrollbar_h.config(command=left_canvas.xview)
        
        # 右侧：压缩后图片
        right_frame = ttk.LabelFrame(image_frame, text="压缩后图片", padding="5")
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        right_canvas_frame = ttk.Frame(right_frame)
        right_canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        right_scrollbar_v = ttk.Scrollbar(right_canvas_frame, orient=tk.VERTICAL)
        right_scrollbar_v.pack(side=tk.RIGHT, fill=tk.Y)
        right_scrollbar_h = ttk.Scrollbar(right_canvas_frame, orient=tk.HORIZONTAL)
        right_scrollbar_h.pack(side=tk.BOTTOM, fill=tk.X)
        
        right_canvas = tk.Canvas(right_canvas_frame, bg='white',
                                yscrollcommand=right_scrollbar_v.set,
                                xscrollcommand=right_scrollbar_h.set)
        right_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right_scrollbar_v.config(command=right_canvas.yview)
        right_scrollbar_h.config(command=right_canvas.xview)
        
        # 详细信息显示区域
        details_frame = ttk.LabelFrame(main_frame, text="图片详细信息", padding="10")
        details_frame.pack(fill=tk.X, pady=10)
        
        details_text = tk.Text(details_frame, wrap=tk.WORD, font=('Courier New', 9), height=8)
        details_scrollbar = ttk.Scrollbar(details_frame, orient=tk.VERTICAL, command=details_text.yview)
        details_text.config(yscrollcommand=details_scrollbar.set)
        details_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        details_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        details_text.config(state=tk.DISABLED)
        
        # 统计信息
        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill=tk.X, pady=5)
        
        stats_label = ttk.Label(stats_frame, text="", font=('Segoe UI', 9))
        stats_label.pack()
        
        # 按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=5)
        
        def load_images():
            """加载并显示图片"""
            progress_label.config(text="正在压缩...", foreground="blue")
            preview_window.update()
            
            try:
                # 加载原图
                original_img = Image.open(source_file)
                original_width, original_height = original_img.size
                original_format = original_img.format or "未知"
                original_mode = original_img.mode
                
                # 执行压缩
                start_time = time.time()
                if self.image_compressor.compress(source_file, temp_output):
                    compressed_size = os.path.getsize(temp_output) if os.path.exists(temp_output) else 0
                    elapsed_time = time.time() - start_time
                    
                    if compressed_size > 0:
                        # 加载压缩后的图片
                        compressed_img = Image.open(temp_output)
                        compressed_width, compressed_height = compressed_img.size
                        compressed_format = compressed_img.format or "未知"
                        compressed_mode = compressed_img.mode
                        
                        # 计算统一的显示尺寸（使用较小的尺寸，保持比例）
                        # 获取画布可用尺寸
                        canvas_width = min(left_canvas.winfo_width(), right_canvas.winfo_width()) or 500
                        canvas_height = min(left_canvas.winfo_height(), right_canvas.winfo_height()) or 400
                        
                        # 计算统一显示尺寸（保持原始图片比例）
                        # 使用原始图片和压缩后图片中较大的尺寸来确定显示比例
                        max_original_dim = max(original_width, original_height)
                        max_compressed_dim = max(compressed_width, compressed_height)
                        max_dim = max(max_original_dim, max_compressed_dim)
                        
                        # 计算缩放比例（限制在画布范围内）
                        scale_ratio = min(canvas_width / max_dim, canvas_height / max_dim, 1.0)
                        display_size = (int(max_dim * scale_ratio), int(max_dim * scale_ratio))
                        
                        # 缩放原图并保持比例
                        original_ratio = original_width / original_height
                        if original_ratio > 1:
                            display_original_width = min(int(canvas_width * 0.9), int(display_size[0]))
                            display_original_height = int(display_original_width / original_ratio)
                        else:
                            display_original_height = min(int(canvas_height * 0.9), int(display_size[1]))
                            display_original_width = int(display_original_height * original_ratio)
                        
                        original_display = original_img.resize((display_original_width, display_original_height), Image.LANCZOS)
                        original_photo = ImageTk.PhotoImage(original_display)
                        
                        # 缩放压缩后图片并保持比例
                        compressed_ratio = compressed_width / compressed_height
                        if compressed_ratio > 1:
                            display_compressed_width = min(int(canvas_width * 0.9), int(display_size[0]))
                            display_compressed_height = int(display_compressed_width / compressed_ratio)
                        else:
                            display_compressed_height = min(int(canvas_height * 0.9), int(display_size[1]))
                            display_compressed_width = int(display_compressed_height * compressed_ratio)
                        
                        compressed_display = compressed_img.resize((display_compressed_width, display_compressed_height), Image.LANCZOS)
                        compressed_photo = ImageTk.PhotoImage(compressed_display)
                        
                        # 在画布中央显示图片（两张图片同样大小显示）
                        # 计算居中位置
                        left_canvas.update_idletasks()
                        right_canvas.update_idletasks()
                        left_cw = left_canvas.winfo_width()
                        left_ch = left_canvas.winfo_height()
                        right_cw = right_canvas.winfo_width()
                        right_ch = right_canvas.winfo_height()
                        
                        # 使用统一的显示尺寸
                        unified_width = min(display_original_width, display_compressed_width)
                        unified_height = min(display_original_height, display_compressed_height)
                        
                        # 重新缩放图片到统一尺寸
                        original_unified = original_img.resize((unified_width, int(unified_width / original_ratio)), Image.LANCZOS)
                        compressed_unified = compressed_img.resize((unified_width, int(unified_width / compressed_ratio)), Image.LANCZOS)
                        
                        # 确保高度不超过统一高度
                        if original_unified.height > unified_height:
                            ratio = unified_height / original_unified.height
                            original_unified = original_unified.resize((int(original_unified.width * ratio), unified_height), Image.LANCZOS)
                        if compressed_unified.height > unified_height:
                            ratio = unified_height / compressed_unified.height
                            compressed_unified = compressed_unified.resize((int(compressed_unified.width * ratio), unified_height), Image.LANCZOS)
                        
                        original_unified_photo = ImageTk.PhotoImage(original_unified)
                        compressed_unified_photo = ImageTk.PhotoImage(compressed_unified)
                        
                        # 在左侧显示原图（居中）
                        left_canvas.delete("all")
                        left_x = (left_cw - original_unified.width) // 2 if left_cw > 0 else 0
                        left_y = (left_ch - original_unified.height) // 2 if left_ch > 0 else 0
                        left_canvas.create_image(left_x, left_y, anchor=tk.NW, image=original_unified_photo)
                        left_canvas.config(scrollregion=left_canvas.bbox("all"))
                        left_canvas.image = original_unified_photo  # 保持引用
                        
                        # 在右侧显示压缩后的图片（居中）
                        right_canvas.delete("all")
                        right_x = (right_cw - compressed_unified.width) // 2 if right_cw > 0 else 0
                        right_y = (right_ch - compressed_unified.height) // 2 if right_ch > 0 else 0
                        right_canvas.create_image(right_x, right_y, anchor=tk.NW, image=compressed_unified_photo)
                        right_canvas.config(scrollregion=right_canvas.bbox("all"))
                        right_canvas.image = compressed_unified_photo  # 保持引用
                        
                        # 更新详细信息
                        compression_ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
                        details_content = f"""【原始图片信息】
文件名: {os.path.basename(source_file)}
文件大小: {FileProcessor.format_size(original_size)}
图片尺寸: {original_width} x {original_height} 像素
图片格式: {original_format}
颜色模式: {original_mode}
显示尺寸: {original_unified.width} x {original_unified.height} 像素

【压缩后图片信息】
文件名: {os.path.basename(temp_output)}
文件大小: {FileProcessor.format_size(compressed_size)}
图片尺寸: {compressed_width} x {compressed_height} 像素
图片格式: {compressed_format}
颜色模式: {compressed_mode}
显示尺寸: {compressed_unified.width} x {compressed_unified.height} 像素

【压缩统计】
压缩率: {compression_ratio:.2f}%
节省空间: {FileProcessor.format_size(original_size - compressed_size)}
压缩用时: {self._format_time(elapsed_time)}"""
                        
                        details_text.config(state=tk.NORMAL)
                        details_text.delete('1.0', tk.END)
                        details_text.insert('1.0', details_content)
                        details_text.config(state=tk.DISABLED)
                        
                        # 更新统计信息
                        stats_text = (f"压缩完成！ | "
                                    f"原始: {FileProcessor.format_size(original_size)} | "
                                    f"压缩后: {FileProcessor.format_size(compressed_size)} | "
                                    f"压缩率: {compression_ratio:.2f}% | "
                                    f"用时: {self._format_time(elapsed_time)}")
                        stats_label.config(text=stats_text, foreground="green")
                        progress_label.config(text="压缩完成", foreground="green")
                    else:
                        stats_label.config(text="压缩失败：未生成输出文件", foreground="red")
                        progress_label.config(text="压缩失败", foreground="red")
                else:
                    stats_label.config(text="压缩失败", foreground="red")
                    progress_label.config(text="压缩失败", foreground="red")
            except Exception as e:
                self.logger.error(f"预览图片压缩失败: {e}")
                stats_label.config(text=f"错误: {str(e)}", foreground="red")
                progress_label.config(text="发生错误", foreground="red")
        
        ttk.Button(button_frame, text="开始压缩预览", command=load_images).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="关闭", command=on_closing).pack(side=tk.RIGHT, padx=5)
        
        # 自动加载原图
        try:
            original_img = Image.open(source_file)
            original_width, original_height = original_img.size
            original_format = original_img.format or "未知"
            original_mode = original_img.mode
            
            # 获取画布尺寸并计算合适的显示尺寸
            left_canvas.update_idletasks()
            canvas_width = left_canvas.winfo_width() or 500
            canvas_height = left_canvas.winfo_height() or 400
            
            # 计算缩放比例（保持原始比例，适应画布）
            ratio = min(canvas_width * 0.9 / original_width, canvas_height * 0.9 / original_height, 1.0)
            display_width = int(original_width * ratio)
            display_height = int(original_height * ratio)
            
            original_display = original_img.resize((display_width, display_height), Image.LANCZOS)
            original_photo = ImageTk.PhotoImage(original_display)
            
            # 居中显示
            left_canvas.update_idletasks()
            cx = (left_canvas.winfo_width() - display_width) // 2 if left_canvas.winfo_width() > 0 else 0
            cy = (left_canvas.winfo_height() - display_height) // 2 if left_canvas.winfo_height() > 0 else 0
            left_canvas.create_image(cx, cy, anchor=tk.NW, image=original_photo)
            left_canvas.config(scrollregion=left_canvas.bbox("all"))
            left_canvas.image = original_photo
            
            # 显示原始图片信息
            details_content = f"""【原始图片信息】
文件名: {os.path.basename(source_file)}
文件大小: {FileProcessor.format_size(original_size)}
图片尺寸: {original_width} x {original_height} 像素
图片格式: {original_format}
颜色模式: {original_mode}
显示尺寸: {display_width} x {display_height} 像素

【压缩后图片信息】
等待压缩...

【压缩统计】
等待压缩..."""
            
            details_text.config(state=tk.NORMAL)
            details_text.delete('1.0', tk.END)
            details_text.insert('1.0', details_content)
            details_text.config(state=tk.DISABLED)
            
            progress_label.config(text="已加载原图，点击\"开始压缩预览\"查看压缩效果", foreground="gray")
        except Exception as e:
            self.logger.error(f"加载原图失败: {e}")
            progress_label.config(text=f"加载原图失败: {str(e)}", foreground="red")
    
    def _preview_video(self, source_file, temp_output, temp_dir):
        """预览视频压缩效果 - 提供对比信息（tkinter不支持内置视频播放）"""
        # 创建预览窗口
        preview_window = tk.Toplevel(self.root)
        preview_window.title("视频压缩预览")
        preview_window.transient(self.root)
        self._center_window(preview_window, 1000, 700)
        
        # 窗口关闭时删除临时文件
        def on_closing():
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as e:
                self.logger.warning(f"清理临时文件失败: {e}")
            preview_window.destroy()
        
        preview_window.protocol("WM_DELETE_WINDOW", on_closing)
        
        # 主框架
        main_frame = ttk.Frame(preview_window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 文件信息
        try:
            original_size = os.path.getsize(source_file)
        except:
            original_size = 0
        
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text=f"📄 文件: {os.path.basename(source_file)}", 
                  font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT, padx=10)
        ttk.Label(info_frame, text=f"📦 原始大小: {FileProcessor.format_size(original_size)}", 
                  font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=10)
        
        # 进度提示
        progress_label = ttk.Label(main_frame, text="准备压缩...", font=('Segoe UI', 9))
        progress_label.pack(pady=5)
        
        # 视频信息区域（由于tkinter不支持视频播放，显示信息和文件路径）
        video_info_frame = ttk.LabelFrame(main_frame, text="视频信息", padding="10")
        video_info_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        info_text = tk.Text(video_info_frame, wrap=tk.WORD, font=('Segoe UI', 9), height=15)
        info_text.pack(fill=tk.BOTH, expand=True)
        
        # 统计信息
        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill=tk.X, pady=10)
        
        stats_label = ttk.Label(stats_frame, text="", font=('Segoe UI', 9))
        stats_label.pack()
        
        # 按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=5)
        
        def compress_video():
            """压缩视频并显示信息"""
            progress_label.config(text="正在压缩视频...", foreground="blue")
            preview_window.update()
            
            try:
                start_time = time.time()
                if self.video_compressor.compress(source_file, temp_output):
                    compressed_size = os.path.getsize(temp_output) if os.path.exists(temp_output) else 0
                    elapsed_time = time.time() - start_time
                    
                    if compressed_size > 0:
                        compression_ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
                        
                        # 更新信息文本
                        info_content = f"""
═══════════════════════════════════════════
视频压缩预览信息
═══════════════════════════════════════════

文件信息：
  文件名: {os.path.basename(source_file)}
  原始大小: {FileProcessor.format_size(original_size)}
  压缩后大小: {FileProcessor.format_size(compressed_size)}
  压缩率: {compression_ratio:.2f}%
  压缩用时: {self._format_time(elapsed_time)}

文件路径：
  原始文件: {source_file}
  压缩文件: {temp_output}

查看提示：
  由于tkinter不支持内置视频播放，您可以：
  1. 使用系统默认播放器打开两个视频文件进行对比
  2. 使用支持双窗口同步播放的播放器（如VLC）
  3. 手动比较文件大小和播放质量

注意：预览文件为临时文件，关闭此窗口后会自动删除
"""
                        info_text.delete('1.0', tk.END)
                        info_text.insert('1.0', info_content)
                        info_text.config(state=tk.DISABLED)
                        
                        stats_text = (f"压缩完成！ | "
                                    f"原始: {FileProcessor.format_size(original_size)} | "
                                    f"压缩后: {FileProcessor.format_size(compressed_size)} | "
                                    f"压缩率: {compression_ratio:.2f}% | "
                                    f"用时: {self._format_time(elapsed_time)}")
                        stats_label.config(text=stats_text, foreground="green")
                        progress_label.config(text="压缩完成", foreground="green")
                        
                        # 添加打开文件按钮
                        def open_original():
                            try:
                                if sys.platform == 'win32':
                                    os.startfile(source_file)
                                elif sys.platform == 'darwin':
                                    os.system(f'open "{source_file}"')
                                else:
                                    os.system(f'xdg-open "{source_file}"')
                            except:
                                pass
                        
                        def open_compressed():
                            try:
                                if sys.platform == 'win32':
                                    os.startfile(temp_output)
                                elif sys.platform == 'darwin':
                                    os.system(f'open "{temp_output}"')
                                else:
                                    os.system(f'xdg-open "{temp_output}"')
                            except:
                                pass
                        
                        # 更新按钮
                        for widget in button_frame.winfo_children():
                            widget.destroy()
                        
                        ttk.Button(button_frame, text="打开原始视频", command=open_original).pack(side=tk.LEFT, padx=5)
                        ttk.Button(button_frame, text="打开压缩视频", command=open_compressed).pack(side=tk.LEFT, padx=5)
                        ttk.Button(button_frame, text="关闭", command=on_closing).pack(side=tk.RIGHT, padx=5)
                    else:
                        stats_label.config(text="压缩失败：未生成输出文件", foreground="red")
                        progress_label.config(text="压缩失败", foreground="red")
                else:
                    stats_label.config(text="压缩失败", foreground="red")
                    progress_label.config(text="压缩失败", foreground="red")
            except Exception as e:
                self.logger.error(f"预览视频压缩失败: {e}")
                stats_label.config(text=f"错误: {str(e)}", foreground="red")
                progress_label.config(text="发生错误", foreground="red")
        
        ttk.Button(button_frame, text="开始压缩预览", command=compress_video).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="关闭", command=on_closing).pack(side=tk.RIGHT, padx=5)
    
    def _preview_slide_compare(self, source_file, temp_output, temp_dir, is_image=True):
        """滑动对比预览界面 - 两个媒体重叠放置，滑块控制分割线"""
        # 创建预览窗口
        preview_window = tk.Toplevel(self.root)
        preview_window.title("滑动对比预览 - " + ("图片" if is_image else "视频"))
        preview_window.transient(self.root)
        self._center_window(preview_window, 1400, 900)
        
        # 窗口关闭处理（在定义on_closing_with_cleanup之前先设置基础处理）
        def on_closing_base():
            """基础关闭处理"""
            try:
                if hasattr(preview_window, '_video_cap_original') and preview_window._video_cap_original:
                    preview_window._video_cap_original.release()
                if hasattr(preview_window, '_video_cap_compressed') and preview_window._video_cap_compressed:
                    preview_window._video_cap_compressed.release()
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as e:
                self.logger.warning(f"清理临时文件失败: {e}")
            preview_window.destroy()
        
        # on_closing函数引用
        on_closing = on_closing_base
        
        # 主框架
        main_frame = ttk.Frame(preview_window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 文件信息
        try:
            original_size = os.path.getsize(source_file)
        except:
            original_size = 0
        
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text=f"文件: {os.path.basename(source_file)}", 
                  font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT, padx=10)
        ttk.Label(info_frame, text=f"原始大小: {FileProcessor.format_size(original_size)}", 
                  font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=10)
        
        # 进度提示
        progress_label = ttk.Label(main_frame, text="准备压缩...", font=('Segoe UI', 9))
        progress_label.pack(pady=5)
        
        # 媒体对比区域（重叠显示）
        media_frame = ttk.Frame(main_frame)
        media_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # 创建Canvas用于显示重叠的媒体
        canvas_frame = ttk.Frame(media_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        preview_canvas = tk.Canvas(canvas_frame, bg='#2c2c2c', highlightthickness=0)
        preview_canvas.pack(fill=tk.BOTH, expand=True)
        
        # 滑块控制区域
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(control_frame, text="左侧: 原始", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=10)
        
        # 滑块
        slider_var = tk.DoubleVar(value=50.0)  # 默认50%位置
        slider = ttk.Scale(control_frame, from_=0.0, to=100.0, 
                          orient=tk.HORIZONTAL, variable=slider_var, length=600)
        slider.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        
        ttk.Label(control_frame, text="右侧: 压缩后", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=10)
        
        # 详细信息显示区域
        details_frame = ttk.LabelFrame(main_frame, text="详细信息", padding="10")
        details_frame.pack(fill=tk.X, pady=10)
        
        details_text = tk.Text(details_frame, wrap=tk.WORD, font=('Courier New', 9), height=6)
        details_scrollbar = ttk.Scrollbar(details_frame, orient=tk.VERTICAL, command=details_text.yview)
        details_text.config(yscrollcommand=details_scrollbar.set)
        details_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        details_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        details_text.config(state=tk.DISABLED)
        
        # 统计信息
        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill=tk.X, pady=5)
        
        stats_label = ttk.Label(stats_frame, text="", font=('Segoe UI', 9))
        stats_label.pack()
        
        # 按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=5)
        
        # 存储媒体数据
        original_photo = None
        compressed_photo = None
        original_img = None
        compressed_img = None
        display_width = 0
        display_height = 0
        display_x = 0
        display_y = 0
        video_playing = False
        video_frame_interval = None
        
        # 视频相关
        if not is_image:
            preview_window._video_cap_original = None
            preview_window._video_cap_compressed = None
            preview_window._video_fps_original = 30
            preview_window._video_fps_compressed = 30
        
        def update_display():
            """更新显示 - 根据滑块位置绘制分割线"""
            nonlocal original_photo, compressed_photo, original_img, compressed_img
            nonlocal display_width, display_height
            
            if not original_img or not compressed_img:
                return
            
            preview_canvas.delete("all")
            
            # 获取画布尺寸
            canvas_width = preview_canvas.winfo_width()
            canvas_height = preview_canvas.winfo_height()
            
            if canvas_width <= 1 or canvas_height <= 1:
                preview_window.after(100, update_display)
                return
            
            # 计算显示区域（居中显示）
            img_ratio = original_img.width / original_img.height if original_img.height > 0 else 1
            canvas_ratio = canvas_width / canvas_height if canvas_height > 0 else 1
            
            if img_ratio > canvas_ratio:
                # 图片更宽，以宽度为准
                display_w = int(canvas_width * 0.95)
                display_h = int(display_w / img_ratio)
            else:
                # 图片更高，以高度为准
                display_h = int(canvas_height * 0.95)
                display_w = int(display_h * img_ratio)
            
            display_x = (canvas_width - display_w) // 2
            display_y = (canvas_height - display_h) // 2
            
            # 计算分割线位置
            slider_pos = slider_var.get() / 100.0  # 0.0 到 1.0
            split_x = display_x + int(display_w * slider_pos)
            
            # 左侧：原始图片（只显示左侧部分）
            if original_img:
                left_width = int((split_x - display_x) * (original_img.width / display_w))
                if left_width > 0:
                    left_img = original_img.crop((0, 0, min(left_width, original_img.width), original_img.height))
                    left_photo = ImageTk.PhotoImage(left_img.resize((split_x - display_x, display_h), Image.LANCZOS))
                    preview_canvas.create_image(display_x, display_y, anchor=tk.NW, image=left_photo, tags='media')
                    preview_canvas.left_photo = left_photo  # 保持引用
            
            # 右侧：压缩后图片（只显示右侧部分）
            if compressed_img:
                right_start = int((split_x - display_x) * (compressed_img.width / display_w))
                if right_start < compressed_img.width:
                    right_img = compressed_img.crop((right_start, 0, compressed_img.width, compressed_img.height))
                    right_photo = ImageTk.PhotoImage(right_img.resize((display_x + display_w - split_x, display_h), Image.LANCZOS))
                    preview_canvas.create_image(split_x, display_y, anchor=tk.NW, image=right_photo, tags='media')
                    preview_canvas.right_photo = right_photo  # 保持引用
            
            # 绘制分割线（垂直分割线，只保留一条线）
            preview_canvas.create_line(split_x, display_y, split_x, display_y + display_h, 
                                       fill='#ffffff', width=4, tags='split_line')
        
        def on_slider_change(*args):
            """滑块变化时更新显示"""
            update_display()
        
        slider_var.trace('w', on_slider_change)
        
        def load_media():
            """加载并显示媒体"""
            if is_image:
                # 图片处理（同步执行，因为速度较快）
                load_media_sync()
            else:
                # 视频处理（异步执行，避免阻塞GUI）
                load_media_async()
        
        def load_media_sync():
            """同步加载图片（因为图片压缩较快）"""
            nonlocal original_photo, compressed_photo, original_img, compressed_img
            nonlocal display_width, display_height
            
            progress_label.config(text="正在压缩...", foreground="blue")
            preview_window.update()
            
            try:
                # 图片处理
                # 加载原图
                original_img = Image.open(source_file)
                original_width, original_height = original_img.size
                original_format = original_img.format or "未知"
                original_mode = original_img.mode
                
                # 执行压缩
                start_time = time.time()
                if self.image_compressor.compress(source_file, temp_output):
                    compressed_size = os.path.getsize(temp_output) if os.path.exists(temp_output) else 0
                    elapsed_time = time.time() - start_time
                    
                    if compressed_size > 0:
                        # 加载压缩后的图片
                        compressed_img = Image.open(temp_output)
                        compressed_width, compressed_height = compressed_img.size
                        compressed_format = compressed_img.format or "未知"
                        compressed_mode = compressed_img.mode
                        
                        # 获取画布尺寸并计算合适的显示尺寸
                        preview_canvas.update_idletasks()
                        canvas_width = preview_canvas.winfo_width() or 800
                        canvas_height = preview_canvas.winfo_height() or 600
                        
                        # 计算缩放比例（保持原始比例，适应画布）
                        ratio = min(canvas_width * 0.9 / max(original_width, compressed_width), 
                                  canvas_height * 0.9 / max(original_height, compressed_height), 1.0)
                        display_width = int(max(original_width, compressed_width) * ratio)
                        display_height = int(max(original_height, compressed_height) * ratio)
                        
                        # 缩放图片到显示尺寸（保持比例）
                        original_display = original_img.resize((display_width, int(display_width * original_height / original_width)), Image.LANCZOS)
                        compressed_display = compressed_img.resize((display_width, int(display_width * compressed_height / compressed_width)), Image.LANCZOS)
                        
                        # 统一高度（使用较大的高度）
                        max_height = max(original_display.height, compressed_display.height)
                        if original_display.height != max_height:
                            original_display = original_display.resize((int(original_display.width * max_height / original_display.height), max_height), Image.LANCZOS)
                        if compressed_display.height != max_height:
                            compressed_display = compressed_display.resize((int(compressed_display.width * max_height / compressed_display.height), max_height), Image.LANCZOS)
                        
                        display_height = max_height
                        display_width = max(original_display.width, compressed_display.width)
                        
                        # 创建PhotoImage对象
                        original_photo = ImageTk.PhotoImage(original_display)
                        compressed_photo = ImageTk.PhotoImage(compressed_display)
                        
                        # 保存原始图片对象用于裁剪
                        original_img = original_display
                        compressed_img = compressed_display
                        
                        # 更新详细信息
                        compression_ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
                        details_content = f"""【原始图片信息】
文件名: {os.path.basename(source_file)}
文件大小: {FileProcessor.format_size(original_size)}
图片尺寸: {original_width} x {original_height} 像素
图片格式: {original_format}
颜色模式: {original_mode}
显示尺寸: {display_width} x {display_height} 像素

【压缩后图片信息】
文件名: {os.path.basename(temp_output)}
文件大小: {FileProcessor.format_size(compressed_size)}
图片尺寸: {compressed_width} x {compressed_height} 像素
图片格式: {compressed_format}
颜色模式: {compressed_mode}
显示尺寸: {display_width} x {display_height} 像素

【压缩统计】
压缩率: {compression_ratio:.2f}%
节省空间: {FileProcessor.format_size(original_size - compressed_size)}
压缩用时: {self._format_time(elapsed_time)}"""
                        
                        details_text.config(state=tk.NORMAL)
                        details_text.delete('1.0', tk.END)
                        details_text.insert('1.0', details_content)
                        details_text.config(state=tk.DISABLED)
                        
                        # 更新统计信息
                        stats_text = (f"压缩完成！ | "
                                    f"原始: {FileProcessor.format_size(original_size)} | "
                                    f"压缩后: {FileProcessor.format_size(compressed_size)} | "
                                    f"压缩率: {compression_ratio:.2f}% | "
                                    f"用时: {self._format_time(elapsed_time)}")
                        stats_label.config(text=stats_text, foreground="green")
                        progress_label.config(text="压缩完成 - 拖动滑块查看对比效果", foreground="green")
                        
                        # 更新显示
                        preview_window.after(100, update_display)
                    else:
                        stats_label.config(text="压缩失败：未生成输出文件", foreground="red")
                        progress_label.config(text="压缩失败", foreground="red")
                else:
                    stats_label.config(text="压缩失败", foreground="red")
                    progress_label.config(text="压缩失败", foreground="red")
            except Exception as e:
                self.logger.error(f"预览压缩失败: {e}")
                import traceback
                traceback.print_exc()
                stats_label.config(text=f"错误: {str(e)}", foreground="red")
                progress_label.config(text="发生错误", foreground="red")
        
        def load_media_async():
            """异步加载视频（使用线程避免阻塞GUI）"""
            if not HAS_CV2:
                messagebox.showerror("错误", "需要opencv-python库来预览视频\n请安装: pip install opencv-python")
                on_closing()
                return
            
            progress_label.config(text="正在压缩视频（后台处理中，请稍候）...", foreground="blue")
            # 禁用按钮，防止重复点击
            for widget in button_frame.winfo_children():
                if isinstance(widget, ttk.Button) and widget.cget('text') == "开始压缩预览":
                    widget.config(state=tk.DISABLED)
            
            def compress_thread():
                """在后台线程中执行压缩"""
                try:
                    import cv2
                    
                    # 执行压缩
                    start_time = time.time()
                    success = self.video_compressor.compress(source_file, temp_output)
                    elapsed_time = time.time() - start_time
                    
                    # 在主线程中更新UI
                    preview_window.after(0, lambda: on_compress_complete(success, elapsed_time))
                except Exception as e:
                    self.logger.error(f"压缩线程错误: {e}")
                    preview_window.after(0, lambda: on_compress_error(str(e)))
            
            # 启动压缩线程
            compression_thread = threading.Thread(target=compress_thread, daemon=True)
            compression_thread.start()
        
        def on_compress_complete(success, elapsed_time):
            """压缩完成后的回调"""
            nonlocal original_photo, compressed_photo, original_img, compressed_img
            nonlocal display_width, display_height
            
            # 重新启用按钮
            for widget in button_frame.winfo_children():
                if isinstance(widget, ttk.Button) and widget.cget('text') == "开始压缩预览":
                    widget.config(state=tk.NORMAL)
            
            if not success:
                stats_label.config(text="压缩失败", foreground="red")
                progress_label.config(text="压缩失败", foreground="red")
                return
            
            try:
                import cv2
                
                compressed_size = os.path.getsize(temp_output) if os.path.exists(temp_output) else 0
                
                if compressed_size > 0:
                    # 打开视频文件
                    preview_window._video_cap_original = cv2.VideoCapture(source_file)
                    preview_window._video_cap_compressed = cv2.VideoCapture(temp_output)
                    
                    if not preview_window._video_cap_original.isOpened() or not preview_window._video_cap_compressed.isOpened():
                        messagebox.showerror("错误", "无法打开视频文件")
                        on_closing()
                        return
                    
                    # 获取视频信息
                    preview_window._video_fps_original = preview_window._video_cap_original.get(cv2.CAP_PROP_FPS) or 30
                    preview_window._video_fps_compressed = preview_window._video_cap_compressed.get(cv2.CAP_PROP_FPS) or 30
                    preview_window._video_frame_count_original = int(preview_window._video_cap_original.get(cv2.CAP_PROP_FRAME_COUNT))
                    preview_window._video_frame_count_compressed = int(preview_window._video_cap_compressed.get(cv2.CAP_PROP_FRAME_COUNT))
                    preview_window._video_duration_original = preview_window._video_frame_count_original / preview_window._video_fps_original if preview_window._video_fps_original > 0 else 0
                    preview_window._video_duration_compressed = preview_window._video_frame_count_compressed / preview_window._video_fps_compressed if preview_window._video_fps_compressed > 0 else 0
                    
                    frame_width_orig = int(preview_window._video_cap_original.get(cv2.CAP_PROP_FRAME_WIDTH))
                    frame_height_orig = int(preview_window._video_cap_original.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    frame_width_comp = int(preview_window._video_cap_compressed.get(cv2.CAP_PROP_FRAME_WIDTH))
                    frame_height_comp = int(preview_window._video_cap_compressed.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    
                    # 读取第一帧
                    ret_orig, frame_orig = preview_window._video_cap_original.read()
                    ret_comp, frame_comp = preview_window._video_cap_compressed.read()
                    
                    if ret_orig and ret_comp:
                        # 重置视频到开始位置
                        preview_window._video_cap_original.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        preview_window._video_cap_compressed.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        
                        # 转换BGR到RGB
                        frame_orig_rgb = cv2.cvtColor(frame_orig, cv2.COLOR_BGR2RGB)
                        frame_comp_rgb = cv2.cvtColor(frame_comp, cv2.COLOR_BGR2RGB)
                        
                        # 转换为PIL Image
                        original_img = Image.fromarray(frame_orig_rgb)
                        compressed_img = Image.fromarray(frame_comp_rgb)
                        
                        # 获取画布尺寸并计算合适的显示尺寸
                        preview_canvas.update_idletasks()
                        canvas_width = preview_canvas.winfo_width() or 800
                        canvas_height = preview_canvas.winfo_height() or 600
                        
                        # 计算缩放比例
                        ratio = min(canvas_width * 0.9 / max(frame_width_orig, frame_width_comp), 
                                  canvas_height * 0.9 / max(frame_height_orig, frame_height_comp), 1.0)
                        display_width = int(max(frame_width_orig, frame_width_comp) * ratio)
                        display_height = int(max(frame_height_orig, frame_height_comp) * ratio)
                        
                        # 缩放图片
                        original_display = original_img.resize((display_width, int(display_width * frame_height_orig / frame_width_orig)), Image.LANCZOS)
                        compressed_display = compressed_img.resize((display_width, int(display_width * frame_height_comp / frame_width_comp)), Image.LANCZOS)
                        
                        # 统一高度
                        max_height = max(original_display.height, compressed_display.height)
                        if original_display.height != max_height:
                            original_display = original_display.resize((int(original_display.width * max_height / original_display.height), max_height), Image.LANCZOS)
                        if compressed_display.height != max_height:
                            compressed_display = compressed_display.resize((int(compressed_display.width * max_height / compressed_display.height), max_height), Image.LANCZOS)
                        
                        display_height = max_height
                        display_width = max(original_display.width, compressed_display.width)
                        
                        # 创建PhotoImage对象
                        original_photo = ImageTk.PhotoImage(original_display)
                        compressed_photo = ImageTk.PhotoImage(compressed_display)
                        
                        # 保存原始图片对象
                        original_img = original_display
                        compressed_img = compressed_display
                        
                        # 更新详细信息
                        compression_ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
                        details_content = f"""【原始视频信息】
文件名: {os.path.basename(source_file)}
文件大小: {FileProcessor.format_size(original_size)}
视频尺寸: {frame_width_orig} x {frame_height_orig} 像素
帧率: {preview_window._video_fps_original:.2f} FPS
总帧数: {preview_window._video_frame_count_original}
时长: {self._format_time(preview_window._video_duration_original)}
显示尺寸: {display_width} x {display_height} 像素

【压缩后视频信息】
文件名: {os.path.basename(temp_output)}
文件大小: {FileProcessor.format_size(compressed_size)}
视频尺寸: {frame_width_comp} x {frame_height_comp} 像素
帧率: {preview_window._video_fps_compressed:.2f} FPS
总帧数: {preview_window._video_frame_count_compressed}
时长: {self._format_time(preview_window._video_duration_compressed)}
显示尺寸: {display_width} x {display_height} 像素

【压缩统计】
压缩率: {compression_ratio:.2f}%
节省空间: {FileProcessor.format_size(original_size - compressed_size)}
压缩用时: {self._format_time(elapsed_time)}"""
                        
                        details_text.config(state=tk.NORMAL)
                        details_text.delete('1.0', tk.END)
                        details_text.insert('1.0', details_content)
                        details_text.config(state=tk.DISABLED)
                        
                        # 更新统计信息
                        stats_text = (f"压缩完成！ | "
                                    f"原始: {FileProcessor.format_size(original_size)} | "
                                    f"压缩后: {FileProcessor.format_size(compressed_size)} | "
                                    f"压缩率: {compression_ratio:.2f}% | "
                                    f"用时: {self._format_time(elapsed_time)}")
                        stats_label.config(text=stats_text, foreground="green")
                        progress_label.config(text="压缩完成 - 拖动滑块查看对比效果，使用播放控制播放视频", foreground="green")
                        
                        # 更新显示
                        preview_window.after(100, update_display)
                        
                        # 启用播放控制
                        enable_playback_controls()
                    else:
                        messagebox.showerror("错误", "无法读取视频帧")
                        on_closing()
                else:
                    stats_label.config(text="压缩失败：未生成输出文件", foreground="red")
                    progress_label.config(text="压缩失败", foreground="red")
            except Exception as e:
                self.logger.error(f"处理压缩结果失败: {e}")
                import traceback
                traceback.print_exc()
                stats_label.config(text=f"错误: {str(e)}", foreground="red")
                progress_label.config(text="发生错误", foreground="red")
        
        def on_compress_error(error_msg):
            """压缩错误回调"""
            # 重新启用按钮
            for widget in button_frame.winfo_children():
                if isinstance(widget, ttk.Button) and widget.cget('text') == "开始压缩预览":
                    widget.config(state=tk.NORMAL)
            
            stats_label.config(text=f"错误: {error_msg}", foreground="red")
            progress_label.config(text="压缩失败", foreground="red")
        
        # 视频播放控制（仅用于视频）
        playback_frame = None
        play_button = None
        pause_button = None
        video_progress_scale = None
        video_progress_label = None
        video_current_frame = 0
        video_total_frames = 0
        video_play_timer = None
        
        def enable_playback_controls():
            """启用视频播放控制"""
            nonlocal playback_frame, play_button, pause_button, video_progress_scale, video_progress_label
            
            if is_image:
                return  # 图片不需要播放控制
            
            # 创建播放控制区域（在滑块下方）
            if playback_frame is None:
                playback_frame = ttk.Frame(control_frame)
                playback_frame.pack(fill=tk.X, pady=(10, 0))
                
                # 播放/暂停按钮
                button_row = ttk.Frame(playback_frame)
                button_row.pack(fill=tk.X, pady=5)
                
                play_button = ttk.Button(button_row, text="▶ 播放", command=play_video, style='Primary.TButton')
                play_button.pack(side=tk.LEFT, padx=5)
                
                pause_button = ttk.Button(button_row, text="⏸ 暂停", command=pause_video, style='Primary.TButton', state=tk.DISABLED)
                pause_button.pack(side=tk.LEFT, padx=5)
                
                # 进度条和标签
                progress_row = ttk.Frame(playback_frame)
                progress_row.pack(fill=tk.X, pady=5)
                
                ttk.Label(progress_row, text="播放进度:", font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=5)
                
                video_progress_var = tk.DoubleVar(value=0.0)
                video_progress_scale = ttk.Scale(progress_row, from_=0.0, to=100.0, 
                                                orient=tk.HORIZONTAL, variable=video_progress_var, length=600,
                                                command=on_progress_change)
                video_progress_scale.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
                
                # 保存变量引用以便后续更新
                preview_window._video_progress_var = video_progress_var
                
                video_progress_label = ttk.Label(progress_row, text="00:00 / 00:00", font=('Segoe UI', 9))
                video_progress_label.pack(side=tk.LEFT, padx=5)
            
            # 更新总帧数
            if hasattr(preview_window, '_video_frame_count_original'):
                video_total_frames = min(preview_window._video_frame_count_original, 
                                        preview_window._video_frame_count_compressed)
                video_progress_scale.config(to=video_total_frames if video_total_frames > 0 else 100)
            
            # 显示播放控制
            playback_frame.pack(fill=tk.X, pady=(10, 0))
        
        def play_video():
            """播放视频"""
            nonlocal video_playing, video_current_frame
            
            if not is_image and hasattr(preview_window, '_video_cap_original') and hasattr(preview_window, '_video_cap_compressed'):
                video_playing = True
                play_button.config(state=tk.DISABLED)
                pause_button.config(state=tk.NORMAL)
                update_video_frame()
        
        def pause_video():
            """暂停视频"""
            nonlocal video_playing
            
            video_playing = False
            play_button.config(state=tk.NORMAL)
            pause_button.config(state=tk.DISABLED)
            
            if video_play_timer:
                preview_window.after_cancel(video_play_timer)
                video_play_timer = None
        
        def on_progress_change(value):
            """进度条变化时的处理"""
            nonlocal video_current_frame
            if not is_image:
                try:
                    frame_num = int(float(value))
                    video_current_frame = frame_num
                    seek_to_frame(frame_num)
                except:
                    pass
        
        def seek_to_frame(frame_num):
            """跳转到指定帧"""
            if not is_image and hasattr(preview_window, '_video_cap_original') and hasattr(preview_window, '_video_cap_compressed'):
                import cv2
                
                # 设置两个视频到相同帧位置
                preview_window._video_cap_original.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                preview_window._video_cap_compressed.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                
                # 读取当前帧
                ret_orig, frame_orig = preview_window._video_cap_original.read()
                ret_comp, frame_comp = preview_window._video_cap_compressed.read()
                
                if ret_orig and ret_comp:
                    # 更新显示
                    update_frame_display(frame_orig, frame_comp)
                    
                    # 更新进度标签
                    if video_progress_label:
                        current_time = frame_num / preview_window._video_fps_original if preview_window._video_fps_original > 0 else 0
                        total_time = preview_window._video_duration_original
                        video_progress_label.config(text=f"{self._format_time(current_time)} / {self._format_time(total_time)}")
        
        def update_frame_display(frame_orig, frame_comp):
            """更新帧显示"""
            nonlocal original_img, compressed_img, original_photo, compressed_photo
            
            import cv2
            
            # 转换BGR到RGB
            frame_orig_rgb = cv2.cvtColor(frame_orig, cv2.COLOR_BGR2RGB)
            frame_comp_rgb = cv2.cvtColor(frame_comp, cv2.COLOR_BGR2RGB)
            
            # 转换为PIL Image
            frame_orig_pil = Image.fromarray(frame_orig_rgb)
            frame_comp_pil = Image.fromarray(frame_comp_rgb)
            
            # 缩放图片（使用之前计算的display_width和display_height）
            original_display = frame_orig_pil.resize((display_width, int(display_width * frame_orig_pil.height / frame_orig_pil.width)), Image.LANCZOS)
            compressed_display = frame_comp_pil.resize((display_width, int(display_width * frame_comp_pil.height / frame_comp_pil.width)), Image.LANCZOS)
            
            # 统一高度
            max_height = max(original_display.height, compressed_display.height)
            if original_display.height != max_height:
                original_display = original_display.resize((int(original_display.width * max_height / original_display.height), max_height), Image.LANCZOS)
            if compressed_display.height != max_height:
                compressed_display = compressed_display.resize((int(compressed_display.width * max_height / compressed_display.height), max_height), Image.LANCZOS)
            
            # 创建PhotoImage对象
            original_photo = ImageTk.PhotoImage(original_display)
            compressed_photo = ImageTk.PhotoImage(compressed_display)
            
            # 保存原始图片对象
            original_img = original_display
            compressed_img = compressed_display
            
            # 更新显示
            update_display()
        
        def update_video_frame():
            """更新视频帧（播放时调用）"""
            nonlocal video_playing, video_current_frame, video_play_timer
            
            if not video_playing or is_image:
                return
            
            if not hasattr(preview_window, '_video_cap_original') or not hasattr(preview_window, '_video_cap_compressed'):
                return
            
            import cv2
            
            # 读取下一帧
            ret_orig, frame_orig = preview_window._video_cap_original.read()
            ret_comp, frame_comp = preview_window._video_cap_compressed.read()
            
            if ret_orig and ret_comp:
                # 更新当前帧号
                video_current_frame = int(preview_window._video_cap_original.get(cv2.CAP_PROP_POS_FRAMES))
                
                # 更新显示
                update_frame_display(frame_orig, frame_comp)
                
                # 更新进度条
                if video_progress_scale and hasattr(preview_window, '_video_progress_var'):
                    preview_window._video_progress_var.set(video_current_frame)
                
                # 更新进度标签
                if video_progress_label:
                    current_time = video_current_frame / preview_window._video_fps_original if preview_window._video_fps_original > 0 else 0
                    total_time = preview_window._video_duration_original
                    video_progress_label.config(text=f"{self._format_time(current_time)} / {self._format_time(total_time)}")
                
                # 计算下一帧的延迟（毫秒）
                if preview_window._video_fps_original > 0:
                    delay_ms = int(1000 / preview_window._video_fps_original)
                else:
                    delay_ms = 33  # 默认30fps
                
                # 安排下一帧更新
                video_play_timer = preview_window.after(delay_ms, update_video_frame)
            else:
                # 视频播放完毕，自动暂停
                pause_video()
                # 重置到开始位置
                preview_window._video_cap_original.set(cv2.CAP_PROP_POS_FRAMES, 0)
                preview_window._video_cap_compressed.set(cv2.CAP_PROP_POS_FRAMES, 0)
                video_current_frame = 0
                if video_progress_scale and hasattr(preview_window, '_video_progress_var'):
                    preview_window._video_progress_var.set(0)
        
        ttk.Button(button_frame, text="开始压缩预览", command=load_media).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="关闭", command=on_closing).pack(side=tk.RIGHT, padx=5)
        
        # 窗口关闭时停止播放（更新关闭处理）
        def on_closing_with_cleanup():
            if not is_image:
                pause_video()
            on_closing_base()
        
        preview_window.protocol("WM_DELETE_WINDOW", on_closing_with_cleanup)
        
        # 窗口大小变化时更新显示
        def on_resize(event):
            if original_photo and compressed_photo:
                update_display()
        
        preview_canvas.bind('<Configure>', on_resize)
    
    def show_history(self):
        """显示压缩历史记录"""
        history = self.history_manager.get_all()
        
        if not history:
            messagebox.showinfo("提示", "暂无压缩历史记录")
            return
        
        # 创建历史记录窗口
        history_window = tk.Toplevel(self.root)
        history_window.title("压缩历史记录")
        history_window.geometry("800x600")
        history_window.transient(self.root)
        
        main_frame = ttk.Frame(history_window, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 标题
        ttk.Label(main_frame, text="压缩历史记录", font=("Arial", 14, "bold")).pack(pady=(0, 10))
        
        # 创建列表
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        history_tree = ttk.Treeview(
            tree_frame,
            columns=("time", "source", "target", "files", "compression_rate", "time_taken"),
            yscrollcommand=scrollbar.set,
            selectmode=tk.BROWSE
        )
        
        history_tree.heading("#0", text="序号")
        history_tree.heading("time", text="时间")
        history_tree.heading("source", text="源文件夹")
        history_tree.heading("target", text="目标文件夹")
        history_tree.heading("files", text="文件数")
        history_tree.heading("compression_rate", text="压缩率")
        history_tree.heading("time_taken", text="用时")
        
        history_tree.column("#0", width=50)
        history_tree.column("time", width=150)
        history_tree.column("source", width=200)
        history_tree.column("target", width=200)
        history_tree.column("files", width=80)
        history_tree.column("compression_rate", width=100)
        history_tree.column("time_taken", width=100)
        
        scrollbar.config(command=history_tree.yview)
        history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 填充数据
        for idx, record in enumerate(reversed(history), 1):
            timestamp = record.get('timestamp', '')
            try:
                dt = datetime.datetime.fromisoformat(timestamp)
                time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                time_str = timestamp
            
            stats = record.get('stats', {})
            history_tree.insert('', 'end', text=str(idx), values=(
                time_str,
                os.path.basename(record.get('source_dir', '')),
                os.path.basename(record.get('target_dir', '')),
                stats.get('total_files', 0),
                f"{stats.get('compression_rate', 0):.2f}%",
                self._format_time(stats.get('time_taken', 0))
            ))
        
        # 按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        def view_details():
            selection = history_tree.selection()
            if not selection:
                messagebox.showinfo("提示", "请选择一条记录查看详情")
                return
            
            item = history_tree.item(selection[0])
            idx = int(item['text']) - 1
            record = list(reversed(history))[idx]
            
            self._show_history_details(record)
        
        ttk.Button(button_frame, text="查看详情", command=view_details).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="清空历史", command=lambda: (self.history_manager.clear(), history_window.destroy(), messagebox.showinfo("成功", "历史记录已清空"))).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="关闭", command=history_window.destroy).pack(side=tk.RIGHT, padx=5)
    
    def _show_history_details(self, record):
        """显示历史记录详情"""
        details_window = tk.Toplevel(self.root)
        details_window.title("压缩记录详情")
        details_window.transient(self.root)
        self._center_window(details_window, 600, 500)
        
        main_frame = ttk.Frame(details_window, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        stats = record.get('stats', {})
        config = record.get('config', {})
        
        details_text = f"""
压缩记录详情
{'='*50}

时间: {record.get('timestamp', '')}
源文件夹: {record.get('source_dir', '')}
目标文件夹: {record.get('target_dir', '')}

统计信息:
  总文件数: {stats.get('total_files', 0)}
  成功压缩: {stats.get('completed_files', 0)}
  原始大小: {FileProcessor.format_size(stats.get('original_size', 0))}
  压缩后大小: {FileProcessor.format_size(stats.get('compressed_size', 0))}
  压缩率: {stats.get('compression_rate', 0):.2f}%
  用时: {self._format_time(stats.get('time_taken', 0))}

压缩设置:
  照片质量: {config.get('photo_quality', 85)}
  视频CRF: {config.get('video_crf', 23)}
  视频预设: {config.get('video_preset', 'medium')}
  编码模式: {config.get('use_gpu', 'cpu').upper()}
"""
        
        text_widget = tk.Text(main_frame, wrap=tk.WORD, font=("Arial", 10))
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert('1.0', details_text)
        text_widget.config(state=tk.DISABLED)
        
        ttk.Button(main_frame, text="关闭", command=details_window.destroy).pack(pady=10)
    
    def _show_compression_summary(self, total_time, compression_rate, stats):
        """显示压缩统计摘要"""
        try:
            # 统计各类型文件
            image_count = 0
            image_original_size = 0
            image_compressed_size = 0
            
            video_count = 0
            video_original_size = 0
            video_compressed_size = 0
            
            other_count = 0
            other_original_size = 0
            other_compressed_size = 0
            
            completed_count = 0
            failed_count = 0
            copied_count = 0
            
            image_exts = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif']
            video_exts = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.m4v', '.webm']
            
            for file_info in self.file_list:
                # 兼容两种格式
                if hasattr(file_info, 'status'):
                    status = file_info.status
                    file_ext = file_info.file_ext.lower()
                    original_size = file_info.file_size
                    actual_size = file_info.actual_size
                else:
                    status = file_info.get('status', '')
                    file_ext = file_info.get('file_ext', '').lower()
                    original_size = file_info.get('file_size', 0)
                    actual_size = file_info.get('actual_size', 0)
                
                if status == '已完成':
                    completed_count += 1
                elif status == '处理失败':
                    failed_count += 1
                elif status == '已复制':
                    copied_count += 1
                
                if file_ext in image_exts:
                    image_count += 1
                    image_original_size += original_size
                    image_compressed_size += actual_size if actual_size > 0 else original_size
                elif file_ext in video_exts:
                    video_count += 1
                    video_original_size += original_size
                    video_compressed_size += actual_size if actual_size > 0 else original_size
                else:
                    other_count += 1
                    other_original_size += original_size
                    other_compressed_size += actual_size if actual_size > 0 else original_size
            
            # 创建摘要窗口
            summary_window = tk.Toplevel(self.root)
            summary_window.title("压缩统计摘要")
            summary_window.transient(self.root)
            summary_window.grab_set()
            self._center_window(summary_window, 600, 600)
            
            main_frame = ttk.Frame(summary_window, padding="20")
            main_frame.pack(fill=tk.BOTH, expand=True)
            
            title_label = ttk.Label(main_frame, text="压缩统计摘要", font=("Arial", 14, "bold"))
            title_label.pack(pady=(0, 20))
            
            text_frame = ttk.Frame(main_frame)
            text_frame.pack(fill=tk.BOTH, expand=True)
            
            scrollbar = ttk.Scrollbar(text_frame)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            summary_text = tk.Text(text_frame, yscrollcommand=scrollbar.set, wrap=tk.WORD, font=("Arial", 10))
            summary_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.config(command=summary_text.yview)
            
            summary_content = f"""
压缩完成统计
{'='*50}

总体统计：
  总文件数: {len(self.file_list)}
  成功压缩: {completed_count}
  复制文件: {copied_count}
  处理失败: {failed_count}
  
  原始大小: {FileProcessor.format_size(stats.get('original_size', 0))}
  压缩后大小: {FileProcessor.format_size(stats.get('compressed_size', 0))}
  节省空间: {FileProcessor.format_size(stats.get('original_size', 0) - stats.get('compressed_size', 0))}
  压缩率: {compression_rate:.2f}%
  实际用时: {total_time}

{'='*50}

按文件类型统计：

图片文件：
  文件数量: {image_count}
  原始大小: {FileProcessor.format_size(image_original_size)}
  压缩后大小: {FileProcessor.format_size(image_compressed_size)}
  {'压缩率: ' + f'{(1 - image_compressed_size / image_original_size) * 100:.2f}%' if image_original_size > 0 else ''}

视频文件：
  文件数量: {video_count}
  原始大小: {FileProcessor.format_size(video_original_size)}
  压缩后大小: {FileProcessor.format_size(video_compressed_size)}
  {'压缩率: ' + f'{(1 - video_compressed_size / video_original_size) * 100:.2f}%' if video_original_size > 0 else ''}

其他文件：
  文件数量: {other_count}
  原始大小: {FileProcessor.format_size(other_original_size)}

{'='*50}

压缩设置：
  照片质量: {self.config_manager.get('photo_quality', 85)}
  视频CRF: {self.config_manager.get('video_crf', 23)}
  视频预设: {self.config_manager.get('video_preset', 'medium')}
  编码模式: {self.config_manager.get('use_gpu', 'cpu').upper()}
"""
            
            summary_text.insert('1.0', summary_content)
            summary_text.config(state=tk.DISABLED)
            
            button_frame = ttk.Frame(main_frame)
            button_frame.pack(fill=tk.X, pady=(10, 0))
            
            def export_summary():
                filename = filedialog.asksaveasfilename(
                    defaultextension=".txt",
                    filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                    initialfile=f"compression_summary_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                )
                if filename:
                    try:
                        with open(filename, 'w', encoding='utf-8') as f:
                            f.write(summary_content)
                        messagebox.showinfo("成功", f"统计摘要已导出到: {filename}")
                    except Exception as e:
                        messagebox.showerror("错误", f"导出失败: {str(e)}")
            
            ttk.Button(button_frame, text="导出摘要", command=export_summary).pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text="关闭", command=summary_window.destroy).pack(side=tk.RIGHT, padx=5)
            
        except Exception as e:
            self.logger.error(f"显示压缩统计摘要失败: {e}")
            messagebox.showerror("错误", f"显示统计摘要失败: {str(e)}")
    
    def _apply_file_filter(self):
        """应用文件类型过滤"""
        filter_type = self.file_filter_type.get()
        self.logger.info(f"文件过滤类型已更改为: {filter_type}")
        # Treeview不支持直接隐藏，这里仅更新状态
    
    def _select_all_files(self):
        """全选文件"""
        if not self.file_listbox:
            return
        self.selected_files = set(range(len(self.file_list)))
        try:
            for item_id in self.file_index_map.values():
                self.file_listbox.selection_add(item_id)
        except:
            pass
        messagebox.showinfo("成功", f"已全选 {len(self.selected_files)} 个文件")
    
    def _deselect_all_files(self):
        """取消全选"""
        if not self.file_listbox:
            return
        self.selected_files.clear()
        try:
            self.file_listbox.selection_set([])
        except:
            pass
        messagebox.showinfo("成功", "已取消全选")
    
    def _invert_selection(self):
        """反选文件"""
        if not self.file_listbox:
            return
        try:
            all_items = set(self.file_index_map.values())
            current_selection = set(self.file_listbox.selection())
            new_selection = all_items - current_selection
            self.file_listbox.selection_set(list(new_selection))
            
            # 兼容两种格式获取source_path
            self.selected_files = set()
            for i, file_info in enumerate(self.file_list):
                if hasattr(file_info, 'source_path'):
                    source_path = file_info.source_path
                else:
                    source_path = file_info.get('source_path') or \
                                 os.path.join(file_info.get('source_dir', ''), 
                                             file_info.get('rel_path', ''),
                                             file_info.get('file_name', '')) if file_info.get('rel_path', '') != '.' else \
                                 os.path.join(file_info.get('source_dir', ''), file_info.get('file_name', ''))
                
                if self.file_index_map.get(source_path) in new_selection:
                    self.selected_files.add(i)
            messagebox.showinfo("成功", f"反选后选中 {len(self.selected_files)} 个文件")
        except Exception as e:
            self.logger.error(f"反选操作失败: {e}")
    
    def _exclude_selected(self):
        """排除选中的文件或文件夹"""
        if not self.file_listbox:
            return
        try:
            selected_items = self.file_listbox.selection()
            if not selected_items:
                messagebox.showinfo("提示", "请先选择要排除的文件或文件夹")
                return
            
            excluded_count = 0
            excluded_folders = set()
            
            def exclude_folder_recursive(folder_node, folder_name):
                """递归排除文件夹下的所有文件"""
                count = 0
                for child in self.file_listbox.get_children(folder_node):
                    child_text = self.file_listbox.item(child, 'text')
                    child_values = self.file_listbox.item(child, 'values')
                    
                    if child_text and (not child_values or not child_values[0]):
                        # 是文件夹，递归处理
                        count += exclude_folder_recursive(child, child_text)
                    else:
                        # 是文件，排除它
                        if child_values and child_values[0]:
                            for i, file_info in enumerate(self.file_list):
                                # 兼容两种格式获取file_name
                                if hasattr(file_info, 'file_name'):
                                    file_name = file_info.file_name
                                else:
                                    file_name = file_info.get('file_name', '')
                                
                                if file_name == child_values[0]:
                                    # 获取source_path
                                    if hasattr(file_info, 'source_path'):
                                        source_path = file_info.source_path
                                    else:
                                        source_path = file_info.get('source_path') or \
                                                     os.path.join(file_info.get('source_dir', ''), 
                                                                 file_info.get('rel_path', ''),
                                                                 file_info.get('file_name', '')) if file_info.get('rel_path', '') != '.' else \
                                                     os.path.join(file_info.get('source_dir', ''), file_info.get('file_name', ''))
                                    
                                    if self.file_index_map.get(source_path) == child:
                                        if i not in self.excluded_files:
                                            self.excluded_files.add(i)
                                            count += 1
                                            self._update_file_status(i, '已排除')
                                            
                                            # 更新统计数据（移除排除文件的大小）
                                            if hasattr(file_info, 'file_size'):
                                                file_size = file_info.file_size
                                                estimated_size = file_info.estimated_size
                                            else:
                                                file_size = file_info.get('file_size', 0)
                                                estimated_size = file_info.get('estimated_size', 0)
                                            
                                            self.total_original_size -= file_size
                                            self.total_estimated_size -= estimated_size
                                        break
                return count
            
            for item_id in selected_items:
                item_text = self.file_listbox.item(item_id, 'text')
                item_values = self.file_listbox.item(item_id, 'values')
                
                # 检查是否为文件夹节点（有text且values为空或第一个值为空）
                if item_text and (not item_values or not item_values[0]):
                    # 是文件夹，排除整个文件夹
                    excluded_folders.add(item_text)
                    folder_count = exclude_folder_recursive(item_id, item_text)
                    excluded_count += folder_count
                else:
                    # 是文件节点，排除单个文件
                    for i, file_info in enumerate(self.file_list):
                        # 兼容两种格式获取source_path
                        if hasattr(file_info, 'source_path'):
                            source_path = file_info.source_path
                        else:
                            source_path = file_info.get('source_path') or \
                                         os.path.join(file_info.get('source_dir', ''), 
                                                     file_info.get('rel_path', ''),
                                                     file_info.get('file_name', '')) if file_info.get('rel_path', '') != '.' else \
                                         os.path.join(file_info.get('source_dir', ''), file_info.get('file_name', ''))
                        
                        if self.file_index_map.get(source_path) == item_id:
                            if i not in self.excluded_files:
                                self.excluded_files.add(i)
                                excluded_count += 1
                                self._update_file_status(i, '已排除')
                                
                                # 更新统计数据（移除排除文件的大小）
                                if hasattr(file_info, 'file_size'):
                                    file_size = file_info.file_size
                                    estimated_size = file_info.estimated_size
                                else:
                                    file_size = file_info.get('file_size', 0)
                                    estimated_size = file_info.get('estimated_size', 0)
                                
                                self.total_original_size -= file_size
                                self.total_estimated_size -= estimated_size
                            break
            
            if excluded_count > 0:
                folder_msg = f"，包含 {len(excluded_folders)} 个文件夹" if excluded_folders else ""
                messagebox.showinfo("成功", f"已排除 {excluded_count} 个文件{folder_msg}")
        except Exception as e:
            self.logger.error(f"排除文件失败: {e}")
            messagebox.showerror("错误", f"排除文件失败: {str(e)}")
    
    def _unexclude_all(self):
        """取消所有排除"""
        excluded_count = len(self.excluded_files)
        if excluded_count == 0:
            messagebox.showinfo("提示", "没有已排除的文件")
            return
        
        excluded_indices = list(self.excluded_files)
        self.excluded_files.clear()
        
        for i in excluded_indices:
            if i < len(self.file_list):
                file_info = self.file_list[i]
                if file_info.get('status') == '已排除':
                    self._update_file_status(i, '等待压缩')
        
        messagebox.showinfo("成功", f"已取消排除 {excluded_count} 个文件")
    
    def open_output_folder(self):
        """打开输出文件夹"""
        target = self.target_dir.get()
        if target and os.path.exists(target):
            if sys.platform.startswith('win'):
                os.startfile(target)
            elif sys.platform.startswith('darwin'):
                import subprocess
                subprocess.run(['open', target])
            else:
                import subprocess
                subprocess.run(['xdg-open', target])
        else:
            messagebox.showinfo("提示", "输出文件夹不存在")
    
    def show_help(self):
        """显示使用说明"""
        help_text = """批量文件压缩工具 v2.0 使用说明

═══════════════════════════════════════════

📋 基本操作流程：

1️⃣ 选择文件夹
   • 点击"📁 源文件夹"中的"📂 浏览"按钮选择源文件夹
   • 点击"📁 目标文件夹"中的"📂 浏览"按钮选择输出位置
   • 点击"🔄 刷新"按钮扫描文件列表

2️⃣ 设置压缩参数
   • 📷 照片质量：0-100（推荐85）
   • 🎬 视频CRF：18-28（18质量最好，28压缩率最高）
   • 预设：ultrafast（最快）到 veryslow（最慢）
   • 💻 编码模式：CPU / AMD GPU / Nvidia GPU

3️⃣ 选择文件（可选）
   • 使用"全选"、"全不选"、"反选"批量选择
   • 使用"🚫 排除"排除不需要的文件
   • 支持排除整个文件夹

4️⃣ 开始压缩
   • 点击"▶️ 开始压缩"或按 Ctrl+R
   • 可随时暂停（Ctrl+P）、恢复（Ctrl+U）或停止（Ctrl+T）

═══════════════════════════════════════════

📄 支持的文件格式：

图片格式：
  ✅ JPG, JPEG, PNG, GIF, BMP, TIFF, TIF, WEBP

视频格式：
  ✅ MP4, AVI, MOV, MKV, WMV, FLV, M4V, WEBM, 3GP

⚠️ 注意：非媒体文件默认自动排除

═══════════════════════════════════════════

⚡ GPU硬件加速：

AMD GPU（AMF编码器）：
  • 支持H.264和HEVC编码
  • 需要AMD Radeon系列显卡
  • 可设置编码器和比特率

Nvidia GPU（NVENC编码器）：
  • 支持H.264和HEVC编码
  • 需要Nvidia GeForce/Quadro系列显卡
  • 可设置编码器、预设和比特率

💡 自动回退：GPU加速失败时自动使用CPU编码

═══════════════════════════════════════════

✨ 高级功能：

🔄 暂停/恢复：
  • 压缩过程中可随时暂停
  • 暂停后可以恢复继续
  • 暂停时间不计入总压缩时间

💾 断点续传：
  • 程序意外关闭后自动保存进度
  • 重新启动时可选择继续未完成的压缩
  • 自动跳过已完成的文件

👁️ 压缩预览：
  • 选择单个文件测试压缩效果
  • 查看压缩率和文件大小对比
  • 预览文件保存到临时目录

📋 历史记录：
  • 自动保存每次压缩的统计信息
  • 查看详细压缩参数和结果
  • 支持导出和清空历史记录

🔍 文件过滤：
  • 按类型过滤：全部、图片、视频、其他
  • 批量操作：全选、全不选、反选、排除

═══════════════════════════════════════════

⌨️ 快捷键：

Ctrl+O    - 选择源文件夹
Ctrl+D    - 选择目标文件夹
Ctrl+S    - 保存设置
F5        - 刷新文件列表
Ctrl+R    - 开始压缩
Ctrl+P    - 暂停压缩
Ctrl+U    - 恢复压缩
Ctrl+T    - 停止压缩
Ctrl+E    - 打开输出文件夹
Ctrl+Q    - 退出程序

═══════════════════════════════════════════

⚠️ 注意事项：

• 首次使用建议选择少量文件测试效果
• 大文件压缩可能需要较长时间
• 确保目标文件夹有足够的磁盘空间
• GPU加速需要相应的硬件和驱动程序
• 压缩过程中可以随时暂停、恢复或停止"""
        
        # 创建一个更大的窗口显示使用说明
        help_window = tk.Toplevel(self.root)
        help_window.title("使用说明 - 批量文件压缩工具 v2.0")
        help_window.transient(self.root)
        self._center_window(help_window, 700, 600)
        
        # 创建文本框和滚动条
        text_frame = ttk.Frame(help_window, padding="10")
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        text_widget = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=('Segoe UI', 9),
            yscrollcommand=scrollbar.set,
            padx=10,
            pady=10
        )
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=text_widget.yview)
        
        text_widget.insert('1.0', help_text)
        text_widget.config(state=tk.DISABLED)
        
        # 关闭按钮
        ttk.Button(help_window, text="关闭", command=help_window.destroy).pack(pady=10)
    
    def show_about(self):
        """显示关于对话框"""
        about_text = """批量文件压缩工具 v2.0

═══════════════════════════════════════════

📦 版本信息：
  版本号：v2.0
  发布日期：2024-2025
  架构：模块化重构版本

═══════════════════════════════════════════

✨ 核心功能：

📸 图片压缩
   • 支持JPG, JPEG, PNG, GIF, BMP, TIFF, WEBP
   • 可调节压缩质量（0-100）
   • 自动调整图片尺寸

🎬 视频压缩
   • 支持MP4, AVI, MOV, MKV, WMV, FLV等主流格式
   • H.264/HEVC编码
   • 可调节CRF值（18-28）
   • 多种编码预设（ultrafast到veryslow）

⚡ 硬件加速
   • AMD GPU加速（AMF编码器）
   • Nvidia GPU加速（NVENC编码器）
   • 自动检测硬件支持
   • 失败时自动回退CPU编码

🔄 任务控制
   • 暂停/恢复压缩
   • 断点续传功能
   • 实时进度显示
   • 批量文件处理

📋 文件管理
   • 树形结构显示
   • 批量选择操作
   • 文件夹排除
   • 自动排除非媒体文件
   • 文件类型过滤

📊 统计功能
   • 压缩预览测试
   • 历史记录管理
   • 详细统计信息
   • 压缩率计算

💾 内存优化
   • 紧凑数据结构
   • 延迟路径计算
   • 压缩后内存清理
   • 支持大文件列表

═══════════════════════════════════════════

🏗️ 模块化架构：

ConfigManager
   └─ 配置管理和验证

FileProcessor
   └─ 路径验证、权限检查、大小估算

ImageCompressor
   └─ 图片压缩处理

VideoCompressor
   └─ 视频压缩（CPU/AMD/Nvidia）

CompressionHistory
   └─ 历史记录管理

FileInfo
   └─ 紧凑数据结构（内存优化）

═══════════════════════════════════════════

🛠️ 技术规格：

开发语言：Python 3.9+
GUI框架：Tkinter (ttk)
图像处理：Pillow (PIL)
视频处理：FFmpeg
日志系统：Python logging

支持格式：
  图片：JPG, JPEG, PNG, GIF, BMP, TIFF, WEBP
  视频：MP4, AVI, MOV, MKV, WMV, FLV, M4V, WEBM, 3GP

压缩算法：
  图片：JPEG质量压缩
  视频：H.264/HEVC编码（CRF模式）

硬件要求：
  CPU：双核及以上
  显卡（可选）：AMD Radeon / Nvidia GeForce
  内存：建议2GB以上
  系统：Windows 10/11

═══════════════════════════════════════════

📄 版权信息：

Copyright © 2024-2025 批量文件压缩工具

本软件仅供个人学习和非商业用途使用。
未经授权，禁止用于商业目的。

保留所有权利

═══════════════════════════════════════════

👨‍💻 开发者信息：

开发者：李林凯
联系邮箱：1205934613@qq.com

感谢使用批量文件压缩工具！

如有问题或建议，欢迎反馈。"""
        
        # 创建一个更大的窗口显示关于信息
        about_window = tk.Toplevel(self.root)
        about_window.title("关于 - 批量文件压缩工具 v2.0")
        about_window.transient(self.root)
        self._center_window(about_window, 650, 700)
        
        # 创建文本框和滚动条
        text_frame = ttk.Frame(about_window, padding="10")
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        text_widget = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=('Segoe UI', 9),
            yscrollcommand=scrollbar.set,
            padx=10,
            pady=10
        )
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=text_widget.yview)
        
        text_widget.insert('1.0', about_text)
        text_widget.config(state=tk.DISABLED)
        
        # 关闭按钮
        ttk.Button(about_window, text="关闭", command=about_window.destroy).pack(pady=10)
    
    def quit_application(self):
        """优雅退出程序"""
        if self.is_compressing:
            response = messagebox.askyesno("确认退出", "正在进行压缩，确定要退出吗？")
            if not response:
                return
            
            self.stop_compression()
            time.sleep(0.5)
        
        # 停止Web服务器
        if self.web_server and self.web_server_running:
            try:
                self.web_server.stop()
                self.logger.info("Web服务器已停止")
            except Exception as e:
                self.logger.error(f"停止Web服务器失败: {e}")
        
        # 保存配置
        try:
            self.config_manager.set('source_dir', self.source_dir.get())
            self.config_manager.set('target_dir', self.target_dir.get())
            self.config_manager.save()
        except:
            pass
        
        self.logger.info("应用关闭 - 批量文件压缩工具")
        
        # 清理内存
        self._cleanup_memory()
        self.root.destroy()
    
    def toggle_web_server(self):
        """切换Web服务器状态"""
        if self.web_server_running:
            self.stop_web_server()
        else:
            self.start_web_server()
    
    def start_web_server(self):
        """启动Web服务器"""
        if not HAS_WEB_SERVER or not self.web_server:
            messagebox.showerror("错误", "Web服务器功能不可用")
            return
        
        if self.web_server_running:
            messagebox.showinfo("提示", "Web服务器已在运行中")
            return
        
        try:
            self.web_server.start()
            self.web_server_running = True
            
            # 更新按钮文本
            if hasattr(self, 'web_server_button'):
                self.web_server_button.config(text="停止Web服务")
            
            # 获取服务器URL
            server_url = self.web_server.get_url()
            if server_url:
                messagebox.showinfo(
                    "Web服务器已启动",
                    f"Web服务器已成功启动！\n\n"
                    f"访问地址：{server_url}\n\n"
                    f"在局域网内的其他设备可以通过此地址访问Web界面。"
                )
                self.logger.info(f"Web服务器已启动: {server_url}")
            else:
                messagebox.showinfo("Web服务器已启动", "Web服务器已启动，但无法获取访问地址")
                self.logger.info("Web服务器已启动")
        except Exception as e:
            error_msg = f"启动Web服务器失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
            self.web_server_running = False
            if hasattr(self, 'web_server_button'):
                self.web_server_button.config(text="启动Web服务")
    
    def stop_web_server(self):
        """停止Web服务器"""
        if not HAS_WEB_SERVER or not self.web_server:
            return
        
        if not self.web_server_running:
            messagebox.showinfo("提示", "Web服务器未运行")
            return
        
        try:
            self.web_server.stop()
            self.web_server_running = False
            
            # 更新按钮文本
            if hasattr(self, 'web_server_button'):
                self.web_server_button.config(text="启动Web服务")
            
            messagebox.showinfo("提示", "Web服务器已停止")
            self.logger.info("Web服务器已停止")
        except Exception as e:
            error_msg = f"停止Web服务器失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
    
    def show_log_window(self):
        """显示实时日志窗口"""
        if self.log_window and self.log_window.winfo_exists():
            # 如果窗口已存在，将其提升到前台
            self.log_window.lift()
            self.log_window.focus()
            return
        
        # 创建日志窗口
        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("实时日志 - 批量文件压缩工具")
        self.log_window.transient(self.root)
        self._center_window(self.log_window, 800, 600)
        
        # 创建主框架
        main_frame = ttk.Frame(self.log_window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建工具栏
        toolbar = ttk.Frame(main_frame)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        
        ttk.Label(toolbar, text="实时日志", font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT, padx=6)
        
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12, pady=4)
        
        ttk.Button(toolbar, text="清空日志", command=self._clear_log, style='Primary.TButton').pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="保存日志", command=self._save_log, style='Primary.TButton').pack(side=tk.LEFT, padx=4)
        
        # 创建文本区域和滚动条
        text_frame = ttk.Frame(main_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar_y = ttk.Scrollbar(text_frame)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        scrollbar_x = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.log_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=('Consolas', 9),
            bg='#1e1e1e',
            fg='#d4d4d4',
            insertbackground='#ffffff',
            selectbackground='#264f78',
            yscrollcommand=scrollbar_y.set,
            xscrollcommand=scrollbar_x.set,
            padx=10,
            pady=10
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar_y.config(command=self.log_text.yview)
        scrollbar_x.config(command=self.log_text.xview)
        
        # 配置文本样式（可选）
        self.log_text.tag_config('INFO', foreground='#4ec9b0')
        self.log_text.tag_config('WARNING', foreground='#ce9178')
        self.log_text.tag_config('ERROR', foreground='#f48771')
        self.log_text.tag_config('DEBUG', foreground='#9cdcfe')
        
        # 添加GUI日志处理器
        if self.log_text:
            # 先移除旧的处理器（如果存在）
            for handler in self.logger.handlers[:]:
                if isinstance(handler, TextHandler):
                    self.logger.removeHandler(handler)
            
            # 重新设置日志系统，添加GUI处理器
            gui_handler = TextHandler(self.log_text)
            gui_handler.setLevel(logging.INFO)
            log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            date_format = '%Y-%m-%d %H:%M:%S'
            gui_formatter = logging.Formatter(log_format, datefmt=date_format)
            gui_handler.setFormatter(gui_formatter)
            self.logger.addHandler(gui_handler)
            self._log_handler = gui_handler  # 保存引用，便于关闭时移除
            
            # 添加欢迎信息
            self.log_text.insert(tk.END, f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 实时日志窗口已打开\n")
            self.log_text.insert(tk.END, "=" * 80 + "\n\n")
            self.log_text.see(tk.END)
        
        # 关闭按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(8, 0))
        
        ttk.Button(button_frame, text="关闭", command=self._close_log_window, style='Primary.TButton').pack(side=tk.RIGHT, padx=4)
        
        # 窗口关闭事件
        self.log_window.protocol("WM_DELETE_WINDOW", self._close_log_window)
    
    def _clear_log(self):
        """清空日志"""
        if self.log_text:
            self.log_text.delete('1.0', tk.END)
            self.log_text.insert(tk.END, f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 日志已清空\n")
            self.log_text.see(tk.END)
    
    def _save_log(self):
        """保存日志到文件"""
        if not self.log_text:
            return
        
        try:
            log_content = self.log_text.get('1.0', tk.END)
            log_file = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                title="保存日志"
            )
            
            if log_file:
                with open(log_file, 'w', encoding='utf-8') as f:
                    f.write(log_content)
                messagebox.showinfo("成功", "日志已保存")
                self.logger.info(f"日志已保存到: {log_file}")
        except Exception as e:
            messagebox.showerror("错误", f"保存日志失败: {str(e)}")
            self.logger.error(f"保存日志失败: {e}")
    
    def _close_log_window(self):
        """关闭日志窗口"""
        if self.log_window:
            # 移除日志处理器（如果存在）
            if hasattr(self, '_log_handler'):
                try:
                    self.logger.removeHandler(self._log_handler)
                except (ValueError, AttributeError):
                    pass  # 处理器可能已经被移除
                delattr(self, '_log_handler')
            
            # 或者移除所有TextHandler类型的处理器
            for handler in self.logger.handlers[:]:
                if isinstance(handler, TextHandler):
                    try:
                        self.logger.removeHandler(handler)
                    except (ValueError, AttributeError):
                        pass
            
            self.log_window.destroy()
            self.log_window = None
            self.log_text = None
    
    def _cleanup_memory(self):
        """清理内存，移除不需要的数据"""
        try:
            # 对于已完成的文件，可以移除estimated_size等不需要的数据
            for file_info in self.file_list:
                if hasattr(file_info, 'status'):
                    if file_info.status in ['已完成', '已复制', '处理失败']:
                        # 清理estimated_size，只保留actual_size
                        file_info.estimated_size = 0
                else:
                    status = file_info.get('status', '')
                    if status in ['已完成', '已复制', '处理失败']:
                        file_info['estimated_size'] = 0
            
            # 清理压缩任务（已完成的任务）
            completed_indices = [idx for idx, task in self.compression_tasks.items() 
                                if task.status in ['已完成', '已复制', '失败', '已停止']]
            for idx in completed_indices:
                self.compression_tasks.pop(idx, None)
            
            self._memory_optimized = True
            self.logger.debug("内存清理完成")
        except Exception as e:
            self.logger.warning(f"内存清理时出错: {e}")


if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = FileCompressorApp(root)
        
        def on_closing():
            app.quit_application()
        
        root.protocol("WM_DELETE_WINDOW", on_closing)
        root.mainloop()
    except Exception as e:
        logging.error(f"应用异常退出: {e}")
        raise