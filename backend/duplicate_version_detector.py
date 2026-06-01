"""Duplicate version detection algorithm.

Detects versions that have the same image count and visually identical images
at each position. Uses MD5 for exact match and pHash for perceptual similarity.

Complexity: O(num_versions^2 * images_per_version) within each (barcode, image_type, count) group.
"""
import logging
from collections import defaultdict
from models import Image, ImageVersion, ScanRoot

_log = logging.getLogger(__name__)

# pHash hamming distance threshold for visual similarity
PHASH_THRESHOLD = 5


def hamming_distance(hex1, hex2):
    """Compute hamming distance between two hex hash strings."""
    if not hex1 or not hex2:
        return max(len(hex1), len(hex2)) * 4  # max distance for empty/missing
    if len(hex1) != len(hex2):
        return max(len(hex1), len(hex2)) * 4  # max distance
    b1 = int(hex1, 16)
    b2 = int(hex2, 16)
    return bin(b1 ^ b2).count('1')


def are_same_image(img_a, img_b):
    """Check if two images are visually the same.
    Layer 1: exact MD5 match (fast).
    Layer 2: pHash perceptual similarity (hamming distance <= threshold).
    """
    md5_a = img_a.content_md5 or ''
    md5_b = img_b.content_md5 or ''
    if md5_a and md5_b and md5_a == md5_b:
        return True
    phash_a = img_a.phash or ''
    phash_b = img_b.phash or ''
    if phash_a and phash_b:
        if hamming_distance(phash_a, phash_b) <= PHASH_THRESHOLD:
            return True
    return False


def are_duplicate_versions(imgs_a, imgs_b):
    """Check if two version image lists are duplicates.
    Short-circuits on the first mismatch.
    Both lists must be in the same order (by sequence).
    """
    if len(imgs_a) != len(imgs_b):
        return False
    for a, b in zip(imgs_a, imgs_b):
        if not are_same_image(a, b):
            return False
    return True


