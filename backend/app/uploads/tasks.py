import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.celery_app import celery_app
from app.config import get_settings
from app.uploads.cleanup import reconcile_upload_sessions


async def run_upload_cleanup() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            return await reconcile_upload_sessions(session, settings)
    finally:
        await engine.dispose()


@celery_app.task(name="mikurag.uploads.cleanup")
def cleanup_upload_sessions() -> int:
    return asyncio.run(run_upload_cleanup())
