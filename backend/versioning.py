import hashlib, json
from models import session, Image, ImageVersion

def compute_content_hash(images):
    """Compute a deterministic hash from a set of (filename, md5_hash) pairs."""
    pairs = sorted((img.filename, img.md5_hash) for img in images)
    payload = json.dumps(pairs, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()

def update_versions_for_barcode(barcode):
    """Rebuild version records for a single barcode, per image_type.
    Groups images by (folder_mtime, image_type), sorts descending by mtime,
    assigns v1 (oldest) through vN (newest) per type, merges duplicate content_hashes."""
    images = session.query(Image).filter(
        Image.barcode == barcode, Image.confirmed == True, Image.status == 'active'
    ).all()

    if not images:
        return

    # Group by (folder_mtime, image_type)
    by_key = {}
    for img in images:
        key = (img.folder_mtime, img.image_type)
        by_key.setdefault(key, []).append(img)

    sorted_keys = sorted(by_key.keys(), key=lambda k: k[0], reverse=True)

    # Build version list per image_type
    versions_by_type = {}
    seen_hashes = {}

    for key in sorted_keys:
        mtime, img_type = key
        imgs = by_key[key]
        ch = compute_content_hash(imgs)

        seen_hashes.setdefault(img_type, set())
        if ch in seen_hashes[img_type]:
            continue
        seen_hashes[img_type].add(ch)

        versions_by_type.setdefault(img_type, []).append((mtime, ch, imgs))

    # Delete old versions for this barcode
    session.query(ImageVersion).filter(ImageVersion.barcode == barcode).delete()

    # Create new versions per image_type
    for img_type, vers in versions_by_type.items():
        total = len(vers)
        for i, (mtime, ch, imgs) in enumerate(vers):
            version_num = total - i
            is_latest = (i == 0)
            v = ImageVersion(
                barcode=barcode,
                image_type=img_type,
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
