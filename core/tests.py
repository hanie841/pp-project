import io
import os
import tempfile
from datetime import date, time, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    CompletionCertificate, DocumentNote, Language, OrderAssignment, OrderDocument,
    Prosecution, Prosecutor, ServiceRecord, TranslatorProfile, UserProfile,
    WorkflowConfig, WorkOrder, WorkOrderApproval, WorkOrderLanguage,
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


# ── Documents ─────────────────────────────────────────────────────────

IN_MEMORY_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    'documents': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
}


def pdf(name='file.pdf', size=100):
    return SimpleUploadedFile(name, b'%PDF-1.4' + b'0' * size, content_type='application/pdf')


class FakeClamd:
    def __init__(self, reply):
        self.reply = reply

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def sendall(self, data):
        pass

    def recv(self, size):
        reply, self.reply = self.reply, b''
        return reply


@override_settings(STORAGES=IN_MEMORY_STORAGES, DOCUMENT_VIRUS_SCAN='off')
class DocumentTestCase(PortalTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.written_order = cls.make_order(
            service_type=WorkOrder.ServiceType.WRITTEN, status=WorkOrder.Status.ASSIGNED,
        )
        cls.written_line = cls.written_order.languages.get()
        cls.assignment = OrderAssignment.objects.create(
            work_order=cls.written_order, language_line=cls.written_line,
            translator=cls.translator, status=OrderAssignment.Status.ACCEPTED,
        )

    def upload_source(self, user, files=None):
        self.client.force_login(user)
        return self.client.post(
            f'/orders/{self.written_order.pk}/documents/upload/',
            {'files': files or [pdf()]},
        )

    def upload_translation(self, user, files=None, note=''):
        self.client.force_login(user)
        return self.client.post(
            f'/orders/{self.written_order.pk}/documents/translation/{self.written_line.pk}/upload/',
            {'files': files or [pdf('translation.pdf')], 'note': note},
        )

    def set_status(self, status):
        WorkOrder.objects.filter(pk=self.written_order.pk).update(status=status)


class DocumentUploadTests(DocumentTestCase):
    def test_pp_staff_upload_notifies_with_link_only(self):
        resp = self.upload_source(self.pp_staff, files=[pdf('a.pdf'), pdf('b.pdf')])

        self.assertEqual(resp.status_code, 302)
        documents = self.written_order.documents.all()
        self.assertEqual(documents.count(), 2)
        self.assertTrue(all(d.kind == OrderDocument.Kind.SOURCE for d in documents))
        self.assertTrue(
            documents[0].file.name.startswith(f'orders/{self.written_order.pk}/source/')
        )
        message = mail.outbox[-1]
        self.assertIn(self.translator.email, message.to)
        self.assertIn(f'/orders/{self.written_order.pk}/#documents', message.body)
        self.assertEqual(message.attachments, [])

    def test_other_prosecution_cannot_upload_or_download(self):
        self.assertEqual(self.upload_source(self.other_pp_staff).status_code, 403)
        self.upload_source(self.pp_staff)
        document = self.written_order.documents.get()

        self.client.force_login(self.other_pp_staff)
        resp = self.client.get(
            f'/orders/{self.written_order.pk}/documents/{document.pk}/download/'
        )
        self.assertEqual(resp.status_code, 403)

    def test_rejects_disallowed_type_and_oversize(self):
        with override_settings(DOCUMENT_MAX_UPLOAD_SIZE=50):
            self.upload_source(self.pp_staff, files=[SimpleUploadedFile('run.exe', b'MZ')])
            self.upload_source(self.pp_staff, files=[pdf(size=100)])
        self.assertFalse(self.written_order.documents.exists())

    def test_order_quota_is_enforced(self):
        with override_settings(DOCUMENT_MAX_ORDER_TOTAL_SIZE=150):
            self.upload_source(self.pp_staff, files=[pdf(size=100)])
            self.upload_source(self.pp_staff, files=[pdf(size=100)])
        self.assertEqual(self.written_order.documents.count(), 1)

    def test_order_creation_saves_source_files(self):
        self.client.force_login(self.pp_staff)
        resp = self.client.post('/orders/new/', {
            'prosecution': self.prosecution.pk,
            'prosecutor': '',
            'custom_prosecutor_name': 'محقق',
            'service_type': 'WRITTEN',
            'execution_date': '2026-09-20',
            'execution_time': '10:00',
            'estimated_duration': '',
            'location_type': 'ONSITE',
            'languages-TOTAL_FORMS': '1',
            'languages-INITIAL_FORMS': '0',
            'languages-MIN_NUM_FORMS': '1',
            'languages-MAX_NUM_FORMS': '1000',
            'languages-0-language': self.english.pk,
            'languages-0-num_translators': '1',
            'languages-0-estimated_pages': '5',
            'source_files': [pdf('case.pdf'), pdf('annex.pdf')],
        })

        self.assertEqual(resp.status_code, 302)
        order = WorkOrder.objects.filter(created_by=self.pp_staff).order_by('-pk').first()
        self.assertEqual(
            sorted(order.documents.values_list('original_name', flat=True)),
            ['annex.pdf', 'case.pdf'],
        )


class DocumentDownloadTests(DocumentTestCase):
    def setUp(self):
        self.upload_source(self.pp_staff, files=[pdf('مذكرة.pdf')])
        self.document = self.written_order.documents.get()
        self.url = f'/orders/{self.written_order.pk}/documents/{self.document.pk}/'

    def test_unassigned_translator_is_denied_even_in_auto_mode(self):
        WorkflowConfig.objects.update_or_create(pk=1, defaults={'mode': WorkflowConfig.Mode.AUTO})
        other = self.make_user('translator2', UserProfile.Role.TRANSLATOR)
        TranslatorProfile.objects.create(user=other).languages.add(self.english)

        self.client.force_login(other)
        # AUTO mode lets them read the order, but not its files.
        self.assertEqual(self.client.get(f'/orders/{self.written_order.pk}/').status_code, 200)
        self.assertEqual(self.client.get(self.url + 'download/').status_code, 403)

    def test_assigned_translator_downloads_with_arabic_filename(self):
        self.client.force_login(self.translator)
        resp = self.client.get(self.url + 'download/')

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp['Content-Disposition'].startswith('attachment'))
        self.assertIn("filename*=utf-8''", resp['Content-Disposition'])
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')

    def test_preview_is_inline_for_pdf_only(self):
        self.client.force_login(self.pp_staff)
        self.assertTrue(
            self.client.get(self.url + 'preview/')['Content-Disposition'].startswith('inline')
        )

        self.upload_source(self.pp_staff, files=[SimpleUploadedFile('memo.docx', b'PK')])
        docx = self.written_order.documents.get(original_name='memo.docx')
        resp = self.client.get(f'/orders/{self.written_order.pk}/documents/{docx.pk}/preview/')
        self.assertEqual(resp.status_code, 404)

    def test_local_storage_uses_x_accel_redirect_outside_media_root(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            storages = {**IN_MEMORY_STORAGES, 'documents': {
                'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': root},
            }}
            with override_settings(STORAGES=storages):
                self.upload_source(self.pp_staff, files=[pdf('local.pdf')])
                document = self.written_order.documents.get(original_name='local.pdf')
                self.assertTrue(os.path.isfile(os.path.join(root, document.file.name)))
                url = f'/orders/{self.written_order.pk}/documents/{document.pk}/download/'
                resp = self.client.get(url)
                # Development (DEBUG) serves the file through Django instead.
                with override_settings(DEBUG=True):
                    dev_resp = self.client.get(url)
                    body = b''.join(dev_resp.streaming_content)
                    dev_resp.close()
        self.assertEqual(resp['X-Accel-Redirect'], f'/internal-documents/{document.file.name}')
        self.assertTrue(body.startswith(b'%PDF-1.4'))

    def test_s3_storage_redirects_to_presigned_url(self):
        signed = 'https://bucket.s3.amazonaws.com/documents/x.pdf?X-Amz-Signature=abc'
        with mock.patch('core.views._uses_presigned_urls', return_value=True), \
                mock.patch('django.core.files.storage.InMemoryStorage.url', return_value=signed) as url:
            self.client.force_login(self.pp_staff)
            resp = self.client.get(self.url + 'download/')

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], signed)
        self.assertEqual(url.call_args.kwargs['expire'], 60)


