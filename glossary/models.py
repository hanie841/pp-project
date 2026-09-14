from django.conf import settings
from django.db import models

from .normalization import normalize


class LegalDomain(models.Model):
    """المجال القانوني - legal field a term belongs to"""
    name_ar = models.CharField('الاسم بالعربية', max_length=150)
    name_en = models.CharField('الاسم بالإنجليزية', max_length=150)
    sort_order = models.PositiveIntegerField('الترتيب', default=0)
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        verbose_name = 'مجال قانوني'
        verbose_name_plural = 'المجالات القانونية'
        ordering = ['sort_order', 'name_ar']

    def __str__(self):
        return self.name_ar


class GlossaryTermQuerySet(models.QuerySet):
    def usable(self):
        """Approved, non-archived terms: the only ones used in lookup and AI."""
        return self.filter(status=GlossaryTerm.Status.APPROVED, is_archived=False)

    def visible_to(self, user):
        from .permissions import is_manager
        if is_manager(user):
            return self
        return self.filter(
            models.Q(status=GlossaryTerm.Status.APPROVED, is_archived=False)
            | models.Q(proposed_by=user, is_archived=False)
        )


def _lines(text):
    return [line.strip() for line in (text or '').splitlines() if line.strip()]


class GlossaryTerm(models.Model):
    """مصطلح - Arabic–English legal term pair"""
    class Status(models.TextChoices):
        PROPOSED = 'PROPOSED', 'مقترح'
        APPROVED = 'APPROVED', 'معتمد'
        REJECTED = 'REJECTED', 'مرفوض'

    class PartOfSpeech(models.TextChoices):
        NOUN = 'NOUN', 'اسم'
        VERB = 'VERB', 'فعل'
        ADJECTIVE = 'ADJECTIVE', 'صفة'
        PHRASE = 'PHRASE', 'عبارة'
        ABBREVIATION = 'ABBREVIATION', 'اختصار'

    term_ar = models.CharField('المصطلح بالعربية', max_length=500)
    term_en = models.CharField('المصطلح بالإنجليزية', max_length=500)
    term_ar_normalized = models.CharField(max_length=500, editable=False, db_index=True)
    term_en_normalized = models.CharField(max_length=500, editable=False, db_index=True)
    # Normalized term and synonyms in both languages, one per line (search target).
    search_text = models.TextField(editable=False, default='')

    domain = models.ForeignKey(
        LegalDomain, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='terms', verbose_name='المجال القانوني'
    )
    part_of_speech = models.CharField(
        'نوع الكلمة', max_length=20, choices=PartOfSpeech.choices, blank=True
    )
    definition_ar = models.TextField('التعريف بالعربية', blank=True)
    definition_en = models.TextField('التعريف بالإنجليزية', blank=True)
    example_ar = models.TextField('مثال بالعربية', blank=True)
    example_en = models.TextField('مثال بالإنجليزية', blank=True)
    source_reference = models.CharField(
        'المرجع القانوني', max_length=500, blank=True,
        help_text='مثال: قانون الإجراءات الجزائية الاتحادي، المادة 5'
    )
    synonyms_ar = models.TextField('مرادفات عربية مقبولة', blank=True, help_text='صيغة واحدة في كل سطر')
    synonyms_en = models.TextField('مرادفات إنجليزية مقبولة', blank=True, help_text='صيغة واحدة في كل سطر')
    forbidden_ar = models.TextField('صيغ عربية غير مقبولة', blank=True, help_text='صيغة واحدة في كل سطر')
    forbidden_en = models.TextField('صيغ إنجليزية غير مقبولة', blank=True, help_text='صيغة واحدة في كل سطر')
    notes = models.TextField('ملاحظات', blank=True)

    status = models.CharField(
        'الحالة', max_length=20, choices=Status.choices, default=Status.PROPOSED
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='proposed_glossary_terms', verbose_name='اقترحه'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='راجعه'
    )
    reviewed_at = models.DateTimeField('تاريخ المراجعة', null=True, blank=True)
    review_note = models.TextField('ملاحظة المراجعة', blank=True)
    is_archived = models.BooleanField('مؤرشف', default=False)
    created_at = models.DateTimeField('تاريخ الإضافة', auto_now_add=True)
    updated_at = models.DateTimeField('آخر تحديث', auto_now=True)

    objects = GlossaryTermQuerySet.as_manager()

    class Meta:
        verbose_name = 'مصطلح'
        verbose_name_plural = 'المصطلحات'
        ordering = ['term_ar_normalized']
        constraints = [
            models.UniqueConstraint(
                fields=['term_ar_normalized', 'term_en_normalized'],
                name='glossary_unique_normalized_pair',
            ),
        ]

    def __str__(self):
        return f'{self.term_ar} — {self.term_en}'

    def save(self, *args, **kwargs):
        self.term_ar_normalized = normalize(self.term_ar)
        self.term_en_normalized = normalize(self.term_en)
        self.search_text = '\n'.join(
            normalize(variant) for variant in self.variants('ar') + self.variants('en')
        )
        super().save(*args, **kwargs)

    def variants(self, language):
        """The term and its accepted synonyms in one language ('ar' or 'en')."""
        if language == 'ar':
            return [self.term_ar] + _lines(self.synonyms_ar)
        return [self.term_en] + _lines(self.synonyms_en)

    def forbidden_variants(self, language):
        return _lines(self.forbidden_ar if language == 'ar' else self.forbidden_en)

    @property
    def synonyms_ar_list(self):
        return _lines(self.synonyms_ar)

    @property
    def synonyms_en_list(self):
        return _lines(self.synonyms_en)

    @property
    def forbidden_ar_list(self):
        return _lines(self.forbidden_ar)

    @property
    def forbidden_en_list(self):
        return _lines(self.forbidden_en)


TRACKED_FIELDS = (
    'term_ar', 'term_en', 'domain', 'part_of_speech',
    'definition_ar', 'definition_en', 'example_ar', 'example_en',
    'source_reference', 'synonyms_ar', 'synonyms_en', 'forbidden_ar', 'forbidden_en',
    'notes', 'status', 'review_note', 'is_archived',
)


def snapshot(term):
    return {
        field: term.domain_id if field == 'domain' else getattr(term, field)
        for field in TRACKED_FIELDS
    }


class GlossaryTermHistory(models.Model):
    """سجل تغييرات المصطلح - audit trail"""
    class Action(models.TextChoices):
        CREATED = 'CREATED', 'إنشاء'
        UPDATED = 'UPDATED', 'تعديل'
        APPROVED = 'APPROVED', 'اعتماد'
        REJECTED = 'REJECTED', 'رفض'
        ARCHIVED = 'ARCHIVED', 'أرشفة'
        RESTORED = 'RESTORED', 'استعادة'
        IMPORTED = 'IMPORTED', 'استيراد'

    term = models.ForeignKey(
        GlossaryTerm, on_delete=models.CASCADE,
        related_name='history', verbose_name='المصطلح'
    )
    action = models.CharField('الإجراء', max_length=20, choices=Action.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='+', verbose_name='بواسطة'
    )
    changed_at = models.DateTimeField('التاريخ', auto_now_add=True)
    changes = models.JSONField('التغييرات', default=dict, blank=True)

    class Meta:
        verbose_name = 'سجل تغيير'
        verbose_name_plural = 'سجل التغييرات'
        ordering = ['-changed_at', '-pk']

    def __str__(self):
        return f'{self.term} - {self.get_action_display()}'


def record_history(term, action, user, before=None):
    """Store what changed since ``before`` (a ``snapshot``) — or just the action."""
    changes = {}
    if before is not None:
        after = snapshot(term)
        changes = {
            field: [before[field], after[field]]
            for field in TRACKED_FIELDS if before[field] != after[field]
        }
    return GlossaryTermHistory.objects.create(
        term=term, action=action, changed_by=user, changes=changes,
    )


class OrderTerm(models.Model):
    """مصطلح مرتبط بأمر تكليف - agreed terminology for a work order"""
    work_order = models.ForeignKey(
        'core.WorkOrder', on_delete=models.CASCADE,
        related_name='glossary_terms', verbose_name='أمر التكليف'
    )
    term = models.ForeignKey(
        GlossaryTerm, on_delete=models.CASCADE,
        related_name='order_links', verbose_name='المصطلح'
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='+', verbose_name='أضافه'
    )
    added_at = models.DateTimeField('تاريخ الإضافة', auto_now_add=True)
    note = models.CharField('ملاحظة', max_length=500, blank=True)

    class Meta:
        verbose_name = 'مصطلح أمر التكليف'
        verbose_name_plural = 'مصطلحات أوامر التكليف'
        ordering = ['term__term_ar_normalized']
        constraints = [
            models.UniqueConstraint(fields=['work_order', 'term'], name='glossary_unique_order_term'),
        ]

    def __str__(self):
        return f'{self.work_order} - {self.term}'
