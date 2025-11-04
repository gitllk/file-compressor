"""
编码器兼容性检查模块
负责检查编码器、容器格式和硬件加速的兼容性
"""
import os
import sys
import subprocess
import logging
import re


class EncoderCompatibility:
    """编码器兼容性检查器"""
    
    # 编码器定义
    ENCODERS = {
        # CPU编码器
        'libx264': {
            'name': 'H.264 (x264)',
            'type': 'cpu',
            'containers': ['.mp4', '.mkv', '.mov', '.avi'],
            'audio_codecs': ['aac', 'mp3', 'opus'],
            'quality_mode': 'crf',  # 使用CRF模式
            'bitrate_mode': False
        },
        'libx265': {
            'name': 'HEVC/H.265 (x265)',
            'type': 'cpu',
            'containers': ['.mp4', '.mkv', '.mov'],
            'audio_codecs': ['aac', 'mp3', 'opus'],
            'quality_mode': 'crf',
            'bitrate_mode': False
        },
        'libvpx': {
            'name': 'VP8',
            'type': 'cpu',
            'containers': ['.webm', '.mkv'],
            'audio_codecs': ['opus', 'vorbis'],
            'quality_mode': 'crf',
            'bitrate_mode': True
        },
        'libvpx-vp9': {
            'name': 'VP9',
            'type': 'cpu',
            'containers': ['.webm', '.mkv'],
            'audio_codecs': ['opus', 'vorbis'],
            'quality_mode': 'crf',
            'bitrate_mode': True
        },
        'libaom-av1': {
            'name': 'AV1 (libaom)',
            'type': 'cpu',
            'containers': ['.webm', '.mkv', '.mp4'],
            'audio_codecs': ['opus', 'aac'],
            'quality_mode': 'crf',
            'bitrate_mode': False
        },
        'libsvtav1': {
            'name': 'AV1 (SVT-AV1)',
            'type': 'cpu',
            'containers': ['.webm', '.mkv', '.mp4'],
            'audio_codecs': ['opus', 'aac'],
            'quality_mode': 'crf',
            'bitrate_mode': False
        },
        
        # Nvidia GPU编码器
        'h264_nvenc': {
            'name': 'H.264 (NVENC)',
            'type': 'nvidia',
            'containers': ['.mp4', '.mkv', '.mov'],
            'audio_codecs': ['aac', 'mp3'],
            'quality_mode': 'bitrate',  # GPU编码器通常使用比特率模式
            'bitrate_mode': True,
            'presets': ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7'],
            'rc_modes': ['cbr', 'vbr', 'constqp', 'vbr_minqp']
        },
        'hevc_nvenc': {
            'name': 'HEVC/H.265 (NVENC)',
            'type': 'nvidia',
            'containers': ['.mp4', '.mkv', '.mov'],
            'audio_codecs': ['aac', 'mp3'],
            'quality_mode': 'bitrate',
            'bitrate_mode': True,
            'presets': ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7'],
            'rc_modes': ['cbr', 'vbr', 'constqp', 'vbr_minqp']
        },
        
        # AMD GPU编码器
        'h264_amf': {
            'name': 'H.264 (AMF)',
            'type': 'amd',
            'containers': ['.mp4', '.mkv', '.mov'],
            'audio_codecs': ['aac', 'mp3'],
            'quality_mode': 'bitrate',
            'bitrate_mode': True
        },
        'hevc_amf': {
            'name': 'HEVC/H.265 (AMF)',
            'type': 'amd',
            'containers': ['.mp4', '.mkv', '.mov'],
            'audio_codecs': ['aac', 'mp3'],
            'quality_mode': 'bitrate',
            'bitrate_mode': True
        }
    }
    
    # 容器格式定义
    CONTAINERS = {
        '.mp4': {
            'name': 'MP4',
            'video_codecs': ['libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'h264_amf', 'hevc_amf', 'libaom-av1', 'libsvtav1'],
            'audio_codecs': ['aac', 'mp3', 'opus'],
            'default_video_codec': 'libx264',
            'default_audio_codec': 'aac'
        },
        '.webm': {
            'name': 'WebM',
            'video_codecs': ['libvpx', 'libvpx-vp9', 'libaom-av1', 'libsvtav1'],
            'audio_codecs': ['opus', 'vorbis'],
            'default_video_codec': 'libvpx-vp9',
            'default_audio_codec': 'opus',
            'cpu_only': True  # WebM只能使用CPU编码
        },
        '.mkv': {
            'name': 'Matroska (MKV)',
            'video_codecs': ['libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'h264_amf', 'hevc_amf', 'libvpx', 'libvpx-vp9', 'libaom-av1', 'libsvtav1'],
            'audio_codecs': ['aac', 'mp3', 'opus', 'vorbis'],
            'default_video_codec': 'libx264',
            'default_audio_codec': 'aac'
        },
        '.mov': {
            'name': 'QuickTime (MOV)',
            'video_codecs': ['libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'h264_amf', 'hevc_amf'],
            'audio_codecs': ['aac', 'mp3'],
            'default_video_codec': 'libx264',
            'default_audio_codec': 'aac'
        },
        '.avi': {
            'name': 'AVI',
            'video_codecs': ['libx264'],
            'audio_codecs': ['aac', 'mp3'],
            'default_video_codec': 'libx264',
            'default_audio_codec': 'aac'
        }
    }
    
    def __init__(self, ffmpeg_path, logger=None):
        """
        初始化编码器兼容性检查器
        
        Args:
            ffmpeg_path: FFmpeg可执行文件路径
            logger: 日志记录器
        """
        self.ffmpeg_path = ffmpeg_path
        self.logger = logger or logging.getLogger('FileCompressor.EncoderCompatibility')
        self._available_encoders = None  # 缓存可用编码器列表
        self._encoder_support = {}  # 缓存编码器支持情况
    
    def get_available_encoders(self, force_refresh=False):
        """
        获取FFmpeg支持的编码器列表
        
        Args:
            force_refresh: 是否强制刷新缓存
            
        Returns:
            支持的编码器列表（编码器名称列表）
        """
        if self._available_encoders is not None and not force_refresh:
            return self._available_encoders
        
        try:
            cmd = [
                self.ffmpeg_path,
                '-hide_banner',
                '-encoders'
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            
            if result.returncode == 0:
                output = result.stdout.decode('utf-8', errors='ignore')
                available = []
                
                # 检查每个编码器是否可用
                for encoder_name in self.ENCODERS.keys():
                    if encoder_name in output:
                        available.append(encoder_name)
                
                self._available_encoders = available
                self.logger.info(f"检测到可用编码器: {available}")
                return available
            else:
                self.logger.warning("无法获取编码器列表")
                return []
        except Exception as e:
            self.logger.error(f"检查编码器时出错: {e}")
            return []
    
    def is_encoder_available(self, encoder_name):
        """
        检查编码器是否可用
        
        Args:
            encoder_name: 编码器名称
            
        Returns:
            True如果可用，False如果不可用
        """
        if encoder_name not in self.ENCODERS:
            return False
        
        if encoder_name in self._encoder_support:
            return self._encoder_support[encoder_name]
        
        available = self.get_available_encoders()
        is_available = encoder_name in available
        self._encoder_support[encoder_name] = is_available
        return is_available
    
    def get_compatible_encoders(self, container_ext, use_gpu='cpu'):
        """
        获取与容器格式兼容的编码器列表
        
        Args:
            container_ext: 容器格式扩展名（如'.mp4', '.webm'）
            use_gpu: GPU模式（'cpu', 'nvidia', 'amd'）
            
        Returns:
            兼容的编码器列表（编码器信息字典列表）
        """
        container_ext = container_ext.lower()
        if container_ext not in self.CONTAINERS:
            return []
        
        container_info = self.CONTAINERS[container_ext]
        
        # 如果容器格式只能使用CPU（如WebM），强制使用CPU
        if container_info.get('cpu_only', False):
            use_gpu = 'cpu'
        
        compatible_encoders = []
        available_encoders = self.get_available_encoders()
        
        for encoder_name in container_info['video_codecs']:
            if encoder_name not in self.ENCODERS:
                continue
            
            encoder_info = self.ENCODERS[encoder_name]
            
            # 检查GPU类型匹配
            if use_gpu == 'cpu' and encoder_info['type'] != 'cpu':
                continue
            if use_gpu == 'nvidia' and encoder_info['type'] != 'nvidia':
                continue
            if use_gpu == 'amd' and encoder_info['type'] != 'amd':
                continue
            
            # 检查编码器是否可用
            if encoder_name in available_encoders:
                compatible_encoders.append({
                    'name': encoder_name,
                    'display_name': encoder_info['name'],
                    'type': encoder_info['type'],
                    'quality_mode': encoder_info['quality_mode'],
                    'bitrate_mode': encoder_info.get('bitrate_mode', False),
                    'presets': encoder_info.get('presets', []),
                    'rc_modes': encoder_info.get('rc_modes', [])
                })
        
        return compatible_encoders
    
    def get_compatible_audio_codecs(self, container_ext, video_encoder=None):
        """
        获取与容器格式兼容的音频编码器列表
        
        Args:
            container_ext: 容器格式扩展名
            video_encoder: 视频编码器名称（可选，用于限制兼容性）
            
        Returns:
            兼容的音频编码器列表
        """
        container_ext = container_ext.lower()
        if container_ext not in self.CONTAINERS:
            return []
        
        container_info = self.CONTAINERS[container_ext]
        
        if video_encoder and video_encoder in self.ENCODERS:
            encoder_info = self.ENCODERS[video_encoder]
            # 取容器和编码器都支持的音频编码器
            compatible = list(set(container_info['audio_codecs']) & set(encoder_info['audio_codecs']))
            return compatible
        
        return container_info['audio_codecs']
    
    def get_default_encoder(self, container_ext, use_gpu='cpu'):
        """
        获取默认编码器
        
        Args:
            container_ext: 容器格式扩展名
            use_gpu: GPU模式
            
        Returns:
            默认编码器名称
        """
        container_ext = container_ext.lower()
        if container_ext not in self.CONTAINERS:
            return None
        
        container_info = self.CONTAINERS[container_ext]
        default_encoder = container_info.get('default_video_codec')
        
        if default_encoder:
            # 检查默认编码器是否可用
            if self.is_encoder_available(default_encoder):
                encoder_info = self.ENCODERS[default_encoder]
                # 检查GPU类型匹配
                if use_gpu == 'cpu' and encoder_info['type'] == 'cpu':
                    return default_encoder
                if use_gpu == 'nvidia' and encoder_info['type'] == 'nvidia':
                    return default_encoder
                if use_gpu == 'amd' and encoder_info['type'] == 'amd':
                    return default_encoder
        
        # 如果默认编码器不可用，尝试获取第一个兼容的编码器
        compatible = self.get_compatible_encoders(container_ext, use_gpu)
        if compatible:
            return compatible[0]['name']
        
        return None
    
    def get_default_audio_codec(self, container_ext):
        """
        获取默认音频编码器
        
        Args:
            container_ext: 容器格式扩展名
            
        Returns:
            默认音频编码器名称
        """
        container_ext = container_ext.lower()
        if container_ext not in self.CONTAINERS:
            return 'aac'
        
        container_info = self.CONTAINERS[container_ext]
        return container_info.get('default_audio_codec', 'aac')
    
    def validate_encoder_for_container(self, encoder_name, container_ext, use_gpu='cpu'):
        """
        验证编码器是否与容器格式兼容
        
        Args:
            encoder_name: 编码器名称
            container_ext: 容器格式扩展名
            use_gpu: GPU模式
            
        Returns:
            (是否兼容, 错误信息)
        """
        if encoder_name not in self.ENCODERS:
            return False, f"未知编码器: {encoder_name}"
        
        if not self.is_encoder_available(encoder_name):
            return False, f"编码器不可用: {encoder_name}"
        
        container_ext = container_ext.lower()
        if container_ext not in self.CONTAINERS:
            return False, f"不支持的容器格式: {container_ext}"
        
        container_info = self.CONTAINERS[container_ext]
        
        # 检查容器格式是否只支持CPU编码
        if container_info.get('cpu_only', False) and self.ENCODERS[encoder_name]['type'] != 'cpu':
            return False, f"容器格式 {container_ext} 只能使用CPU编码"
        
        # 检查编码器是否与容器格式兼容
        if encoder_name not in container_info['video_codecs']:
            return False, f"编码器 {encoder_name} 与容器格式 {container_ext} 不兼容"
        
        encoder_info = self.ENCODERS[encoder_name]
        
        # 检查GPU类型匹配
        if use_gpu == 'cpu' and encoder_info['type'] != 'cpu':
            return False, f"编码器 {encoder_name} 需要GPU加速，但当前模式为CPU"
        if use_gpu == 'nvidia' and encoder_info['type'] != 'nvidia':
            return False, f"编码器 {encoder_name} 与Nvidia GPU不兼容"
        if use_gpu == 'amd' and encoder_info['type'] != 'amd':
            return False, f"编码器 {encoder_name} 与AMD GPU不兼容"
        
        return True, None