class DocumentDeleteTests(DocumentTestCase):
    def test_only_uploader_can_delete_and_not_once_completed(self):
        self.upload_source(self.pp_staff)
        document = self.written_order.documents.get()
        url = f'/orders/{self.written_order.pk}/documents/{document.pk}/delete/'

        self.client.force_login(self.sw_staff)
        self.assertEqual(self.client.post(url).status_code, 403)

        self.set_status(WorkOrder.Status.COMPLETED)
        self.client.force_login(self.pp_staff)
        self.assertEqual(self.client.post(url).status_code, 403)

        self.set_status(WorkOrder.Status.ASSIGNED)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(OrderDocument.objects.filter(pk=document.pk).exists())


class TranslatorDeliveryTests(DocumentTestCase):
    def test_written_order_requires_translation_file(self):
        url = f'/translator/assignment/{self.assignment.pk}/log/'
        self.client.force_login(self.translator)

        resp = self.client.post(url, {'actual_pages': '3'})
        self.assertEqual(resp.status_code, 200)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, OrderAssignment.Status.ACCEPTED)

        resp = self.client.post(url, {'actual_pages': '3', 'translation_files': [pdf('done.pdf')]})
        self.assertEqual(resp.status_code, 302)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, OrderAssignment.Status.COMPLETED)
        document = self.written_line.documents.get()
        self.assertEqual(document.kind, OrderDocument.Kind.TRANSLATION)
        self.assertEqual(document.assignment_id, self.assignment.pk)

    def test_interpretation_logs_without_file(self):
        order = self.make_order(status=WorkOrder.Status.ASSIGNED)
        assignment = OrderAssignment.objects.create(
            work_order=order, language_line=order.languages.get(),
            translator=self.translator, status=OrderAssignment.Status.ACCEPTED,
        )
        self.client.force_login(self.translator)
        resp = self.client.post(f'/translator/assignment/{assignment.pk}/log/', {'actual_hours': '2'})
        self.assertEqual(resp.status_code, 302)

    def test_smartworld_can_upload_translation(self):
        self.assertEqual(self.upload_translation(self.sw_staff).status_code, 302)
        self.assertTrue(
            self.written_line.documents.filter(kind=OrderDocument.Kind.TRANSLATION).exists()
        )

    def test_unassigned_translator_cannot_upload_translation(self):
        other = self.make_user('translator3', UserProfile.Role.TRANSLATOR)
        self.assertEqual(self.upload_translation(other).status_code, 403)


