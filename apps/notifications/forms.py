from django import forms
from django.core.exceptions import ValidationError

from apps.contributions.models import ContributionType
from apps.notifications.models import NotificationTemplate, NotificationType
from apps.notifications.services import NotificationTemplateService


class NotificationTemplateForm(forms.ModelForm):
    """
    Form for creating and editing configurable SMS notification templates.
    Validates placeholder syntax and approved variables.
    """

    contribution_type = forms.ModelChoiceField(
        queryset=ContributionType.objects.filter(is_active=True).order_by("name"),
        required=False,
        empty_label="Generic / All Categories (Fallback)",
        help_text="Select a category for a category-specific message, or leave blank to serve as generic fallback.",
    )

    class Meta:
        model = NotificationTemplate
        fields = [
            "name",
            "notification_type",
            "contribution_type",
            "template_body",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. Tithe Thank You SMS",
            }),
            "notification_type": forms.Select(attrs={
                "class": "form-control",
            }),
            "template_body": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Dear {first_name}, thank you for your contribution of {amount} {currency} towards {contribution_type}...",
            }),
        }

    def clean_template_body(self):
        body = self.cleaned_data.get("template_body", "").strip()
        if not body:
            raise ValidationError("Template wording cannot be blank.")
        # Validate approved placeholders
        NotificationTemplateService.validate_template_body(body)
        return body
