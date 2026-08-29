import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import UploadPartReceipt, UploadSession, UploadSessionStatus
from app.uploads.storage import (
    file_size,
    move_file,
    orphan_temporary_keys,
    remove_file,
    truncate_file,
)

logger = logging.getLogger(__name__)


async def _fail_inconsistent_session(
    session: AsyncSession,
    upload: UploadSession,
    settings: Settings,
) -> None:
    await remove_file(settings.upload_dir, upload.temporary_storage_key)
    await remove_file(settings.upload_dir, upload.final_storage_key)
    await session.execute(
        delete(UploadPartReceipt).where(
            UploadPartReceipt.upload_session_id == upload.id
        )
    )
    upload.status = UploadSessionStatus.FAILED
    upload.safe_error = "Upload checkpoint storage was inconsistent. Start this upload again."
    upload.expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.upload_session_ttl_seconds
    )


async def reconcile_upload_sessions(
    session: AsyncSession,
    settings: Settings,
    *,
    remove_orphans: bool = True,
) -> int:
    now = datetime.now(UTC)
    uploads = list(
        await session.scalars(
            select(UploadSession).with_for_update(skip_locked=True)
        )
    )
    removed = 0
    known_open_keys = set(
        await session.scalars(
            select(UploadSession.temporary_storage_key).where(
                UploadSession.status == UploadSessionStatus.OPEN
            )
        )
    )

    for upload in uploads:
        if upload.expires_at <= now:
            if upload.status != UploadSessionStatus.COMPLETED:
                await remove_file(settings.upload_dir, upload.temporary_storage_key)
                await remove_file(settings.upload_dir, upload.final_storage_key)
            else:
                await remove_file(settings.upload_dir, upload.temporary_storage_key)
            await session.delete(upload)
            removed += 1
            continue

        if upload.status == UploadSessionStatus.OPEN:
            temporary_size = await file_size(
                settings.upload_dir, upload.temporary_storage_key
            )
            final_size = await file_size(settings.upload_dir, upload.final_storage_key)
            if temporary_size is None and final_size is not None:
                await move_file(
                    settings.upload_dir,
                    upload.final_storage_key,
                    upload.temporary_storage_key,
                )
                temporary_size = final_size
            elif temporary_size is not None and final_size is not None:
                await remove_file(settings.upload_dir, upload.final_storage_key)

            if temporary_size is None:
                if upload.received_bytes:
                    await _fail_inconsistent_session(session, upload, settings)
                continue
            if temporary_size > upload.received_bytes:
                await truncate_file(
                    settings.upload_dir,
                    upload.temporary_storage_key,
                    upload.received_bytes,
                )
            elif temporary_size < upload.received_bytes:
                await _fail_inconsistent_session(session, upload, settings)
        else:
            await remove_file(settings.upload_dir, upload.temporary_storage_key)
            if upload.status == UploadSessionStatus.FAILED:
                await remove_file(settings.upload_dir, upload.final_storage_key)

    if remove_orphans:
        for storage_key in await orphan_temporary_keys(
            settings.upload_dir, known_open_keys
        ):
            await remove_file(settings.upload_dir, storage_key)
            removed += 1

    await session.commit()
    if removed:
        logger.info("Reconciled %s expired or orphaned uploads", removed)
    return removed
