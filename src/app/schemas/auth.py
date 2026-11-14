from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from app.schemas.base import RequestDTO


class LoginRequest(RequestDTO):
    model_config = ConfigDict(str_strip_whitespace=False)  # the password is verbatim

    email: EmailStr = Field(max_length=320)
    password: SecretStr = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds
