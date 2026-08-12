"""Error classes consumed by the workflow supervisor."""


class AgentError(Exception):
    code = "AGENT_ERROR"
    retryable = False


class TransientInfrastructureError(AgentError):
    code = "TRANSIENT_INFRASTRUCTURE"
    retryable = True


class ModelRateLimitedError(TransientInfrastructureError):
    code = "MODEL_RATE_LIMITED"


class ModelTimeoutError(TransientInfrastructureError):
    code = "MODEL_TIMEOUT"


class ModelSchemaError(AgentError):
    code = "MODEL_SCHEMA_INVALID"
    retryable = True


class ContentBlockedError(AgentError):
    code = "MODEL_CONTENT_BLOCKED"


class FatalBusinessError(AgentError):
    code = "FATAL_BUSINESS_ERROR"
