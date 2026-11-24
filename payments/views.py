import json
import logging

from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import PaymentChannelConfig
from .services import ingest_raw
from .tasks import process_raw_event_task

log = logging.getLogger(__name__)
ACCEPTED = {"ResultCode": 0, "ResultDesc": "Accepted"}


def _config_or_404(token):
    config = PaymentChannelConfig.objects.select_related("org").filter(
        webhook_token=token, channel="mpesa", enabled=True).first()
    if config is None:
        raise Http404
    return config


@csrf_exempt
@require_POST
def mpesa_confirmation(request, token):
    """Safaricom calls this after a payment. Store, acknowledge, queue. No business logic here."""
    config = _config_or_404(token)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid JSON"}, status=400)
    event = ingest_raw(config.org, "mpesa", payload, source_label=config.shortcode)
    try:
        process_raw_event_task.delay(event.pk)
    except Exception:  # the event is safely stored; the replay tool will pick it up
        log.exception("could not queue raw payment event %s", event.pk)
    return JsonResponse(ACCEPTED)


@csrf_exempt
@require_POST
def mpesa_validation(request, token):
    """External validation is optional in Daraja; we accept everything and sort it out in reconciliation."""
    _config_or_404(token)
    return JsonResponse(ACCEPTED)
