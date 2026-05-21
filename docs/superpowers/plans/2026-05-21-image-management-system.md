# 商品图片管理系统 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an enterprise internal image management system with folder scanning, barcode-based indexing, version management, thumbnail preview, and Excel batch export.

**Architecture:** Flask REST API backend with SQLAlchemy/SQLite, React+TypeScript frontend with Ant Design. Backend scans configured folders, parses filenames by regex (strict+fuzzy), indexes images with MD5 dedup, versions by folder mtime. Frontend provides table+card layout with batch selection across both views.

**Tech Stack:** Python 3.10+ / Flask 3.x / SQLAlchemy 2.x / Pillow / openpyxl | Node 18+ / React 18 / TypeScript / Ant Design 5.x / Vite 5.x / axios

---

### Task 1: Backend scaffolding — dependencies, config, Flask entry point

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/config.py`
- Create: `backend/app.py`

- [ ] **Step 1: Create requirements.txt**

```
flask==3.1.0
flask-cors==5.0.1
sqlalchemy==2.0.36
pillow==11.1.0
openpyxl==3.1.5
```

- [ ] **Step 2: Install dependencies**

Run: `cd backend && pip install -r requirements.txt`
Expected: all packages install without error

- [ ] **Step 3: Create config.py**

```python
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'images.db')
THUMBNAIL_DIR = os.path.join(DATA_DIR, 'thumbnails')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

THUMBNAIL_SIZE = (200, 200)
THUMBNAIL_QUALITY = 75
ZIP_CLEANUP_HOURS = 24
```

- [ ] **Step 4: Create app.py — Flask entry point**

```python
from flask import Flask
from flask_cors import CORS
from config import DB_PATH
from models import Base, engine

app = Flask(__name__)
CORS(app)

Base.metadata.create_all(bind=engine)

from routes.scan import scan_bp
from routes.images import images_bp
from routes.export import export_bp
from routes.pending import pending_bp

app.register_blueprint(scan_bp, url_prefix='/api')
app.register_blueprint(images_bp, url_prefix='/api')
app.register_blueprint(export_bp, url_prefix='/api')
app.register_blueprint(pending_bp, url_prefix='/api')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
```

- [ ] **Step 5: Verify app starts (will fail on missing models — expected)**

Run: `cd backend && python app.py`
Expected: ImportError about `models` — confirms config works, models is next

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/config.py backend/app.py
git commit -m "feat: add backend scaffolding with Flask, config, and entry point"
```

---

### Task 2: Database models — SQLAlchemy models for all 4 tables

**Files:**
- Create: `backend/models.py`

- [ ] **Step 1: Create models.py with all models**

```python
import hashlib, os, datetime
from sqlalchemy import create_engine, Column, Integer, Text, Boolean, UniqueConstraint, Index, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from config import DB_PATH

engine = create_engine(f' sqlite:///{DB_PATH}', connect_args={'check_same_thread': False})
session = Session(engine)

class Base(DeclarativeBase):
    pass

class ScanRoot(Base):
    __tablename__ = 'scan_root'
    id = Column(Integer, primary_key=True)
    path = Column(Text, nullable=False)
    recursive = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True)

class Image(Base):
    __tablename__ = 'image'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, nullable=False, index=True)
    image_type = Column(Text, nullable=False, default='main')
    sequence = Column(Integer, default=0)
    filename = Column(Text, nullable=False)
    ext = Column(Text, nullable=False)
    file_path = Column(Text, unique=True, nullable=False)
    file_size = Column(Integer, default=0)
    md5_hash = Column(Text, default='')
    folder_path = Column(Text, default='')
    folder_mtime = Column(Text, default='')
    scan_root_id = Column(Integer, ForeignKey('scan_root.id'), nullable=False)
    confirmed = Column(Boolean, default=True)
    status = Column(Text, default='active')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())
    updated_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        Index('idx_barcode_type', 'barcode', 'image_type'),
        Index('idx_md5', 'md5_hash'),
        Index('idx_folder_mtime', 'folder_mtime'),
    )

class ImageVersion(Base):
    __tablename__ = 'image_version'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, nullable=False, index=True)
    version_label = Column(Text, nullable=False)
    folder_mtime = Column(Text, default='')
    content_hash = Column(Text, nullable=False)
    is_latest = Column(Boolean, default=False)
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        UniqueConstraint('barcode', 'content_hash', name='uq_barcode_content'),
    )

class ExportTask(Base):
    __tablename__ = 'export_task'
    id = Column(Integer, primary_key=True)
    status = Column(Text, default='pending')
    zip_path = Column(Text, default='')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())
```

- [ ] **Step 2: Verify models import correctly**

Run: `cd backend && python -c "from models import Base, engine, ScanRoot, Image, ImageVersion, ExportTask; print('All models OK')"`
Expected: `All models OK`

- [ ] **Step 3: Verify app.py starts and creates DB**

Run: `cd backend && python app.py`
Expected: Flask starts on port 5000, `backend/data/images.db` is created

- [ ] **Step 4: Commit**

```bash
git add backend/models.py
git commit -m "feat: add SQLAlchemy models for scan_root, image, image_version, export_task"
```

---

### Task 3: Scanner — filename parsing with regex (strict + fuzzy)

**Files:**
- Create: `backend/scanner.py`

- [ ] **Step 1: Create scanner.py with parsing functions**

```python
import re, os, hashlib, datetime
from models import session, Image, ScanRoot

STRICT_RE = re.compile(
    r'^(\d+)_(主图|详情图)_(\d+)\.(jpg|jpeg|png|gif|webp)$', re.IGNORECASE
)
FUZZY_RE = re.compile(
    r'^(\d+)_(\d+)\.(jpg|jpeg|png|gif|webp)$', re.IGNORECASE
)

TYPE_MAP = {'主图': 'main', '详情图': 'detail'}

def parse_filename(filename, allow_fuzzy=False):
    """Parse a filename. Returns dict with barcode, image_type, sequence, ext, match_type
    or None if no match."""
    m = STRICT_RE.match(filename)
    if m:
        return {
            'barcode': m.group(1),
            'image_type': TYPE_MAP[m.group(2)],
            'sequence': int(m.group(3)),
            'ext': m.group(4).lower(),
            'match_type': 'strict',
            'confirmed': True,
        }
    if allow_fuzzy:
        m = FUZZY_RE.match(filename)
        if m:
            return {
                'barcode': m.group(1),
                'image_type': '',
                'sequence': int(m.group(2)),
                'ext': m.group(3).lower(),
                'match_type': 'fuzzy',
                'confirmed': False,
            }
    return None
```

- [ ] **Step 2: Verify parsing with a quick test script**

Run: `cd backend && python -c "
from scanner import parse_filename
# strict match
r = parse_filename('6901234567890_主图_1.jpg')
print('Strict:', r)
# fuzzy match
r2 = parse_filename('6901234567890_2.png', allow_fuzzy=True)
print('Fuzzy:', r2)
# no match
r3 = parse_filename('random_file.jpg')
print('No match:', r3)
# fuzzy disabled
r4 = parse_filename('6901234567890_2.png', allow_fuzzy=False)
print('Fuzzy disabled:', r4)
"`
Expected: strict returns dict with image_type='main', fuzzy returns dict with confirmed=False, no match returns None, fuzzy disabled returns None

