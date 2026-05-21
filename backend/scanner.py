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
