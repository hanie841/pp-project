from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import (
    Prosecution, Prosecutor, UserProfile, Language,
    WorkOrder, WorkOrderLanguage, ServiceRecord,
    WorkOrderApproval, CompletionCertificate,
)


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'ملف المستخدم'


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'get_role', 'is_active', 'is_staff')
    list_editable = ('is_active', 'is_staff')

    @admin.display(description='الدور')
    def get_role(self, obj):
        if hasattr(obj, 'profile'):
            return obj.profile.get_role_display()
        return '-'


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(Prosecution)
class ProsecutionAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')
    list_editable = ('is_active',)


@admin.register(Prosecutor)
class ProsecutorAdmin(admin.ModelAdmin):
    list_display = ('name', 'prosecution', 'phone', 'email', 'is_active')
    list_filter = ('prosecution', 'is_active')
    search_fields = ('name', 'email')
    list_editable = ('is_active',)


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'name_en', 'hourly_rate', 'page_rate')
    list_editable = ('hourly_rate', 'page_rate')
    search_fields = ('name_ar', 'name_en')


class WorkOrderLanguageInline(admin.TabularInline):
    model = WorkOrderLanguage
    extra = 1
    fields = ('language', 'custom_language_name', 'num_translators', 'estimated_hours', 'estimated_pages')


class ServiceRecordInline(admin.TabularInline):
    model = ServiceRecord
    extra = 0
    fields = ('language', 'num_translators', 'actual_hours', 'actual_pages', 'unit_rate', 'amount', 'notes')


class WorkOrderApprovalInline(admin.StackedInline):
    model = WorkOrderApproval
    extra = 0
    max_num = 1


class CompletionCertificateInline(admin.StackedInline):
    model = CompletionCertificate
    extra = 0
    max_num = 1
    readonly_fields = ('certificate_number', 'generated_at')


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'prosecution', 'prosecutor_display_admin', 'service_type', 'status', 'execution_date', 'created_at')
    list_filter = ('status', 'service_type', 'prosecution')
    search_fields = ('order_number', 'custom_prosecutor_name')
    list_editable = ('status',)
    inlines = [WorkOrderLanguageInline, ServiceRecordInline, WorkOrderApprovalInline, CompletionCertificateInline]
    readonly_fields = ('order_number', 'created_at', 'updated_at', 'submitted_at')
    fieldsets = (
        ('معلومات الطلب', {
            'fields': ('order_number', 'request_date', 'prosecution', 'prosecutor', 'custom_prosecutor_name', 'service_type', 'status')
        }),
        ('تفاصيل التنفيذ', {
            'fields': ('execution_date', 'execution_time', 'estimated_duration', 'location_type', 'location_detail')
        }),
        ('التواصل', {
            'fields': ('contact_name', 'contact_phone', 'notes')
        }),
        ('معلومات النظام', {
            'fields': ('created_by', 'submitted_at', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='المحقق')
    def prosecutor_display_admin(self, obj):
        return obj.prosecutor_display


@admin.register(WorkOrderApproval)
class WorkOrderApprovalAdmin(admin.ModelAdmin):
    list_display = ('work_order', 'smartworld_approved_by', 'smartworld_approved_at', 'pp_approved_by', 'pp_approved_at')
    search_fields = ('work_order__order_number',)


@admin.register(CompletionCertificate)
class CompletionCertificateAdmin(admin.ModelAdmin):
    list_display = ('certificate_number', 'work_order', 'subtotal', 'vat_amount', 'grand_total', 'status')
    list_filter = ('status',)
    list_editable = ('status',)
    readonly_fields = ('certificate_number', 'generated_at')
    search_fields = ('certificate_number', 'invoice_number', 'work_order__order_number')


@admin.register(ServiceRecord)
class ServiceRecordAdmin(admin.ModelAdmin):
    list_display = ('work_order', 'language', 'num_translators', 'actual_hours', 'actual_pages', 'unit_rate', 'amount')
    list_filter = ('language',)
    search_fields = ('work_order__order_number',)


admin.site.site_header = 'بوابة خدمات الترجمة - النيابة العامة الاتحادية'
admin.site.site_title = 'إدارة بوابة الترجمة'
admin.site.index_title = 'لوحة الإدارة'