- [ ] **Step 3: Commit**

```bash
git add backend/scanner.py
git commit -m "feat: add filename parser with strict and fuzzy regex matching"
```

---

### Task 4: Scanner — folder traversal, MD5 hashing, and index building

**Files:**
- Modify: `backend/scanner.py` — append scan functions

- [ ] **Step 1: Append folder scanning and indexing functions to scanner.py**

```python
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

def get_folder_mtime(folder_path):
    """Get ISO8601 mtime for a folder."""
    try:
        return datetime.datetime.fromtimestamp(
            os.path.getmtime(folder_path)
        ).isoformat()
    except OSError:
        return ''

def compute_md5(filepath):
    """Compute MD5 hash of a file."""
    h = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def scan_root(root_id, allow_fuzzy=False):
    """Scan a single scan root: walk files, parse names, index new ones.
    Returns dict with counts: {added, skipped, broken_cleaned}."""
    root = session.get(ScanRoot, root_id)
    if not root:
        return {'error': 'Scan root not found'}

    added = 0
    skipped = 0
    broken_cleaned = 0

    # Clean up broken records for this root
    broken = session.query(Image).filter(
        Image.scan_root_id == root_id, Image.status == 'broken'
    ).all()
    for img in broken:
        session.delete(img)
    broken_cleaned = len(broken)
    session.commit()

    indexed_paths = {
        img.file_path for img in session.query(Image.file_path).filter(
            Image.scan_root_id == root_id
        ).all()
    }

    walk = os.walk if root.recursive else lambda p: [(p, [], os.listdir(p))]

    for dirpath, _, filenames in walk(root.path):
        folder_mtime = get_folder_mtime(dirpath)
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in IMAGE_EXTS:
                continue
            full_path = os.path.normpath(os.path.join(dirpath, fname))
            if full_path in indexed_paths:
                skipped += 1
                continue
            parsed = parse_filename(fname, allow_fuzzy)
            if not parsed:
                continue
            try:
                fsize = os.path.getsize(full_path)
                md5 = compute_md5(full_path)
            except OSError:
                continue
            img = Image(
                barcode=parsed['barcode'],
                image_type=parsed['image_type'],
                sequence=parsed['sequence'],
                filename=fname,
                ext=parsed['ext'],
                file_path=full_path,
                file_size=fsize,
                md5_hash=md5,
                folder_path=dirpath,
                folder_mtime=folder_mtime,
                scan_root_id=root_id,
                confirmed=parsed['confirmed'],
            )
            session.add(img)
            added += 1

    session.commit()
    return {'added': added, 'skipped': skipped, 'broken_cleaned': broken_cleaned}
```

- [ ] **Step 2: Verify scan logic parses correctly**

Run: `cd backend && python -c "
from scanner import scan_root, parse_filename, get_folder_mtime, compute_md5
print('All scan functions importable')
"`  
Expected: `All scan functions importable`

- [ ] **Step 3: Commit**

```bash
git add backend/scanner.py
git commit -m "feat: add folder traversal, MD5 hashing, and incremental indexing"
```

---

### Task 5: Versioning — version computation and merging by content hash

**Files:**
- Create: `backend/versioning.py`

- [ ] **Step 1: Create versioning.py**

```python
import hashlib, json
from models import session, Image, ImageVersion

def compute_content_hash(images):
    """Compute a deterministic hash from a set of (filename, md5_hash) pairs."""
    pairs = sorted((img.filename, img.md5_hash) for img in images)
    payload = json.dumps(pairs, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()

def update_versions_for_barcode(barcode):
    """Rebuild version records for a single barcode.
    Groups images by unique folder_mtime values, sorts descending by mtime,
    assigns v1 (oldest) through vN (newest), merges duplicate content_hashes."""
    images = session.query(Image).filter(
        Image.barcode == barcode, Image.confirmed == True, Image.status == 'active'
    ).all()

    if not images:
        return

    # Group by folder_mtime
    by_mtime = {}
    for img in images:
        by_mtime.setdefault(img.folder_mtime, []).append(img)

    sorted_mtimes = sorted(by_mtime.keys(), reverse=True)

    # Build version list: (mtime, content_hash, images)
    versions = []
    seen_hashes = set()
    for mtime in sorted_mtimes:
        imgs = by_mtime[mtime]
        ch = compute_content_hash(imgs)
        if ch in seen_hashes:
            continue
        seen_hashes.add(ch)
        versions.append((mtime, ch, imgs))

    # Delete old versions for this barcode
    session.query(ImageVersion).filter(ImageVersion.barcode == barcode).delete()

    # Create new versions: v1=oldest (last in list), vN=newest (first in list)
    total = len(versions)
    for i, (mtime, ch, imgs) in enumerate(versions):
        version_num = total - i
        is_latest = (i == 0)
        v = ImageVersion(
            barcode=barcode,
            version_label=f'v{version_num}',
            folder_mtime=mtime,
            content_hash=ch,
            is_latest=is_latest,
        )
        session.add(v)
    session.commit()

def update_all_versions():
    """Run version update for all barcodes in the database."""
    barcodes = session.query(Image.barcode).filter(
        Image.confirmed == True, Image.status == 'active'
    ).distinct().all()
    for (bc,) in barcodes:
        update_versions_for_barcode(bc)
```

- [ ] **Step 2: Verify versioning imports**

Run: `cd backend && python -c "from versioning import compute_content_hash, update_versions_for_barcode, update_all_versions; print('Versioning OK')"`  
Expected: `Versioning OK`

- [ ] **Step 3: Commit**

```bash
git add backend/versioning.py
git commit -m "feat: add version management with content-hash dedup and mtime-based ordering"
```

---

### Task 6: Thumbnail generation with Pillow

**Files:**
- Create: `backend/thumbnail.py`

- [ ] **Step 1: Create thumbnail.py**

```python
import os
from PIL import Image as PILImage
from config import THUMBNAIL_DIR, THUMBNAIL_SIZE, THUMBNAIL_QUALITY

def get_thumbnail_path(image_id):
    return os.path.join(THUMBNAIL_DIR, f'{image_id}.jpg')

def thumbnail_exists(image_id):
    return os.path.exists(get_thumbnail_path(image_id))

def generate_thumbnail(image_id, source_path):
    """Generate a 200x200 thumbnail, maintaining aspect ratio, white background."""
    thumb_path = get_thumbnail_path(image_id)
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)

    img = PILImage.open(source_path)
    img = img.convert('RGBA')
    # Scale to fit within THUMBNAIL_SIZE keeping aspect ratio
    img.thumbnail(THUMBNAIL_SIZE, PILImage.LANCZOS)
    # Paste onto white background
    bg = PILImage.new('RGBA', THUMBNAIL_SIZE, (255, 255, 255, 255))
    offset = (
        (THUMBNAIL_SIZE[0] - img.width) // 2,
        (THUMBNAIL_SIZE[1] - img.height) // 2,
    )
    bg.paste(img, offset, img if img.mode == 'RGBA' else None)
    bg = bg.convert('RGB')
    bg.save(thumb_path, 'JPEG', quality=THUMBNAIL_QUALITY)
    return thumb_path
```

- [ ] **Step 2: Verify thumbnail imports**

