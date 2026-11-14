from typing import Annotated

from pydantic import ConfigDict, EmailStr, Field, SecretStr, StringConstraints

from app.schemas.base import ReadDTO, RequestDTO

Name = Annotated[str, StringConstraints(strip_whitespace=True)]

# Passwords are used verbatim: RequestDTO strips whitespace from every string,
# which must not happen to a secret, so these bodies turn it off and strip the
# plain text fields explicitly.
_VERBATIM = ConfigDict(str_strip_whitespace=False)


class UserCreate(RequestDTO):
    model_config = _VERBATIM

    email: EmailStr = Field(max_length=320)
    name: Name = Field(min_length=1, max_length=200)
    # Argon2 has no practical length limit; the cap only stops hashing abuse.
    password: SecretStr = Field(min_length=8, max_length=128)


class UserUpdate(RequestDTO):
    model_config = _VERBATIM

    email: EmailStr | None = Field(default=None, max_length=320)
    name: Name | None = Field(default=None, min_length=1, max_length=200)
    password: SecretStr | None = Field(default=None, min_length=8, max_length=128)


class UserRead(ReadDTO):
    """Never includes the password or its hash."""

    email: str
    name: str
