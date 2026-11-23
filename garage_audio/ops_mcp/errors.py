"""Structured, message-safe failures. Messages never include credentials or raw upstream text."""


class OpsError(Exception):
    """A refusal or failure with a stable machine code and a plain-words message."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def payload(self):
        value = {'code': self.code, 'message': self.message}
        if self.details:
            value['details'] = self.details
        return value


class ConfigError(OpsError):
    def __init__(self, message, **details):
        super().__init__('config_invalid', message, **details)


class SafetyError(OpsError):
    """A requested value violates a hard or operator speaker-protection limit."""

    def __init__(self, message, **details):
        super().__init__('safety_limit', message, **details)


class DependencyMissing(OpsError):
    def __init__(self, message):
        super().__init__('dependency_missing', message)
