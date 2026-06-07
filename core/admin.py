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
    list_display = ('username', 'email', 'first_name', 'last_name', 'get_role')

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


@admin.register(Prosecutor)
class ProsecutorAdmin(admin.ModelAdmin):
    list_display = ('name', 'prosecution', 'phone', 'is_active')
    list_filter = ('prosecution', 'is_active')
    search_fields = ('name',)


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'name_en', 'hourly_rate', 'page_rate')


class WorkOrderLanguageInline(admin.TabularInline):
    model = WorkOrderLanguage
    extra = 1


class ServiceRecordInline(admin.TabularInline):
    model = ServiceRecord
    extra = 0


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'prosecution', 'service_type', 'status', 'execution_date')
    list_filter = ('status', 'service_type', 'prosecution')
    search_fields = ('order_number',)
    inlines = [WorkOrderLanguageInline, ServiceRecordInline]
    readonly_fields = ('order_number', 'created_at', 'updated_at', 'submitted_at')


@admin.register(CompletionCertificate)
class CompletionCertificateAdmin(admin.ModelAdmin):
    list_display = ('certificate_number', 'work_order', 'grand_total', 'status')
    list_filter = ('status',)
    readonly_fields = ('certificate_number', 'generated_at')


admin.site.site_header = 'بوابة خدمات الترجمة - النيابة العامة الاتحادية'
admin.site.site_title = 'إدارة بوابة الترجمة'
admin.site.index_title = 'لوحة الإدارة'
