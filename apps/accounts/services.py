import datetime
import hashlib
import secrets
import uuid
import logging
from django.conf import settings
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.core.cache import cache
from django.core.mail import send_mail
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.accounts.models import User, Role, Permission, UserRole, RolePermission, PasswordResetToken, AdminRecoverySession
from apps.audit.models import AuditResult
from apps.audit.services import AuditService

logger = logging.getLogger(__name__)


class AuthService:
    """Service handling authentication, sessions, and password recovery."""

    @classmethod
    def authenticate_and_login(cls, request, email: str, password: str, remember_me: bool = False) -> tuple[User | None, str | None]:
        """
        Authenticates user with email and password.
        Rejects inactive users.
        Maintains generic error messages on invalid credentials.
        Emits immutable audit events.
        """
        email = email.strip()
        user_candidate = User.objects.filter(email__iexact=email).first()

        client_ip = request.META.get("REMOTE_ADDR", "unknown")
        attempt_key = f"tci-login-fail:{client_ip}:{email.lower()}"
        failure_count = cache.get(attempt_key, 0)
        if failure_count >= 5:
            AuditService.log(
                action="AUTH_LOGIN_THROTTLED",
                entity_type="User",
                entity_id=user_candidate.id if user_candidate else uuid.UUID("00000000-0000-0000-0000-000000000000"),
                reason="Too many failed login attempts",
                result=AuditResult.FAILURE,
            )
            return None, "Too many failed login attempts. Please try again later."

        user = authenticate(request, email=email, password=password)

        if user is None:
            # Login failed - determine if account was inactive or bad credentials
            if user_candidate and user_candidate.check_password(password) and not user_candidate.is_active:
                AuditService.log(
                    action="AUTH_LOGIN_FAILED",
                    entity_type="User",
                    entity_id=user_candidate.id,
                    reason="Inactive user denied authentication",
                    result=AuditResult.FAILURE,
                )
                cache.set(attempt_key, failure_count + 1, timeout=900)
                return None, "This account is inactive. Please contact the System Administrator."

            # Bad credentials: emit failure audit log without disclosing account existence
            entity_id = user_candidate.id if user_candidate else uuid.UUID("00000000-0000-0000-0000-000000000000")
            cache.set(attempt_key, failure_count + 1, timeout=900)
            AuditService.log(
                action="AUTH_LOGIN_FAILED",
                entity_type="User",
                entity_id=entity_id,
                reason="Invalid credentials supplied",
                result=AuditResult.FAILURE,
            )
            return None, "Invalid email or password."

        if not user.is_active:
            AuditService.log(
                action="AUTH_LOGIN_FAILED",
                entity_type="User",
                entity_id=user.id,
                reason="Inactive user denied authentication",
                result=AuditResult.FAILURE,
            )
            return None, "This account is inactive. Please contact the System Administrator."

        # Successful login
        cache.delete(attempt_key)
        auth_login(request, user)
        user.last_login_at = timezone.now()
        user.save(update_fields=["last_login_at"])

        if remember_me:
            request.session.set_expiry(1209600)  # 2 weeks
        else:
            request.session.set_expiry(0)  # Browser close

        AuditService.log(
            action="AUTH_LOGIN_SUCCESS",
            entity_type="User",
            entity_id=user.id,
            result=AuditResult.SUCCESS,
        )
        return user, None

    @classmethod
    def logout(cls, request):
        """Terminates session and records audit event."""
        if hasattr(request, "user") and request.user.is_authenticated:
            AuditService.log(
                action="AUTH_LOGOUT",
                entity_type="User",
                entity_id=request.user.id,
                result=AuditResult.SUCCESS,
            )
        auth_logout(request)

    @classmethod
    def request_password_reset(cls, email: str, reset_url_base: str | None = None) -> tuple[bool, str | None]:
        """
        Generates a secure, single-use, expiring token.
        Always returns True to prevent user enumeration.
        """
        email = email.strip()
        user = User.objects.filter(email__iexact=email, is_active=True).first()

        if not user:
            return True, None

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = timezone.now() + datetime.timedelta(hours=1)

        PasswordResetToken.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        if reset_url_base:
            reset_url = f"{reset_url_base}{raw_token}/"
            try:
                send_mail(
                    subject="TCI HLC — Password Reset",
                    message=(
                        "A password reset was requested for your TCI Higher Life Center account.\n\n"
                        f"Use this link within 1 hour:\n{reset_url}"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
            except Exception:
                AuditService.log(
                    action="AUTH_PASSWORD_RESET_EMAIL_FAILED",
                    entity_type="User",
                    entity_id=user.id,
                    result=AuditResult.FAILURE,
                )

        AuditService.log(
            action="AUTH_PASSWORD_RESET_REQUESTED",
            entity_type="User",
            entity_id=user.id,
            result=AuditResult.SUCCESS,
        )
        return True, raw_token

    @classmethod
    def confirm_password_reset(cls, raw_token: str, new_password: str) -> tuple[bool, str | None]:
        """
        Validates token validity, enforces single-use, and updates password.
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_record = PasswordResetToken.objects.filter(token_hash=token_hash).first()

        if not token_record or not token_record.is_valid:
            return False, "This password reset link is invalid or has expired."

        user = token_record.user
        if not user.is_active:
            return False, "This user account is inactive."

        try:
            validate_password(new_password, user=user)
        except ValidationError as e:
            return False, "; ".join(e.messages)

        user.set_password(new_password)
        user.save(update_fields=["password"])

        token_record.is_used = True
        token_record.used_at = timezone.now()
        token_record.save(update_fields=["is_used", "used_at"])

        AuditService.log(
            action="AUTH_PASSWORD_RESET_COMPLETED",
            entity_type="User",
            entity_id=user.id,
            result=AuditResult.SUCCESS,
        )
        return True, None


class AdminRecoveryService:
    """Multi-factor recovery service for the single System Administrator."""

    @classmethod
    def initiate_recovery(cls, email: str) -> tuple[AdminRecoverySession | None, str | None, str | None, str | None]:
        """
        Initiates recovery for the System Administrator.
        Generates dynamic Email OTP and Phone OTP.
        """
        admin_role = Role.objects.filter(name=Role.SYSTEM_ADMINISTRATOR).first()
        admin_user_role = UserRole.objects.filter(role=admin_role).first() if admin_role else None
        admin_user = admin_user_role.user if admin_user_role else None

        if not admin_user or admin_user.email.lower() != email.strip().lower():
            return None, None, None, "Invalid System Administrator credentials."

        session_token = secrets.token_urlsafe(32)
        email_otp = f"{secrets.randbelow(900000) + 100000}"
        phone_otp = f"{secrets.randbelow(900000) + 100000}"

        email_otp_hash = hashlib.sha256(email_otp.encode()).hexdigest()
        phone_otp_hash = hashlib.sha256(phone_otp.encode()).hexdigest()
        expires_at = timezone.now() + datetime.timedelta(minutes=15)

        session = AdminRecoverySession.objects.create(
            session_token=session_token,
            email_otp_hash=email_otp_hash,
            phone_otp_hash=phone_otp_hash,
            expires_at=expires_at,
        )

        try:
            send_mail(
                subject="TCI HLC — Administrator Recovery OTP",
                message=f"Your administrator recovery Email OTP is {email_otp}. It expires in 15 minutes.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[admin_user.email],
                fail_silently=False,
            )
        except Exception:
            AuditService.log(
                action="ADMIN_RECOVERY_EMAIL_FAILED",
                entity_type="AdminRecoverySession",
                entity_id=session.id,
                result=AuditResult.FAILURE,
            )

        if settings.DEBUG:
            logger.warning("LOCAL DEVELOPMENT ONLY — administrator Phone OTP: %s", phone_otp)

        AuditService.log(
            action="ADMIN_RECOVERY_INITIATED",
            entity_type="AdminRecoverySession",
            entity_id=session.id,
            result=AuditResult.SUCCESS,
        )
        return session, email_otp, phone_otp, None

    @classmethod
    def verify_email_otp(cls, session_token: str, otp_code: str) -> tuple[bool, str | None]:
        """Validates Email OTP."""
        session = AdminRecoverySession.objects.filter(session_token=session_token).first()
        if not session or not session.is_valid:
            return False, "Recovery session has expired or is invalid."

        computed_hash = hashlib.sha256(otp_code.strip().encode()).hexdigest()
        if computed_hash != session.email_otp_hash:
            AuditService.log(
                action="ADMIN_RECOVERY_EMAIL_OTP_FAILED",
                entity_type="AdminRecoverySession",
                entity_id=session.id,
                result=AuditResult.FAILURE,
            )
            return False, "Invalid Email OTP."

        session.email_verified = True
        session.save(update_fields=["email_verified"])

        AuditService.log(
            action="ADMIN_RECOVERY_EMAIL_VERIFIED",
            entity_type="AdminRecoverySession",
            entity_id=session.id,
            result=AuditResult.SUCCESS,
        )
        return True, None

    @classmethod
    def verify_phone_otp(cls, session_token: str, otp_code: str) -> tuple[bool, str | None]:
        """Validates Phone OTP after Email OTP verification."""
        session = AdminRecoverySession.objects.filter(session_token=session_token).first()
        if not session or not session.is_valid:
            return False, "Recovery session has expired or is invalid."

        if not session.email_verified:
            return False, "Email OTP must be verified before Phone OTP."

        computed_hash = hashlib.sha256(otp_code.strip().encode()).hexdigest()
        if computed_hash != session.phone_otp_hash:
            AuditService.log(
                action="ADMIN_RECOVERY_PHONE_OTP_FAILED",
                entity_type="AdminRecoverySession",
                entity_id=session.id,
                result=AuditResult.FAILURE,
            )
            return False, "Invalid Phone OTP."

        session.phone_verified = True
        session.save(update_fields=["phone_verified"])

        AuditService.log(
            action="ADMIN_RECOVERY_PHONE_VERIFIED",
            entity_type="AdminRecoverySession",
            entity_id=session.id,
            result=AuditResult.SUCCESS,
        )
        return True, None

    @classmethod
    def complete_recovery(cls, session_token: str, new_password: str) -> tuple[bool, str | None]:
        """Sets new password for the System Administrator upon complete MFA verification."""
        session = AdminRecoverySession.objects.filter(session_token=session_token).first()
        if not session or not session.is_ready_for_reset:
            return False, "Recovery verification is incomplete or session expired."

        admin_role = Role.objects.filter(name=Role.SYSTEM_ADMINISTRATOR).first()
        admin_user_role = UserRole.objects.filter(role=admin_role).first() if admin_role else None
        admin_user = admin_user_role.user if admin_user_role else None

        if not admin_user:
            return False, "System Administrator user not found."

        try:
            validate_password(new_password, user=admin_user)
        except ValidationError as e:
            return False, "; ".join(e.messages)

        admin_user.set_password(new_password)
        admin_user.save(update_fields=["password"])

        session.is_completed = True
        session.save(update_fields=["is_completed"])

        AuditService.log(
            action="ADMIN_RECOVERY_COMPLETED",
            entity_type="User",
            entity_id=admin_user.id,
            result=AuditResult.SUCCESS,
        )
        return True, None


class RolePermissionService:
    """Service for configuring permissions on non-administrator roles."""

    @classmethod
    def update_role_permissions(cls, actor: User, role: Role, permission_codes: list[str]) -> Role:
        if not actor.is_active or not actor.is_system_administrator:
            raise ValidationError("Only the System Administrator may configure role permissions.")

        if role.name == Role.SYSTEM_ADMINISTRATOR:
            raise ValidationError("The System Administrator role permissions are protected.")

        requested = set(permission_codes)
        valid_codes = set(Permission.objects.values_list("code", flat=True))
        unknown = requested - valid_codes
        if unknown:
            raise ValidationError("One or more selected permissions are invalid.")

        old_codes = set(role.role_permissions.values_list("permission__code", flat=True))

        RolePermission.objects.filter(role=role).delete()
        permissions = Permission.objects.filter(code__in=requested)
        RolePermission.objects.bulk_create([
            RolePermission(role=role, permission=permission, assigned_by=actor)
            for permission in permissions
        ])

        AuditService.log(
            action="ROLE_PERMISSIONS_UPDATED",
            entity_type="Role",
            entity_id=role.id,
            user=actor,
            old_values={"permission_codes": sorted(old_codes)},
            new_values={"permission_codes": sorted(requested)},
            result=AuditResult.SUCCESS,
        )
        return role


class UserService:
    """Service handling System User creation, role assignment, and lifecycle management."""

    @classmethod
    def create_system_user(
        cls,
        creator: User,
        email: str,
        first_name: str,
        last_name: str,
        role_name: str = Role.SYSTEM_USER,
        initial_password: str | None = None,
        permission_codes: list[str] | None = None,
    ) -> User:
        """Creates a new System User with assigned role and permissions."""
        email = email.strip()

        if not creator.is_active or not creator.is_system_administrator:
            if not creator.has_permission("user.manage"):
                raise ValidationError("Only an authorized System Administrator or System User with user.manage may create users.")

        role = Role.objects.get(name=role_name)

        # System Administrator creation is reserved for the protected bootstrap/recovery path.
        if role.name == Role.SYSTEM_ADMINISTRATOR:
            raise ValidationError("A System User cannot create or assign the System Administrator role.")

        password = initial_password or secrets.token_urlsafe(16)
        user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            is_active=True,
        )

        UserRole.objects.create(user=user, role=role, assigned_by=creator)

        AuditService.log(
            action="USER_CREATED",
            entity_type="User",
            entity_id=user.id,
            user=creator,
            new_values={
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": role.name,
            },
            result=AuditResult.SUCCESS,
        )
        return user

    @classmethod
    def update_system_user(
        cls,
        actor: User,
        user: User,
        first_name: str,
        last_name: str,
        role_name: str | None = None,
    ) -> User:
        """Updates user details and role."""
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("user.manage")):
            raise ValidationError("You do not have permission to manage system users.")

        if role_name == Role.SYSTEM_ADMINISTRATOR and not actor.is_system_administrator:
            raise ValidationError("Only the System Administrator may manage the System Administrator role.")

        old_values = {
            "first_name": user.first_name,
            "last_name": user.last_name,
        }

        user.first_name = first_name.strip()
        user.last_name = last_name.strip()
        user.save(update_fields=["first_name", "last_name", "updated_at"])

        if role_name:
            new_role = Role.objects.get(name=role_name)
            current_user_role = user.user_roles.first()
            if current_user_role and current_user_role.role != new_role:
                if new_role.name == Role.SYSTEM_ADMINISTRATOR:
                    if UserRole.objects.filter(role__name=Role.SYSTEM_ADMINISTRATOR).exclude(user=user).exists():
                        raise ValidationError("Exactly one System Administrator may exist in the system.")
                
                # Protect removing the only System Administrator
                if current_user_role.role.name == Role.SYSTEM_ADMINISTRATOR and new_role.name != Role.SYSTEM_ADMINISTRATOR:
                    raise ValidationError("Cannot reassign the single System Administrator role.")

                current_user_role.role = new_role
                current_user_role.assigned_by = actor
                current_user_role.save(update_fields=["role", "assigned_by"])

        AuditService.log(
            action="USER_UPDATED",
            entity_type="User",
            entity_id=user.id,
            user=actor,
            old_values=old_values,
            new_values={"first_name": user.first_name, "last_name": user.last_name},
            result=AuditResult.SUCCESS,
        )
        return user

    @classmethod
    def toggle_user_status(cls, actor: User, user: User, is_active: bool, reason: str = "") -> User:
        """Activates or deactivates a user. Prevents deactivating the single System Administrator."""
        if user.is_system_administrator and not is_active:
            raise ValidationError("The single System Administrator account cannot be deactivated.")

        user.is_active = is_active
        user.save(update_fields=["is_active", "updated_at"])

        AuditService.log(
            action="USER_ACTIVATED" if is_active else "USER_DEACTIVATED",
            entity_type="User",
            entity_id=user.id,
            user=actor,
            reason=reason or ("Activated" if is_active else "Deactivated"),
            result=AuditResult.SUCCESS,
        )
        return user
