import hashlib, json, time, logging
from collections import defaultdict
from models import session, Image, ImageVersion, ScanRoot
from scanner import compute_md5

_log = logging.getLogger(__name__)

from db_retry import _is_sqlite_locked  # re-export for backward compat

_SQLITE_RETRY_ATTEMPTS = 5
_SQLITE_RETRY_DELAY = 0.5  # seconds


def update_versions_for_barcode(barcode):
    """Rebuild version records for a single barcode, per image_type.
    Groups images by (folder_ctime, image_type), then uses signature-based
    dedup + funnel comparison to merge identical groups before creating versions."""
    for attempt in range(1, _SQLITE_RETRY_ATTEMPTS + 1):
        try:
            _do_update_versions_for_barcode(barcode)
            return
        except Exception as e:
            if not _is_sqlite_locked(e):
                raise
            session.rollback()
            if attempt == _SQLITE_RETRY_ATTEMPTS:
                _log.error("update_versions_for_barcode(%s) failed after %d retries: %s",
                           barcode, _SQLITE_RETRY_ATTEMPTS, e)
                raise
            _log.warning("update_versions_for_barcode(%s) retry %d/%d (locked)",
                         barcode, attempt, _SQLITE_RETRY_ATTEMPTS)
            time.sleep(_SQLITE_RETRY_DELAY)

# Batch size for session cleanup during bulk version updates
_VERSION_BATCH_SIZE = 200


