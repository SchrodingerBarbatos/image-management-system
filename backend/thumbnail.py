import os
from PIL import Image as PILImage
from config import THUMBNAIL_DIR, THUMBNAIL_SIZE, THUMBNAIL_QUALITY

def get_thumbnail_path(image_id):
    return os.path.join(THUMBNAIL_DIR, f'{image_id}.jpg')

def thumbnail_exists(image_id):
    return os.path.exists(get_thumbnail_path(image_id))

def generate_thumbnail(image_id, source_path):
    """Generate a 200x200 thumbnail, maintaining aspect ratio, white background."""
    thumb_path = get_thumbnail_path(image_id)
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)

    try:
        img = PILImage.open(source_path)
        img = img.convert('RGBA')
    except (OSError, IOError):
        raise ValueError(f'Cannot open or process image: {source_path}')

    img.thumbnail(THUMBNAIL_SIZE, PILImage.LANCZOS)
    bg = PILImage.new('RGBA', THUMBNAIL_SIZE, (255, 255, 255, 255))
    offset = (
        (THUMBNAIL_SIZE[0] - img.width) // 2,
        (THUMBNAIL_SIZE[1] - img.height) // 2,
    )
    bg.paste(img, offset, img if img.mode == 'RGBA' else None)
    bg = bg.convert('RGB')
    bg.save(thumb_path, 'JPEG', quality=THUMBNAIL_QUALITY)
    return thumb_path
