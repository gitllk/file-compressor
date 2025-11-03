"""
压缩历史记录模块
负责保存和管理压缩历史记录
"""
import os
import json
import datetime
import logging
from pathlib import Path


class CompressionHistory:
    """压缩历史记录管理器"""
    
    def __init__(self, history_file=None, logger=None):
        """
        初始化压缩历史记录管理器
        
        Args:
            history_file: 历史记录文件路径，如果为None则使用默认路径
            logger: 日志记录器
        """
        if history_file is None:
            history_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'v2', 'history')
            os.makedirs(history_dir, exist_ok=True)
            history_file = os.path.join(history_dir, 'compression_history.json')
        
        self.history_file = history_file
        self.logger = logger or logging.getLogger('FileCompressor.History')
        self.history = []
        self.load()
    
    def load(self):
        """加载历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.history = json.load(f)
                self.logger.info(f"加载历史记录: {len(self.history)} 条记录")
            except Exception as e:
                self.logger.error(f"加载历史记录失败: {e}")
                self.history = []
        else:
            self.history = []
    
    def save(self):
        """保存历史记录"""
        try:
            # 限制历史记录数量（最多保留100条）
            if len(self.history) > 100:
                self.history = self.history[-100:]
            
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
            self.logger.info("历史记录已保存")
        except Exception as e:
            self.logger.error(f"保存历史记录失败: {e}")
    
    def add_record(self, source_dir, target_dir, stats, config):
        """
        添加压缩记录
        
        Args:
            source_dir: 源文件夹
            target_dir: 目标文件夹
            stats: 统计信息字典
            config: 配置字典
        """
        record = {
            'timestamp': datetime.datetime.now().isoformat(),
            'source_dir': source_dir,
            'target_dir': target_dir,
            'stats': stats,
            'config': config
        }
        
        self.history.append(record)
        self.save()
        self.logger.info(f"添加压缩记录: {source_dir}")
    
    def get_all(self):
        """获取所有历史记录"""
        return self.history.copy()
    
    def get_recent(self, count=10):
        """获取最近的历史记录"""
        return self.history[-count:]
    
    def clear(self):
        """清空历史记录"""
        self.history = []
        self.save()
        self.logger.info("历史记录已清空")

