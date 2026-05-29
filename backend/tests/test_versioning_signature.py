"""Tests for versioning signature-based dedup optimization."""

import pytest
from unittest.mock import MagicMock

from versioning import _group_signature, groups_are_identical


def _make_mock_image(filename, file_size, content_md5="md5_abc"):
    """Create a minimal mock Image for testing signatures."""
    img = MagicMock()
    img.filename = filename
    img.file_size = file_size
    img.content_md5 = content_md5
    img.md5_hash = "fallback_hash"
    img.file_path = f"/fake/{filename}"
    return img


class TestGroupSignature:
    """Tests for _group_signature fast dedup."""

    def test_identical_groups_same_signature(self):
        imgs1 = [_make_mock_image("a.jpg", 100, "md5_a"), _make_mock_image("b.jpg", 200, "md5_b")]
        imgs2 = [_make_mock_image("a.jpg", 100, "md5_a"), _make_mock_image("b.jpg", 200, "md5_b")]
        assert _group_signature(imgs1) == _group_signature(imgs2)

    def test_order_independent(self):
        """Signature should be independent of input order (sorted internally)."""
        imgs1 = [_make_mock_image("a.jpg", 100), _make_mock_image("b.jpg", 200)]
        imgs2 = [_make_mock_image("b.jpg", 200), _make_mock_image("a.jpg", 100)]
        assert _group_signature(imgs1) == _group_signature(imgs2)

    def test_different_content_different_signature(self):
        imgs1 = [_make_mock_image("a.jpg", 100, "md5_x")]
        imgs2 = [_make_mock_image("a.jpg", 100, "md5_y")]
        assert _group_signature(imgs1) != _group_signature(imgs2)

    def test_different_size_different_signature(self):
        imgs1 = [_make_mock_image("a.jpg", 100)]
        imgs2 = [_make_mock_image("a.jpg", 200)]
        assert _group_signature(imgs1) != _group_signature(imgs2)

    def test_different_filename_different_signature(self):
        imgs1 = [_make_mock_image("a.jpg", 100)]
        imgs2 = [_make_mock_image("b.jpg", 100)]
        assert _group_signature(imgs1) != _group_signature(imgs2)

    def test_different_count_different_signature(self):
        imgs1 = [_make_mock_image("a.jpg", 100)]
        imgs2 = [_make_mock_image("a.jpg", 100), _make_mock_image("b.jpg", 200)]
        assert _group_signature(imgs1) != _group_signature(imgs2)

    def test_uses_content_md5_when_available(self):
        """Should prefer content_md5 over md5_hash."""
        img1 = _make_mock_image("a.jpg", 100, content_md5="real_md5")
        img2 = _make_mock_image("a.jpg", 100, content_md5="real_md5")
        sig1 = _group_signature([img1])
        sig2 = _group_signature([img2])
        assert sig1 == sig2
        # Verify the signature contains the real md5
        assert "real_md5" in sig1[0]

    def test_falls_back_to_md5_hash(self):
        """When content_md5 is empty, should fall back to md5_hash."""
        img1 = _make_mock_image("a.jpg", 100, content_md5="")
        img1.md5_hash = "fallback"
        sig = _group_signature([img1])
        assert "fallback" in sig[0]


class TestGroupsAreIdenticalWithSignature:
    """Integration test: signature dedup + groups_are_identical should produce
    the same result as groups_are_identical alone."""

    def test_signature_match_implies_groups_identical(self):
        """When signatures match, groups_are_identical should also return True
        (assuming content_md5 is available)."""
        imgs1 = [_make_mock_image("a.jpg", 100, "md5_a"), _make_mock_image("b.jpg", 200, "md5_b")]
        imgs2 = [_make_mock_image("a.jpg", 100, "md5_a"), _make_mock_image("b.jpg", 200, "md5_b")]

        sig1 = _group_signature(imgs1)
        sig2 = _group_signature(imgs2)
        assert sig1 == sig2
        assert groups_are_identical(imgs1, imgs2) is True

    def test_signature_mismatch_means_not_identical(self):
        """When signatures differ, groups_are_identical should return False."""
        imgs1 = [_make_mock_image("a.jpg", 100, "md5_a")]
        imgs2 = [_make_mock_image("a.jpg", 100, "md5_b")]

        sig1 = _group_signature(imgs1)
        sig2 = _group_signature(imgs2)
        assert sig1 != sig2
        assert groups_are_identical(imgs1, imgs2) is False
