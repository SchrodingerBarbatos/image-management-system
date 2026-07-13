import re, os, hashlib, datetime, uuid, logging
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from models import session, Image, ImageVersion, ScanRoot, RejectedBarcode, DeletedFolder
from thumbnail import generate_thumbnail, thumbnail_exists

_log = logging.getLogger(__name__)


class ScanCancelled(Exception):
    """Raised when a scan is cancelled by the user."""
    pass


def calculate_gtin_check_digit(payload: str) -> int:
    """计算 GTIN 校验位。"""
    total = 0
    reversed_payload = [int(x) for x in payload[::-1]]

    for idx, num in enumerate(reversed_payload):
        if idx % 2 == 0:
            total += num * 3
        else:
            total += num

    return (10 - (total % 10)) % 10


def validate_business_gtin(barcode: str) -> tuple[bool, str]:
    """业务有效性校验：拒绝 GS1 Restricted Circulation Numbers。

    Returns:
        (is_valid, reason)
    """
    if not barcode.isdigit():
        return True, ""

    length = len(barcode)

    # GTIN-13: 前3位为 GS1 Prefix
    if length == 13:
        prefix3 = int(barcode[:3])
        if 200 <= prefix3 <= 299:
            return False, "GS1 200–299 为限制流通码（Restricted Circulation Number）"

    # GTIN-14: 第2~4位为 GS1 Prefix（第1位为包装指示码）
    elif length == 14:
        prefix3 = int(barcode[1:4])
        if 200 <= prefix3 <= 299:
            return False, "GS1 200–299 为限制流通码（Restricted Circulation Number）"

    # UPC-A / GTIN-12: Number System digit is the first digit.
    # NS=2 (RCN / variable measure) and NS=4 (restricted circulation) cover
    # the full 200–299 and 400–499 three-digit ranges, not only 020–029/040–049.
    elif length == 12:
        ns = barcode[0]
        if ns == '2':
            return False, "UPC NS=2（200–299）为限制流通码"
        if ns == '4':
            return False, "UPC NS=4（400–499）为企业内部流通码"

    return True, ""


def validate_gtin(barcode: str):
    """
    验证 GTIN-8 / GTIN-12 / GTIN-13 / GTIN-14

    Returns:
        (is_valid, reason)
        is_valid: bool
        reason: str
    """
    # 去除首尾空格
    barcode = barcode.strip()

    # 1. 长度校验
    if len(barcode) not in (8, 12, 13, 14):
        return False, (
            f"长度 {len(barcode)} 不符合 GTIN 要求"
            "（需要 8、12、13 或 14 位）"
        )

    # 2. 数字校验
    if not barcode.isdigit():
        return False, "包含非数字字符"

    digits = [int(x) for x in barcode]

    check_digit = digits[-1]
    payload = digits[:-1]

    # 3. GTIN Modulo-10 校验
    expected_check = calculate_gtin_check_digit(barcode[:-1])

    if check_digit != expected_check:
        return False, (
            f"校验位错误（期望 {expected_check}，实际 {check_digit}）"
        )

    # 4. 业务有效性校验（RCN 拒绝）
    is_valid, reason = validate_business_gtin(barcode)
    if not is_valid:
        return False, reason

    return True, ""

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

