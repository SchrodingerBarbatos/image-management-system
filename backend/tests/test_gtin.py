"""Tests for GTIN validation function."""

import pytest
from scanner import validate_gtin, calculate_gtin_check_digit


def test_validate_gtin_valid_gtin13():
    """测试有效的 GTIN-13。"""
    is_valid, reason = validate_gtin("4006381333931")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin_valid_gtin12():
    """测试有效的 GTIN-12。"""
    is_valid, reason = validate_gtin("036000291452")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin_valid_gtin8():
    """测试有效的 GTIN-8。"""
    is_valid, reason = validate_gtin("55123457")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin_valid_gtin14():
    """测试有效的 GTIN-14。"""
    is_valid, reason = validate_gtin("15400141288763")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin_invalid_length():
    """测试长度不符合要求的条码。"""
    is_valid, reason = validate_gtin("12345")
    assert is_valid is False
    assert "长度" in reason
    assert "5" in reason


def test_validate_gtin_invalid_length_11():
    """测试长度为11的条码。"""
    is_valid, reason = validate_gtin("12345678901")
    assert is_valid is False
    assert "长度" in reason
    assert "11" in reason


def test_validate_gtin_non_digit():
    """测试包含非数字字符的条码。"""
    is_valid, reason = validate_gtin("400638133393A")
    assert is_valid is False
    assert "非数字字符" in reason


def test_validate_gtin_invalid_check_digit():
    """测试校验位错误的条码。"""
    is_valid, reason = validate_gtin("4006381333932")
    assert is_valid is False
    assert "校验位错误" in reason
    assert "期望 1" in reason
    assert "实际 2" in reason


def test_validate_gtin_with_spaces():
    """测试带空格的条码。"""
    is_valid, reason = validate_gtin(" 4006381333931 ")
    assert is_valid is True
    assert reason == ""


def test_calculate_gtin_check_digit():
    """测试计算校验位。"""
    check_digit = calculate_gtin_check_digit("400638133393")
    assert check_digit == 1


def test_calculate_gtin_check_digit_gtin12():
    """测试计算 GTIN-12 的校验位。"""
    check_digit = calculate_gtin_check_digit("03600029145")
    assert check_digit == 2
