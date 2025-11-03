"""
图片压缩模块
负责图片文件的压缩处理
"""
import os
import shutil
import logging
from PIL import Image


class ImageCompressor:
    """图片压缩器"""
    
    def __init__(self, config_manager, logger=None):
        """
        初始化图片压缩器
        
        Args:
            config_manager: 配置管理器实例
            logger: 日志记录器
        """
        self.config = config_manager
        self.logger = logger or logging.getLogger('FileCompressor.ImageCompressor')
    
    def compress(self, source_path, target_path):
        """
        压缩图片文件
        
        Args:
            source_path: 源文件路径
            target_path: 目标文件路径
            
        Returns:
            True如果成功，False如果失败
        """
        try:
            # 验证路径
            normalized_source = self._normalize_path(source_path)
            if not normalized_source or not os.path.isfile(normalized_source):
                raise FileNotFoundError(f"源文件不存在: {source_path}")
            
            normalized_target = self._normalize_path(os.path.dirname(target_path))
            if not normalized_target:
                raise ValueError(f"无效的目标路径: {target_path}")
            
            # 确保目标目录存在
            target_dir = os.path.dirname(target_path)
            os.makedirs(target_dir, exist_ok=True)
            
            photo_quality = self.config.get('photo_quality', 85)
            max_width = self.config.get('max_photo_width', 2000)
            max_height = self.config.get('max_photo_height', 2000)
            
            with Image.open(normalized_source) as img:
                # 调整图片大小（保持原始比例）
                width, height = img.size
                if width > max_width or height > max_height:
                    ratio = min(max_width / width, max_height / height)
                    new_width = int(width * ratio)
                    new_height = int(height * ratio)
                    img = img.resize((new_width, new_height), Image.LANCZOS)
                
                # 保存压缩后的图片
                img.save(target_path, quality=photo_quality, optimize=True)
                
            return True
                
        except (FileNotFoundError, PermissionError, OSError) as e:
            self.logger.error(f"压缩图片出错: {source_path}, 错误类型: {type(e).__name__}, 错误: {str(e)}")
            # 如果压缩失败，直接复制原文件
            try:
                shutil.copy2(source_path, target_path)
                return False  # 返回False表示压缩失败但已复制
            except Exception as copy_error:
                self.logger.error(f"复制原始文件失败: {source_path}, 错误: {str(copy_error)}")
                raise
        except (ValueError, Image.UnidentifiedImageError, Image.DecompressionBombError) as e:
            self.logger.error(f"图片格式错误或损坏: {source_path}, 错误类型: {type(e).__name__}, 错误: {str(e)}")
            # 如果压缩失败，直接复制原文件
            try:
                shutil.copy2(source_path, target_path)
                return False
            except Exception as copy_error:
                self.logger.error(f"复制原始文件失败: {source_path}, 错误: {str(copy_error)}")
                raise
        except Exception as e:
            self.logger.error(f"压缩图片时发生未知错误: {source_path}, 错误类型: {type(e).__name__}, 错误: {str(e)}")
            try:
                shutil.copy2(source_path, target_path)
                return False
            except Exception as copy_error:
                self.logger.error(f"复制原始文件失败: {source_path}, 错误: {str(copy_error)}")
                raise
    
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

