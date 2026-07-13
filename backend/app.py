import os, sys, datetime
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


def _migrate_export_task_schema(conn):
    """Idempotent migration: add columns to export_task if the table exists."""
    tables = {
        row[0]
        for row in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))
    }
    if 'export_task' not in tables:
        return

    columns = {
        row[1]
        for row in conn.execute(text("PRAGMA table_info('export_task')"))
    }
    if 'barcode_data' not in columns:
        conn.execute(text(
            "ALTER TABLE export_task ADD COLUMN barcode_data TEXT DEFAULT ''"
        ))
    if 'progress' not in columns:
        conn.execute(text(
            "ALTER TABLE export_task ADD COLUMN progress INTEGER DEFAULT 0"
        ))
    if 'total_images' not in columns:
        conn.execute(text(
            "ALTER TABLE export_task ADD COLUMN total_images INTEGER DEFAULT 0"
        ))
    if 'error_message' not in columns:
        conn.execute(text(
            "ALTER TABLE export_task ADD COLUMN error_message TEXT DEFAULT ''"
        ))
    conn.commit()

app = Flask(__name__, static_folder=None)


@app.before_request
def _optional_api_token_guard():
    """If app_config.json has a non-empty api_token, require it on mutating /api/* calls.

    Accepts X-API-Token header or Authorization: Bearer <token> only.
    Query-string tokens are rejected so secrets never land in URLs/logs.

    Safe methods (GET/HEAD/OPTIONS) are left open so thumbnails, original files,
    ZIP/Excel downloads keep working without rewriting every media URL. Destructive
    POST/PUT/PATCH/DELETE still require the token when configured.
    Empty/missing token keeps backward-compatible open LAN access for local tools.
    """
    from flask import request, jsonify
    import hmac
    path = request.path or ''
    if not path.startswith('/api/'):
        return None
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    try:
        from config import load_config
        token = (load_config() or {}).get('api_token') or ''
    except Exception:
        token = ''
    if not token:
        return None
    provided = request.headers.get('X-API-Token') or ''
    if not provided:
        auth = request.headers.get('Authorization') or ''
        if auth.lower().startswith('bearer '):
            provided = auth[7:].strip()
    # Constant-time compare; never accept query-string tokens
    if not provided or not hmac.compare_digest(str(provided), str(token)):
        return jsonify({'error': '未授权：需要有效的 API Token'}), 401
    return None


Base.metadata.create_all(bind=engine)