Run: `cd backend && python -c "from thumbnail import generate_thumbnail, get_thumbnail_path, thumbnail_exists; print('Thumbnail OK')"`  
Expected: `Thumbnail OK`

- [ ] **Step 3: Commit**

```bash
git add backend/thumbnail.py
git commit -m "feat: add thumbnail generation with Pillow (200x200, white bg, aspect-ratio preserved)"
```

---

### Task 7: Scan routes — scan-root CRUD and scan trigger

**Files:**
- Create: `backend/routes/__init__.py` (empty)
- Create: `backend/routes/scan.py`

- [ ] **Step 1: Create empty __init__.py**

```bash
touch backend/routes/__init__.py
```

- [ ] **Step 2: Create routes/scan.py**

```python
import os
from flask import Blueprint, request, jsonify
from models import session, ScanRoot, Image
from scanner import scan_root
from versioning import update_all_versions

scan_bp = Blueprint('scan', __name__)

@scan_bp.route('/scan-roots', methods=['GET'])
def list_scan_roots():
    roots = session.query(ScanRoot).all()
    return jsonify([{
        'id': r.id, 'path': r.path, 'recursive': r.recursive, 'enabled': r.enabled
    } for r in roots])

@scan_bp.route('/scan-roots', methods=['POST'])
def add_scan_root():
    data = request.json
    if not data or 'path' not in data:
        return jsonify({'error': 'path is required'}), 400
    if not os.path.isdir(data['path']):
        return jsonify({'error': 'path does not exist'}), 400
    root = ScanRoot(
        path=data['path'],
        recursive=data.get('recursive', True),
        enabled=True,
    )
    session.add(root)
    session.commit()
    return jsonify({
        'id': root.id, 'path': root.path,
        'recursive': root.recursive, 'enabled': root.enabled
    }), 201

@scan_bp.route('/scan-roots/<int:root_id>', methods=['DELETE'])
def delete_scan_root(root_id):
    root = session.get(ScanRoot, root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    session.query(Image).filter(Image.scan_root_id == root_id).delete()
    session.delete(root)
    session.commit()
    return jsonify({'message': 'deleted'})

@scan_bp.route('/scan', methods=['POST'])
def trigger_scan():
    data = request.get_json(silent=True) or {}
    allow_fuzzy = data.get('allow_fuzzy', False)
    root_id = data.get('root_id')
    if root_id:
        result = scan_root(root_id, allow_fuzzy=allow_fuzzy)
        update_all_versions()
        return jsonify(result)
    roots = session.query(ScanRoot).filter(ScanRoot.enabled == True).all()
    total = {'added': 0, 'skipped': 0, 'broken_cleaned': 0}
    for r in roots:
        res = scan_root(r.id, allow_fuzzy=allow_fuzzy)
        for k in total:
            total[k] += res.get(k, 0)
    update_all_versions()
    return jsonify(total)
```

- [ ] **Step 3: Verify scan routes import**

Run: `cd backend && python -c "from routes.scan import scan_bp; print('Scan routes OK')"`  
Expected: `Scan routes OK`

- [ ] **Step 4: Verify app starts**

Run: `cd backend && python app.py`  
Expected: Flask starts on port 5000 with no import errors

- [ ] **Step 5: Commit**

```bash
git add backend/routes/__init__.py backend/routes/scan.py
git commit -m "feat: add scan routes — scan-root CRUD and scan trigger endpoint"
```

---

### Task 8: Image routes — CRUD, file serving, thumbnail serving, batch operations

**Files:**
- Create: `backend/routes/images.py`

- [ ] **Step 1: Create routes/images.py**

```python
import os
from flask import Blueprint, request, jsonify, send_file
from models import session, Image, ImageVersion
from thumbnail import thumbnail_exists, generate_thumbnail, get_thumbnail_path

images_bp = Blueprint('images', __name__)

@images_bp.route('/images', methods=['GET'])
def list_images():
    q = session.query(Image)
    barcode = request.args.get('barcode')
    if barcode:
        q = q.filter(Image.barcode.like(f'%{barcode}%'))
    image_type = request.args.get('image_type')
    if image_type:
        q = q.filter(Image.image_type == image_type)
    scan_root_id = request.args.get('scan_root_id')
    if scan_root_id:
        q = q.filter(Image.scan_root_id == int(scan_root_id))
    confirmed = request.args.get('confirmed')
    if confirmed is not None:
        q = q.filter(Image.confirmed == (confirmed == 'true'))
    sort = request.args.get('sort', 'created_at')
    col = getattr(Image, sort, Image.created_at)
    order = col.desc() if request.args.get('order') == 'desc' else col.asc()
    q = q.order_by(order)
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return jsonify({
        'items': [_image_to_dict(img) for img in items],
        'total': total, 'page': page, 'page_size': page_size,
    })

@images_bp.route('/images/<int:img_id>', methods=['GET'])
def get_image(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    versions = session.query(ImageVersion).filter(
        ImageVersion.barcode == img.barcode
    ).order_by(ImageVersion.version_label.desc()).all()
    return jsonify({
        'image': _image_to_dict(img),
        'versions': [{
            'id': v.id, 'barcode': v.barcode, 'version_label': v.version_label,
            'folder_mtime': v.folder_mtime, 'content_hash': v.content_hash,
            'is_latest': v.is_latest, 'created_at': v.created_at,
        } for v in versions],
    })

@images_bp.route('/images/<int:img_id>', methods=['PUT'])
def update_image(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    data = request.json
    if 'image_type' in data:
        img.image_type = data['image_type']
    if 'confirmed' in data:
        img.confirmed = data['confirmed']
    session.commit()
    return jsonify(_image_to_dict(img))

@images_bp.route('/images/<int:img_id>', methods=['DELETE'])
def delete_image(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    session.delete(img)
    session.commit()
    return jsonify({'message': 'deleted'})

@images_bp.route('/images/<int:img_id>/file')
def serve_file(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    if not os.path.exists(img.file_path):
        img.status = 'broken'
        session.commit()
        return jsonify({'error': 'file not found on disk'}), 404
    return send_file(img.file_path)

@images_bp.route('/thumbnails/<int:img_id>')
def serve_thumbnail(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    if not os.path.exists(img.file_path):
        img.status = 'broken'
        session.commit()
        return jsonify({'error': 'source file not found'}), 404
    if not thumbnail_exists(img_id):
        generate_thumbnail(img_id, img.file_path)
    return send_file(get_thumbnail_path(img_id), mimetype='image/jpeg')

@images_bp.route('/images/batch-delete', methods=['POST'])
def batch_delete():
    data = request.json
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'ids required'}), 400
    session.query(Image).filter(Image.id.in_(ids)).delete(synchronize_session='fetch')
    session.commit()
    return jsonify({'message': f'deleted {len(ids)} images'})

@images_bp.route('/images/batch-export', methods=['POST'])
def batch_export():
    """Export selected images as ZIP. Returns export_task id."""
    from models import ExportTask
    from config import UPLOAD_DIR
    import zipfile, datetime
    data = request.json
    ids = data.get('ids', [])
    image_type = data.get('image_type', '')
    if not ids:
        return jsonify({'error': 'ids required'}), 400
    q = session.query(Image).filter(Image.id.in_(ids))
    if image_type:
        q = q.filter(Image.image_type == image_type)
    imgs = q.all()
    task = ExportTask(status='processing')
    session.add(task)
    session.commit()
    zip_name = f'batch_export_{task.id}.zip'
    zip_path = os.path.join(UPLOAD_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for img in imgs:
            if os.path.exists(img.file_path):
                arcname = f"{img.barcode}/{img.filename}"
                zf.write(img.file_path, arcname)
    task.status = 'done'
    task.zip_path = zip_path
    session.commit()
    return jsonify({'task_id': task.id})

def _image_to_dict(img):
    return {
        'id': img.id, 'barcode': img.barcode, 'image_type': img.image_type,
        'sequence': img.sequence, 'filename': img.filename, 'ext': img.ext,
        'file_path': img.file_path, 'file_size': img.file_size,
        'md5_hash': img.md5_hash, 'folder_path': img.folder_path,
        'folder_mtime': img.folder_mtime, 'scan_root_id': img.scan_root_id,
        'confirmed': img.confirmed, 'status': img.status,
        'created_at': img.created_at, 'updated_at': img.updated_at,
    }
```

