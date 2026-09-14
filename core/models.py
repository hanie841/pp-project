import os
import uuid

from django.db import IntegrityError, models, transaction
from django.conf import settings
from django.utils import timezone
from decimal import Decimal

from .storage import get_document_storage

NUMBER_SAVE_ATTEMPTS = 5


def _save_with_generated_number(assign_number, save):
    """Save a row whose unique number is generated as "last number + 1".

    Two concurrent saves can generate the same number. The unique constraint
    rejects the loser, so regenerate and retry inside a savepoint.
    """
    for attempt in range(NUMBER_SAVE_ATTEMPTS):
        assign_number()
        try:
            with transaction.atomic():
                return save()
        except IntegrityError:
            if attempt == NUMBER_SAVE_ATTEMPTS - 1:
                raise


class Prosecution(models.Model):
    """النيابة - Prosecution office"""
    name = models.CharField('اسم النيابة', max_length=200)
    code = models.CharField('الرمز', max_length=20, unique=True)
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        verbose_name = 'النيابة'
        verbose_name_plural = 'النيابات'
        ordering = ['name']

    def __str__(self):
        return self.name


class Prosecutor(models.Model):
    """المحقق - Prosecutor"""
    prosecution = models.ForeignKey(
        Prosecution, on_delete=models.CASCADE,
        related_name='prosecutors', verbose_name='النيابة'
    )
    name = models.CharField('اسم المحقق', max_length=200)
    phone = models.CharField('الهاتف', max_length=20, blank=True)
    email = models.EmailField('البريد الإلكتروني', blank=True)
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        verbose_name = 'المحقق'
        verbose_name_plural = 'المحققون'
        ordering = ['name']

    def __str__(self):
        return f'{self.name} - {self.prosecution.name}'


class UserProfile(models.Model):
    """Extended user profile with role and prosecution link"""
    class Role(models.TextChoices):
        PP_STAFF = 'PP_STAFF', 'موظف النيابة'
        SMARTWORLD_ADMIN = 'SMARTWORLD_ADMIN', 'مدير سمارت وورلد'
        SMARTWORLD_STAFF = 'SMARTWORLD_STAFF', 'موظف سمارت وورلد'
        CONTRACT_MANAGER = 'CONTRACT_MANAGER', 'مدير العقد'
        TRANSLATOR = 'TRANSLATOR', 'مترجم'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='profile', verbose_name='المستخدم'
    )
    role = models.CharField('الدور', max_length=20, choices=Role.choices)
    prosecution = models.ForeignKey(
        Prosecution, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='النيابة', help_text='فقط لموظفي النيابة'
    )

    class Meta:
        verbose_name = 'ملف المستخدم'
        verbose_name_plural = 'ملفات المستخدمين'

    def __str__(self):
        return f'{self.user.get_full_name()} ({self.get_role_display()})'

    @property
    def is_pp_staff(self):
        return self.role == self.Role.PP_STAFF

    @property
    def is_smartworld(self):
        return self.role in (self.Role.SMARTWORLD_ADMIN, self.Role.SMARTWORLD_STAFF)

    @property
    def is_smartworld_admin(self):
        return self.role == self.Role.SMARTWORLD_ADMIN

    @property
    def is_contract_manager(self):
        return self.role == self.Role.CONTRACT_MANAGER

    @property
    def is_translator(self):
        return self.role == self.Role.TRANSLATOR


class Language(models.Model):
    """اللغة - Language with rate card"""
    name_ar = models.CharField('الاسم بالعربية', max_length=100)
    name_en = models.CharField('الاسم بالإنجليزية', max_length=100)
    hourly_rate = models.DecimalField(
        'سعر الساعة (ترجمة فورية)', max_digits=10, decimal_places=2
    )
    page_rate = models.DecimalField(
        'سعر الصفحة (ترجمة تحريرية)', max_digits=10, decimal_places=2
    )

    class Meta:
        verbose_name = 'اللغة'
        verbose_name_plural = 'اللغات'
        ordering = ['name_ar']

    def __str__(self):
        return self.name_ar


