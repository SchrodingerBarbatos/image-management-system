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

def compute_md5(filepath):
    """Compute MD5 hash of a file."""
    h = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def _walk_nonrecursive(path):
    """Yield (dirpath, [], filenames) for a single directory, with error handling."""
    try:
        yield path, [], os.listdir(path)
    except OSError:
        return

def scan_root(root_id, allow_fuzzy=False, full_scan=False):
    """Scan a single scan root. When both the global parameter and the root's
    allow_fuzzy toggle are on, image_type is taken from the root's
    fuzzy_image_type setting; otherwise defaults to 'main'.

    If full_scan is True, all existing images for this root are deleted first."""
    root = session.get(ScanRoot, root_id)
    if not root:
        return {'error': 'Scan root not found'}

    use_custom_type = allow_fuzzy and root.allow_fuzzy
    fuzzy_type = root.fuzzy_image_type if use_custom_type else 'main'

    added = 0
    skipped = 0
    broken_cleaned = 0
    thumb_jobs = []

    # Full scan: delete all existing images for this root
    if full_scan:
        deleted = session.query(Image).filter(
            Image.scan_root_id == root_id
        ).delete()
        session.commit()
        broken_cleaned = deleted
    else:
        # Clean up broken records for this root
        broken = session.query(Image).filter(
            Image.scan_root_id == root_id, Image.status == 'broken'
        ).all()
        for img in broken:
            session.delete(img)
        broken_cleaned = len(broken)
        session.commit()

    indexed_map = {
        img.file_path: img for img in session.query(Image).filter(
            Image.scan_root_id == root_id
        ).all()
    }

    walk = os.walk if root.recursive else _walk_nonrecursive

    for dirpath, _, filenames in walk(root.path):
        folder_mtime = get_folder_mtime(dirpath)
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in IMAGE_EXTS:
                continue
            full_path = os.path.normpath(os.path.join(dirpath, fname))

            existing = indexed_map.pop(full_path, None)
            if existing is not None:
                try:
                    new_md5 = compute_md5(full_path)
                    new_size = os.path.getsize(full_path)
                except OSError:
                    continue
                if new_md5 == existing.md5_hash:
                    # 文件内容未变，保留旧的 folder_mtime，不更新
                    skipped += 1
                    continue
                # 文件内容已变，重新解析文件名并更新所有字段
                reparsed = parse_filename(fname, fuzzy_type)
                if reparsed:
                    existing.barcode = reparsed['barcode']
                    existing.image_type = reparsed['image_type']
                    existing.sequence = reparsed['sequence']
                    existing.ext = reparsed['ext']
                    existing.confirmed = reparsed['confirmed']
                existing.md5_hash = new_md5
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
            session.flush()  # 获取 img.id
            thumb_jobs.append((img.id, full_path))
            added += 1

    # 磁盘上已不存在的文件标记为 broken
    for img in indexed_map.values():
        img.status = 'broken'

    session.commit()

    # 预生成缩略图（新图片和内容变更的图片）
    for img_id, full_path in thumb_jobs:
        generate_thumbnail(img_id, full_path)

    return {'added': added, 'skipped': skipped, 'broken_cleaned': broken_cleaned, 'broken_new': len(indexed_map)}