- [ ] **Step 2: Verify image routes import**

Run: `cd backend && python -c "from routes.images import images_bp; print('Image routes OK')"`  
Expected: `Image routes OK`

- [ ] **Step 3: Commit**

```bash
git add backend/routes/images.py
git commit -m "feat: add image routes — CRUD, file/thumbnail serving, batch delete/export"
```

---

### Task 9: Pending routes — list, confirm, ignore

**Files:**
- Create: `backend/routes/pending.py`

- [ ] **Step 1: Create routes/pending.py**

```python
from flask import Blueprint, request, jsonify
from models import session, Image
from versioning import update_all_versions

pending_bp = Blueprint('pending', __name__)

@pending_bp.route('/pending', methods=['GET'])
def list_pending():
    imgs = session.query(Image).filter(
        Image.confirmed == False, Image.status == 'active'
    ).order_by(Image.barcode, Image.sequence).all()
    return jsonify([_pending_to_dict(img) for img in imgs])

@pending_bp.route('/pending/confirm', methods=['POST'])
def confirm_pending():
    data = request.json  # [{id, image_type}, ...]
    if not data:
        return jsonify({'error': 'array of {id, image_type} required'}), 400
    for item in data:
        img = session.get(Image, item['id'])
        if img and not img.confirmed:
            img.image_type = item.get('image_type', 'main')
            img.confirmed = True
    session.commit()
    update_all_versions()
    return jsonify({'message': f'confirmed {len(data)} images'})

@pending_bp.route('/pending/<int:img_id>', methods=['DELETE'])
def ignore_pending(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    session.delete(img)
    session.commit()
    return jsonify({'message': 'ignored'})

def _pending_to_dict(img):
    return {
        'id': img.id, 'barcode': img.barcode, 'sequence': img.sequence,
        'filename': img.filename, 'ext': img.ext,
        'file_path': img.file_path, 'file_size': img.file_size,
        'folder_path': img.folder_path, 'scan_root_id': img.scan_root_id,
        'created_at': img.created_at,
    }
```

- [ ] **Step 2: Verify pending routes import**

Run: `cd backend && python -c "from routes.pending import pending_bp; print('Pending routes OK')"`  
Expected: `Pending routes OK`

- [ ] **Step 3: Commit**

```bash
git add backend/routes/pending.py
git commit -m "feat: add pending routes — list, batch confirm, ignore fuzzy matches"
```

---

### Task 10: Export routes — Excel upload, column parsing, ZIP generation, download

**Files:**
- Create: `backend/routes/export.py`

- [ ] **Step 1: Create routes/export.py**

```python
import os, uuid
from flask import Blueprint, request, jsonify, send_file
from openpyxl import load_workbook
from models import session, Image, ExportTask
from config import UPLOAD_DIR

export_bp = Blueprint('export', __name__)

@export_bp.route('/export/excel', methods=['POST'])
def upload_excel():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'file required'}), 400
    upload_id = uuid.uuid4().hex[:12]
    upload_path = os.path.join(UPLOAD_DIR, f'{upload_id}.xlsx')
    file.save(upload_path)
    wb = load_workbook(upload_path, read_only=True)
    ws = wb.active
    headers = [str(cell.value) for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    wb.close()
    column_names = [f'{chr(65+i)}-{h}' for i, h in enumerate(headers)]
    return jsonify({'columns': column_names, 'upload_id': upload_id})

@export_bp.route('/export/zip', methods=['POST'])
def generate_zip():
    import zipfile
    data = request.json
    barcode_col = data.get('barcode_column', '')
    image_type = data.get('image_type', '')
    upload_id = data.get('upload_id', '')
    selected = data.get('selected_barcodes')

    # Parse barcode column letter
    col_letter = barcode_col.split('-')[0] if '-' in barcode_col else 'A'
    col_idx = ord(col_letter.upper()) - ord('A')

    # Read barcodes from Excel
    upload_path = os.path.join(UPLOAD_DIR, f'{upload_id}.xlsx')
    wb = load_workbook(upload_path, read_only=True)
    ws = wb.active
    barcodes = []
    for row in ws.iter_rows(min_row=2):
        val = str(row[col_idx].value).strip() if row[col_idx].value else ''
        if val:
            barcodes.append(val)
    wb.close()

    if selected:
        barcodes = [b for b in barcodes if b in selected]

    # Find matching images
    q = session.query(Image).filter(Image.barcode.in_(barcodes), Image.confirmed == True)
    if image_type and image_type != 'all':
        q = q.filter(Image.image_type == image_type)
    imgs = q.all()

    task = ExportTask(status='processing')
    session.add(task)
    session.commit()

    zip_name = f'export_{task.id}.zip'
    zip_path = os.path.join(UPLOAD_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for img in imgs:
            if os.path.exists(img.file_path):
                zf.write(img.file_path, f"{img.barcode}/{img.filename}")

    task.status = 'done'
    task.zip_path = zip_path
    session.commit()
    return jsonify({'task_id': task.id})

@export_bp.route('/export/download/<int:task_id>')
def download_zip(task_id):
    task = session.get(ExportTask, task_id)
    if not task or task.status != 'done':
        return jsonify({'error': 'not ready'}), 404
    return send_file(task.zip_path, as_attachment=True, download_name=f'export_{task_id}.zip')
```

- [ ] **Step 2: Verify export routes import**

Run: `cd backend && python -c "from routes.export import export_bp; print('Export routes OK')"`
Expected: `Export routes OK`

- [ ] **Step 3: Commit**

```bash
git add backend/routes/export.py
git commit -m "feat: add export routes — Excel upload, column parsing, ZIP generation, download"
```

---

### Task 11: Frontend scaffolding — Vite + React + TypeScript + Ant Design

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "image-management-frontend",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "antd": "^5.22.0",
    "@ant-design/icons": "^5.5.0",
    "axios": "^1.7.9",
    "dayjs": "^1.11.13"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.6.3",
    "vite": "^5.4.11"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:5000',
    },
  },
})
```

- [ ] **Step 3: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create tsconfig.node.json**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 5: Create index.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>商品图片管理系统</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Create src/main.tsx**

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 7: Install dependencies and verify startup**

Run: `cd frontend && npm install && npm run dev`
Expected: Vite dev server starts on port 3000

- [ ] **Step 8: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold frontend with Vite, React 18, TypeScript, Ant Design"
```

