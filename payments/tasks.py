from celery import shared_task
from django.db import OperationalError

from . import services


@shared_task(bind=True, autoretry_for=(OperationalError,), retry_backoff=True, max_retries=6)
def process_raw_event_task(self, event_id):
    services.process_raw_event(event_id)
