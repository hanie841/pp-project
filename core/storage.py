"""Private storage for work-order documents (``STORAGES['documents']``)."""
from django.core.files.storage import storages
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.functional import LazyObject, empty


class DocumentStorage(LazyObject):
    """Resolves the configured documents backend on first use.

    A FileField keeps whatever storage object it is given at import time; giving
    it this proxy lets ``override_settings(STORAGES=...)`` swap the backend.
    """

    def _setup(self):
        self._wrapped = storages['documents']


document_storage = DocumentStorage()


def get_document_storage():
    # Passed to FileField as a callable so the backend stays out of migrations.
    return document_storage


@receiver(setting_changed)
def _reset_document_storage(*, setting, **kwargs):
    if setting == 'STORAGES':
        document_storage._wrapped = empty
