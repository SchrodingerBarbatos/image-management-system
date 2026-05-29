import os, hashlib, logging
from PIL import Image as PILImage
from config import THUMBNAIL_DIR, THUMBNAIL_SIZE, THUMBNAIL_QUALITY

logger = logging.getLogger(__name__)

_MD5_CHUNK_SIZE = 8192


def get_thumbnail_path(image_id):
    return os.path.join(THUMBNAIL_DIR, f'{image_id}.jpg')


def thumbnail_exists(image_id):
    return os.path.exists(get_thumbnail_path(image_id))


def _stream_md5(filepath):
    """Compute MD5 hash of a file by reading in chunks.
    Returns hex digest string, or '' if the file cannot be read."""
    try:
        h = hashlib.md5()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(_MD5_CHUNK_SIZE), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ''


def generate_thumbnail(image_id, source_path):
    """Generate a 200x200 thumbnail and compute MD5 from the same file.
    Returns (success: bool, md5: str). md5 is '' if the file cannot be read."""
    thumb_path = get_thumbnail_path(image_id)
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)

    # Stream MD5 without loading the entire file into memory
    md5_hash = _stream_md5(source_path)
    if not md5_hash:
        return False, ''

    try:
        # Open directly from path — Pillow uses memory-mapped / lazy decoding
        img = PILImage.open(source_path)
        img.load()  # force full decode while we still need orig_mode / info
        orig_mode = img.mode
        img_info = dict(img.info) if img.info else {}

        # thumbnail() before convert() so JPEG draft mode can decode at reduced resolution
        img.thumbnail(THUMBNAIL_SIZE, PILImage.LANCZOS)

        # detect transparency from original mode after thumbnail is already small
        has_transparency = orig_mode in ('RGBA', 'LA', 'PA') or (
            orig_mode == 'P' and 'transparency' in img_info
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
    except Exception:
        logger.exception("Failed to generate thumbnail for %s", image_id)
        return False, md5_hash
