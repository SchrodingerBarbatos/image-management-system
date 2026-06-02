"""Unit tests for duplicate_version_detector module."""
import pytest
from unittest.mock import MagicMock

from duplicate_version_detector import (
    hamming_distance,
    are_same_image,
    are_duplicate_versions,
    sample_indices,
    passes_sample_filter,
    are_duplicate_versions_skip_indices,
    _version_signature,
    _build_sample_signature,
    _mi_hash_candidates,
    _find_groups_in_pool,
    pick_keep_version,
)


# ---------- Fixtures ----------

def _make_image(content_md5='', phash='', filename='img.jpg', sequence=0, file_size=1000):
    """Create a mock Image object."""
    img = MagicMock()
    img.content_md5 = content_md5
    img.phash = phash
    img.filename = filename
    img.sequence = sequence
    img.file_size = file_size
    return img


def _make_version(folder_ctime, version_label='v1', is_latest=False, content_hash=''):
    """Create a mock ImageVersion object."""
    v = MagicMock()
    v.folder_ctime = folder_ctime
    v.version_label = version_label
    v.is_latest = is_latest
    v.content_hash = content_hash
    return v


# ---------- hamming_distance ----------

class TestHammingDistance:
    def test_identical(self):
        assert hamming_distance('abc123', 'abc123') == 0

    def test_one_bit_diff(self):
        # 0x00 vs 0x01 = 1 bit difference
        assert hamming_distance('000000', '000001') == 1

    def test_all_bits_diff(self):
        # 0x00 vs 0xff = 8 bits difference per byte
        assert hamming_distance('00', 'ff') == 8

    def test_different_lengths(self):
        # Should return max distance
        result = hamming_distance('ab', 'abcd')
        assert result == 16  # max(2, 4) * 4

    def test_empty_strings(self):
        # Empty strings → max distance (treated as missing hash)
        assert hamming_distance('', '') == 0  # both empty, max(0,0)*4 = 0

    def test_one_empty(self):
        assert hamming_distance('abc', '') == 12  # max(3, 0) * 4


# ---------- are_same_image ----------

class TestAreSameImage:
    def test_md5_match(self):
        a = _make_image(content_md5='abc123')
        b = _make_image(content_md5='abc123')
        assert are_same_image(a, b) is True

    def test_md5_mismatch_phash_empty(self):
        a = _make_image(content_md5='abc123')
        b = _make_image(content_md5='def456')
        assert are_same_image(a, b) is False

    def test_phash_match_within_threshold(self):
        # hamming distance = 3 <= 5
        a = _make_image(phash='0000000000000000')
        b = _make_image(phash='0000000000000007')  # 3 bits diff
        assert are_same_image(a, b) is True

    def test_phash_mismatch_beyond_threshold(self):
        # hamming distance = 8 > 5
        a = _make_image(phash='00')
        b = _make_image(phash='ff')
        assert are_same_image(a, b) is False

    def test_both_empty(self):
        a = _make_image()
        b = _make_image()
        assert are_same_image(a, b) is False

    def test_md5_match_overrides_phash_mismatch(self):
        a = _make_image(content_md5='abc', phash='00')
        b = _make_image(content_md5='abc', phash='ff')
        assert are_same_image(a, b) is True

    def test_one_missing_md5_uses_phash(self):
        a = _make_image(content_md5='', phash='0000000000000000')
        b = _make_image(content_md5='abc', phash='0000000000000000')
        # MD5: empty vs 'abc' → no match
        # pHash: identical → match
        assert are_same_image(a, b) is True


# ---------- are_duplicate_versions ----------

class TestAreDuplicateVersions:
    def test_different_lengths(self):
        a = [_make_image(content_md5='a')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        assert are_duplicate_versions(a, b) is False

    def test_identical_single(self):
        a = [_make_image(content_md5='abc')]
        b = [_make_image(content_md5='abc')]
        assert are_duplicate_versions(a, b) is True

    def test_identical_multiple(self):
        a = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        assert are_duplicate_versions(a, b) is True

    def test_short_circuit_first_diff(self):
        a = [_make_image(content_md5='x'), _make_image(content_md5='b')]
        b = [_make_image(content_md5='y'), _make_image(content_md5='b')]
        assert are_duplicate_versions(a, b) is False

    def test_short_circuit_second_diff(self):
        a = [_make_image(content_md5='a'), _make_image(content_md5='x')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='y')]
        assert are_duplicate_versions(a, b) is False

    def test_empty_lists(self):
        assert are_duplicate_versions([], []) is True


