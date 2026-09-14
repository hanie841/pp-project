"""Delete stored files of old completed work orders (document retention policy).

Dry run unless --apply is given. Database rows are kept (with purged_at set) so
the audit trail of who uploaded what survives.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import OrderDocument, WorkOrder


class Command(BaseCommand):
    help = (
        'Delete document files of COMPLETED work orders approved more than '
        'DOCUMENT_RETENTION_DAYS ago. Dry run unless --apply is given.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually delete the files (default: only list them).',
        )

    def handle(self, *args, apply=False, **options):
        days = settings.DOCUMENT_RETENTION_DAYS
        if not days:
            raise CommandError('DOCUMENT_RETENTION_DAYS is not set; retention is disabled.')

        cutoff = timezone.now() - timedelta(days=days)
        documents = OrderDocument.objects.filter(
            work_order__status=WorkOrder.Status.COMPLETED,
            work_order__approval__pp_approved_at__lt=cutoff,
            purged_at__isnull=True,
        ).select_related('work_order')

        count = total_size = 0
        for document in documents.iterator():
            count += 1
            total_size += document.size
            # ASCII only (order number + stored name): Windows consoles use cp1252.
            self.stdout.write(
                f'  {"-" if apply else "?"} {document.work_order.order_number}  {document.file.name}'
            )
            if apply:
                document.file.storage.delete(document.file.name)
                document.purged_at = timezone.now()
                document.save(update_fields=['purged_at'])

        verb = 'Purged' if apply else 'Would purge (dry run)'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {count} file(s), {total_size} bytes.'))
