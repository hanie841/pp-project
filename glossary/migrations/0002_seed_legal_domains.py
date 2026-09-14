"""Seed the legal-domain categories. Editable later in the Django admin."""
from django.db import migrations

DOMAINS = [
    ('القانون الجزائي', 'Criminal Law'),
    ('الإجراءات الجزائية', 'Criminal Procedure'),
    ('القانون المدني', 'Civil Law'),
    ('القانون التجاري', 'Commercial Law'),
    ('الأحوال الشخصية', 'Personal Status'),
    ('قانون العمل', 'Labour Law'),
    ('القانون الإداري', 'Administrative Law'),
    ('الجرائم الإلكترونية', 'Cybercrime'),
    ('التعاون الدولي وتسليم المجرمين', 'International Cooperation & Extradition'),
    ('عام', 'General'),
]


def seed_domains(apps, schema_editor):
    LegalDomain = apps.get_model('glossary', 'LegalDomain')
    for sort_order, (name_ar, name_en) in enumerate(DOMAINS, start=1):
        LegalDomain.objects.get_or_create(
            name_en=name_en, defaults={'name_ar': name_ar, 'sort_order': sort_order},
        )


def remove_unused_domains(apps, schema_editor):
    LegalDomain = apps.get_model('glossary', 'LegalDomain')
    LegalDomain.objects.filter(
        name_en__in=[name_en for _, name_en in DOMAINS], terms__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('glossary', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_domains, remove_unused_domains),
    ]