def compute_content_hash(images):
    """Deterministic hash from sorted (filename, file_size, content_md5) triples.
    Same content → same hash, regardless of file paths or ctimes.
    Uses content_md5 (real MD5) when available; falls back to md5_hash (fingerprint)
    for legacy data that predates content_md5."""
    pairs = sorted(
        (img.filename, img.file_size, img.content_md5 or img.md5_hash)
        for img in images
    )
    payload = json.dumps(pairs, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()


def _group_signature(images):
    """Fast signature for a group of images, used for O(1) dedup before
    falling back to full groups_are_identical comparison.
    Returns a tuple that is equal for groups with identical (filename, size, md5)
    triples, regardless of order. This avoids the O(n²) pairwise comparison
    in the common case where groups differ in content."""
    triples = sorted(
        (img.filename, img.file_size, img.content_md5 or img.md5_hash)
        for img in images
    )
    return tuple(triples)


def groups_are_identical(imgs1, imgs2):
    """Funnel comparison: count → filename+size → MD5.
    Returns True only when all three layers match."""
    if not imgs1 or not imgs2:
        return False
    # Layer 1: same count
    if len(imgs1) != len(imgs2):
        return False
    # Layer 2: same (filename, file_size) pairs sorted
    key1 = sorted((img.filename, img.file_size) for img in imgs1)
    key2 = sorted((img.filename, img.file_size) for img in imgs2)
    if key1 != key2:
        return False
    # Layer 3: content hash confirmation.
    # Prefer content_md5 (real MD5 computed at scan time, survives DB portability),
    # fall back to reading file, then to fingerprint (size_mtime) if file unavailable.
    s1 = sorted(imgs1, key=lambda x: x.filename)
    s2 = sorted(imgs2, key=lambda x: x.filename)
    for a, b in zip(s1, s2):
        md5_a = a.content_md5 or compute_md5(a.file_path) or a.md5_hash
        md5_b = b.content_md5 or compute_md5(b.file_path) or b.md5_hash
        if md5_a != md5_b:
            return False
    return True


def _expunge_image_version_objects():
    """Remove cached ImageVersion instances before recreating rows.

    SQLite may reuse the same primary keys for the rebuilt versions, so stale
    ORM identities must be dropped first to avoid identity-map replacement
    warnings during flush.
    """
    for obj in list(session.identity_map.values()):
        if isinstance(obj, ImageVersion):
            try:
                session.expunge(obj)
            except Exception:
                pass



def _do_update_versions_for_barcode(barcode):
    # RCN 条码不应有版本记录：删除残留版本后跳过
    from scanner import validate_business_gtin
    is_valid, _ = validate_business_gtin(barcode)
    if not is_valid:
        deleted = session.query(ImageVersion).filter(ImageVersion.barcode == barcode).delete()
        if deleted:
            session.commit()
        return

    images = session.query(Image).filter(
        Image.barcode == barcode, Image.confirmed == True, Image.status == 'active'
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True
    ).all()

    if not images:
        # Clean up orphaned version records when all images are gone
        session.query(ImageVersion).filter(ImageVersion.barcode == barcode).delete()
        session.commit()
        return

    # Group by (folder_ctime, image_type)
    by_key = defaultdict(list)
    for img in images:
        key = (img.folder_ctime, img.image_type)
        by_key[key].append(img)

    # Per image_type: sort by ctime desc, signature-dedup + funnel-merge duplicates
    versions_by_type = {}  # {img_type: [(ctime, imgs, duplicate_ctimes)]}

    for img_type in ('main', 'detail'):
        type_keys = sorted(
            [k for k in by_key if k[1] == img_type],
            key=lambda k: k[0], reverse=True,
        )
        accepted = []  # [(ctime, imgs)]
        accepted_sigs = []  # parallel list of signatures for fast dedup
        dup_map = defaultdict(list)  # {ctime: [duplicate_ctime, ...]}

        for ctime, _ in type_keys:
            imgs = by_key[(ctime, img_type)]
            sig = _group_signature(imgs)

            # Fast path: check signature against accepted groups first
            duplicate_of = None
            for acc_idx, acc_sig in enumerate(accepted_sigs):
                if sig == acc_sig:
                    # Signatures match — confirm with full comparison
                    # (handles rare MD5 fallback differences)
                    if groups_are_identical(imgs, accepted[acc_idx][1]):
                        duplicate_of = accepted[acc_idx][0]
                        break

            if duplicate_of:
                dup_map[duplicate_of].append(ctime)
            else:
                accepted.append((ctime, imgs))
                accepted_sigs.append(sig)

        versions_by_type[img_type] = [
            (ctime, imgs, json.dumps(dup_map.get(ctime, [])))
            for ctime, imgs in accepted
        ]

    # Delete old versions for this barcode
    session.query(ImageVersion).filter(ImageVersion.barcode == barcode).delete()
    session.flush()
    _expunge_image_version_objects()

    # Create new versions per image_type
    for img_type, vers in versions_by_type.items():
        total = len(vers)
        for i, (ctime, imgs, dup_ctimes) in enumerate(vers):
            version_num = total - i
            is_latest = (i == 0)
            ch = compute_content_hash(imgs)
            v = ImageVersion(
                barcode=barcode,
                image_type=img_type,
                version_label=f'v{version_num}',
                folder_ctime=ctime,
                content_hash=ch,
                is_latest=is_latest,
                duplicate_mtimes=dup_ctimes,
            )
            session.add(v)
    session.commit()


def _expunge_versions_and_images():
    """Expunge only Image and ImageVersion objects from the session.
    Leaves ScanRoot and other ORM objects untouched to avoid DetachedInstanceError
    for callers that hold references to them."""
    to_expunge = [
        obj for obj in session.identity_map.values()
        if isinstance(obj, (Image, ImageVersion))
    ]
    for obj in to_expunge:
        try:
            session.expunge(obj)
        except Exception:
            pass  # already detached or deleted


def update_all_versions(progress_callback=None):
    """Run version update for all barcodes in the database.
    Includes progress logging and periodic session cleanup for memory control."""
    barcodes = session.query(Image.barcode).filter(
        Image.confirmed == True, Image.status == 'active'
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True
    ).distinct().all()

    total = len(barcodes)
    _log.info("update_all_versions: starting version rebuild for %d barcodes", total)

    for i, (bc,) in enumerate(barcodes):
        update_versions_for_barcode(bc)

        # Progress logging every _VERSION_BATCH_SIZE barcodes
        if (i + 1) % _VERSION_BATCH_SIZE == 0:
            _log.info("update_all_versions: processed %d/%d barcodes", i + 1, total)
            # Periodic session cleanup to control memory — only expunge
            # Image/ImageVersion, not ScanRoot (caller may hold a reference)
            _expunge_versions_and_images()

        # Optional progress callback for async reporting
        if progress_callback:
            progress_callback(current=i + 1, total=total)

    _log.info("update_all_versions: completed version rebuild for %d barcodes", total)
