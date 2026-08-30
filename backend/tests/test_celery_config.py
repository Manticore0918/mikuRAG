from app.celery_app import celery_app, settings


def test_redis_visibility_timeout_matches_worker_recovery_setting() -> None:
    assert celery_app.conf.broker_transport_options == {
        "visibility_timeout": settings.celery_visibility_timeout_seconds,
    }
    assert (
        celery_app.conf.visibility_timeout
        == settings.celery_visibility_timeout_seconds
    )
