from django import forms
from apps.contributions.models import ContributionType, EntryMethod, PaymentMode


class PaymentSummaryFilters(forms.Form):
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    contribution_type = forms.ModelChoiceField(queryset=ContributionType.objects.all(), required=False)
    entry_method = forms.ChoiceField(choices=[("", "All entry methods"), *EntryMethod.choices], required=False)
    mode = forms.ChoiceField(choices=[("", "All payment methods"), *PaymentMode.choices], required=False)
    currency = forms.RegexField(regex=r"^[A-Z]{3}$", max_length=3, required=False)

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            raise forms.ValidationError("Start date must be on or before end date.")
        return data
