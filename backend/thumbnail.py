import os, hashlib, io, logging
from PIL import Image as PILImage
from config import THUMBNAIL_DIR, THUMBNAIL_SIZE, THUMBNAIL_QUALITY

logger = logging.getLogger(__name__)


def get_thumbnail_path(image_id):
    return os.path.join(THUMBNAIL_DIR, f'{image_id}.jpg')


def thumbnail_exists(image_id):
    return os.path.exists(get_thumbnail_path(image_id))


def generate_thumbnail(image_id, source_path):
    """Generate a 200x200 thumbnail and compute MD5 from the same file read.
    Returns (success: bool, md5: str). md5 is '' if the file cannot be read."""
    thumb_path = get_thumbnail_path(image_id)
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)

    try:
        with open(source_path, 'rb') as f:
            data = f.read()
    except OSError:
        return False, ''

    md5_hash = hashlib.md5(data).hexdigest()

    try:
        img = PILImage.open(io.BytesIO(data))
        orig_mode = img.mode

        # thumbnail() before convert() so JPEG draft mode can decode at reduced resolution
        img.thumbnail(THUMBNAIL_SIZE, PILImage.LANCZOS)

        del data

        # detect transparency from original mode after thumbnail is already small
        has_transparency = orig_mode in ('RGBA', 'LA', 'PA') or (
            orig_mode == 'P' and 'transparency' in img.info
        )

        offset = (
            (THUMBNAIL_SIZE[0] - img.width) // 2,
            (THUMBNAIL_SIZE[1] - img.height) // 2,
        )

        if has_transparency:
            img = img.convert('RGBA')
            bg = PILImage.new('RGBA', THUMBNAIL_SIZE, (255, 255, 255, 255))
            bg.paste(img, offset, img)
            bg = bg.convert('RGB')
        else:
            img = img.convert('RGB')
            bg = PILImage.new('RGB', THUMBNAIL_SIZE, (255, 255, 255))
            bg.paste(img, offset)

        bg.save(thumb_path, 'JPEG', quality=THUMBNAIL_QUALITY)
        return True, md5_hash
    except OSError:
        logger.exception("Failed to generate thumbnail for %s", image_id)
        return False, md5_hash
