import logging
import uuid

from celery.exceptions import CeleryError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from app.ingestion.tasks import ingest_document

logger = logging.getLogger(__name__)


def enqueue_ingestion(document_id: uuid.UUID) -> bool:
    try:
        ingest_document.delay(str(document_id))
        return True
    except (CeleryError, KombuOperationalError, RedisError, OSError):
        logger.error("Could not enqueue Document ingestion for %s", document_id)
        return False
