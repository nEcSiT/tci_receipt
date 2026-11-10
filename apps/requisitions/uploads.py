"""Private supporting files, independent of publicly served MEDIA_ROOT."""
from io import BytesIO
from pathlib import Path
import re
import uuid
import warnings
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from PIL import Image
from apps.core.services.storage import LocalStorageService
from .models import RequisitionAttachment, RequisitionStatus
from .services import RequesterService


def private_storage():
    return LocalStorageService(settings.REQUISITION_PRIVATE_STORAGE)


def validate_upload(upload):
    content = upload.read(settings.REQUISITION_UPLOAD_MAX_BYTES + 1)
    if not content or len(content) > settings.REQUISITION_UPLOAD_MAX_BYTES:
        raise ValidationError('File is empty or exceeds the configured size limit.')
    extension = Path(upload.name).suffix.lower()
    types = {'.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
             '.png': 'image/png', '.txt': 'text/plain'}
    if extension not in types:
        raise ValidationError('Use PDF, JPG, JPEG, PNG or TXT files.')
    try:
        if extension in ('.jpg', '.jpeg', '.png'):
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as img:
                    if img.format != ('PNG' if extension == '.png' else 'JPEG'):
                        raise ValueError()
                    img.verify()
                # Store a fresh raster to strip metadata and appended payloads.
                with Image.open(BytesIO(content)) as img:
                    output = BytesIO()
                    img.save(output, format=img.format)
                    content = output.getvalue()
        elif extension == '.pdf':
            from pypdf import PdfReader, PdfWriter
            if not content.startswith(b'%PDF-'):
                raise ValueError()
            pdf = PdfReader(BytesIO(content), strict=True)
            if pdf.is_encrypted or not len(pdf.pages):
                raise ValueError()
            # Fail closed for interactive documents, actions, external resources,
            # embedded files and forms. Inspect parsed objects (including escaped names).
            forbidden = {'/JavaScript', '/JS', '/AA', '/OpenAction', '/Launch', '/URI',
                         '/EmbeddedFiles', '/Filespec', '/RichMedia', '/XFA', '/AcroForm',
                         '/Annots', '/SubmitForm', '/GoToR', '/F', '/EF'}
            seen = set()
            def inspect(obj, depth=0):
                if depth > 100 or len(seen) > 20000:
                    raise ValueError()
                obj = obj.get_object() if hasattr(obj, 'get_object') else obj
                if id(obj) in seen:
                    return
                seen.add(id(obj))
                if isinstance(obj, dict):
                    if forbidden.intersection(str(k) for k in obj):
                        raise ValueError()
                    for value in obj.values():
                        inspect(value, depth + 1)
                elif isinstance(obj, (list, tuple)):
                    for value in obj:
                        inspect(value, depth + 1)
            inspect(pdf.trailer)
            output = BytesIO()
            PdfWriter(clone_from=pdf).write(output)
            content = output.getvalue()
        else:
            text = content.decode('utf-8')
            if any(ord(c) < 32 and c not in '\n\r\t' for c in text) or '<' in text or '>' in text:
                raise ValueError()
    except Exception as exc:
        raise ValidationError('File content is invalid or contains unsupported active content.') from exc
    if len(content) > settings.REQUISITION_UPLOAD_MAX_BYTES:
        raise ValidationError('The validated file exceeds the configured size limit.')
    return content, types[extension], extension


def add_attachments(token, uploads):
    # Validate all files before touching storage. The outer transaction belongs
    # here so rollback cleanup covers both file and metadata failures.
    uploads = list(uploads)
    if len(uploads) > settings.REQUISITION_UPLOAD_MAX_COUNT:
        raise ValidationError('Too many files.')
    validated = [(upload, *validate_upload(upload)) for upload in uploads]
    storage = private_storage()
    written = []
    try:
        with transaction.atomic():
            req = RequesterService.resolve(token, lock=True)
            if req.status != RequisitionStatus.DRAFT:
                raise ValidationError('Supporting documents can only be added to a draft.')
            if req.attachments.count() + len(uploads) > settings.REQUISITION_UPLOAD_MAX_COUNT:
                raise ValidationError('This request has reached its file limit.')
            for upload, content, mime, extension in validated:
                key = f'supporting/{req.pk}/{uuid.uuid4().hex}{extension}'
                written.append(key)
                storage.save(key, content)
                safe_name = re.sub(r'[\r\n\x00-\x1f"\\/]', '_', Path(upload.name).name)[:255]
                RequisitionAttachment.objects.create(requisition=req,
                    uploader_name=req.requester_name or 'External requester',
                    uploader_phone=req.requester_phone, file_name=safe_name,
                    file_type=mime, storage_key=key, file_size=len(content))
            if uploads:
                RequesterService.audit(req, 'REQUISITION_ATTACHMENTS_UPLOADED')
    except Exception:
        for key in written:
            storage.delete(key)
        raise
