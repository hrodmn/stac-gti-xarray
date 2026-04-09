"""Tests for stac_gti_xarray.open using pre-recorded STAC cassettes.

These tests mock the STAC API call (rustac.search_to) with a local parquet
fixture so they run offline.  Pass ``--record-cassettes`` to refresh the
fixture from the live Earth Search API.

For tests that run end-to-end against the live API, mark them with
``@pytest.mark.integration`` and run with ``uv run pytest -m integration``.
"""

import numpy as np
import pytest

import stac_gti_xarray

EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"

# Small bbox (50 km x 50 km) in EPSG:5070 to keep data transfer minimal.
SMALL_BBOX = (-150000.0, 2500000.0, -100000.0, 2550000.0)

# Same area expressed in WGS84 (for tests that omit crs).
SMALL_BBOX_WGS84 = (-97.93, 45.47, -97.28, 45.93)


def test_open_returns_correct_structure(stac_cassette) -> None:
    """Opening a two-day window should yield a (time=2, band=3, y, x) DataArray."""
    da = stac_gti_xarray.open(
        href=EARTH_SEARCH_URL,
        collections=["sentinel-2-c1-l2a"],
        datetime="2025-06-10/2025-06-11",
        bbox=SMALL_BBOX,
        crs="epsg:5070",
        resolution=60,
        bands=["red", "green", "blue"],
        chunks={"x": 256, "y": 256},
        sort_by="eo:cloud_cover",
    )

    assert da.dims == ("time", "band", "y", "x")
    assert list(da.coords["band"].values) == ["red", "green", "blue"]
    assert da.dtype == np.uint16
    assert da.sizes["time"] >= 1


def test_open_without_crs_and_resolution(stac_cassette) -> None:
    """Omitting crs/resolution should return a valid DataArray with native projection."""
    da = stac_gti_xarray.open(
        href=EARTH_SEARCH_URL,
        collections=["sentinel-2-c1-l2a"],
        datetime="2025-06-10/2025-06-11",
        bbox=SMALL_BBOX_WGS84,
        bands=["red"],
        chunks={"x": 512, "y": 512},
    )

    assert "time" in da.dims
    assert "band" in da.dims


def test_open_band_autodiscovery(stac_cassette) -> None:
    """When bands=None, all raster assets should be auto-discovered."""
    da = stac_gti_xarray.open(
        href=EARTH_SEARCH_URL,
        collections=["sentinel-2-c1-l2a"],
        datetime="2025-06-10/2025-06-10",
        bbox=SMALL_BBOX,
        crs="epsg:5070",
        resolution=60,
        chunks={"x": 256, "y": 256},
    )

    assert da.sizes["band"] > 1


def test_open_invalid_band_raises(stac_cassette) -> None:
    """Requesting a band that does not exist should raise ValueError."""
    with pytest.raises(ValueError, match="not found"):
        stac_gti_xarray.open(
            href=EARTH_SEARCH_URL,
            collections=["sentinel-2-c1-l2a"],
            datetime="2025-06-10/2025-06-10",
            bbox=SMALL_BBOX,
            crs="epsg:5070",
            resolution=60,
            bands=["nonexistent_band"],
        )


def test_open_data_is_lazy(stac_cassette) -> None:
    """The returned DataArray should be dask-backed before compute is called."""
    import dask.array as da_module

    da = stac_gti_xarray.open(
        href=EARTH_SEARCH_URL,
        collections=["sentinel-2-c1-l2a"],
        datetime="2025-06-10/2025-06-10",
        bbox=SMALL_BBOX,
        crs="epsg:5070",
        resolution=60,
        bands=["red"],
        chunks={"x": 256, "y": 256},
    )

    assert isinstance(da.data, da_module.Array)


@pytest.mark.integration
@pytest.mark.enable_socket
def test_open_live_api() -> None:
    """Smoke test against the live Earth Search API (requires network)."""
    da = stac_gti_xarray.open(
        href=EARTH_SEARCH_URL,
        collections=["sentinel-2-c1-l2a"],
        datetime="2025-06-10/2025-06-10",
        bbox=SMALL_BBOX,
        crs="epsg:5070",
        resolution=60,
        bands=["red"],
        chunks={"x": 256, "y": 256},
    )

    assert da.dims == ("time", "band", "y", "x")
