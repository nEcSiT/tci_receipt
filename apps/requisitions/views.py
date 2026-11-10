import uuid
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import Http404, FileResponse
from io import BytesIO
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from .forms import DraftForm
from .models import RequisitionStatus
from .services import RequesterService
from .uploads import add_attachments, private_storage
from .services import StaffService
from .models import Requisition
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib.auth.decorators import login_required
from django.contrib import messages


def private_response(response):
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@require_http_methods(['GET', 'POST'])
def portal(request):
    if not settings.REQUISITION_PORTAL_ENABLED:
        raise Http404
    if request.method == 'POST':
        try:
            key = signing.loads(request.POST.get('creation_key', ''),
                                salt='requisitions.create', max_age=3600)
        except signing.BadSignature:
            return private_response(render(request, 'requisitions/start.html',
                {'error': 'This form expired. Reload the page to begin again.'}, status=400))
        req = RequesterService.create(key)
        token = RequesterService.token(req)
        return private_response(redirect('requisitions:requester', token=token))
    key = signing.dumps(str(uuid.uuid4()), salt='requisitions.create')
    return private_response(render(request, 'requisitions/start.html', {'creation_key': key}))


@require_http_methods(['GET', 'POST'])
def requester(request, token):
    if not settings.REQUISITION_PORTAL_ENABLED:
        raise Http404
    req = RequesterService.resolve(token)
    form = DraftForm(instance=req)
    error = None
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'upload':
                add_attachments(token, request.FILES.getlist('documents'))
            else:
                RequesterService.save(token, request.POST, submit=request.POST.get('action') == 'submit')
            return private_response(redirect('requisitions:requester', token=token))
        except ValidationError as exc:
            error = exc.messages
            form = DraftForm(request.POST, instance=req,
                             submitting=request.POST.get('action') == 'submit')
            form.is_valid()
            req.refresh_from_db()
    return private_response(render(request, 'requisitions/requester.html', {
        'requisition': req, 'form': form, 'error': error,
        'is_draft': req.status == RequisitionStatus.DRAFT,
        'resume_url': request.build_absolute_uri(),
        'access_days': settings.REQUISITION_ACCESS_MAX_AGE // 86400,
        'attachments': req.attachments.all(), 'token': token,
        'max_file_bytes': settings.REQUISITION_UPLOAD_MAX_BYTES,
        'max_file_count': settings.REQUISITION_UPLOAD_MAX_COUNT,
    }, status=400 if error else 200))


@require_http_methods(['GET'])
def attachment(request, token, file_id):
    if not settings.REQUISITION_PORTAL_ENABLED:
        raise Http404
    req = RequesterService.resolve(token)
    file = req.attachments.filter(pk=file_id).first()
    if not file:
        raise Http404
    try:
        content = private_storage().get(file.storage_key)
    except FileNotFoundError:
        raise Http404
    response = FileResponse(BytesIO(content), as_attachment=True,
                            filename=file.file_name, content_type='application/octet-stream')
    response['X-Content-Type-Options'] = 'nosniff'
    return private_response(response)


@login_required
@require_http_methods(['GET'])
def queue(request):
    StaffService.authorize(request.user, 'requisition.view')
    qs = Requisition.objects.exclude(status=RequisitionStatus.DRAFT).prefetch_related('assignments__assigned_to')
    query = request.GET.get('q', '').strip()
    if query:
        qs = qs.filter(Q(requisition_number__icontains=query) | Q(purpose__icontains=query) |
                       Q(ministry__icontains=query) | Q(department__icontains=query))
    status = request.GET.get('status')
    if status in RequisitionStatus.values:
        qs = qs.filter(status=status)
    if request.GET.get('work') == 'mine':
        qs = qs.filter(assignments__is_current=True, assignments__assigned_to=request.user)
    elif request.GET.get('work') == 'unassigned':
        qs = qs.exclude(assignments__is_current=True)
    from django import forms
    date_field = forms.DateField(required=False)
    error = None
    try:
        for param, lookup in [('from', 'submitted_at__date__gte'), ('to', 'submitted_at__date__lte')]:
            date = date_field.clean(request.GET.get(param))
            if date:
                qs = qs.filter(**{lookup: date})
    except ValidationError:
        error = 'Enter valid dates.'
        qs = qs.none()
    return render(request, 'requisitions/queue.html', {
        'page': Paginator(qs, 25).get_page(request.GET.get('page')),
        'statuses': RequisitionStatus.choices, 'error': error,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def detail(request, requisition_id):
    try:
        if request.method == 'POST':
            try:
                if request.POST.get('action') in ('APPROVED', 'REJECTED'):
                    StaffService.decide(request.user, requisition_id, request.POST['action'],
                        amount=request.POST.get('amount'), reason=request.POST.get('reason', ''),
                        remarks=request.POST.get('remarks', ''))
                else:
                    StaffService.start_working(request.user, requisition_id)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
            return redirect('requisitions:detail', requisition_id=requisition_id)
        req = StaffService.detail(request.user, requisition_id)
    except Requisition.DoesNotExist:
        raise Http404
    return render(request, 'requisitions/detail.html', {'requisition': req,
        'pending': req.status in (RequisitionStatus.SUBMITTED, RequisitionStatus.PENDING_REVIEW)})


@login_required
@require_http_methods(['GET'])
def staff_attachment(request, requisition_id, file_id):
    try:
        req = StaffService.detail(request.user, requisition_id)
    except Requisition.DoesNotExist:
        raise Http404
    file = req.attachments.filter(pk=file_id).first()
    if not file:
        raise Http404
    try:
        content = private_storage().get(file.storage_key)
    except FileNotFoundError:
        raise Http404
    response = FileResponse(BytesIO(content), as_attachment=True,
                            filename=file.file_name, content_type='application/octet-stream')
    response['X-Content-Type-Options'] = 'nosniff'
    return private_response(response)
