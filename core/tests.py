import io
from datetime import date, time
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import (
    CompletionCertificate, Language, OrderAssignment, Prosecution, Prosecutor,
    ServiceRecord, UserProfile, WorkOrder, WorkOrderApproval, WorkOrderLanguage,
)


class PortalTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prosecution = Prosecution.objects.create(name='نيابة أبوظبي', code='AUH')
        cls.other_prosecution = Prosecution.objects.create(name='نيابة دبي', code='DXB')
        cls.english = Language.objects.create(
            name_ar='الإنجليزية', name_en='English',
            hourly_rate=Decimal('300'), page_rate=Decimal('90'),
        )
        cls.pp_staff = cls.make_user('pp', UserProfile.Role.PP_STAFF, cls.prosecution)
        cls.other_pp_staff = cls.make_user(
            'pp_other', UserProfile.Role.PP_STAFF, cls.other_prosecution
        )
        cls.sw_admin = cls.make_user('sw_admin', UserProfile.Role.SMARTWORLD_ADMIN)
        cls.sw_staff = cls.make_user('sw_staff', UserProfile.Role.SMARTWORLD_STAFF)
        cls.translator = cls.make_user('translator', UserProfile.Role.TRANSLATOR)
        cls.order = cls.make_order()

    @classmethod
    def make_user(cls, username, role, prosecution=None):
        user = User.objects.create_user(username, f'{username}@example.com', 'pw')
        UserProfile.objects.create(user=user, role=role, prosecution=prosecution)
        return user

    @classmethod
    def make_order(cls, **overrides):
        fields = dict(
            prosecution=cls.prosecution,
            custom_prosecutor_name='محقق',
            service_type=WorkOrder.ServiceType.INTERPRETATION,
            execution_date=date(2026, 9, 20),
            execution_time=time(10),
            location_type=WorkOrder.LocationType.ONSITE,
            created_by=cls.pp_staff,
            status=WorkOrder.Status.SUBMITTED,
        )
        fields.update(overrides)
        order = WorkOrder.objects.create(**fields)
        WorkOrderLanguage.objects.create(
            work_order=order, language=cls.english, estimated_hours=Decimal('2')
        )
        return order


class OrderReadAccessTests(PortalTestCase):
    def test_pp_staff_of_other_prosecution_is_denied(self):
        self.client.force_login(self.other_pp_staff)
        pk = self.order.pk
        for url in (f'/orders/{pk}/', f'/orders/{pk}/pdf/',
                    f'/orders/{pk}/certificate/', f'/orders/{pk}/certificate/pdf/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_pp_staff_of_same_prosecution_can_view(self):
        self.client.force_login(self.pp_staff)
        self.assertEqual(self.client.get(f'/orders/{self.order.pk}/').status_code, 200)

    def test_order_list_hides_other_prosecutions(self):
        self.client.force_login(self.other_pp_staff)
        self.assertNotContains(self.client.get('/orders/'), self.order.order_number)

    def test_translator_sees_only_assigned_orders(self):
        self.client.force_login(self.translator)
        url = f'/orders/{self.order.pk}/'
        self.assertEqual(self.client.get(url).status_code, 403)

        OrderAssignment.objects.create(
            work_order=self.order, language_line=self.order.languages.get(),
            translator=self.translator,
        )
        self.assertEqual(self.client.get(url).status_code, 200)


class ApprovalScopeTests(PortalTestCase):
    def test_pp_staff_cannot_approve_other_prosecutions_orders(self):
        self.order.status = WorkOrder.Status.PENDING_APPROVAL
        self.order.save()
        WorkOrderApproval.objects.create(work_order=self.order)

        self.client.force_login(self.other_pp_staff)
        resp = self.client.post(f'/orders/{self.order.pk}/approve/', {'action': 'approve'})

        self.assertEqual(resp.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrder.Status.PENDING_APPROVAL)


class LoginRedirectTests(PortalTestCase):
    def login(self, next_url):
        return self.client.post(
            f'/login/?next={next_url}', {'username': 'pp', 'password': 'pw'}
        )

    def test_external_next_url_is_ignored(self):
        self.assertRedirects(
            self.login('https://evil.example/'), '/', fetch_redirect_response=False
        )

    def test_local_next_url_is_followed(self):
        self.assertRedirects(
            self.login('/orders/'), '/orders/', fetch_redirect_response=False
        )


class RequestSafetyTests(PortalTestCase):
    def test_accept_rejects_get(self):
        self.client.force_login(self.sw_admin)
        resp = self.client.get(f'/orders/{self.order.pk}/accept/')
        self.assertEqual(resp.status_code, 405)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrder.Status.SUBMITTED)

    def test_prosecutor_names_are_escaped(self):
        Prosecutor.objects.create(prosecution=self.prosecution, name='<script>x</script>')
        self.client.force_login(self.pp_staff)
        resp = self.client.get(f'/orders/prosecutors/?prosecution={self.prosecution.pk}')
        self.assertNotContains(resp, '<script>')
        self.assertContains(resp, '&lt;script&gt;')


