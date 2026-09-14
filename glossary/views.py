from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import WorkOrder
from core.views import _can_view_order, _get_profile, _safe_redirect_url, _visible_orders

from . import importers
from .forms import DUPLICATE_MESSAGE, GlossaryTermForm, ImportForm, ReviewForm
from .models import (
    GlossaryTerm, GlossaryTermHistory, LegalDomain, OrderTerm, record_history, snapshot,
)
from .normalization import contains_arabic
from .notifications import notify_term_proposed, notify_term_reviewed
from .permissions import can_edit, can_link_terms, can_view, is_manager
from .search import search_terms

PAGE_SIZE = 25
LOOKUP_LIMIT = 10
PREVIEW_ROWS_SHOWN = 500
IMPORT_SESSION_KEY = 'glossary_import'
TABS = [
    ('approved', 'المعتمدة'),
    ('proposed', 'بانتظار المراجعة'),
    ('rejected', 'المرفوضة'),
    ('archived', 'المؤرشفة'),
]


def _render(request, template, context=None):
    return render(request, template, {
        'profile': _get_profile(request.user),
        'is_glossary_manager': is_manager(request.user),
        **(context or {}),
    })


def _forbidden():
    return HttpResponseForbidden('غير مسموح')


def _visible_term_or_404(request, pk):
    term = get_object_or_404(GlossaryTerm.objects.visible_to(request.user), pk=pk)
    return term


def _flash_form_errors(request, form):
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, error)


# ── Browse ───────────────────────────────────────────────────────────

@login_required
def term_list(request):
    if not can_view(request.user):
        return _forbidden()
    manager = is_manager(request.user)

    terms = GlossaryTerm.objects.select_related('domain')
    tab_querysets = {
        'approved': terms.usable(),
        'proposed': terms.filter(status=GlossaryTerm.Status.PROPOSED, is_archived=False),
        'rejected': terms.filter(status=GlossaryTerm.Status.REJECTED, is_archived=False),
        'archived': terms.filter(is_archived=True),
    }
    tab = request.GET.get('tab', 'approved')
    if not manager or tab not in tab_querysets:
        tab = 'approved'
    terms = tab_querysets[tab]

    selected_domain = request.GET.get('domain', '')
    if selected_domain.isdigit():
        terms = terms.filter(domain_id=selected_domain)
    selected_pos = request.GET.get('pos', '')
    if selected_pos in GlossaryTerm.PartOfSpeech.values:
        terms = terms.filter(part_of_speech=selected_pos)
    query = request.GET.get('q', '').strip()

    params = request.GET.copy()
    params.pop('page', None)
    context = {
        'page': Paginator(search_terms(terms, query), PAGE_SIZE).get_page(request.GET.get('page')),
        'query': query,
        'tab': tab,
        'querystring': params.urlencode(),
        'domains': LegalDomain.objects.filter(is_active=True),
        'parts_of_speech': GlossaryTerm.PartOfSpeech.choices,
        'selected_domain': selected_domain,
        'selected_pos': selected_pos,
    }
    if manager:
        context['tabs'] = [(key, label, tab_querysets[key].count()) for key, label in TABS]
    else:
        context['my_proposals'] = GlossaryTerm.objects.filter(
            proposed_by=request.user, is_archived=False,
        ).exclude(status=GlossaryTerm.Status.APPROVED)
    return _render(request, 'glossary/list.html', context)


def _history_rows(term):
    labels = {
        field.name: field.verbose_name
        for field in GlossaryTerm._meta.get_fields() if hasattr(field, 'verbose_name')
    }
    domain_names = dict(LegalDomain.objects.values_list('pk', 'name_ar'))
    rows = []
    for entry in term.history.select_related('changed_by'):
        changes = []
        for field, (old, new) in entry.changes.items():
            if field == 'domain':
                old, new = domain_names.get(old, old or ''), domain_names.get(new, new or '')
            changes.append((labels.get(field, field), old, new))
        rows.append((entry, changes))
    return rows


@login_required
def term_detail(request, pk):
    if not can_view(request.user):
        return _forbidden()
    term = _visible_term_or_404(request, pk)
    manager = is_manager(request.user)
    orders = _visible_orders(request.user, _get_profile(request.user)).filter(
        glossary_terms__term=term
    ).distinct()
    return _render(request, 'glossary/detail.html', {
        'term': term,
        'can_edit': can_edit(request.user, term),
        'orders': orders,
        'history_rows': _history_rows(term) if manager else None,
    })


