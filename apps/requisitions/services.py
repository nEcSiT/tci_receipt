"""Requester operations. Every mutation revalidates access and input in a transaction."""
import uuid
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.audit.models import AuditLog
from .forms import DraftForm
from .models import Requisition, RequisitionStatus
from .models import RequisitionAssignment, RequisitionApproval, ApprovalDecision
from apps.audit.services import AuditService
from apps.core.models import BusinessSequence


class RequesterService:
    SALT = 'requisitions.requester.v1'

    @classmethod
    def token(cls, req):
        return signing.dumps({'id': str(req.pk), 'version': str(req.access_version),
                              'purpose': 'draft-status'}, salt=cls.SALT)

    @classmethod
    def resolve(cls, token, lock=False):
        try:
            payload = signing.loads(token, salt=cls.SALT,
                                    max_age=settings.REQUISITION_ACCESS_MAX_AGE)
            if payload['purpose'] != 'draft-status':
                raise ValueError()
            qs = Requisition.objects.select_for_update() if lock else Requisition.objects
            return qs.get(pk=payload['id'], access_version=payload['version'])
        except (signing.BadSignature, KeyError, TypeError, ValueError, ValidationError, Requisition.DoesNotExist):
            raise PermissionDenied('This access link is invalid, expired or revoked.')

    @staticmethod
    def audit(req, action):
        # Explicitly anonymous: AuditService otherwise infers any staff browser
        # session, which would misattribute an external requester event.
        AuditLog.objects.create(action=action, entity_type='Requisition', entity_id=req.pk,
                                actor_type='REQUESTER', user=None,
                                new_values={'status': req.status})

    @classmethod
    @transaction.atomic
    def create(cls, creation_key):
        key = uuid.UUID(str(creation_key))
        # Serialize creation with the existing ID sequence lock so a simultaneous
        # retry cannot fail model-level uniqueness validation before get_or_create
        # can recover from a database unique-key race.
        BusinessSequence.objects.select_for_update().get_or_create(
            sequence_type='REQUISITION_NUMBER', defaults={'last_value': 0})
        req, created = Requisition.objects.get_or_create(creation_key=key)
        if created:
            cls.audit(req, 'REQUISITION_DRAFT_CREATED')
        return req


    @classmethod
    @transaction.atomic
    def save(cls, token, data, submit=False):
        req = cls.resolve(token, lock=True)
        if req.status != RequisitionStatus.DRAFT:
            if submit and req.status in (RequisitionStatus.SUBMITTED, RequisitionStatus.PENDING_REVIEW):
                return req  # Retry of successful submission; never edit its fields.
            raise ValidationError('Submitted requests cannot be edited.')
        form = DraftForm(data, instance=req, submitting=submit)
        if not form.is_valid():
            raise ValidationError(dict(form.errors))
        req = form.save(commit=False)
        if submit:
            req.status = RequisitionStatus.SUBMITTED
            req.submitted_at = timezone.now()
        req.save()
        cls.audit(req, 'REQUISITION_SUBMITTED' if submit else 'REQUISITION_DRAFT_SAVED')
        return req


class StaffService:
    @staticmethod
    def authorize(actor, permission):
        if not actor or not actor.is_authenticated or not actor.is_active or not actor.has_permission(permission):
            raise PermissionDenied('You do not have permission for this action.')

    @classmethod
    def detail(cls, actor, requisition_id, lock=False, permission='requisition.view'):
        cls.authorize(actor, permission)
        qs = Requisition.objects.select_for_update() if lock else Requisition.objects
        req = qs.get(pk=requisition_id)
        if req.status == RequisitionStatus.DRAFT:
            raise PermissionDenied('Requester drafts are private.')
        assignment = req.current_assignment
        if assignment and assignment.assigned_to_id != actor.pk:
            raise PermissionDenied('This request is locked to its current handler.')
        return req

    @classmethod
    @transaction.atomic
    def start_working(cls, actor, requisition_id):
        req = cls.detail(actor, requisition_id, lock=True, permission='requisition.review')
        if req.current_assignment and req.status == RequisitionStatus.UNDER_REVIEW:
            return req
        if req.status not in (RequisitionStatus.SUBMITTED, RequisitionStatus.PENDING_REVIEW):
            raise ValidationError('Only pending requests can enter review.')
        RequisitionAssignment.objects.create(requisition=req, assigned_to=actor, assigned_by=actor)
        req.status = RequisitionStatus.UNDER_REVIEW
        req.save()
        AuditService.log(action='REQUISITION_ASSIGNED', entity_type='Requisition', entity_id=req.pk,
                         user=actor, new_values={'handler': str(actor.pk), 'status': req.status})
        return req

    @classmethod
    @transaction.atomic
    def decide(cls, actor, requisition_id, decision, amount=None, reason='', remarks=''):
        from django import forms
        from decimal import Decimal
        from .notifications import queue_decision
        req = cls.detail(actor, requisition_id, lock=True, permission='requisition.approve')
        assignment = req.current_assignment
        if not assignment or assignment.assigned_to_id != actor.pk:
            raise PermissionDenied('The active handler must make this decision.')
        if decision not in ApprovalDecision.values:
            raise ValidationError('Choose approval or rejection.')
        existing = req.latest_approval
        if req.status != RequisitionStatus.UNDER_REVIEW:
            if existing and existing.decision == decision and existing.approver_id == actor.pk:
                return req
            raise ValidationError('Only a request under review can be decided.')
        if decision == ApprovalDecision.APPROVED:
            amount = forms.DecimalField(max_digits=12, decimal_places=2,
                                        min_value=Decimal('0.01')).clean(amount)
        elif not reason.strip():
            raise ValidationError('A rejection reason is required.')
        RequisitionApproval.objects.create(requisition=req, approver=actor, decision=decision,
            approved_amount=amount if decision == ApprovalDecision.APPROVED else None,
            reason=reason.strip(), remarks=remarks.strip())
        req.status = (RequisitionStatus.PENDING_DISBURSEMENT if decision == ApprovalDecision.APPROVED
                      else RequisitionStatus.REJECTED)
        req.save()
        AuditService.log(action='REQUISITION_' + decision, entity_type='Requisition', entity_id=req.pk,
            user=actor, reason=reason.strip(), old_values={'status': RequisitionStatus.UNDER_REVIEW},
            new_values={'status': req.status, 'approved_amount': str(amount) if amount is not None else None})
        queue_decision(req, decision, actor)
        return req