class WorkOrder(models.Model):
    """أمر تكليف - Work Order"""
    class ServiceType(models.TextChoices):
        INTERPRETATION = 'INTERPRETATION', 'ترجمة فورية'
        WRITTEN = 'WRITTEN', 'ترجمة تحريرية'
        REVIEW = 'REVIEW', 'مراجعة'
        AI_REVIEW = 'AI_REVIEW', 'مراجعة بالذكاء الاصطناعي'

    class LocationType(models.TextChoices):
        ONSITE = 'ONSITE', 'حضوري'
        ONLINE = 'ONLINE', 'عن بعد (رابط متوفر)'
        NEED_LINK = 'NEED_LINK', 'عن بعد (يرجى توفير الرابط)'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'مسودة'
        SUBMITTED = 'SUBMITTED', 'مقدم'
        ACCEPTED = 'ACCEPTED', 'مقبول'
        ASSIGNED = 'ASSIGNED', 'تم التعيين'
        IN_PROGRESS = 'IN_PROGRESS', 'قيد التنفيذ'
        PENDING_APPROVAL = 'PENDING_APPROVAL', 'بانتظار الاعتماد'
        COMPLETED = 'COMPLETED', 'مكتمل'
        DISPUTED = 'DISPUTED', 'متنازع عليه'

    DURATION_CHOICES = [
        (Decimal('1'), 'ساعة واحدة'),
        (Decimal('2'), 'ساعتان'),
        (Decimal('3'), '3 ساعات'),
        (Decimal('4'), '4 ساعات'),
        (Decimal('5'), '5 ساعات'),
        (Decimal('6'), '6 ساعات'),
        (Decimal('8'), '8 ساعات'),
    ]

    order_number = models.CharField('رقم أمر التكليف', max_length=20, unique=True, editable=False)
    request_date = models.DateField('تاريخ الطلب', default=timezone.now)
    prosecution = models.ForeignKey(
        Prosecution, on_delete=models.PROTECT,
        related_name='work_orders', verbose_name='النيابة'
    )
    prosecutor = models.ForeignKey(
        Prosecutor, on_delete=models.PROTECT,
        related_name='work_orders', verbose_name='المحقق',
        null=True, blank=True
    )
    custom_prosecutor_name = models.CharField(
        'اسم المحقق (يدوي)', max_length=200, blank=True,
        help_text='في حال عدم وجود المحقق في القائمة'
    )
    service_type = models.CharField(
        'نوع الخدمة', max_length=20, choices=ServiceType.choices
    )
    execution_date = models.DateField('تاريخ التنفيذ')
    execution_time = models.TimeField('وقت التنفيذ')
    estimated_duration = models.DecimalField(
        'المدة التقديرية (ساعات)', max_digits=4, decimal_places=1,
        choices=DURATION_CHOICES, null=True, blank=True
    )
    location_type = models.CharField(
        'نوع الموقع', max_length=20, choices=LocationType.choices
    )
    location_detail = models.TextField(
        'تفاصيل الموقع', blank=True,
        help_text='العنوان أو رابط الاجتماع'
    )
    contact_name = models.CharField('اسم جهة الاتصال', max_length=200, blank=True)
    contact_phone = models.CharField('هاتف جهة الاتصال', max_length=20, blank=True)
    notes = models.TextField('ملاحظات', blank=True)
    conference_room = models.CharField(
        'غرفة الاجتماع', max_length=100, blank=True,
        help_text='LiveKit room name (auto-generated)'
    )
    status = models.CharField(
        'الحالة', max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='created_orders', verbose_name='أنشئ بواسطة'
    )
    submitted_at = models.DateTimeField('تاريخ التقديم', null=True, blank=True)
    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)

    class Meta:
        verbose_name = 'أمر تكليف'
        verbose_name_plural = 'أوامر التكليف'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.order_number} - {self.prosecution.name}'

    def save(self, *args, **kwargs):
        if self.order_number:
            return super().save(*args, **kwargs)

        def assign_number():
            self.order_number = self._generate_order_number()

        return _save_with_generated_number(
            assign_number, lambda: super(WorkOrder, self).save(*args, **kwargs)
        )

    def _generate_order_number(self):
        year = timezone.now().year
        prefix = f'{year}/AO/'
        last = WorkOrder.objects.filter(
            order_number__startswith=prefix
        ).order_by('-order_number').first()
        if last:
            last_num = int(last.order_number.split('/')[-1])
            new_num = last_num + 1
        else:
            new_num = 1
        return f'{prefix}{new_num:04d}'

    @property
    def estimated_total(self):
        total = Decimal('0')
        for lang_line in self.languages.all():
            total += lang_line.estimated_amount
        return total

    @property
    def prosecutor_display(self):
        if self.prosecutor:
            return self.prosecutor.name
        return self.custom_prosecutor_name or '-'

    @property
    def is_interpretation(self):
        return self.service_type == self.ServiceType.INTERPRETATION

    @property
    def is_written(self):
        return self.service_type in (
            self.ServiceType.WRITTEN,
            self.ServiceType.REVIEW,
            self.ServiceType.AI_REVIEW,
        )

    @property
    def documents_total_size(self):
        """Bytes used by this order's stored documents (counts toward its quota)."""
        return self.documents.filter(purged_at__isnull=True).aggregate(
            total=models.Sum('size')
        )['total'] or 0


