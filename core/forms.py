from django import forms
from django.forms import inlineformset_factory
from .models import (
    WorkOrder, WorkOrderLanguage, ServiceRecord,
    Prosecution, Prosecutor, Language,
)


class WorkOrderForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        fields = [
            'prosecution', 'prosecutor', 'custom_prosecutor_name',
            'service_type',
            'execution_date', 'execution_time', 'estimated_duration',
            'location_type', 'location_detail',
            'contact_name', 'contact_phone', 'notes',
        ]
        widgets = {
            'prosecution': forms.Select(attrs={'class': 'form-select', 'hx-get': '/orders/prosecutors/', 'hx-target': '#id_prosecutor', 'hx-trigger': 'change'}),
            'prosecutor': forms.Select(attrs={'class': 'form-select'}),
            'custom_prosecutor_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'أو اكتب اسم المحقق هنا'}),
            'service_type': forms.Select(attrs={'class': 'form-select', 'id': 'id_service_type'}),
            'execution_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'execution_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'estimated_duration': forms.Select(attrs={'class': 'form-select'}),
            'location_type': forms.RadioSelect(),
            'location_detail': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'contact_name': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_phone': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['prosecutor'].required = False
        self.fields['prosecution'].queryset = Prosecution.objects.filter(is_active=True)
        if user and hasattr(user, 'profile') and user.profile.prosecution:
            self.fields['prosecution'].initial = user.profile.prosecution
            self.fields['prosecutor'].queryset = Prosecutor.objects.filter(
                prosecution=user.profile.prosecution, is_active=True
            )
        else:
            self.fields['prosecutor'].queryset = Prosecutor.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        prosecutor = cleaned_data.get('prosecutor')
        custom_name = cleaned_data.get('custom_prosecutor_name', '').strip()
        if not prosecutor and not custom_name:
            raise forms.ValidationError('يرجى اختيار المحقق من القائمة أو كتابة اسمه يدوياً')
        return cleaned_data


class WorkOrderLanguageForm(forms.ModelForm):
    class Meta:
        model = WorkOrderLanguage
        fields = ['language', 'custom_language_name', 'num_translators', 'estimated_hours', 'estimated_pages']
        widgets = {
            'language': forms.Select(attrs={'class': 'form-select language-select'}),
            'custom_language_name': forms.TextInput(attrs={'class': 'form-control custom-lang-name', 'placeholder': 'اسم اللغة', 'style': 'display:none;'}),
            'num_translators': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'value': 1}),
            'estimated_hours': forms.NumberInput(attrs={'class': 'form-control hours-field', 'step': '0.5', 'min': '0.5'}),
            'estimated_pages': forms.NumberInput(attrs={'class': 'form-control pages-field', 'step': '1', 'min': '1'}),
        }


WorkOrderLanguageFormSet = inlineformset_factory(
    WorkOrder, WorkOrderLanguage,
    form=WorkOrderLanguageForm,
    extra=1, can_delete=True, min_num=1, validate_min=True,
)


class ServiceRecordForm(forms.ModelForm):
    class Meta:
        model = ServiceRecord
        fields = ['actual_hours', 'actual_pages', 'num_translators', 'notes']
        widgets = {
            'actual_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5', 'min': '0.5'}),
            'actual_pages': forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '1'}),
            'num_translators': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class MeetingLinkForm(forms.Form):
    meeting_link = forms.URLField(
        label='رابط الاجتماع',
        widget=forms.URLInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'https://...'})
    )


class DisputeForm(forms.Form):
    reason = forms.CharField(
        label='سبب الاعتراض',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
    )
