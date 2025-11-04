// IndexedDB文件缓存管理器
class FileCacheManager {
    constructor() {
        this.dbName = 'FileCompressorCache';
        this.dbVersion = 1;
        this.storeName = 'files';
        this.db = null;
        this.maxCacheSize = 100 * 1024 * 1024; // 100MB最大缓存
        this.init();
    }

    async init() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(this.dbName, this.dbVersion);
            
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                this.db = request.result;
                resolve();
            };
            
            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains(this.storeName)) {
                    const objectStore = db.createObjectStore(this.storeName, { keyPath: 'key' });
                    objectStore.createIndex('timestamp', 'timestamp', { unique: false });
                }
            };
        });
    }

    async saveFile(key, file) {
        if (!this.db) await this.init();
        
        return new Promise((resolve, reject) => {
            const transaction = this.db.transaction([this.storeName], 'readwrite');
            const store = transaction.objectStore(this.storeName);
            
            // 检查缓存大小，如果超过限制则清理旧文件
            this.checkAndCleanCache(file.size);
            
            const fileData = {
                key: key,
                name: file.name,
                type: file.type,
                size: file.size,
                timestamp: Date.now(),
                data: file
            };
            
            const request = store.put(fileData);
            request.onsuccess = () => resolve();
            request.onerror = () => reject(request.error);
        });
    }

    async getFile(key) {
        if (!this.db) await this.init();
        
        return new Promise((resolve, reject) => {
            const transaction = this.db.transaction([this.storeName], 'readonly');
            const store = transaction.objectStore(this.storeName);
            const request = store.get(key);
            
            request.onsuccess = () => {
                if (request.result) {
                    resolve(request.result.data);
                } else {
                    resolve(null);
                }
            };
            request.onerror = () => reject(request.error);
        });
    }

    async deleteFile(key) {
        if (!this.db) await this.init();
        
        return new Promise((resolve, reject) => {
            const transaction = this.db.transaction([this.storeName], 'readwrite');
            const store = transaction.objectStore(this.storeName);
            const request = store.delete(key);
            
            request.onsuccess = () => resolve();
            request.onerror = () => reject(request.error);
        });
    }

    async clearAll() {
        if (!this.db) await this.init();
        
        return new Promise((resolve, reject) => {
            const transaction = this.db.transaction([this.storeName], 'readwrite');
            const store = transaction.objectStore(this.storeName);
            const request = store.clear();
            
            request.onsuccess = () => resolve();
            request.onerror = () => reject(request.error);
        });
    }

    async getCacheSize() {
        if (!this.db) await this.init();
        
        return new Promise((resolve, reject) => {
            const transaction = this.db.transaction([this.storeName], 'readonly');
            const store = transaction.objectStore(this.storeName);
            const request = store.getAll();
            
            request.onsuccess = () => {
                let totalSize = 0;
                request.result.forEach(item => {
                    totalSize += item.size || 0;
                });
                resolve(totalSize);
            };
            request.onerror = () => reject(request.error);
        });
    }

    async checkAndCleanCache(newFileSize) {
        const currentSize = await this.getCacheSize();
        if (currentSize + newFileSize > this.maxCacheSize) {
            // 清理最旧的文件，直到有足够空间
            const transaction = this.db.transaction([this.storeName], 'readwrite');
            const store = transaction.objectStore(this.storeName);
            const index = store.index('timestamp');
            
            const request = index.openCursor();
            request.onsuccess = (event) => {
                const cursor = event.target.result;
                if (cursor && currentSize + newFileSize > this.maxCacheSize) {
                    cursor.delete();
                    cursor.continue();
                }
            };
        }
    }

    async getFileAsDataURL(key) {
        const file = await this.getFile(key);
        if (!file) return null;
        
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => resolve(e.target.result);
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }
}

// 常量定义
const FILE_EXTENSIONS = {
    IMAGE: ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp'],
    VIDEO: ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.m4v', '.webm', '.3gp']
};

const API_ENDPOINTS = {
    STATUS: '/api/status',
    UPLOAD_SINGLE: '/api/upload',
    UPLOAD_BATCH: '/api/upload-batch',
    TASK: '/api/task',
    CONFIG_PRESETS: '/api/config/presets',
    CONFIG_ADVANCED: '/api/config/advanced',
    DELETE: '/api/delete',
    DOWNLOAD_ALL: '/api/download-all'
};

const CONSTANTS = {
    MAX_BATCH_FILES: 100,
    MAX_FILE_SIZE: 2 * 1024 * 1024 * 1024,  // 2GB
    DELETE_DELAY: 5000,  // 5秒后通知删除
    FILE_SIZE_UNITS: ['Bytes', 'KB', 'MB', 'GB']
};

// Web应用主逻辑
class CompressorApp {
    constructor() {
        this.apiBase = window.location.origin;
        this.currentTaskId = null;
        this.currentMode = 'single';
        this.pollInterval = null;
        this.currentFile = null;  // 当前上传的文件对象
        this.currentFileKey = null;  // 当前文件的缓存键
        this.fileCache = new FileCacheManager();  // 文件缓存管理器
        this.batchFilesMap = new Map();  // 批量上传的文件映射：task_id -> file_index -> File对象
        this.compressedFilesMap = new Map();  // 压缩后文件缓存映射：task_id -> file_index -> { fileKey, file }
        this.init();
    }

    init() {
        this.setupTabs();
        this.setupSettings();
        this.setupSingleUpload();
        this.setupBatchUpload();
        this.checkServerStatus();
        this.loadSettings();
    }

    setupSettings() {
        // 切换高级设置显示
        const toggleBtn = document.getElementById('toggle-settings');
        const settingsContent = document.getElementById('settings-content');
        
        toggleBtn.addEventListener('click', () => {
            const isVisible = settingsContent.style.display !== 'none';
            settingsContent.style.display = isVisible ? 'none' : 'block';
            toggleBtn.textContent = isVisible ? '高级设置' : '隐藏设置';
        });

        // 预设选择
        const presetSelect = document.getElementById('preset-select');
        presetSelect.addEventListener('change', () => {
            this.loadPreset(presetSelect.value);
        });

        // 保存设置
        document.getElementById('save-settings').addEventListener('click', () => {
            this.saveSettings();
        });

        // 重置设置
        document.getElementById('reset-settings').addEventListener('click', () => {
            this.resetSettings();
        });
    }

    async loadPreset(presetId) {
        try {
            const response = await fetch(`${this.apiBase}${API_ENDPOINTS.CONFIG_PRESETS}`);
            const data = await response.json();
            const preset = data.presets[presetId];
            
            if (preset) {
                // 应用预设值到UI
                document.getElementById('photo-quality').value = preset.photo_quality || 85;
                document.getElementById('video-crf').value = preset.video_crf || 23;
                document.getElementById('max-width').value = preset.max_photo_width || 2000;
                document.getElementById('max-height').value = preset.max_photo_height || 2000;
                document.getElementById('resolution-preset').value = preset.resolution_preset || '1280x720 (HD)';
            }
        } catch (error) {
            console.error('加载预设失败:', error);
        }
    }

    async loadSettings() {
        try {
            const response = await fetch(`${this.apiBase}${API_ENDPOINTS.CONFIG_ADVANCED}`);
            const config = await response.json();
            
            document.getElementById('photo-quality').value = config.photo_quality || 85;
            document.getElementById('video-crf').value = config.video_crf || 23;
            document.getElementById('video-preset').value = config.video_preset || 'medium';
            document.getElementById('max-width').value = config.max_photo_width || 2000;
            document.getElementById('max-height').value = config.max_photo_height || 2000;
            document.getElementById('resolution-preset').value = config.resolution_preset || '1280x720 (HD)';
        } catch (error) {
            console.error('加载设置失败:', error);
        }
    }

