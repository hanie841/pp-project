import csv
import io
import json
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, override_settings

from core.models import OrderAssignment, UserProfile
from core.tests import PortalTestCase

from .forms import GlossaryTermForm
from .matching import clear_index_cache, find_terms_in_text
from .models import GlossaryTerm, GlossaryTermHistory, LegalDomain, OrderTerm
from .normalization import normalize
from .search import search_terms

APPROVED = GlossaryTerm.Status.APPROVED
PROPOSED = GlossaryTerm.Status.PROPOSED
REJECTED = GlossaryTerm.Status.REJECTED


class NormalizationTests(SimpleTestCase):
    def test_arabic_spelling_variants_normalize_alike(self):
        pairs = [
            ('الإجراءاتُ الجزائيّة', 'الاجراءات الجزائيه'),
            ('مـحـكـمـة', 'محكمه'),
            ('مستشفى', 'مستشفي'),
            ('مسؤول', 'مسوول'),
            ('آثار', 'اثار'),
        ]
        for typed, plain in pairs:
            with self.subTest(typed=typed):
                self.assertEqual(normalize(typed), normalize(plain))

    def test_english_case_and_punctuation_are_ignored(self):
        self.assertEqual(normalize('Public-Prosecution!'), normalize('  public   prosecution '))


class GlossaryTestCase(PortalTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.contract_mgr = cls.make_user('cm', UserProfile.Role.CONTRACT_MANAGER)

    def setUp(self):
        clear_index_cache()

    def term(self, term_ar, term_en, status=APPROVED, **fields):
        return GlossaryTerm.objects.create(term_ar=term_ar, term_en=term_en, status=status, **fields)


class MatchingTests(GlossaryTestCase):
    def test_whole_words_and_multi_word_terms(self):
        self.term('عقد', 'contract')
        prosecution = self.term('النيابة العامة', 'Public Prosecution')
        found = find_terms_in_text('The subcontractor wrote to the Public Prosecution.', 'en')
        self.assertEqual(found, [prosecution])

    def test_arabic_attached_prefixes_and_synonyms(self):
        contract = self.term('عقد', 'contract', synonyms_ar='اتفاقية')
        self.assertEqual(find_terms_in_text('وقّع الطرفان بالعقد', 'ar'), [contract])
        self.assertEqual(find_terms_in_text('نصّت الاتفاقية على ذلك', 'ar'), [contract])

    def test_unapproved_and_archived_terms_never_match(self):
        self.term('حبس', 'detention', status=PROPOSED)
        self.term('قبض', 'arrest', is_archived=True)
        self.assertEqual(find_terms_in_text('حبس وقبض', 'ar'), [])


class SearchTests(GlossaryTestCase):
    def test_exact_then_prefix_then_contains_with_or_without_diacritics(self):
        exact = self.term('محكمة', 'court')
        prefix = self.term('محكمة الاستئناف', 'court of appeal')
        contains = self.term('رئيس المحكمة', 'chief judge')
        for query in ('محكمة', 'مَحْكَمَة'):
            with self.subTest(query=query):
                self.assertEqual(
                    list(search_terms(GlossaryTerm.objects.all(), query)), [exact, prefix, contains]
                )

    def test_differently_spelled_duplicates_are_rejected(self):
        self.term('النيابة العامة', 'Public Prosecution')
        form = GlossaryTermForm(data={'term_ar': 'النيابه العامّة', 'term_en': 'public  prosecution'})
        self.assertFalse(form.is_valid())
        with self.assertRaises(IntegrityError), transaction.atomic():
            GlossaryTerm.objects.create(term_ar='النيابه العامه', term_en='PUBLIC PROSECUTION')


class WorkflowTests(GlossaryTestCase):
    def test_proposal_review_cycle(self):
        self.client.force_login(self.translator)
        resp = self.client.post('/glossary/new/', {
            'term_ar': 'الحبس الاحتياطي', 'term_en': 'pretrial detention',
        })
        term = GlossaryTerm.objects.get()
        self.assertRedirects(resp, f'/glossary/{term.pk}/', fetch_redirect_response=False)
        self.assertEqual(term.status, PROPOSED)
        self.assertIn(self.sw_admin.email, mail.outbox[-1].to)

        # Invisible to other non-managers, who also can't review it.
        self.client.force_login(self.pp_staff)
        self.assertEqual(self.client.get(f'/glossary/{term.pk}/').status_code, 404)
        self.assertNotContains(self.client.get('/glossary/', {'q': 'pretrial'}), 'pretrial detention')
        resp = self.client.post(f'/glossary/{term.pk}/review/', {'decision': 'approve'})
        self.assertEqual(resp.status_code, 403)

        self.client.force_login(self.contract_mgr)
        self.client.post(f'/glossary/{term.pk}/review/', {'decision': 'approve'})
        term.refresh_from_db()
        self.assertEqual((term.status, term.reviewed_by), (APPROVED, self.contract_mgr))
        self.assertEqual(
            list(term.history.values_list('action', flat=True)),
            [GlossaryTermHistory.Action.APPROVED, GlossaryTermHistory.Action.CREATED],
        )
        self.assertIn(self.translator.email, mail.outbox[-1].to)

    def test_manager_terms_are_approved_immediately(self):
        self.client.force_login(self.sw_admin)
        self.client.post('/glossary/new/', {'term_ar': 'أمر القبض', 'term_en': 'arrest warrant'})
        self.assertEqual(GlossaryTerm.objects.get().status, APPROVED)

    def test_rejection_requires_a_note(self):
        term = self.term('قاضي', 'judje', status=PROPOSED, proposed_by=self.translator)
        url = f'/glossary/{term.pk}/review/'
        self.client.force_login(self.sw_admin)

        self.client.post(url, {'decision': 'reject'})
        term.refresh_from_db()
        self.assertEqual(term.status, PROPOSED)

        self.client.post(url, {'decision': 'reject', 'note': 'خطأ إملائي في المصطلح الإنجليزي'})
        term.refresh_from_db()
        self.assertEqual((term.status, term.review_note), (REJECTED, 'خطأ إملائي في المصطلح الإنجليزي'))

    def test_proposer_edits_only_their_own_pending_proposal(self):
        term = self.term('قاضي', 'judje', status=PROPOSED, proposed_by=self.translator)
        edit_url = f'/glossary/{term.pk}/edit/'

        self.client.force_login(self.pp_staff)
        self.assertEqual(self.client.get(edit_url).status_code, 404)

        self.client.force_login(self.translator)
        self.client.post(edit_url, {'term_ar': 'قاضي', 'term_en': 'judge'})
        term.refresh_from_db()
        self.assertEqual(term.term_en, 'judge')
        self.assertEqual(term.history.get().changes, {'term_en': ['judje', 'judge']})

        GlossaryTerm.objects.filter(pk=term.pk).update(status=APPROVED)
        self.assertEqual(self.client.get(edit_url).status_code, 403)

    def test_archive_hides_term_until_restored(self):
        term = self.term('قاضي', 'judge')
        archive_url = f'/glossary/{term.pk}/archive/'

        self.client.force_login(self.sw_admin)
        self.client.post(archive_url)
        self.client.force_login(self.translator)
        self.assertNotContains(self.client.get('/glossary/'), 'judge')

        self.client.force_login(self.sw_admin)
        self.client.post(archive_url)
        self.client.force_login(self.translator)
        self.assertContains(self.client.get('/glossary/'), 'judge')

    def test_managers_see_the_proposals_badge(self):
        self.term('قاضي', 'judge', status=PROPOSED)
        self.client.force_login(self.contract_mgr)
        self.assertEqual(self.client.get('/glossary/').context['glossary_pending_count'], 1)


class ImportExportTests(GlossaryTestCase):
    def upload(self, name, content, on_duplicate='skip', import_as=APPROVED):
        self.client.force_login(self.sw_admin)
        return self.client.post('/glossary/import/', {
            'file': SimpleUploadedFile(name, content),
            'on_duplicate': on_duplicate,
            'import_as': import_as,
        })

    def confirm(self):
        return self.client.post('/glossary/import/preview/', {'action': 'confirm'})

    @staticmethod
    def csv_file(rows):
        buffer = io.StringIO()
        csv.writer(buffer).writerows(rows)
        return ('﻿' + buffer.getvalue()).encode('utf-8')

    def test_csv_preview_flags_problems_and_imports_valid_rows(self):
        self.term('النيابة العامة', 'Public Prosecution')
        resp = self.upload('terms.csv', self.csv_file([
            ['term_ar', 'term_en', 'domain', 'synonyms_en'],
            ['أمر القبض', 'arrest warrant', 'Criminal Procedure', 'warrant of arrest | arrest order'],
            ['النيابه العامه', 'public prosecution', '', ''],
            ['', 'missing arabic', '', ''],
            ['قاضي', 'judge', 'Space Law', ''],
        ]))
        self.assertRedirects(resp, '/glossary/import/preview/', fetch_redirect_response=False)

        preview = self.client.get('/glossary/import/preview/')
        self.assertContains(preview, 'مكرر — سيتم التجاهل')
        self.assertContains(preview, 'مجال غير معروف')
        self.assertContains(preview, 'المصطلح العربي مطلوب')

        self.confirm()
        warrant = GlossaryTerm.objects.get(term_en='arrest warrant')
        self.assertEqual(warrant.status, APPROVED)
        self.assertEqual(warrant.domain.name_en, 'Criminal Procedure')
        self.assertEqual(warrant.synonyms_en_list, ['warrant of arrest', 'arrest order'])
        self.assertEqual(warrant.history.get().action, GlossaryTermHistory.Action.IMPORTED)
        self.assertEqual(GlossaryTerm.objects.count(), 2)

    def test_update_option_overwrites_existing_terms(self):
        term = self.term('النيابة العامة', 'Public Prosecution')
        self.upload('terms.csv', self.csv_file([
            ['المصطلح بالعربية', 'English term', 'Definition (English)'],
            ['النيابة العامة', 'Public Prosecution', 'The authority that prosecutes crimes.'],
        ]), on_duplicate='update')
        self.confirm()

        term.refresh_from_db()
        self.assertEqual(term.definition_en, 'The authority that prosecutes crimes.')
        self.assertEqual(
            term.history.get().changes['definition_en'],
            ['', 'The authority that prosecutes crimes.'],
        )

    def test_xlsx_export_import_round_trip_keeps_arabic(self):
        domain = LegalDomain.objects.get(name_en='Criminal Procedure')
        self.term(
            'الحبس الاحتياطي', 'pretrial detention', domain=domain,
            part_of_speech=GlossaryTerm.PartOfSpeech.PHRASE, synonyms_en='remand in custody',
        )
        self.client.force_login(self.sw_admin)
        exported = self.client.get('/glossary/export.xlsx')
        self.assertEqual(exported.status_code, 200)
        GlossaryTerm.objects.all().delete()

        self.upload('glossary.xlsx', exported.content)
        self.confirm()
        term = GlossaryTerm.objects.get()
        self.assertEqual(
            (term.term_ar, term.domain, term.part_of_speech),
            ('الحبس الاحتياطي', domain, GlossaryTerm.PartOfSpeech.PHRASE),
        )
        self.assertEqual(term.synonyms_en_list, ['remand in custody'])

    def test_csv_export_for_non_managers_is_approved_only_and_formula_safe(self):
        self.term('قاضي', 'judge', notes='=1+1')
        self.term('حبس', 'detention', status=PROPOSED)
        self.client.force_login(self.translator)

        body = self.client.get('/glossary/export.csv').content.decode('utf-8')
        self.assertTrue(body.startswith('﻿'))
        self.assertIn('judge', body)
        self.assertNotIn('detention', body)
        self.assertIn("'=1+1", body)

    def test_only_managers_can_import(self):
        self.client.force_login(self.translator)
        self.assertEqual(self.client.get('/glossary/import/').status_code, 403)


class CaptionGlossaryTests(GlossaryTestCase):
    def setUp(self):
        super().setUp()
        self.term('النيابة العامة', 'Public Prosecution', forbidden_en='General Prosecution')
        self.term('محكمة', 'court')
        self.client.force_login(self.pp_staff)

    def system_prompt(self, text, source='العربية', target='English'):
        completion = mock.Mock()
        completion.choices = [mock.Mock(message=mock.Mock(content='translated'))]
        with override_settings(OPENAI_API_KEY='test-key'), mock.patch('openai.OpenAI') as client_class:
            create = client_class.return_value.chat.completions.create
            create.return_value = completion
            resp = self.client.post(
                f'/orders/{self.order.pk}/conference/captions/translate/',
                data=json.dumps({'text': text, 'source_lang': source, 'target_lang': target}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        return create.call_args.kwargs['messages'][0]['content']

    def test_prompt_lists_only_terms_in_the_caption(self):
        prompt = self.system_prompt('قررت النيابة العامة إحالة القضية')
        self.assertIn('"النيابة العامة" → "Public Prosecution"', prompt)
        self.assertIn('"General Prosecution"', prompt)
        self.assertNotIn('court', prompt)

    def test_english_to_arabic(self):
        prompt = self.system_prompt('The court adjourned the hearing', 'English', 'العربية')
        self.assertIn('"court" → "محكمة"', prompt)

    def test_disabled_or_other_language_pairs_add_nothing(self):
        with override_settings(GLOSSARY_IN_CAPTIONS=False):
            self.assertNotIn('GLOSSARY', self.system_prompt('النيابة العامة'))
        self.assertNotIn('GLOSSARY', self.system_prompt('النيابة العامة', 'العربية', 'اردو'))


class LookupAndOrderTermTests(GlossaryTestCase):
    def setUp(self):
        super().setUp()
        self.warrant = self.term('أمر القبض', 'arrest warrant')
        self.add_url = f'/glossary/order/{self.order.pk}/terms/add/'

    def test_lookup_returns_approved_terms_only(self):
        self.term('أمر الحبس', 'detention order', status=PROPOSED)
        self.client.force_login(self.translator)
        resp = self.client.get('/glossary/lookup/', {'q': 'أمر'})
        self.assertContains(resp, 'arrest warrant')
        self.assertNotContains(resp, 'detention order')

    def test_assigned_translator_and_order_staff_manage_order_terms(self):
        OrderAssignment.objects.create(
            work_order=self.order, language_line=self.order.languages.get(),
            translator=self.translator, status=OrderAssignment.Status.ACCEPTED,
        )
        self.client.force_login(self.translator)
        self.assertEqual(self.client.post(self.add_url, {'term': self.warrant.pk}).status_code, 302)
        link = OrderTerm.objects.get(work_order=self.order, term=self.warrant)

        self.client.force_login(self.pp_staff)
        detail = self.client.get(f'/orders/{self.order.pk}/')
        self.assertContains(detail, 'مصطلحات هذا الأمر')
        self.assertContains(detail, 'arrest warrant')

        resp = self.client.post(f'/glossary/order/{self.order.pk}/terms/{link.pk}/remove/')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OrderTerm.objects.exists())

    def test_outsiders_cannot_link_terms(self):
        for user in (self.translator, self.other_pp_staff):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                resp = self.client.post(self.add_url, {'term': self.warrant.pk})
                self.assertEqual(resp.status_code, 403)

    def test_unapproved_terms_cannot_be_linked(self):
        proposal = self.term('محضر', 'report', status=PROPOSED)
        self.client.force_login(self.pp_staff)
        self.assertEqual(self.client.post(self.add_url, {'term': proposal.pk}).status_code, 404)