class RevisionNoteTests(DocumentTestCase):
    def setUp(self):
        self.upload_translation(self.translator)
        self.translation = self.written_line.documents.get()
        self.set_status(WorkOrder.Status.PENDING_APPROVAL)
        WorkOrderApproval.objects.create(work_order=self.written_order)
        self.note_url = (
            f'/orders/{self.written_order.pk}/documents/translation/{self.written_line.pk}/notes/add/'
        )
        self.approve_url = f'/orders/{self.written_order.pk}/approve/'

    def test_revision_cycle_blocks_approval_until_addressed(self):
        self.client.force_login(self.pp_staff)
        resp = self.client.post(self.note_url, {
            'body': 'الصفحة الثالثة ناقصة', 'document': self.translation.pk,
        })
        self.assertEqual(resp.status_code, 302)
        note = DocumentNote.objects.get()
        self.assertEqual(note.status, DocumentNote.Status.OPEN)
        self.assertIn(self.translator.email, mail.outbox[-1].to)

        self.client.post(self.approve_url, {'action': 'approve'})
        self.written_order.refresh_from_db()
        self.assertEqual(self.written_order.status, WorkOrder.Status.PENDING_APPROVAL)

        self.client.force_login(self.translator)
        self.assertContains(self.client.get('/translator/'), 'ملاحظات بحاجة لمعالجة')

        self.upload_translation(self.translator, files=[pdf('fixed.pdf')], note='أضيفت الصفحة')
        note.refresh_from_db()
        self.assertEqual(note.status, DocumentNote.Status.ADDRESSED)
        self.assertEqual(note.addressed_by_document.original_name, 'fixed.pdf')

        self.client.force_login(self.pp_staff)
        self.client.post(self.approve_url, {'action': 'approve'})
        self.written_order.refresh_from_db()
        self.assertEqual(self.written_order.status, WorkOrder.Status.COMPLETED)

    def test_translator_cannot_add_notes(self):
        self.client.force_login(self.translator)
        self.assertEqual(self.client.post(self.note_url, {'body': 'x'}).status_code, 403)

    def test_document_pages_render(self):
        DocumentNote.objects.create(
            work_order=self.written_order, language_line=self.written_line,
            document=self.translation, author=self.pp_staff, body='ملاحظة',
        )
        detail_url = f'/orders/{self.written_order.pk}/'

        self.client.force_login(self.pp_staff)
        for url in (detail_url, self.approve_url, '/orders/new/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        # Approving must not be blocked by the browser's check on the dispute reason.
        self.assertContains(self.client.get(self.approve_url), 'value="approve" formnovalidate')
        detail = self.client.get(detail_url)
        self.assertContains(detail, 'translation.pdf')
        self.assertContains(detail, 'بحاجة لمراجعة')

        self.client.force_login(self.translator)
        self.assertContains(self.client.get('/'), 'ملاحظات بحاجة لمعالجة')
        self.assertEqual(
            self.client.get(f'/translator/assignment/{self.assignment.pk}/log/').status_code, 200
        )


@override_settings(DOCUMENT_VIRUS_SCAN='required')
class VirusScanTests(DocumentTestCase):
    def test_infected_file_is_rejected(self):
        reply = FakeClamd(b'stream: Eicar-Test-Signature FOUND\0')
        with mock.patch('core.antivirus._connect', return_value=reply), \
                self.assertLogs('core.antivirus', 'WARNING'):
            self.upload_source(self.pp_staff)
        self.assertFalse(self.written_order.documents.exists())

    def test_unreachable_scanner_fails_closed(self):
        with mock.patch('core.antivirus._connect', side_effect=ConnectionRefusedError), \
                self.assertLogs('core.forms', 'ERROR'):
            self.upload_source(self.pp_staff)
        self.assertFalse(self.written_order.documents.exists())

    def test_clean_file_is_saved_as_scanned(self):
        with mock.patch('core.antivirus._connect', return_value=FakeClamd(b'stream: OK\0')):
            self.upload_source(self.pp_staff)
        self.assertEqual(
            self.written_order.documents.get().scan_status, OrderDocument.ScanStatus.CLEAN
        )


class PurgeDocumentsTests(DocumentTestCase):
    def setUp(self):
        self.upload_source(self.pp_staff)
        self.document = self.written_order.documents.get()
        self.set_status(WorkOrder.Status.COMPLETED)
        WorkOrderApproval.objects.create(
            work_order=self.written_order, pp_approved_at=timezone.now() - timedelta(days=400),
        )

    def purge(self, *args):
        call_command('purge_documents', *args, stdout=io.StringIO())
        self.document.refresh_from_db()

    @override_settings(DOCUMENT_RETENTION_DAYS=None)
    def test_refuses_when_retention_is_unset(self):
        with self.assertRaises(CommandError):
            self.purge('--apply')

    @override_settings(DOCUMENT_RETENTION_DAYS=365)
    def test_dry_run_then_apply(self):
        storage, name = self.document.file.storage, self.document.file.name

        self.purge()
        self.assertIsNone(self.document.purged_at)
        self.assertTrue(storage.exists(name))

        self.purge('--apply')
        self.assertIsNotNone(self.document.purged_at)
        self.assertFalse(storage.exists(name))
        resp = self.client.get(f'/orders/{self.written_order.pk}/documents/{self.document.pk}/download/')
        self.assertEqual(resp.status_code, 404)

    @override_settings(DOCUMENT_RETENTION_DAYS=500)
    def test_recent_orders_are_kept(self):
        self.purge('--apply')
        self.assertIsNone(self.document.purged_at)
