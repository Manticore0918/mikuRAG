"""Recovery worker used by the checkpoint 1 provenance demo."""

def restore_checkpoint(snapshot: str) -> str:
    """Return the audit code recorded after restoring a snapshot."""
    if not snapshot:
        raise ValueError("snapshot is required")
    return "PY-2048"
