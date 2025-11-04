"""
打包前验证脚本
检查打包前的准备工作是否完成
"""

import os
import sys
from pathlib import Path

def check_python_version():
    """检查Python版本"""
    # 设置输出编码为UTF-8（Windows兼容）
    import sys
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 7):
        print(f"[X] Python版本过低: {version.major}.{version.minor}")
        print("   需要Python 3.7+")
        return False
    else:
        print(f"[OK] Python版本: {version.major}.{version.minor}.{version.micro}")
        return True

def check_pyinstaller():
    """检查PyInstaller是否安装"""
    try:
        import PyInstaller
        print(f"✅ PyInstaller已安装: {PyInstaller.__version__}")
        return True
    except ImportError:
        print("❌ PyInstaller未安装")
        print("   请运行: pip install PyInstaller")
        return False

def check_dependencies():
    """检查必需的依赖是否安装"""
    required = ['PIL', 'configparser']
    optional = ['flask', 'cv2']
    
    missing = []
    for module in required:
        try:
            __import__(module)
            print(f"✅ {module} 已安装")
        except ImportError:
            print(f"❌ {module} 未安装")
            missing.append(module)
    
    for module in optional:
        try:
            __import__(module)
            print(f"✅ {module} 已安装（可选）")
        except ImportError:
            print(f"⚠️  {module} 未安装（可选，不影响打包）")
    
    return len(missing) == 0

def check_spec_files():
    """检查spec文件是否存在"""
    spec_files = [
        'build_exe_optimized.spec',
        'build_exe.spec',
        'build_exe_with_web.spec',
    ]
    
    all_exist = True
    for spec_file in spec_files:
        if os.path.exists(spec_file):
            print(f"[OK] {spec_file} 存在")
        else:
            print(f"[X] {spec_file} 不存在")
            all_exist = False
    
    return all_exist

def check_main_file():
    """检查主程序文件是否存在"""
    if os.path.exists('compress_tool.py'):
        print("[OK] compress_tool.py 存在")
        return True
    else:
        print("[X] compress_tool.py 不存在")
        return False

def check_module_files():
    """检查核心模块文件是否存在"""
    modules = [
        'config_manager.py',
        'file_processor.py',
        'image_compressor.py',
        'video_compressor.py',
        'compression_history.py',
        'ffmpeg_manager.py',
        'encoder_compatibility.py',
        'file_info.py',
        'path_utils.py',
    ]
    
    missing = []
    for module in modules:
        if os.path.exists(module):
            print(f"[OK] {module} 存在")
        else:
            print(f"[X] {module} 不存在")
            missing.append(module)
    
    return len(missing) == 0

def check_config_file():
    """检查配置文件是否存在"""
    if os.path.exists('config.ini'):
        print("[OK] config.ini 存在")
        return True
    else:
        print("[!] config.ini 不存在（程序会自动创建）")
        return True  # 不是必需，程序会自动创建

def check_upx():
    """检查UPX是否可用"""
    import subprocess
    
    # 首先检查本地UPX目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_upx_dirs = [
        os.path.join(script_dir, 'upx-5.0.2-win64'),
        os.path.join(script_dir, 'upx'),
    ]
    
    upx_path = None
    for upx_dir in local_upx_dirs:
        upx_exe = os.path.join(upx_dir, 'upx.exe')
        if os.path.exists(upx_exe):
            upx_path = upx_exe
            print(f"[OK] 找到本地UPX: {upx_exe}")
            break
    
    # 如果找到本地UPX，添加到PATH
    if upx_path:
        upx_dir = os.path.dirname(upx_path)
        current_path = os.environ.get('PATH', '')
        if upx_dir not in current_path:
            os.environ['PATH'] = upx_dir + os.pathsep + current_path
    
    # 检查UPX是否可用
    try:
        result = subprocess.run(['upx', '--version'], 
                              capture_output=True, 
                              text=True, 
                              timeout=5,
                              env=os.environ)
        if result.returncode == 0:
            version_info = result.stdout.strip().split('\n')[0]
            print(f"[OK] UPX可用: {version_info}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    if upx_path:
        print("[!] UPX文件存在但无法运行，可能需要检查文件权限")
    else:
        print("[!] UPX未找到（可选，用于压缩文件大小）")
        print("   提示: 可以将UPX解压到v2目录下，如: upx-5.0.2-win64\\")
        print("   下载地址: https://upx.github.io/")
    
    return True  # 不是必需的

def main():
    """主函数"""
    # 设置输出编码为UTF-8（Windows兼容）
    import sys
    if sys.platform == 'win32':
        import io
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        except:
            pass  # 如果已经设置过，忽略错误
    
    print("=" * 60)
    print("打包前验证")
    print("=" * 60)
    print()
    
    checks = [
        ("Python版本", check_python_version),
        ("PyInstaller", check_pyinstaller),
        ("依赖项", check_dependencies),
        ("主程序文件", check_main_file),
        ("模块文件", check_module_files),
        ("配置文件", check_config_file),
        ("Spec文件", check_spec_files),
        ("UPX工具", check_upx),
    ]
    
    results = []
    for name, check_func in checks:
        print(f"\n检查 {name}:")
        result = check_func()
        results.append((name, result))
    
    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)
    
    all_passed = True
    for name, result in results:
        status = "[OK] 通过" if result else "[X] 失败"
        print(f"{name}: {status}")
        if not result:
            all_passed = False
    
    print()
    if all_passed:
        print("[OK] 所有检查通过！可以开始打包。")
        print("\n推荐使用以下命令打包:")
        print("  python build_exe.py")
        print("  或")
        print("  pyinstaller --clean build_exe_optimized.spec")
        return 0
    else:
        print("[X] 部分检查未通过，请先解决上述问题。")
        return 1

if __name__ == '__main__':
    sys.exit(main())

