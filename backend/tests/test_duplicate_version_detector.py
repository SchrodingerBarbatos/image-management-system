"""Unit tests for duplicate_version_detector module."""
import pytest
from unittest.mock import MagicMock

from duplicate_version_detector import (
    hamming_distance,
    are_same_image,
    are_duplicate_versions,
    _version_signature,
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


def _make_version(folder_ctime, version_label='v1', is_latest=False):
    """Create a mock ImageVersion object."""
    v = MagicMock()
    v.folder_ctime = folder_ctime
    v.version_label = version_label
    v.is_latest = is_latest
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
            (_make_version('t1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2'), [_make_image(content_md5='b')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0

    def test_two_identical(self):
        """Same content → one group with 2 members."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_three_identical(self):
        """Three identical → one group with 3 members."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t3'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_two_pairs(self):
        """Two separate duplicate pairs → two groups."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t3'), [_make_image(content_md5='b')], 1, 200),
            (_make_version('t4'), [_make_image(content_md5='b')], 1, 200),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 2
        assert all(len(g) == 2 for g in groups)

    def test_single_pool(self):
        """Pool with 1 item → no groups."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0

    def test_cross_signature_match(self):
        """Items with different signatures but matching via MD5 should be grouped.
        This happens when one has phash and the other doesn't, but MD5 matches."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='md5val', phash='phash1')], 1, 100),
            (_make_version('t2'), [_make_image(content_md5='md5val', phash='phash2')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        # Different signatures (p:phash1 vs p:phash2), but MD5 matches
        assert len(groups) == 1

    def test_multiple_images_short_circuit(self):
        """With multiple images, short-circuit on first mismatch."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='a'), _make_image(content_md5='x')], 2, 100),
            (_make_version('t2'), [_make_image(content_md5='a'), _make_image(content_md5='y')], 2, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0

    def test_candidate_key_filters_different_first_image(self):
        """Different first image hash → candidate_key mismatch → no comparison."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2'), [_make_image(content_md5='b')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 0
        # candidate_pairs should be 0 because candidate_key filters them out
        assert stats['candidate_pairs'] == 0

    def test_candidate_key_allows_matching_first_image(self):
        """Same first image hash → candidate_key matches → comparison happens."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert stats['actual_comparisons'] >= 1

    def test_stats_tracking(self):
        """Verify stats are properly tracked."""
        pool = [
            (_make_version('t1'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t2'), [_make_image(content_md5='a')], 1, 100),
            (_make_version('t3'), [_make_image(content_md5='a')], 1, 100),
        ]
        groups, stats = _find_groups_in_pool(pool)
        assert len(groups) == 1
        assert len(groups[0]) == 3
        assert 'candidate_pairs' in stats
        assert 'actual_comparisons' in stats
