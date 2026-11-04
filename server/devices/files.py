import hashlib, os
from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename
from .models import UploadedFile

MAX_UPLOAD = 8 * 1024 * 1024
# Files are opaque bytes end to end (CSV may be UTF-16/Latin-1, PDF is binary): never decoded, never fed to ParserSchema.
UPLOAD_TYPES = {".csv": None, ".txt": None, ".pdf": b"%PDF"}  # extension -> required magic prefix

def store_upload(device, raw_name, body):
    """Validate and save a file received from a device. Returns (UploadedFile | None, error | None)."""
    name = get_valid_filename(os.path.basename(raw_name or "")) or "upload"
    ext = os.path.splitext(name)[1].lower()
    if ext not in UPLOAD_TYPES: return None, "only .csv .txt .pdf accepted"
    if not body or len(body) > MAX_UPLOAD: return None, "empty or too large"
    if UPLOAD_TYPES[ext] and not body.startswith(UPLOAD_TYPES[ext]): return None, "content does not match extension"
    f = UploadedFile(device=device, name=name, size=len(body), sha256=hashlib.sha256(body).hexdigest())
    f.file.save(name, ContentFile(body), save=False); f.save()
    return f, None
