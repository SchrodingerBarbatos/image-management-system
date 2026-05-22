import os, sys
from flask import Flask, send_from_directory
from flask_cors import CORS
from config import DB_PATH
from models import Base, engine, session

IS_PACKAGED = getattr(sys, 'frozen', False)

app = Flask(__name__, static_folder=None)
CORS(app)

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

from routes.scan import scan_bp
from routes.images import images_bp
from routes.export import export_bp
from routes.pending import pending_bp

app.register_blueprint(scan_bp, url_prefix='/api')
app.register_blueprint(images_bp, url_prefix='/api')
app.register_blueprint(export_bp, url_prefix='/api')
app.register_blueprint(pending_bp, url_prefix='/api')

from routes.export import cleanup_old_exports
cleanup_old_exports()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--debug', action='store_true', default=False)
    parser.add_argument('--open-browser', action='store_true', default=False)
    args = parser.parse_args()

    if args.open_browser:
        import webbrowser, threading
        threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{args.port}')).start()

    app.run(debug=args.debug, port=args.port)
