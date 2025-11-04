"""
路径工具模块
统一管理应用程序路径，避免重复定义
"""
import os
import sys


# 获取当前文件所在目录（v2目录）
V2_DIR = os.path.dirname(os.path.abspath(__file__))

# 获取应用程序根目录（父目录的父目录）
# 处理PyInstaller打包后的路径
if getattr(sys, 'frozen', False):
    APP_PATH = sys._MEIPASS
else:
    APP_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 获取exe所在目录（打包后的exe运行时，这是exe所在的目录）
# 对于打包后的exe，这应该与exe同目录，而不是临时目录
if getattr(sys, 'frozen', False):
    # 打包后的exe，使用exe所在目录
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # 开发模式，使用v2目录
    EXE_DIR = V2_DIR


def get_v2_dir():
    """
    获取v2目录路径
    
    Returns:
        str: v2目录的绝对路径
    """
    return V2_DIR


def get_app_path():
    """
    获取应用程序根目录路径
    
    Returns:
        str: 应用程序根目录的绝对路径
    """
    return APP_PATH


def get_config_path():
    """
    获取配置文件路径
    
    打包后的exe运行时，配置文件应该在与exe同目录下。
    
    Returns:
        str: config.ini文件的绝对路径
    """
    if getattr(sys, 'frozen', False):
        # 打包后的exe，配置文件应该在与exe同目录下
        return os.path.join(EXE_DIR, 'config.ini')
    else:
        # 开发模式，使用v2目录下的config.ini
        return os.path.join(V2_DIR, 'config.ini')


def get_bin_dir():
    """
    获取bin目录路径
    
    打包后的exe运行时，bin目录应该在与exe同目录下，而不是系统临时目录。
    
    Returns:
        str: bin目录的绝对路径
    """
    if getattr(sys, 'frozen', False):
        # 打包后的exe，bin目录应该在与exe同目录下
        return os.path.join(EXE_DIR, 'bin')
    else:
        # 开发模式，使用v2目录下的bin
        return os.path.join(V2_DIR, 'bin')


def get_log_dir():
    """
    获取日志目录路径
    
    打包后的exe运行时，日志目录应该在与exe同目录下。
    
    Returns:
        str: logs目录的绝对路径
    """
    if getattr(sys, 'frozen', False):
        # 打包后的exe，日志目录应该在与exe同目录下
        return os.path.join(EXE_DIR, 'logs')
    else:
        # 开发模式，使用v2目录下的logs
        return os.path.join(V2_DIR, 'logs')


def get_history_dir():
    """
    获取历史记录目录路径
    
    打包后的exe运行时，历史记录目录应该在与exe同目录下。
    
    Returns:
        str: history目录的绝对路径
    """
    if getattr(sys, 'frozen', False):
        # 打包后的exe，历史记录目录应该在与exe同目录下
        return os.path.join(EXE_DIR, 'history')
    else:
        # 开发模式，使用v2目录下的history
        return os.path.join(V2_DIR, 'history')