def get_folder_ctime(folder_path):
    """Get ISO8601 creation time for a folder."""
    try:
        return datetime.datetime.fromtimestamp(
            os.path.getctime(folder_path)
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


def count_image_files(roots, progress_callback=None, is_cancelled=None):
    """Count total image files across all scan roots.
    Note: this performs a separate os.walk traversal before the actual scan,
    adding one extra pass over the directory tree. Acceptable trade-off for
    accurate progress reporting. May add startup latency on network drives
    or very large directories.
    Returns total count of image files matching IMAGE_EXTS.
    Raises ScanCancelled if is_cancelled returns True."""
    total = 0
    for i, r in enumerate(roots):
        walk = os.walk if r.recursive else _walk_nonrecursive
        try:
            for dirpath, _, filenames in walk(r.path):
                if is_cancelled and is_cancelled():
                    raise ScanCancelled()
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in IMAGE_EXTS:
                        total += 1
                if progress_callback:
                    progress_callback('counting',
                        counting_root_index=i + 1,
                        counting_total_roots=len(roots),
                        counting_current_dir=dirpath,
                        counted_files=total)
        except OSError:
            continue
    return total

# Lightweight tuple for dir_index entries to avoid loading full ORM objects.
# Fields: (image_id, barcode, md5_hash, image_type)
_IDX_ID = 0
_IDX_BARCODE = 1
_IDX_MD5 = 2
_IDX_IMAGE_TYPE = 3

# Batch sizes
_THUMB_BATCH_SIZE = 300  # process thumbnails every N files during walk
_LEFTOVER_BATCH_SIZE = 500


def _build_dir_index(root_id, dirpath):
    """Load a lightweight index of images for a specific directory.
    Returns {file_path: (image_id, barcode, md5_hash, image_type)}.
    Uses Core expression to bypass ORM identity map entirely."""
    result = {}
    stmt = select(
        Image.id, Image.file_path, Image.barcode, Image.md5_hash, Image.image_type
    ).where(
        Image.scan_root_id == root_id,
        Image.folder_path == dirpath,
    )
    rows = session.execute(stmt)
    for row in rows:
        result[row.file_path] = (row.id, row.barcode, row.md5_hash, row.image_type)
    return result


def _insert_rejected_ignore(root_id, barcode, file_path, filename, reason):
    """Insert a RejectedBarcode record, ignoring duplicates via INSERT OR IGNORE."""
    stmt = sqlite_insert(RejectedBarcode).values(
        barcode=barcode,
        file_path=file_path,
        filename=filename,
        reason=reason,
        scan_root_id=root_id,
    ).on_conflict_do_nothing(
        index_elements=['scan_root_id', 'barcode', 'file_path']
    )
    session.execute(stmt)


def _process_thumbnail_batch(thumb_jobs, progress_callback=None, is_cancelled=None):
    """Process a batch of thumbnail jobs and write back content_md5 and phash via Core update.
    Returns dict of {image_id: md5} for successful MD5 computations.
    Reports progress every 5 thumbnails when progress_callback is provided.
    Checks is_cancelled between each thumbnail when provided."""
    if not thumb_jobs:
        return {}
    md5_updates = {}
    phash_updates = {}
    total = len(thumb_jobs)
    for i, (img_id, full_path) in enumerate(thumb_jobs):
        if is_cancelled and is_cancelled():
            raise ScanCancelled()
        _, md5, phash = generate_thumbnail(img_id, full_path)
        if md5:
            md5_updates[img_id] = md5
        if phash:
            phash_updates[img_id] = phash
        # Report progress every 5 thumbnails or on the last one
        if progress_callback and (i == 0 or (i + 1) % 5 == 0 or i + 1 == total):
            progress_callback('thumbnails', thumbnail_total=total, thumbnail_current=i + 1)
    # Write back content_md5 and phash via Core update (lightweight, no ORM objects)
    if md5_updates or phash_updates:
        _flush_hash_updates_core(md5_updates, phash_updates)
    return md5_updates


def _flush_hash_updates_core(md5_updates, phash_updates, sess=None):
    """Batch-write content_md5 and phash updates using Core UPDATE (no ORM objects loaded).
    If sess is provided, use it instead of the module-level session."""
    if sess is None:
        sess = session
    all_ids = set(md5_updates.keys()) | set(phash_updates.keys())
    if not all_ids:
        return
    ids = list(all_ids)
    for chunk_start in range(0, len(ids), 500):
        chunk = ids[chunk_start:chunk_start + 500]
        for img_id in chunk:
            values = {}
            if img_id in md5_updates:
                values['content_md5'] = md5_updates[img_id]
            if img_id in phash_updates:
                values['phash'] = phash_updates[img_id]
            if values:
                sess.execute(
                    update(Image)
                    .where(Image.id == img_id)
                    .values(**values)
                )
    sess.flush()


def _load_deleted_folders_set(scan_root_id=None):
    """Load deleted (barcode, image_type, folder_ctime) tuples into a set.

    When scan_root_id is provided, include both that root's rows and legacy
    rows with scan_root_id=0 (pre-root-scoped blacklist).
    """
    q = select(DeletedFolder.barcode, DeletedFolder.image_type, DeletedFolder.folder_ctime)
    if scan_root_id is not None:
        q = q.where(
            (DeletedFolder.scan_root_id == scan_root_id) | (DeletedFolder.scan_root_id == 0)
        )
    rows = session.execute(q).all()
    return {(r.barcode, r.image_type, r.folder_ctime) for r in rows}


def scan_root(root_id, full_scan=False, progress_callback=None, processed_offset=0,
              is_cancelled=None):
    """Scan a single scan root. If the root's allow_fuzzy toggle is on,
    image_type is taken from the root's fuzzy_image_type setting;
    otherwise defaults to 'main'.

    If full_scan is True, missing records are deleted only after disk scan succeeds.
    progress_callback(phase, **kwargs) is called at key points for async progress reporting.
    processed_offset: cumulative processed_files count from previous roots (for multi-root progress).
    is_cancelled: callable returning True if scan should be cancelled.
    Raises ScanCancelled if is_cancelled returns True."""
    root = session.get(ScanRoot, root_id)
    if not root:
        return {'error': 'Scan root not found'}

    try:
        return _do_scan(root, root_id, full_scan, progress_callback, processed_offset,
                        is_cancelled)
    except Exception:
        session.rollback()
        raise


def _do_scan(root, root_id, full_scan, progress_callback, processed_offset=0,
             is_cancelled=None):

    def _report(phase, **kw):
        if progress_callback:
            progress_callback(phase, **kw)

    # Save root attributes to local variables to avoid issues if they get expired
    root_path = root.path
    root_recursive = root.recursive
    use_custom_type = root.allow_fuzzy
    fuzzy_type = root.fuzzy_image_type if use_custom_type else 'main'

    # Pre-load deleted folders set for O(1) lookup — skip files that were
    # intentionally deleted by the user (all hard-delete paths record these).
    # Scoped to this scan root (+ legacy root_id=0) so roots do not cross-blacklist.
    deleted_folders = _load_deleted_folders_set(root_id)

    # Generate a unique scan token for leftover detection
    scan_token = uuid.uuid4().hex

    added = 0
    skipped = 0
    broken_cleaned = 0  # in full_scan mode this counts all deleted records, not just broken
    rejected_count = 0
    local_processed = 0  # counter local to this root
    thumb_jobs = []  # accumulated between batch processing
    affected_barcodes = set()

    _report('scan_start', current_root_path=root_path)

    if not full_scan:
        # Incremental: clean up broken records BEFORE scanning
        # so they don't end up counted as leftover
        broken = session.query(Image).filter(
            Image.scan_root_id == root_id, Image.status == 'broken'
        ).all()
        for img in broken:
            affected_barcodes.add(img.barcode)
            session.delete(img)
        broken_cleaned = len(broken)
        session.flush()

    walk = os.walk if root_recursive else _walk_nonrecursive

    for dirpath, _, filenames in walk(root_path):
        if is_cancelled and is_cancelled():
            raise ScanCancelled()
        folder_ctime = get_folder_ctime(dirpath)

        # Build directory-level index (replaces full-root indexed_map)
        dir_index = _build_dir_index(root_id, dirpath)

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in IMAGE_EXTS:
                continue
            full_path = os.path.normpath(os.path.join(dirpath, fname))

            local_processed += 1
            _report('scanning', current_file=fname, added=added, skipped=skipped,
                    rejected=rejected_count, processed_files=processed_offset + local_processed,
                    current_dir=dirpath)

            entry = dir_index.pop(full_path, None)
            if entry is not None:
                img_id, img_barcode, img_md5, img_type = entry
                fp = file_fingerprint(full_path)
                if not fp:
                    # File inaccessible — load ORM object and mark broken
                    img = session.get(Image, img_id)
                    if img:
                        img.status = 'broken'
                        img.last_scan_token = scan_token
                        affected_barcodes.add(img.barcode)
                    continue
                if fp == img_md5:
                    # fingerprint 未变（文件大小 + mtime 均未变）
                    # 检查是否需要补算 content_md5 / phash / thumbnail
                    img = session.get(Image, img_id)
                    if img:
                        img.last_scan_token = scan_token
                        needs_hash = not img.content_md5 or not img.phash
                        needs_thumb = not thumbnail_exists(img_id)
                        if needs_hash or needs_thumb:
                            thumb_jobs.append((img_id, full_path))
                    skipped += 1
                    continue
                # fingerprint 已变 → 文件内容已变化，重新解析并更新
                try:
                    new_size = os.path.getsize(full_path)
                except OSError:
                    continue
                reparsed = parse_filename(fname, fuzzy_type)
                if reparsed:
                    # GTIN validation for re-parsed barcodes
                    is_valid, reason = validate_gtin(reparsed['barcode'])
                    if not is_valid:
                        _insert_rejected_ignore(root_id, reparsed['barcode'], full_path, fname, reason)
                        # Delete ImageVersion records for old barcode+type
                        session.query(ImageVersion).filter(
                            ImageVersion.barcode == img_barcode,
                            ImageVersion.image_type == img_type,
                        ).delete()
                        img = session.get(Image, img_id)
                        if img:
                            session.delete(img)
                        rejected_count += 1
                        continue
                    # Load ORM object for update
                    img = session.get(Image, img_id)
                    if not img:
                        continue
                    # Track both old and new barcode for version rebuild
                    old_barcode = img_barcode
                    new_barcode = reparsed['barcode']
                    affected_barcodes.add(old_barcode)
                    affected_barcodes.add(new_barcode)
                    img.barcode = new_barcode
                    img.image_type = reparsed['image_type']
                    img.sequence = reparsed['sequence']
                    img.ext = reparsed['ext']
                    img.confirmed = reparsed['confirmed']
                else:
                    img = session.get(Image, img_id)
                    if not img:
                        continue
                    affected_barcodes.add(img.barcode)
                img.md5_hash = fp
                img.file_size = new_size
                img.folder_ctime = folder_ctime
                img.status = 'active'
                img.last_scan_token = scan_token
                added += 1
                thumb_jobs.append((img_id, full_path))
                continue

            parsed = parse_filename(fname, fuzzy_type)
            if not parsed:
                continue

            # GTIN validation: reject non-GTIN barcodes
            is_valid, reason = validate_gtin(parsed['barcode'])
            if not is_valid:
                _insert_rejected_ignore(root_id, parsed['barcode'], full_path, fname, reason)
                rejected_count += 1
                continue

            # Check deleted_folders tracking: skip files from folders the user
            # intentionally deleted, preventing re-add on incremental scan.
            key = (parsed['barcode'], parsed['image_type'], folder_ctime)
            if key in deleted_folders:
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
                folder_ctime=folder_ctime,
                scan_root_id=root_id,
                confirmed=parsed['confirmed'],
                last_scan_token=scan_token,
            )
            session.add(img)
            session.flush()  # 获取 img.id for thumbnail
            thumb_jobs.append((img.id, full_path))
            affected_barcodes.add(parsed['barcode'])
            added += 1

        # Release dir_index — memory freed for this directory
        del dir_index

        # Process thumbnail batch every _THUMB_BATCH_SIZE files
        if len(thumb_jobs) >= _THUMB_BATCH_SIZE:
            if is_cancelled and is_cancelled():
                raise ScanCancelled()
            _process_thumbnail_batch(thumb_jobs, progress_callback=_report, is_cancelled=is_cancelled)
            thumb_jobs.clear()

        # Segment commit: persist this directory's results before moving on.
        # Shortens write-lock hold time, prevents WAL bloat, and preserves
        # progress if the process crashes mid-scan.
        session.commit()

    # Process remaining thumbnail jobs
    if thumb_jobs:
        if is_cancelled and is_cancelled():
            raise ScanCancelled()
        _process_thumbnail_batch(thumb_jobs, progress_callback=_report, is_cancelled=is_cancelled)
        thumb_jobs.clear()

    # Commit remaining thumbnails and any last-directory data before leftover handling
    session.commit()

    # Check cancellation before leftover handling
    if is_cancelled and is_cancelled():
        raise ScanCancelled()

    # Handle leftover records not found on disk (via scan token)
    # Query images that were NOT touched during this scan
    # Includes: last_scan_token != scan_token OR last_scan_token IS NULL OR last_scan_token = ''
    leftover_q = session.query(Image).filter(
        Image.scan_root_id == root_id,
        (Image.last_scan_token != scan_token) |
        (Image.last_scan_token == None) |
        (Image.last_scan_token == '')
    )
    leftover_count = leftover_q.count()

    if leftover_count > 0:
        # Collect barcodes for version rebuild
        leftover_barcodes = set(
            bc for (bc,) in session.query(Image.barcode).filter(
                Image.scan_root_id == root_id,
                (Image.last_scan_token != scan_token) |
                (Image.last_scan_token == None) |
                (Image.last_scan_token == '')
            ).all()
        )
        affected_barcodes.update(leftover_barcodes)

        if full_scan:
            # Batch-delete leftover records
            leftover_ids = [
                img_id for (img_id,) in session.query(Image.id).filter(
                    Image.scan_root_id == root_id,
                    (Image.last_scan_token != scan_token) |
                    (Image.last_scan_token == None) |
                    (Image.last_scan_token == '')
                ).all()
            ]
            for chunk_start in range(0, len(leftover_ids), _LEFTOVER_BATCH_SIZE):
                chunk = leftover_ids[chunk_start:chunk_start + _LEFTOVER_BATCH_SIZE]
                session.query(Image).filter(Image.id.in_(chunk)).delete(synchronize_session='fetch')
            broken_cleaned += leftover_count
        else:
            # Batch-mark as broken
            leftover_ids = [
                img_id for (img_id,) in session.query(Image.id).filter(
                    Image.scan_root_id == root_id,
                    (Image.last_scan_token != scan_token) |
                    (Image.last_scan_token == None) |
                    (Image.last_scan_token == '')
                ).all()
            ]
            for chunk_start in range(0, len(leftover_ids), _LEFTOVER_BATCH_SIZE):
                chunk = leftover_ids[chunk_start:chunk_start + _LEFTOVER_BATCH_SIZE]
                session.query(Image).filter(Image.id.in_(chunk)).update(
                    {Image.status: 'broken'}, synchronize_session='fetch'
                )

    # Single commit for all scan results (transaction consistency)
    session.commit()

    _report('root_done', added=added, skipped=skipped,
            broken_cleaned=broken_cleaned, broken_new=leftover_count,
            rejected=rejected_count, processed_files=processed_offset + local_processed)

    return {'added': added, 'skipped': skipped, 'broken_cleaned': broken_cleaned, 'broken_new': leftover_count,
            'rejected': rejected_count, 'affected_barcodes': list(affected_barcodes)}