# ── Propose / edit / review ──────────────────────────────────────────

def _initial_from_query(request):
    query = request.GET.get('q', '').strip()
    if not query:
        return {}
    return {'term_ar': query} if contains_arabic(query) else {'term_en': query}


@login_required
def term_create(request):
    if not can_view(request.user):
        return _forbidden()
    manager = is_manager(request.user)
    form = GlossaryTermForm(request.POST or None, initial=_initial_from_query(request))
    if request.method == 'POST' and form.is_valid():
        term = form.save(commit=False)
        term.proposed_by = request.user
        if manager:
            term.status = GlossaryTerm.Status.APPROVED
            term.reviewed_by, term.reviewed_at = request.user, timezone.now()
        else:
            term.status = GlossaryTerm.Status.PROPOSED
        try:
            with transaction.atomic():
                term.save()
                record_history(term, GlossaryTermHistory.Action.CREATED, request.user)
        except IntegrityError:
            form.add_error(None, DUPLICATE_MESSAGE)
        else:
            if manager:
                messages.success(request, 'تمت إضافة المصطلح إلى المسرد')
            else:
                notify_term_proposed(term)
                messages.success(request, 'تم إرسال المصطلح إلى مديري المسرد للمراجعة')
            return redirect('glossary:term_detail', pk=term.pk)
    return _render(request, 'glossary/form.html', {'form': form, 'is_new': True})


@login_required
def term_edit(request, pk):
    term = _visible_term_or_404(request, pk)
    if not can_edit(request.user, term):
        return _forbidden()
    before = snapshot(term)
    form = GlossaryTermForm(request.POST or None, instance=term)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                term = form.save()
                record_history(term, GlossaryTermHistory.Action.UPDATED, request.user, before)
        except IntegrityError:
            form.add_error(None, DUPLICATE_MESSAGE)
        else:
            messages.success(request, 'تم حفظ التعديلات')
            return redirect('glossary:term_detail', pk=term.pk)
    return _render(request, 'glossary/form.html', {'form': form, 'term': term, 'is_new': False})


@login_required
@require_POST
def term_review(request, pk):
    if not is_manager(request.user):
        return _forbidden()
    term = get_object_or_404(GlossaryTerm, pk=pk)
    form = ReviewForm(request.POST)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return redirect('glossary:term_detail', pk=term.pk)

    approve = form.cleaned_data['decision'] == 'approve'
    before = snapshot(term)
    term.status = GlossaryTerm.Status.APPROVED if approve else GlossaryTerm.Status.REJECTED
    term.review_note = form.cleaned_data['note']
    term.reviewed_by, term.reviewed_at = request.user, timezone.now()
    with transaction.atomic():
        term.save()
        record_history(
            term,
            GlossaryTermHistory.Action.APPROVED if approve else GlossaryTermHistory.Action.REJECTED,
            request.user, before,
        )
    notify_term_reviewed(term)
    messages.success(request, 'تم اعتماد المصطلح' if approve else 'تم رفض المصطلح')
    return redirect('glossary:term_detail', pk=term.pk)


@login_required
@require_POST
def term_archive(request, pk):
    """Archive or restore — terms are never hard-deleted from the portal."""
    if not is_manager(request.user):
        return _forbidden()
    term = get_object_or_404(GlossaryTerm, pk=pk)
    before = snapshot(term)
    term.is_archived = not term.is_archived
    with transaction.atomic():
        term.save()
        record_history(
            term,
            GlossaryTermHistory.Action.ARCHIVED if term.is_archived else GlossaryTermHistory.Action.RESTORED,
            request.user, before,
        )
    messages.success(request, 'تمت أرشفة المصطلح' if term.is_archived else 'تمت استعادة المصطلح')
    return redirect('glossary:term_detail', pk=term.pk)


# ── Import / export ──────────────────────────────────────────────────

@login_required
def import_upload(request):
    if not is_manager(request.user):
        return _forbidden()
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            rows = importers.read_rows(form.cleaned_data['file'])
        except importers.ImportFileError as exc:
            form.add_error('file', str(exc))
        else:
            request.session[IMPORT_SESSION_KEY] = {
                'filename': form.cleaned_data['file'].name,
                'on_duplicate': form.cleaned_data['on_duplicate'],
                'import_as': form.cleaned_data['import_as'],
                'entries': importers.build_preview(rows),
            }
            return redirect('glossary:import_preview')
    return _render(request, 'glossary/import.html', {'form': form, 'columns': importers.COLUMNS})