class WorkOrderLanguage(models.Model):
    """Language line item for a work order"""
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE,
        related_name='languages', verbose_name='أمر التكليف'
    )
    language = models.ForeignKey(
        Language, on_delete=models.PROTECT, verbose_name='اللغة'
    )
    custom_language_name = models.CharField(
        'اسم اللغة (يدوي)', max_length=200, blank=True,
        help_text='في حال اختيار "أخرى"'
    )
    num_translators = models.PositiveIntegerField('عدد المترجمين', default=1)
    estimated_hours = models.DecimalField(
        'الساعات التقديرية', max_digits=6, decimal_places=2, null=True, blank=True
    )
    estimated_pages = models.DecimalField(
        'الصفحات التقديرية', max_digits=6, decimal_places=2, null=True, blank=True
    )

    class Meta:
        verbose_name = 'لغة أمر التكليف'
        verbose_name_plural = 'لغات أمر التكليف'

    def __str__(self):
        return f'{self.language_display} x{self.num_translators}'

    @property
    def language_display(self):
        if self.language.name_en == 'Other' and self.custom_language_name:
            return self.custom_language_name
        return self.language.name_ar

    @property
    def estimated_amount(self):
        if self.estimated_hours and self.language:
            return (self.estimated_hours * self.language.hourly_rate
                    * self.num_translators)
        elif self.estimated_pages and self.language:
            return (self.estimated_pages * self.language.page_rate
                    * self.num_translators)
        return Decimal('0')


class ServiceRecord(models.Model):
    """Actual work done - one per language line"""
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE,
        related_name='service_records', verbose_name='أمر التكليف'
    )
    language = models.ForeignKey(
        Language, on_delete=models.PROTECT, verbose_name='اللغة'
    )
    num_translators = models.PositiveIntegerField('عدد المترجمين', default=1)
    actual_hours = models.DecimalField(
        'الساعات الفعلية', max_digits=6, decimal_places=2, null=True, blank=True
    )
    actual_pages = models.DecimalField(
        'الصفحات الفعلية', max_digits=6, decimal_places=2, null=True, blank=True
    )
    unit_rate = models.DecimalField(
        'سعر الوحدة', max_digits=10, decimal_places=2
    )
    amount = models.DecimalField(
        'المبلغ', max_digits=12, decimal_places=2
    )
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        verbose_name = 'سجل الخدمة'
        verbose_name_plural = 'سجلات الخدمة'

    def __str__(self):
        return f'{self.work_order.order_number} - {self.language.name_ar}'

    def calculate_amount(self):
        if self.actual_hours:
            self.unit_rate = self.language.hourly_rate
            self.amount = (self.actual_hours * self.unit_rate
                          * self.num_translators)
        elif self.actual_pages:
            self.unit_rate = self.language.page_rate
            self.amount = (self.actual_pages * self.unit_rate
                          * self.num_translators)
        else:
            self.amount = Decimal('0')