class CertificateTests(PortalTestCase):
    def make_certificate(self, order):
        return CompletionCertificate.objects.create(work_order=order, subtotal=Decimal('1000'))

    def test_same_day_invoice_numbers_are_unique(self):
        first = self.make_certificate(self.order)
        second = self.make_certificate(self.make_order())
        self.assertNotEqual(first.invoice_number, second.invoice_number)

    def test_only_smartworld_admin_can_finalize(self):
        certificate = self.make_certificate(self.order)
        url = f'/orders/{self.order.pk}/certificate/finalize/'

        self.client.force_login(self.sw_staff)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(CompletionCertificate.total_invoiced(), Decimal('0'))

        self.client.force_login(self.sw_admin)
        self.assertEqual(self.client.post(url).status_code, 302)
        certificate.refresh_from_db()
        self.assertEqual(certificate.status, CompletionCertificate.Status.FINALIZED)
        self.assertEqual(CompletionCertificate.total_invoiced(), Decimal('1050'))


class NumberGenerationRetryTests(PortalTestCase):
    def test_order_number_collision_is_retried(self):
        taken = self.order.order_number
        generate = WorkOrder._generate_order_number
        calls = []

        def collide_once(instance):
            calls.append(instance)
            return taken if len(calls) == 1 else generate(instance)

        with mock.patch.object(WorkOrder, '_generate_order_number', collide_once):
            order = self.make_order()

        self.assertEqual(len(calls), 2)
        self.assertNotEqual(order.order_number, taken)


class LogServiceTests(PortalTestCase):
    def setUp(self):
        self.order.status = WorkOrder.Status.ACCEPTED
        self.order.save()
        self.line = self.order.languages.get()
        self.url = f'/orders/{self.order.pk}/log/'
        self.client.force_login(self.sw_staff)

    def test_invalid_numbers_rerender_instead_of_crashing(self):
        resp = self.client.post(self.url, {
            f'lang_{self.line.pk}_hours': 'abc',
            f'lang_{self.line.pk}_translators': 'two',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.order.service_records.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrder.Status.ACCEPTED)

    def test_relog_keeps_translator_record_linked(self):
        record = ServiceRecord(
            work_order=self.order, language=self.english, actual_hours=Decimal('2')
        )
        record.calculate_amount()
        record.save()
        assignment = OrderAssignment.objects.create(
            work_order=self.order, language_line=self.line, translator=self.translator,
            status=OrderAssignment.Status.COMPLETED, service_record=record,
        )

        resp = self.client.post(self.url, {
            f'lang_{self.line.pk}_hours': '3',
            f'lang_{self.line.pk}_translators': '1',
        })

        self.assertEqual(resp.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.service_record_id, record.pk)
        record.refresh_from_db()
        self.assertEqual(record.amount, Decimal('900'))
        self.assertEqual(self.order.service_records.count(), 1)


class SeedDemoTests(TestCase):
    @override_settings(DEBUG=True)
    def test_runs_on_a_cp1252_console(self):
        Prosecution.objects.create(name='نيابة الشارقة', code='SHJ')
        stdout = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
        call_command('seed_demo', stdout=stdout)
        self.assertTrue(User.objects.filter(username='pp_staff').exists())
