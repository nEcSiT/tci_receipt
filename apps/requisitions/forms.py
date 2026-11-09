from decimal import Decimal
import re
from django import forms
from .models import Requisition


class DraftForm(forms.ModelForm):
    amount_requested = forms.DecimalField(required=False, max_digits=12, decimal_places=2,
                                          min_value=Decimal('0.01'))

    class Meta:
        model = Requisition
        fields = ('requester_name', 'requester_phone', 'ministry', 'team', 'department',
                  'group_name', 'purpose', 'description', 'amount_requested',
                  'date_funds_needed', 'preferred_disbursement_method',
                  'preferred_disbursement_details', 'remarks')
        widgets = {'date_funds_needed': forms.DateInput(attrs={'type': 'date'}),
                   'description': forms.Textarea(attrs={'rows': 4}),
                   'preferred_disbursement_details': forms.Textarea(attrs={'rows': 3}),
                   'remarks': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, submitting=False, **kwargs):
        super().__init__(*args, **kwargs)
        if submitting:
            for name in ('requester_name', 'requester_phone', 'purpose', 'description', 'amount_requested'):
                self.fields[name].required = True
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_requester_phone(self):
        # Match the existing member service's normalization; do not silently
        # change the canonical phone format used elsewhere in the application.
        phone = self.cleaned_data['requester_phone'].strip()
        if phone and not re.fullmatch(r'\+?[0-9]{7,15}', phone):
            raise forms.ValidationError('Use 7–15 digits, optionally beginning with +.')
        return phone
