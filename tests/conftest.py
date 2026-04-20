"""Shared pytest configuration and fixtures."""

import shutil
from pathlib import Path

import pytest
import rustac

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Single cassette covering all sentinel-2 tests (June 10-11 2025, CONUS area).
DEFAULT_CASSETTE = FIXTURE_DIR / "sentinel-2-c1-l2a_2025-06-10_2025-06-11.parquet"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add --record-cassettes CLI option."""
    parser.addoption(
        "--record-cassettes",
        action="store_true",
        default=False,
        help="Re-record STAC cassettes from the live API (requires network access).",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: requires network access and a working GDAL installation",
    )


@pytest.fixture
def stac_cassette(request, mocker):  # noqa: PT004
    """Mock rustac.search_to using a pre-recorded parquet fixture.

    In normal test runs, copies a fixture parquet to the requested output path
    so no STAC API calls are made. Pass ``--record-cassettes`` to re-record the
    fixture from the live API (requires network access).

    Usage::

        def test_something(stac_cassette):
            da = stac_gti_xarray.open(...)
    """
    # The GTI driver reads COG headers from S3 even in playback mode, so
    # re-enable TCP sockets (the STAC API is blocked via the mock, not the
    # socket layer).
    request.node.add_marker(pytest.mark.enable_socket)

    record: bool = request.config.getoption("--record-cassettes")

    if record:
        original_search_to = rustac.search_to

        async def _recording_search_to(output_path: str, *args, **kwargs):
            await original_search_to(output_path, *args, **kwargs)
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy(output_path, DEFAULT_CASSETTE)

        mocker.patch(
            "stac_gti_xarray._core.rustac.search_to",
            side_effect=_recording_search_to,
        )
    else:
        if not DEFAULT_CASSETTE.exists():
            pytest.skip(
                f"Cassette not recorded yet: {DEFAULT_CASSETTE}. "
                "Run `uv run pytest --record-cassettes` once to record it."
            )

        async def _playback_search_to(output_path: str, *args, **kwargs):
            shutil.copy(DEFAULT_CASSETTE, output_path)

        mocker.patch(
            "stac_gti_xarray._core.rustac.search_to",
            side_effect=_playback_search_to,
        )
