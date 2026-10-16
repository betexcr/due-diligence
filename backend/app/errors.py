"""Error taxonomy. The worker decides retry vs dead-letter from the class alone."""


class PipelineError(Exception):
    retryable: bool = False


class RetryableError(PipelineError):
    """Transient: rate limits, 5xx, network, timeouts. Retry with backoff."""

    retryable = True


class PermanentError(PipelineError):
    """Will fail the same way every time: corrupt file, unsupported type,
    model output that violates the contract after repair. Dead-letter immediately."""

    retryable = False


class JobTimeoutError(RetryableError):
    pass
