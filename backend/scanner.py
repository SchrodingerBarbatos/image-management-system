import re, os, hashlib, datetime
from models import session, Image, ScanRoot

STRICT_RE = re.compile(
    r'^(\d+)_(主图|详情图)_(\d+)\.(jpg|jpeg|png|gif|webp)$', re.IGNORECASE
)
FUZZY_RE = re.compile(
    r'^(\d+)_(\d+)\.(jpg|jpeg|png|gif|webp)$', re.IGNORECASE
)

TYPE_MAP = {'主图': 'main', '详情图': 'detail'}

def parse_filename(filename, allow_fuzzy=False):
    """Parse a filename. Returns dict with barcode, image_type, sequence, ext, match_type
    or None if no match."""
    m = STRICT_RE.match(filename)
    if m:
        return {
            'barcode': m.group(1),
            'image_type': TYPE_MAP[m.group(2)],
            'sequence': int(m.group(3)),
            'ext': m.group(4).lower(),
            'match_type': 'strict',
            'confirmed': True,
        }
    if allow_fuzzy:
        m = FUZZY_RE.match(filename)
        if m:
            return {
                'barcode': m.group(1),
                'image_type': '',
                'sequence': int(m.group(2)),
                'ext': m.group(3).lower(),
                'match_type': 'fuzzy',
                'confirmed': False,
            }
    return None