---

### Task 12: API service layer — axios wrapper with all endpoint functions

**Files:**
- Create: `frontend/src/services/api.ts`

- [ ] **Step 1: Create src/services/api.ts**

```typescript
import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

export interface ScanRoot {
  id: number; path: string; recursive: boolean; enabled: boolean;
}

export interface ImageRec {
  id: number; barcode: string; image_type: string; sequence: number;
  filename: string; ext: string; file_path: string; file_size: number;
  md5_hash: string; folder_path: string; folder_mtime: string;
  scan_root_id: number; confirmed: boolean; status: string;
  created_at: string; updated_at: string;
}

export interface ImageVersion {
  id: number; barcode: string; version_label: string;
  folder_mtime: string; content_hash: string; is_latest: boolean;
  created_at: string;
}

export interface Paginated<T> {
  items: T[]; total: number; page: number; page_size: number;
}

export interface ImageListParams {
  barcode?: string; image_type?: string; scan_root_id?: number;
  page?: number; page_size?: number; sort?: string; order?: string;
}

export const scanRootApi = {
  list: () => api.get<ScanRoot[]>('/scan-roots').then(r => r.data),
  create: (data: { path: string; recursive?: boolean }) =>
    api.post<ScanRoot>('/scan-roots', data).then(r => r.data),
  delete: (id: number) => api.delete(`/scan-roots/${id}`).then(r => r.data),
};

export const scanApi = {
  trigger: (data?: { root_id?: number; allow_fuzzy?: boolean }) =>
    api.post('/scan', data || {}).then(r => r.data),
};

export const imageApi = {
  list: (params: ImageListParams) =>
    api.get<Paginated<ImageRec>>('/images', { params }).then(r => r.data),
  get: (id: number) =>
    api.get<{ image: ImageRec; versions: ImageVersion[] }>(`/images/${id}`).then(r => r.data),
  update: (id: number, data: Partial<ImageRec>) =>
    api.put<ImageRec>(`/images/${id}`, data).then(r => r.data),
  delete: (id: number) => api.delete(`/images/${id}`),
  thumbnailUrl: (id: number) => `/api/thumbnails/${id}`,
  fileUrl: (id: number) => `/api/images/${id}/file`,
  batchDelete: (ids: number[]) =>
    api.post('/images/batch-delete', { ids }).then(r => r.data),
  batchExport: (ids: number[], image_type?: string) =>
    api.post<{ task_id: number }>('/images/batch-export', { ids, image_type }).then(r => r.data),
};

export const pendingApi = {
  list: () => api.get<ImageRec[]>('/pending').then(r => r.data),
  confirm: (items: { id: number; image_type: string }[]) =>
    api.post('/pending/confirm', items).then(r => r.data),
  ignore: (id: number) => api.delete(`/pending/${id}`),
};

export const exportApi = {
  uploadExcel: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return api.post<{ columns: string[]; upload_id: string }>('/export/excel', fd).then(r => r.data);
  },
  generateZip: (data: { barcode_column: string; image_type: string; upload_id: string; selected_barcodes?: string[] }) =>
    api.post<{ task_id: number }>('/export/zip', data).then(r => r.data),
  downloadUrl: (taskId: number) => `/api/export/download/${taskId}`,
};
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (may have unused variable warnings)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api.ts
git commit -m "feat: add API service layer with axios wrapper and typed endpoints"
```

---

### Task 13: SearchBar component — top search bar with action buttons

**Files:**
- Create: `frontend/src/components/SearchBar.tsx`

- [ ] **Step 1: Create SearchBar.tsx**

```tsx
import React from 'react';
import { Input, Button, Space } from 'antd';
import { SearchOutlined, FolderAddOutlined, ExportOutlined, ScanOutlined, WarningOutlined } from '@ant-design/icons';

interface Props {
  onSearch: (barcode: string) => void;
  onAddScanRoot: () => void;
  onExportExcel: () => void;
  onTriggerScan: () => void;
  onOpenPending: () => void;
  pendingCount?: number;
  loading?: boolean;
}

const SearchBar: React.FC<Props> = ({ onSearch, onAddScanRoot, onExportExcel, onTriggerScan, onOpenPending, pendingCount, loading }) => {
  return (
    <Space wrap style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
      <Space>
        <Input.Search
          placeholder="输入条码搜索..."
          allowClear
          onSearch={onSearch}
          style={{ width: 280 }}
          prefix={<SearchOutlined />}
        />
      </Space>
      <Space>
        <Button icon={<FolderAddOutlined />} onClick={onAddScanRoot}>添加扫描目录</Button>
        <Button icon={<ScanOutlined />} onClick={onTriggerScan} loading={loading}>扫描</Button>
        <Button icon={<ExportOutlined />} onClick={onExportExcel}>Excel 导出</Button>
        <Button icon={<WarningOutlined />} onClick={onOpenPending} danger={!!pendingCount}>
          待确认 {pendingCount ? `(${pendingCount})` : ''}
        </Button>
      </Space>
    </Space>
  );
};

export default SearchBar;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/SearchBar.tsx
git commit -m "feat: add SearchBar component with scan, export, pending buttons"
```

---

### Task 14: ImageTable component — left panel Ant Design table with row selection

**Files:**
- Create: `frontend/src/components/ImageTable.tsx`

- [ ] **Step 1: Create ImageTable.tsx**

```tsx
import React from 'react';
import { Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ImageRec } from '../services/api';

interface Props {
  images: ImageRec[];
  loading: boolean;
  total: number;
  page: number;
  pageSize: number;
  selectedRowKeys: React.Key[];
  onSelectionChange: (keys: React.Key[], rows: ImageRec[]) => void;
  onRowClick: (barcode: string) => void;
  onPageChange: (page: number, pageSize: number) => void;
}

const columns: ColumnsType<ImageRec> = [
  { title: '条码', dataIndex: 'barcode', width: 150, sorter: true },
  { title: '类型', dataIndex: 'image_type', width: 80, render: (t: string) => (
    <Tag color={t === 'main' ? 'blue' : 'green'}>{t === 'main' ? '主图' : '详情图'}</Tag>
  )},
  { title: '序号', dataIndex: 'sequence', width: 60 },
  { title: '文件名', dataIndex: 'filename', ellipsis: true },
  { title: '文件夹', dataIndex: 'folder_path', ellipsis: true, width: 200 },
  { title: '大小', dataIndex: 'file_size', width: 80, render: (s: number) => `${(s / 1024).toFixed(0)} KB` },
  { title: '状态', dataIndex: 'confirmed', width: 70, render: (c: boolean) => c ? null : <Tag color="orange">待确认</Tag> },
];

const ImageTable: React.FC<Props> = ({
  images, loading, total, page, pageSize,
  selectedRowKeys, onSelectionChange, onRowClick, onPageChange,
}) => {
  return (
    <Table<ImageRec>
      rowKey="id"
      columns={columns}
      dataSource={images}
      loading={loading}
      size="small"
      rowSelection={{
        selectedRowKeys,
        onChange: onSelectionChange,
      }}
      onRow={(record) => ({
        onClick: () => onRowClick(record.barcode),
        style: { cursor: 'pointer' },
      })}
      pagination={{
        current: page, pageSize, total, showSizeChanger: true,
        onChange: onPageChange, showTotal: (t) => `共 ${t} 条`,
      }}
      scroll={{ y: 'calc(100vh - 280px)' }}
    />
  );
};

export default ImageTable;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ImageTable.tsx
git commit -m "feat: add ImageTable with row selection, pagination, and column rendering"
```

