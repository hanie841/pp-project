"""Seed demo users and prosecutors for local development.

Creates the accounts the local workflow test and manual QA expect:
    pp_staff / pp123          -> PP_STAFF
    admin / admin123          -> SMARTWORLD_ADMIN (superuser)
    contract_mgr / cm123      -> CONTRACT_MANAGER

Idempotent: safe to re-run. Refuses to run when DEBUG is off, since the
passwords are well known.
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from core.models import Prosecution, Prosecutor, UserProfile

DEMO_USERS = [
    # username, password, first, last, role, is_superuser, link_prosecution
    ('pp_staff', 'pp123', 'PP', 'Staff',
     UserProfile.Role.PP_STAFF, False, True),
    ('admin', 'admin123', 'SmartWorld', 'Admin',
     UserProfile.Role.SMARTWORLD_ADMIN, True, False),
    ('contract_mgr', 'cm123', 'Contract', 'Manager',
     UserProfile.Role.CONTRACT_MANAGER, False, False),
]


class Command(BaseCommand):
    help = 'Create demo users and prosecutors for local development.'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'seed_demo uses well-known passwords and only runs with DEBUG=True.'
            )

        prosecution = Prosecution.objects.order_by('pk').first()
        if prosecution is None:
            raise CommandError(
                'No Prosecution rows found. Run "manage.py loaddata initial_data" first.'
            )

        # Every prosecution needs at least one prosecutor to raise a work order.
        for pros in Prosecution.objects.all():
            if not pros.prosecutors.exists():
                Prosecutor.objects.create(
                    prosecution=pros,
                    name=f'محقق {pros.name}',
                    email=f'prosecutor.{pros.code.lower()}@example.com',
                )
                # ASCII only: Windows consoles default to cp1252, which
                # can't encode the Arabic prosecution name.
                self.stdout.write(f'  + prosecutor for {pros.code}')

        for username, password, first, last, role, is_super, link in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': f'{username}@example.com',
                    'first_name': first,
                    'last_name': last,
                },
            )
            user.is_staff = is_super
            user.is_superuser = is_super
            user.set_password(password)
            user.save()

            profile, _ = UserProfile.objects.get_or_create(
                user=user, defaults={'role': role}
            )
            profile.role = role
            profile.prosecution = prosecution if link else None
            profile.save()

            self.stdout.write(
                f'  {"+" if created else "~"} {username} / {password}  ({role})'
            )

        self.stdout.write(self.style.SUCCESS('Demo data seeded.'))