# ---------- sample_indices ----------

class TestSampleIndices:
    def test_1_image(self):
        assert sample_indices(1) == [0]

    def test_2_images(self):
        assert sample_indices(2) == [0, 1]

    def test_3_images(self):
        assert sample_indices(3) == [0, 1, 2]

    def test_4_images(self):
        # first=0, 25%=1, mid=2, 75%=3, last=3 → dedup → [0, 1, 2, 3]
        assert sample_indices(4) == [0, 1, 2, 3]

    def test_5_images(self):
        # first=0, 25%=1, mid=2, 75%=3, last=4 → [0, 1, 2, 3, 4]
        assert sample_indices(5) == [0, 1, 2, 3, 4]

    def test_6_images(self):
        # first=0, 25%=1, mid=3, 75%=4, last=5 → [0, 1, 3, 4, 5]
        assert sample_indices(6) == [0, 1, 3, 4, 5]

    def test_9_images(self):
        # first=0, 25%=2, mid=4, 75%=6, last=8 → [0, 2, 4, 6, 8]
        assert sample_indices(9) == [0, 2, 4, 6, 8]

    def test_12_images(self):
        # first=0, 25%=3, mid=6, 75%=9, last=11 → [0, 3, 6, 9, 11]
        assert sample_indices(12) == [0, 3, 6, 9, 11]

    def test_0_images(self):
        assert sample_indices(0) == []

    def test_no_random(self):
        """Calling twice returns same result (deterministic)."""
        assert sample_indices(15) == sample_indices(15)


# ---------- passes_sample_filter ----------

class TestPassesSampleFilter:
    def test_different_lengths(self):
        a = [_make_image(content_md5='a')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        passed, matched = passes_sample_filter(a, b)
        assert passed is False
        assert matched == set()

    def test_first_image_different(self):
        a = [_make_image(content_md5='x'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        b = [_make_image(content_md5='y'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        passed, matched = passes_sample_filter(a, b)
        assert passed is False
        assert matched == set()

    def test_mid_image_different(self):
        a = [_make_image(content_md5='a'), _make_image(content_md5='x'), _make_image(content_md5='c')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='y'), _make_image(content_md5='c')]
        passed, matched = passes_sample_filter(a, b)
        assert passed is False
        assert matched == set()

    def test_last_image_different(self):
        a = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='x')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='y')]
        passed, matched = passes_sample_filter(a, b)
        assert passed is False
        assert matched == set()

    def test_sampled_same_but_others_different(self):
        """Sampled positions match but non-sampled differ → filter passes (True),
        but are_duplicate_versions would return False."""
        # 9 images: sample_indices = [0, 2, 4, 6, 8]
        # Make sampled positions match, non-sampled differ
        a = [_make_image(content_md5='a'), _make_image(content_md5='x'), _make_image(content_md5='c'),
             _make_image(content_md5='d'), _make_image(content_md5='e'), _make_image(content_md5='f'),
             _make_image(content_md5='g'), _make_image(content_md5='h'), _make_image(content_md5='i')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='DIFF'), _make_image(content_md5='c'),
             _make_image(content_md5='DIFF'), _make_image(content_md5='e'), _make_image(content_md5='DIFF'),
             _make_image(content_md5='g'), _make_image(content_md5='DIFF'), _make_image(content_md5='i')]
        passed, matched = passes_sample_filter(a, b)
        assert passed is True
        assert matched == {0, 2, 4, 6, 8}
        # But full comparison fails at index 1
        assert are_duplicate_versions(a, b) is False

    def test_matched_indices_returned(self):
        """Verify matched_indices contains correct positions."""
        a = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        passed, matched = passes_sample_filter(a, b)
        assert passed is True
        # 3 images: sample_indices = [0, 1, 2]
        assert matched == {0, 1, 2}

    def test_all_match(self):
        a = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        passed, _ = passes_sample_filter(a, b)
        assert passed is True

    def test_single_image_match(self):
        a = [_make_image(content_md5='a')]
        b = [_make_image(content_md5='a')]
        passed, _ = passes_sample_filter(a, b)
        assert passed is True

    def test_single_image_mismatch(self):
        a = [_make_image(content_md5='a')]
        b = [_make_image(content_md5='b')]
        passed, matched = passes_sample_filter(a, b)
        assert passed is False
        assert matched == set()


