class IntelligenceError(Exception):
    """Base error for the prompt intelligence domain."""


class IntelligenceValidationError(IntelligenceError):
    """Raised when an intelligence input fails validation."""


class IntelligenceNotImplementedError(IntelligenceError):
    """Raised when an intelligence capability exists as a contract but is not implemented.

    Phase 3A deliberately raises this instead of fabricating results: the API must
    never return placeholder intelligence. Concrete implementations land in later
    Phase 3 subphases and will reuse these typed errors.
    """

    def __init__(self, capability: str):
        super().__init__(
            f"{capability} is not implemented yet: the prompt intelligence boundary exists, "
            "but the concrete logic ships in a later Phase 3 subphase."
        )
        self.capability = capability


class IntelligenceResponseError(IntelligenceError):
    """Raised when an AI response cannot be normalized into an intelligence contract.

    Phase 3B: the model output is strictly parsed and validated; anything malformed,
    incomplete, or mistyped raises this error. The message is stable and sanitized —
    the raw model output never reaches the caller or the API client, and external
    failure details are surfaced only through the gateway's own typed errors.
    """

    def __init__(self, capability: str):
        super().__init__(
            f"{capability} returned an invalid response: the structured result could "
            "not be parsed."
        )
        self.capability = capability