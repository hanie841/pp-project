import os

from django import forms

from .models import GlossaryTerm, LegalDomain
from .normalization import normalize

_AR = {'class': 'form-control', 'dir': 'rtl'}
_EN = {'class': 'form-control', 'dir': 'ltr'}
DUPLICATE_MESSAGE = 'هذا المصطلح موجود بالفعل في المسرد'
MAX_IMPORT_FILE_SIZE = 5 * 1024 * 1024


class GlossaryTermForm(forms.ModelForm):
    class Meta:
        model = GlossaryTerm
        fields = [
            'term_ar', 'term_en', 'domain', 'part_of_speech',
            'definition_ar', 'definition_en', 'example_ar', 'example_en',
            'synonyms_ar', 'synonyms_en', 'forbidden_ar', 'forbidden_en',
            'source_reference', 'notes',
        ]
        widgets = {
            'term_ar': forms.TextInput(attrs=_AR),
            'term_en': forms.TextInput(attrs=_EN),
            'domain': forms.Select(attrs={'class': 'form-select'}),
            'part_of_speech': forms.Select(attrs={'class': 'form-select'}),
            'definition_ar': forms.Textarea(attrs={**_AR, 'rows': 3}),
            'definition_en': forms.Textarea(attrs={**_EN, 'rows': 3}),
            'example_ar': forms.Textarea(attrs={**_AR, 'rows': 2}),
            'example_en': forms.Textarea(attrs={**_EN, 'rows': 2}),
            'synonyms_ar': forms.Textarea(attrs={**_AR, 'rows': 2}),
            'synonyms_en': forms.Textarea(attrs={**_EN, 'rows': 2}),
            'forbidden_ar': forms.Textarea(attrs={**_AR, 'rows': 2}),
            'forbidden_en': forms.Textarea(attrs={**_EN, 'rows': 2}),
            'source_reference': forms.TextInput(attrs=_AR),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['domain'].queryset = LegalDomain.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        term_ar, term_en = cleaned_data.get('term_ar'), cleaned_data.get('term_en')
        if term_ar is not None and not normalize(term_ar):
            self.add_error('term_ar', 'يجب أن يحتوي المصطلح على حروف')
        if term_en is not None and not normalize(term_en):
            self.add_error('term_en', 'يجب أن يحتوي المصطلح على حروف')
        if term_ar and term_en:
            duplicates = GlossaryTerm.objects.filter(
                term_ar_normalized=normalize(term_ar), term_en_normalized=normalize(term_en),
            )
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise forms.ValidationError(DUPLICATE_MESSAGE)
        return cleaned_data


class ReviewForm(forms.Form):
    decision = forms.ChoiceField(choices=[('approve', 'اعتماد'), ('reject', 'رفض')])
    note = forms.CharField(required=False, max_length=2000)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('decision') == 'reject' and not cleaned_data.get('note'):
            self.add_error('note', 'يرجى ذكر سبب الرفض')
        return cleaned_data


class ImportForm(forms.Form):
    file = forms.FileField(
        label='ملف المصطلحات (Excel أو CSV)',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.csv'}),
    )
    on_duplicate = forms.ChoiceField(
        label='المصطلحات الموجودة مسبقاً',
        choices=[('skip', 'تجاهلها'), ('update', 'تحديثها ببيانات الملف')],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    import_as = forms.ChoiceField(
        label='حالة المصطلحات الجديدة',
        choices=[
            (GlossaryTerm.Status.APPROVED, 'معتمدة مباشرة'),
            (GlossaryTerm.Status.PROPOSED, 'مقترحات للمراجعة'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def clean_file(self):
        uploaded = self.cleaned_data['file']
        if os.path.splitext(uploaded.name)[1].lower() not in ('.xlsx', '.csv'):
            raise forms.ValidationError('يرجى رفع ملف بصيغة XLSX أو CSV')
        if uploaded.size > MAX_IMPORT_FILE_SIZE:
            raise forms.ValidationError('الحد الأقصى لحجم الملف 5 ميغابايت')
        return uploaded