    async saveSettings() {
        try {
            const settings = {
                photo_quality: parseInt(document.getElementById('photo-quality').value),
                video_crf: parseInt(document.getElementById('video-crf').value),
                video_preset: document.getElementById('video-preset').value,
                max_photo_width: parseInt(document.getElementById('max-width').value),
                max_photo_height: parseInt(document.getElementById('max-height').value),
                resolution_preset: document.getElementById('resolution-preset').value
            };

            const response = await fetch(`${this.apiBase}${API_ENDPOINTS.CONFIG_ADVANCED}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(settings)
            });

            const data = await response.json();
            if (response.ok) {
                this.updateStatus('设置已保存');
                alert('设置已保存成功！');
            } else {
                this.showError(data.error || '保存设置失败');
            }
        } catch (error) {
            this.showError(`保存设置错误: ${error.message}`);
        }
    }

    resetSettings() {
        this.loadPreset('balanced');
        this.updateStatus('设置已重置');
    }

    setupTabs() {
        const tabBtns = document.querySelectorAll('.tab-btn');
        const tabContents = document.querySelectorAll('.tab-content');

        tabBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                const tabId = btn.dataset.tab;
                
                // 更新按钮状态
                tabBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                
                // 更新内容显示
                tabContents.forEach(content => {
                    content.classList.remove('active');
                });
                document.getElementById(`${tabId}-tab`).classList.add('active');
                
                // 切换tab时清理之前的缓存
                if (this.currentMode !== tabId) {
                    // 清理单文件缓存
                    if (this.currentFileKey) {
                        this.fileCache.deleteFile(this.currentFileKey).catch(err => {
                            console.warn('清理单文件缓存失败:', err);
                        });
                        this.currentFileKey = null;
                    }
                    this.currentFile = null;
                    
                    // 清理批量上传缓存
                    if (this.batchFilesMap.size > 0) {
                        this.batchFilesMap.forEach((taskFilesMap, taskId) => {
                            taskFilesMap.forEach(({ fileKey }) => {
                                this.fileCache.deleteFile(fileKey).catch(err => {
                                    console.warn('清理批量上传缓存失败:', err);
                                });
                            });
                        });
                        this.batchFilesMap.clear();
                    }
                }
                
                this.currentMode = tabId;
            });
        });
    }

    setupSingleUpload() {
        const uploadArea = document.getElementById('single-upload-area');
        const fileInput = document.getElementById('single-file-input');

        // 点击上传
        uploadArea.addEventListener('click', () => {
            fileInput.click();
        });
        
        // 文件选择变化时清理之前的缓存
        fileInput.addEventListener('change', (e) => {
            if (this.currentFileKey) {
                // 清理之前的文件缓存
                this.fileCache.deleteFile(this.currentFileKey).catch(err => {
                    console.warn('清理缓存失败:', err);
                });
                this.currentFileKey = null;
            }
        });

        // 文件选择
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                this.currentFile = e.target.files[0];
                this.previewOriginalFile(this.currentFile);
                this.uploadSingleFile(this.currentFile);
            }
        });

        // 拖拽上传
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('dragover');
        });

        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('dragover');
        });

        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                this.currentFile = e.dataTransfer.files[0];
                this.previewOriginalFile(this.currentFile);
                this.uploadSingleFile(this.currentFile);
            }
        });
    }

    async previewOriginalFile(file) {
        // 只预览图片和视频
        if (file.type.startsWith('image/') || file.type.startsWith('video/')) {
            // 生成缓存键
            const fileKey = `original_${file.name}_${file.size}_${file.lastModified}`;
            this.currentFileKey = fileKey;
            
            // 先尝试从缓存读取
            let dataURL = await this.fileCache.getFileAsDataURL(fileKey);
            
            if (!dataURL) {
                // 缓存中没有，读取文件并保存到缓存
                dataURL = await new Promise((resolve, reject) => {
            const reader = new FileReader();
                    reader.onload = (e) => resolve(e.target.result);
                    reader.onerror = () => reject(reader.error);
                    reader.readAsDataURL(file);
                });
                
                // 保存到IndexedDB缓存
                try {
                    await this.fileCache.saveFile(fileKey, file);
                } catch (err) {
                    console.warn('保存文件到缓存失败:', err);
                }
            }
            
            // 显示预览
                const previewContainer = document.getElementById('preview-before');
                const previewImg = document.getElementById('preview-original-img');
                
            if (file.type.startsWith('image/')) {
                previewImg.src = dataURL;
                previewImg.style.maxWidth = '100%';
                previewImg.style.maxHeight = '400px';
                previewImg.style.objectFit = 'contain';
                previewImg.style.display = 'block';
                previewContainer.style.display = 'block';
            } else if (file.type.startsWith('video/')) {
                // 视频预览
                const videoElement = previewContainer.querySelector('video') || document.createElement('video');
                if (!previewContainer.querySelector('video')) {
                    videoElement.controls = true;
                    videoElement.style.maxWidth = '100%';
                    videoElement.style.maxHeight = '400px';
                    videoElement.style.objectFit = 'contain';
                    previewImg.style.display = 'none';
                    previewContainer.appendChild(videoElement);
                }
                videoElement.src = dataURL;
                videoElement.style.display = 'block';
                previewContainer.style.display = 'block';
            }
        }
    }

    setupBatchUpload() {
        const uploadArea = document.getElementById('batch-upload-area');
        const fileInput = document.getElementById('batch-file-input');

        // 点击上传
        uploadArea.addEventListener('click', () => {
            fileInput.click();
        });

        // 文件选择
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                this.handleBatchFiles(Array.from(e.target.files));
            }
        });

        // 拖拽上传
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('dragover');
        });

        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('dragover');
        });

        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                this.handleBatchFiles(Array.from(e.dataTransfer.files));
            }
        });
    }
    
    /**
     * 处理批量文件上传，检查文件数量和大小限制
     * @param {File[]} files - 要上传的文件列表
     */
    handleBatchFiles(files) {
        // 检查文件数量限制
        if (files.length > CONSTANTS.MAX_BATCH_FILES) {
            this.showError(`单次最多只能上传 ${CONSTANTS.MAX_BATCH_FILES} 个文件，您选择了 ${files.length} 个文件。请分批上传。`);
            return;
        }
        
        // 检查文件大小限制
        const oversizedFiles = [];
        files.forEach(file => {
            if (file.size > CONSTANTS.MAX_FILE_SIZE) {
                oversizedFiles.push(file.name);
            }
        });
        
        if (oversizedFiles.length > 0) {
            this.showError(`以下文件超过 ${this.formatFileSize(CONSTANTS.MAX_FILE_SIZE)} 限制，建议使用桌面GUI版本：\n${oversizedFiles.join('\n')}`);
            // 过滤掉超大文件
            files = files.filter(file => file.size <= CONSTANTS.MAX_FILE_SIZE);
            if (files.length === 0) {
                return;
            }
        }
        
        // 上传文件（支持追加上传）
        this.uploadBatchFiles(files);
    }

    async uploadSingleFile(file) {
        this.updateStatus('上传文件中...');
        
        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch(`${this.apiBase}${API_ENDPOINTS.UPLOAD_SINGLE}`, {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (response.ok) {
                this.currentTaskId = data.task_id;
                this.showSingleFileUploaded(file, data);
                this.updateStatus('文件上传成功，请确认后开始压缩');
            } else {
                this.showError(data.error || '上传失败');
            }
        } catch (error) {
            this.showError(`上传错误: ${error.message}`);
        }
    }
    
    async showSingleFileUploaded(file, uploadData) {
        // 隐藏上传区域
        document.getElementById('single-upload-area').style.display = 'none';
        
        // 使用本地缓存的文件对象显示预览（不调用服务器API）
        if (this.currentFile && (this.currentFile.type.startsWith('image/') || this.currentFile.type.startsWith('video/'))) {
            await this.previewOriginalFile(this.currentFile);
        }
        
        // 显示文件信息和确认按钮
        const fileInfo = document.getElementById('single-file-info');
        fileInfo.style.display = 'block';
        fileInfo.querySelector('.file-name').textContent = file.name;
        fileInfo.querySelector('.file-size').textContent = this.formatFileSize(uploadData.upload_size || file.size);
        
        // 添加确认压缩按钮
        let confirmBtn = fileInfo.querySelector('.confirm-compress-btn');
        if (!confirmBtn) {
            confirmBtn = document.createElement('button');
            confirmBtn.className = 'btn btn-primary confirm-compress-btn';
            confirmBtn.textContent = '确认压缩';
            confirmBtn.style.marginTop = '10px';
            confirmBtn.onclick = () => this.startCompress(uploadData.task_id, 'single');
            fileInfo.appendChild(confirmBtn);
        } else {
            confirmBtn.onclick = () => this.startCompress(uploadData.task_id, 'single');
        }
        
        // 重置进度条
        const progressFill = fileInfo.querySelector('.progress-fill');
        const progressText = fileInfo.querySelector('.progress-text');
        progressFill.style.width = '0%';
        progressText.textContent = '等待确认...';
    }
    
    async startCompress(taskId, mode) {
        try {
            // 获取当前设置
            const settings = {
                photo_quality: parseInt(document.getElementById('photo-quality').value),
                video_crf: parseInt(document.getElementById('video-crf').value),
                video_preset: document.getElementById('video-preset').value,
                max_photo_width: parseInt(document.getElementById('max-width').value),
                max_photo_height: parseInt(document.getElementById('max-height').value),
                resolution_preset: document.getElementById('resolution-preset').value
            };
            
            const response = await fetch(`${this.apiBase}/api/start-compress/${taskId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ settings })
            });
            
            const data = await response.json();
            
