import re, os, hashlib, datetime
from models import session, Image, ScanRoot
from thumbnail import generate_thumbnail

# NAMED_RE: barcode_主图/详情图_sequence.ext — type from filename
NAMED_RE = re.compile(
    r'^(\d+)_(主图|详情图)_(\d+)\.(jpg|jpeg|png|gif|webp)$', re.IGNORECASE
)
# PLAIN_RE: barcode_sequence.ext — type from root setting
PLAIN_RE = re.compile(
    r'^(\d+)_(\d+)\.(jpg|jpeg|png|gif|webp)$', re.IGNORECASE
)

TYPE_MAP = {'主图': 'main', '详情图': 'detail'}

def parse_filename(filename, fuzzy_image_type='main'):
    """Parse a filename. NAMED format (with 主图/详情图) gets type from
    filename; PLAIN format gets type from the root's fuzzy_image_type."""
    m = NAMED_RE.match(filename)
    if m:
        return {
            'barcode': m.group(1),
            'image_type': TYPE_MAP[m.group(2)],
            'sequence': int(m.group(3)),
            'ext': m.group(4).lower(),
            'match_type': 'strict',
            'confirmed': True,
        }
    m = PLAIN_RE.match(filename)
    if m:
        return {
            'barcode': m.group(1),
            'image_type': fuzzy_image_type,
            'sequence': int(m.group(2)),
            'ext': m.group(3).lower(),
            'match_type': 'strict',
            'confirmed': True,
        }
    return None

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

def get_folder_mtime(folder_path):
    """Get ISO8601 mtime for a folder."""
    try:
        return datetime.datetime.fromtimestamp(
            os.path.getmtime(folder_path)
        ).isoformat()
    except OSError:
        return ''

def file_fingerprint(filepath):
    """Return a fast fingerprint for change detection: size_mtime.
    Does NOT read file content — only stat()."""
    try:
        st = os.stat(filepath)
        return f"{st.st_size}_{int(st.st_mtime)}"
    except OSError:
        return ''

def compute_md5(filepath):
    """Compute MD5 hash of a file (reads content). Only called for
    new files or files whose fingerprint has changed.
    Returns '' if the file cannot be read (missing, permission, etc.)."""
    try:
        h = hashlib.md5()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ''

def _walk_nonrecursive(path):
    """Yield (dirpath, [], filenames) for a single directory, with error handling."""
    try:
        entries = os.listdir(path)
        files = [e for e in entries if os.path.isfile(os.path.join(path, e))]
        yield path, [], files
    except OSError:
        return

def scan_root(root_id, full_scan=False, progress_callback=None):
    """Scan a single scan root. If the root's allow_fuzzy toggle is on,
    image_type is taken from the root's fuzzy_image_type setting;
    otherwise defaults to 'main'.

    If full_scan is True, all existing images for this root are deleted first.
    progress_callback(phase, **kwargs) is called at key points for async progress reporting."""
    root = session.get(ScanRoot, root_id)
    if not root:
        return {'error': 'Scan root not found'}

    try:
        return _do_scan(root, root_id, full_scan, progress_callback)
    except Exception:
        session.rollback()
        raise


def _do_scan(root, root_id, full_scan, progress_callback):

    def _report(phase, **kw):
        if progress_callback:
            progress_callback(phase, **kw)

    use_custom_type = root.allow_fuzzy
    fuzzy_type = root.fuzzy_image_type if use_custom_type else 'main'

    added = 0
    skipped = 0
    broken_cleaned = 0  # in full_scan mode this counts all deleted records, not just broken
    thumb_jobs = []

    _report('scan_start', current_root_path=root.path)

    # Build indexed_map BEFORE any deletion so we can safely recover on failure
    indexed_map = {
        img.file_path: img for img in session.query(Image).filter(
            Image.scan_root_id == root_id
        ).all()
    }

    if not full_scan:
        # Incremental: clean up broken records for this root
        broken = session.query(Image).filter(
            Image.scan_root_id == root_id, Image.status == 'broken'
        ).all()
        for img in broken:
            session.delete(img)
        broken_cleaned = len(broken)
        session.commit()

    walk = os.walk if root.recursive else _walk_nonrecursive

    for dirpath, _, filenames in walk(root.path):
        folder_mtime = get_folder_mtime(dirpath)
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in IMAGE_EXTS:
                continue
            full_path = os.path.normpath(os.path.join(dirpath, fname))

            _report('scanning', current_file=fname, added=added, skipped=skipped)

            existing = indexed_map.pop(full_path, None)
            if existing is not None:
                fp = file_fingerprint(full_path)
                if not fp:
                    existing.status = 'broken'
                    continue
                if fp == existing.md5_hash:
                    # fingerprint 未变（文件大小 + mtime 均未变），跳过
                    skipped += 1
                    continue
                # fingerprint 已变 → 文件内容已变化，重新解析并更新
                try:
                    new_size = os.path.getsize(full_path)
                except OSError:
                    continue
                reparsed = parse_filename(fname, fuzzy_type)
                if reparsed:
                    existing.barcode = reparsed['barcode']
                    existing.image_type = reparsed['image_type']
                    existing.sequence = reparsed['sequence']
                    existing.ext = reparsed['ext']
                    existing.confirmed = reparsed['confirmed']
                existing.md5_hash = fp
                existing.file_size = new_size
                existing.folder_mtime = folder_mtime
                existing.status = 'active'
                added += 1
                thumb_jobs.append((existing.id, full_path))
                continue

            parsed = parse_filename(fname, fuzzy_type)
            if not parsed:
                continue
            try:
                fsize = os.path.getsize(full_path)
                fp = file_fingerprint(full_path)
                if not fp:
                    continue
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
                md5_hash=fp,
                folder_path=dirpath,
                folder_mtime=folder_mtime,
                scan_root_id=root_id,
                confirmed=parsed['confirmed'],
            )
            session.add(img)
            session.flush()  # 获取 img.id
            thumb_jobs.append((img.id, full_path))
            added += 1

    # Handle leftover records not found on disk
    leftover_count = len(indexed_map)
    if full_scan:
        for img in indexed_map.values():
            session.delete(img)
        broken_cleaned += leftover_count
    else:
        for img in indexed_map.values():
            img.status = 'broken'

    session.commit()

    # 预生成缩略图（新图片和内容变更的图片），同时回写 content_md5
    _report('thumbnails', thumbnail_total=len(thumb_jobs), thumbnail_current=0,
            added=added, skipped=skipped)
    md5_updates = {}
    for i, (img_id, full_path) in enumerate(thumb_jobs):
        _, md5 = generate_thumbnail(img_id, full_path)
        if md5:
            md5_updates[img_id] = md5
        if (i + 1) % 10 == 0:
            _report('thumbnails', thumbnail_current=i + 1,
                    thumbnail_total=len(thumb_jobs))
    if md5_updates:
        ids = list(md5_updates.keys())
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start:chunk_start + 500]
            imgs = session.query(Image).filter(Image.id.in_(chunk)).all()
            for img in imgs:
                img.content_md5 = md5_updates[img.id]
        session.commit()

    _report('root_done', added=added, skipped=skipped,
            broken_cleaned=broken_cleaned, broken_new=leftover_count)

    return {'added': added, 'skipped': skipped, 'broken_cleaned': broken_cleaned, 'broken_new': leftover_count}