---

### Task 15: ImageCardDetail component — right panel with thumbnails, versions, and card-level selection

**Files:**
- Create: `frontend/src/components/ImageCardDetail.tsx`

- [ ] **Step 1: Create ImageCardDetail.tsx**

```tsx
import React, { useState, useEffect } from 'react';
import { Card, Checkbox, Collapse, Tag, Image, Spin, Empty, Typography, Space, Button } from 'antd';
import { ImageRec, ImageVersion, imageApi } from '../services/api';

const { Text } = Typography;

interface Props {
  barcode: string | null;
  selectedMainIds: Set<number>;
  selectedDetailIds: Set<number>;
  onMainSelectionChange: (ids: Set<number>) => void;
  onDetailSelectionChange: (ids: Set<number>) => void;
}

const ImageCardDetail: React.FC<Props> = ({
  barcode, selectedMainIds, selectedDetailIds,
  onMainSelectionChange, onDetailSelectionChange,
}) => {
  const [loading, setLoading] = useState(false);
  const [images, setImages] = useState<ImageRec[]>([]);
  const [versions, setVersions] = useState<ImageVersion[]>([]);
  const [activeVersion, setActiveVersion] = useState<string | null>(null);

  useEffect(() => {
    if (!barcode) return;
    setLoading(true);
    imageApi.list({ barcode, page_size: 500 }).then(res => {
      setImages(res.items);
      // Fetch versions via any image in this barcode
      if (res.items.length > 0) {
        imageApi.get(res.items[0].id).then(detail => {
          setVersions(detail.versions);
          setActiveVersion(detail.versions.find(v => v.is_latest)?.version_label || null);
        });
      }
    }).finally(() => setLoading(false));
  }, [barcode]);

  const filteredImages = activeVersion
    ? images.filter(img => img.folder_mtime === versions.find(v => v.version_label === activeVersion)?.folder_mtime)
    : images;

  const mainImages = filteredImages.filter(i => i.image_type === 'main');
  const detailImages = filteredImages.filter(i => i.image_type === 'detail');

  const toggleCheck = (id: number, type: 'main' | 'detail') => {
    const selected = type === 'main' ? new Set(selectedMainIds) : new Set(selectedDetailIds);
    if (selected.has(id)) selected.delete(id); else selected.add(id);
    type === 'main' ? onMainSelectionChange(selected) : onDetailSelectionChange(selected);
  };

  const toggleAll = (imgs: ImageRec[], type: 'main' | 'detail') => {
    const currentSet = type === 'main' ? selectedMainIds : selectedDetailIds;
    const allIds = new Set(imgs.map(i => i.id));
    const allSelected = imgs.every(i => currentSet.has(i.id));
    type === 'main' ? onMainSelectionChange(allSelected ? new Set() : allIds) : onDetailSelectionChange(allSelected ? new Set() : allIds);
  };

  if (!barcode) return <Empty description="点击表格行查看图片详情" />;

  return (
    <Spin spinning={loading}>
      <Card size="small" title={<Text strong>条码: {barcode}</Text>}>
        {versions.length > 0 && (
          <Collapse size="small" style={{ marginBottom: 12 }}
            items={[{
              key: 'versions', label: `版本历史 (${versions.length})`,
              children: versions.map(v => (
                <Tag key={v.id} color={v.is_latest ? 'blue' : 'default'}
                  style={{ cursor: 'pointer', marginBottom: 4 }}
                  onClick={() => setActiveVersion(v.version_label)}>
                  {v.version_label} {v.is_latest ? '(最新)' : ''}
                </Tag>
              )),
            }]}
          />
        )}

        <div style={{ marginBottom: 12 }}>
          <Space style={{ marginBottom: 8 }}>
            <Text strong>主图 ({mainImages.length})</Text>
            <Button size="small" onClick={() => toggleAll(mainImages, 'main')}>全选主图</Button>
          </Space>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {mainImages.map(img => (
              <div key={img.id} style={{ position: 'relative' }}>
                <Image src={imageApi.thumbnailUrl(img.id)} width={100} height={100}
                  style={{ objectFit: 'cover', borderRadius: 4 }} preview={{ src: imageApi.fileUrl(img.id) }} />
                <Checkbox checked={selectedMainIds.has(img.id)}
                  onChange={() => toggleCheck(img.id, 'main')}
                  style={{ position: 'absolute', top: 2, left: 2 }} />
              </div>
            ))}
          </div>
        </div>

        <div>
          <Space style={{ marginBottom: 8 }}>
            <Text strong>详情图 ({detailImages.length})</Text>
            <Button size="small" onClick={() => toggleAll(detailImages, 'detail')}>全选详情图</Button>
          </Space>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {detailImages.map(img => (
              <div key={img.id} style={{ position: 'relative' }}>
                <Image src={imageApi.thumbnailUrl(img.id)} width={100} height={100}
                  style={{ objectFit: 'cover', borderRadius: 4 }} preview={{ src: imageApi.fileUrl(img.id) }} />
                <Checkbox checked={selectedDetailIds.has(img.id)}
                  onChange={() => toggleCheck(img.id, 'detail')}
                  style={{ position: 'absolute', top: 2, left: 2 }} />
              </div>
            ))}
          </div>
        </div>
      </Card>
    </Spin>
  );
};

export default ImageCardDetail;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ImageCardDetail.tsx
git commit -m "feat: add ImageCardDetail with thumbnail grid, version history, card-level batch selection"
```

---

### Task 16: ScanManager component — modal for managing scan root directories

**Files:**
- Create: `frontend/src/components/ScanManager.tsx`

- [ ] **Step 1: Create ScanManager.tsx**