# ---------- are_duplicate_versions_skip_indices ----------

class TestAreDuplicateVersionsSkipIndices:
    def test_different_lengths(self):
        a = [_make_image(content_md5='a')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        assert are_duplicate_versions_skip_indices(a, b, {0}) is False

    def test_skip_all_indices_duplicates(self):
        """All indices skipped → vacuously True (all were pre-verified)."""
        a = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        assert are_duplicate_versions_skip_indices(a, b, {0, 1}) is True

    def test_skip_all_indices_different(self):
        """All indices skipped but lengths differ → False."""
        a = [_make_image(content_md5='a')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        assert are_duplicate_versions_skip_indices(a, b, {0}) is False

    def test_skip_first_check_rest_match(self):
        """Skip index 0 (pre-verified), rest match → True."""
        a = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        assert are_duplicate_versions_skip_indices(a, b, {0}) is True

    def test_skip_first_check_rest_mismatch(self):
        """Skip index 0, but index 1 differs → False."""
        a = [_make_image(content_md5='a'), _make_image(content_md5='x'), _make_image(content_md5='c')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='y'), _make_image(content_md5='c')]
        assert are_duplicate_versions_skip_indices(a, b, {0}) is False

    def test_skip_none_all_checked(self):
        """Empty skip set → checks all, same as are_duplicate_versions."""
        a = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        assert are_duplicate_versions_skip_indices(a, b, set()) is True

    def test_skip_none_mismatch(self):
        """Empty skip set, mismatch at index 0 → False."""
        a = [_make_image(content_md5='x'), _make_image(content_md5='b')]
        b = [_make_image(content_md5='y'), _make_image(content_md5='b')]
        assert are_duplicate_versions_skip_indices(a, b, set()) is False

    def test_skip_mid_check_first_and_last(self):
        """Skip middle index, check first and last."""
        a = [_make_image(content_md5='a'), _make_image(content_md5='x'), _make_image(content_md5='c')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='y'), _make_image(content_md5='c')]
        # Skip index 1 (mid), check 0 and 2 → both match → True
        assert are_duplicate_versions_skip_indices(a, b, {1}) is True

    def test_consistency_with_full_check(self):
        """When skip_indices covers all positions, result must match full check."""
        a = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        b = [_make_image(content_md5='a'), _make_image(content_md5='b'), _make_image(content_md5='c')]
        assert are_duplicate_versions_skip_indices(a, b, {0, 1, 2}) == are_duplicate_versions(a, b)

    def test_empty_lists(self):
        assert are_duplicate_versions_skip_indices([], [], set()) is True


# ---------- _version_signature ----------

class TestVersionSignature:
    def test_phash_preferred(self):
        imgs = [_make_image(content_md5='md5val', phash='phashval')]
        sig = _version_signature(imgs)
        assert sig == 'p:phashval'

    def test_md5_fallback(self):
        imgs = [_make_image(content_md5='md5val', phash='')]
        sig = _version_signature(imgs)
        assert sig == 'm:md5val'

    def test_empty_hash(self):
        imgs = [_make_image(content_md5='', phash='')]
        sig = _version_signature(imgs)
        assert sig == ''

    def test_multiple_images(self):
        imgs = [
            _make_image(phash='aaa'),
            _make_image(content_md5='bbb'),
            _make_image(),
        ]
        sig = _version_signature(imgs)
        assert sig == 'p:aaa|m:bbb|'

    def test_no_cross_type_collision(self):
        """pHash 'abc' and content_md5 'abc' should produce different signatures."""
        imgs_a = [_make_image(phash='abc')]
        imgs_b = [_make_image(content_md5='abc')]
        assert _version_signature(imgs_a) != _version_signature(imgs_b)


# ---------- _build_sample_signature ----------

