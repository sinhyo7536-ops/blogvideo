class UserError(Exception):
    """사용자에게 그대로 보여 줄 오류."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
