from django.contrib import admin

from .forms import GlossaryTermForm
from .models import (
    GlossaryTerm, GlossaryTermHistory, LegalDomain, OrderTerm, record_history, snapshot,
)


@admin.register(LegalDomain)
class LegalDomainAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'name_en', 'sort_order', 'is_active')
    list_editable = ('sort_order', 'is_active')
    search_fields = ('name_ar', 'name_en')


class GlossaryTermAdminForm(GlossaryTermForm):
    class Meta(GlossaryTermForm.Meta):
        fields = GlossaryTermForm.Meta.fields + ['status', 'review_note', 'is_archived']


class GlossaryTermHistoryInline(admin.TabularInline):
    model = GlossaryTermHistory
    extra = 0
    can_delete = False
    fields = ('action', 'changed_by', 'changed_at', 'changes')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(GlossaryTerm)
class GlossaryTermAdmin(admin.ModelAdmin):
    form = GlossaryTermAdminForm
    list_display = ('term_ar', 'term_en', 'domain', 'status', 'is_archived', 'updated_at')
    list_filter = ('status', 'is_archived', 'domain', 'part_of_speech')
    search_fields = ('term_ar', 'term_en', 'search_text')
    readonly_fields = ('proposed_by', 'reviewed_by', 'reviewed_at', 'created_at', 'updated_at')
    inlines = [GlossaryTermHistoryInline]

    def save_model(self, request, obj, form, change):
        # Admin edits go into the same audit trail as edits in the portal.
        before = snapshot(GlossaryTerm.objects.get(pk=obj.pk)) if change else None
        if not change:
            obj.proposed_by = request.user
        super().save_model(request, obj, form, change)
        action = GlossaryTermHistory.Action.UPDATED if change else GlossaryTermHistory.Action.CREATED
        record_history(obj, action, request.user, before)


@admin.register(OrderTerm)
class OrderTermAdmin(admin.ModelAdmin):
    list_display = ('work_order', 'term', 'added_by', 'added_at')
    search_fields = ('work_order__order_number', 'term__term_ar', 'term__term_en')
    raw_id_fields = ('work_order', 'term')
    readonly_fields = ('added_by', 'added_at')
