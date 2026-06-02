"""Duplicate version detection algorithm.

Detects perceptual duplicate versions: versions where files are different but
images are visually equivalent due to recompression, resizing, or re-exporting.

Uses multi-index hashing on sampled pHash positions to find candidate pairs
efficiently, avoiding O(n²) all-pairs comparison.

Exact duplicate content (identical content_hash) is skipped — that is already
handled by the separate duplicate-folder feature.
"""
import logging
from collections import defaultdict
from models import Image, ImageVersion, ScanRoot

_log = logging.getLogger(__name__)

# pHash hamming distance threshold for visual similarity
PHASH_THRESHOLD = 5

# Multi-index hashing parameters (per position)
# Split 64-bit pHash into 8 × 8-bit chunks.
# With threshold 5: worst case 1 bit in 5 chunks → 3 chunks match exactly.
_MI_NUM_CHUNKS = 8          # number of chunks per hash
_MI_MIN_CHUNKS = max(1, _MI_NUM_CHUNKS - PHASH_THRESHOLD)  # = 3


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
    Samples: first (0), 25%, middle (count // 2), 75%, last (count - 1).
    25% and 75% are included when count >= 4.
    Deduplicated and sorted. No random sampling.
    """
    if count <= 0:
        return []
    indices = {0, count // 2, count - 1}
    if count >= 4:
        indices.add(count // 4)
        indices.add(3 * count // 4)
    return sorted(indices)


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


def _build_sample_signature(images, indices):
    """Build a lightweight signature from sampled positions.
    Uses pHash (preferred) or content_md5 (fallback) at each position.
    Prefixes with 'p:' or 'm:' to avoid cross-type collisions.
    Returns a '|' joined string. Missing hash produces an empty segment.
    """
    parts = []
    for idx in indices:
        if idx < len(images):
            img = images[idx]
            if img.phash:
                parts.append(f'p:{img.phash}')
            elif img.content_md5:
                parts.append(f'm:{img.content_md5}')
            else:
                parts.append('')
        else:
            parts.append('')
    return '|'.join(parts)


def _parse_single_hash(segment):
    """Parse a single 'p:hex' or 'm:hex' segment.
    Returns (hash_type, int_value, bit_width) or (None, None, 0) if invalid.
    hash_type is 'p' for pHash or 'm' for MD5.
    """
    if not segment or ':' not in segment:
        return None, None, 0
    hash_type, hex_val = segment.split(':', 1)
    if not hex_val:
        return None, None, 0
    try:
        val = int(hex_val, 16)
    except (ValueError, OverflowError):
        return None, None, 0
    return hash_type, val, len(hex_val) * 4


def _mi_hash_candidates_for_group(group_data, bit_width):
    """Multi-index hashing for a single typed group at one position.

    All entries in group_data must have the same bit_width.

    Args:
        group_data: list of (idx, hash_value) tuples
        bit_width: number of bits per hash value

    Returns:
        set of (i, j) pairs with i < j
    """
    if len(group_data) < 2 or bit_width < _MI_NUM_CHUNKS:
        return set()

    chunk_size = max(1, bit_width // _MI_NUM_CHUNKS)

    # Build per-chunk dictionaries
    chunk_dicts = []
    for chunk_pos in range(_MI_NUM_CHUNKS):
        shift = bit_width - (chunk_pos + 1) * chunk_size
        if shift < 0:
            shift = 0
        mask = (1 << chunk_size) - 1
        d = defaultdict(list)
        for idx, val in group_data:
            chunk_val = (val >> shift) & mask
            d[chunk_val].append(idx)
        chunk_dicts.append(d)

    # Count exact chunk matches per pair
    pair_counts = defaultdict(int)
    for d in chunk_dicts:
        for chunk_val, indices in d.items():
            if len(indices) < 2:
                continue
            for ii in range(len(indices)):
                for jj in range(ii + 1, len(indices)):
                    i, j = indices[ii], indices[jj]
                    if i > j:
                        i, j = j, i
                    pair_counts[(i, j)] += 1

    candidates = {pair for pair, cnt in pair_counts.items() if cnt >= _MI_MIN_CHUNKS}
    return candidates


def _mi_hash_candidates_for_position(position_data):
    """Multi-index hashing for a single sampled position.

    Splits entries by hash_type and bit_width so that pHash and MD5 values
    are never compared across types. Each typed group is processed
    independently with its own chunk_size.

    Args:
        position_data: list of (hash_type, idx, hash_value, bit_width) tuples

    Returns:
        set of (i, j) pairs with i < j
    """
    if len(position_data) < 2:
        return set()

    # Group by (hash_type, bit_width) to never mix pHash and MD5
    groups = defaultdict(list)
    for hash_type, idx, val, bit_width in position_data:
        groups[(hash_type, bit_width)].append((idx, val))

    all_candidates = set()
    for (hash_type, bit_width), group_data in groups.items():
        all_candidates.update(_mi_hash_candidates_for_group(group_data, bit_width))

    return all_candidates


def _mi_hash_candidates(sample_sigs):
    """Multi-index hashing to find candidate pairs within hamming distance.

    Runs MI hash independently per sampled position with per-position threshold.
    Returns per-position candidate sets for multi-position filtering.

    Args:
        sample_sigs: list of (idx, sig_string) where sig is '|' joined 'p:hex' or 'm:hex'

    Returns:
        list of set, one per sampled position. Each set contains (i, j) pairs with i < j.
    """
    if len(sample_sigs) < 2:
        return []

    # Parse into per-position data
    num_positions = len(sample_sigs[0][1].split('|'))
    per_position = []

    for pos in range(num_positions):
        position_data = []
        for idx, sig in sample_sigs:
            segments = sig.split('|')
            if pos >= len(segments):
                continue
            hash_type, val, bit_width = _parse_single_hash(segments[pos])
            if hash_type is not None and bit_width > 0:
                position_data.append((hash_type, idx, val, bit_width))

        pos_candidates = _mi_hash_candidates_for_position(position_data)
        per_position.append(pos_candidates)

    return per_position


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


def _find_groups_in_pool(pool):
    """Given a list of (version, images, count, total_size) tuples,
    find duplicate groups using:
    - Exact content_hash duplicate skipping (handled by duplicate-folder)
    - Signature bucketing for exact matches
    - Multi-index hashing on sampled positions for perceptual candidates
    - Multi-position filtering (require hits on multiple positions)
    - Sample filter + full comparison for verification

    Returns (groups, stats) where groups is a list of member-lists
    and stats tracks comparison counts.
    """
    if len(pool) < 2:
        return [], {
            'candidate_pairs': 0, 'raw_mi_candidates': 0,
            'sample_filter_rejected': 0, 'actual_comparisons': 0,
            'exact_duplicates_skipped': 0,
        }

    # Pre-compute signatures and content hashes
    items = []
    for v, imgs, cnt, ts in pool:
        sig = _version_signature(imgs)
        content_hash = getattr(v, 'content_hash', '') or ''
        items.append((v, imgs, cnt, ts, sig, content_hash))

    assigned = set()
    groups = []
    candidate_pairs = 0
    raw_mi_candidates = 0
    sample_filter_rejected = 0
    actual_comparisons = 0
    exact_duplicates_skipped = 0
    image_count = items[0][2]

    # --- Step 0: Skip exact content_hash duplicates ---
    ch_groups = defaultdict(list)
    for idx, (v, imgs, cnt, ts, sig, ch) in enumerate(items):
        if ch:
            ch_groups[ch].append(idx)

    for ch, ch_indices in ch_groups.items():
        if len(ch_indices) >= 2:
            for idx in ch_indices:
                assigned.add(idx)
            exact_duplicates_skipped += len(ch_indices)

    # --- Pass 1: group by exact signature (fast path) ---
    sig_buckets = defaultdict(list)
    for idx, (v, imgs, cnt, ts, sig, ch) in enumerate(items):
        if idx not in assigned:
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

    # --- Pass 2: multi-index hashing for cross-signature candidates ---
    unassigned = [idx for idx in range(len(items)) if idx not in assigned]
    if len(unassigned) < 2:
        stats = {
            'candidate_pairs': candidate_pairs, 'raw_mi_candidates': raw_mi_candidates,
            'sample_filter_rejected': sample_filter_rejected,
            'actual_comparisons': actual_comparisons,
            'exact_duplicates_skipped': exact_duplicates_skipped,
        }
        return groups, stats

    # Build sample signatures for multi-index hashing (pHash-based)
    sample_idxs = sample_indices(image_count)
    sample_sigs = []
    for idx in unassigned:
        imgs = items[idx][1]
        sig = _build_sample_signature(imgs, sample_idxs)
        sample_sigs.append((idx, sig))

    # Find candidate pairs via multi-index hashing (per-position)
    per_position_candidates = _mi_hash_candidates(sample_sigs)
    num_positions = len(per_position_candidates)

    # Multi-position filter: count how many positions each pair is a candidate in
    # image_count <= 3: require >= 1 position hit
    # image_count >= 4: require >= 2 position hits
    min_position_hits = 2 if image_count >= 4 else 1
    pair_position_hits = defaultdict(int)
    for pos_candidates in per_position_candidates:
        raw_mi_candidates += len(pos_candidates)
        for pair in pos_candidates:
            pair_position_hits[pair] += 1

    mi_candidates = {pair for pair, hits in pair_position_hits.items()
                     if hits >= min_position_hits}
    candidate_pairs = len(mi_candidates)

    # Build adjacency list from filtered MI hash candidates
    adj = defaultdict(set)
    for i, j in mi_candidates:
        adj[i].add(j)
        adj[j].add(i)

    # Also group by sampled MD5 values to catch cross-signature matches
    md5_sample_buckets = defaultdict(list)
    for idx in unassigned:
        imgs = items[idx][1]
        md5_parts = []
        for si in sample_idxs:
            if si < len(imgs) and imgs[si].content_md5:
                md5_parts.append(imgs[si].content_md5)
            else:
                md5_parts.append('')
        md5_key = '|'.join(md5_parts)
        if md5_key and '' not in md5_parts:
            md5_sample_buckets[md5_key].append(idx)

    # Add MD5-based candidates to adjacency list
    for md5_key, bucket_indices in md5_sample_buckets.items():
        if len(bucket_indices) < 2:
            continue
        for ii in range(len(bucket_indices)):
            for jj in range(ii + 1, len(bucket_indices)):
                i, j = bucket_indices[ii], bucket_indices[jj]
                if i > j:
                    i, j = j, i
                adj[i].add(j)
                adj[j].add(i)
                candidate_pairs += 1

    # Process candidates: verify and group
    remaining = set(unassigned)
    for i_idx in unassigned:
        if i_idx not in remaining:
            continue
        v_i, imgs_i, cnt_i, ts_i, _, _ = items[i_idx]
        remaining.discard(i_idx)
        assigned.add(i_idx)
        members = [_make_member(v_i, cnt_i, ts_i)]

        for j_idx in sorted(adj.get(i_idx, set())):
            if j_idx not in remaining:
                continue
            v_j, imgs_j, cnt_j, ts_j, _, _ = items[j_idx]
            passed, skip = passes_sample_filter(imgs_i, imgs_j)
            if not passed:
                sample_filter_rejected += 1
                continue
            actual_comparisons += 1
            if are_duplicate_versions_skip_indices(imgs_i, imgs_j, skip):
                remaining.discard(j_idx)
                assigned.add(j_idx)
                members.append(_make_member(v_j, cnt_j, ts_j))

        if len(members) >= 2:
            groups.append(members)

    stats = {
        'candidate_pairs': candidate_pairs, 'raw_mi_candidates': raw_mi_candidates,
        'sample_filter_rejected': sample_filter_rejected,
        'actual_comparisons': actual_comparisons,
        'exact_duplicates_skipped': exact_duplicates_skipped,
    }
    return groups, stats


def _load_images_for_group(sess, barcode, image_type):
    """Load active+confirmed images for one (barcode, image_type) group,
    grouped by folder_ctime, ordered by sequence then filename.
    Returns dict: {folder_ctime: [Image, ...]}"""
    imgs = sess.query(Image).filter(
        Image.barcode == barcode,
        Image.image_type == image_type,
        Image.status == 'active',
        Image.confirmed == True,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True,
    ).order_by(Image.folder_ctime, Image.sequence, Image.filename).all()

    grouped = defaultdict(list)
    for img in imgs:
        grouped[img.folder_ctime].append(img)
    return grouped


def detect_duplicate_versions(sess, progress_callback=None):
    """Detect all duplicate versions across the database.

    Streams versions per (barcode, image_type) group to avoid loading all
    ImageVersion rows into memory at once.

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

    # Query distinct (barcode, image_type) groups — no full table load
    distinct_groups = sess.query(
        ImageVersion.barcode, ImageVersion.image_type
    ).distinct().all()

    if not distinct_groups:
        return []

    total_groups = len(distinct_groups)
    processed = 0
    all_groups = []
    group_id = 0
    total_versions = 0
    total_pools = 0
    total_candidate_pairs = 0
    total_raw_mi_candidates = 0
    total_sample_rejected = 0
    total_actual_comparisons = 0
    total_exact_duplicates_skipped = 0
    max_versions_in_group = 0
    max_images_in_group = 0
    last_progress_time = start_time

    for barcode, image_type in distinct_groups:
        # Load versions for this specific (barcode, image_type) group
        vers = sess.query(ImageVersion).filter(
            ImageVersion.barcode == barcode,
            ImageVersion.image_type == image_type,
        ).all()

        total_versions += len(vers)
        if len(vers) > max_versions_in_group:
            max_versions_in_group = len(vers)

        processed += 1

        # Throttle progress updates: every 5 groups or every 1 second
        now = time.monotonic()
        if progress_callback and (processed % 5 == 0 or (now - last_progress_time) >= 1.0):
            progress_callback(current=processed, total=total_groups)
            last_progress_time = now

        # Load images only for this barcode/image_type group
        imgs_by_ctime = _load_images_for_group(sess, barcode, image_type)
        group_img_count = sum(len(v) for v in imgs_by_ctime.values())
        if group_img_count > max_images_in_group:
            max_images_in_group = group_img_count

        # Build version_data from per-group images
        version_data = []
        for v in vers:
            imgs = imgs_by_ctime.get(v.folder_ctime, [])
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
            total_raw_mi_candidates += stats.get('raw_mi_candidates', 0)
            total_sample_rejected += stats['sample_filter_rejected']
            total_actual_comparisons += stats['actual_comparisons']
            total_exact_duplicates_skipped += stats.get('exact_duplicates_skipped', 0)

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

    # Final progress update
    if progress_callback:
        progress_callback(current=total_groups, total=total_groups)

    elapsed = time.monotonic() - start_time
    _log.info(
        "DuplicateVersionScan: versions=%d groups=%d pools=%d "
        "raw_mi_candidates=%d filtered_candidates=%d "
        "sample_rejected=%d actual_comparisons=%d "
        "exact_skipped=%d max_versions=%d max_imgs=%d "
        "duplicate_groups=%d elapsed=%.1fs",
        total_versions, total_groups, total_pools,
        total_raw_mi_candidates, total_candidate_pairs,
        total_sample_rejected, total_actual_comparisons,
        total_exact_duplicates_skipped, max_versions_in_group,
        max_images_in_group, len(all_groups), elapsed,
    )
    return all_groups