class TestBuildSampleSignature:
    def test_single_position(self):
        imgs = [_make_image(phash='aaaa')]
        sig = _build_sample_signature(imgs, [0])
        assert sig == 'p:aaaa'

    def test_multiple_positions(self):
        imgs = [_make_image(phash='aaaa'), _make_image(phash='bbbb'), _make_image(phash='cccc')]
        sig = _build_sample_signature(imgs, [0, 1, 2])
        assert sig == 'p:aaaa|p:bbbb|p:cccc'

    def test_subset_positions(self):
        imgs = [_make_image(phash='aaaa'), _make_image(phash='bbbb'), _make_image(phash='cccc')]
        sig = _build_sample_signature(imgs, [0, 2])
        assert sig == 'p:aaaa|p:cccc'

    def test_missing_phash(self):
        imgs = [_make_image(phash=''), _make_image(phash='bbbb')]
        sig = _build_sample_signature(imgs, [0, 1])
        assert sig == '|p:bbbb'

    def test_out_of_range_index(self):
        imgs = [_make_image(phash='aaaa')]
        sig = _build_sample_signature(imgs, [0, 5])
        assert sig == 'p:aaaa|'

    def test_md5_fallback(self):
        imgs = [_make_image(content_md5='md5val', phash='')]
        sig = _build_sample_signature(imgs, [0])
        assert sig == 'm:md5val'


# ---------- _mi_hash_candidates ----------

class TestMiHashCandidates:
    def test_empty_input(self):
        assert _mi_hash_candidates([]) == []

    def test_single_item(self):
        assert _mi_hash_candidates([(0, 'p:aaaa')]) == []

    def test_identical_signatures(self):
        """Two items with identical signatures should be candidates in every position."""
        sig = 'p:0000000000000001|p:0000000000000001|p:0000000000000001'
        result = _mi_hash_candidates([(0, sig), (1, sig)])
        # 3 positions, each should have (0,1)
        assert len(result) == 3
        assert all((0, 1) in pos for pos in result)

    def test_very_different_signatures(self):
        """Signatures with very different pHash values should NOT be candidates."""
        sig_a = 'p:0000000000000000|p:0000000000000000|p:0000000000000000'
        sig_b = 'p:ffffffffffffffff|p:ffffffffffffffff|p:ffffffffffffffff'
        result = _mi_hash_candidates([(0, sig_a), (1, sig_b)])
        # No position should have candidates
        assert all(len(pos) == 0 for pos in result)

    def test_similar_signatures_within_threshold(self):
        """Signatures with pHash distance <= 5 should be candidates in that position."""
        sig_a = 'p:0000000000000001|p:0000000000000001|p:0000000000000001'
        sig_b = 'p:0000000000000006|p:0000000000000001|p:0000000000000001'
        result = _mi_hash_candidates([(0, sig_a), (1, sig_b)])
        # Position 0: distance 3 → candidate. Positions 1,2: identical → candidate.
        assert all((0, 1) in pos for pos in result)

    def test_missing_phash_partial(self):
        """Items with some empty segments can still be candidates from other positions."""
        sig_a = '|p:bbbb|p:cccc'
        sig_b = '|p:bbbb|p:cccc'
        result = _mi_hash_candidates([(0, sig_a), (1, sig_b)])
        # 3 positions returned (one per segment), position 0 is empty
        assert len(result) == 3
        assert len(result[0]) == 0  # position 0: empty
        assert (0, 1) in result[1]  # position 1: match
        assert (0, 1) in result[2]  # position 2: match

    def test_all_positions_empty(self):
        """Items with all empty segments produce no candidates in any position."""
        sig_a = '||'
        sig_b = '||'
        result = _mi_hash_candidates([(0, sig_a), (1, sig_b)])
        assert len(result) == 3  # 3 positions returned
        assert all(len(pos) == 0 for pos in result)

    def test_multiple_items_grouping(self):
        """Three similar items should produce 3 candidate pairs per position."""
        base = 'p:0000000000000001|p:0000000000000001|p:0000000000000001'
        close = 'p:0000000000000000|p:0000000000000001|p:0000000000000001'
        result = _mi_hash_candidates([(0, base), (1, close), (2, base)])
        # 3 positions, each should have 3 pairs
        assert len(result) == 3
        for pos in result:
            assert len(pos) == 3

    def test_boundary_distance_5(self):
        """Test at exact threshold boundary: distance = 5 bits per position."""
        sig_a = 'p:0000000000000000|p:0000000000000000|p:0000000000000000'
        sig_b = 'p:000000000000001f|p:0000000000000000|p:0000000000000000'
        result = _mi_hash_candidates([(0, sig_a), (1, sig_b)])
        assert (0, 1) in result[0]  # position 0: distance 5 → candidate
        assert (0, 1) in result[1]  # position 1: identical → candidate
        assert (0, 1) in result[2]  # position 2: identical → candidate

    def test_all_positions_max_distance(self):
        """All 3 positions at max threshold (5 bits each) should still be candidates."""
        sig_a = 'p:0000000000000000|p:0000000000000000|p:0000000000000000'
        sig_b = 'p:000000000000001f|p:000000000000001f|p:000000000000001f'
        result = _mi_hash_candidates([(0, sig_a), (1, sig_b)])
        assert all((0, 1) in pos for pos in result)


