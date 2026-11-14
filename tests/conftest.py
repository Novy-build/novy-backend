import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--verify-platform-db",
        action="store_true",
        default=False,
        help="Authorize isolated scratch writes to the configured platform_db",
    )
    parser.addoption(
        "--verify-builder-profiles-db",
        action="store_true",
        default=False,
        help="Authorize builder profile verification in an isolated platform_db collection",
    )
