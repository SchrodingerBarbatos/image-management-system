"""Tests for GTIN validation function."""

import pytest
from scanner import validate_gtin, validate_business_gtin, calculate_gtin_check_digit


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


# === 业务有效性校验测试（RCN 拒绝） ===


def test_validate_gtin_rejects_rcn_200_299():
    """GTIN-13 前缀 200-299 应被拒绝（限制流通码）。"""
    is_valid, reason = validate_gtin("2300000082579")
    assert is_valid is False
    assert "200–299" in reason
    assert "限制流通码" in reason


def test_validate_gtin_rejects_rcn_200_boundary():
    """GTIN-13 前缀 200 边界值应被拒绝。"""
    is_valid, reason = validate_gtin("2000000000008")
    assert is_valid is False
    assert "200–299" in reason


def test_validate_gtin_rejects_rcn_299_boundary():
    """GTIN-13 前缀 299 边界值应被拒绝。"""
    is_valid, reason = validate_gtin("2990000000002")
    assert is_valid is False
    assert "200–299" in reason


def test_validate_gtin_allows_prefix_199():
    """GTIN-13 前缀 199 应被允许。"""
    is_valid, reason = validate_gtin("1990000000003")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin_allows_prefix_300():
    """GTIN-13 前缀 300 应被允许。"""
    # 3000000000007: prefix=300, check digit=7
    is_valid, reason = validate_gtin("3000000000007")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin14_rejects_rcn_200_299():
    """GTIN-14 第2-4位为 200-299 应被拒绝。"""
    is_valid, reason = validate_gtin("12300000000099")
    assert is_valid is False
    assert "200–299" in reason


def test_validate_gtin14_allows_non_rcn():
    """GTIN-14 非 RCN 前缀应被允许。"""
    # 15400141288763: existing valid GTIN-14
    is_valid, reason = validate_gtin("15400141288763")
    assert is_valid is True
    assert reason == ""


def test_validate_upc_rejects_rcn_020_029():
    """UPC-A 前缀 020-029 应被拒绝。"""
    is_valid, reason = validate_gtin("021234567893")
    assert is_valid is False
    assert "020–029" in reason
    assert "限制流通码" in reason


def test_validate_upc_rejects_rcn_040_049():
    """UPC-A 前缀 040-049 应被拒绝。"""
    is_valid, reason = validate_gtin("041234567891")
    assert is_valid is False
    assert "040–049" in reason
    assert "企业内部流通码" in reason


def test_validate_gtin_valid_china():
    """有效中国条码（前缀 690）应通过。"""
    is_valid, reason = validate_gtin("6901234567892")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin_valid_korea():
    """有效韩国条码（前缀 880）应通过。"""
    is_valid, reason = validate_gtin("8801234567893")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin_valid_isbn():
    """有效 ISBN（前缀 978）应通过。"""
    is_valid, reason = validate_gtin("9787111123453")
    assert is_valid is True
    assert reason == ""


def test_validate_upc_allows_prefix_030():
    """UPC-A 前缀 030（029 范围之上）应被允许。"""
    is_valid, reason = validate_gtin("030000000007")
    assert is_valid is True
    assert reason == ""


def test_validate_upc_allows_prefix_050():
    """UPC-A 前缀 050（049 范围之上）应被允许。"""
    is_valid, reason = validate_gtin("050000000005")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin14_rejects_rcn_200_boundary():
    """GTIN-14 前缀 200 边界值应被拒绝。"""
    is_valid, reason = validate_gtin("12000000000005")
    assert is_valid is False
    assert "200–299" in reason


def test_validate_gtin14_rejects_rcn_299_boundary():
    """GTIN-14 前缀 299 边界值应被拒绝。"""
    is_valid, reason = validate_gtin("12990000000009")
    assert is_valid is False
    assert "200–299" in reason


def test_validate_gtin14_allows_prefix_199():
    """GTIN-14 前缀 199（边界外）应被允许。"""
    is_valid, reason = validate_gtin("11990000000000")
    assert is_valid is True
    assert reason == ""


def test_validate_gtin14_allows_prefix_300():
    """GTIN-14 前缀 300（边界外）应被允许。"""
    is_valid, reason = validate_gtin("13000000000004")
    assert is_valid is True
    assert reason == ""


# === validate_business_gtin 直接测试 ===


def test_business_gtin_rejects_rcn():
    """直接测试 validate_business_gtin 拒绝 RCN。"""
    is_valid, reason = validate_business_gtin("2300000082579")
    assert is_valid is False
    assert "限制流通码" in reason


def test_business_gtin_allows_normal():
    """直接测试 validate_business_gtin 允许正常条码。"""
    is_valid, reason = validate_business_gtin("6901234567892")
    assert is_valid is True
    assert reason == ""
