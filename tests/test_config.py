import pytest
from pydantic import ValidationError

from app.core.config import Settings

DB = "postgresql+psycopg://u:p@localhost/db"
SECRET = "x" * 32


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {"database_url": DB, "jwt_secret": SECRET}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_valid_settings() -> None:
    s = make()
    assert s.environment in {"development", "test", "production"}


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret=SECRET)  # type: ignore[call-arg, arg-type]


def test_wrong_driver_fails() -> None:
    with pytest.raises(ValidationError):
        make(database_url="postgresql://u:p@localhost/db")


def test_invalid_environment_fails() -> None:
    with pytest.raises(ValidationError):
        make(environment="staging")


def test_jwt_secret_is_required_and_long_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=DB)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        make(jwt_secret="short")


def test_placeholder_jwt_secret_is_rejected_in_production() -> None:
    placeholder = "change-me-change-me-change-me"
    assert make(jwt_secret=placeholder).environment  # fine outside production
    with pytest.raises(ValidationError):
        make(jwt_secret=placeholder, environment="production")


def test_access_token_ttl_defaults_to_an_hour_and_must_be_positive() -> None:
    assert make().access_token_ttl_minutes == 60
    assert make(access_token_ttl_minutes=5).access_token_ttl_minutes == 5
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            make(access_token_ttl_minutes=bad)
