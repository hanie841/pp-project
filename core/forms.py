import logging
import os

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.contrib.auth.models import User
from django.template.defaultfilters import filesizeformat

from . import antivirus
from .models import (
    WorkOrder, WorkOrderLanguage, ServiceRecord,
    Prosecution, Prosecutor, Language,
    TranslatorProfile, OrderAssignment, OrderDocument,
)

logger = logging.getLogger(__name__)


def validate_document_files(files, order=None):
    """Type, size, the order's quota and (when required) a virus scan."""
    errors = []
    for uploaded in files:
        extension = os.path.splitext(uploaded.name)[1].lower().lstrip('.')
        if extension not in settings.DOCUMENT_ALLOWED_EXTENSIONS:
            errors.append(ValidationError(
                'نوع الملف غير مسموح: %(name)s', params={'name': uploaded.name}))
        elif uploaded.size > settings.DOCUMENT_MAX_UPLOAD_SIZE:
            errors.append(ValidationError(
                'حجم الملف %(name)s يتجاوز الحد المسموح (%(limit)s)',
                params={'name': uploaded.name,
                        'limit': filesizeformat(settings.DOCUMENT_MAX_UPLOAD_SIZE)}))
    if errors:
        raise ValidationError(errors)

    used = order.documents_total_size if order is not None else 0
    remaining = settings.DOCUMENT_MAX_ORDER_TOTAL_SIZE - used
    if sum(uploaded.size for uploaded in files) > remaining:
        raise ValidationError(
            'تتجاوز الملفات المساحة المتبقية لأمر التكليف (%(remaining)s)',
            params={'remaining': filesizeformat(max(remaining, 0))})

    if settings.DOCUMENT_VIRUS_SCAN == 'required':
        for uploaded in files:
            try:
                clean = antivirus.scan(uploaded)
            except antivirus.ScanUnavailable:
                logger.exception('Virus scan unavailable for upload %s', uploaded.name)
                raise ValidationError('تعذر فحص الملف، حاول لاحقاً')
            if not clean:
                raise ValidationError(
                    'تم رفض الملف %(name)s لاحتوائه على برمجيات ضارة',
                    params={'name': uploaded.name})


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Several files in one input. Set ``order`` to enforce that order's quota."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={'class': 'form-control'}))
        super().__init__(*args, **kwargs)
        self.order = None

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        items = data if isinstance(data, (list, tuple)) else [data]
        files = [f for f in (single_file_clean(item, initial) for item in items) if f]
        if self.required and not files:
            raise ValidationError(self.error_messages['required'], code='required')
        if files:
            validate_document_files(files, self.order)
        return files


class WorkOrderForm(forms.ModelForm):
    source_files = MultipleFileField(label='المستندات المطلوب ترجمتها', required=False)

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
            'estimated_hours': forms.NumberInput(attrs={'class': 'form-control hours-field', 'step': '1', 'min': '1'}),
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
            'actual_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '1'}),
            'actual_pages': forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '1'}),
            'num_translators': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def clean(self):
        cleaned_data = super().clean()
        hours = cleaned_data.get('actual_hours')
        pages = cleaned_data.get('actual_pages')
        if not hours and not pages and not self.has_error('actual_hours') \
                and not self.has_error('actual_pages'):
            raise forms.ValidationError('يرجى إدخال الساعات الفعلية أو الصفحات الفعلية')
        for field, value in (('actual_hours', hours), ('actual_pages', pages)):
            if value is not None and value < 0:
                self.add_error(field, 'يجب ألا تكون القيمة سالبة')
        translators = cleaned_data.get('num_translators')
        if translators is not None and translators < 1:
            self.add_error('num_translators', 'يجب أن يكون عدد المترجمين 1 على الأقل')
        return cleaned_data


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


class AssignTranslatorForm(forms.Form):
    """Contract manager picks a translator for a language line"""
    translator = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label='المترجم',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, language_line=None, service_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        if language_line:
            lang = language_line.language
            profiles = TranslatorProfile.objects.filter(
                is_active=True, languages=lang
            )
            if service_type == WorkOrder.ServiceType.INTERPRETATION:
                profiles = profiles.filter(can_interpret=True)
            elif service_type in (
                WorkOrder.ServiceType.WRITTEN,
                WorkOrder.ServiceType.REVIEW,
                WorkOrder.ServiceType.AI_REVIEW,
            ):
                profiles = profiles.filter(can_translate=True)
            self.fields['translator'].queryset = User.objects.filter(
                pk__in=profiles.values_list('user_id', flat=True)
            )


class TranslatorServiceForm(forms.Form):
    """Translator logs actual hours/pages for their assignment"""
    actual_hours = forms.DecimalField(
        label='الساعات الفعلية',
        max_digits=6, decimal_places=2, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '1'}),
    )
    actual_pages = forms.DecimalField(
        label='الصفحات الفعلية',
        max_digits=6, decimal_places=2, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '1'}),
    )
    notes = forms.CharField(
        label='ملاحظات',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
    )
    translation_files = MultipleFileField(label='ملفات الترجمة', required=False)

    def __init__(self, *args, require_translation=False,
                 has_existing_translation=False, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_translation = require_translation
        self.has_existing_translation = has_existing_translation
        self.fields['translation_files'].order = order

    def clean(self):
        cleaned_data = super().clean()
        if (self.require_translation and not self.has_existing_translation
                and not cleaned_data.get('translation_files')
                and not self.has_error('translation_files')):
            self.add_error('translation_files', 'يرجى رفع ملف الترجمة قبل تسجيل الخدمة')
        hours = cleaned_data.get('actual_hours')
        pages = cleaned_data.get('actual_pages')
        if not hours and not pages:
            raise forms.ValidationError('يرجى إدخال الساعات الفعلية أو الصفحات الفعلية')
        return cleaned_data


class DocumentUploadForm(forms.Form):
    files = MultipleFileField(label='الملفات')
    note = forms.CharField(label='ملاحظة', required=False, max_length=2000)

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['files'].order = order


class DocumentNoteForm(forms.Form):
    body = forms.CharField(label='الملاحظة', max_length=4000)
    document = forms.ModelChoiceField(
        label='الملف المعني', queryset=OrderDocument.objects.none(), required=False,
    )

    def __init__(self, *args, language_line=None, **kwargs):
        super().__init__(*args, **kwargs)
        if language_line is not None:
            self.fields['document'].queryset = language_line.documents.filter(
                kind=OrderDocument.Kind.TRANSLATION
            )
