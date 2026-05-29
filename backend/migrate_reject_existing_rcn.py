import argparse

from models import session, Image, ImageVersion, RejectedBarcode
from scanner import validate_gtin


def main(apply: bool):
    images = session.query(Image).all()

    targets = []
    for img in images:
        is_valid, reason = validate_gtin(img.barcode)
        if not is_valid:
            targets.append((img, reason))

    print(f"Found {len(targets)} invalid / restricted GTIN images")

    for img, reason in targets:
        print(f"[REJECT] {img.barcode} | {img.filename} | {reason}")

        if not apply:
            continue

        exists = session.query(RejectedBarcode).filter(
            RejectedBarcode.file_path == img.file_path,
            RejectedBarcode.barcode == img.barcode,
        ).first()

        if not exists:
            session.add(RejectedBarcode(
                barcode=img.barcode,
                file_path=img.file_path,
                filename=img.filename,
                reason=reason,
                scan_root_id=img.scan_root_id,
            ))

        session.query(ImageVersion).filter(
            ImageVersion.barcode == img.barcode,
            ImageVersion.image_type == img.image_type,
        ).delete(synchronize_session=False)

        session.delete(img)

    if apply:
        session.commit()
        print(f"Applied. Moved {len(targets)} images to RejectedBarcode.")
    else:
        session.rollback()
        print("Dry run only. Run with --apply to modify database.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    main(apply=args.apply)
