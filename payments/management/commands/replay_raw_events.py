from django.core.management.base import BaseCommand

from payments import services
from payments.models import RawPaymentEvent


class Command(BaseCommand):
    help = "Reprocess raw payment events that were stored but not processed (safe: duplicates are ignored)."

    def handle(self, *args, **opts):
        pending = RawPaymentEvent.objects.filter(processed_at__isnull=True).order_by("id")
        done = still_failing = 0
        for event in pending:
            services.process_raw_event(event.pk)
            event.refresh_from_db()
            if event.processed_at:
                done += 1
            else:
                still_failing += 1
        self.stdout.write(f"replayed {done}, still failing {still_failing} (see processing_error in admin)")
