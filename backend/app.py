import os, sys
import ctypes
import threading
import socket
import logging
import re
try:
    import tkinter.messagebox
except ImportError:
    tkinter = None
from flask import Flask, send_from_directory
from flask_cors import CORS
from config import DB_PATH
from models import Base, engine, session

IS_PACKAGED = getattr(sys, 'frozen', False)


def _is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _request_admin():
    """Re-launch the current script with administrator privileges."""
    if IS_PACKAGED:
        exe = sys.executable
        args = sys.argv[1:]
        params = ' '.join(f'"{a}"' for a in args)
        ret = ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, params, None, 1)
    else:
        exe = sys.executable
        script = os.path.abspath(sys.argv[0])
        args = sys.argv[1:]
        params = f'"{script}" ' + ' '.join(f'"{a}"' for a in args)
        ret = ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, params, None, 1)
    if ret <= 32:
        tkinter.messagebox.showerror(
            "权限不足",
            "需要管理员权限来配置防火墙规则。\n请以管理员身份重新运行此程序。",
        )


app = Flask(__name__, static_folder=None)

Base.metadata.create_all(bind=engine)

@app.teardown_appcontext
def shutdown_session(exception=None):
    session.remove()

# Determine frontend dist path
if IS_PACKAGED:
    FRONTEND_DIR = os.path.join(sys._MEIPASS, 'frontend')
else:
    FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'dist')

@app.route('/')
def serve_index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    return send_from_directory(os.path.join(FRONTEND_DIR, 'assets'), filename)

@app.route('/<path:filename>')
def serve_frontend(filename):
    file_path = os.path.join(FRONTEND_DIR, filename)
    if os.path.isfile(file_path):
        return send_from_directory(FRONTEND_DIR, filename)
    return send_from_directory(FRONTEND_DIR, 'index.html')

