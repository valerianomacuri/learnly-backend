import pytest
from pydantic import SecretStr, ValidationError

from app.schemas.user import UserCreate


def test_user_rejects_bad_email() -> None:
    with pytest.raises(ValidationError):
        UserCreate(email="not-an-email", name="Ann", password=SecretStr("password-123"))


def test_user_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        UserCreate(
            email="a@b.com",
            name="Ann",
            password=SecretStr("password-123"),
            admin=True,  # type: ignore[call-arg]
        )


def test_user_roles_cannot_be_set_through_the_body() -> None:
    with pytest.raises(ValidationError):
        UserCreate(
            email="a@b.com",
            name="Ann",
            password=SecretStr("password-123"),
            roles=["supervisor"],  # type: ignore[call-arg]
        )
