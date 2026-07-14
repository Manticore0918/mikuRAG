class IngestionError(Exception):
    """A failure with a message that is safe to expose to an Administrator."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class UploadValidationError(IngestionError):
    pass


class ExtractionError(IngestionError):
    pass


class EmbeddingProviderError(IngestionError):
    pass
