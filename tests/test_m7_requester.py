import uuid
from unittest.mock import patch
import pytest
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.urls import reverse
from apps.audit.models import AuditLog
from apps.requisitions.models import Requisition, RequisitionStatus
from apps.requisitions.services import RequesterService


VALID = dict(requester_name='Test Requester', requester_phone='+233240001122',
             purpose='Camp supplies', description='Stationery for camp', amount_requested='120.50')


def draft():
    req = RequesterService.create(uuid.uuid4())
    return req, RequesterService.token(req)


def test_incomplete_draft_and_creation_retry():
    key = uuid.uuid4()
    req = RequesterService.create(key)
    assert RequesterService.create(key).pk == req.pk
    assert req.amount_requested is None
    token = RequesterService.token(req)
    saved = RequesterService.save(token, {'purpose': 'Supplies'})
    assert saved.purpose == 'Supplies'
    assert saved.requisition_number == req.requisition_number
    assert saved.status == RequisitionStatus.DRAFT


@pytest.mark.parametrize('amount', ['bad', '0', '-1', '10000000000.00', 'NaN', '1.234'])
def test_invalid_amount_never_persists(amount):
    req, token = draft()
    with pytest.raises(ValidationError):
        RequesterService.save(token, {'amount_requested': amount})
    req.refresh_from_db()
    assert req.amount_requested is None


def test_submission_validation_retry_and_freeze():
    req, token = draft()
    with pytest.raises(ValidationError):
        RequesterService.save(token, {}, submit=True)
    req.refresh_from_db()
    assert req.status == RequisitionStatus.DRAFT
    submitted = RequesterService.save(token, VALID, submit=True)
    assert submitted.status == RequisitionStatus.SUBMITTED
    assert submitted.submitted_at
    assert submitted.requisition_number == req.requisition_number
    RequesterService.save(token, {'purpose': 'changed'}, submit=True)
    submitted.refresh_from_db()
    assert submitted.purpose == VALID['purpose']
    assert AuditLog.objects.filter(action='REQUISITION_SUBMITTED', entity_id=req.pk).count() == 1
    with pytest.raises(ValidationError):
        RequesterService.save(token, VALID)


def test_scope_expiry_revocation_and_wrong_purpose():
    req, token = draft()
    other, _ = draft()
    assert RequesterService.resolve(token).pk != other.pk
    bad = signing.dumps({'id': str(req.pk), 'version': str(req.access_version),
                         'purpose': 'evidence'}, salt=RequesterService.SALT)
    for invalid in (token + 'tamper', str(req.pk), req.requisition_number, bad):
        with pytest.raises(PermissionDenied):
            RequesterService.resolve(invalid)
    with patch('django.core.signing.time.time', return_value=99999999999):
        with pytest.raises(PermissionDenied):
            RequesterService.resolve(token)
    req.access_version = uuid.uuid4()
    req.save()
    with pytest.raises(PermissionDenied):
        RequesterService.resolve(token)


def test_rejected_field_edit_is_blocked():
    req = Requisition.objects.create(**VALID, status=RequisitionStatus.REJECTED)
    req.purpose = 'Changed purpose'
    with pytest.raises(ValidationError, match='Rejected requisitions cannot'):
        req.save()


@override_settings(REQUISITION_PORTAL_ENABLED=True)
def test_public_browser_flow_and_audit(client, system_admin):
    # A staff session must not falsely attribute requester actions to staff.
    client.force_login(system_admin)
    start = client.get(reverse('requisitions:portal'))
    key = start.context['creation_key']
    response = client.post(reverse('requisitions:portal'), {'creation_key': key})
    location = response['Location']
    assert client.post(reverse('requisitions:portal'), {'creation_key': key}).status_code == 302
    assert Requisition.objects.count() == 1
    assert client.get(location)['Cache-Control'] == 'no-store'
    assert client.post(location, {'action': 'submit'}).status_code == 400
    assert client.post(location, {**VALID, 'action': 'submit'}).status_code == 302
    confirmation = client.get(location)
    assert b'Your request has been submitted' in confirmation.content
    assert b'name="requester_member"' not in confirmation.content
    audit = AuditLog.objects.get(action='REQUISITION_SUBMITTED')
    assert audit.user_id is None
    assert audit.actor_type == 'REQUESTER'


def test_portal_disabled_by_default(client):
    with override_settings(REQUISITION_PORTAL_ENABLED=False):
        assert client.get(reverse('requisitions:portal')).status_code == 404


@override_settings(REQUISITION_PORTAL_ENABLED=True)
def test_supporting_uploads_private_and_scoped(client, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.requisitions.uploads import add_attachments
    req, token = draft()
    other, other_token = draft()
    with override_settings(REQUISITION_PRIVATE_STORAGE=tmp_path):
        add_attachments(token, [SimpleUploadedFile('receipt.txt', b'Camp supplies'),
                                SimpleUploadedFile('notes.txt', b'More details')])
        assert req.attachments.count() == 2
        file = req.attachments.first()
        url = reverse('requisitions:attachment', args=[token, file.pk])
        response = client.get(url)
        assert response.status_code == 200
        assert response['Content-Disposition'].startswith('attachment;')
        assert client.get(reverse('requisitions:attachment', args=[other_token, file.pk])).status_code == 404
        for name, content in [('fake.pdf', b'not pdf'), ('script.txt', b'<script>alert(1)</script>'),
                              ('bad.png', b'not image'), ('run.exe', b'MZ')]:
            with pytest.raises(ValidationError):
                add_attachments(token, [SimpleUploadedFile(name, content)])
        with override_settings(REQUISITION_UPLOAD_MAX_BYTES=2):
            with pytest.raises(ValidationError):
                add_attachments(token, [SimpleUploadedFile('large.txt', b'123')])
        assert req.attachments.count() == 2
        RequesterService.save(token, VALID, submit=True)
        with pytest.raises(ValidationError):
            add_attachments(token, [SimpleUploadedFile('late.txt', b'Late')])


def test_attachment_storage_failure_cleans_up(tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.requisitions.uploads import add_attachments
    req, token = draft()
    with override_settings(REQUISITION_PRIVATE_STORAGE=tmp_path):
        with patch('apps.requisitions.uploads.RequisitionAttachment.objects.create', side_effect=RuntimeError):
            with pytest.raises(RuntimeError):
                add_attachments(token, [SimpleUploadedFile('notes.txt', b'Content')])
    assert req.attachments.count() == 0
    assert not [p for p in tmp_path.rglob('*') if p.is_file()]


def test_supported_images_and_pdf():
    from io import BytesIO
    from PIL import Image
    from pypdf import PdfWriter
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.requisitions.uploads import validate_upload
    for format, extension in [('PNG', 'png'), ('JPEG', 'jpg')]:
        stream = BytesIO()
        Image.new('RGB', (2, 2)).save(stream, format=format)
        validate_upload(SimpleUploadedFile('photo.' + extension, stream.getvalue()))
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    validate_upload(SimpleUploadedFile('proof.pdf', stream.getvalue()))
    writer.add_js('app.alert("unsafe")')
    stream = BytesIO()
    writer.write(stream)
    with pytest.raises(ValidationError):
        validate_upload(SimpleUploadedFile('active.pdf', stream.getvalue()))
