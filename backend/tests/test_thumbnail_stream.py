"""Tests for thumbnail module — stream MD5 computation."""

import os
import hashlib
import tempfile
import pytest

import thumbnail


def test_stream_md5_matches_hashlib():
    """_stream_md5 should produce the same result as hashlib.md5(data).hexdigest()."""
    # Create a temporary file with known content
    content = b"Hello, World! " * 1000  # ~14KB — spans multiple 8KB chunks
    expected_md5 = hashlib.md5(content).hexdigest()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
        f.write(content)
        tmp_path = f.name

    try:
        result = thumbnail._stream_md5(tmp_path)
        assert result == expected_md5
    finally:
        os.unlink(tmp_path)


def test_stream_md5_empty_file():
    """Empty file should return the MD5 of empty content."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
        tmp_path = f.name

    try:
        result = thumbnail._stream_md5(tmp_path)
        assert result == hashlib.md5(b"").hexdigest()
    finally:
        os.unlink(tmp_path)


def test_stream_md5_missing_file():
    """Missing file should return empty string."""
    result = thumbnail._stream_md5("/nonexistent/path/file.jpg")
    assert result == ""


def test_stream_md5_large_file():
    """Large file should be handled without loading entirely into memory."""
    # 1MB file
    content = os.urandom(1024 * 1024)
    expected_md5 = hashlib.md5(content).hexdigest()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
        f.write(content)
        tmp_path = f.name

    try:
        result = thumbnail._stream_md5(tmp_path)
        assert result == expected_md5
    finally:
        os.unlink(tmp_path)


def test_stream_md5_multichunk():
    """File larger than chunk size triggers multiple reads."""
    chunk_size = thumbnail._MD5_CHUNK_SIZE  # 8192
    # 3 chunks + partial
    content = os.urandom(chunk_size * 3 + 123)
    expected_md5 = hashlib.md5(content).hexdigest()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
        f.write(content)
        tmp_path = f.name

    try:
        result = thumbnail._stream_md5(tmp_path)
        assert result == expected_md5
    finally:
        os.unlink(tmp_path)
