"""
视频压缩模块
负责视频文件的压缩处理，支持CPU、AMD和Nvidia GPU加速
支持多种编码格式：H.264, HEVC, VP8, VP9, AV1
支持多种容器格式：MP4, WebM, MKV, MOV, AVI
"""
import os
import sys
import shutil
import subprocess
import re
import logging
from encoder_compatibility import EncoderCompatibility


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
        self.encoder_compat = EncoderCompatibility(self.ffmpeg_path, logger)
    
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
            
            # 获取容器格式（如果没有指定，使用目标文件扩展名）
            container = self.config.get('video_container', file_ext)
            if not container.startswith('.'):
                container = '.' + container
            
            # 如果容器格式与目标文件扩展名不匹配，使用目标文件扩展名
            if container != file_ext:
                container = file_ext
            
            use_gpu = self.config.get('use_gpu', 'cpu')
            
            # 检查容器格式是否只支持CPU编码（如WebM）
            container_info = self.encoder_compat.CONTAINERS.get(container, {})
            if container_info.get('cpu_only', False):
                use_gpu = 'cpu'
                self.logger.info(f"容器格式 {container} 只支持CPU编码，强制使用CPU")
            
            # 获取编码器（根据容器和GPU自动选择）
            encoder = self._get_encoder_for_container(container, use_gpu)
            if not encoder:
                self.logger.error(f"无法为容器格式 {container} 找到合适的编码器")
                return False
            
            # 验证编码器与容器的兼容性
            is_compatible, error_msg = self.encoder_compat.validate_encoder_for_container(
                encoder, container, use_gpu
            )
            if not is_compatible:
                self.logger.error(f"编码器与容器格式不兼容: {error_msg}")
                return False
            
            if use_gpu == 'nvidia':
                return self._compress_with_nvidia(source_path, target_path, container, encoder)
            elif use_gpu == 'amd':
                return self._compress_with_amd(source_path, target_path, container, encoder)
            else:
                return self._compress_with_cpu(source_path, target_path, container, encoder)
                
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
    
    def _get_encoder_for_container(self, container_ext, use_gpu):
        """
        根据容器格式和GPU模式获取编码器
        
        Args:
            container_ext: 容器格式扩展名
            use_gpu: GPU模式
            
        Returns:
            编码器名称
        """
        # 首先尝试使用配置的编码器
        if use_gpu == 'cpu':
            encoder = self.config.get('cpu_encoder', 'libx264')
        elif use_gpu == 'nvidia':
            encoder = self.config.get('nvidia_encoder', 'h264_nvenc')
        elif use_gpu == 'amd':
            encoder = self.config.get('amd_encoder', 'h264_amf')
        else:
            encoder = self.config.get('video_encoder', 'libx264')
        
        # 验证编码器是否可用且与容器兼容
        is_compatible, _ = self.encoder_compat.validate_encoder_for_container(
            encoder, container_ext, use_gpu
        )
        if is_compatible:
            return encoder
        
        # 如果配置的编码器不可用，尝试获取默认编码器
        default_encoder = self.encoder_compat.get_default_encoder(container_ext, use_gpu)
        if default_encoder:
            return default_encoder
        
        # 如果还没有找到，尝试获取第一个兼容的编码器
        compatible = self.encoder_compat.get_compatible_encoders(container_ext, use_gpu)
        if compatible:
            return compatible[0]['name']
        
        return None
    
    def _compress_with_cpu(self, source_path, target_path, container_ext, encoder):
        """使用CPU编码压缩视频"""
        cmd = self._build_cpu_command(source_path, target_path, container_ext, encoder)
        
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
    
    def _compress_with_amd(self, source_path, target_path, container_ext, encoder):
        """使用AMD GPU编码压缩视频"""
        cmd = self._build_amd_gpu_command(source_path, target_path, container_ext, encoder)
        
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
            cpu_encoder = self._get_encoder_for_container(container_ext, 'cpu')
            if not cpu_encoder:
                self.logger.error(f"无法为容器格式 {container_ext} 找到CPU编码器")
                return False
            cmd = self._build_cpu_command(source_path, target_path, container_ext, cpu_encoder)
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
    
    def _compress_with_nvidia(self, source_path, target_path, container_ext, encoder):
        """使用Nvidia GPU编码压缩视频"""
        cmd = self._build_nvidia_gpu_command(source_path, target_path, container_ext, encoder)
        
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
            cpu_encoder = self._get_encoder_for_container(container_ext, 'cpu')
            if not cpu_encoder:
                self.logger.error(f"无法为容器格式 {container_ext} 找到CPU编码器")
                return False
            cmd = self._build_cpu_command(source_path, target_path, container_ext, cpu_encoder)
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
    
    def _build_cpu_command(self, source_path, target_path, container_ext, encoder):
        """构建CPU编码的FFmpeg命令"""
        video_crf = self.config.get('video_crf', 23)
        video_preset = self.config.get('video_preset', 'medium')
        audio_encoder = self.config.get('audio_encoder', 'aac')
        
        # 获取容器格式兼容的音频编码器
        compatible_audio = self.encoder_compat.get_compatible_audio_codecs(container_ext, encoder)
        if audio_encoder not in compatible_audio:
            audio_encoder = compatible_audio[0] if compatible_audio else 'aac'
        
        normalized_source = self._normalize_path(source_path)
        if not normalized_source:
            raise ValueError(f"无效的源文件路径: {source_path}")
        
        normalized_target = self._normalize_path(os.path.dirname(target_path))
        if not normalized_target:
            raise ValueError(f"无效的目标路径: {target_path}")
        
        cmd = [
            self.ffmpeg_path,
            '-i', normalized_source,
            '-c:v', encoder,
            '-c:a', audio_encoder,
            '-y'
        ]
        
        # 根据编码器类型设置质量参数
        encoder_info = self.encoder_compat.ENCODERS.get(encoder, {})
        if encoder_info.get('quality_mode') == 'crf':
            if encoder in ['libx264', 'libx265']:
                cmd.extend(['-crf', str(video_crf)])
                cmd.extend(['-preset', video_preset])
            elif encoder == 'libvpx':
                # VP8使用CRF模式
                cmd.extend(['-crf', str(video_crf)])
                cmd.extend(['-b:v', '0'])  # 使用CRF时不需要比特率
            elif encoder == 'libvpx-vp9':
                # VP9使用CRF模式
                cmd.extend(['-crf', str(video_crf)])
                cmd.extend(['-b:v', '0'])
            elif encoder in ['libaom-av1', 'libsvtav1']:
                # AV1使用CRF模式
                cmd.extend(['-crf', str(video_crf)])
        elif encoder_info.get('bitrate_mode'):
            # 使用比特率模式（某些编码器需要）
            video_bitrate = self.config.get('video_bitrate', '5000k')
            cmd.extend(['-b:v', video_bitrate])
        
        # 设置像素格式
        if encoder in ['libx264', 'libx265', 'h264_amf', 'hevc_amf', 'h264_nvenc', 'hevc_nvenc']:
            cmd.extend(['-pix_fmt', 'yuv420p'])
        
        # 设置音频比特率
        if audio_encoder == 'aac':
            cmd.extend(['-b:a', '128k'])
        elif audio_encoder == 'opus':
            cmd.extend(['-b:a', '128k'])
        
        # MP4格式优化
        if container_ext == '.mp4':
            cmd.extend(['-movflags', 'faststart'])
        
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        cmd.append(target_path)
        
        return cmd
    
    def _build_amd_gpu_command(self, source_path, target_path, container_ext, encoder):
        """构建AMD GPU加速编码的FFmpeg命令"""
        amd_video_bitrate = self.config.get('amd_video_bitrate', '5000k')
        audio_encoder = self.config.get('audio_encoder', 'aac')
        
        # 获取容器格式兼容的音频编码器
        compatible_audio = self.encoder_compat.get_compatible_audio_codecs(container_ext, encoder)
        if audio_encoder not in compatible_audio:
            audio_encoder = compatible_audio[0] if compatible_audio else 'aac'
        
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
            '-c:v', encoder,
            '-b:v', amd_video_bitrate,
            '-c:a', audio_encoder,
            '-pix_fmt', 'yuv420p',
            '-y'
        ]
        
        # AMF编码器特定参数
        if encoder == 'h264_amf':
            cmd.extend(['-usage', 'transcoding'])
        elif encoder == 'hevc_amf':
            cmd.extend(['-usage', 'transcoding', '-profile:v', 'main'])
        
        # 设置音频比特率
        if audio_encoder == 'aac':
            cmd.extend(['-b:a', '128k'])
        elif audio_encoder == 'opus':
            cmd.extend(['-b:a', '128k'])
        
        # MP4格式优化
        if container_ext == '.mp4':
            cmd.extend(['-movflags', 'faststart'])
        
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        cmd.append(target_path)
        
        return cmd
    
    def _build_nvidia_gpu_command(self, source_path, target_path, container_ext, encoder):
        """构建Nvidia GPU加速编码的FFmpeg命令"""
        nvidia_preset = self.config.get('nvidia_preset', 'p4')
        nvidia_video_bitrate = self.config.get('nvidia_video_bitrate', '5000k')
        nvidia_rc = self.config.get('nvidia_rc', 'cbr')
        audio_encoder = self.config.get('audio_encoder', 'aac')
        
        # 获取容器格式兼容的音频编码器
        compatible_audio = self.encoder_compat.get_compatible_audio_codecs(container_ext, encoder)
        if audio_encoder not in compatible_audio:
            audio_encoder = compatible_audio[0] if compatible_audio else 'aac'
        
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
            '-c:v', encoder,
            '-preset', nvidia_preset,
            '-rc', nvidia_rc,
            '-b:v', nvidia_video_bitrate,
            '-c:a', audio_encoder,
            '-y'
        ]
        
        # 设置音频比特率
        if audio_encoder == 'aac':
            cmd.extend(['-b:a', '128k'])
        elif audio_encoder == 'opus':
            cmd.extend(['-b:a', '128k'])
        
        # MP4格式优化
        if container_ext == '.mp4':
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