# Migration: add missing export_task columns (idempotent)
from sqlalchemy import text
with engine.connect() as conn:
    _migrate_export_task_schema(conn)

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
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_barcode_type_ctime ON image (barcode, image_type, folder_mtime)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_scanroot_status ON image (scan_root_id, status)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_iv_barcode_type ON image_version (barcode, image_type)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_iv_barcode_type_latest ON image_version (barcode, image_type, is_latest)'))
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
    # Migration: add last_scan_token to image (token-based leftover detection)
    if 'last_scan_token' not in img_cols:
        conn.execute(text("ALTER TABLE image ADD COLUMN last_scan_token TEXT DEFAULT ''"))
        conn.commit()
    # Create index on (scan_root_id, folder_path) for directory-level queries
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_scanroot_folderpath ON image (scan_root_id, folder_path)'))
    # Create index on (scan_root_id, last_scan_token) for leftover detection
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_scanroot_token ON image (scan_root_id, last_scan_token)'))
    conn.commit()
    # Migration: deduplicate rejected_barcode before adding unique constraint
    # Keep only the earliest record per (scan_root_id, barcode, file_path)
    conn.execute(text('''
        DELETE FROM rejected_barcode WHERE id NOT IN (
            SELECT MIN(id) FROM rejected_barcode GROUP BY scan_root_id, barcode, file_path
        )
    '''))
    # Create unique constraint on rejected_barcode (scan_root_id, barcode, file_path)
    conn.execute(text('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_rejected_root_barcode_path
        ON rejected_barcode (scan_root_id, barcode, file_path)
    '''))
    conn.commit()
    # export_task schema already migrated above via _migrate_export_task_schema

# Rebuild versions if we just added the image_type column
if need_rebuild:
    from versioning import update_all_versions
    threading.Thread(target=update_all_versions, daemon=True).start()

# Migration: create batch_task and related tables if not exist
with engine.connect() as conn:
    conn.execute(text('''
        CREATE TABLE IF NOT EXISTS batch_task (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            progress INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            result_count INTEGER DEFAULT 0,
            error_message TEXT DEFAULT '',
            params_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            started_at TEXT DEFAULT '',
            finished_at TEXT DEFAULT ''
        )
    '''))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_task_type_status ON batch_task (task_type, status, created_at)'))
    conn.execute(text('''
        CREATE TABLE IF NOT EXISTS duplicate_scan_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES batch_task(id),
            barcode TEXT NOT NULL,
            image_type TEXT NOT NULL,
            version_label TEXT,
            version_folder_ctime TEXT,
            folder_ctime TEXT NOT NULL,
            image_count INTEGER DEFAULT 0,
            total_file_size INTEGER DEFAULT 0,
            delete_status TEXT DEFAULT 'pending',
            delete_message TEXT DEFAULT '',
            deleted_at TEXT DEFAULT ''
        )
    '''))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_dup_task_id ON duplicate_scan_result (task_id)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_dup_task_barcode ON duplicate_scan_result (task_id, barcode)'))
    conn.execute(text('''
        CREATE TABLE IF NOT EXISTS low_version_scan_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES batch_task(id),
            barcode TEXT NOT NULL,
            image_type TEXT NOT NULL,
            version_label TEXT,
            folder_ctime TEXT NOT NULL,
            image_count INTEGER DEFAULT 0,
            total_file_size INTEGER DEFAULT 0,
            is_latest INTEGER DEFAULT 0,
            is_only_version INTEGER DEFAULT 0,
            meets_threshold INTEGER DEFAULT 0,
            main_threshold INTEGER DEFAULT 0,
            detail_threshold INTEGER DEFAULT 0,
            status_tag TEXT DEFAULT 'will_delete',
            delete_status TEXT DEFAULT 'pending',
            delete_message TEXT DEFAULT '',
            deleted_at TEXT DEFAULT ''
        )
    '''))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_lv_task_id ON low_version_scan_result (task_id)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_lv_task_barcode ON low_version_scan_result (task_id, barcode)'))
    conn.commit()

    # Migration: add current_item column to batch_task if missing
    task_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('batch_task')"))}
    if 'current_item' not in task_cols:
        conn.execute(text("ALTER TABLE batch_task ADD COLUMN current_item TEXT DEFAULT ''"))
        conn.commit()
    if 'failed_count' not in task_cols:
        conn.execute(text("ALTER TABLE batch_task ADD COLUMN failed_count INTEGER DEFAULT 0"))
        conn.commit()
    if 'failed_items' not in task_cols:
        conn.execute(text("ALTER TABLE batch_task ADD COLUMN failed_items TEXT DEFAULT '[]'"))
        conn.commit()

    # Migration: add phash to image table
    img_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('image')"))}
    if 'phash' not in img_cols:
        conn.execute(text("ALTER TABLE image ADD COLUMN phash TEXT DEFAULT ''"))
        conn.commit()

    # Migration: create duplicate_version_scan_result table
    conn.execute(text('''
        CREATE TABLE IF NOT EXISTS duplicate_version_scan_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES batch_task(id),
            group_id INTEGER NOT NULL,
            barcode TEXT NOT NULL,
            image_type TEXT NOT NULL,
            folder_ctime TEXT NOT NULL,
            version_label TEXT DEFAULT '',
            image_count INTEGER DEFAULT 0,
            total_file_size INTEGER DEFAULT 0,
            total_pixels INTEGER DEFAULT 0,
            is_latest INTEGER DEFAULT 0,
            role TEXT DEFAULT 'clean',
            keep_reason TEXT DEFAULT '',
            delete_status TEXT DEFAULT 'pending',
            delete_message TEXT DEFAULT '',
            deleted_at TEXT DEFAULT '',
            kept_version_ctime TEXT DEFAULT ''
        )
    '''))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_dvsr_task_id ON duplicate_version_scan_result (task_id)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_dvsr_task_group ON duplicate_version_scan_result (task_id, group_id)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_dvsr_task_barcode ON duplicate_version_scan_result (task_id, barcode)'))
    conn.commit()

    # Migration: create deleted_folders tracking table
    conn.execute(text('''
        CREATE TABLE IF NOT EXISTS deleted_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT NOT NULL,
            image_type TEXT NOT NULL,
            folder_ctime TEXT NOT NULL,
            scan_root_id INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT DEFAULT ''
        )
    '''))
    # Add scan_root_id to existing tables (legacy rows default to 0)
    df_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('deleted_folders')"))}
    if 'scan_root_id' not in df_cols:
        conn.execute(text(
            "ALTER TABLE deleted_folders ADD COLUMN scan_root_id INTEGER NOT NULL DEFAULT 0"
        ))
        conn.commit()
    # Drop old unique index (barcode, type, ctime) and create root-scoped one
    conn.execute(text('DROP INDEX IF EXISTS uq_deleted_folder'))
    conn.execute(text(
        'CREATE UNIQUE INDEX IF NOT EXISTS uq_deleted_folder_root '
        'ON deleted_folders (barcode, image_type, folder_ctime, scan_root_id)'
    ))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_df_barcode_type ON deleted_folders (barcode, image_type)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_df_root ON deleted_folders (scan_root_id)'))
    conn.commit()

    # One-shot: migrate legacy scan_root_id=0 rows to a real root when unique,
    # otherwise drop them so they cannot global-blacklist every scan root.
    try:
        already = conn.execute(text(
            "SELECT COUNT(*) FROM scan_log WHERE action = 'deleted_folders_root_migrate'"
        )).fetchone()[0]
        if not already:
            legacy = conn.execute(text(
                "SELECT id, barcode, image_type, folder_ctime FROM deleted_folders "
                "WHERE scan_root_id = 0 OR scan_root_id IS NULL"
            )).fetchall()
            migrated = 0
            dropped = 0
            for row in legacy:
                df_id, bc, it, ctime = row
                roots = conn.execute(text(
                    "SELECT DISTINCT scan_root_id FROM image "
                    "WHERE barcode = :bc AND image_type = :it AND folder_mtime = :ct"
                ), {'bc': bc, 'it': it, 'ct': ctime}).fetchall()
                # Also check if any image ever matched via barcode alone when ctime empty
                if not roots:
                    roots = conn.execute(text(
                        "SELECT DISTINCT scan_root_id FROM image WHERE barcode = :bc"
                    ), {'bc': bc}).fetchall()
                if len(roots) == 1:
                    rid = roots[0][0]
                    # Upsert into root-scoped key; drop the legacy row after
                    try:
                        conn.execute(text(
                            "UPDATE deleted_folders SET scan_root_id = :rid WHERE id = :id"
                        ), {'rid': rid, 'id': df_id})
                        migrated += 1
                    except Exception:
                        conn.execute(text("DELETE FROM deleted_folders WHERE id = :id"), {'id': df_id})
                        dropped += 1
                else:
                    # 0 or >1 candidate roots — cannot safely attribute; drop
                    conn.execute(text("DELETE FROM deleted_folders WHERE id = :id"), {'id': df_id})
                    dropped += 1
            conn.execute(text(
                "INSERT INTO scan_log (action, status, message, created_at) "
                "VALUES ('deleted_folders_root_migrate', 'done', :msg, :now)"
            ), {
                'msg': f'legacy deleted_folders 迁移: 归因 {migrated} 条, 丢弃 {dropped} 条',
                'now': datetime.datetime.now().isoformat(),
            })
            conn.commit()
            if migrated or dropped:
                import logging
                logging.getLogger(__name__).info(
                    "deleted_folders root migrate: migrated=%d dropped=%d", migrated, dropped,
                )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("deleted_folders root migrate skipped: %s", e)

    # Mark stale running and queued tasks as interrupted on startup
    now_iso = datetime.datetime.now().isoformat()
    _running = conn.execute(text("SELECT COUNT(*) FROM batch_task WHERE status = 'running'")).fetchone()[0]
    _queued = conn.execute(text("SELECT COUNT(*) FROM batch_task WHERE status = 'queued'")).fetchone()[0]
    if _running:
        conn.execute(text(
            "UPDATE batch_task SET status = 'interrupted', error_message = '程序重启，任务中断', finished_at = :now WHERE status = 'running'"
        ), {'now': now_iso})
    if _queued:
        conn.execute(text(
            "UPDATE batch_task SET status = 'interrupted', error_message = '程序重启，任务未执行', finished_at = :now WHERE status = 'queued'"
        ), {'now': now_iso})
    if _running or _queued:
        conn.commit()
        import logging
        logging.getLogger(__name__).info(
            "Marked %d running and %d queued batch tasks as interrupted on startup", _running, _queued
        )

# Migration: move non-GTIN barcodes from image to rejected_barcode (once only)
try:
    with engine.connect() as conn:
        already_migrated = conn.execute(text(
            "SELECT COUNT(*) FROM scan_log WHERE action = 'gtin_migration'"
        )).fetchone()[0]
        if not already_migrated:
            from scanner import validate_gtin
            rows = conn.execute(text(
                "SELECT id, barcode, image_type, file_path, filename, scan_root_id FROM image"
            )).fetchall()
            migrated = 0
            for row in rows:
                img_id, barcode, image_type, file_path, filename, scan_root_id = row
                is_valid, reason = validate_gtin(barcode)
                if not is_valid:
                    conn.execute(text(
                        "INSERT INTO rejected_barcode (barcode, file_path, filename, reason, scan_root_id, created_at) "
                        "VALUES (:barcode, :file_path, :filename, :reason, :scan_root_id, :created_at)"
                    ), {
                        'barcode': barcode,
                        'file_path': file_path,
                        'filename': filename,
                        'reason': reason,
                        'scan_root_id': scan_root_id,
                        'created_at': datetime.datetime.now().isoformat(),
                    })
                    conn.execute(text("DELETE FROM image WHERE id = :id"), {'id': img_id})
                    other_count = conn.execute(text(
                        "SELECT COUNT(*) FROM image WHERE barcode = :barcode"
                    ), {'barcode': barcode}).fetchone()[0]
                    if other_count == 0:
                        conn.execute(text(
                            "DELETE FROM image_version WHERE barcode = :barcode"
                        ), {'barcode': barcode})
                    else:
                        conn.execute(text(
                            "DELETE FROM image_version WHERE barcode = :barcode AND image_type = :image_type"
                        ), {'barcode': barcode, 'image_type': image_type})
                    migrated += 1
            conn.execute(text(
                "INSERT INTO scan_log (action, status, message, created_at) "
                "VALUES ('gtin_migration', 'done', :msg, :now)"
            ), {
                'msg': f'迁移完成: 移动 {migrated} 条非标品记录',
                'now': datetime.datetime.now().isoformat(),
            })
            conn.commit()
            if migrated:
                import logging
                logging.getLogger(__name__).info("GTIN 迁移完成: 移动 %d 条非标品记录到 rejected_barcode", migrated)
except Exception as e:
    import logging
    logging.getLogger(__name__).warning("GTIN 迁移跳过: %s", e)

# Migration: clean orphaned RCN ImageVersion records (once only)
try:
    with engine.connect() as conn:
        already_cleaned = conn.execute(text(
            "SELECT COUNT(*) FROM scan_log WHERE action = 'rcn_version_cleanup'"
        )).fetchone()[0]
        if not already_cleaned:
            # 仅删除「无任何 image 行」的孤立版本：
            # 1) GTIN-13 RCN 前缀 200-299
            # 2) GTIN-14 RCN（第 2–4 位 200-299）
            # 3) GTIN-12 NS=2/4（首位 2/4）
            # 4) 已在 rejected_barcode 且无 image 的条码
            # 注意：绝不删除仍有 image 行的 barcode 的版本
            result = conn.execute(text(
                "DELETE FROM image_version WHERE "
                "barcode NOT IN (SELECT DISTINCT barcode FROM image) "
                "AND ("
                "  (length(barcode) = 13 AND CAST(substr(barcode, 1, 3) AS INTEGER) BETWEEN 200 AND 299)"
                "  OR (length(barcode) = 14 AND CAST(substr(barcode, 2, 3) AS INTEGER) BETWEEN 200 AND 299)"
                "  OR (length(barcode) = 12 AND substr(barcode, 1, 1) IN ('2', '4'))"
                "  OR barcode IN (SELECT DISTINCT barcode FROM rejected_barcode)"
                ")"
            ))
            total_deleted = result.rowcount
            conn.execute(text(
                "INSERT INTO scan_log (action, status, message, created_at) "
                "VALUES ('rcn_version_cleanup', 'done', :msg, :now)"
            ), {
                'msg': f'清理完成: 删除 {total_deleted} 条孤立 RCN/rejected 版本',
                'now': datetime.datetime.now().isoformat(),
            })
            conn.commit()
            if total_deleted:
                import logging
                logging.getLogger(__name__).info(
                    "RCN 版本清理完成: 删除 %d 条孤立 RCN/rejected 版本记录",
                    total_deleted)
except Exception as e:
    import logging
    logging.getLogger(__name__).warning("RCN 版本清理跳过: %s", e)

from routes.scan import scan_bp
from routes.images import images_bp
from routes.export import export_bp
from routes.pending import pending_bp
from routes.batch import batch_bp
from routes.batch_tasks import batch_tasks_bp
from routes.rejected import rejected_bp
from routes.settings import settings_bp

app.register_blueprint(scan_bp, url_prefix='/api')
app.register_blueprint(images_bp, url_prefix='/api')
app.register_blueprint(export_bp, url_prefix='/api')
app.register_blueprint(pending_bp, url_prefix='/api')
app.register_blueprint(batch_bp, url_prefix='/api')
app.register_blueprint(batch_tasks_bp, url_prefix='/api')
app.register_blueprint(rejected_bp, url_prefix='/api/rejected-barcodes')
app.register_blueprint(settings_bp, url_prefix='/api')


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


def _cleanup_exports_on_startup():
    """Shared startup cleanup — reset stale processing first, then remove old
    export records.  Called by both tray and non-tray startup paths."""
    from routes.export import cleanup_old_exports, reset_stale_processing
    reset_stale_processing()
    cleanup_old_exports()


def start_tray(port, open_browser_on_start=True):
    import pystray
    from PIL import Image as PILImage
    import webbrowser
    from config import LOG_DIR

    # Set up file logging since --windowed suppresses console output
    log_file = os.path.join(LOG_DIR, 'app.log')
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    logger = logging.getLogger(__name__)

    # Restore debug mode handler if it was enabled
    from config import load_config
    cfg = load_config()
    if cfg.get('debug_mode', False):
        from routes.settings import _ensure_debug_handler
        _ensure_debug_handler()
        logger.info("调试模式已恢复开启状态")

    # Cleanup old export tasks on startup
    _cleanup_exports_on_startup()

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
        target=lambda: __import__('waitress').serve(app, host='0.0.0.0', port=port, threads=8),
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
            _cleanup_exports_on_startup()
            _ensure_firewall_rule(port)
            _configure_cors(app, port)
            # Restore debug mode handler if it was enabled
            from config import load_config
            cfg = load_config()
            if cfg.get('debug_mode', False):
                from routes.settings import _ensure_debug_handler
                _ensure_debug_handler()
            if args.open_browser:
                import webbrowser
                threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
            if args.debug:
                # Flask dev server for debug mode (reloader + debugger)
                app.run(host='0.0.0.0', debug=True, port=port)
            else:
                import waitress
                waitress.serve(app, host='0.0.0.0', port=port, threads=8)
