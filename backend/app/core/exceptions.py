class ApplicationError(Exception):
    """A safe application error, independent of FastAPI."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AuthenticationRequired(ApplicationError):
    def __init__(self, message: str = "A valid current user is required.") -> None:
        super().__init__("AUTH_TOKEN_INVALID", message, 401)


class AccessDenied(ApplicationError):
    def __init__(self) -> None:
        super().__init__("ACCESS_DENIED", "This action is not permitted.", 403)


class NotFound(ApplicationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 404)


class Conflict(ApplicationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 409)


class InvalidInput(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__("VALIDATION_ERROR", message, 422)