            if (response.ok) {
                // 隐藏确认按钮
                const confirmBtn = document.querySelector('.confirm-compress-btn');
                if (confirmBtn) {
                    confirmBtn.style.display = 'none';
                }
                
                // 开始轮询任务状态
                this.startPolling(taskId, mode);
                this.updateStatus('压缩已开始...');
            } else {
                this.showError(data.error || '开始压缩失败');
            }
        } catch (error) {
            this.showError(`开始压缩错误: ${error.message}`);
        }
    }

    async uploadBatchFiles(files) {
        // 如果已有任务，追加到现有任务；否则创建新任务
        const isAppend = this.currentTaskId !== null && this.currentTaskId !== undefined;
        
        this.updateStatus(isAppend ? `追加上传 ${files.length} 个文件...` : `上传 ${files.length} 个文件...`);
        
        const formData = new FormData();
        files.forEach(file => {
            formData.append('files', file);
        });
        
        // 如果是追加上传，传递任务ID
        if (isAppend) {
            formData.append('task_id', this.currentTaskId);
        }

        try {
            const response = await fetch(`${this.apiBase}${API_ENDPOINTS.UPLOAD_BATCH}`, {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (response.ok) {
                this.currentTaskId = data.task_id;
                // 保存文件对象到缓存映射
                if (!this.batchFilesMap.has(data.task_id)) {
                    this.batchFilesMap.set(data.task_id, new Map());
                }
                const taskFilesMap = this.batchFilesMap.get(data.task_id);
                const startIndex = taskFilesMap.size;  // 从现有文件数量开始索引
                files.forEach((file, fileIndex) => {
                    const index = startIndex + fileIndex;  // 使用实际索引
                    // 为每个文件生成缓存键并保存到IndexedDB
                    const fileKey = `batch_${data.task_id}_${index}_${file.name}_${file.size}_${file.lastModified}`;
                    this.fileCache.saveFile(fileKey, file).catch(err => {
                        console.warn('保存文件到缓存失败:', err);
                    });
                    taskFilesMap.set(index, { file, fileKey });
                });
                await this.showBatchFileListUploaded(data);
                this.updateStatus(`${data.total} 个文件已上传，请确认后开始压缩`);
            } else {
                this.showError(data.error || '批量上传失败');
            }
        } catch (error) {
            this.showError(`批量上传错误: ${error.message}`);
        }
    }
    
    async showBatchFileListUploaded(uploadData) {
        // 隐藏上传区域
        document.getElementById('batch-upload-area').style.display = 'none';
        
        // 获取任务状态
            const taskResponse = await fetch(`${this.apiBase}${API_ENDPOINTS.TASK}/${uploadData.task_id}`);
        const task = await taskResponse.json();
        
        const fileList = document.getElementById('batch-file-list');
        fileList.style.display = 'block';
        
        const itemsContainer = document.getElementById('batch-file-items');
        // 如果是追加上传，不清空现有列表，只追加新文件
        const isAppend = itemsContainer.children.length > 0;
        
        if (!isAppend) {
        itemsContainer.innerHTML = '';
        }

        // 显示文件列表（不显示预览按钮）
        task.files.forEach((fileInfo, index) => {
            // 如果是追加上传，跳过已存在的文件
            if (isAppend && index < itemsContainer.children.length) {
                return;
            }
            
            const item = document.createElement('div');
            item.className = 'file-item';
            item.dataset.index = index;
            
            item.innerHTML = `
                <div class="file-item-name">${fileInfo.original_filename}</div>
                <div class="file-item-size">${this.formatFileSize(fileInfo.upload_size)}</div>
                <div><span class="status-badge status-uploaded">已上传</span></div>
                <div>-</div>
            `;
            itemsContainer.appendChild(item);
        });
        
        // 添加确认压缩按钮
        let confirmBtn = fileList.querySelector('.confirm-compress-btn');
        if (!confirmBtn) {
            confirmBtn = document.createElement('button');
            confirmBtn.className = 'btn btn-primary confirm-compress-btn';
            confirmBtn.textContent = '确认压缩所有文件';
            confirmBtn.style.marginTop = '15px';
            confirmBtn.style.width = '100%';
            confirmBtn.onclick = () => this.startCompress(uploadData.task_id, 'batch');
            fileList.appendChild(confirmBtn);
        } else {
            confirmBtn.onclick = () => this.startCompress(uploadData.task_id, 'batch');
            confirmBtn.style.display = 'block';
        }
        
        // 添加"继续上传"按钮
        let addMoreBtn = fileList.querySelector('.add-more-files-btn');
        if (!addMoreBtn) {
            addMoreBtn = document.createElement('button');
            addMoreBtn.className = 'btn btn-secondary add-more-files-btn';
            addMoreBtn.textContent = '继续上传文件';
            addMoreBtn.style.marginTop = '10px';
            addMoreBtn.style.width = '100%';
            addMoreBtn.onclick = () => {
                document.getElementById('batch-file-input').click();
            };
            fileList.appendChild(addMoreBtn);
        } else {
            addMoreBtn.style.display = 'block';
        }
        
        // 重置进度条
        const progressFill = document.getElementById('batch-progress-fill');
        const progressText = document.getElementById('batch-progress-text');
        progressFill.style.width = '0%';
        progressText.textContent = `0 / ${task.files.length}`;
    }

    showSingleFileInfo(file) {
        // 此方法已废弃，使用 showSingleFileUploaded 代替
    }

    showBatchFileList(files) {
        document.getElementById('batch-upload-area').style.display = 'none';
        const fileList = document.getElementById('batch-file-list');
        fileList.style.display = 'block';
        
        const itemsContainer = document.getElementById('batch-file-items');
        itemsContainer.innerHTML = '';

        files.forEach((file, index) => {
            const item = document.createElement('div');
            item.className = 'file-item';
            item.dataset.index = index;
            item.innerHTML = `
                <div class="file-item-name">${file.name}</div>
                <div class="file-item-size">${this.formatFileSize(file.size)}</div>
                <div><span class="status-badge status-processing">处理中</span></div>
                <div>-</div>
            `;
            itemsContainer.appendChild(item);
        });
    }

    startPolling(taskId, mode) {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
        }

        this.pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`${this.apiBase}${API_ENDPOINTS.TASK}/${taskId}`);
                const task = await response.json();

                if (mode === 'single') {
                    this.updateSingleProgress(task);
                } else {
                    this.updateBatchProgress(task);
                }

                if (task.status === 'completed' || task.status === 'failed' || task.status === 'partial') {
                    clearInterval(this.pollInterval);
                    this.pollInterval = null;
                }
            } catch (error) {
                console.error('轮询错误:', error);
            }
        }, 1000);
    }

    updateSingleProgress(task) {
        const fileInfo = document.getElementById('single-file-info');
        const progressFill = fileInfo.querySelector('.progress-fill');
        const progressText = fileInfo.querySelector('.progress-text');

        if (task.status === 'uploaded') {
            // 已上传，等待确认（不显示进度）
            progressFill.style.width = '0%';
            progressText.textContent = '等待确认...';
        } else if (task.status === 'processing') {
            progressFill.style.width = `${task.progress || 0}%`;
            progressText.textContent = `${task.progress || 0}%`;
        } else if (task.status === 'completed') {
            progressFill.style.width = '100%';
            progressText.textContent = '100%';
            this.showSingleResult(task);
        } else if (task.status === 'failed') {
            this.showError(task.error || '压缩失败');
        }
    }

    updateBatchProgress(task) {
        const progressFill = document.getElementById('batch-progress-fill');
        const progressText = document.getElementById('batch-progress-text');
        const itemsContainer = document.getElementById('batch-file-items');
        
        // 隐藏确认按钮（如果存在）
        const confirmBtn = document.querySelector('.confirm-compress-btn');
        if (confirmBtn && task.status !== 'uploaded') {
            confirmBtn.style.display = 'none';
        }

        if (task.status === 'uploaded') {
            // 已上传，等待确认（不显示进度）
            progressFill.style.width = '0%';
            progressText.textContent = `0 / ${task.total}`;
        } else {
            progressFill.style.width = `${task.progress || 0}%`;
            progressText.textContent = `${task.completed + task.failed} / ${task.total}`;
        }

        // 更新文件状态
        task.files.forEach((fileInfo, index) => {
            const item = itemsContainer.children[index];
            if (item) {
                const statusBadge = item.querySelector('.status-badge');
                const actionDiv = item.querySelector('div:last-child');
                
                if (fileInfo.status === 'uploaded') {
                    statusBadge.className = 'status-badge status-uploaded';
                    statusBadge.textContent = '已上传';
                    actionDiv.textContent = '-';
                } else if (fileInfo.status === 'processing') {
                    statusBadge.className = 'status-badge status-processing';
                    statusBadge.textContent = '处理中';
                    actionDiv.textContent = '-';
                } else if (fileInfo.status === 'completed') {
                    statusBadge.className = 'status-badge status-completed';
                    statusBadge.textContent = '完成';
                    
                    // 更新文件大小显示为压缩后大小
                    const sizeDiv = item.querySelector('.file-item-size');
                    if (sizeDiv && fileInfo.compressed_size) {
                        // 显示格式：原始大小 -> 压缩后大小
                        const originalSize = this.formatFileSize(fileInfo.upload_size || fileInfo.original_size || 0);
                        const compressedSize = this.formatFileSize(fileInfo.compressed_size);
                        sizeDiv.textContent = `${originalSize} → ${compressedSize}`;
                        sizeDiv.title = `原始: ${originalSize}, 压缩后: ${compressedSize}`;
                    }
                    
                    // 只显示下载按钮（不显示预览按钮）
                    actionDiv.innerHTML = `<a href="${this.apiBase}${fileInfo.download_url}" class="btn btn-primary btn-sm" style="padding: 4px 12px; font-size: 0.85rem;">下载</a>`;
                    
                    // 缓存压缩后的文件
                    if (fileInfo.download_url) {
                        this.cacheCompressedFile(this.currentTaskId, index, fileInfo.download_url, fileInfo.output_filename || fileInfo.filename, fileInfo.file_ext).catch(err => {
                            console.warn(`缓存压缩后文件失败 (${fileInfo.filename}):`, err);
                        });
                    }
                    
                    // 修改下载链接，优先从缓存下载
                    const downloadLink = actionDiv.querySelector('a[href*="download"]');
                    if (downloadLink && fileInfo.download_url) {
                        downloadLink.addEventListener('click', (e) => {
                            e.preventDefault();
                            this.downloadFile(
                                this.currentTaskId,
                                index,
                                `${this.apiBase}${fileInfo.download_url}`,
                                fileInfo.output_filename || fileInfo.filename,
                                fileInfo.download_token || ''
                            );
                        });
                    }
                } else if (fileInfo.status === 'failed') {
                    statusBadge.className = 'status-badge status-failed';
                    statusBadge.textContent = '失败';
                    actionDiv.textContent = '-';
                }
            }
        });

        if (task.status === 'completed' || task.status === 'partial' || task.status === 'failed') {
            this.showBatchResult(task);
        }
    }

    showSingleResult(task) {
        document.getElementById('single-file-info').style.display = 'none';
        const result = document.getElementById('single-result');
        result.style.display = 'block';

        document.getElementById('original-size').textContent = this.formatFileSize(task.original_size);
        document.getElementById('compressed-size').textContent = this.formatFileSize(task.compressed_size);
        document.getElementById('compression-ratio').textContent = `${task.compression_ratio.toFixed(2)}%`;

        // 显示滑动对比预览按钮（如果是图片或视频）
        const fileExt = task.file_ext || '';
        const isImage = FILE_EXTENSIONS.IMAGE.includes(fileExt.toLowerCase());
        const isVideo = FILE_EXTENSIONS.VIDEO.includes(fileExt.toLowerCase());
        
        // 压缩后替换预览窗口为滑块对比预览（如果是图片或视频）
        if ((isImage || isVideo) && task.preview_original_url && task.preview_compressed_url) {
            // 先缓存压缩后的文件，然后加载预览
            if (task.download_url) {
                this.cacheCompressedFile(this.currentTaskId, null, task.download_url, task.output_filename || task.filename, task.file_ext).then(() => {
                    // 压缩后文件缓存完成，尝试加载预览
                    this.loadPreviewFromCache(this.currentTaskId, null, task.preview_original_url, task.preview_compressed_url, isImage).catch((err) => {
                        console.warn('从缓存加载预览失败:', err);
                        // 如果缓存加载失败，尝试直接从服务器URL加载（至少压缩后文件可以这样）
                        this.loadPreviewWithFallback(this.currentTaskId, null, task.preview_original_url, task.preview_compressed_url, isImage);
                    });
                }).catch(err => {
                    console.warn('缓存压缩后文件失败:', err);
                    // 即使缓存失败，也尝试从服务器URL加载预览
                    this.loadPreviewWithFallback(this.currentTaskId, null, task.preview_original_url, task.preview_compressed_url, isImage);
                });
            } else {
                // 没有下载URL，直接尝试从服务器URL加载
                this.loadPreviewWithFallback(this.currentTaskId, null, task.preview_original_url, task.preview_compressed_url, isImage);
            }
        } else if (task.download_url) {
            // 如果不是图片或视频，只缓存压缩后的文件
            this.cacheCompressedFile(this.currentTaskId, null, task.download_url, task.output_filename || task.filename, task.file_ext).catch(err => {
                console.warn('缓存压缩后文件失败:', err);
            });
        }

        const downloadBtn = document.getElementById('download-btn');
        if (task.download_url || task.download_token) {
            downloadBtn.onclick = () => {
                this.downloadFile(
                    this.currentTaskId,
                    null,
                    task.download_url ? `${this.apiBase}${task.download_url}` : null,
                    task.output_filename || task.filename,
                    task.download_token || ''
                );
            };
        }

        this.updateStatus('压缩完成！');
        
        // 添加"压缩新文件"按钮
        const newFileBtn = document.createElement('button');
        newFileBtn.className = 'btn btn-primary';
        newFileBtn.textContent = '压缩新文件';
        newFileBtn.style.marginTop = '10px';
        newFileBtn.onclick = () => {
            // 重置页面状态
            document.getElementById('single-result').style.display = 'none';
            document.getElementById('single-file-info').style.display = 'none';
            document.getElementById('preview-before').style.display = 'none';
            document.getElementById('preview-before').innerHTML = '<h4>原始文件预览</h4><div class="preview-image-wrapper"><img id="preview-original-img" class="preview-image" alt="原始文件预览"></div>';
            document.getElementById('single-upload-area').style.display = 'block';
            this.currentFile = null;
            this.currentFileKey = null;
            this.currentTaskId = null;
            // 清理缓存
            if (this.currentFileKey) {
                this.fileCache.deleteFile(this.currentFileKey).catch(err => {
                    console.warn('清理缓存失败:', err);
                });
                this.currentFileKey = null;
            }
            this.updateStatus('已重置，可以上传新文件');
        };
        
        const resultDiv = document.getElementById('single-result');
        // 检查是否已存在按钮
        const existingBtn = resultDiv.querySelector('.new-file-btn');
        if (!existingBtn) {
            newFileBtn.className += ' new-file-btn';
            resultDiv.appendChild(newFileBtn);
        }
    }

    cacheImage(url) {
        // 使用浏览器缓存机制
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.src = url;
        
        img.onload = () => {
            try {
                const canvas = document.createElement('canvas');
                canvas.width = img.width;
                canvas.height = img.height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
                
                // 转换为base64缓存到localStorage
                const dataUrl = canvas.toDataURL('image/jpeg', 0.8);
                localStorage.setItem('preview_' + url, dataUrl);
            } catch (err) {
                console.warn('无法缓存图片:', err);
            }
        };
    }

    showBatchResult(task) {
        const result = document.getElementById('batch-result');
        result.style.display = 'block';

        document.getElementById('total-files').textContent = task.total;
        document.getElementById('success-count').textContent = task.completed;
        document.getElementById('failed-count').textContent = task.failed;

        const downloadAllBtn = document.getElementById('download-all-btn');
        downloadAllBtn.onclick = () => {
            window.location.href = `${this.apiBase}${API_ENDPOINTS.DOWNLOAD_ALL}/${this.currentTaskId}`;
        };

        this.updateStatus(`批量压缩完成！成功: ${task.completed}, 失败: ${task.failed}`);
        
        // 添加"压缩新文件"按钮
        const newFileBtn = document.createElement('button');
        newFileBtn.className = 'btn btn-primary';
        newFileBtn.textContent = '压缩新文件';
        newFileBtn.style.marginTop = '10px';
        newFileBtn.onclick = () => {
            // 重置页面状态
            document.getElementById('batch-result').style.display = 'none';
            document.getElementById('batch-file-list').style.display = 'none';
            document.getElementById('batch-upload-area').style.display = 'block';
            this.currentTaskId = null;
            // 清理批量上传缓存
            if (this.batchFilesMap.size > 0) {
                this.batchFilesMap.forEach((taskFilesMap, taskId) => {
                    taskFilesMap.forEach(({ fileKey }) => {
                        this.fileCache.deleteFile(fileKey).catch(err => {
                            console.warn('清理批量上传缓存失败:', err);
                        });
                    });
                });
                this.batchFilesMap.clear();
            }
            this.updateStatus('已重置，可以上传新文件');
        };
        
        const resultDiv = document.getElementById('batch-result');
        // 检查是否已存在按钮
        const existingBtn = resultDiv.querySelector('.new-file-btn');
        if (!existingBtn) {
            newFileBtn.className += ' new-file-btn';
            resultDiv.appendChild(newFileBtn);
        }
    }

    async checkServerStatus() {
        try {
            const response = await fetch(`${this.apiBase}${API_ENDPOINTS.STATUS}`);
            const data = await response.json();
            this.updateStatus(`服务器运行中 - ${data.host}:${data.port}`);
        } catch (error) {
            this.updateStatus('无法连接到服务器');
        }
    }

    updateStatus(message) {
        document.getElementById('status-text').textContent = message;
    }

    showError(message) {
        alert(message);
        this.updateStatus(`错误: ${message}`);
    }

    /**
     * 格式化文件大小
     * @param {number} bytes - 文件大小（字节）
     * @returns {string} 格式化后的文件大小字符串
     */
    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + CONSTANTS.FILE_SIZE_UNITS[i];
    }
    
    /**
     * 检查文件扩展名是否为图片
     * @param {string} fileExt - 文件扩展名
     * @returns {boolean} 是否为图片
     */
    isImageFile(fileExt) {
        return FILE_EXTENSIONS.IMAGE.includes((fileExt || '').toLowerCase());
    }
    
    /**
     * 检查文件扩展名是否为视频
     * @param {string} fileExt - 文件扩展名
     * @returns {boolean} 是否为视频
     */
    isVideoFile(fileExt) {
        return FILE_EXTENSIONS.VIDEO.includes((fileExt || '').toLowerCase());
    }
    
    /**
     * 从缓存或服务器下载文件
     * @param {string} taskId - 任务ID
     * @param {number|null} fileIndex - 文件索引（null表示单文件）
     * @param {string} downloadUrl - 下载URL
     * @param {string} filename - 文件名
     * @param {string} [token] - 下载令牌（可选）
     */
    async downloadFile(taskId, fileIndex, downloadUrl, filename, token = '') {
        try {
            // 优先从缓存下载
            const cachedFile = await this.getCompressedFileFromCache(taskId, fileIndex);
            if (cachedFile) {
                const dataURL = await this.fileCache.getFileAsDataURL(cachedFile.fileKey);
                this._triggerDownload(dataURL, filename);
                return;
            }
            
            // 从服务器下载
            const url = downloadUrl || `${this.apiBase}${API_ENDPOINTS.TASK}/${taskId}/${fileIndex !== null ? fileIndex : 'download'}${token ? `?token=${token}` : ''}`;
            this._triggerDownload(url, filename);
            
            // 下载完成后通知服务器删除文件
            setTimeout(async () => {
                try {
                    await fetch(`${this.apiBase}${API_ENDPOINTS.DELETE}/${taskId}`, {
                        method: 'POST'
                    });
                } catch (err) {
                    console.warn('删除通知失败:', err);
                }
            }, CONSTANTS.DELETE_DELAY);
        } catch (error) {
            console.error('下载文件失败:', error);
            this.showError('下载文件失败: ' + error.message);
        }
    }
    
    /**
     * 触发文件下载
     * @private
     * @param {string} url - 下载URL或dataURL
     * @param {string} filename - 文件名
     */
    _triggerDownload(url, filename) {
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    showPreviewModal(imageUrl, title) {
        // 简单的图片预览模态框
        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.style.display = 'block';
        modal.style.position = 'fixed';
        modal.style.zIndex = '10000';
        modal.style.left = '0';
        modal.style.top = '0';
        modal.style.width = '100%';
        modal.style.height = '100%';
        modal.style.backgroundColor = 'rgba(0, 0, 0, 0.8)';
        modal.style.cursor = 'pointer';

        const content = document.createElement('div');
        content.style.position = 'absolute';
        content.style.left = '50%';
        content.style.top = '50%';
        content.style.transform = 'translate(-50%, -50%)';
        content.style.maxWidth = '90%';
        content.style.maxHeight = '90%';

        const img = document.createElement('img');
        img.src = imageUrl;
        img.style.maxWidth = '100%';
        img.style.maxHeight = '100%';
        img.style.objectFit = 'contain';

        const closeBtn = document.createElement('button');
        closeBtn.textContent = '×';
        closeBtn.style.position = 'absolute';
        closeBtn.style.right = '10px';
        closeBtn.style.top = '10px';
        closeBtn.style.width = '40px';
        closeBtn.style.height = '40px';
        closeBtn.style.fontSize = '30px';
        closeBtn.style.border = 'none';
        closeBtn.style.backgroundColor = 'rgba(0, 0, 0, 0.7)';
        closeBtn.style.color = 'white';
        closeBtn.style.cursor = 'pointer';
        closeBtn.style.borderRadius = '50%';

        content.appendChild(closeBtn);
        content.appendChild(img);
        modal.appendChild(content);

        const closeModal = () => {
            document.body.removeChild(modal);
        };

        closeBtn.onclick = closeModal;
        modal.onclick = (e) => {
            if (e.target === modal) {
                closeModal();
            }
        };

        document.body.appendChild(modal);
    }

    replacePreviewWithSlideCompare(originalUrl, compressedUrl, isImage = true) {
        // 替换预览窗口为滑块对比预览（不弹出模态框）
        const previewContainer = document.getElementById('preview-before');
        if (!previewContainer) return;
        
        // 清空原有内容
        previewContainer.innerHTML = '';
        previewContainer.style.display = 'block';
        
        // 创建滑块预览容器
        const slideCompareContainer = document.createElement('div');
        slideCompareContainer.style.width = '100%';
        slideCompareContainer.style.display = 'flex';
        slideCompareContainer.style.flexDirection = 'column';
        slideCompareContainer.style.gap = '15px';
        
        // 标题和切换按钮
        const titleRow = document.createElement('div');
        titleRow.style.display = 'flex';
        titleRow.style.justifyContent = 'space-between';
        titleRow.style.alignItems = 'center';
        titleRow.style.marginBottom = '10px';
        
        const title = document.createElement('h4');
        title.textContent = '对比预览';
        title.style.margin = '0';
        
        // 切换按钮组
        const modeToggleGroup = document.createElement('div');
        modeToggleGroup.style.display = 'flex';
        modeToggleGroup.style.gap = '10px';
        
        const slideModeBtn = document.createElement('button');
        slideModeBtn.textContent = '滑块对比';
        slideModeBtn.className = 'btn btn-primary';
        slideModeBtn.style.padding = '6px 15px';
        slideModeBtn.style.fontSize = '13px';
        
        const splitModeBtn = document.createElement('button');
        splitModeBtn.textContent = '左右分离';
        splitModeBtn.className = 'btn btn-secondary';
        splitModeBtn.style.padding = '6px 15px';
        splitModeBtn.style.fontSize = '13px';
        
        modeToggleGroup.appendChild(slideModeBtn);
        modeToggleGroup.appendChild(splitModeBtn);
        titleRow.appendChild(title);
        titleRow.appendChild(modeToggleGroup);
        slideCompareContainer.appendChild(titleRow);
        
        // 当前模式：'slide' 或 'split'
        let currentMode = 'slide';
        
        // 画布容器（滑块模式）
        const canvasContainer = document.createElement('div');
        canvasContainer.style.width = '100%';
        canvasContainer.style.height = '400px';
        canvasContainer.style.position = 'relative';
        canvasContainer.style.overflow = 'hidden';
        canvasContainer.style.backgroundColor = '#2c2c2c';
        canvasContainer.style.borderRadius = '5px';
        
        const canvas = document.createElement('canvas');
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.objectFit = 'contain';
        
        // 左右分离容器（分离模式）
        const splitContainer = document.createElement('div');
        splitContainer.style.width = '100%';
        splitContainer.style.height = '400px';
        splitContainer.style.display = 'none';  // 默认隐藏
        splitContainer.style.position = 'relative';
        splitContainer.style.overflow = 'hidden';
        splitContainer.style.backgroundColor = '#2c2c2c';
        splitContainer.style.borderRadius = '5px';
        splitContainer.style.flexDirection = 'row';
        splitContainer.style.gap = '10px';
        splitContainer.style.padding = '10px';
        splitContainer.style.boxSizing = 'border-box';
        
        // 左侧容器（原始）
        const leftContainer = document.createElement('div');
        leftContainer.style.flex = '1';
        leftContainer.style.display = 'flex';
        leftContainer.style.flexDirection = 'column';
        leftContainer.style.alignItems = 'center';
        leftContainer.style.justifyContent = 'center';
        leftContainer.style.backgroundColor = '#1e1e1e';
        leftContainer.style.borderRadius = '5px';
        leftContainer.style.padding = '10px';
        leftContainer.style.boxSizing = 'border-box';
        
        const leftLabel = document.createElement('div');
        leftLabel.textContent = '原始';
        leftLabel.style.color = '#fff';
        leftLabel.style.fontSize = '14px';
        leftLabel.style.fontWeight = 'bold';
        leftLabel.style.marginBottom = '10px';
        
        const leftMedia = document.createElement(isImage ? 'img' : 'video');
        leftMedia.style.maxWidth = '100%';
        leftMedia.style.maxHeight = 'calc(100% - 30px)';
        leftMedia.style.objectFit = 'contain';
        if (!isImage) {
            leftMedia.controls = true;
        }
        
        leftContainer.appendChild(leftLabel);
        leftContainer.appendChild(leftMedia);
        
        // 右侧容器（压缩后）
        const rightContainer = document.createElement('div');
        rightContainer.style.flex = '1';
        rightContainer.style.display = 'flex';
        rightContainer.style.flexDirection = 'column';
        rightContainer.style.alignItems = 'center';
        rightContainer.style.justifyContent = 'center';
        rightContainer.style.backgroundColor = '#1e1e1e';
        rightContainer.style.borderRadius = '5px';
        rightContainer.style.padding = '10px';
        rightContainer.style.boxSizing = 'border-box';
        
        const rightLabel = document.createElement('div');
        rightLabel.textContent = '压缩后';
        rightLabel.style.color = '#fff';
        rightLabel.style.fontSize = '14px';
        rightLabel.style.fontWeight = 'bold';
        rightLabel.style.marginBottom = '10px';
        
        const rightMedia = document.createElement(isImage ? 'img' : 'video');
        rightMedia.style.maxWidth = '100%';
        rightMedia.style.maxHeight = 'calc(100% - 30px)';
        rightMedia.style.objectFit = 'contain';
        if (!isImage) {
            rightMedia.controls = true;
        }
        
        rightContainer.appendChild(rightLabel);
        rightContainer.appendChild(rightMedia);
        
        splitContainer.appendChild(leftContainer);
        splitContainer.appendChild(rightContainer);
        
        // 滑块控制
        const controlPanel = document.createElement('div');
        controlPanel.style.display = 'flex';
        controlPanel.style.alignItems = 'center';
        controlPanel.style.gap = '15px';
        
        const labelLeft = document.createElement('span');
        labelLeft.textContent = '左侧: 原始';
        labelLeft.style.fontSize = '14px';
        
        const slider = document.createElement('input');
        slider.type = 'range';
        slider.min = '0';
        slider.max = '100';
        slider.value = '50';
        slider.style.flex = '1';
        slider.style.height = '8px';
        slider.style.borderRadius = '5px';
        slider.style.background = '#ddd';
        slider.style.outline = 'none';
        
        const labelRight = document.createElement('span');
        labelRight.textContent = '右侧: 压缩后';
        labelRight.style.fontSize = '14px';
        
        // 视频播放控制（如果适用）
        const playbackControls = document.createElement('div');
        playbackControls.style.display = 'none';
        playbackControls.style.flexDirection = 'column';
        playbackControls.style.gap = '10px';
        
        const buttonRow = document.createElement('div');
        buttonRow.style.display = 'flex';
        buttonRow.style.gap = '10px';
        
        const playBtn = document.createElement('button');
        playBtn.textContent = '▶ 播放';
        playBtn.className = 'btn btn-primary';
        playBtn.style.padding = '8px 20px';
        
        const pauseBtn = document.createElement('button');
        pauseBtn.textContent = '⏸ 暂停';
        pauseBtn.className = 'btn btn-primary';
        pauseBtn.style.padding = '8px 20px';
        pauseBtn.disabled = true;
        
        const progressRow = document.createElement('div');
        progressRow.style.display = 'flex';
        progressRow.style.alignItems = 'center';
        progressRow.style.gap = '10px';
        
        const progressLabel = document.createElement('span');
        progressLabel.textContent = '播放进度:';
        progressLabel.style.fontSize = '14px';
        
        const videoProgress = document.createElement('input');
        videoProgress.type = 'range';
        videoProgress.min = '0';
        videoProgress.max = '100';
        videoProgress.value = '0';
        videoProgress.style.flex = '1';
        videoProgress.style.height = '6px';
        
        const timeLabel = document.createElement('span');
        timeLabel.textContent = '00:00 / 00:00';
        timeLabel.style.fontSize = '14px';
        timeLabel.style.minWidth = '120px';
        
        buttonRow.appendChild(playBtn);
        buttonRow.appendChild(pauseBtn);
        progressRow.appendChild(progressLabel);
        progressRow.appendChild(videoProgress);
        progressRow.appendChild(timeLabel);
        playbackControls.appendChild(buttonRow);
        playbackControls.appendChild(progressRow);
        
        controlPanel.appendChild(labelLeft);
        controlPanel.appendChild(slider);
        controlPanel.appendChild(labelRight);
        
        canvasContainer.appendChild(canvas);
        slideCompareContainer.appendChild(canvasContainer);
        slideCompareContainer.appendChild(splitContainer);
        slideCompareContainer.appendChild(controlPanel);
        slideCompareContainer.appendChild(playbackControls);
        previewContainer.appendChild(slideCompareContainer);
        
        // 切换模式函数
        const switchMode = (mode) => {
            currentMode = mode;
            if (mode === 'slide') {
                // 滑块对比模式
                canvasContainer.style.display = 'block';
                splitContainer.style.display = 'none';
                controlPanel.style.display = 'flex';
                slideModeBtn.className = 'btn btn-primary';
                splitModeBtn.className = 'btn btn-secondary';
                drawFrame();  // 重新绘制
            } else {
                // 左右分离模式
                canvasContainer.style.display = 'none';
                splitContainer.style.display = 'flex';
                controlPanel.style.display = 'none';
                playbackControls.style.display = 'none';
                slideModeBtn.className = 'btn btn-secondary';
                splitModeBtn.className = 'btn btn-primary';
            }
        };
        
        // 绑定切换按钮事件
        slideModeBtn.addEventListener('click', () => switchMode('slide'));
        splitModeBtn.addEventListener('click', () => switchMode('split'));
        
        // 状态变量
        let originalImg = null;
        let compressedImg = null;
        let isVideo = !isImage;
        let videoOriginal = null;
        let videoCompressed = null;
        let videoPlaying = false;
        let videoCurrentTime = 0;
        let videoDuration = 0;
        let animationFrameId = null;
        
        // 加载图片/视频
        const loadMedia = async () => {
            try {
                // 检测是否为视频
                if (!isVideo) {
                    const originalExt = originalUrl.split('.').pop().toLowerCase();
                    isVideo = FILE_EXTENSIONS.VIDEO.some(ext => ext.substring(1) === originalExt);
                }
                
                if (isVideo) {
                    // 视频处理
                    videoOriginal = document.createElement('video');
                    videoOriginal.src = originalUrl;
                    videoOriginal.crossOrigin = 'anonymous';
                    videoOriginal.preload = 'metadata';
                    
                    videoCompressed = document.createElement('video');
                    videoCompressed.src = compressedUrl;
                    videoCompressed.crossOrigin = 'anonymous';
                    videoCompressed.preload = 'metadata';
                    
                    videoOriginal.addEventListener('loadedmetadata', () => {
                        videoDuration = videoOriginal.duration;
                        videoProgress.max = Math.floor(videoDuration);
                        timeLabel.textContent = `00:00 / ${this.formatTime(videoDuration)}`;
                        playbackControls.style.display = 'flex';
                        drawFrame();
                    });
                    
                    videoOriginal.addEventListener('timeupdate', () => {
                        if (videoPlaying) {
                            videoCurrentTime = videoOriginal.currentTime;
                            videoProgress.value = videoCurrentTime;
                            timeLabel.textContent = `${this.formatTime(videoCurrentTime)} / ${this.formatTime(videoDuration)}`;
                        }
                    });
                    
                    videoOriginal.addEventListener('ended', () => {
                        pauseVideo();
                    });
                    
                    videoCompressed.addEventListener('loadedmetadata', () => {
                        drawFrame();
                    });
                } else {
                    // 图片处理
                    originalImg = new Image();
                    originalImg.crossOrigin = 'anonymous';
                    originalImg.onload = () => {
                        drawFrame();
                        // 更新左右分离模式的显示
                        if (currentMode === 'split') {
                            leftMedia.src = originalUrl;
                            rightMedia.src = compressedUrl;
                        }
                    };
                    originalImg.src = originalUrl;
                    
                    compressedImg = new Image();
                    compressedImg.crossOrigin = 'anonymous';
                    compressedImg.onload = () => {
                        drawFrame();
                        // 更新左右分离模式的显示
                        if (currentMode === 'split') {
                            leftMedia.src = originalUrl;
                            rightMedia.src = compressedUrl;
                        }
                    };
                    compressedImg.src = compressedUrl;
                    
                    // 设置左右分离模式的图片源
                    leftMedia.src = originalUrl;
                    rightMedia.src = compressedUrl;
                }
            } catch (error) {
                console.error('加载媒体失败:', error);
                previewContainer.innerHTML = '<p style="color: red;">加载媒体失败: ' + error.message + '</p>';
            }
        };
        
        // 绘制帧
        const drawFrame = () => {
            // 只在滑块模式下绘制
            if (currentMode !== 'slide') {
                return;
            }
            
            const ctx = canvas.getContext('2d');
            const containerWidth = canvasContainer.clientWidth;
            const containerHeight = canvasContainer.clientHeight;
            
            canvas.width = containerWidth;
            canvas.height = containerHeight;
            
            ctx.fillStyle = '#2c2c2c';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            
            if (isVideo && videoOriginal && videoCompressed) {
                // 视频处理
                const sliderPos = parseFloat(slider.value) / 100;
                const splitX = canvas.width * sliderPos;
                
                // 绘制原始视频（左侧）
                if (splitX > 0) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(0, 0, splitX, canvas.height);
                    ctx.clip();
                    ctx.drawImage(videoOriginal, 0, 0, canvas.width, canvas.height);
                    ctx.restore();
                }
                
                // 绘制压缩后视频（右侧）
                if (splitX < canvas.width) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(splitX, 0, canvas.width - splitX, canvas.height);
                    ctx.clip();
                    ctx.drawImage(videoCompressed, 0, 0, canvas.width, canvas.height);
                    ctx.restore();
                }
                
                // 绘制分割线（只保留一条线）
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 4;
                ctx.beginPath();
                ctx.moveTo(splitX, 0);
                ctx.lineTo(splitX, canvas.height);
                ctx.stroke();
            } else if (originalImg && compressedImg) {
                // 图片处理
                const sliderPos = parseFloat(slider.value) / 100;
                const splitX = canvas.width * sliderPos;
                
                // 计算显示尺寸（保持比例）
                const imgRatio = originalImg.width / originalImg.height;
                const canvasRatio = canvas.width / canvas.height;
                
                let displayWidth, displayHeight, offsetX, offsetY;
                if (imgRatio > canvasRatio) {
                    displayWidth = canvas.width * 0.95;
                    displayHeight = displayWidth / imgRatio;
                } else {
                    displayHeight = canvas.height * 0.95;
                    displayWidth = displayHeight * imgRatio;
                }
                offsetX = (canvas.width - displayWidth) / 2;
                offsetY = (canvas.height - displayHeight) / 2;
                
                // 确保splitX在有效范围内
                const minSplitX = offsetX;
                const maxSplitX = offsetX + displayWidth;
                const actualSplitX = Math.max(minSplitX, Math.min(maxSplitX, splitX));
                
                // 绘制原始图片（左侧）
                if (actualSplitX > offsetX) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(offsetX, offsetY, actualSplitX - offsetX, displayHeight);
                    ctx.clip();
                    ctx.drawImage(originalImg, offsetX, offsetY, displayWidth, displayHeight);
                    ctx.restore();
                }
                
                // 绘制压缩后图片（右侧）
                if (actualSplitX < offsetX + displayWidth) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(actualSplitX, offsetY, offsetX + displayWidth - actualSplitX, displayHeight);
                    ctx.clip();
                    ctx.drawImage(compressedImg, offsetX, offsetY, displayWidth, displayHeight);
                    ctx.restore();
                }
                
                // 绘制分割线（只保留一条线）
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 4;
                ctx.beginPath();
                ctx.moveTo(actualSplitX, offsetY);
                ctx.lineTo(actualSplitX, offsetY + displayHeight);
                ctx.stroke();
            }
        };
        
        // 视频播放控制
        const playVideo = () => {
            if (isVideo && videoOriginal && videoCompressed) {
                videoPlaying = true;
                videoOriginal.play();
                videoCompressed.play();
                playBtn.disabled = true;
                pauseBtn.disabled = false;
                
                const updateFrame = () => {
                    if (videoPlaying) {
                        drawFrame();
                        animationFrameId = requestAnimationFrame(updateFrame);
                    }
                };
                updateFrame();
            }
        };
        
        const pauseVideo = () => {
            if (isVideo && videoOriginal && videoCompressed) {
                videoPlaying = false;
                videoOriginal.pause();
                videoCompressed.pause();
                playBtn.disabled = false;
                pauseBtn.disabled = true;
                if (animationFrameId) {
                    cancelAnimationFrame(animationFrameId);
                }
            }
        };
        
        // 事件绑定
        slider.addEventListener('input', () => {
            if (currentMode === 'slide') {
                drawFrame();
            }
        });
        
        playBtn.addEventListener('click', playVideo);
        pauseBtn.addEventListener('click', pauseVideo);
        
        videoProgress.addEventListener('input', (e) => {
            if (isVideo && videoOriginal && videoCompressed) {
                const seekTime = parseFloat(e.target.value);
                videoOriginal.currentTime = seekTime;
                videoCompressed.currentTime = seekTime;
                videoCurrentTime = seekTime;
                timeLabel.textContent = `${this.formatTime(seekTime)} / ${this.formatTime(videoDuration)}`;
                if (currentMode === 'slide') {
                    drawFrame();
                }
                // 同步左右分离模式的视频进度
                if (currentMode === 'split') {
                    leftMedia.currentTime = seekTime;
                    rightMedia.currentTime = seekTime;
                }
            }
        });
        
        window.addEventListener('resize', () => {
            if (currentMode === 'slide') {
                drawFrame();
            }
        });
        
        // 左右分离模式下视频同步播放
        if (!isImage) {
            leftMedia.addEventListener('play', () => {
                if (currentMode === 'split' && rightMedia.paused) {
                    rightMedia.play();
                }
            });
            leftMedia.addEventListener('pause', () => {
                if (currentMode === 'split' && !rightMedia.paused) {
                    rightMedia.pause();
                }
            });
            leftMedia.addEventListener('seeked', () => {
                if (currentMode === 'split') {
                    rightMedia.currentTime = leftMedia.currentTime;
                }
            });
            
            rightMedia.addEventListener('play', () => {
                if (currentMode === 'split' && leftMedia.paused) {
                    leftMedia.play();
                }
            });
            rightMedia.addEventListener('pause', () => {
                if (currentMode === 'split' && !leftMedia.paused) {
                    leftMedia.pause();
                }
            });
            rightMedia.addEventListener('seeked', () => {
                if (currentMode === 'split') {
                    leftMedia.currentTime = rightMedia.currentTime;
                }
            });
        }
        
        // 加载媒体
        loadMedia();
    }

    showSlideCompareModal(originalUrl, compressedUrl) {
        // 创建滑动对比预览模态框
        const modal = document.createElement('div');
        modal.className = 'slide-compare-modal';
        modal.style.display = 'block';
        modal.style.position = 'fixed';
        modal.style.zIndex = '10000';
        modal.style.left = '0';
        modal.style.top = '0';
        modal.style.width = '100%';
        modal.style.height = '100%';
        modal.style.backgroundColor = 'rgba(0, 0, 0, 0.95)';
        modal.style.fontFamily = 'Arial, sans-serif';

        // 创建模态框内容
        const container = document.createElement('div');
        container.style.width = '100%';
        container.style.height = '100%';
        container.style.display = 'flex';
        container.style.flexDirection = 'column';
        container.style.padding = '20px';
        container.style.boxSizing = 'border-box';

        // 标题栏
        const header = document.createElement('div');
        header.style.display = 'flex';
        header.style.justifyContent = 'space-between';
        header.style.alignItems = 'center';
        header.style.marginBottom = '20px';
        header.style.color = 'white';

        const title = document.createElement('h2');
        title.textContent = '滑动对比预览';
        title.style.margin = '0';
        title.style.color = 'white';

        const closeBtn = document.createElement('button');
        closeBtn.textContent = '×';
        closeBtn.style.width = '40px';
        closeBtn.style.height = '40px';
        closeBtn.style.fontSize = '30px';
        closeBtn.style.border = 'none';
        closeBtn.style.backgroundColor = 'rgba(255, 255, 255, 0.2)';
        closeBtn.style.color = 'white';
        closeBtn.style.cursor = 'pointer';
        closeBtn.style.borderRadius = '5px';
        closeBtn.style.padding = '0';

        header.appendChild(title);
        header.appendChild(closeBtn);

        // 画布容器
        const canvasContainer = document.createElement('div');
        canvasContainer.style.flex = '1';
        canvasContainer.style.position = 'relative';
        canvasContainer.style.overflow = 'hidden';
        canvasContainer.style.backgroundColor = '#2c2c2c';
        canvasContainer.style.borderRadius = '5px';

        const canvas = document.createElement('canvas');
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.objectFit = 'contain';

        // 滑块控制
        const controlPanel = document.createElement('div');
        controlPanel.style.marginTop = '20px';
        controlPanel.style.display = 'flex';
        controlPanel.style.alignItems = 'center';
        controlPanel.style.gap = '15px';
        controlPanel.style.color = 'white';

        const labelLeft = document.createElement('span');
        labelLeft.textContent = '左侧: 原始';
        labelLeft.style.fontSize = '14px';

        const slider = document.createElement('input');
        slider.type = 'range';
        slider.min = '0';
        slider.max = '100';
        slider.value = '50';
        slider.style.flex = '1';
        slider.style.height = '8px';
        slider.style.borderRadius = '5px';
        slider.style.background = '#ddd';
        slider.style.outline = 'none';

        const labelRight = document.createElement('span');
        labelRight.textContent = '右侧: 压缩后';
        labelRight.style.fontSize = '14px';

        // 视频播放控制（如果适用）
        const playbackControls = document.createElement('div');
        playbackControls.style.display = 'none';
        playbackControls.style.marginTop = '15px';
        playbackControls.style.flexDirection = 'column';
        playbackControls.style.gap = '10px';

        const buttonRow = document.createElement('div');
        buttonRow.style.display = 'flex';
        buttonRow.style.gap = '10px';

        const playBtn = document.createElement('button');
        playBtn.textContent = '▶ 播放';
        playBtn.className = 'btn btn-primary';
        playBtn.style.padding = '8px 20px';

        const pauseBtn = document.createElement('button');
        pauseBtn.textContent = '⏸ 暂停';
        pauseBtn.className = 'btn btn-primary';
        pauseBtn.style.padding = '8px 20px';
        pauseBtn.disabled = true;

        const progressRow = document.createElement('div');
        progressRow.style.display = 'flex';
        progressRow.style.alignItems = 'center';
        progressRow.style.gap = '10px';
        progressRow.style.color = 'white';

        const progressLabel = document.createElement('span');
        progressLabel.textContent = '播放进度:';
        progressLabel.style.fontSize = '14px';

        const videoProgress = document.createElement('input');
        videoProgress.type = 'range';
        videoProgress.min = '0';
        videoProgress.max = '100';
        videoProgress.value = '0';
        videoProgress.style.flex = '1';
        videoProgress.style.height = '6px';

        const timeLabel = document.createElement('span');
        timeLabel.textContent = '00:00 / 00:00';
        timeLabel.style.fontSize = '14px';
        timeLabel.style.minWidth = '120px';

        buttonRow.appendChild(playBtn);
        buttonRow.appendChild(pauseBtn);
        progressRow.appendChild(progressLabel);
        progressRow.appendChild(videoProgress);
        progressRow.appendChild(timeLabel);
        playbackControls.appendChild(buttonRow);
        playbackControls.appendChild(progressRow);

        controlPanel.appendChild(labelLeft);
        controlPanel.appendChild(slider);
        controlPanel.appendChild(labelRight);

        canvasContainer.appendChild(canvas);
        container.appendChild(header);
        container.appendChild(canvasContainer);
        container.appendChild(controlPanel);
        container.appendChild(playbackControls);
        modal.appendChild(container);

        // 状态变量
        let originalImg = null;
        let compressedImg = null;
        let isVideo = false;
        let videoOriginal = null;
        let videoCompressed = null;
        let videoPlaying = false;
        let videoCurrentTime = 0;
        let videoDuration = 0;
        let animationFrameId = null;

        // 加载图片/视频
        const loadMedia = async () => {
            try {
                    // 检测是否为视频
                    const originalExt = originalUrl.split('.').pop().toLowerCase();
                    isVideo = FILE_EXTENSIONS.VIDEO.some(ext => ext.substring(1) === originalExt);

                if (isVideo) {
                    // 视频处理
                    videoOriginal = document.createElement('video');
                    videoOriginal.src = originalUrl;
                    videoOriginal.crossOrigin = 'anonymous';
                    videoOriginal.preload = 'metadata';

                    videoCompressed = document.createElement('video');
                    videoCompressed.src = compressedUrl;
                    videoCompressed.crossOrigin = 'anonymous';
                    videoCompressed.preload = 'metadata';

                    videoOriginal.addEventListener('loadedmetadata', () => {
                        videoDuration = videoOriginal.duration;
                        videoProgress.max = Math.floor(videoDuration);
                        timeLabel.textContent = `00:00 / ${this.formatTime(videoDuration)}`;
                        playbackControls.style.display = 'flex';
                        drawFrame();
                    });

                    videoOriginal.addEventListener('timeupdate', () => {
                        if (videoPlaying) {
                            videoCurrentTime = videoOriginal.currentTime;
                            videoProgress.value = videoCurrentTime;
                            timeLabel.textContent = `${this.formatTime(videoCurrentTime)} / ${this.formatTime(videoDuration)}`;
                        }
                    });

                    videoOriginal.addEventListener('ended', () => {
                        pauseVideo();
                    });

                    videoCompressed.addEventListener('loadedmetadata', () => {
                        drawFrame();
                    });
                } else {
                    // 图片处理
                    originalImg = new Image();
                    originalImg.crossOrigin = 'anonymous';
                    originalImg.onload = () => {
                        drawFrame();
                    };
                    originalImg.src = originalUrl;

                    compressedImg = new Image();
                    compressedImg.crossOrigin = 'anonymous';
                    compressedImg.onload = () => {
                        drawFrame();
                    };
                    compressedImg.src = compressedUrl;
                }
            } catch (error) {
                console.error('加载媒体失败:', error);
                alert('加载媒体失败: ' + error.message);
            }
        };

        // 绘制帧
        const drawFrame = () => {
            const ctx = canvas.getContext('2d');
            const containerWidth = canvasContainer.clientWidth;
            const containerHeight = canvasContainer.clientHeight;

            canvas.width = containerWidth;
            canvas.height = containerHeight;

            ctx.fillStyle = '#2c2c2c';
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            if (isVideo && videoOriginal && videoCompressed) {
                // 视频处理
                const sliderPos = parseFloat(slider.value) / 100;
                const splitX = canvas.width * sliderPos;

                // 绘制原始视频（左侧）
                if (splitX > 0) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(0, 0, splitX, canvas.height);
                    ctx.clip();
                    ctx.drawImage(videoOriginal, 0, 0, canvas.width, canvas.height);
                    ctx.restore();
                }

                // 绘制压缩后视频（右侧）
                if (splitX < canvas.width) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(splitX, 0, canvas.width - splitX, canvas.height);
                    ctx.clip();
                    ctx.drawImage(videoCompressed, 0, 0, canvas.width, canvas.height);
                    ctx.restore();
                }

                // 绘制分割线（只保留一条线）
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 4;
                ctx.beginPath();
                ctx.moveTo(splitX, 0);
                ctx.lineTo(splitX, canvas.height);
                ctx.stroke();
            } else if (originalImg && compressedImg) {
                // 图片处理
                const sliderPos = parseFloat(slider.value) / 100;
                const splitX = canvas.width * sliderPos;

                // 计算显示尺寸（保持比例）
                const imgRatio = originalImg.width / originalImg.height;
                const canvasRatio = canvas.width / canvas.height;

                let displayWidth, displayHeight, offsetX, offsetY;
                if (imgRatio > canvasRatio) {
                    displayWidth = canvas.width * 0.95;
                    displayHeight = displayWidth / imgRatio;
                } else {
                    displayHeight = canvas.height * 0.95;
                    displayWidth = displayHeight * imgRatio;
                }
                offsetX = (canvas.width - displayWidth) / 2;
                offsetY = (canvas.height - displayHeight) / 2;

                // 确保splitX在有效范围内
                const minSplitX = offsetX;
                const maxSplitX = offsetX + displayWidth;
                const actualSplitX = Math.max(minSplitX, Math.min(maxSplitX, splitX));

                // 绘制原始图片（左侧）
                if (actualSplitX > offsetX) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(offsetX, offsetY, actualSplitX - offsetX, displayHeight);
                    ctx.clip();
                    ctx.drawImage(originalImg, offsetX, offsetY, displayWidth, displayHeight);
                    ctx.restore();
                }

                // 绘制压缩后图片（右侧）
                if (actualSplitX < offsetX + displayWidth) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(actualSplitX, offsetY, offsetX + displayWidth - actualSplitX, displayHeight);
                    ctx.clip();
                    ctx.drawImage(compressedImg, offsetX, offsetY, displayWidth, displayHeight);
                    ctx.restore();
                }

                // 绘制分割线（只保留一条线，使用已计算的actualSplitX）
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 4;
                ctx.beginPath();
                ctx.moveTo(actualSplitX, offsetY);
                ctx.lineTo(actualSplitX, offsetY + displayHeight);
                ctx.stroke();
            }
        };

        // 视频播放控制
        const playVideo = () => {
            if (isVideo && videoOriginal && videoCompressed) {
                videoPlaying = true;
                videoOriginal.play();
                videoCompressed.play();
                playBtn.disabled = true;
                pauseBtn.disabled = false;

                const updateFrame = () => {
                    if (videoPlaying) {
                        drawFrame();
                        animationFrameId = requestAnimationFrame(updateFrame);
                    }
                };
                updateFrame();
            }
        };

        const pauseVideo = () => {
            if (isVideo && videoOriginal && videoCompressed) {
                videoPlaying = false;
                videoOriginal.pause();
                videoCompressed.pause();
                playBtn.disabled = false;
                pauseBtn.disabled = true;
                if (animationFrameId) {
                    cancelAnimationFrame(animationFrameId);
                }
            }
        };

        // 事件绑定
        slider.addEventListener('input', () => {
            drawFrame();
        });

        playBtn.addEventListener('click', playVideo);
        pauseBtn.addEventListener('click', pauseVideo);

        videoProgress.addEventListener('input', (e) => {
            if (isVideo && videoOriginal && videoCompressed) {
                const seekTime = parseFloat(e.target.value);
                videoOriginal.currentTime = seekTime;
                videoCompressed.currentTime = seekTime;
                videoCurrentTime = seekTime;
                timeLabel.textContent = `${this.formatTime(seekTime)} / ${this.formatTime(videoDuration)}`;
                drawFrame();
            }
        });

        window.addEventListener('resize', () => {
            drawFrame();
        });

        const closeModal = () => {
            if (videoPlaying) {
                pauseVideo();
            }
            if (videoOriginal) {
                videoOriginal.pause();
                videoOriginal.src = '';
            }
            if (videoCompressed) {
                videoCompressed.pause();
                videoCompressed.src = '';
            }
            if (animationFrameId) {
                cancelAnimationFrame(animationFrameId);
            }
            document.body.removeChild(modal);
        };

        closeBtn.onclick = closeModal;

        // 加载媒体
        loadMedia();

        document.body.appendChild(modal);
    }

    formatTime(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    
    // 缓存压缩后的文件
    async cacheCompressedFile(taskId, fileIndex, downloadUrl, filename, fileExt) {
        try {
            // 下载压缩后的文件
            const response = await fetch(`${this.apiBase}${downloadUrl}`);
            if (!response.ok) {
                throw new Error(`下载失败: ${response.statusText}`);
            }
            
            const blob = await response.blob();
            const file = new File([blob], filename, { type: blob.type || this.getMimeType(fileExt) });
            
            // 生成缓存键
            const fileKey = `compressed_${taskId}_${fileIndex !== null ? fileIndex : 'single'}_${filename}_${file.size}_${Date.now()}`;
            
            // 保存到IndexedDB
            await this.fileCache.saveFile(fileKey, file);
            
            // 保存到内存映射
            if (!this.compressedFilesMap.has(taskId)) {
                this.compressedFilesMap.set(taskId, new Map());
            }
            const taskMap = this.compressedFilesMap.get(taskId);
            taskMap.set(fileIndex !== null ? fileIndex : 'single', { fileKey, file });
            
            console.log('压缩后文件已缓存:', filename);
        } catch (error) {
            console.error('缓存压缩后文件失败:', error);
            throw error;
        }
    }
    
    // 从缓存获取压缩后的文件
    async getCompressedFileFromCache(taskId, fileIndex) {
        try {
            const taskMap = this.compressedFilesMap.get(taskId);
            if (!taskMap) {
                return null;
            }
            
            const key = fileIndex !== null ? fileIndex : 'single';
            const cached = taskMap.get(key);
            if (!cached) {
                return null;
            }
            
            // 验证文件是否还在IndexedDB中
            const file = await this.fileCache.getFile(cached.fileKey);
            if (!file) {
                // 文件已从IndexedDB中删除，清理映射
                taskMap.delete(key);
                return null;
            }
            
            return { fileKey: cached.fileKey, file };
        } catch (error) {
            console.error('获取压缩后文件缓存失败:', error);
            return null;
        }
    }
    
    // 从缓存加载预览
    async loadPreviewFromCache(taskId, fileIndex, originalUrl, compressedUrl, isImage) {
        try {
            // 获取原始文件（从原始文件缓存）
            let originalDataURL = null;
            
            if (fileIndex !== null) {
                // 批量模式：从批量文件映射获取
                const taskFilesMap = this.batchFilesMap.get(taskId);
                if (taskFilesMap && taskFilesMap.has(fileIndex)) {
                    const { fileKey, file } = taskFilesMap.get(fileIndex);
                    originalDataURL = await this.fileCache.getFileAsDataURL(fileKey);
                    // 如果缓存中找不到，但文件对象存在，直接从文件对象读取
                    if (!originalDataURL && file) {
                        originalDataURL = await new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onload = (e) => resolve(e.target.result);
                            reader.onerror = () => reject(reader.error);
                            reader.readAsDataURL(file);
                        });
                    }
                }
            } else {
                // 单文件模式：从当前文件获取
                if (this.currentFileKey) {
                    originalDataURL = await this.fileCache.getFileAsDataURL(this.currentFileKey);
                }
                // 如果缓存中找不到，但文件对象存在，直接从文件对象读取
                if (!originalDataURL && this.currentFile) {
                    originalDataURL = await new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onload = (e) => resolve(e.target.result);
                        reader.onerror = () => reject(reader.error);
                        reader.readAsDataURL(this.currentFile);
                    });
                    // 重新保存到缓存，确保下次能找到
                    if (this.currentFileKey) {
                        this.fileCache.saveFile(this.currentFileKey, this.currentFile).catch(err => {
                            console.warn('重新保存文件到缓存失败:', err);
                        });
                    }
                }
            }
            
            // 获取压缩后文件（从压缩文件缓存）
            const compressedFile = await this.getCompressedFileFromCache(taskId, fileIndex);
            if (!compressedFile) {
                throw new Error('压缩后文件不在缓存中');
            }
            
            const compressedDataURL = await this.fileCache.getFileAsDataURL(compressedFile.fileKey);
            
            // 原始文件必须从客户端获取，不能从服务器获取
            if (!originalDataURL) {
                throw new Error('原始文件不在客户端缓存中，且文件对象已丢失，无法预览');
            }
            
            // 使用缓存的文件进行预览（原始文件和压缩后文件都从缓存读取）
            this.replacePreviewWithSlideCompare(originalDataURL, compressedDataURL, isImage);
        } catch (error) {
            console.error('从缓存加载预览失败:', error);
            throw error;
        }
    }
    
    // 备用加载预览方法（如果缓存失败，从服务器URL加载）
    async loadPreviewWithFallback(taskId, fileIndex, originalUrl, compressedUrl, isImage) {
        try {
            // 首先尝试从缓存加载
            await this.loadPreviewFromCache(taskId, fileIndex, originalUrl, compressedUrl, isImage);
        } catch (error) {
            console.warn('从缓存加载预览失败，尝试从服务器URL加载:', error);
            
            // 获取原始文件（必须从本地缓存）
            let originalDataURL = null;
            
            if (fileIndex !== null) {
                // 批量模式
                const taskFilesMap = this.batchFilesMap.get(taskId);
                if (taskFilesMap && taskFilesMap.has(fileIndex)) {
                    const { fileKey, file } = taskFilesMap.get(fileIndex);
                    originalDataURL = await this.fileCache.getFileAsDataURL(fileKey);
                    if (!originalDataURL && file) {
                        originalDataURL = await new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onload = (e) => resolve(e.target.result);
                            reader.onerror = () => reject(reader.error);
                            reader.readAsDataURL(file);
                        });
                    }
                }
            } else {
                // 单文件模式
                if (this.currentFileKey) {
                    originalDataURL = await this.fileCache.getFileAsDataURL(this.currentFileKey);
                }
                if (!originalDataURL && this.currentFile) {
                    originalDataURL = await new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onload = (e) => resolve(e.target.result);
                        reader.onerror = () => reject(reader.error);
                        reader.readAsDataURL(this.currentFile);
                    });
                }
            }
            
            // 如果原始文件找不到，无法预览
            if (!originalDataURL) {
                this.updateStatus('原始文件不在缓存中，无法显示对比预览');
                return;
            }
            
            // 压缩后文件从服务器URL加载（如果缓存中没有）
            const compressedFile = await this.getCompressedFileFromCache(taskId, fileIndex);
            let compressedDataURL = null;
            
            if (compressedFile) {
                compressedDataURL = await this.fileCache.getFileAsDataURL(compressedFile.fileKey);
            }
            
            // 如果缓存中没有压缩后文件，从服务器URL加载
            if (!compressedDataURL && compressedUrl) {
                // 从服务器URL加载压缩后文件
                compressedDataURL = compressedUrl.startsWith('http') ? compressedUrl : `${this.apiBase}${compressedUrl}`;
            }
            
            if (originalDataURL && compressedDataURL) {
                // 使用原始文件（本地）和压缩后文件（服务器URL或缓存）进行预览
                this.replacePreviewWithSlideCompare(originalDataURL, compressedDataURL, isImage);
            } else {
                this.updateStatus('无法加载预览文件，请刷新页面重试');
            }
        }
    }
    
    // 根据文件扩展名获取MIME类型
    getMimeType(fileExt) {
        const mimeTypes = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.tiff': 'image/tiff',
            '.tif': 'image/tiff',
            '.webp': 'image/webp',
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime',
            '.mkv': 'video/x-matroska',
            '.wmv': 'video/x-ms-wmv',
            '.flv': 'video/x-flv',
            '.m4v': 'video/mp4',
            '.webm': 'video/webm',
            '.3gp': 'video/3gpp'
        };
        return mimeTypes[fileExt.toLowerCase()] || 'application/octet-stream';
    }
}

// 初始化应用
document.addEventListener('DOMContentLoaded', () => {
    new CompressorApp();
});