# Migration: add columns that may not exist in existing databases
from sqlalchemy import text
with engine.connect() as conn:
    existing = {row[1] for row in conn.execute(text("PRAGMA table_info('scan_root')"))}
    if 'allow_fuzzy' not in existing:
        conn.execute(text('ALTER TABLE scan_root ADD COLUMN allow_fuzzy INTEGER DEFAULT 0'))
    if 'fuzzy_image_type' not in existing:
        conn.execute(text("ALTER TABLE scan_root ADD COLUMN fuzzy_image_type TEXT DEFAULT 'main'"))
    conn.commit()
    # Create barcode_setting table if not exists
    conn.execute(text('''
        CREATE TABLE IF NOT EXISTS barcode_setting (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT UNIQUE NOT NULL,
            default_main_mtime TEXT DEFAULT '',
            default_detail_mtime TEXT DEFAULT ''
        )
    '''))
    conn.commit()
    # Create performance indexes if not exist
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_status_barcode_type ON image (status, barcode, image_type)'))
    conn.commit()
    # Migration: add image_type to image_version and update unique constraint
    ver_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('image_version')"))}
    need_rebuild = False
    if 'image_type' not in ver_cols:
        conn.execute(text("ALTER TABLE image_version ADD COLUMN image_type TEXT DEFAULT 'main'"))
        conn.commit()
        need_rebuild = True
    # Drop old unique constraint (on barcode+content_hash only)
    try:
        conn.execute(text('DROP INDEX IF EXISTS uq_barcode_content'))
        conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to drop old index uq_barcode_content: %s", e)
    # Create new unique constraint on barcode+image_type+content_hash
    conn.execute(text('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_barcode_type_content
        ON image_version (barcode, image_type, content_hash)
    '''))
    conn.commit()
    # Migration: add duplicate_mtimes to image_version
    if 'duplicate_mtimes' not in ver_cols:
        conn.execute(text("ALTER TABLE image_version ADD COLUMN duplicate_mtimes TEXT DEFAULT ''"))
        conn.commit()
    # Migration: add content_md5 to image (real MD5 for DB portability)
    img_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('image')"))}
    if 'content_md5' not in img_cols:
        conn.execute(text("ALTER TABLE image ADD COLUMN content_md5 TEXT DEFAULT ''"))
        conn.commit()
    # Migration: add progress and total_images to export_task
    task_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('export_task')"))}
    if 'progress' not in task_cols:
        conn.execute(text("ALTER TABLE export_task ADD COLUMN progress INTEGER DEFAULT 0"))
    if 'total_images' not in task_cols:
        conn.execute(text("ALTER TABLE export_task ADD COLUMN total_images INTEGER DEFAULT 0"))
    if 'error_message' not in task_cols:
        conn.execute(text("ALTER TABLE export_task ADD COLUMN error_message TEXT DEFAULT ''"))
    conn.commit()

# Rebuild versions if we just added the image_type column
if need_rebuild:
    from versioning import update_all_versions
    threading.Thread(target=update_all_versions, daemon=True).start()

from routes.scan import scan_bp
from routes.images import images_bp
from routes.export import export_bp
from routes.pending import pending_bp

app.register_blueprint(scan_bp, url_prefix='/api')
app.register_blueprint(images_bp, url_prefix='/api')
app.register_blueprint(export_bp, url_prefix='/api')
app.register_blueprint(pending_bp, url_prefix='/api')


def _get_icon_path():
    if IS_PACKAGED:
        return os.path.join(sys._MEIPASS, 'image_manager_flat_multires.ico')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'image_manager_flat_multires.ico')


def _check_port(host, port):
    """Return True if port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _resolve_port(host, preferred):
    """Return first available port starting from preferred, or None if all 10 are taken."""
    if _check_port(host, preferred):
        return preferred
    for p in range(preferred + 1, preferred + 10):
        if _check_port(host, p):
            return p
    return None


def _ensure_firewall_rule(port):
    """Add Windows Firewall inbound rule for the given port, if not already present."""
    import subprocess
    rule_name = '图片管理系统 (Image Manager)'
    try:
        check = subprocess.run(
            ['netsh', 'advfirewall', 'firewall', 'show', 'rule', f'name={rule_name}'],
            capture_output=True, text=True, timeout=10,
        )
        if re.search(r'Rule Name:\s+' + re.escape(rule_name) + r'\s*$', check.stdout, re.MULTILINE):
            return
        subprocess.run(
            [
                'netsh', 'advfirewall', 'firewall', 'add', 'rule',
                f'name={rule_name}',
                'dir=in',
                'action=allow',
                'protocol=TCP',
                f'localport={port}',
                'profile=any',
            ],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        import sys as _sys
        print(f"[WARNING] 防火墙规则配置失败: {e}", file=_sys.stderr)


def _get_lan_ips():
    """Return LAN IPv4 addresses of this machine."""
    ips = set()
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and not ip.startswith('127.'):
            ips.add(ip)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ip and not ip.startswith('127.') and ':' not in ip:
                ips.add(ip)
    except Exception:
        pass
    return ips


def _configure_cors(app, port):
    """Restrict CORS to localhost and LAN IPs on the given port."""
    origins = [f'http://localhost:{port}', f'http://127.0.0.1:{port}']
    for ip in _get_lan_ips():
        origins.append(f'http://{ip}:{port}')
    CORS(app, origins=origins)


def start_tray(port, open_browser_on_start=True):
    import pystray
    from PIL import Image as PILImage
    import webbrowser

    # Set up file logging since --windowed suppresses console output
    log_dir = os.path.dirname(DB_PATH)
    log_file = os.path.join(log_dir, 'image-manager.log')
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    logger = logging.getLogger(__name__)

    # Cleanup old export tasks on startup
    from routes.export import cleanup_old_exports
    cleanup_old_exports()

    # Check port availability with fallback
    resolved = _resolve_port('127.0.0.1', port)
    if resolved is None:
        logger.critical("No available port in range %s-%s", port, port + 9)
        try:
            tkinter.messagebox.showerror("启动失败", f"端口 {port}-{port+9} 均被占用，无法启动服务")
        except Exception:
            pass
        return
    if resolved != port:
        logger.warning("Port %s is in use, using fallback port %s", port, resolved)
    port = resolved

    _ensure_firewall_rule(port)
    _configure_cors(app, port)

    stop_event = threading.Event()

    flask_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', debug=False, port=port, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()

    # Load tray icon with fallback
    try:
        icon_image = PILImage.open(_get_icon_path())
    except Exception as e:
        logger.warning("Failed to load tray icon: %s, using default", e)
        icon_image = None

    def _open_web(icon, item):
        webbrowser.open(f'http://localhost:{port}')

    def _quit(icon, item):
        logger.info("Tray quit requested, shutting down")
        stop_event.set()
        session.remove()
        try:
            engine.dispose()
        except Exception:
            pass
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem('打开网页', _open_web, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出', _quit),
    )

    tray_icon = pystray.Icon('image-manager', icon_image, '图片管理系统', menu)

    logger.info("Tray started on port %s", port)

    if open_browser_on_start:
        threading.Timer(0.8, lambda: webbrowser.open(f'http://localhost:{port}')).start()

    tray_icon.run()


if __name__ == '__main__':
    if IS_PACKAGED and not _is_admin():
        _request_admin()
        sys.exit(0)

    if IS_PACKAGED:
        start_tray(port=5000, open_browser_on_start=True)
    else:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--port', type=int, default=5000)
        parser.add_argument('--debug', action='store_true', default=False)
        parser.add_argument('--open-browser', action='store_true', default=False)
        parser.add_argument('--tray', action='store_true', default=False)
        args = parser.parse_args()

        if args.tray:
            start_tray(port=args.port, open_browser_on_start=args.open_browser)
        else:
            port = args.port
            resolved = _resolve_port('127.0.0.1', port)
            if resolved is None:
                print(f"Error: ports {port}-{port+9} are all in use", file=sys.stderr)
                sys.exit(1)
            if resolved != port:
                print(f"Port {port} in use, using {resolved} instead")
            port = resolved
            from routes.export import cleanup_old_exports
            cleanup_old_exports()
            _ensure_firewall_rule(port)
            _configure_cors(app, port)
            if args.open_browser:
                import webbrowser
                threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
            app.run(host='0.0.0.0', debug=args.debug, port=port)
