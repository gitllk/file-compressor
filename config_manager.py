"""
配置管理模块
负责配置文件的加载、保存和验证
"""
import os
import sys
import configparser
import re
import logging
from path_utils import get_v2_dir, get_app_path, get_config_path

# 获取路径（使用统一路径工具）
app_path = get_app_path()
v2_dir = get_v2_dir()


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config_path=None):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
        """
        self.config = configparser.ConfigParser()
        if config_path is None:
            # 使用统一路径工具获取配置文件路径
            self.config_path = get_config_path()
        else:
            self.config_path = config_path
        
        self.logger = logging.getLogger('FileCompressor.ConfigManager')
        
        # 默认配置
        self.defaults = {
            'ffmpeg_path': 'bin\\ffmpeg.exe',
            'photo_quality': 85,
            'video_crf': 23,
            'video_preset': 'medium',
            'max_photo_width': 2000,
            'max_photo_height': 2000,
            'resolution_preset': '自定义',  # 分辨率预设
            'output_folder': 'compressed',
            'use_gpu': 'cpu',
            # 视频编码配置
            'video_container': '.mp4',  # 容器格式：.mp4, .webm, .mkv, .mov, .avi
            'video_encoder': 'libx264',  # 视频编码器（会根据容器和GPU自动选择）
            'cpu_encoder': 'libx264',  # CPU编码器
            'amd_encoder': 'h264_amf',  # AMD GPU编码器
            'nvidia_encoder': 'h264_nvenc',  # Nvidia GPU编码器
            'video_bitrate': '5000k',  # 视频比特率（用于GPU编码器）
            'audio_encoder': 'aac',  # 音频编码器：aac, opus, mp3, vorbis
            # AMD GPU配置
            'amd_video_bitrate': '5000k',
            'amd_bframes': 3,
            'amd_refs': 3,
            # Nvidia GPU配置
            'nvidia_preset': 'p4',
            'nvidia_video_bitrate': '5000k',
            'nvidia_rc': 'cbr',
            'auto_exclude_non_media': True
        }
        
        # 分辨率预设字典
        self.resolution_presets = {
            '原始大小': (99999, 99999),  # 不限制，使用很大的值
            '1920x1080 (Full HD)': (1920, 1080),
            '1280x720 (HD)': (1280, 720),
            '3840x2160 (4K UHD)': (3840, 2160),
            '2560x1440 (2K/QHD)': (2560, 1440),
            '2048x1536 (iPad Retina)': (2048, 1536),
            '1920x1200 (WUXGA)': (1920, 1200),
            '1600x900': (1600, 900),
            '1366x768': (1366, 768),
            '1024x768': (1024, 768),
            '自定义': None  # 自定义需要用户输入
        }
        
        self.settings = {}
    
    def load(self):
        """加载配置文件"""
        if os.path.exists(self.config_path):
            try:
                self.config.read(self.config_path, encoding='utf-8')
                self.logger.info(f"配置文件加载成功: {self.config_path}")
            except Exception as e:
                self.logger.error(f"加载配置文件失败: {e}")
                self._create_default_config()
        else:
            self.logger.info("配置文件不存在，使用默认配置")
            self._create_default_config()
        
        # 加载所有配置项
        self._load_settings()
        
        # 验证配置
        errors = self.validate()
        if errors:
            self.logger.warning(f"配置验证发现问题: {errors}")
            self.save()  # 保存修复后的配置
    
    def _load_settings(self):
        """加载所有配置设置"""
        # 处理FFmpeg路径
        ffmpeg_path_from_config = self.config.get('General', 'ffmpeg_path', 
                                                   fallback=self.defaults['ffmpeg_path'])
        if getattr(sys, 'frozen', False):
            # 打包后的exe，FFmpeg路径应该在与exe同目录下
            if not os.path.isabs(ffmpeg_path_from_config):
                # 相对路径，使用exe所在目录
                from path_utils import get_bin_dir
                exe_bin_dir = get_bin_dir()
                # 如果路径是bin\ffmpeg.exe，使用exe同目录下的bin
                if ffmpeg_path_from_config.startswith('bin'):
                    self.settings['ffmpeg_path'] = os.path.join(exe_bin_dir, os.path.basename(ffmpeg_path_from_config))
                else:
                    self.settings['ffmpeg_path'] = os.path.join(exe_bin_dir, ffmpeg_path_from_config)
            else:
                self.settings['ffmpeg_path'] = ffmpeg_path_from_config
        else:
            # 开发模式下，如果是相对路径，使用v2_dir作为基础路径
            if not os.path.isabs(ffmpeg_path_from_config):
                self.settings['ffmpeg_path'] = os.path.join(v2_dir, ffmpeg_path_from_config)
            else:
                self.settings['ffmpeg_path'] = ffmpeg_path_from_config
        
        # 基本配置
        self.settings['photo_quality'] = self.config.getint('General', 'photo_quality', 
                                                           fallback=self.defaults['photo_quality'])
        self.settings['video_crf'] = self.config.getint('General', 'video_crf', 
                                                       fallback=self.defaults['video_crf'])
        self.settings['video_preset'] = self.config.get('General', 'video_preset', 
                                                       fallback=self.defaults['video_preset'])
        self.settings['max_photo_width'] = self.config.getint('General', 'max_photo_width', 
                                                             fallback=self.defaults['max_photo_width'])
        self.settings['max_photo_height'] = self.config.getint('General', 'max_photo_height', 
                                                              fallback=self.defaults['max_photo_height'])
        self.settings['resolution_preset'] = self.config.get('General', 'resolution_preset', 
                                                            fallback=self.defaults['resolution_preset'])
        self.settings['output_folder'] = self.config.get('General', 'output_folder', 
                                                        fallback=self.defaults['output_folder'])
        
        # GPU配置
        self.settings['use_gpu'] = self.config.get('General', 'use_gpu', 
                                                   fallback=self.defaults['use_gpu']).lower()
        
        # 视频编码配置
        self.settings['video_container'] = self.config.get('General', 'video_container', 
                                                           fallback=self.defaults['video_container'])
        self.settings['video_encoder'] = self.config.get('General', 'video_encoder', 
                                                         fallback=self.defaults['video_encoder'])
        self.settings['cpu_encoder'] = self.config.get('General', 'cpu_encoder', 
                                                       fallback=self.defaults['cpu_encoder'])
        self.settings['audio_encoder'] = self.config.get('General', 'audio_encoder', 
                                                         fallback=self.defaults['audio_encoder'])
        self.settings['video_bitrate'] = self.config.get('General', 'video_bitrate', 
                                                        fallback=self.defaults['video_bitrate'])
        
        # AMD GPU配置
        self.settings['amd_encoder'] = self.config.get('General', 'amd_encoder', 
                                                      fallback=self.defaults['amd_encoder'])
        self.settings['amd_video_bitrate'] = self.config.get('General', 'amd_video_bitrate', 
                                                           fallback=self.defaults['amd_video_bitrate'])
        self.settings['amd_bframes'] = self.config.getint('General', 'amd_bframes', 
                                                         fallback=self.defaults['amd_bframes'])
        self.settings['amd_refs'] = self.config.getint('General', 'amd_refs', 
                                                      fallback=self.defaults['amd_refs'])
        
        # Nvidia GPU配置
        self.settings['nvidia_encoder'] = self.config.get('General', 'nvidia_encoder', 
                                                         fallback=self.defaults['nvidia_encoder'])
        self.settings['nvidia_preset'] = self.config.get('General', 'nvidia_preset', 
                                                        fallback=self.defaults['nvidia_preset'])
        self.settings['nvidia_video_bitrate'] = self.config.get('General', 'nvidia_video_bitrate', 
                                                                fallback=self.defaults['nvidia_video_bitrate'])
        self.settings['nvidia_rc'] = self.config.get('General', 'nvidia_rc', 
                                                    fallback=self.defaults['nvidia_rc'])
        
        # 路径配置
        self.settings['source_dir'] = ''
        self.settings['target_dir'] = ''
        if self.config.has_section('Paths'):
            self.settings['source_dir'] = self.config.get('Paths', 'source_dir', fallback='')
            self.settings['target_dir'] = self.config.get('Paths', 'target_dir', fallback='')
    
    def save(self):
        """保存配置文件"""
        if not self.config.has_section('General'):
            self.config.add_section('General')
        
        # 保存基本配置
        self.config.set('General', 'ffmpeg_path', 
                       self.config.get('General', 'ffmpeg_path', fallback=self.defaults['ffmpeg_path']))
        self.config.set('General', 'photo_quality', str(self.settings.get('photo_quality', self.defaults['photo_quality'])))
        self.config.set('General', 'video_crf', str(self.settings.get('video_crf', self.defaults['video_crf'])))
        self.config.set('General', 'video_preset', self.settings.get('video_preset', self.defaults['video_preset']))
        self.config.set('General', 'max_photo_width', str(self.settings.get('max_photo_width', self.defaults['max_photo_width'])))
        self.config.set('General', 'max_photo_height', str(self.settings.get('max_photo_height', self.defaults['max_photo_height'])))
        self.config.set('General', 'resolution_preset', self.settings.get('resolution_preset', self.defaults['resolution_preset']))
        self.config.set('General', 'output_folder', self.settings.get('output_folder', self.defaults['output_folder']))
        
        # 保存GPU配置
        self.config.set('General', 'use_gpu', self.settings.get('use_gpu', self.defaults['use_gpu']))
        
        # 保存视频编码配置
        self.config.set('General', 'video_container', self.settings.get('video_container', self.defaults['video_container']))
        self.config.set('General', 'video_encoder', self.settings.get('video_encoder', self.defaults['video_encoder']))
        self.config.set('General', 'cpu_encoder', self.settings.get('cpu_encoder', self.defaults['cpu_encoder']))
        self.config.set('General', 'audio_encoder', self.settings.get('audio_encoder', self.defaults['audio_encoder']))
        self.config.set('General', 'video_bitrate', self.settings.get('video_bitrate', self.defaults['video_bitrate']))
        
        # 保存AMD GPU配置
        self.config.set('General', 'amd_encoder', self.settings.get('amd_encoder', self.defaults['amd_encoder']))
        self.config.set('General', 'amd_video_bitrate', self.settings.get('amd_video_bitrate', self.defaults['amd_video_bitrate']))
        self.config.set('General', 'amd_bframes', str(self.settings.get('amd_bframes', self.defaults['amd_bframes'])))
        self.config.set('General', 'amd_refs', str(self.settings.get('amd_refs', self.defaults['amd_refs'])))
        
        # 保存Nvidia GPU配置
        self.config.set('General', 'nvidia_encoder', self.settings.get('nvidia_encoder', self.defaults['nvidia_encoder']))
        self.config.set('General', 'nvidia_preset', self.settings.get('nvidia_preset', self.defaults['nvidia_preset']))
        self.config.set('General', 'nvidia_video_bitrate', self.settings.get('nvidia_video_bitrate', self.defaults['nvidia_video_bitrate']))
        self.config.set('General', 'nvidia_rc', self.settings.get('nvidia_rc', self.defaults['nvidia_rc']))
        
        # 保存路径
        if not self.config.has_section('Paths'):
            self.config.add_section('Paths')
        self.config.set('Paths', 'source_dir', self.settings.get('source_dir', ''))
        self.config.set('Paths', 'target_dir', self.settings.get('target_dir', ''))
        
        try:
            with open(self.config_path, 'w', encoding='utf-8') as configfile:
                self.config.write(configfile)
            self.logger.info("配置已保存")
        except Exception as e:
            self.logger.error(f"保存配置失败: {e}")
    
    def _create_default_config(self):
        """创建默认配置"""
        if not self.config.has_section('General'):
            self.config.add_section('General')
        
        for key, value in self.defaults.items():
            self.config.set('General', key, str(value))
        
        if not self.config.has_section('Paths'):
            self.config.add_section('Paths')
        
        self.save()
    
    def validate(self):
        """验证配置的有效性，返回错误列表"""
        errors = []
        
        # 验证照片质量
        photo_quality = self.settings.get('photo_quality', self.defaults['photo_quality'])
        if not (0 <= photo_quality <= 100):
            errors.append(f"照片质量 ({photo_quality}) 超出有效范围 (0-100)")
            self.settings['photo_quality'] = self.defaults['photo_quality']
        
        # 验证视频CRF
        video_crf = self.settings.get('video_crf', self.defaults['video_crf'])
        if not (18 <= video_crf <= 28):
            errors.append(f"视频CRF值 ({video_crf}) 超出有效范围 (18-28)")
            self.settings['video_crf'] = self.defaults['video_crf']
        
        # 验证视频预设
        video_preset = self.settings.get('video_preset', self.defaults['video_preset'])
        valid_presets = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
        if video_preset not in valid_presets:
            errors.append(f"视频预设 ({video_preset}) 无效")
            self.settings['video_preset'] = self.defaults['video_preset']
        
        # 验证照片尺寸
        max_photo_width = self.settings.get('max_photo_width', self.defaults['max_photo_width'])
        max_photo_height = self.settings.get('max_photo_height', self.defaults['max_photo_height'])
        if max_photo_width <= 0:
            errors.append(f"最大照片宽度 ({max_photo_width}) 必须为正整数")
            self.settings['max_photo_width'] = self.defaults['max_photo_width']
        if max_photo_height <= 0:
            errors.append(f"最大照片高度 ({max_photo_height}) 必须为正整数")
            self.settings['max_photo_height'] = self.defaults['max_photo_height']
        
        # 验证GPU模式
        use_gpu = self.settings.get('use_gpu', self.defaults['use_gpu'])
        if use_gpu not in ['cpu', 'amd', 'nvidia']:
            errors.append(f"GPU模式 ({use_gpu}) 无效")
            self.settings['use_gpu'] = self.defaults['use_gpu']
        
        # 验证视频容器格式
        video_container = self.settings.get('video_container', self.defaults['video_container'])
        valid_containers = ['.mp4', '.webm', '.mkv', '.mov', '.avi']
        if video_container not in valid_containers:
            errors.append(f"视频容器格式 ({video_container}) 无效")
            self.settings['video_container'] = self.defaults['video_container']
        
        # 验证音频编码器
        audio_encoder = self.settings.get('audio_encoder', self.defaults['audio_encoder'])
        valid_audio_encoders = ['aac', 'opus', 'mp3', 'vorbis']
        if audio_encoder not in valid_audio_encoders:
            errors.append(f"音频编码器 ({audio_encoder}) 无效")
            self.settings['audio_encoder'] = self.defaults['audio_encoder']
        
        # 验证AMD编码器
        amd_encoder = self.settings.get('amd_encoder', self.defaults['amd_encoder'])
        valid_amd_encoders = ['h264_amf', 'hevc_amf']
        if amd_encoder not in valid_amd_encoders:
            errors.append(f"AMD编码器 ({amd_encoder}) 无效")
            self.settings['amd_encoder'] = self.defaults['amd_encoder']
        
        # 验证AMD视频比特率
        amd_video_bitrate = self.settings.get('amd_video_bitrate', self.defaults['amd_video_bitrate'])
        if not re.match(r'^\d+[kmgKMG]?$', amd_video_bitrate):
            errors.append(f"AMD视频比特率 ({amd_video_bitrate}) 格式无效")
            self.settings['amd_video_bitrate'] = self.defaults['amd_video_bitrate']
        
        # 验证Nvidia编码器
        nvidia_encoder = self.settings.get('nvidia_encoder', self.defaults['nvidia_encoder'])
        valid_nvidia_encoders = ['h264_nvenc', 'hevc_nvenc']
        if nvidia_encoder not in valid_nvidia_encoders:
            errors.append(f"Nvidia编码器 ({nvidia_encoder}) 无效")
            self.settings['nvidia_encoder'] = self.defaults['nvidia_encoder']
        
        # 验证Nvidia预设
        nvidia_preset = self.settings.get('nvidia_preset', self.defaults['nvidia_preset'])
        valid_nvidia_presets = ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7']
        if nvidia_preset not in valid_nvidia_presets:
            errors.append(f"Nvidia预设 ({nvidia_preset}) 无效")
            self.settings['nvidia_preset'] = self.defaults['nvidia_preset']
        
        # 验证Nvidia视频比特率
        nvidia_video_bitrate = self.settings.get('nvidia_video_bitrate', self.defaults['nvidia_video_bitrate'])
        if not re.match(r'^\d+[kmgKMG]?$', nvidia_video_bitrate):
            errors.append(f"Nvidia视频比特率 ({nvidia_video_bitrate}) 格式无效")
            self.settings['nvidia_video_bitrate'] = self.defaults['nvidia_video_bitrate']
        
        # 验证Nvidia码率控制模式
        nvidia_rc = self.settings.get('nvidia_rc', self.defaults['nvidia_rc'])
        valid_nvidia_rc = ['cbr', 'vbr', 'constqp', 'vbr_minqp']
        if nvidia_rc not in valid_nvidia_rc:
            errors.append(f"Nvidia码率控制模式 ({nvidia_rc}) 无效")
            self.settings['nvidia_rc'] = self.defaults['nvidia_rc']
        
        # 验证FFmpeg路径
        ffmpeg_path = self.settings.get('ffmpeg_path', self.defaults['ffmpeg_path'])
        if not os.path.isfile(ffmpeg_path):
            errors.append(f"FFmpeg可执行文件不存在: {ffmpeg_path}")
            # 尝试查找默认路径（v2目录下的bin文件夹）
            default_ffmpeg = os.path.join(v2_dir, 'bin', 'ffmpeg.exe')
            if os.path.isfile(default_ffmpeg):
                self.settings['ffmpeg_path'] = default_ffmpeg
                errors[-1] += f"，已自动使用默认路径: {default_ffmpeg}"
        
        return errors
    
    def get(self, key, default=None):
        """获取配置项"""
        return self.settings.get(key, default if default is not None else self.defaults.get(key))
    
    def set(self, key, value):
        """设置配置项"""
        self.settings[key] = value
    
    def get_all(self):
        """获取所有配置"""
        return self.settings.copy()