```tsx
import React, { useState, useEffect } from 'react';
import { Modal, Table, Button, Form, Input, Switch, Space, Popconfirm, message } from 'antd';
import { PlusOutlined, DeleteOutlined, ScanOutlined } from '@ant-design/icons';
import { ScanRoot, scanRootApi, scanApi } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
  onScanComplete: () => void;
}

const ScanManager: React.FC<Props> = ({ visible, onClose, onScanComplete }) => {
  const [roots, setRoots] = useState<ScanRoot[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [path, setPath] = useState('');
  const [recursive, setRecursive] = useState(true);

  const fetchRoots = () => {
    setLoading(true);
    scanRootApi.list().then(setRoots).finally(() => setLoading(false));
  };

  useEffect(() => { if (visible) fetchRoots(); }, [visible]);

  const handleAdd = async () => {
    if (!path.trim()) return;
    await scanRootApi.create({ path: path.trim(), recursive });
    setPath(''); setShowAdd(false);
    fetchRoots();
    message.success('扫描目录已添加');
  };

  const handleDelete = async (id: number) => {
    await scanRootApi.delete(id);
    fetchRoots();
    message.success('已删除');
  };

  const handleScan = async () => {
    setScanning(true);
    await scanApi.trigger({ allow_fuzzy: true });
    setScanning(false);
    message.success('扫描完成');
    onScanComplete();
  };

  const columns = [
    { title: '路径', dataIndex: 'path', ellipsis: true },
    { title: '递归', dataIndex: 'recursive', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '启用', dataIndex: 'enabled', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '操作', width: 80, render: (_: unknown, r: ScanRoot) => (
      <Popconfirm title="确定删除此目录及其索引?" onConfirm={() => handleDelete(r.id)}>
        <Button size="small" danger icon={<DeleteOutlined />} />
      </Popconfirm>
    )},
  ];

  return (
    <Modal title="扫描目录管理" open={visible} onCancel={onClose} width={700} footer={null}>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<PlusOutlined />} onClick={() => setShowAdd(!showAdd)}>添加</Button>
        <Button icon={<ScanOutlined />} loading={scanning} onClick={handleScan}>执行扫描</Button>
      </Space>
      {showAdd && (
        <Space style={{ marginBottom: 12 }}>
          <Input placeholder="文件夹绝对路径" value={path} onChange={e => setPath(e.target.value)} style={{ width: 320 }} />
          <span>递归: <Switch checked={recursive} onChange={setRecursive} /></span>
          <Button type="primary" onClick={handleAdd}>确认</Button>
        </Space>
      )}
      <Table rowKey="id" columns={columns} dataSource={roots} loading={loading} size="small"
        pagination={false} scroll={{ y: 300 }} />
    </Modal>
  );
};

export default ScanManager;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ScanManager.tsx
git commit -m "feat: add ScanManager modal for scan root CRUD and scan triggering"
```

---

### Task 17: PendingList component — modal for reviewing fuzzy-matched images

**Files:**
- Create: `frontend/src/components/PendingList.tsx`

- [ ] **Step 1: Create PendingList.tsx**

```tsx
import React, { useState, useEffect } from 'react';
import { Modal, Table, Button, Select, Space, message } from 'antd';
import { ImageRec, pendingApi } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
  onConfirmed: () => void;
}

const PendingList: React.FC<Props> = ({ visible, onClose, onConfirmed }) => {
  const [items, setItems] = useState<ImageRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [selectedType, setSelectedType] = useState<string>('main');

  const fetchPending = () => {
    setLoading(true);
    pendingApi.list().then(setItems).finally(() => setLoading(false));
  };

  useEffect(() => { if (visible) fetchPending(); }, [visible]);

  const handleConfirm = async () => {
    if (selectedRowKeys.length === 0) return;
    const toConfirm = selectedRowKeys.map(Number).map(id => ({ id, image_type: selectedType }));
    await pendingApi.confirm(toConfirm);
    message.success(`已确认 ${selectedRowKeys.length} 条`);
    setSelectedRowKeys([]);
    fetchPending();
    onConfirmed();
  };

  const handleIgnore = async (id: number) => {
    await pendingApi.ignore(id);
    fetchPending();
  };

  const columns = [
    { title: '条码', dataIndex: 'barcode', width: 130 },
    { title: '序号', dataIndex: 'sequence', width: 60 },
    { title: '文件名', dataIndex: 'filename', ellipsis: true },
    { title: '文件夹', dataIndex: 'folder_path', ellipsis: true },
    { title: '大小', dataIndex: 'file_size', width: 80, render: (s: number) => `${(s / 1024).toFixed(0)} KB` },
    { title: '操作', width: 60, render: (_: unknown, r: ImageRec) => (
      <Button size="small" onClick={() => handleIgnore(r.id)}>忽略</Button>
    )},
  ];

  return (
    <Modal title="待确认图片" open={visible} onCancel={onClose} width={800} footer={null}>
      <Space style={{ marginBottom: 12 }}>
        <Select value={selectedType} onChange={setSelectedType} style={{ width: 100 }}
          options={[{ value: 'main', label: '主图' }, { value: 'detail', label: '详情图' }]} />
        <Button type="primary" onClick={handleConfirm} disabled={selectedRowKeys.length === 0}>
          确认选中 ({selectedRowKeys.length})
        </Button>
      </Space>
      <Table rowKey="id" columns={columns} dataSource={items} loading={loading} size="small"
        rowSelection={{ selectedRowKeys, onChange: keys => setSelectedRowKeys(keys) }}
        pagination={false} scroll={{ y: 400 }} />
    </Modal>
  );
};

export default PendingList;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PendingList.tsx
git commit -m "feat: add PendingList modal for reviewing and confirming fuzzy-matched images"
```

---

### Task 18: ExportDialog component — step-by-step Excel export modal

**Files:**
- Create: `frontend/src/components/ExportDialog.tsx`

- [ ] **Step 1: Create ExportDialog.tsx**

```tsx
import React, { useState } from 'react';
import { Modal, Upload, Button, Select, Radio, Space, message, Steps } from 'antd';
import { UploadOutlined, DownloadOutlined } from '@ant-design/icons';
import { exportApi } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
}

const ExportDialog: React.FC<Props> = ({ visible, onClose }) => {
  const [step, setStep] = useState(0);
  const [columns, setColumns] = useState<string[]>([]);
  const [uploadId, setUploadId] = useState('');
  const [barcodeColumn, setBarcodeColumn] = useState('');
  const [imageType, setImageType] = useState('all');
  const [taskId, setTaskId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const handleUpload = async (file: File) => {
    setLoading(true);
    try {
      const res = await exportApi.uploadExcel(file);
      setColumns(res.columns);
      setUploadId(res.upload_id);
      setStep(1);
    } catch {
      message.error('上传失败');
    }
    setLoading(false);
    return false;
  };

  const handleGenerate = async () => {
    if (!barcodeColumn) return;
    setLoading(true);
    try {
      const res = await exportApi.generateZip({ barcode_column: barcodeColumn, image_type: imageType, upload_id: uploadId });
      setTaskId(res.task_id);
      setStep(2);
    } catch {
      message.error('生成失败');
    }
    setLoading(false);
  };

  const handleDownload = () => {
    if (taskId) window.open(exportApi.downloadUrl(taskId), '_blank');
  };

  const reset = () => { setStep(0); setColumns([]); setUploadId(''); setBarcodeColumn(''); setTaskId(null); };

  return (
    <Modal title="Excel 批量导出" open={visible} onCancel={() => { reset(); onClose(); }} width={550} footer={null}>
      <Steps current={step} size="small" style={{ marginBottom: 24 }}
        items={[{ title: '上传 Excel' }, { title: '选择列' }, { title: '下载' }]} />

      {step === 0 && (
        <Upload accept=".xlsx" maxCount={1} beforeUpload={handleUpload} showUploadList={false}>
          <Button icon={<UploadOutlined />} loading={loading}>上传 Excel 文件</Button>
        </Upload>
      )}

      {step === 1 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <span>条码所在列：</span>
            <Select value={barcodeColumn} onChange={setBarcodeColumn} style={{ width: '100%' }}
              placeholder="选择条码所在的列"
              options={columns.map(c => ({ value: c, label: c }))} />
          </div>
          <div>
            <span>导出类型：</span>
            <Radio.Group value={imageType} onChange={e => setImageType(e.target.value)}>
              <Radio.Button value="all">全部</Radio.Button>
              <Radio.Button value="main">仅主图</Radio.Button>
              <Radio.Button value="detail">仅详情图</Radio.Button>
            </Radio.Group>
          </div>
          <Button type="primary" onClick={handleGenerate} loading={loading} disabled={!barcodeColumn}>
            生成 ZIP
          </Button>
        </Space>
      )}

      {step === 2 && (
        <Space direction="vertical" style={{ width: '100%', alignItems: 'center' }}>
          <p>ZIP 文件已生成</p>
          <Button icon={<DownloadOutlined />} type="primary" size="large" onClick={handleDownload}>
            下载 ZIP
          </Button>
        </Space>
      )}
    </Modal>
  );
};

export default ExportDialog;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ExportDialog.tsx
git commit -m "feat: add ExportDialog with 3-step wizard — upload, column select, download ZIP"
```