class WorkOrderApproval(models.Model):
    """Dual approval record"""
    work_order = models.OneToOneField(
        WorkOrder, on_delete=models.CASCADE,
        related_name='approval', verbose_name='أمر التكليف'
    )
    smartworld_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sw_approvals',
        verbose_name='اعتماد سمارت وورلد'
    )
    smartworld_approved_at = models.DateTimeField(
        'تاريخ اعتماد سمارت وورلد', null=True, blank=True
    )
    pp_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pp_approvals',
        verbose_name='اعتماد النيابة'
    )
    pp_approved_at = models.DateTimeField(
        'تاريخ اعتماد النيابة', null=True, blank=True
    )
    pp_dispute_reason = models.TextField(
        'سبب الاعتراض', blank=True
    )

    class Meta:
        verbose_name = 'اعتماد أمر التكليف'
        verbose_name_plural = 'اعتمادات أوامر التكليف'

    def __str__(self):
        return f'اعتماد {self.work_order.order_number}'


class CompletionCertificate(models.Model):
    """شهادة إنجاز - Completion Certificate"""
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'مسودة'
        FINALIZED = 'FINALIZED', 'نهائي'

    work_order = models.OneToOneField(
        WorkOrder, on_delete=models.CASCADE,
        related_name='certificate', verbose_name='أمر التكليف'
    )
    certificate_number = models.CharField(
        'رقم الشهادة', max_length=30, unique=True, editable=False
    )
    certificate_date = models.DateField('تاريخ الشهادة', default=timezone.now)
    invoice_number = models.CharField('رقم الفاتورة', max_length=30, blank=True)
    invoice_date = models.DateField('تاريخ الفاتورة', null=True, blank=True)
    subtotal = models.DecimalField('المجموع الفرعي', max_digits=12, decimal_places=2)
    vat_rate = models.DecimalField(
        'نسبة الضريبة', max_digits=5, decimal_places=2,
        default=Decimal('5.00')
    )
    vat_amount = models.DecimalField('مبلغ الضريبة', max_digits=12, decimal_places=2)
    grand_total = models.DecimalField('المجموع الكلي', max_digits=12, decimal_places=2)
    status = models.CharField(
        'الحالة', max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    generated_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)

    class Meta:
        verbose_name = 'شهادة إنجاز'
        verbose_name_plural = 'شهادات الإنجاز'
        ordering = ['-generated_at']

    def __str__(self):
        return f'{self.certificate_number} - {self.work_order.order_number}'

    def save(self, *args, **kwargs):
        self.vat_amount = self.subtotal * self.vat_rate / Decimal('100')
        self.grand_total = self.subtotal + self.vat_amount
        if self.certificate_number:
            if not self.invoice_number:
                self._assign_invoice_number()
            return super().save(*args, **kwargs)

        generate_invoice = not self.invoice_number

        def assign_numbers():
            self.certificate_number = self._generate_certificate_number()
            if generate_invoice:
                self._assign_invoice_number()

        return _save_with_generated_number(
            assign_numbers,
            lambda: super(CompletionCertificate, self).save(*args, **kwargs),
        )

    def _generate_certificate_number(self):
        year = timezone.now().year
        prefix = f'{year}/SC/'
        last = CompletionCertificate.objects.filter(
            certificate_number__startswith=prefix
        ).order_by('-certificate_number').first()
        if last:
            last_num = int(last.certificate_number.split('/')[-1])
            new_num = last_num + 1
        else:
            new_num = 1
        return f'{prefix}{new_num:04d}'

    def _assign_invoice_number(self):
        # The certificate sequence keeps invoice numbers unique; the date
        # alone was shared by every certificate issued on the same day.
        now = timezone.now()
        sequence = self.certificate_number.rsplit('/', 1)[-1]
        self.invoice_number = f'INV_PP_SWLT_{now:%y%m%d}_{sequence}'
        self.invoice_date = now.date()

    @classmethod
    def total_invoiced(cls):
        result = cls.objects.filter(
            status=cls.Status.FINALIZED
        ).aggregate(total=models.Sum('grand_total'))
        return result['total'] or Decimal('0')


