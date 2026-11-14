class NotFoundError(Exception):
    def __init__(self, entity: str) -> None:
        super().__init__(f"{entity} not found")


class ConflictError(Exception):
    pass


class ForbiddenError(Exception):
    """The caller is authenticated but not allowed to perform the action."""