# ---------- pick_keep_version ----------

class TestPickKeepVersion:
    def test_empty(self):
        idx, reason = pick_keep_version([])
        assert idx == 0

    def test_single(self):
        members = [{'is_latest': False, 'total_file_size': 100,  'folder_ctime': '2024-01-01'}]
        idx, reason = pick_keep_version(members)
        assert idx == 0
        assert reason == '默认保留'

    def test_is_latest_priority(self):
        members = [
            {'is_latest': False, 'total_file_size': 100,  'folder_ctime': '2024-01-01'},
            {'is_latest': True, 'total_file_size': 50,  'folder_ctime': '2023-01-01'},
        ]
        idx, reason = pick_keep_version(members)
        assert idx == 1
        assert '当前主版本' in reason

    def test_total_file_size_priority(self):
        members = [
            {'is_latest': False, 'total_file_size': 100,  'folder_ctime': '2024-01-01'},
            {'is_latest': False, 'total_file_size': 200,  'folder_ctime': '2023-01-01'},
        ]
        idx, reason = pick_keep_version(members)
        assert idx == 1
        assert '总文件大小更大' in reason

    def test_folder_ctime_tiebreaker(self):
        members = [
            {'is_latest': False, 'total_file_size': 100,  'folder_ctime': '2023-01-01'},
            {'is_latest': False, 'total_file_size': 100,  'folder_ctime': '2024-06-01'},
        ]
        idx, reason = pick_keep_version(members)
        assert idx == 1
        assert '修改时间更新' in reason


# ---------- _find_groups_in_pool ----------

