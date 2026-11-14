import uuid
from datetime import timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.permissions import ROLE_PERMISSIONS, Perm, RoleName
from app.core.security import (
    InvalidTokenError,
    Principal,
    create_access_token,
    decode_access_token,
)

USER = uuid.uuid4()


def _secret() -> str:
    return get_settings().jwt_secret.get_secret_value()


def test_roundtrip_returns_the_user_id() -> None:
    assert decode_access_token(create_access_token(user_id=USER)) == USER


def test_token_carries_no_role_or_tenant_claims() -> None:
    token = create_access_token(user_id=USER)
    claims = jwt.decode(token, _secret(), algorithms=["HS256"])
    assert set(claims) == {"sub", "iat", "exp"}


def test_subject_must_be_a_uuid() -> None:
    token = jwt.encode({"sub": "supervisor-1", "exp": 9**10}, _secret(), "HS256")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_expired_token_is_rejected() -> None:
    token = create_access_token(user_id=USER, expires_in=timedelta(seconds=-5))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_token_signed_with_another_secret_is_rejected() -> None:
    forged = jwt.encode(
        {"sub": str(USER), "exp": 9**10},
        "a-different-secret-a-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(forged)


def test_unsigned_token_is_rejected() -> None:
    unsigned = jwt.encode({"sub": str(USER), "exp": 9**10}, key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_access_token(unsigned)


@pytest.mark.parametrize("missing", ["sub", "exp"])
def test_required_claims(missing: str) -> None:
    claims = {"sub": str(USER), "exp": 9**10}
    del claims[missing]
    token = jwt.encode(claims, _secret(), algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_principal_for_roles_follows_the_catalog() -> None:
    supervisor = Principal.for_roles(USER, [RoleName.SUPERVISOR])
    customer = Principal.for_roles(USER, [RoleName.CUSTOMER])
    assert supervisor.has(Perm.USER_READ_ANY)
    assert not customer.has(Perm.USER_READ_ANY)
    assert customer.has(Perm.USER_READ_OWN)
    assert supervisor.sub == str(USER)


def test_principal_with_two_roles_gets_the_union() -> None:
    both = Principal.for_roles(USER, [RoleName.SUPERVISOR, RoleName.CUSTOMER])
    expected = (
        ROLE_PERMISSIONS[RoleName.SUPERVISOR] | ROLE_PERMISSIONS[RoleName.CUSTOMER]
    )
    assert both.permissions == {str(p) for p in expected}


def test_customers_never_hold_an_any_or_supervisor_permission() -> None:
    forbidden = {
        Perm.USER_CREATE,
        Perm.USER_READ_ANY,
        Perm.USER_UPDATE_ANY,
    }
    assert not forbidden & ROLE_PERMISSIONS[RoleName.CUSTOMER]