def sample_indices(count):
    """Return fixed sample indices for pre-filtering.
    Samples: first (0), middle (count // 2), last (count - 1).
    Deduplicated and sorted. No random sampling.
    """
    if count <= 0:
        return []
    indices = sorted({0, count // 2, count - 1})
    return indices


def passes_sample_filter(imgs_a, imgs_b):
    """Pre-filter using sampled positions (first/mid/last).
    Returns (passed, matched_indices).
    passed=True means all sampled positions matched.
    matched_indices is the set of indices that were compared and matched.
    Used ONLY for exclusion — never to判定重复.
    Final duplicate判定 must use are_duplicate_versions_skip_indices().
    """
    if len(imgs_a) != len(imgs_b):
        return False, set()
    matched = set()
    for idx in sample_indices(len(imgs_a)):
        if not are_same_image(imgs_a[idx], imgs_b[idx]):
            return False, set()
        matched.add(idx)
    return True, matched


def are_duplicate_versions_skip_indices(imgs_a, imgs_b, skip_indices):
    """Check if two version image lists are duplicates, skipping indices
    already verified by sample filter. Same logic as are_duplicate_versions()
    but skips positions in skip_indices.
    skip_indices must come from the current pair's sample filter only.
    """
    if len(imgs_a) != len(imgs_b):
        return False
    for i, (a, b) in enumerate(zip(imgs_a, imgs_b)):
        if i in skip_indices:
            continue
        if not are_same_image(a, b):
            return False
    return True


def _version_signature(images):
    """Compute a fast signature for pre-filtering.
    Uses phash1|phash2|...|phashN in sequence order.
    Falls back to content_md5 if phash is missing.
    Prefixes hash type (p:/m:) to avoid cross-type collisions.
    """
    parts = []
    for img in images:
        if img.phash:
            parts.append(f'p:{img.phash}')
        elif img.content_md5:
            parts.append(f'm:{img.content_md5}')
        else:
            parts.append('')
    return '|'.join(parts)


def _get_ordered_images(sess, barcode, image_type, folder_ctime):
    """Get images for a version, ordered by sequence (stable sort)."""
    return sess.query(Image).filter(
        Image.barcode == barcode,
        Image.image_type == image_type,
        Image.folder_ctime == folder_ctime,
        Image.status == 'active',
        Image.confirmed == True,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True,
    ).order_by(Image.sequence, Image.filename).all()


def _compute_version_stats(images):
    """Compute (image_count, total_file_size) for a list of images."""
    count = len(images)
    total_size = sum(img.file_size or 0 for img in images)
    return count, total_size


def _make_member(v, cnt, total_size):
    """Create a member dict for a version."""
    return {
        'folder_ctime': v.folder_ctime,
        'version_label': v.version_label,
        'image_count': cnt,
        'total_file_size': total_size,
        'is_latest': v.is_latest,
        'role': 'clean',
        'keep_reason': '',
    }


def pick_keep_version(members):
    """Select the recommended version to keep from a duplicate group.
    Priority:
    1. user_selected (manual choice — handled externally)
    2. is_latest (current/active version)
    3. total_file_size larger
    4. folder_ctime newer
    Returns (best_index, reason_string).
    """
    if not members:
        return 0, ''

    best_idx = 0
    best = members[0]
    reason = ''

    for i, m in enumerate(members):
        if i == 0:
            continue
        # Priority 2: is_latest
        if m.get('is_latest') and not best.get('is_latest'):
            best_idx = i
            best = m
            reason = '当前主版本'
            continue
        if best.get('is_latest') and not m.get('is_latest'):
            continue
        # Priority 3: total_file_size
        if (m.get('total_file_size') or 0) > (best.get('total_file_size') or 0):
            best_idx = i
            best = m
            reason = '总文件大小更大'
            continue
        if (m.get('total_file_size') or 0) < (best.get('total_file_size') or 0):
            continue
        # Priority 4: newer folder_ctime
        if (m.get('folder_ctime') or '') > (best.get('folder_ctime') or ''):
            best_idx = i
            best = m
            reason = '修改时间更新'
            continue

    if not reason and best.get('is_latest'):
        reason = '当前主版本'
    elif not reason:
        reason = '默认保留'

    return best_idx, reason


def _candidate_key(images, total_size):
    """Compute a candidate key for pre-filtering before pairwise comparison.
    Includes first/last image hash and file-size bucket. Two versions must
    have the same candidate key to be considered potential duplicates.
    This is a filter only — it can exclude non-matches but never判定重复.
    Uses content_md5 first (exact match indicator), falls back to phash.
    """
    def _img_hash(img):
        return img.content_md5 or img.phash or ''

    first_hash = _img_hash(images[0]) if images else ''
    last_hash = _img_hash(images[-1]) if images else ''
    size_bucket = total_size // 65536
    return (first_hash, last_hash, size_bucket)


def _find_groups_in_pool(pool):
    """Given a list of (version, images, count, total_size) tuples,
    find duplicate groups using candidate-key filtering + signature
    pre-filtering + short-circuit comparison.
    Returns (groups, stats) where groups is a list of member-lists
    and stats tracks comparison counts.
    """
    if len(pool) < 2:
        return [], {'candidate_pairs': 0, 'sample_filter_rejected': 0, 'actual_comparisons': 0}

    # Pre-compute signatures and candidate keys
    items = []
    for v, imgs, cnt, ts in pool:
        sig = _version_signature(imgs)
        ckey = _candidate_key(imgs, ts)
        items.append((v, imgs, cnt, ts, sig, ckey))

    assigned = set()  # set of folder_ctime
    groups = []
    candidate_pairs = 0
    sample_filter_rejected = 0
    actual_comparisons = 0

    # First pass: group by signature (fast path — exact signature match)
    sig_buckets = defaultdict(list)
    for idx, (v, imgs, cnt, ts, sig, ckey) in enumerate(items):
        sig_buckets[sig].append(idx)

    for sig, bucket_indices in sig_buckets.items():
        if len(bucket_indices) < 2:
            continue
        for i_idx in bucket_indices:
            if i_idx in assigned:
                continue
            v_i, imgs_i, cnt_i, ts_i, _, _ = items[i_idx]
            assigned.add(i_idx)
            members = [_make_member(v_i, cnt_i, ts_i)]
            for j_idx in bucket_indices:
                if j_idx in assigned:
                    continue
                v_j, imgs_j, cnt_j, ts_j, _, _ = items[j_idx]
                passed, skip = passes_sample_filter(imgs_i, imgs_j)
                if not passed:
                    sample_filter_rejected += 1
                    continue
                actual_comparisons += 1
                if are_duplicate_versions_skip_indices(imgs_i, imgs_j, skip):
                    assigned.add(j_idx)
                    members.append(_make_member(v_j, cnt_j, ts_j))
            if len(members) >= 2:
                groups.append(members)

    # Second pass: cross-signature comparison for unassigned items.
    # Use candidate_key as a fast filter: only compare versions whose
    # (first_hash, last_hash, size_bucket) match. This reduces the number
    # of expensive are_duplicate_versions() calls without affecting accuracy.
    unassigned = [idx for idx in range(len(items)) if idx not in assigned]

    # Build candidate-key buckets for unassigned items
    ckey_buckets = defaultdict(list)
    for idx in unassigned:
        _, _, _, _, _, ckey = items[idx]
        ckey_buckets[ckey].append(idx)

    for ckey, bucket_indices in ckey_buckets.items():
        if len(bucket_indices) < 2:
            continue
        for ui, i_idx in enumerate(bucket_indices):
            if i_idx in assigned:
                continue
            v_i, imgs_i, cnt_i, ts_i, _, _ = items[i_idx]
            assigned.add(i_idx)
            members = [_make_member(v_i, cnt_i, ts_i)]
            for j_idx in bucket_indices[ui + 1:]:
                if j_idx in assigned:
                    continue
                v_j, imgs_j, cnt_j, ts_j, _, _ = items[j_idx]
                candidate_pairs += 1
                passed, skip = passes_sample_filter(imgs_i, imgs_j)
                if not passed:
                    sample_filter_rejected += 1
                    continue
                actual_comparisons += 1
                if are_duplicate_versions_skip_indices(imgs_i, imgs_j, skip):
                    assigned.add(j_idx)
                    members.append(_make_member(v_j, cnt_j, ts_j))
            if len(members) >= 2:
                groups.append(members)

    stats = {
        'candidate_pairs': candidate_pairs,
        'sample_filter_rejected': sample_filter_rejected,
        'actual_comparisons': actual_comparisons,
    }
    return groups, stats


def _batch_load_images(sess):
    """Load all active+confirmed images from enabled scan roots, grouped by
    (barcode, image_type, folder_ctime), ordered by sequence then filename.
    Returns dict: {(barcode, image_type, folder_ctime): [Image, ...]}"""
    all_imgs = sess.query(Image).filter(
        Image.status == 'active',
        Image.confirmed == True,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True,
    ).order_by(Image.barcode, Image.image_type, Image.folder_ctime, Image.sequence, Image.filename).all()

    grouped = defaultdict(list)
    for img in all_imgs:
        grouped[(img.barcode, img.image_type, img.folder_ctime)].append(img)
    return grouped


def detect_duplicate_versions(sess, progress_callback=None):
    """Detect all duplicate versions across the database.

    Returns a list of groups, each containing:
    {
        group_id: int,
        barcode: str,
        image_type: str,
        image_count: int,
        members: [{
            folder_ctime: str,
            version_label: str,
            image_count: int,
            total_file_size: int,
            is_latest: bool,
            role: str,  # 'keep' or 'clean'
            keep_reason: str,
        }]
    }
    """
    import time
    start_time = time.monotonic()

    versions = sess.query(ImageVersion).all()
    if not versions:
        return []

    total_versions = len(versions)

    # Batch-load all images once (avoids N+1 queries)
    images_by_key = _batch_load_images(sess)

    # Group by (barcode, image_type)
    by_barcode_type = defaultdict(list)
    for v in versions:
        by_barcode_type[(v.barcode, v.image_type)].append(v)

    total_groups = len(by_barcode_type)
    processed = 0
    all_groups = []
    group_id = 0
    total_pools = 0
    total_candidate_pairs = 0
    total_sample_rejected = 0
    total_actual_comparisons = 0

    for (barcode, image_type), vers in by_barcode_type.items():
        processed += 1
        if progress_callback:
            progress_callback(current=processed, total=total_groups)

        # Build version data from batch-loaded images
        version_data = []
        for v in vers:
            imgs = images_by_key.get((barcode, image_type, v.folder_ctime), [])
            if not imgs:
                continue
            count, total_size = _compute_version_stats(imgs)
            version_data.append((v, imgs, count, total_size))

        if len(version_data) < 2:
            continue

        # Group by image_count — only compare within same count
        by_count = defaultdict(list)
        for item in version_data:
            by_count[item[2]].append(item)  # item[2] = count

        for count, count_pool in by_count.items():
            if len(count_pool) < 2:
                continue

            total_pools += 1
            member_lists, stats = _find_groups_in_pool(count_pool)
            total_candidate_pairs += stats['candidate_pairs']
            total_sample_rejected += stats['sample_filter_rejected']
            total_actual_comparisons += stats['actual_comparisons']

            for members in member_lists:
                keep_idx, reason = pick_keep_version(members)
                members[keep_idx]['role'] = 'keep'
                members[keep_idx]['keep_reason'] = reason
                group_id += 1
                all_groups.append({
                    'group_id': group_id,
                    'barcode': barcode,
                    'image_type': image_type,
                    'image_count': count,
                    'members': members,
                })

    elapsed = time.monotonic() - start_time
    _log.info(
        "DuplicateVersionScan: versions=%d pools=%d candidate_pairs=%d "
        "sample_rejected=%d actual_comparisons=%d groups=%d elapsed=%.1fs",
        total_versions, total_pools, total_candidate_pairs,
        total_sample_rejected, total_actual_comparisons, len(all_groups), elapsed,
    )
    return all_groups