---

### Task 19: Home page — main layout, state management, batch operations bar

**Files:**
- Create: `frontend/src/pages/Home.tsx`

- [ ] **Step 1: Create pages/Home.tsx**

```tsx
import React, { useState, useEffect, useCallback } from 'react';
import { Layout, message, Space, Button, Typography } from 'antd';
import { DeleteOutlined, ExportOutlined } from '@ant-design/icons';
import SearchBar from '../components/SearchBar';
import ImageTable from '../components/ImageTable';
import ImageCardDetail from '../components/ImageCardDetail';
import ScanManager from '../components/ScanManager';
import PendingList from '../components/PendingList';
import ExportDialog from '../components/ExportDialog';
import { ImageRec, imageApi, pendingApi, exportApi } from '../services/api';

const { Header, Content, Footer } = Layout;
const { Text } = Typography;

const Home: React.FC = () => {
  // Data state
  const [images, setImages] = useState<ImageRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [pendingCount, setPendingCount] = useState(0);

  // Search & sort
  const [barcode, setBarcode] = useState('');
  const [sortField, setSortField] = useState('created_at');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Selection state — union of table + card selections
  const [tableSelectedKeys, setTableSelectedKeys] = useState<React.Key[]>([]);
  const [tableSelectedRows, setTableSelectedRows] = useState<ImageRec[]>([]);
  const [selectedBarcode, setSelectedBarcode] = useState<string | null>(null);
  const [selectedMainIds, setSelectedMainIds] = useState<Set<number>>(new Set());
  const [selectedDetailIds, setSelectedDetailIds] = useState<Set<number>>(new Set());

  // Modals
  const [scanVisible, setScanVisible] = useState(false);
  const [pendingVisible, setPendingVisible] = useState(false);
  const [exportVisible, setExportVisible] = useState(false);

  const fetchImages = useCallback(() => {
    setLoading(true);
    imageApi.list({ barcode: barcode || undefined, page, page_size: pageSize, sort: sortField, order: sortOrder })
      .then(res => { setImages(res.items); setTotal(res.total); })
      .finally(() => setLoading(false));
  }, [barcode, page, pageSize, sortField, sortOrder]);

  const fetchPendingCount = useCallback(() => {
    pendingApi.list().then(list => setPendingCount(list.length));
  }, []);

  useEffect(() => { fetchImages(); fetchPendingCount(); }, [fetchImages, fetchPendingCount]);

  // All selected image IDs (union)
  const allSelectedIds = new Set<number>([
    ...tableSelectedKeys.map(Number),
    ...selectedMainIds,
    ...selectedDetailIds,
  ]);

  const handleBatchDelete = async () => {
    if (allSelectedIds.size === 0) return;
    await imageApi.batchDelete(Array.from(allSelectedIds));
    message.success(`已删除 ${allSelectedIds.size} 张图片`);
    setTableSelectedKeys([]); setTableSelectedRows([]);
    setSelectedMainIds(new Set()); setSelectedDetailIds(new Set());
    fetchImages();
  };

  const handleBatchExport = async () => {
    if (allSelectedIds.size === 0) return;
    const res = await imageApi.batchExport(Array.from(allSelectedIds));
    window.open(exportApi.downloadUrl(res.task_id), '_blank');
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
        <Text strong style={{ fontSize: 18 }}>商品图片管理系统</Text>
      </Header>
      <Content style={{ padding: 16 }}>
        <SearchBar
          onSearch={setBarcode}
          onAddScanRoot={() => setScanVisible(true)}
          onExportExcel={() => setExportVisible(true)}
          onTriggerScan={() => { /* handled by ScanManager */ setScanVisible(true); }}
          onOpenPending={() => setPendingVisible(true)}
          pendingCount={pendingCount}
          loading={loading}
        />

        {/* Batch operation bar */}
        {allSelectedIds.size > 0 && (
          <div style={{ background: '#e6f7ff', padding: '8px 16px', marginBottom: 12, borderRadius: 6,
            display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>已选 <Text strong>{allSelectedIds.size}</Text> 张图片</span>
            <Space>
              <Button icon={<ExportOutlined />} onClick={handleBatchExport}>批量导出</Button>
              <Button icon={<DeleteOutlined />} danger onClick={handleBatchDelete}>批量删除</Button>
            </Space>
          </div>
        )}

        <div style={{ display: 'flex', gap: 16 }}>
          <div style={{ flex: '0 0 60%', minWidth: 0 }}>
            <ImageTable
              images={images} loading={loading} total={total}
              page={page} pageSize={pageSize}
              selectedRowKeys={tableSelectedKeys}
              onSelectionChange={(keys, rows) => { setTableSelectedKeys(keys); setTableSelectedRows(rows); }}
              onRowClick={setSelectedBarcode}
              onPageChange={(p, ps) => { setPage(p); setPageSize(ps); }}
            />
          </div>
          <div style={{ flex: '0 0 40%', minWidth: 300 }}>
            <ImageCardDetail
              barcode={selectedBarcode}
              selectedMainIds={selectedMainIds}
              selectedDetailIds={selectedDetailIds}
              onMainSelectionChange={setSelectedMainIds}
              onDetailSelectionChange={setSelectedDetailIds}
            />
          </div>
        </div>
      </Content>
      <Footer style={{ textAlign: 'center', padding: '8px 0' }}>
        <Text type="secondary">图片库系统 v1.0</Text>
      </Footer>

      <ScanManager visible={scanVisible} onClose={() => setScanVisible(false)} onScanComplete={fetchImages} />
      <PendingList visible={pendingVisible} onClose={() => setPendingVisible(false)} onConfirmed={() => { fetchImages(); fetchPendingCount(); }} />
      <ExportDialog visible={exportVisible} onClose={() => setExportVisible(false)} />
    </Layout>
  );
};

export default Home;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Home.tsx
git commit -m "feat: add Home page with left table, right card layout, batch operations bar"
```

---

### Task 20: App entry point — wire up Home page

**Files:**
- Create: `frontend/src/App.tsx`

- [ ] **Step 1: Create App.tsx**

```tsx
import React from 'react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import Home from './pages/Home';

const App: React.FC = () => (
  <ConfigProvider locale={zhCN}>
    <Home />
  </ConfigProvider>
);

export default App;
```

- [ ] **Step 2: Full TypeScript check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Start both servers and smoke test**

Run backend: `cd backend && python app.py`
Run frontend: `cd frontend && npm run dev`
Open browser at `http://localhost:3000`
Expected: App loads with header, search bar, empty table, and empty card detail panel

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: wire up App entry with Ant Design Chinese locale"
```

---

## Implementation Order

```
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20
```

Tasks must run sequentially — each depends on files from previous tasks.
