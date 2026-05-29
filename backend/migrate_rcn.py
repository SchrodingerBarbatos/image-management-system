"""迁移脚本：将已入库的 RCN（限制流通码）条码从 Image 表移至 RejectedBarcode 表。

运行方式：
    cd backend
    python migrate_rcn.py

可选参数：
    --dry-run   仅预览受影响记录，不执行删除
"""

import sys
from models import session, Image, ImageVersion, RejectedBarcode
from scanner import validate_business_gtin


RCN_REASON = {
    13: "GS1 200–299 为限制流通码（Restricted Circulation Number）",
    14: "GS1 200–299 为限制流通码（Restricted Circulation Number）",
    12: None,  # 需要进一步判断 020-029 还是 040-049
}


def get_rcn_reason(barcode: str) -> str | None:
    """返回 RCN 拒绝原因，非 RCN 返回 None。"""
    is_valid, reason = validate_business_gtin(barcode)
    if not is_valid:
        return reason
    return None


def migrate(dry_run: bool = False):
    # 查找所有 status=active 的 Image 记录
    images = session.query(Image).filter(Image.status == 'active').all()
    rcn_images = []

    for img in images:
        reason = get_rcn_reason(img.barcode)
        if reason:
            rcn_images.append((img, reason))

    if not rcn_images:
        print("没有发现已入库的 RCN 条码记录。")
        return

    print(f"发现 {len(rcn_images)} 条 RCN 记录：")
    for img, reason in rcn_images:
        print(f"  {img.barcode} | {img.file_path} | {reason}")

    if dry_run:
        print("\n--dry-run 模式，未执行任何操作。")
        return

    # 按 barcode 分组，用于清理 ImageVersion
    affected_barcodes = set()

    for img, reason in rcn_images:
        rejected = RejectedBarcode(
            barcode=img.barcode,
            file_path=img.file_path,
            filename=img.filename,
            reason=reason,
            scan_root_id=img.scan_root_id,
        )
        session.add(rejected)
        affected_barcodes.add(img.barcode)
        session.delete(img)

    # 清理关联的 ImageVersion 记录
    if affected_barcodes:
        deleted_versions = session.query(ImageVersion).filter(
            ImageVersion.barcode.in_(list(affected_barcodes))
        ).delete(synchronize_session=False)
        print(f"同时清理了 {deleted_versions} 条关联的 ImageVersion 记录。")

    session.commit()
    print(f"\n迁移完成：已将 {len(rcn_images)} 条 RCN 记录移至 RejectedBarcode 表。")


if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    migrate(dry_run=dry_run)