@login_required
def import_preview(request):
    if not is_manager(request.user):
        return _forbidden()
    pending = request.session.get(IMPORT_SESSION_KEY)
    if not pending:
        messages.info(request, 'لا توجد عملية استيراد جارية')
        return redirect('glossary:import')
    entries = pending['entries']

    if request.method == 'POST':
        request.session.pop(IMPORT_SESSION_KEY, None)
        if request.POST.get('action') != 'confirm':
            messages.info(request, 'تم إلغاء الاستيراد')
            return redirect('glossary:import')
        try:
            created, updated, skipped = importers.apply_import(
                entries, request.user,
                on_duplicate=pending['on_duplicate'], status=pending['import_as'],
            )
        except IntegrityError:
            messages.error(request, 'تغيرت بيانات المسرد أثناء الاستيراد. يرجى رفع الملف مرة أخرى.')
            return redirect('glossary:import')
        messages.success(
            request, f'اكتمل الاستيراد: {created} جديد، {updated} محدَّث، {skipped} متجاوَز'
        )
        return redirect('glossary:list')

    valid = [entry for entry in entries if not entry['errors']]
    summary = {
        'total': len(entries),
        'errors': len(entries) - len(valid),
        'duplicates': sum(1 for entry in valid if entry['duplicate_of']),
        'new': sum(1 for entry in valid if not entry['duplicate_of']),
    }
    return _render(request, 'glossary/import_preview.html', {
        'pending': pending,
        'summary': summary,
        'entries': entries[:PREVIEW_ROWS_SHOWN],
        'hidden_count': max(0, len(entries) - PREVIEW_ROWS_SHOWN),
    })


@login_required
def import_template(request, fmt):
    if not is_manager(request.user):
        return _forbidden()
    return importers.export_response([], fmt, 'glossary_template')


@login_required
def export_terms(request, fmt):
    if not can_view(request.user):
        return _forbidden()
    terms = GlossaryTerm.objects.select_related('domain')
    terms = terms.filter(is_archived=False) if is_manager(request.user) else terms.usable()
    return importers.export_response(terms, fmt, 'legal_glossary')


# ── Lookup panel & order terms ───────────────────────────────────────

@login_required
def lookup(request):
    """HTMX partial: approved terms matching the query, for the lookup panel."""
    if not can_view(request.user):
        return _forbidden()
    query = request.GET.get('q', '').strip()

    order = None
    order_pk = request.GET.get('order', '')
    if order_pk.isdigit():
        order = WorkOrder.objects.filter(pk=order_pk).first()
        if order and not _can_view_order(request.user, _get_profile(request.user), order):
            order = None

    terms = []
    if query:
        terms = search_terms(GlossaryTerm.objects.usable().select_related('domain'), query)[:LOOKUP_LIMIT]
    return render(request, 'glossary/_lookup_results.html', {
        'terms': terms,
        'query': query,
        'order': order,
        'can_link': bool(order) and can_link_terms(request.user, order),
        'linked_ids': set(order.glossary_terms.values_list('term_id', flat=True)) if order else set(),
        'return_url': request.htmx.current_url if request.htmx else '',
    })


def _order_terms_url(order):
    return f"{reverse('order_detail', args=[order.pk])}#order-terms"


@login_required
@require_POST
def order_term_add(request, order_pk):
    order = get_object_or_404(WorkOrder, pk=order_pk)
    if not can_link_terms(request.user, order):
        return _forbidden()
    term_pk = request.POST.get('term', '')
    if not term_pk.isdigit():
        raise Http404
    term = get_object_or_404(GlossaryTerm.objects.usable(), pk=term_pk)
    _, created = OrderTerm.objects.get_or_create(
        work_order=order, term=term,
        defaults={'added_by': request.user, 'note': request.POST.get('note', '')[:500]},
    )
    if created:
        messages.success(request, f'تمت إضافة "{term.term_ar}" إلى مصطلحات الأمر')
    return redirect(_safe_redirect_url(request, request.POST.get('next', ''), _order_terms_url(order)))


@login_required
@require_POST
def order_term_remove(request, order_pk, link_pk):
    order = get_object_or_404(WorkOrder, pk=order_pk)
    if not can_link_terms(request.user, order):
        return _forbidden()
    get_object_or_404(OrderTerm, pk=link_pk, work_order=order).delete()
    messages.success(request, 'تمت إزالة المصطلح من الأمر')
    return redirect(_safe_redirect_url(request, request.POST.get('next', ''), _order_terms_url(order)))