class TranslatorProfile(models.Model):
    """Translator/Interpreter profile with language capabilities"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='translator_profile', verbose_name='المستخدم'
    )
    languages = models.ManyToManyField(Language, verbose_name='اللغات')
    can_interpret = models.BooleanField('ترجمة فورية', default=True)
    can_translate = models.BooleanField('ترجمة تحريرية', default=True)
    phone = models.CharField('الهاتف', max_length=20, blank=True)
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        verbose_name = 'ملف مترجم'
        verbose_name_plural = 'ملفات المترجمين'

    def __str__(self):
        return f'{self.user.get_full_name()} - مترجم'


class OrderAssignment(models.Model):
    """Assignment of a translator to a specific language line of an order"""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'بانتظار القبول'
        ACCEPTED = 'ACCEPTED', 'مقبول'
        DECLINED = 'DECLINED', 'مرفوض'
        COMPLETED = 'COMPLETED', 'مكتمل'

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE,
        related_name='assignments', verbose_name='أمر التكليف'
    )
    language_line = models.ForeignKey(
        WorkOrderLanguage, on_delete=models.CASCADE,
        related_name='assignments', verbose_name='بند اللغة'
    )
    translator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='assignments', verbose_name='المترجم'
    )
    status = models.CharField(
        'الحالة', max_length=20, choices=Status.choices,
        default=Status.PENDING
    )
    assigned_at = models.DateTimeField('تاريخ التعيين', auto_now_add=True)
    accepted_at = models.DateTimeField('تاريخ القبول', null=True, blank=True)
    completed_at = models.DateTimeField('تاريخ الإنجاز', null=True, blank=True)
    service_record = models.OneToOneField(
        ServiceRecord, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assignment',
        verbose_name='سجل الخدمة'
    )

    class Meta:
        verbose_name = 'تعيين مترجم'
        verbose_name_plural = 'تعيينات المترجمين'
        ordering = ['-assigned_at']

    def __str__(self):
        return f'{self.translator.get_full_name()} → {self.work_order.order_number} ({self.language_line.language_display})'


class WorkflowConfig(models.Model):
    """Singleton: admin controls for workflow mode"""
    class Mode(models.TextChoices):
        MANUAL = 'MANUAL', 'يدوي (مدير العقد يعين المترجمين)'
        AUTO = 'AUTO', 'تلقائي (المترجمون يختارون الأوامر)'

    mode = models.CharField(
        'وضع سير العمل', max_length=10,
        choices=Mode.choices, default=Mode.MANUAL
    )
    updated_at = models.DateTimeField('آخر تحديث', auto_now=True)

    class Meta:
        verbose_name = 'إعدادات سير العمل'
        verbose_name_plural = 'إعدادات سير العمل'

    def __str__(self):
        return f'وضع سير العمل: {self.get_mode_display()}'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ConferenceRecording(models.Model):
    """تسجيل اجتماع مرئي - Conference session recording"""
    class Status(models.TextChoices):
        RECORDING = 'RECORDING', 'جاري التسجيل'
        STOPPING = 'STOPPING', 'جاري الإيقاف'
        COMPLETED = 'COMPLETED', 'مكتمل'
        FAILED = 'FAILED', 'فشل'

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE,
        related_name='recordings', verbose_name='أمر التكليف'
    )
    egress_id = models.CharField('معرف التسجيل', max_length=100, unique=True)
    room_name = models.CharField('اسم الغرفة', max_length=100)
    file_path = models.CharField('مسار الملف', max_length=500, blank=True)
    file_size = models.BigIntegerField('حجم الملف', null=True, blank=True)
    status = models.CharField(
        'الحالة', max_length=20, choices=Status.choices,
        default=Status.RECORDING
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, verbose_name='بدأ بواسطة'
    )
    started_at = models.DateTimeField('تاريخ البدء', auto_now_add=True)
    stopped_at = models.DateTimeField('تاريخ الإيقاف', null=True, blank=True)

    class Meta:
        verbose_name = 'تسجيل اجتماع'
        verbose_name_plural = 'تسجيلات الاجتماعات'
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.work_order.order_number} - {self.started_at}'


DOCUMENT_CONTENT_TYPES = {
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'ppt': 'application/vnd.ms-powerpoint',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'rtf': 'application/rtf',
    'txt': 'text/plain',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'tif': 'image/tiff',
    'tiff': 'image/tiff',
}


def content_type_for(filename):
    """Content type from the extension; the browser-supplied type isn't trusted."""
    extension = os.path.splitext(filename)[1].lower().lstrip('.')
    return DOCUMENT_CONTENT_TYPES.get(extension, 'application/octet-stream')


