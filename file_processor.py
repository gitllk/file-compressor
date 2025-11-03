"""
文件处理模块
负责文件路径验证、权限检查、大小估算等操作
"""
import os
import sys
import shutil
import subprocess
import math
import logging
from pathlib import Path
from PIL import Image


class FileProcessor:
    """文件处理器"""
    
    def __init__(self, logger=None):
        """
        初始化文件处理器
        
        Args:
            logger: 日志记录器
        """
        self.logger = logger or logging.getLogger('FileCompressor.FileProcessor')
    
    @staticmethod
    def normalize_path(path):
        """规范化路径，防止路径注入攻击"""
        if not path:
            return None
        
        try:
            normalized = Path(path).resolve()
            
            # 检查路径是否包含危险字符
            path_str = str(normalized)
            if '..' in path_str or path_str.startswith('\\\\'):
                return None
            
            return str(normalized)
        except (ValueError, OSError) as e:
            # 注意：这里不能使用self.logger，因为这是静态方法
            return None
    
    @staticmethod
    def check_path_permissions(path, need_read=True, need_write=False):
        """
        检查路径的读写权限
        
        Args:
            path: 路径
            need_read: 是否需要读取权限
            need_write: 是否需要写入权限
            
        Returns:
            (是否通过, 错误消息)
        """
        try:
            path_obj = Path(path)
            
            if not path_obj.exists():
                return True, None  # 不存在的路径，权限检查通过
            
            # 检查读取权限
            if need_read and not os.access(path, os.R_OK):
                return False, f"路径无读取权限: {path}"
            
            # 检查写入权限
            if need_write and not os.access(path, os.W_OK):
                return False, f"路径无写入权限: {path}"
            
            return True, None
        except Exception as e:
            return False, f"检查路径权限时出错: {str(e)}"
    
    @staticmethod
    def check_disk_space(target_path, required_size):
        """
        检查磁盘可用空间是否足够
        
        Args:
            target_path: 目标路径
            required_size: 所需空间大小（字节）
            
        Returns:
            (是否足够, 错误消息)
        """
        try:
            target_dir = os.path.dirname(target_path) if os.path.isfile(target_path) else target_path
            stat = shutil.disk_usage(target_dir)
            free_space = stat.free
            
            # 保留10%的磁盘空间作为安全余量
            available_space = free_space * 0.9
            
            if required_size > available_space:
                return False, f"磁盘空间不足。需要: {FileProcessor.format_size(required_size)}, 可用: {FileProcessor.format_size(available_space)}"
            
            return True, None
        except Exception as e:
            return True, None  # 检查失败时允许继续，避免阻塞操作
    
    @staticmethod
    def format_size(size_bytes):
        """格式化文件大小为人类可读的形式"""
        if size_bytes == 0:
            return "0 B"
        
        size_name = ["B", "KB", "MB", "GB", "TB"]
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        
        return f"{s} {size_name[i]}"
    
    def estimate_image_size(self, source_file, file_ext, original_size, config):
        """
        估算图片压缩后的大小
        
        Args:
            source_file: 源文件路径
            file_ext: 文件扩展名
            original_size: 原始大小
            config: 配置管理器实例
            
        Returns:
            估算的压缩后大小（字节）
        """
        try:
            with Image.open(source_file) as img:
                width, height = img.size
                max_width = config.get('max_photo_width', 2000)
                max_height = config.get('max_photo_height', 2000)
                
                # 计算是否需要调整尺寸
                needs_resize = (width > max_width or height > max_height)
                
                if needs_resize:
                    ratio = min(max_width / width, max_height / height)
                    pixel_ratio = ratio * ratio
                else:
                    pixel_ratio = 1.0
                
                photo_quality = config.get('photo_quality', 85)
                
                # 根据格式和质量估算压缩率
                if file_ext.lower() in ['.jpg', '.jpeg']:
                    if photo_quality >= 90:
                        base_ratio = 0.65
                    elif photo_quality >= 75:
                        base_ratio = 0.55
                    elif photo_quality >= 60:
                        base_ratio = 0.45
                    else:
                        base_ratio = 0.35
                    
                    quality_adjustment = (85 - photo_quality) * 0.003
                    compression_ratio = max(0.2, min(0.8, base_ratio + quality_adjustment))
                elif file_ext.lower() == '.png':
                    if photo_quality >= 85:
                        compression_ratio = 0.35
                    elif photo_quality >= 75:
                        compression_ratio = 0.30
                    else:
                        compression_ratio = 0.25
                else:
                    compression_ratio = 0.40
                
                estimated_size = original_size * pixel_ratio * compression_ratio
                return max(int(original_size * 0.1), min(int(estimated_size), int(original_size * 0.9)))
                
        except Exception as e:
            self.logger.debug(f"图片大小估算失败，使用保守估算: {source_file}, 错误: {e}")
            photo_quality = config.get('photo_quality', 85)
            if photo_quality >= 85:
                compression_ratio = 0.65
            elif photo_quality >= 75:
                compression_ratio = 0.55
            elif photo_quality >= 60:
                compression_ratio = 0.45
            else:
                compression_ratio = 0.35
            return int(original_size * compression_ratio)
    
    def estimate_video_size(self, source_file, file_ext, original_size, config, ffmpeg_path):
        """
        估算视频压缩后的大小
        
        Args:
            source_file: 源文件路径
            file_ext: 文件扩展名
            original_size: 原始大小
            config: 配置管理器实例
            ffmpeg_path: FFmpeg路径
            
        Returns:
            估算的压缩后大小（字节）
        """
        try:
            video_duration = None
            video_bitrate = None
            
            # 尝试使用ffprobe获取视频信息
            ffprobe_path = None
            if 'ffmpeg.exe' in ffmpeg_path:
                ffprobe_path = ffmpeg_path.replace('ffmpeg.exe', 'ffprobe.exe')
            elif 'ffmpeg' in ffmpeg_path:
                ffprobe_path = ffmpeg_path.replace('ffmpeg', 'ffprobe')
            
            if ffprobe_path and os.path.isfile(ffprobe_path):
                try:
                    cmd = [
                        ffprobe_path,
                        '-v', 'error',
                        '-show_entries', 'format=duration,bit_rate',
                        '-of', 'default=noprint_wrappers=1:nokey=1',
                        source_file
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
                        lines = result.stdout.decode('utf-8', errors='ignore').strip().split('\n')
                        if len(lines) >= 2:
                            video_duration = float(lines[0]) if lines[0] else None
                            video_bitrate = int(lines[1]) if lines[1] else None
                except Exception:
                    pass
            
            # 根据编码模式估算
            use_gpu = config.get('use_gpu', 'cpu')
            
            if use_gpu == 'amd':
                return self._estimate_amd_video_size(original_size, video_duration, video_bitrate, config)
            elif use_gpu == 'nvidia':
                return self._estimate_nvidia_video_size(original_size, video_duration, video_bitrate, config)
            else:
                return self._estimate_cpu_video_size(original_size, video_duration, video_bitrate, config)
                
        except Exception as e:
            self.logger.debug(f"视频大小估算失败，使用保守估算: {source_file}, 错误: {e}")
            return int(original_size * 0.65)
    
    def _estimate_amd_video_size(self, original_size, duration, bitrate, config):
        """估算AMD GPU编码后的视频大小"""
        try:
            amd_video_bitrate = config.get('amd_video_bitrate', '5000k').lower().strip()
            if amd_video_bitrate.endswith('k'):
                target_bitrate_bps = int(amd_video_bitrate[:-1]) * 1000 * 8
            elif amd_video_bitrate.endswith('m'):
                target_bitrate_bps = int(amd_video_bitrate[:-1]) * 1000000 * 8
            else:
                target_bitrate_bps = int(amd_video_bitrate) * 1000 * 8
            
            if duration and duration > 0:
                estimated_size = (target_bitrate_bps * duration) / 8
                audio_bitrate = 128 * 1000
                audio_size = (audio_bitrate * duration) / 8
                estimated_size += audio_size
                estimated_size *= 1.02
            elif bitrate and bitrate > 0:
                bitrate_ratio = target_bitrate_bps / bitrate
                estimated_size = original_size * bitrate_ratio
            else:
                estimated_size = original_size * 0.6
            
            return max(int(original_size * 0.2), min(int(estimated_size), int(original_size * 0.95)))
        except Exception:
            return int(original_size * 0.6)
    
    def _estimate_nvidia_video_size(self, original_size, duration, bitrate, config):
        """估算Nvidia GPU编码后的视频大小"""
        try:
            nvidia_video_bitrate = config.get('nvidia_video_bitrate', '5000k').lower().strip()
            if nvidia_video_bitrate.endswith('k'):
                target_bitrate_bps = int(nvidia_video_bitrate[:-1]) * 1000 * 8
            elif nvidia_video_bitrate.endswith('m'):
                target_bitrate_bps = int(nvidia_video_bitrate[:-1]) * 1000000 * 8
            else:
                target_bitrate_bps = int(nvidia_video_bitrate) * 1000 * 8
            
            if duration and duration > 0:
                estimated_size = (target_bitrate_bps * duration) / 8
                audio_bitrate = 128 * 1000
                audio_size = (audio_bitrate * duration) / 8
                estimated_size += audio_size
                estimated_size *= 1.02
            elif bitrate and bitrate > 0:
                bitrate_ratio = target_bitrate_bps / bitrate
                estimated_size = original_size * bitrate_ratio
            else:
                preset_map = {
                    'p1': 0.55, 'p2': 0.60, 'p3': 0.65,
                    'p4': 0.70, 'p5': 0.75, 'p6': 0.80, 'p7': 0.85
                }
                nvidia_preset = config.get('nvidia_preset', 'p4')
                compression_ratio = preset_map.get(nvidia_preset, 0.70)
                estimated_size = original_size * compression_ratio
            
            return max(int(original_size * 0.2), min(int(estimated_size), int(original_size * 0.95)))
        except Exception:
            return int(original_size * 0.65)
    
    def _estimate_cpu_video_size(self, original_size, duration, bitrate, config):
        """估算CPU编码后的视频大小"""
        try:
            crf_compression_map = {
                18: 0.85, 19: 0.80, 20: 0.75, 21: 0.70,
                22: 0.65, 23: 0.60, 24: 0.55, 25: 0.50,
                26: 0.45, 27: 0.40, 28: 0.35
            }
            
            video_crf = config.get('video_crf', 23)
            base_ratio = crf_compression_map.get(
                video_crf,
                0.60 + (28 - video_crf) * 0.025
            )
            
            preset_adjustments = {
                'ultrafast': -0.05, 'superfast': -0.03, 'veryfast': -0.02,
                'faster': -0.01, 'fast': 0, 'medium': 0,
                'slow': 0.02, 'slower': 0.03, 'veryslow': 0.05
            }
            video_preset = config.get('video_preset', 'medium')
            adjustment = preset_adjustments.get(video_preset, 0)
            compression_ratio = max(0.2, min(0.9, base_ratio + adjustment))
            
            estimated_size = original_size * compression_ratio
            return max(int(original_size * 0.2), min(int(estimated_size), int(original_size * 0.95)))
        except Exception:
            return int(original_size * 0.60)

