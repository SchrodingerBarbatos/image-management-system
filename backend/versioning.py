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