def order_document_path(instance, filename):
    # Random name on disk: nothing user-controlled ends up in the path.
    extension = os.path.splitext(filename)[1].lower()
    return f'orders/{instance.work_order_id}/{instance.kind.lower()}/{uuid.uuid4().hex}{extension}'


class OrderDocument(models.Model):
    """مستند أمر التكليف - file to translate or delivered translation"""
    class Kind(models.TextChoices):
        SOURCE = 'SOURCE', 'مستند للترجمة'
        TRANSLATION = 'TRANSLATION', 'ترجمة منجزة'

    class ScanStatus(models.TextChoices):
        CLEAN = 'CLEAN', 'سليم'
        NOT_SCANNED = 'NOT_SCANNED', 'غير مفحوص'

    PREVIEWABLE_CONTENT_TYPES = frozenset({'application/pdf', 'image/jpeg', 'image/png'})

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE,
        related_name='documents', verbose_name='أمر التكليف'
    )
    kind = models.CharField('النوع', max_length=20, choices=Kind.choices)
    language_line = models.ForeignKey(
        WorkOrderLanguage, on_delete=models.CASCADE, null=True, blank=True,
        related_name='documents', verbose_name='بند اللغة'
    )
    assignment = models.ForeignKey(
        OrderAssignment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documents', verbose_name='التعيين'
    )
    file = models.FileField(
        'الملف', upload_to=order_document_path,
        storage=get_document_storage, max_length=255
    )
    original_name = models.CharField('اسم الملف', max_length=255)
    size = models.PositiveBigIntegerField('الحجم')
    content_type = models.CharField('نوع المحتوى', max_length=100)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='uploaded_documents', verbose_name='رفع بواسطة'
    )
    uploaded_at = models.DateTimeField('تاريخ الرفع', auto_now_add=True)
    note = models.TextField('ملاحظة', blank=True)
    scan_status = models.CharField(
        'نتيجة الفحص', max_length=20, choices=ScanStatus.choices,
        default=ScanStatus.NOT_SCANNED
    )
    purged_at = models.DateTimeField('تاريخ الحذف (سياسة الاحتفاظ)', null=True, blank=True)

    class Meta:
        verbose_name = 'مستند'
        verbose_name_plural = 'المستندات'
        ordering = ['-uploaded_at', '-pk']

    def __str__(self):
        return f'{self.work_order.order_number} - {self.original_name}'

    @property
    def is_previewable(self):
        return self.purged_at is None and self.content_type in self.PREVIEWABLE_CONTENT_TYPES


class DocumentNote(models.Model):
    """ملاحظة مراجعة - revision request on a delivered translation"""
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'بحاجة لمعالجة'
        ADDRESSED = 'ADDRESSED', 'تمت المعالجة'

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE,
        related_name='document_notes', verbose_name='أمر التكليف'
    )
    language_line = models.ForeignKey(
        WorkOrderLanguage, on_delete=models.CASCADE,
        related_name='document_notes', verbose_name='بند اللغة'
    )
    document = models.ForeignKey(
        OrderDocument, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='review_notes', verbose_name='الملف المعني'
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='document_notes', verbose_name='الكاتب'
    )
    body = models.TextField('الملاحظة')
    status = models.CharField(
        'الحالة', max_length=20, choices=Status.choices, default=Status.OPEN
    )
    created_at = models.DateTimeField('تاريخ الإضافة', auto_now_add=True)
    addressed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='عولجت بواسطة'
    )
    addressed_at = models.DateTimeField('تاريخ المعالجة', null=True, blank=True)
    addressed_by_document = models.ForeignKey(
        OrderDocument, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='addressed_notes', verbose_name='النسخة المعدلة'
    )

    class Meta:
        verbose_name = 'ملاحظة مراجعة'
        verbose_name_plural = 'ملاحظات المراجعة'
        ordering = ['created_at', 'pk']

    def __str__(self):
        return f'{self.work_order.order_number} - {self.get_status_display()}'

    @classmethod
    def address_open_notes(cls, language_line, document, user):
        """Mark a line's open notes as addressed by a newly uploaded translation."""
        return cls.objects.filter(
            language_line=language_line, status=cls.Status.OPEN,
        ).update(
            status=cls.Status.ADDRESSED,
            addressed_by=user,
            addressed_at=timezone.now(),
            addressed_by_document=document,
        )
