"""
Web服务器模块
提供HTTP API和Web界面，支持单文件和批量文件压缩
"""
import os
import sys
import threading
import logging
import tempfile
import shutil
import json
import time
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory
try:
    from flask_cors import CORS
except ImportError:
    # 如果没有flask_cors，创建一个空的CORS类
    class CORS:
        def __init__(self, app):
            pass
from werkzeug.utils import secure_filename
from werkzeug.serving import make_server

# 导入自定义模块
from config_manager import ConfigManager
from file_processor import FileProcessor
from image_compressor import ImageCompressor
from video_compressor import VideoCompressor

# 导入统一路径工具
from path_utils import get_v2_dir

# 获取当前文件所在目录（v2目录）
v2_dir = get_v2_dir()

# 支持的文件扩展名
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp'}
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.m4v', '.webm', '.3gp'}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS


class WebServer:
    """Web服务器类"""
    
    # 预设配置
    PRESET_CONFIGS = {
        'high_quality': {
            'name': '高质量',
            'photo_quality': 95,
            'video_crf': 18,
            'max_photo_width': 99999,
            'max_photo_height': 99999,
            'resolution_preset': '原始大小',
            'video_preset': 'slow'
            # 注意：use_gpu不在预设中，由服务器自动检测
        },
        'balanced': {
            'name': '平衡',
            'photo_quality': 85,
            'video_crf': 23,
            'max_photo_width': 2000,
            'max_photo_height': 2000,
            'resolution_preset': '1920x1080 (Full HD)',
            'video_preset': 'medium'
        },
        'small_size': {
            'name': '小体积',
            'photo_quality': 75,
            'video_crf': 28,
            'max_photo_width': 1280,
            'max_photo_height': 1280,
            'resolution_preset': '1280x720 (HD)',
            'video_preset': 'fast'
        },
        'fast': {
            'name': '快速',
            'photo_quality': 80,
            'video_crf': 25,
            'max_photo_width': 1920,
            'max_photo_height': 1920,
            'resolution_preset': '1920x1080 (Full HD)',
            'video_preset': 'veryfast'
        }
    }
    
    def __init__(self, logger=None, host='0.0.0.0', port=5000):
        """
        初始化Web服务器
        
        Args:
            logger: 日志记录器
            host: 服务器地址
            port: 服务器端口
        """
        self.logger = logger or logging.getLogger('FileCompressor.WebServer')
        self.host = host
        self.port = port
        self.server = None
        self.server_thread = None
        self.is_running = False
        
        # 创建独立的Web配置管理器（使用web_config.ini）
        web_config_path = os.path.join(v2_dir, 'web_config.ini')
        self.config_manager = ConfigManager(config_path=web_config_path)
        self.config_manager.load()
        
        # 自动检测GPU可用性并设置配置
        detected_gpu = self._detect_available_gpu()
        if detected_gpu:
            self.config_manager.set('use_gpu', detected_gpu)
            self.config_manager.save()
            self.logger.info(f"Web服务器自动检测到GPU: {detected_gpu}")
        else:
            self.config_manager.set('use_gpu', 'cpu')
            self.config_manager.save()
            self.logger.info("Web服务器未检测到GPU，使用CPU模式")
        
        # 初始化压缩器（使用Web配置）
        self.file_processor = FileProcessor(self.logger)
        self.image_compressor = ImageCompressor(self.config_manager, self.logger)
        self.video_compressor = VideoCompressor(self.config_manager, self.logger)
        
        # 当前使用的配置预设
        self.current_preset = 'balanced'
        
        # 创建Flask应用
        self.app = Flask(__name__, 
                        static_folder=os.path.join(v2_dir, 'web', 'static'),
                        template_folder=os.path.join(v2_dir, 'web', 'templates'))
        CORS(self.app)  # 允许跨域请求
        
        # 配置上传目录
        self.upload_dir = os.path.join(v2_dir, 'web', 'uploads')
        self.output_dir = os.path.join(v2_dir, 'web', 'outputs')
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 设置上传配置
        self.app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB最大文件大小
        self.app.config['UPLOAD_FOLDER'] = self.upload_dir
        self.app.config['OUTPUT_FOLDER'] = self.output_dir
        
        # 注册路由
        self._register_routes()
        
        # 任务状态存储
        self.tasks = {}  # task_id -> task_info
    
    def _register_routes(self):
        """注册路由"""
        
        @self.app.route('/')
        def index():
            """主页"""
            return send_from_directory(
                os.path.join(v2_dir, 'web', 'templates'),
                'index.html'
            )
        
        @self.app.route('/api/status', methods=['GET'])
        def get_status():
            """获取服务器状态"""
            return jsonify({
                'status': 'running' if self.is_running else 'stopped',
                'host': self.host,
                'port': self.port,
                'upload_dir': self.upload_dir,
                'output_dir': self.output_dir
            })
        
        @self.app.route('/api/upload', methods=['POST'])
        def upload_file():
            """上传单个文件（单文件压缩模式）"""
            try:
                # 检查是否有设置更新请求
                if request.form.get('settings'):
                    try:
                        settings = json.loads(request.form.get('settings'))
                        # 应用设置（不包含GPU配置）
                        allowed_keys = {
                            'photo_quality', 'video_crf', 'video_preset',
                            'max_photo_width', 'max_photo_height', 'resolution_preset'
                        }
                        for key, value in settings.items():
                            if key in allowed_keys and key in self.config_manager.defaults:
                                self.config_manager.set(key, value)
                        self.config_manager.save()
                        # 更新压缩器
                        self.image_compressor = ImageCompressor(self.config_manager, self.logger)
                        self.video_compressor = VideoCompressor(self.config_manager, self.logger)
                    except Exception as e:
                        self.logger.warning(f"应用设置失败: {e}")
                
                if 'file' not in request.files:
                    return jsonify({'error': '没有文件被上传'}), 400
                
                file = request.files['file']
                if file.filename == '':
                    return jsonify({'error': '文件名为空'}), 400
                
                # 检查文件扩展名
                file_ext = os.path.splitext(file.filename)[1].lower()
                if file_ext not in ALLOWED_EXTENSIONS:
                    return jsonify({
                        'error': f'不支持的文件格式: {file_ext}。支持的格式: {", ".join(ALLOWED_EXTENSIONS)}'
                    }), 400
                
                # 保存上传的文件
                filename = secure_filename(file.filename)
                timestamp = int(time.time())
                upload_filename = f"{timestamp}_{filename}"
                upload_path = os.path.join(self.upload_dir, upload_filename)
                file.save(upload_path)
                
                self.logger.info(f"Web上传文件: {filename} -> {upload_path}")
                
                # 创建任务ID（上传状态，等待确认）
                task_id = f"single_{timestamp}"
                
                # 创建上传任务（等待确认压缩）
                self.tasks[task_id] = {
                    'status': 'uploaded',  # 已上传，等待确认
                    'filename': filename,
                    'upload_path': upload_path,
                    'file_ext': file_ext,
                    'upload_size': os.path.getsize(upload_path),
                    'progress': 0
                }
                
                return jsonify({
                    'task_id': task_id,
                    'filename': filename,
                    'file_ext': file_ext,
                    'upload_size': os.path.getsize(upload_path),
                    'message': '文件上传成功，请确认后开始压缩'
                }), 200
                
            except Exception as e:
                self.logger.error(f"上传文件错误: {str(e)}")
                return jsonify({'error': f'上传失败: {str(e)}'}), 500
        
        @self.app.route('/api/upload-batch', methods=['POST'])
        def upload_batch():
            """上传多个文件（批量压缩模式）"""
            try:
                # 检查是否有设置更新请求
                if request.form.get('settings'):
                    try:
                        settings = json.loads(request.form.get('settings'))
                        # 应用设置（不包含GPU配置）
                        allowed_keys = {
                            'photo_quality', 'video_crf', 'video_preset',
                            'max_photo_width', 'max_photo_height', 'resolution_preset'
                        }
                        for key, value in settings.items():
                            if key in allowed_keys and key in self.config_manager.defaults:
                                self.config_manager.set(key, value)
                        self.config_manager.save()
                        # 更新压缩器
                        self.image_compressor = ImageCompressor(self.config_manager, self.logger)
                        self.video_compressor = VideoCompressor(self.config_manager, self.logger)
                    except Exception as e:
                        self.logger.warning(f"应用设置失败: {e}")
                
                if 'files' not in request.files:
                    return jsonify({'error': '没有文件被上传'}), 400
                
                files = request.files.getlist('files')
                if not files or all(f.filename == '' for f in files):
                    return jsonify({'error': '没有有效的文件被上传'}), 400
                
                # 验证文件
                valid_files = []
                for file in files:
                    file_ext = os.path.splitext(file.filename)[1].lower()
                    if file_ext in ALLOWED_EXTENSIONS:
                        valid_files.append((file, file_ext))
                    else:
                        self.logger.warning(f"跳过不支持的文件: {file.filename}")
                
                if not valid_files:
                    return jsonify({'error': '没有有效的媒体文件'}), 400
                
                # 创建任务ID（上传状态，等待确认）
                task_id = f"batch_{int(time.time())}"
                
                # 保存上传的文件（等待确认压缩）
                uploaded_files = []
                for idx, (file, file_ext) in enumerate(valid_files):
                    filename = secure_filename(file.filename)
                    timestamp = int(time.time())
                    upload_filename = f"{timestamp}_{idx}_{filename}"
                    upload_path = os.path.join(self.upload_dir, upload_filename)
                    file.save(upload_path)
                    
                    uploaded_files.append({
                        'original_filename': filename,
                        'upload_path': upload_path,
                        'file_ext': file_ext,
                        'upload_size': os.path.getsize(upload_path),
                        'status': 'uploaded'
                    })
                    self.logger.info(f"Web批量上传文件: {filename}")
                
                # 创建上传任务（等待确认）
                self.tasks[task_id] = {
                    'status': 'uploaded',  # 已上传，等待确认
                    'total': len(uploaded_files),
                    'completed': 0,
                    'failed': 0,
                    'files': uploaded_files
                }
                
                return jsonify({
                    'task_id': task_id,
                    'total': len(uploaded_files),
                    'files': uploaded_files,
                    'message': f'{len(uploaded_files)}个文件上传成功，请确认后开始压缩'
                }), 200
                
            except Exception as e:
                self.logger.error(f"批量上传文件错误: {str(e)}")
                return jsonify({'error': f'批量上传失败: {str(e)}'}), 500
        
        @self.app.route('/api/task/<task_id>', methods=['GET'])
        def get_task_status(task_id):
            """获取任务状态（包含下载令牌和预览令牌）"""
            if task_id not in self.tasks:
                return jsonify({'error': '任务不存在'}), 404
            
            task = self.tasks[task_id].copy()
            
            # 为单文件任务生成预览令牌（上传状态）
            if 'upload_path' in task and task.get('status') == 'uploaded':
                if task.get('file_ext', '').lower() in ALLOWED_IMAGE_EXTENSIONS:
                    preview_token = self._generate_download_token(task_id, 'preview_uploaded')
                    task['preview_token'] = preview_token
                    task['preview_uploaded_url'] = f"/api/preview-uploaded/{task_id}/0?token={preview_token}"
            
            # 为单文件任务生成下载令牌和预览令牌（完成状态）
            if 'output_filename' in task:
                filename = task.get('filename', '')
                task['download_token'] = self._generate_download_token(task_id, filename)
                task['download_url'] = f"/api/download/{task_id}/{filename}?token={task['download_token']}"
                
                # 生成预览令牌（如果是图片）
                if task.get('file_ext', '').lower() in ALLOWED_IMAGE_EXTENSIONS:
                    preview_token = self._generate_download_token(task_id, 'preview')
                    task['preview_token'] = preview_token
                    task['preview_original_url'] = f"/api/preview/{task_id}/original?token={preview_token}"
                    task['preview_compressed_url'] = f"/api/preview/{task_id}/compressed?token={preview_token}"
            
            # 为批量任务生成预览和下载令牌
            if 'files' in task:
                for idx, file_info in enumerate(task['files']):
                    # 上传状态：生成上传文件预览令牌
                    if file_info.get('status') == 'uploaded':
                        if file_info.get('file_ext', '').lower() in ALLOWED_IMAGE_EXTENSIONS:
                            preview_token = self._generate_download_token(task_id, f'preview_{idx}')
                            file_info['preview_token'] = preview_token
                            file_info['preview_uploaded_url'] = f"/api/preview-uploaded/{task_id}/{idx}?token={preview_token}"
                    
                    # 完成状态：生成下载令牌和对比预览令牌
                    if file_info.get('status') == 'completed':
                        filename = file_info.get('original_filename', '')
                        file_info['download_token'] = self._generate_download_token(task_id, filename)
                        file_info['download_url'] = f"/api/download/{task_id}/{filename}?token={file_info['download_token']}"
                        
                        # 生成对比预览令牌（如果是图片或视频）
                        file_ext_lower = file_info.get('file_ext', '').lower()
                        if file_ext_lower in ALLOWED_IMAGE_EXTENSIONS or file_ext_lower in ALLOWED_VIDEO_EXTENSIONS:
                            preview_token = self._generate_download_token(task_id, f'preview_compressed_{idx}')
                            file_info['preview_token'] = preview_token
                            file_info['preview_original_url'] = f"/api/preview-compressed/{task_id}/{idx}?type=original&token={preview_token}"
                            file_info['preview_compressed_url'] = f"/api/preview-compressed/{task_id}/{idx}?type=compressed&token={preview_token}"
            
            return jsonify(task)
        
        @self.app.route('/api/download/<task_id>/<filename>', methods=['GET'])
        def download_file(task_id, filename):
            """下载压缩后的文件（带安全验证）"""
            try:
                # 验证时间码（如果提供）
                token = request.args.get('token')
                if not token:
                    return jsonify({'error': '缺少访问令牌'}), 403
                
                # 验证时间码有效性（5分钟内有效）
                try:
                    import hashlib
                    import hmac
                    expected_token = self._generate_download_token(task_id, filename)
                    if not hmac.compare_digest(token, expected_token):
                        return jsonify({'error': '无效的访问令牌'}), 403
                except Exception as e:
                    self.logger.warning(f"令牌验证失败: {e}")
                    return jsonify({'error': '令牌验证失败'}), 403
                
                # 查找任务中的文件
                if task_id not in self.tasks:
                    return jsonify({'error': '任务不存在'}), 404
                
                task = self.tasks[task_id]
                file_info = None
                
                # 单文件任务结构（直接在task中）
                if 'output_path' in task:
                    if task.get('filename') == filename or task.get('output_filename') == filename:
                        file_info = task
                else:
                    # 批量任务结构（在files列表中）
                    for f in task.get('files', []):
                        if f.get('original_filename') == filename or f.get('output_filename') == filename:
                            file_info = f
                            break
                
                if not file_info:
                    return jsonify({'error': '文件不存在'}), 404
                
                file_path = file_info.get('output_path')
                if not file_path or not os.path.exists(file_path):
                    return jsonify({'error': '文件路径不存在'}), 404
                
                # 发送文件
                response = send_file(
                    file_path,
                    as_attachment=True,
                    download_name=file_info.get('output_filename', filename)
                )
                
                # 标记为已下载，准备删除（延迟删除，给下载时间）
                # 注意：实际删除应该在客户端确认下载完成后触发
                # 这里先标记，延迟删除
                if 'download_marked' not in file_info:
                    file_info['download_marked'] = True
                    # 延迟删除（60秒后，给足够时间下载）
                    threading.Timer(60.0, self._delete_file_after_download, args=(file_path,)).start()
                
                return response
                
            except Exception as e:
                self.logger.error(f"下载文件错误: {str(e)}")
                return jsonify({'error': f'下载失败: {str(e)}'}), 500
        
        @self.app.route('/api/download-all/<task_id>', methods=['GET'])
        def download_all(task_id):
            """下载所有压缩后的文件（ZIP）"""
            try:
                if task_id not in self.tasks:
                    return jsonify({'error': '任务不存在'}), 404
                
                task = self.tasks[task_id]
                files = task.get('files', [])
                if not files:
                    return jsonify({'error': '没有可下载的文件'}), 404
                
                # 创建ZIP文件
                import zipfile
                zip_filename = f"compressed_{task_id}.zip"
                zip_path = os.path.join(self.output_dir, zip_filename)
                
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file_info in files:
                        output_path = file_info.get('output_path')
                        if output_path and os.path.exists(output_path):
                            zipf.write(
                                output_path,
                                file_info.get('output_filename', os.path.basename(output_path))
                            )
                
                response = send_file(
                    zip_path,
                    as_attachment=True,
                    download_name=zip_filename
                )
                
                # 标记为已下载，准备删除（延迟删除）
                if 'download_marked' not in task:
                    task['download_marked'] = True
                    # 延迟删除ZIP文件（60秒后）
                    threading.Timer(60.0, self._delete_file_after_download, args=(zip_path,)).start()
                
                return response
                
            except Exception as e:
                self.logger.error(f"下载ZIP错误: {str(e)}")
                return jsonify({'error': f'下载失败: {str(e)}'}), 500
        
        @self.app.route('/api/delete/<task_id>', methods=['POST'])
        def delete_task_files(task_id):
            """客户端确认下载完成，删除服务器文件"""
            try:
                self._delete_task_files(task_id)
                return jsonify({'message': '文件已删除'}), 200
            except Exception as e:
                self.logger.error(f"删除任务文件错误: {str(e)}")
                return jsonify({'error': f'删除失败: {str(e)}'}), 500
        
        @self.app.route('/api/start-compress/<task_id>', methods=['POST'])
        def start_compress(task_id):
            """确认并开始压缩"""
            try:
                if task_id not in self.tasks:
                    return jsonify({'error': '任务不存在'}), 404
                
                task = self.tasks[task_id]
                
                if task.get('status') != 'uploaded':
                    return jsonify({'error': '任务状态不正确，无法开始压缩'}), 400
                
                # 检查是否有设置更新请求
                if request.get_json():
                    settings = request.get_json().get('settings')
                    if settings:
                        try:
                            # 应用设置（不包含GPU配置）
                            allowed_keys = {
                                'photo_quality', 'video_crf', 'video_preset',
                                'max_photo_width', 'max_photo_height', 'resolution_preset'
                            }
                            for key, value in settings.items():
                                if key in allowed_keys and key in self.config_manager.defaults:
                                    self.config_manager.set(key, value)
                            self.config_manager.save()
                            # 更新压缩器
                            self.image_compressor = ImageCompressor(self.config_manager, self.logger)
                            self.video_compressor = VideoCompressor(self.config_manager, self.logger)
                        except Exception as e:
                            self.logger.warning(f"应用设置失败: {e}")
                
                # 单文件压缩
                if 'upload_path' in task:
                    upload_path = task['upload_path']
                    filename = task['filename']
                    file_ext = task['file_ext']
                    
                    # 在后台线程中处理压缩
                    thread = threading.Thread(
                        target=self._compress_single_file,
                        args=(task_id, upload_path, filename, file_ext)
                    )
                    thread.daemon = True
                    thread.start()
                    
                    return jsonify({
                        'message': '压缩已开始',
                        'task_id': task_id
                    }), 200
                
                # 批量压缩
                elif 'files' in task:
                    upload_files = []
                    for file_info in task['files']:
                        if file_info.get('status') == 'uploaded':
                            upload_files.append((
                                file_info['upload_path'],
                                file_info['original_filename'],
                                file_info['file_ext']
                            ))
                    
                    if not upload_files:
                        return jsonify({'error': '没有可压缩的文件'}), 400
                    
                    # 更新任务状态
                    task['status'] = 'processing'
                    task['completed'] = 0
                    task['failed'] = 0
                    
                    # 在后台线程中处理批量压缩
                    thread = threading.Thread(
                        target=self._compress_batch_files,
                        args=(task_id, upload_files)
                    )
                    thread.daemon = True
                    thread.start()
                    
                    return jsonify({
                        'message': f'{len(upload_files)}个文件压缩已开始',
                        'task_id': task_id
                    }), 200
                
                else:
                    return jsonify({'error': '任务格式错误'}), 400
                    
            except Exception as e:
                self.logger.error(f"开始压缩错误: {str(e)}")
                return jsonify({'error': f'开始压缩失败: {str(e)}'}), 500
        
        @self.app.route('/api/preview-uploaded/<task_id>/<file_index>', methods=['GET'])
        def preview_uploaded_file(task_id, file_index):
            """预览已上传的文件（批量模式）"""
            try:
                # 验证时间码
                token = request.args.get('token')
                if not token:
                    return jsonify({'error': '缺少访问令牌'}), 403
                
                # 验证时间码有效性
                try:
                    import hashlib
                    import hmac
                    expected_token = self._generate_download_token(task_id, f'preview_{file_index}')
                    if not hmac.compare_digest(token, expected_token):
                        return jsonify({'error': '无效的访问令牌'}), 403
                except Exception as e:
                    self.logger.warning(f"预览令牌验证失败: {e}")
                    return jsonify({'error': '令牌验证失败'}), 403
                
                if task_id not in self.tasks:
                    return jsonify({'error': '任务不存在'}), 404
                
                task = self.tasks[task_id]
                
                # 单文件模式
                if 'upload_path' in task:
                    file_path = task.get('upload_path')
                    if not file_path or not os.path.exists(file_path):
                        return jsonify({'error': '文件不存在'}), 404
                    
                    file_ext = os.path.splitext(file_path)[1].lower()
                    if file_ext not in ALLOWED_IMAGE_EXTENSIONS:
                        return jsonify({'error': '只支持图片预览'}), 400
                    
                    response = send_file(file_path, mimetype=f'image/{file_ext[1:]}')
                    response.headers['Cache-Control'] = 'public, max-age=31536000'
                    response.headers['ETag'] = str(int(os.path.getmtime(file_path)))
                    return response
                
                # 批量模式
                elif 'files' in task:
                    try:
                        file_index_int = int(file_index)
                        files = task.get('files', [])
                        if file_index_int < 0 or file_index_int >= len(files):
                            return jsonify({'error': '文件索引无效'}), 400
                        
                        file_info = files[file_index_int]
                        file_path = file_info.get('upload_path')
                        
                        if not file_path or not os.path.exists(file_path):
                            return jsonify({'error': '文件不存在'}), 404
                        
                        file_ext = os.path.splitext(file_path)[1].lower()
                        if file_ext not in ALLOWED_IMAGE_EXTENSIONS:
                            return jsonify({'error': '只支持图片预览'}), 400
                        
                        response = send_file(file_path, mimetype=f'image/{file_ext[1:]}')
                        response.headers['Cache-Control'] = 'public, max-age=31536000'
                        response.headers['ETag'] = str(int(os.path.getmtime(file_path)))
                        return response
                    except ValueError:
                        return jsonify({'error': '无效的文件索引'}), 400
                
                return jsonify({'error': '任务格式错误'}), 400
                
            except Exception as e:
                self.logger.error(f"预览上传文件错误: {str(e)}")
                return jsonify({'error': f'预览失败: {str(e)}'}), 500
        
        @self.app.route('/api/preview-compressed/<task_id>/<file_index>', methods=['GET'])
        def preview_compressed_file(task_id, file_index):
            """预览压缩后的文件（批量模式，支持对比）"""
            try:
                # 验证时间码
                token = request.args.get('token')
                file_type = request.args.get('type', 'compressed')  # original 或 compressed
                
                if not token:
                    return jsonify({'error': '缺少访问令牌'}), 403
                
                # 验证时间码有效性
                try:
                    import hashlib
                    import hmac
                    expected_token = self._generate_download_token(task_id, f'preview_compressed_{file_index}')
                    if not hmac.compare_digest(token, expected_token):
                        return jsonify({'error': '无效的访问令牌'}), 403
                except Exception as e:
                    self.logger.warning(f"预览令牌验证失败: {e}")
                    return jsonify({'error': '令牌验证失败'}), 403
                
                if task_id not in self.tasks:
                    return jsonify({'error': '任务不存在'}), 404
                
                task = self.tasks[task_id]
                
                # 批量模式
                if 'files' in task:
                    try:
                        file_index_int = int(file_index)
                        files = task.get('files', [])
                        if file_index_int < 0 or file_index_int >= len(files):
                            return jsonify({'error': '文件索引无效'}), 400
                        
                        file_info = files[file_index_int]
                        
                        if file_type == 'original':
                            file_path = file_info.get('upload_path')
                        else:  # compressed
                            file_path = file_info.get('output_path')
                        
                        if not file_path or not os.path.exists(file_path):
                            return jsonify({'error': '文件不存在'}), 404
                        
                        file_ext = os.path.splitext(file_path)[1].lower()
                        if file_ext not in ALLOWED_IMAGE_EXTENSIONS and file_ext not in ALLOWED_VIDEO_EXTENSIONS:
                            return jsonify({'error': '只支持图片和视频预览'}), 400
                        
                        # 根据文件类型设置MIME类型
                        if file_ext in ALLOWED_IMAGE_EXTENSIONS:
                            mimetype = f'image/{file_ext[1:]}'
                        else:  # 视频
                            mimetype_map = {
                                '.mp4': 'video/mp4',
                                '.avi': 'video/x-msvideo',
                                '.mov': 'video/quicktime',
                                '.mkv': 'video/x-matroska',
                                '.wmv': 'video/x-ms-wmv',
                                '.flv': 'video/x-flv',
                                '.m4v': 'video/mp4',
                                '.webm': 'video/webm',
                                '.3gp': 'video/3gpp'
                            }
                            mimetype = mimetype_map.get(file_ext, 'video/mp4')
                        
                        response = send_file(file_path, mimetype=mimetype)
                        response.headers['Cache-Control'] = 'public, max-age=31536000'
                        response.headers['ETag'] = str(int(os.path.getmtime(file_path)))
                        return response
                    except ValueError:
                        return jsonify({'error': '无效的文件索引'}), 400
                
                return jsonify({'error': '只支持批量模式'}), 400
                
            except Exception as e:
                self.logger.error(f"预览压缩文件错误: {str(e)}")
                return jsonify({'error': f'预览失败: {str(e)}'}), 500
        
        @self.app.route('/api/config/presets', methods=['GET'])
        def get_presets():
            """获取所有预设配置"""
            presets = {}
            for key, value in self.PRESET_CONFIGS.items():
                presets[key] = {
                    'name': value['name'],
                    'photo_quality': value['photo_quality'],
                    'video_crf': value['video_crf'],
                    'resolution': value['resolution_preset']
                }
            return jsonify({
                'presets': presets,
                'current': self.current_preset
            })
        
        @self.app.route('/api/config/preset/<preset_id>', methods=['POST'])
        def set_preset(preset_id):
            """设置配置预设（不包含GPU配置）"""
            if preset_id not in self.PRESET_CONFIGS:
                return jsonify({'error': '预设不存在'}), 400
            
            preset = self.PRESET_CONFIGS[preset_id]
            
            # 应用预设配置（排除GPU配置，GPU由服务器自动检测）
            for key, value in preset.items():
                if key != 'name':  # 排除名称字段
                    self.config_manager.set(key, value)
            
            # 注意：不修改use_gpu配置，保持服务器自动检测的值
            
            # 保存配置
            self.config_manager.save()
            
            # 更新压缩器
            self.image_compressor = ImageCompressor(self.config_manager, self.logger)
            self.video_compressor = VideoCompressor(self.config_manager, self.logger)
            
            self.current_preset = preset_id
            self.logger.info(f"Web配置已切换到预设: {preset['name']}（GPU配置保持不变）")
            
            return jsonify({
                'message': f'已切换到预设: {preset["name"]}',
                'preset': preset_id
            })
        
        @self.app.route('/api/config/advanced', methods=['GET'])
        def get_advanced_config():
            """获取高级配置（不包含GPU配置）"""
            return jsonify({
                'photo_quality': self.config_manager.get('photo_quality'),
                'video_crf': self.config_manager.get('video_crf'),
                'video_preset': self.config_manager.get('video_preset'),
                'max_photo_width': self.config_manager.get('max_photo_width'),
                'max_photo_height': self.config_manager.get('max_photo_height'),
                'resolution_preset': self.config_manager.get('resolution_preset')
                # 注意：GPU配置不暴露给前端，由服务器自动检测和管理
            })
        
        @self.app.route('/api/config/advanced', methods=['POST'])
        def set_advanced_config():
            """设置高级配置（不包含GPU配置）"""
            try:
                data = request.get_json()
                
                # 允许更新的配置项（排除GPU相关配置）
                allowed_keys = {
                    'photo_quality', 'video_crf', 'video_preset',
                    'max_photo_width', 'max_photo_height', 'resolution_preset'
                }
                
                # 更新配置
                for key, value in data.items():
                    if key in allowed_keys and key in self.config_manager.defaults:
                        self.config_manager.set(key, value)
                
                # 保存配置
                self.config_manager.save()
                
                # 更新压缩器
                self.image_compressor = ImageCompressor(self.config_manager, self.logger)
                self.video_compressor = VideoCompressor(self.config_manager, self.logger)
                
                self.current_preset = 'custom'  # 标记为自定义配置
                self.logger.info("Web高级配置已更新（GPU配置保持不变）")
                
                return jsonify({'message': '配置已保存'})
            except Exception as e:
                self.logger.error(f"设置高级配置错误: {str(e)}")
                return jsonify({'error': f'保存配置失败: {str(e)}'}), 500
        
        @self.app.route('/api/config/gpu-status', methods=['GET'])
        def get_gpu_status():
            """获取GPU状态（只读）"""
            current_gpu = self.config_manager.get('use_gpu', 'cpu')
            return jsonify({
                'gpu_type': current_gpu,
                'gpu_name': self._get_gpu_name(current_gpu),
                'auto_detected': True,
                'note': 'GPU配置由服务器自动检测，无法手动修改'
            })
        
        @self.app.route('/api/preview/<task_id>/<file_type>', methods=['GET'])
        def preview_file(task_id, file_type):
            """预览文件（原始文件或压缩后文件）"""
            try:
                # 验证时间码
                token = request.args.get('token')
                if not token:
                    return jsonify({'error': '缺少访问令牌'}), 403
                
                # 验证时间码有效性
                try:
                    import hashlib
                    import hmac
                    expected_token = self._generate_download_token(task_id, 'preview')
                    if not hmac.compare_digest(token, expected_token):
                        return jsonify({'error': '无效的访问令牌'}), 403
                except Exception as e:
                    self.logger.warning(f"预览令牌验证失败: {e}")
                    return jsonify({'error': '令牌验证失败'}), 403
                
                if task_id not in self.tasks:
                    return jsonify({'error': '任务不存在'}), 404
                
                task = self.tasks[task_id]
                file_path = None
                
                # 根据文件类型返回对应的文件
                if file_type == 'original':
                    file_path = task.get('upload_path')
                elif file_type == 'compressed':
                    file_path = task.get('output_path')
                else:
                    return jsonify({'error': '无效的文件类型'}), 400
                
                if not file_path or not os.path.exists(file_path):
                    return jsonify({'error': '文件不存在'}), 404
                
                # 只支持图片预览
                file_ext = os.path.splitext(file_path)[1].lower()
                if file_ext not in ALLOWED_IMAGE_EXTENSIONS:
                    return jsonify({'error': '只支持图片预览'}), 400
                
                # 发送文件（不强制下载，用于预览）
                # 设置缓存头，利用浏览器缓存
                response = send_file(file_path, mimetype=f'image/{file_ext[1:]}')
                response.headers['Cache-Control'] = 'public, max-age=31536000'  # 1年缓存
                response.headers['ETag'] = str(int(os.path.getmtime(file_path)))
                return response
                
            except Exception as e:
                self.logger.error(f"预览文件错误: {str(e)}")
                return jsonify({'error': f'预览失败: {str(e)}'}), 500
    
    def _compress_single_file(self, task_id, upload_path, filename, file_ext):
        """压缩单个文件"""
        try:
            self.tasks[task_id] = {
                'status': 'processing',
                'filename': filename,
                'upload_path': upload_path,  # 保存上传路径用于预览
                'file_ext': file_ext,
                'progress': 0
            }
            
            # 确定输出路径
            output_filename = f"compressed_{filename}"
            output_path = os.path.join(self.output_dir, output_filename)
            
            # 压缩文件
            success = False
            if file_ext in ALLOWED_IMAGE_EXTENSIONS:
                success = self.image_compressor.compress(upload_path, output_path)
            elif file_ext in ALLOWED_VIDEO_EXTENSIONS:
                success = self.video_compressor.compress(upload_path, output_path)
            
            if success and os.path.exists(output_path):
                original_size = os.path.getsize(upload_path)
                compressed_size = os.path.getsize(output_path)
                compression_ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
                
                self.tasks[task_id] = {
                    'status': 'completed',
                    'filename': filename,
                    'output_filename': output_filename,
                    'output_path': output_path,
                    'upload_path': upload_path,  # 保存用于预览对比
                    'file_ext': file_ext,
                    'original_size': original_size,
                    'compressed_size': compressed_size,
                    'compression_ratio': compression_ratio,
                    'progress': 100
                }
                
                self.logger.info(f"Web单文件压缩完成: {filename}, 压缩率: {compression_ratio:.2f}%")
            else:
                self.tasks[task_id] = {
                    'status': 'failed',
                    'filename': filename,
                    'error': '压缩失败'
                }
                self.logger.error(f"Web单文件压缩失败: {filename}")
                
        except Exception as e:
            self.tasks[task_id] = {
                'status': 'failed',
                'filename': filename,
                'error': str(e)
            }
            self.logger.error(f"Web单文件压缩错误: {str(e)}")
        finally:
            # 清理上传文件（可选，保留一段时间）
            pass
    
    def _compress_batch_files(self, task_id, upload_files):
        """批量压缩文件"""
        try:
            task = self.tasks[task_id]
            total = len(upload_files)
            
            for idx, (upload_path, filename, file_ext) in enumerate(upload_files):
                try:
                    # 确定输出路径
                    output_filename = f"compressed_{filename}"
                    output_path = os.path.join(self.output_dir, output_filename)
                    
                    # 保存上传路径用于后续删除
                    file_info_base = {
                        'original_filename': filename,
                        'upload_path': upload_path,
                        'file_ext': file_ext
                    }
                    
                    # 压缩文件
                    success = False
                    if file_ext in ALLOWED_IMAGE_EXTENSIONS:
                        success = self.image_compressor.compress(upload_path, output_path)
                    elif file_ext in ALLOWED_VIDEO_EXTENSIONS:
                        success = self.video_compressor.compress(upload_path, output_path)
                    
                    if success and os.path.exists(output_path):
                        original_size = os.path.getsize(upload_path)
                        compressed_size = os.path.getsize(output_path)
                        compression_ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
                        
                        file_info = {
                            'original_filename': filename,
                            'output_filename': output_filename,
                            'output_path': output_path,
                            'upload_path': upload_path,  # 保存用于后续删除和预览
                            'file_ext': file_ext,
                            'original_size': original_size,
                            'compressed_size': compressed_size,
                            'compression_ratio': compression_ratio,
                            'status': 'completed'
                        }
                        task['files'].append(file_info)
                        task['completed'] += 1
                        self.logger.info(f"Web批量压缩完成 [{idx+1}/{total}]: {filename}")
                    else:
                        task['files'].append({
                            'original_filename': filename,
                            'upload_path': upload_path,  # 保存用于后续删除
                            'file_ext': file_ext,
                            'status': 'failed',
                            'error': '压缩失败'
                        })
                        task['failed'] += 1
                        self.logger.error(f"Web批量压缩失败 [{idx+1}/{total}]: {filename}")
                    
                except Exception as e:
                    task['files'].append({
                        'original_filename': filename,
                        'upload_path': upload_path,  # 保存用于后续删除
                        'file_ext': file_ext,
                        'status': 'failed',
                        'error': str(e)
                    })
                    task['failed'] += 1
                    self.logger.error(f"Web批量压缩错误 [{idx+1}/{total}]: {str(e)}")
                
                # 更新进度
                task['progress'] = int((idx + 1) / total * 100)
            
            # 更新任务状态
            if task['failed'] == 0:
                task['status'] = 'completed'
            elif task['completed'] > 0:
                task['status'] = 'partial'
            else:
                task['status'] = 'failed'
            
            self.logger.info(f"Web批量压缩任务完成: {task_id}, 成功: {task['completed']}, 失败: {task['failed']}")
            
        except Exception as e:
            task['status'] = 'failed'
            task['error'] = str(e)
            self.logger.error(f"Web批量压缩任务错误: {str(e)}")
    
    def start(self):
        """启动Web服务器"""
        if self.is_running:
            self.logger.warning("Web服务器已在运行")
            return
        
        try:
            self.server = make_server(self.host, self.port, self.app, threaded=True)
            self.server_thread = threading.Thread(target=self.server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()
            self.is_running = True
            self.logger.info(f"Web服务器已启动: http://{self.host}:{self.port}")
        except Exception as e:
            self.logger.error(f"启动Web服务器失败: {str(e)}")
            raise
    
    def stop(self):
        """停止Web服务器"""
        if not self.is_running:
            return
        
        try:
            if self.server:
                self.server.shutdown()
            self.is_running = False
            self.logger.info("Web服务器已停止")
        except Exception as e:
            self.logger.error(f"停止Web服务器失败: {str(e)}")
    
    def get_url(self):
        """获取服务器URL"""
        if self.is_running:
            import socket
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            return f"http://{local_ip}:{self.port}"
        return None
    
    def _detect_available_gpu(self):
        """
        自动检测可用的GPU
        优先级：Nvidia > AMD > CPU
        
        Returns:
            'nvidia', 'amd', 或 None（表示使用CPU）
        """
        ffmpeg_path = self.config_manager.get('ffmpeg_path', 'ffmpeg')
        
        # 首先检查Nvidia GPU
        if self._check_nvidia_gpu(ffmpeg_path):
            return 'nvidia'
        
        # 然后检查AMD GPU
        if self._check_amd_gpu(ffmpeg_path):
            return 'amd'
        
        # 没有可用的GPU，返回None（使用CPU）
        return None
    
    def _check_nvidia_gpu(self, ffmpeg_path):
        """检查Nvidia GPU是否可用"""
        try:
            # 首先检查FFmpeg是否支持NVENC编码器
            cmd = [
                ffmpeg_path,
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
                # 检查是否包含NVENC编码器
                if 'h264_nvenc' in output or 'hevc_nvenc' in output:
                    # 进一步检查：尝试实际初始化编码器以验证硬件是否存在
                    # 使用一个简单的测试命令来检测硬件
                    test_cmd = [
                        ffmpeg_path,
                        '-hide_banner',
                        '-f', 'lavfi',
                        '-i', 'testsrc=duration=0.1:size=320x240:rate=1',
                        '-c:v', 'h264_nvenc',
                        '-preset', 'fast',
                        '-frames:v', '1',
                        '-f', 'null',
                        '-'
                    ]
                    test_result = subprocess.run(
                        test_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=10,
                        shell=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                    
                    # 只有测试命令成功（returncode == 0）才认为检测到GPU硬件
                    # 如果失败，检查错误信息中是否明确表示硬件不存在
                    error_output = test_result.stderr.decode('utf-8', errors='ignore').lower()
                    if test_result.returncode == 0:
                        # 测试命令成功，说明硬件存在且可用
                        self.logger.info("检测到Nvidia GPU支持（NVENC编码器）")
                        return True
                    else:
                        # 测试命令失败，检查是否是硬件不存在导致的
                        hardware_error_keywords = [
                            'no device', 'no hardware', 'no nvenc device', 
                            'could not find', 'failed to initialize',
                            'no nvenc capable devices found',
                            'no nvenc capable device found',
                            'no nvenc devices found'
                        ]
                        if any(keyword in error_output for keyword in hardware_error_keywords):
                            self.logger.debug("FFmpeg支持NVENC，但未检测到Nvidia GPU硬件")
                            return False
                        else:
                            # 其他错误，可能是配置问题，但不一定是硬件不存在
                            # 为了安全起见，不认为检测到GPU
                            self.logger.debug(f"NVENC测试命令失败，不确定硬件是否存在。错误: {error_output[:200]}")
                            return False
            
            return False
        except Exception as e:
            self.logger.debug(f"检查Nvidia GPU时出错: {str(e)}")
            return False
    
    def _check_amd_gpu(self, ffmpeg_path):
        """检查AMD GPU是否可用"""
        try:
            # 首先检查FFmpeg是否支持AMF编码器
            cmd = [
                ffmpeg_path,
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
                # 检查是否包含AMF编码器
                if 'h264_amf' in output or 'hevc_amf' in output:
                    # 进一步检查：尝试实际初始化编码器以验证硬件是否存在
                    # 使用一个简单的测试命令来检测硬件
                    test_cmd = [
                        ffmpeg_path,
                        '-hide_banner',
                        '-f', 'lavfi',
                        '-i', 'testsrc=duration=0.1:size=320x240:rate=1',
                        '-c:v', 'h264_amf',
                        '-frames:v', '1',
                        '-f', 'null',
                        '-'
                    ]
                    test_result = subprocess.run(
                        test_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=10,
                        shell=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                    
                    # 只有测试命令成功（returncode == 0）才认为检测到GPU硬件
                    # 如果失败，检查错误信息中是否明确表示硬件不存在
                    error_output = test_result.stderr.decode('utf-8', errors='ignore').lower()
                    if test_result.returncode == 0:
                        # 测试命令成功，说明硬件存在且可用
                        self.logger.info("检测到AMD GPU支持（AMF编码器）")
                        return True
                    else:
                        # 测试命令失败，检查是否是硬件不存在导致的
                        hardware_error_keywords = [
                            'no device', 'no hardware', 'no amf device', 
                            'could not find', 'failed to initialize',
                            'no amf capable devices found',
                            'no amf capable device found'
                        ]
                        if any(keyword in error_output for keyword in hardware_error_keywords):
                            self.logger.debug("FFmpeg支持AMF，但未检测到AMD GPU硬件")
                            return False
                        else:
                            # 其他错误，可能是配置问题，但不一定是硬件不存在
                            # 为了安全起见，不认为检测到GPU
                            self.logger.debug(f"AMF测试命令失败，不确定硬件是否存在。错误: {error_output[:200]}")
                            return False
            
            return False
        except Exception as e:
            self.logger.debug(f"检查AMD GPU时出错: {str(e)}")
            return False
    
    def _get_gpu_name(self, gpu_type):
        """获取GPU类型名称"""
        names = {
            'nvidia': 'Nvidia GPU (NVENC)',
            'amd': 'AMD GPU (AMF)',
            'cpu': 'CPU'
        }
        return names.get(gpu_type, '未知')
    
    def _generate_download_token(self, task_id, filename):
        """生成下载令牌（带时间戳）"""
        import hashlib
        import hmac
        import time
        
        # 使用任务ID、文件名和当前时间（分钟级别）生成令牌
        timestamp = int(time.time() / 300)  # 5分钟有效期
        secret = f"{task_id}_{filename}_{timestamp}"
        token = hashlib.sha256(secret.encode()).hexdigest()
        return token
    
    def _delete_file_after_download(self, file_path):
        """下载后删除文件"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                self.logger.info(f"已删除文件: {file_path}")
        except Exception as e:
            self.logger.error(f"删除文件失败: {file_path}, 错误: {e}")
    
    def _delete_task_files(self, task_id):
        """删除任务相关的所有文件"""
        try:
            if task_id not in self.tasks:
                return
            
            task = self.tasks[task_id]
            
            # 删除单文件任务的文件
            if 'output_path' in task:
                output_path = task.get('output_path')
                upload_path = task.get('upload_path')
                if output_path and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                        self.logger.info(f"已删除输出文件: {output_path}")
                    except Exception:
                        pass
                if upload_path and os.path.exists(upload_path):
                    try:
                        os.remove(upload_path)
                        self.logger.info(f"已删除上传文件: {upload_path}")
                    except Exception:
                        pass
            
            # 删除批量任务的文件
            if 'files' in task:
                for file_info in task.get('files', []):
                    output_path = file_info.get('output_path')
                    upload_path = file_info.get('upload_path')
                    if output_path and os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                            self.logger.info(f"已删除输出文件: {output_path}")
                        except Exception:
                            pass
                    if upload_path and os.path.exists(upload_path):
                        try:
                            os.remove(upload_path)
                            self.logger.info(f"已删除上传文件: {upload_path}")
                        except Exception:
                            pass
        except Exception as e:
            self.logger.error(f"删除任务文件失败: {task_id}, 错误: {e}")

