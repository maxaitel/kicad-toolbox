import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--live", action="store_true", help="Run local KiCad runtime integration tests"
    )


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("live") and not config.getoption("--live"):
            item.add_marker(
                pytest.mark.skip(reason="Requires explicit --live and installed runtime")
            )
