"""
文件信息数据结构
使用紧凑的数据结构存储文件信息，减少内存占用
"""
import os
from typing import Optional


class FileInfo:
    """文件信息类 - 使用__slots__减少内存占用"""
    __slots__ = (
        'source_dir',      # 源目录（相对路径的父目录）
        'target_dir',      # 目标目录（相对路径的父目录）
        'rel_path',        # 相对路径
        'file_name',       # 文件名
        'file_ext',        # 文件扩展名
        'file_size',       # 文件大小
        'estimated_size',  # 估算压缩后大小
        'actual_size',     # 实际压缩后大小
        'status'           # 状态
    )
    
    def __init__(self, source_dir, target_dir, rel_path, file_name, file_ext, 
                 file_size, estimated_size=0, actual_size=0, status='等待压缩'):
        self.source_dir = source_dir
        self.target_dir = target_dir
        self.rel_path = rel_path
        self.file_name = file_name
        self.file_ext = file_ext
        self.file_size = file_size
        self.estimated_size = estimated_size
        self.actual_size = actual_size
        self.status = status
    
    @property
    def source_path(self):
        """获取完整源路径（延迟计算）"""
        if self.rel_path == '.':
            return os.path.join(self.source_dir, self.file_name)
        return os.path.join(self.source_dir, self.rel_path, self.file_name)
    
    @property
    def target_path(self):
        """获取完整目标路径（延迟计算）"""
        if self.rel_path == '.':
            return os.path.join(self.target_dir, self.file_name)
        return os.path.join(self.target_dir, self.rel_path, self.file_name)
    
    def to_dict(self):
        """转换为字典（兼容旧代码）"""
        return {
            'source_path': self.source_path,
            'target_path': self.target_path,
            'rel_path': self.rel_path,
            'file_name': self.file_name,
            'file_ext': self.file_ext,
            'file_size': self.file_size,
            'estimated_size': self.estimated_size,
            'actual_size': self.actual_size,
            'status': self.status
        }
    
    def get(self, key, default=None):
        """兼容字典接口"""
        if key == 'source_path':
            return self.source_path
        elif key == 'target_path':
            return self.target_path
        elif hasattr(self, key):
            return getattr(self, key)
        return default

