class NotFoundError(Exception):
    def __init__(self, entity: str) -> None:
        super().__init__(f"{entity} not found")


class ConflictError(Exception):
    pass


class InvalidInputError(Exception):
    """A well-formed request carries a value the service cannot use (e.g. a cursor)."""


class ForbiddenError(Exception):
    """The caller is authenticated but not allowed to perform the action."""
