import os

os.environ.setdefault("MIKURAG_ENVIRONMENT", "test")
os.environ.setdefault(
    "MIKURAG_SESSION_SECRET", "test-session-secret-that-is-at-least-32-characters"
)
os.environ.setdefault(
    "MIKURAG_ENCRYPTION_MASTER_KEY", "test-encryption-key-that-is-at-least-32-characters"
)

