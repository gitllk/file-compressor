"""
FFmpeg管理器
负责检测、下载和管理FFmpeg可执行文件
"""
import os
import sys
import shutil
import subprocess
import logging
import zipfile
import urllib.request
import urllib.error
from pathlib import Path

# 导入统一路径工具
from path_utils import get_v2_dir, get_app_path, get_bin_dir

# 获取路径（使用统一路径工具）
app_path = get_app_path()
v2_dir = get_v2_dir()


class FFmpegManager:
    """FFmpeg管理器，负责检测和下载FFmpeg"""
    
    # FFmpeg下载URL（Windows静态构建版本）
    # 使用GitHub上官方推荐的构建：https://www.gyan.dev/ffmpeg/builds/
    FFMPEG_DOWNLOAD_URLS = {
        'windows': 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
        'windows_alt': 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
    }
    
    def __init__(self, logger=None):
        """
        初始化FFmpeg管理器
        
        Args:
            logger: 日志记录器
        """
        self.logger = logger or logging.getLogger('FileCompressor.FFmpegManager')
        # 使用当前文件所在目录（v2目录）下的bin文件夹
        self.bin_dir = get_bin_dir()
        self.default_ffmpeg_path = os.path.join(self.bin_dir, 'ffmpeg.exe')
        self.default_ffprobe_path = os.path.join(self.bin_dir, 'ffprobe.exe')
    
    def check_ffmpeg(self, config_path=None):
        """
        检测FFmpeg是否可用
        
        Args:
            config_path: 配置的FFmpeg路径（可选）
            
        Returns:
            tuple: (是否可用, ffmpeg路径)
        """
        # 1. 检查配置的路径
        if config_path and os.path.isfile(config_path):
            if self._test_ffmpeg(config_path):
                self.logger.info(f"FFmpeg在配置路径可用: {config_path}")
                return True, config_path
        
        # 2. 检查默认bin目录
        if os.path.isfile(self.default_ffmpeg_path):
            if self._test_ffmpeg(self.default_ffmpeg_path):
                self.logger.info(f"FFmpeg在默认路径可用: {self.default_ffmpeg_path}")
                return True, self.default_ffmpeg_path
        
        # 3. 检查系统PATH环境变量
        ffmpeg_in_path = shutil.which('ffmpeg')
        if ffmpeg_in_path:
            if self._test_ffmpeg(ffmpeg_in_path):
                self.logger.info(f"FFmpeg在系统PATH中可用: {ffmpeg_in_path}")
                return True, ffmpeg_in_path
        
        # 检查Windows路径（可能需要完整路径）
        if sys.platform == 'win32':
            common_paths = [
                'C:\\ffmpeg\\bin\\ffmpeg.exe',
                'C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe',
                'C:\\Program Files (x86)\\ffmpeg\\bin\\ffmpeg.exe',
            ]
            for path in common_paths:
                if os.path.isfile(path) and self._test_ffmpeg(path):
                    self.logger.info(f"FFmpeg在常见路径中可用: {path}")
                    return True, path
        
        self.logger.warning("FFmpeg未找到")
        return False, None
    
    def _test_ffmpeg(self, ffmpeg_path):
        """
        测试FFmpeg是否可执行
        
        Args:
            ffmpeg_path: FFmpeg可执行文件路径
            
        Returns:
            bool: 是否可用
        """
        try:
            result = subprocess.run(
                [ffmpeg_path, '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            return result.returncode == 0
        except Exception as e:
            self.logger.debug(f"测试FFmpeg失败: {e}")
            return False
    
    def download_ffmpeg(self, progress_callback=None):
        """
        下载FFmpeg到bin目录
        
        Args:
            progress_callback: 进度回调函数，接收(current, total)参数
            
        Returns:
            tuple: (是否成功, 错误信息)
        """
        if sys.platform != 'win32':
            return False, "自动下载仅支持Windows系统，请手动安装FFmpeg"
        
        try:
            # 确保bin目录存在
            os.makedirs(self.bin_dir, exist_ok=True)
            
            # 选择下载URL
            download_url = self.FFMPEG_DOWNLOAD_URLS.get('windows')
            if not download_url:
                return False, "未找到FFmpeg下载地址"
            
            # 临时文件路径
            temp_zip = os.path.join(self.bin_dir, 'ffmpeg_temp.zip')
            
            # 下载文件
            self.logger.info(f"开始下载FFmpeg: {download_url}")
            
            def show_progress(block_num, block_size, total_size):
                """下载进度显示"""
                if total_size > 0:
                    downloaded = block_num * block_size
                    percent = min(downloaded * 100 / total_size, 100)
                    if progress_callback:
                        progress_callback(downloaded, total_size)
                    self.logger.debug(f"下载进度: {percent:.1f}%")
            
            try:
                urllib.request.urlretrieve(download_url, temp_zip, show_progress)
            except urllib.error.URLError as e:
                # 尝试备用URL
                self.logger.warning(f"主下载URL失败，尝试备用URL: {e}")
                download_url = self.FFMPEG_DOWNLOAD_URLS.get('windows_alt')
                if download_url:
                    try:
                        urllib.request.urlretrieve(download_url, temp_zip, show_progress)
                    except urllib.error.URLError as e2:
                        return False, f"下载失败: {str(e2)}\n请检查网络连接或手动下载FFmpeg"
                else:
                    return False, f"下载失败: {str(e)}\n请检查网络连接或手动下载FFmpeg"
            
            if not os.path.isfile(temp_zip):
                return False, "下载文件未找到"
            
            self.logger.info("开始解压FFmpeg")
            
            # 解压zip文件
            with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
                # 查找ffmpeg.exe和ffprobe.exe
                ffmpeg_found = None
                ffprobe_found = None
                
                for file_info in zip_ref.filelist:
                    filename = file_info.filename.replace('\\', os.sep)
                    if filename.endswith('ffmpeg.exe'):
                        ffmpeg_found = file_info
                    elif filename.endswith('ffprobe.exe'):
                        ffprobe_found = file_info
                
                if not ffmpeg_found:
                    return False, "在下载的ZIP文件中未找到ffmpeg.exe"
                
                # 解压ffmpeg.exe
                zip_ref.extract(ffmpeg_found, self.bin_dir)
                extracted_ffmpeg = os.path.join(self.bin_dir, ffmpeg_found.filename.replace('\\', os.sep))
                
                # 移动到目标位置
                if extracted_ffmpeg != self.default_ffmpeg_path:
                    if os.path.exists(self.default_ffmpeg_path):
                        os.remove(self.default_ffmpeg_path)
                    shutil.move(extracted_ffmpeg, self.default_ffmpeg_path)
                
                # 解压ffprobe.exe（如果存在）
                if ffprobe_found:
                    zip_ref.extract(ffprobe_found, self.bin_dir)
                    extracted_ffprobe = os.path.join(self.bin_dir, ffprobe_found.filename.replace('\\', os.sep))
                    if extracted_ffprobe != self.default_ffprobe_path:
                        if os.path.exists(self.default_ffprobe_path):
                            os.remove(self.default_ffprobe_path)
                        shutil.move(extracted_ffprobe, self.default_ffprobe_path)
                
                # 清理解压后的目录结构
                self._cleanup_extracted_files()
            
            # 删除临时zip文件
            try:
                os.remove(temp_zip)
            except:
                pass
            
            # 验证下载的文件
            if not os.path.isfile(self.default_ffmpeg_path):
                return False, "下载的文件验证失败"
            
            if not self._test_ffmpeg(self.default_ffmpeg_path):
                return False, "下载的FFmpeg无法正常运行"
            
            self.logger.info(f"FFmpeg下载成功: {self.default_ffmpeg_path}")
            return True, None
            
        except Exception as e:
            self.logger.error(f"下载FFmpeg时出错: {e}")
            return False, f"下载失败: {str(e)}"
    
    def _cleanup_extracted_files(self):
        """清理解压后的多余目录结构"""
        try:
            # 查找bin目录下的所有目录
            for item in os.listdir(self.bin_dir):
                item_path = os.path.join(self.bin_dir, item)
                if os.path.isdir(item_path):
                    # 检查是否包含ffmpeg.exe
                    ffmpeg_in_dir = os.path.join(item_path, 'ffmpeg.exe')
                    ffprobe_in_dir = os.path.join(item_path, 'ffprobe.exe')
                    
                    if os.path.isfile(ffmpeg_in_dir):
                        # 移动文件到bin目录
                        if not os.path.exists(self.default_ffmpeg_path):
                            shutil.move(ffmpeg_in_dir, self.default_ffmpeg_path)
                        else:
                            os.remove(ffmpeg_in_dir)
                    
                    if os.path.isfile(ffprobe_in_dir):
                        if not os.path.exists(self.default_ffprobe_path):
                            shutil.move(ffprobe_in_dir, self.default_ffprobe_path)
                        else:
                            os.remove(ffprobe_in_dir)
                    
                    # 删除空目录
                    try:
                        shutil.rmtree(item_path)
                    except:
                        pass
        except Exception as e:
            self.logger.warning(f"清理解压文件时出错: {e}")

