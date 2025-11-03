"""
视频压缩模块
负责视频文件的压缩处理，支持CPU、AMD和Nvidia GPU加速
"""
import os
import sys
import shutil
import subprocess
import re
import logging


class VideoCompressor:
    """视频压缩器"""
    
    def __init__(self, config_manager, logger=None):
        """
        初始化视频压缩器
        
        Args:
            config_manager: 配置管理器实例
            logger: 日志记录器
        """
        self.config = config_manager
        self.logger = logger or logging.getLogger('FileCompressor.VideoCompressor')
        self.ffmpeg_path = config_manager.get('ffmpeg_path')
    
    def compress(self, source_path, target_path):
        """
        压缩视频文件
        
        Args:
            source_path: 源文件路径
            target_path: 目标文件路径
            
        Returns:
            True如果成功，False如果失败
        """
        try:
            file_ext = os.path.splitext(target_path)[1].lower()
            use_gpu = self.config.get('use_gpu', 'cpu')
            
            if use_gpu == 'nvidia':
                return self._compress_with_nvidia(source_path, target_path, file_ext)
            elif use_gpu == 'amd':
                return self._compress_with_amd(source_path, target_path, file_ext)
            else:
                return self._compress_with_cpu(source_path, target_path, file_ext)
                
        except Exception as e:
            self.logger.error(f"压缩视频时发生错误: {source_path}, 错误: {str(e)}")
            # 如果压缩失败，尝试复制原文件
            try:
                target_dir = os.path.dirname(target_path)
                os.makedirs(target_dir, exist_ok=True)
                shutil.copy2(source_path, target_path)
                return False
            except Exception as copy_error:
                self.logger.error(f"复制原始视频文件失败: {source_path}, 错误: {str(copy_error)}")
                raise
    
    def _compress_with_cpu(self, source_path, target_path, file_ext):
        """使用CPU编码压缩视频"""
        cmd = self._build_cpu_command(source_path, target_path, file_ext)
        
        self.logger.info(f"使用CPU压缩视频: {source_path}")
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        self.logger.info(f"CPU视频压缩成功: {source_path} -> {target_path}")
        return True
    
    def _compress_with_amd(self, source_path, target_path, file_ext):
        """使用AMD GPU编码压缩视频"""
        cmd = self._build_amd_gpu_command(source_path, target_path, file_ext)
        
        self.logger.info(f"使用AMD GPU加速压缩视频: {source_path}")
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            self.logger.info(f"AMD GPU视频压缩成功: {source_path} -> {target_path}")
            return True
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
            self.logger.warning(f"AMD GPU编码失败，回退到CPU编码: {source_path}, 错误: {error_msg}")
            
            # 回退到CPU编码
            cmd = self._build_cpu_command(source_path, target_path, file_ext)
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            self.logger.info(f"CPU视频压缩成功: {source_path} -> {target_path}")
            return True
    
    def _compress_with_nvidia(self, source_path, target_path, file_ext):
        """使用Nvidia GPU编码压缩视频"""
        cmd = self._build_nvidia_gpu_command(source_path, target_path, file_ext)
        
        self.logger.info(f"使用Nvidia GPU加速压缩视频: {source_path}")
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            self.logger.info(f"Nvidia GPU视频压缩成功: {source_path} -> {target_path}")
            return True
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
            self.logger.warning(f"Nvidia GPU编码失败，回退到CPU编码: {source_path}, 错误: {error_msg}")
            
            # 回退到CPU编码
            cmd = self._build_cpu_command(source_path, target_path, file_ext)
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            self.logger.info(f"CPU视频压缩成功: {source_path} -> {target_path}")
            return True
    
    def _build_cpu_command(self, source_path, target_path, file_ext):
        """构建CPU编码的FFmpeg命令"""
        video_crf = self.config.get('video_crf', 23)
        video_preset = self.config.get('video_preset', 'medium')
        
        normalized_source = self._normalize_path(source_path)
        if not normalized_source:
            raise ValueError(f"无效的源文件路径: {source_path}")
        
        normalized_target = self._normalize_path(os.path.dirname(target_path))
        if not normalized_target:
            raise ValueError(f"无效的目标路径: {target_path}")
        
        cmd = [
            self.ffmpeg_path,
            '-i', normalized_source,
            '-c:v', 'libx264',
            '-crf', str(video_crf),
            '-preset', video_preset,
            '-c:a', 'aac',
            '-b:a', '128k',
            '-pix_fmt', 'yuv420p',
            '-y'
        ]
        
        if file_ext == '.mp4':
            cmd.extend(['-movflags', 'faststart'])
        
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        cmd.append(target_path)
        
        return cmd
    
    def _build_amd_gpu_command(self, source_path, target_path, file_ext):
        """构建AMD GPU加速编码的FFmpeg命令"""
        amd_encoder = self.config.get('amd_encoder', 'h264_amf')
        amd_video_bitrate = self.config.get('amd_video_bitrate', '5000k')
        
        normalized_source = self._normalize_path(source_path)
        if not normalized_source:
            raise ValueError(f"无效的源文件路径: {source_path}")
        
        normalized_target = self._normalize_path(os.path.dirname(target_path))
        if not normalized_target:
            raise ValueError(f"无效的目标路径: {target_path}")
        
        cmd = [
            self.ffmpeg_path,
            '-hwaccel', 'd3d11va',
            '-i', normalized_source,
            '-c:v', amd_encoder,
            '-b:v', amd_video_bitrate,
            '-c:a', 'aac',
            '-b:a', '128k',
            '-pix_fmt', 'yuv420p',
            '-y'
        ]
        
        if amd_encoder == 'h264_amf':
            cmd.extend(['-usage', 'transcoding'])
        elif amd_encoder == 'hevc_amf':
            cmd.extend(['-usage', 'transcoding', '-profile:v', 'main'])
        
        if file_ext == '.mp4':
            cmd.extend(['-movflags', 'faststart'])
        
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        cmd.append(target_path)
        
        return cmd
    
    def _build_nvidia_gpu_command(self, source_path, target_path, file_ext):
        """构建Nvidia GPU加速编码的FFmpeg命令"""
        nvidia_encoder = self.config.get('nvidia_encoder', 'h264_nvenc')
        nvidia_preset = self.config.get('nvidia_preset', 'p4')
        nvidia_video_bitrate = self.config.get('nvidia_video_bitrate', '5000k')
        nvidia_rc = self.config.get('nvidia_rc', 'cbr')
        
        normalized_source = self._normalize_path(source_path)
        if not normalized_source:
            raise ValueError(f"无效的源文件路径: {source_path}")
        
        normalized_target = self._normalize_path(os.path.dirname(target_path))
        if not normalized_target:
            raise ValueError(f"无效的目标路径: {target_path}")
        
        cmd = [
            self.ffmpeg_path,
            '-hwaccel', 'cuda',
            '-hwaccel_output_format', 'cuda',
            '-i', normalized_source,
            '-c:v', nvidia_encoder,
            '-preset', nvidia_preset,
            '-rc', nvidia_rc,
            '-b:v', nvidia_video_bitrate,
            '-c:a', 'aac',
            '-b:a', '128k',
            '-y'
        ]
        
        if file_ext == '.mp4':
            cmd.extend(['-movflags', 'faststart'])
        
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        cmd.append(target_path)
        
        return cmd
    
    @staticmethod
    def _normalize_path(path):
        """规范化路径"""
        if not path:
            return None
        try:
            from pathlib import Path
            normalized = Path(path).resolve()
            path_str = str(normalized)
            if '..' in path_str or path_str.startswith('\\\\'):
                return None
            return str(normalized)
        except (ValueError, OSError):
            return None