class TestFindGroupsInPool:
    def test_no_duplicates(self):
        """Different content → no groups."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='b')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0

    def test_two_identical(self):
        """Same content → one group with 2 members."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_three_identical(self):
        """Three identical → one group with 3 members."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t3', content_hash='h3'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_two_pairs(self):
        """Two separate duplicate pairs → two groups."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t3', content_hash='h3'), [_make_image(content_md5='b')], 1, 200),
            (_make_version('t4', content_hash='h4'), [_make_image(content_md5='b')], 1, 200),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 2
        assert all(len(g) == 2 for g in groups)

    def test_single_pool(self):
        """Pool with 1 item → no groups."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0

    def test_cross_signature_match(self):
        """Items with different signatures but matching via MD5 should be grouped.
        This happens when one has phash and the other doesn't, but MD5 matches."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='md5val', phash='phash1')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='md5val', phash='phash2')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        # Different signatures (p:phash1 vs p:phash2), but MD5 matches
        assert len(groups) == 1

    def test_multiple_images_short_circuit(self):
        """With multiple images, short-circuit on first mismatch."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a'), _make_image(content_md5='x')], 2, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='a'), _make_image(content_md5='y')], 2, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0

    def test_different_first_image_rejected_by_sample_filter(self):
        """Different first image → not candidates (different sample signatures),
        so no comparison happens at all."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='b')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0
        # Different MD5 → different sample signatures → not candidates → no comparison
        assert stats['actual_comparisons'] == 0

    def test_same_first_image_passes_sample_filter(self):
        """Same first image → sample filter passes → comparison happens."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert stats['actual_comparisons'] >= 1

    def test_stats_tracking(self):
        """Verify stats are properly tracked."""
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t3', content_hash='h3'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert len(groups[0]) == 3
        assert 'candidate_pairs' in stats
        assert 'actual_comparisons' in stats
        assert 'exact_duplicates_skipped' in stats

    def test_multi_image_sample_filter_no_false_positive(self):
        """Two versions with same sampled positions but different non-sampled positions
        must NOT be grouped together. This is the critical end-to-end test
        ensuring sample filter only排除, never误判."""
        # 6 images: sample_indices = [0, 1, 3, 4, 5]
        # Only position 2 is NOT sampled
        imgs_a = [
            _make_image(content_md5='same'),   # 0 - sampled
            _make_image(content_md5='same'),   # 1 - sampled
            _make_image(content_md5='a2'),      # 2 - NOT sampled, different
            _make_image(content_md5='same'),   # 3 - sampled
            _make_image(content_md5='same'),   # 4 - sampled
            _make_image(content_md5='same'),   # 5 - sampled
        ]
        imgs_b = [
            _make_image(content_md5='same'),   # 0 - sampled
            _make_image(content_md5='same'),   # 1 - sampled
            _make_image(content_md5='b2'),      # 2 - NOT sampled, DIFFERENT
            _make_image(content_md5='same'),   # 3 - sampled
            _make_image(content_md5='same'),   # 4 - sampled
            _make_image(content_md5='same'),   # 5 - sampled
        ]
        pool = [
            (_make_version('t1', content_hash='h1'), imgs_a, 6, 100),
            (_make_version('t2', content_hash='h2'), imgs_b, 6, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        # Must NOT be grouped — non-sampled positions differ
        assert len(groups) == 0
        # Sample filter should have passed (sampled positions match)
        # but full comparison rejected
        assert stats['sample_filter_rejected'] == 0
        assert stats['actual_comparisons'] >= 1

    def test_phash_similar_not_missed(self):
        """Two versions with different MD5 but pHash distance <= 5 must still
        be grouped. Old candidate_key logic would漏判 these because it used
        exact hash matching. Verify修复后不再漏判."""
        # phash distance 3 between '0000000000000000' and '0000000000000007'
        imgs_a = [
            _make_image(content_md5='md5_a', phash='0000000000000000'),
            _make_image(content_md5='md5_b', phash='0000000000000000'),
            _make_image(content_md5='md5_c', phash='0000000000000000'),
        ]
        imgs_b = [
            _make_image(content_md5='md5_x', phash='0000000000000007'),  # distance 3
            _make_image(content_md5='md5_y', phash='0000000000000007'),  # distance 3
            _make_image(content_md5='md5_z', phash='0000000000000007'),  # distance 3
        ]
        # Different signatures (different phash) → cross-signature path
        pool = [
            (_make_version('t1', content_hash='h1'), imgs_a, 3, 100),
            (_make_version('t2', content_hash='h2'), imgs_b, 3, 200),  # different file_size too
        ]
        groups, stats = _find_groups_in_pool(pool)
        # Must be grouped — pHash distance <= 5 means are_same_image = True
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_different_size_bucket_not_missed(self):
        """Two versions with same images but different total_file_size must
        still be grouped. Old candidate_key used size_bucket as filter."""
        imgs_a = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        imgs_b = [_make_image(content_md5='a'), _make_image(content_md5='b')]
        # Same signature → first pass handles this, but verify anyway
        pool = [
            (_make_version('t1', content_hash='h1'), imgs_a, 2, 100000),   # size_bucket = 1
            (_make_version('t2', content_hash='h2'), imgs_b, 2, 200000),   # size_bucket = 3
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_cross_signature_phash_similar_5_images(self):
        """5 images with different MD5 but all pHash within threshold.
        Sample indices = [0, 2, 4]. All sampled positions match via pHash.
        Must be grouped even though signatures differ."""
        imgs_a = [
            _make_image(content_md5='', phash='a0a0a0a0a0a0a0a0'),
            _make_image(content_md5='', phash='b0b0b0b0b0b0b0b0'),
            _make_image(content_md5='', phash='c0c0c0c0c0c0c0c0'),
            _make_image(content_md5='', phash='d0d0d0d0d0d0d0d0'),
            _make_image(content_md5='', phash='e0e0e0e0e0e0e0e0'),
        ]
        # Each phash differs by 3 bits from corresponding image in imgs_a
        imgs_b = [
            _make_image(content_md5='', phash='a0a0a0a0a0a0a0a7'),  # 3 bits diff
            _make_image(content_md5='', phash='b0b0b0b0b0b0b0b7'),
            _make_image(content_md5='', phash='c0c0c0c0c0c0c0c7'),
            _make_image(content_md5='', phash='d0d0d0d0d0d0d0d7'),
            _make_image(content_md5='', phash='e0e0e0e0e0e0e0e7'),
        ]
        pool = [
            (_make_version('t1', content_hash='h1'), imgs_a, 5, 500),
            (_make_version('t2', content_hash='h2'), imgs_b, 5, 700),
        ]
        groups, stats = _find_groups_in_pool(pool)
        # Must be grouped — all images have pHash distance <= 5
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_strong_mi_bypasses_sample_filter(self):
        """Strong MI candidates (>= 3 position hits) must bypass sample_filter.

        Scenario from barcode=6901294179608/detail:
        3 versions, 9 images each.  Sample indices = [0, 2, 4, 6, 8].
        All three versions differ ONLY at sampled position 0, so they each
        have a unique version signature (forcing the MI cross-signature path).

        pHash distances at position 0:
          A vs B: 4 bits  (within PHASH_THRESHOLD=5, passes sample_filter)
          A vs C: 4 bits  (within PHASH_THRESHOLD=5, passes sample_filter)
          B vs C: 8 bits  (> PHASH_THRESHOLD=5, sample_filter REJECTS)

        MI hash detects ALL pairs at all 5 positions (each pair shares 4+
        matching sampled positions), giving mi_hit_count = 5 >= 3 for each.

        The fix must bypass sample_filter for pair (B,C) and send it
        directly to full per-image verification with relaxed threshold
        (PHASH_STRONG_MI_THRESHOLD=8), which passes (all 9 images match).
        """
        # Three pHashes at position 0 with pairwise distances 4, 4, 8:
        # A = 0x0000000000000000 (reference)
        # B = 0x000000000000000F (4 bits in low nibble)
        # C = 0x00000000000000F0 (4 bits in next nibble, non-overlapping)
        # B vs C: distance 8 (non-overlapping bits combine)
        phash_pos0_a = '0000000000000000'
        phash_pos0_b = '000000000000000f'
        phash_pos0_c = '00000000000000f0'
        assert hamming_distance(phash_pos0_a, phash_pos0_b) == 4  # <= 5
        assert hamming_distance(phash_pos0_a, phash_pos0_c) == 4  # <= 5
        assert hamming_distance(phash_pos0_b, phash_pos0_c) == 8  # > 5, <= 8

        common = [  # positions 1-8: identical across all versions
            None,  # placeholder for position 0
            _make_image(phash='1111111111111111', sequence=1),
            _make_image(phash='2222222222222222', sequence=2),
            _make_image(phash='3333333333333333', sequence=3),
            _make_image(phash='4444444444444444', sequence=4),
            _make_image(phash='5555555555555555', sequence=5),
            _make_image(phash='6666666666666666', sequence=6),
            _make_image(phash='7777777777777777', sequence=7),
            _make_image(phash='8888888888888888', sequence=8),
        ]

        def _make_imgs(phash_pos0):
            imgs = list(common)
            imgs[0] = _make_image(phash=phash_pos0, sequence=0)
            return imgs

        pool = [
            (_make_version('t1', content_hash='h1'), _make_imgs(phash_pos0_a), 9, 900),
            (_make_version('t2', content_hash='h2'), _make_imgs(phash_pos0_b), 9, 900),
            (_make_version('t3', content_hash='h3'), _make_imgs(phash_pos0_c), 9, 900),
        ]
        groups, stats = _find_groups_in_pool(pool)
        # Must find one group with all 3 members — strong MI bypasses sample_filter
        assert len(groups) == 1
        assert len(groups[0]) == 3
        assert stats['strong_mi_bypassed'] >= 1  # at least (B,C) pair bypassed
        assert stats['actual_comparisons'] >= 2

    def test_strong_mi_bypass_required_for_grouping(self):
        """Without the MI bypass, this pair would be rejected by sample_filter.

        2 versions, 9 images.  Position 0 has distance 8 (> PHASH_THRESHOLD=5).
        All other positions identical.  sample_filter rejects the pair at
        position 0, so without the bypass no group would form.

        With the bypass, the pair has mi_hit_count = 4 (positions 2, 4, 6, 8)
        >= MI_STRONG_HITS_THRESHOLD = 3, so it's sent directly to full
        verification with PHASH_STRONG_MI_THRESHOLD = 8, which passes.
        """
        phash_b = '000000000000000f'
        phash_c = '00000000000000f0'
        assert hamming_distance(phash_b, phash_c) == 8  # > 5, <= 8

        common = [
            _make_image(phash='1111111111111111', sequence=1),
            _make_image(phash='2222222222222222', sequence=2),
            _make_image(phash='3333333333333333', sequence=3),
            _make_image(phash='4444444444444444', sequence=4),
            _make_image(phash='5555555555555555', sequence=5),
            _make_image(phash='6666666666666666', sequence=6),
            _make_image(phash='7777777777777777', sequence=7),
            _make_image(phash='8888888888888888', sequence=8),
        ]

        imgs_b = [_make_image(phash=phash_b, sequence=0)] + list(common)
        imgs_c = [_make_image(phash=phash_c, sequence=0)] + list(common)

        pool = [
            (_make_version('t1', content_hash='h1'), imgs_b, 9, 900),
            (_make_version('t2', content_hash='h2'), imgs_c, 9, 900),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert len(groups[0]) == 2
        assert stats['strong_mi_bypassed'] >= 1
        assert stats['actual_comparisons'] >= 1

    def test_strong_mi_rejected_above_threshold(self):
        """Pair with distance 9 (> PHASH_STRONG_MI_THRESHOLD=8) must be
        rejected even with strong MI evidence.

        This is the boundary test: distance 8 passes, distance 9 does not.
        """
        # Distance 9: B and C differ by 9 bits (4 + 4 + 1)
        phash_b = '000000000000000f'   # 4 bits
        phash_c = '00000000000001f0'   # 5 bits (4 + 1), non-overlapping with B
        assert hamming_distance(phash_b, phash_c) == 9  # > 8

        common = [
            _make_image(phash='1111111111111111', sequence=1),
            _make_image(phash='2222222222222222', sequence=2),
            _make_image(phash='3333333333333333', sequence=3),
            _make_image(phash='4444444444444444', sequence=4),
            _make_image(phash='5555555555555555', sequence=5),
            _make_image(phash='6666666666666666', sequence=6),
            _make_image(phash='7777777777777777', sequence=7),
            _make_image(phash='8888888888888888', sequence=8),
        ]

        imgs_b = [_make_image(phash=phash_b, sequence=0)] + list(common)
        imgs_c = [_make_image(phash=phash_c, sequence=0)] + list(common)

        pool = [
            (_make_version('t1', content_hash='h1'), imgs_b, 9, 900),
            (_make_version('t2', content_hash='h2'), imgs_c, 9, 900),
        ]
        groups, stats = _find_groups_in_pool(pool)
        # Distance 9 > PHASH_STRONG_MI_THRESHOLD=8 → must NOT be grouped
        assert len(groups) == 0


# ---------- exact duplicate skipping ----------

class TestExactDuplicateSkipping:
    def test_same_content_hash_skipped(self):
        """Versions with identical content_hash are exact duplicates and should
        be skipped (handled by duplicate-folder feature)."""
        pool = [
            (_make_version('t1', content_hash='same_hash'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='same_hash'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0
        assert stats['exact_duplicates_skipped'] == 2

    def test_different_content_hash_not_skipped(self):
        """Versions with different content_hash should still be compared."""
        pool = [
            (_make_version('t1', content_hash='hash_a'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='hash_b'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert stats['exact_duplicates_skipped'] == 0

    def test_empty_content_hash_not_skipped(self):
        """Versions with empty content_hash should not be treated as exact duplicates."""
        pool = [
            (_make_version('t1', content_hash=''), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash=''), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        # Empty content_hash → not treated as exact duplicate → still compared
        assert len(groups) == 1
        assert stats['exact_duplicates_skipped'] == 0

    def test_mixed_content_hash_groups(self):
        """Mix of exact duplicates and perceptual duplicates."""
        # 3 versions: t1 and t2 have same content_hash, t3 has different
        # t1 and t3 are perceptual duplicates (same MD5)
        pool = [
            (_make_version('t1', content_hash='same'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='same'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t3', content_hash='diff'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        # t1 and t2 skipped (exact duplicates). t3 is unassigned but alone.
        assert stats['exact_duplicates_skipped'] == 2

    def test_multiple_duplicate_groups_same_barcode(self):
        """Multiple duplicate groups under same barcode/type but different content_hash."""
        # Group 1: t1, t2 with same MD5
        # Group 2: t3, t4 with same MD5 (different from group 1)
        pool = [
            (_make_version('t1', content_hash='h1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2', content_hash='h2'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t3', content_hash='h3'), [_make_image(content_md5='b')], 1, 200),
            (_make_version('t4', content_hash='h4'), [_make_image(content_md5='b')], 1, 200),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 2
        assert all(len(g) == 2 for g in groups)
