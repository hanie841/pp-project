from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal


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
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

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
        if not self.certificate_number:
            self.certificate_number = self._generate_certificate_number()
        if not self.invoice_number:
            self.invoice_number = self._generate_invoice_number()
            self.invoice_date = timezone.now().date()
        self.vat_amount = self.subtotal * self.vat_rate / Decimal('100')
        self.grand_total = self.subtotal + self.vat_amount
        super().save(*args, **kwargs)

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

    def _generate_invoice_number(self):
        now = timezone.now()
        date_part = now.strftime('%y%m%d')
        return f'INV_PP_SWLT_{date_part}'

    @classmethod
    def total_invoiced(cls):
        result = cls.objects.filter(
            status=cls.Status.FINALIZED
        ).aggregate(total=models.Sum('grand_total'))
        return result['total'] or Decimal('0')
