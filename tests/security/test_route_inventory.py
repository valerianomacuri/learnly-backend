"""Every route of the app must have an entry in the permission table (spec 12)."""

from permissions_table import PERMISSIONS, Access

from app.main import app


def routes() -> set[tuple[str, str]]:
    """Every (method, path) the app serves, read from the OpenAPI document."""
    found: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            found.add((method.upper(), path))
    return found


def test_every_route_has_an_expected_permission() -> None:
    missing = sorted(routes() - set(PERMISSIONS))
    assert not missing, (
        "routes without an entry in tests/security/permissions_table.py "
        f"(decide who may call them): {missing}"
    )


def test_the_table_has_no_stale_entries() -> None:
    stale = sorted(set(PERMISSIONS) - routes())
    assert not stale, f"table entries without a route: {stale}"


def test_openapi_security_matches_the_table() -> None:
    """Only the PUBLIC routes may be callable without a bearer token."""
    schema = app.openapi()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            expected = PERMISSIONS[(method.upper(), path)]
            secured = bool(operation.get("security"))
            assert secured == (expected is not Access.PUBLIC), (method, path)
