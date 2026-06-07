"""
Full workflow integration test for PP Translation Services Portal.

Tests the complete lifecycle:
1. PP Staff logs in and creates a work order with multiple languages
2. SmartWorld accepts the order
3. SmartWorld logs actual service (different from estimated)
4. PP Staff approves the actuals
5. Completion certificate is auto-generated with correct VAT
6. PDF export works for both work order and certificate
7. Dashboard shows correct contract balance
8. Dispute flow: PP rejects, SmartWorld re-logs, PP approves
"""

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pp_portal.settings')
django.setup()

from django.test import Client, TestCase
from django.contrib.auth.models import User
from core.models import (
    WorkOrder, WorkOrderLanguage, ServiceRecord,
    WorkOrderApproval, CompletionCertificate,
    Prosecution, Prosecutor, Language, UserProfile,
)
from decimal import Decimal

PASS = '\033[92mPASS\033[0m'
FAIL = '\033[91mFAIL\033[0m'
errors = []

def check(description, condition, detail=''):
    if condition:
        print(f'  {PASS}  {description}')
    else:
        msg = f'  {FAIL}  {description}'
        if detail:
            msg += f' -- {detail}'
        print(msg)
        errors.append(description)


def run_tests():
    print('=' * 60)
    print('PP TRANSLATION SERVICES PORTAL - FULL WORKFLOW TEST')
    print('=' * 60)

    # Setup
    pp_client = Client()
    sw_client = Client()
    cm_client = Client()

    pp_user = User.objects.get(username='pp_staff')
    sw_user = User.objects.get(username='admin')
    cm_user = User.objects.get(username='contract_mgr')

    prosecution = Prosecution.objects.first()
    prosecutor = Prosecutor.objects.filter(prosecution=prosecution).first()
    english = Language.objects.get(name_en='English')
    urdu = Language.objects.get(name_en='Urdu')

    # =============================================
    print('\n--- STEP 1: Authentication ---')
    # =============================================

    # PP Staff login
    resp = pp_client.post('/login/', {'username': 'pp_staff', 'password': 'pp123'})
    check('PP Staff login redirects', resp.status_code == 302)

    # SmartWorld login
    resp = sw_client.post('/login/', {'username': 'admin', 'password': 'admin123'})
    check('SmartWorld Admin login redirects', resp.status_code == 302)

    # Contract Manager login
    resp = cm_client.post('/login/', {'username': 'contract_mgr', 'password': 'cm123'})
    check('Contract Manager login redirects', resp.status_code == 302)

    # Dashboard access
    resp = pp_client.get('/')
    check('PP Staff dashboard loads', resp.status_code == 200)

    resp = sw_client.get('/')
    check('SmartWorld dashboard loads', resp.status_code == 200)

    resp = cm_client.get('/')
    check('Contract Manager dashboard loads', resp.status_code == 200)

    # =============================================
    print('\n--- STEP 2: Create Work Order (PP Staff) ---')
    # =============================================

    # Load create page
    resp = pp_client.get('/orders/new/')
    check('Work order create page loads', resp.status_code == 200)

    # SmartWorld should NOT access create page
    resp = sw_client.get('/orders/new/')
    check('SmartWorld cannot access create page', resp.status_code == 403)

    # Submit work order with English + Urdu interpretation
    post_data = {
        'prosecution': prosecution.pk,
        'prosecutor': prosecutor.pk,
        'service_type': 'INTERPRETATION',
        'execution_date': '2026-06-15',
        'execution_time': '10:00',
        'estimated_duration': '2',
        'location_type': 'NEED_LINK',
        'location_detail': '',
        'contact_name': 'أحمد خالد',
        'contact_phone': '0501234567',
        'notes': 'جلسة تحقيق مع متهمين',
        # Formset management
        'languages-TOTAL_FORMS': '2',
        'languages-INITIAL_FORMS': '0',
        'languages-MIN_NUM_FORMS': '1',
        'languages-MAX_NUM_FORMS': '1000',
        # Language 1: English
        'languages-0-language': english.pk,
        'languages-0-num_translators': '1',
        'languages-0-estimated_hours': '2',
        'languages-0-estimated_pages': '',
        # Language 2: Urdu
        'languages-1-language': urdu.pk,
        'languages-1-num_translators': '2',
        'languages-1-estimated_hours': '2',
        'languages-1-estimated_pages': '',
    }
    resp = pp_client.post('/orders/new/', post_data)
    check('Work order submission redirects', resp.status_code == 302, f'got {resp.status_code}')

    order = WorkOrder.objects.order_by('-created_at').first()
    check('Work order created in DB', order is not None)
    check('Order number generated', order.order_number.startswith('2026/AO/'))
    check('Status is SUBMITTED', order.status == 'SUBMITTED')
    check('Submitted_at is set', order.submitted_at is not None)
    check('Created by PP Staff', order.created_by == pp_user)

    lang_lines = order.languages.all()
    check('Two language lines created', lang_lines.count() == 2)

    eng_line = lang_lines.get(language=english)
    urdu_line = lang_lines.get(language=urdu)
    check('English: 1 translator, 2 hours', eng_line.num_translators == 1 and eng_line.estimated_hours == Decimal('2'))
    check('Urdu: 2 translators, 2 hours', urdu_line.num_translators == 2 and urdu_line.estimated_hours == Decimal('2'))

    # Estimated cost: English = 2 * 300 * 1 = 600, Urdu = 2 * 315 * 2 = 1260, Total = 1860
    check('English estimated amount = 600', eng_line.estimated_amount == Decimal('600'))
    check('Urdu estimated amount = 1260', urdu_line.estimated_amount == Decimal('1260'))
    check('Total estimated = 1860', order.estimated_total == Decimal('1860'))

    order_pk = order.pk

    # =============================================
    print('\n--- STEP 3: View Order Detail ---')
    # =============================================

    resp = pp_client.get(f'/orders/{order_pk}/')
    check('PP Staff can view order detail', resp.status_code == 200)

    resp = sw_client.get(f'/orders/{order_pk}/')
    check('SmartWorld can view order detail', resp.status_code == 200)
    check('Detail page contains order number', order.order_number.encode() in resp.content)

    # =============================================
    print('\n--- STEP 4: Order List ---')
    # =============================================

    resp = pp_client.get('/orders/')
    check('Order list loads for PP Staff', resp.status_code == 200)
    check('Order appears in list', order.order_number.encode() in resp.content)

    resp = pp_client.get('/orders/?status=SUBMITTED')
    check('Status filter works', resp.status_code == 200)

    # =============================================
    print('\n--- STEP 5: SmartWorld Accepts Order ---')
    # =============================================

    # PP should not be able to accept
    resp = pp_client.post(f'/orders/{order_pk}/accept/')
    check('PP Staff cannot accept order', resp.status_code == 403)

    resp = sw_client.post(f'/orders/{order_pk}/accept/')
    check('SmartWorld accept redirects', resp.status_code == 302)

    order.refresh_from_db()
    check('Status changed to ACCEPTED', order.status == 'ACCEPTED')

    # =============================================
    print('\n--- STEP 6: SmartWorld Provides Meeting Link ---')
    # =============================================

    resp = sw_client.post(f'/orders/{order_pk}/provide-link/', {
        'meeting_link': 'https://teams.microsoft.com/meeting/123'
    })
    check('Meeting link submission redirects', resp.status_code == 302)

    order.refresh_from_db()
    check('Location detail updated', 'teams.microsoft.com' in order.location_detail)
    check('Location type changed to ONLINE', order.location_type == 'ONLINE')

    # =============================================
    print('\n--- STEP 7: SmartWorld Logs Actual Service ---')
    # =============================================

    # Load log service page
    resp = sw_client.get(f'/orders/{order_pk}/log/')
    check('Log service page loads', resp.status_code == 200)

    # PP should not access log service
    resp = pp_client.get(f'/orders/{order_pk}/log/')
    check('PP Staff cannot access log service', resp.status_code == 403)

    # Log actuals: English used 4 hours (more than estimated 2), Urdu used 1.5 hours (less)
    post_data = {
        f'lang_{eng_line.pk}_hours': '4',
        f'lang_{eng_line.pk}_translators': '1',
        f'lang_{eng_line.pk}_notes': 'الجلسة استمرت أطول من المتوقع',
        f'lang_{urdu_line.pk}_hours': '1.5',
        f'lang_{urdu_line.pk}_translators': '2',
        f'lang_{urdu_line.pk}_notes': '',
    }
    resp = sw_client.post(f'/orders/{order_pk}/log/', post_data)
    check('Log service submission redirects', resp.status_code == 302)

    order.refresh_from_db()
    check('Status changed to PENDING_APPROVAL', order.status == 'PENDING_APPROVAL')

    records = order.service_records.all()
    check('Two service records created', records.count() == 2)

    eng_record = records.get(language=english)
    urdu_record = records.get(language=urdu)
    # English: 4 hours * 300 * 1 = 1200
    check('English actual: 4 hrs, rate=300, amount=1200',
          eng_record.actual_hours == Decimal('4') and
          eng_record.unit_rate == Decimal('300') and
          eng_record.amount == Decimal('1200'))
    # Urdu: 1.5 hours * 315 * 2 = 945
    check('Urdu actual: 1.5 hrs, rate=315, 2 translators, amount=945',
          urdu_record.actual_hours == Decimal('1.5') and
          urdu_record.unit_rate == Decimal('315') and
          urdu_record.amount == Decimal('945'))

    # Approval record created
    approval = order.approval
    check('Approval record exists', approval is not None)
    check('SmartWorld approved by is set', approval.smartworld_approved_by == sw_user)
    check('SmartWorld approved at is set', approval.smartworld_approved_at is not None)

    # =============================================
    print('\n--- STEP 8: PP Staff Reviews & Approves ---')
    # =============================================

    resp = pp_client.get(f'/orders/{order_pk}/approve/')
    check('Approval page loads', resp.status_code == 200)
    check('Approval page shows estimated vs actual comparison',
          b'1200' in resp.content or b'1,200' in resp.content)

    resp = pp_client.post(f'/orders/{order_pk}/approve/', {'action': 'approve'})
    check('PP approval redirects', resp.status_code == 302)

    order.refresh_from_db()
    check('Status changed to COMPLETED', order.status == 'COMPLETED')

    approval.refresh_from_db()
    check('PP approved by is set', approval.pp_approved_by == pp_user)
    check('PP approved at is set', approval.pp_approved_at is not None)

    # =============================================
    print('\n--- STEP 9: Certificate Auto-Generated ---')
    # =============================================

    check('Certificate exists', hasattr(order, 'certificate'))

    cert = order.certificate
    check('Certificate number generated', cert.certificate_number.startswith('2026/SC/'))
    check('Invoice number generated', cert.invoice_number.startswith('INV_PP_SWLT_'))

    # Subtotal: 1200 + 945 = 2145
    check(f'Subtotal = 2145 (got {cert.subtotal})', cert.subtotal == Decimal('2145'))

    # VAT: 2145 * 5% = 107.25
    expected_vat = Decimal('2145') * Decimal('5') / Decimal('100')
    check(f'VAT amount = 107.25 (got {cert.vat_amount})', cert.vat_amount == expected_vat)

    # Grand total: 2145 + 107.25 = 2252.25
    expected_total = Decimal('2145') + expected_vat
    check(f'Grand total = 2252.25 (got {cert.grand_total})', cert.grand_total == expected_total)

    check('VAT rate is 5%', cert.vat_rate == Decimal('5'))

    # =============================================
    print('\n--- STEP 10: Certificate View & PDF ---')
    # =============================================

    resp = pp_client.get(f'/orders/{order_pk}/certificate/')
    check('Certificate page loads', resp.status_code == 200)
    check('Certificate shows certificate number', cert.certificate_number.encode() in resp.content)
    # Arabic locale uses comma as decimal separator (2252,25 not 2252.25)
    total_str = str(cert.grand_total)
    total_str_comma = total_str.replace('.', ',')
    check('Certificate shows grand total',
          total_str.encode() in resp.content or total_str_comma.encode() in resp.content)

    # Work order PDF
    resp = pp_client.get(f'/orders/{order_pk}/pdf/')
    check('Work order PDF/HTML export works', resp.status_code == 200)

    # Certificate PDF
    resp = pp_client.get(f'/orders/{order_pk}/certificate/pdf/')
    check('Certificate PDF/HTML export works', resp.status_code == 200)

    # =============================================
    print('\n--- STEP 11: Dashboard Contract Balance ---')
    # =============================================

    # Finalize the certificate first
    cert.status = CompletionCertificate.Status.FINALIZED
    cert.save()

    resp = cm_client.get('/')
    check('Dashboard loads for Contract Manager', resp.status_code == 200)

    total_invoiced = CompletionCertificate.total_invoiced()
    check(f'Total invoiced = {cert.grand_total} (got {total_invoiced})',
          total_invoiced == cert.grand_total)

    # =============================================
    print('\n--- STEP 12: Dispute Flow ---')
    # =============================================

    # Create a second work order for dispute testing
    post_data2 = {
        'prosecution': prosecution.pk,
        'prosecutor': prosecutor.pk,
        'service_type': 'WRITTEN',
        'execution_date': '2026-06-20',
        'execution_time': '09:00',
        'estimated_duration': '',
        'location_type': 'ONSITE',
        'location_detail': 'مكتب النيابة - الطابق الثالث',
        'contact_name': 'سعيد محمد',
        'contact_phone': '0509876543',
        'notes': 'ترجمة مستندات قضية',
        'languages-TOTAL_FORMS': '1',
        'languages-INITIAL_FORMS': '0',
        'languages-MIN_NUM_FORMS': '1',
        'languages-MAX_NUM_FORMS': '1000',
        'languages-0-language': english.pk,
        'languages-0-num_translators': '1',
        'languages-0-estimated_hours': '',
        'languages-0-estimated_pages': '10',
    }
    resp = pp_client.post('/orders/new/', post_data2)
    check('Second work order created (written translation)', resp.status_code == 302)

    order2 = WorkOrder.objects.order_by('-created_at').first()
    check('Second order service type is WRITTEN', order2.service_type == 'WRITTEN')
    order2_pk = order2.pk

    # English written: 10 pages * 90 = 900
    eng_line2 = order2.languages.first()
    check('Written translation estimated: 10 pages * 90 = 900',
          eng_line2.estimated_amount == Decimal('900'))

    # SmartWorld accepts
    sw_client.post(f'/orders/{order2_pk}/accept/')
    order2.refresh_from_db()
    check('Second order accepted', order2.status == 'ACCEPTED')

    # SmartWorld logs actuals (15 pages instead of 10)
    post_data_log = {
        f'lang_{eng_line2.pk}_pages': '15',
        f'lang_{eng_line2.pk}_translators': '1',
        f'lang_{eng_line2.pk}_notes': 'المستندات كانت أكثر من المتوقع',
    }
    resp = sw_client.post(f'/orders/{order2_pk}/log/', post_data_log)
    check('Service logged for second order', resp.status_code == 302)

    order2.refresh_from_db()
    check('Second order pending approval', order2.status == 'PENDING_APPROVAL')

    eng_record2 = order2.service_records.first()
    # 15 pages * 90 = 1350
    check(f'Written actual: 15 pages * 90 = 1350 (got {eng_record2.amount})',
          eng_record2.actual_pages == Decimal('15') and
          eng_record2.amount == Decimal('1350'))

    # PP disputes
    resp = pp_client.post(f'/orders/{order2_pk}/approve/', {
        'action': 'dispute',
        'reason': 'عدد الصفحات المقدمة أكثر بكثير من المتوقع، يرجى المراجعة'
    })
    check('Dispute submission redirects', resp.status_code == 302)

    order2.refresh_from_db()
    check('Status changed to DISPUTED', order2.status == 'DISPUTED')

    approval2 = order2.approval
    check('Dispute reason recorded',
          'عدد الصفحات' in approval2.pp_dispute_reason)

    # SmartWorld re-logs corrected actuals (12 pages instead of 15)
    resp = sw_client.get(f'/orders/{order2_pk}/log/')
    check('SmartWorld can access log page for disputed order', resp.status_code == 200)

    post_data_relog = {
        f'lang_{eng_line2.pk}_pages': '12',
        f'lang_{eng_line2.pk}_translators': '1',
        f'lang_{eng_line2.pk}_notes': 'تم المراجعة والتصحيح',
    }
    resp = sw_client.post(f'/orders/{order2_pk}/log/', post_data_relog)
    check('Re-logged actuals submitted', resp.status_code == 302)

    order2.refresh_from_db()
    check('Status back to PENDING_APPROVAL', order2.status == 'PENDING_APPROVAL')

    eng_record2_new = order2.service_records.first()
    # 12 pages * 90 = 1080
    check(f'Corrected actual: 12 pages * 90 = 1080 (got {eng_record2_new.amount})',
          eng_record2_new.actual_pages == Decimal('12') and
          eng_record2_new.amount == Decimal('1080'))

    # PP approves this time
    resp = pp_client.post(f'/orders/{order2_pk}/approve/', {'action': 'approve'})
    check('PP approves corrected actuals', resp.status_code == 302)

    order2.refresh_from_db()
    check('Second order now COMPLETED', order2.status == 'COMPLETED')

    cert2 = order2.certificate
    check('Second certificate auto-generated', cert2 is not None)
    # Subtotal = 1080, VAT = 54, Grand total = 1134
    check(f'Second cert subtotal = 1080 (got {cert2.subtotal})',
          cert2.subtotal == Decimal('1080'))
    expected_vat2 = Decimal('1080') * Decimal('5') / Decimal('100')
    check(f'Second cert VAT = 54 (got {cert2.vat_amount})',
          cert2.vat_amount == expected_vat2)
    check(f'Second cert grand total = 1134 (got {cert2.grand_total})',
          cert2.grand_total == Decimal('1134'))

    # =============================================
    print('\n--- STEP 13: HTMX Prosecutor Endpoint ---')
    # =============================================

    resp = pp_client.get(f'/orders/prosecutors/?prosecution={prosecution.pk}')
    check('Prosecutors endpoint returns 200', resp.status_code == 200)
    check('Prosecutors endpoint returns options', b'<option' in resp.content)
    check('Prosecutor name appears', prosecutor.name.encode() in resp.content)

    # =============================================
    print('\n--- STEP 14: Email Notifications (Console) ---')
    # =============================================

    # Email is configured as console backend, so we just verify the
    # signals module functions exist and don't error
    from core.signals import (
        notify_new_order, notify_order_accepted, notify_meeting_link,
        notify_actuals_logged, notify_pp_approved, notify_pp_disputed,
    )
    check('All 6 notification functions exist', True)

    # =============================================
    print('\n--- STEP 15: Access Control Verification ---')
    # =============================================

    # Unauthenticated access
    anon = Client()
    resp = anon.get('/')
    check('Unauthenticated user redirected from dashboard', resp.status_code == 302)
    check('Redirected to login', '/login/' in resp.url)

    resp = anon.get('/orders/new/')
    check('Unauthenticated user redirected from create', resp.status_code == 302)

    # SmartWorld cannot create orders
    resp = sw_client.get('/orders/new/')
    check('SmartWorld blocked from creating orders', resp.status_code == 403)

    # PP cannot log service
    resp = pp_client.get(f'/orders/{order_pk}/log/')
    check('PP Staff blocked from logging service', resp.status_code == 403)

    # PP cannot accept orders
    resp = pp_client.post(f'/orders/{order_pk}/accept/')
    check('PP Staff blocked from accepting orders', resp.status_code == 403)

    # =============================================
    print('\n--- STEP 16: Admin Site ---')
    # =============================================

    resp = sw_client.get('/admin/')
    check('Admin site loads for superuser', resp.status_code in (200, 302))

    # =============================================
    # Summary
    # =============================================
    print('\n' + '=' * 60)
    total = len(errors)
    if total == 0:
        print(f'{PASS}  ALL TESTS PASSED!')
    else:
        print(f'{FAIL}  {total} TEST(S) FAILED:')
        for e in errors:
            print(f'  - {e}')
    print('=' * 60)

    return len(errors) == 0


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
