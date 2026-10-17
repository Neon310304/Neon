class AuthenticationError(RuntimeError):
    def __init__(self, code: str, message: str = "Authentication required.") -> None:
        super().__init__(message)
        self.code = code
        self.detail = {"code": code, "message": message}


class AuthorizationError(RuntimeError):
    def __init__(self, code: str, message: str = "Forbidden.") -> None:
        super().__init__(message)
        self.code = code
        self.detail = {"code": code, "message": message}
