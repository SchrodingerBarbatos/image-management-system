from flask import Flask
from flask_cors import CORS
from config import DB_PATH
from models import Base, engine, session

app = Flask(__name__)
CORS(app)

Base.metadata.create_all(bind=engine)

@app.teardown_appcontext
def shutdown_session(exception=None):
    session.remove()

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
    app.run(debug=True, port=5000)
