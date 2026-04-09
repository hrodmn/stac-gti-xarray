"""Unit tests for stac_gti_xarray._core (no network or GDAL required)."""

from datetime import datetime

import pytest

from stac_gti_xarray._core import (
    RASTER_MEDIA_TYPES,
    _build_gti_options,
    _discover_bands,
    _normalize_datetime,
    _parse_datetime_range,
)


class TestNormalizeDatetime:
    def test_plain_dates_expanded(self) -> None:
        result = _normalize_datetime("2025-06-01/2025-06-05")
        assert result == "2025-06-01T00:00:00Z/2025-06-05T23:59:59Z"

    def test_already_full_iso_unchanged(self) -> None:
        full = "2025-06-01T00:00:00Z/2025-06-05T23:59:59Z"
        assert _normalize_datetime(full) == full

    def test_mixed_plain_and_full(self) -> None:
        result = _normalize_datetime("2025-06-01/2025-06-05T12:00:00Z")
        assert result == "2025-06-01T00:00:00Z/2025-06-05T12:00:00Z"

    def test_single_day_range(self) -> None:
        result = _normalize_datetime("2025-06-01/2025-06-01")
        assert result == "2025-06-01T00:00:00Z/2025-06-01T23:59:59Z"


class TestParseDatetimeRange:
    def test_simple_date_range(self) -> None:
        dates = _parse_datetime_range("2025-06-01/2025-06-03")
        assert dates == [
            datetime(2025, 6, 1),
            datetime(2025, 6, 2),
            datetime(2025, 6, 3),
        ]

    def test_iso_with_time_and_tz(self) -> None:
        dates = _parse_datetime_range("2025-06-01T00:00:00Z/2025-06-03T23:59:59Z")
        assert dates == [
            datetime(2025, 6, 1),
            datetime(2025, 6, 2),
            datetime(2025, 6, 3),
        ]

    def test_single_day(self) -> None:
        dates = _parse_datetime_range("2025-06-01/2025-06-01")
        assert dates == [datetime(2025, 6, 1)]

    def test_year_boundary(self) -> None:
        dates = _parse_datetime_range("2024-12-30/2025-01-02")
        assert len(dates) == 4
        assert dates[0] == datetime(2024, 12, 30)
        assert dates[-1] == datetime(2025, 1, 2)


class TestDiscoverBands:
    def _make_item(self, assets: dict) -> dict:
        return {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "test-item",
            "geometry": None,
            "properties": {"datetime": "2025-06-01T00:00:00Z"},
            "assets": assets,
            "links": [],
        }

    def test_discovers_raster_assets(self, mocker) -> None:
        item = self._make_item(
            {
                "red": {
                    "href": "s3://bucket/red.tif",
                    "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                },
                "green": {
                    "href": "s3://bucket/green.tif",
                    "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                },
                "thumbnail": {
                    "href": "s3://bucket/thumb.jpg",
                    "type": "image/jpeg",
                },
            }
        )
        mocker.patch(
            "stac_gti_xarray._core.rustac.read_sync",
            return_value={"type": "FeatureCollection", "features": [item]},
        )
        bands = _discover_bands("/fake/path.parquet")
        assert set(bands) == {"red", "green"}
        assert "thumbnail" not in bands

    def test_all_raster_media_types_recognized(self, mocker) -> None:
        assets = {
            media_type.replace("; ", "_").replace("/", "_").replace("=", "_"): {
                "href": "s3://bucket/file.tif",
                "type": media_type,
            }
            for media_type in RASTER_MEDIA_TYPES
        }
        item = self._make_item(assets)
        mocker.patch(
            "stac_gti_xarray._core.rustac.read_sync",
            return_value={"type": "FeatureCollection", "features": [item]},
        )
        bands = _discover_bands("/fake/path.parquet")
        assert len(bands) == len(RASTER_MEDIA_TYPES)

    def test_raises_when_no_raster_assets(self, mocker) -> None:
        item = self._make_item(
            {
                "thumbnail": {"href": "s3://bucket/thumb.jpg", "type": "image/jpeg"},
                "metadata": {"href": "s3://bucket/meta.xml", "type": "application/xml"},
            }
        )
        mocker.patch(
            "stac_gti_xarray._core.rustac.read_sync",
            return_value={"type": "FeatureCollection", "features": [item]},
        )
        with pytest.raises(ValueError, match="No raster assets"):
            _discover_bands("/fake/path.parquet")

    def test_raises_when_no_items(self, mocker) -> None:
        mocker.patch(
            "stac_gti_xarray._core.rustac.read_sync",
            return_value={"type": "FeatureCollection", "features": []},
        )
        with pytest.raises(ValueError, match="No STAC items"):
            _discover_bands("/fake/path.parquet")


class TestBuildGtiOptions:
    def test_minimal_options(self) -> None:
        date = datetime(2025, 6, 1)
        options = _build_gti_options(
            band="red",
            date=date,
            crs=None,
            bbox=None,
            resolution=None,
            sort_by=None,
        )
        assert options["LOCATION_FIELD"] == "assets.red.href"
        assert "2025-06-01" in options["FILTER"]
        assert "2025-06-02" in options["FILTER"]
        assert "SRS" not in options
        assert "MINX" not in options
        assert "RESX" not in options

    def test_with_all_spatial_options(self) -> None:
        date = datetime(2025, 6, 1)
        options = _build_gti_options(
            band="red",
            date=date,
            crs="EPSG:5070",
            bbox=(-150000.0, 2500000.0, 600000.0, 3000000.0),
            resolution=10,
            sort_by="eo:cloud_cover",
        )
        assert options["SRS"] == "EPSG:5070"
        assert options["SRS_BEHAVIOR"] == "REPROJECT"
        assert options["MINX"] == -150000.0
        assert options["MINY"] == 2500000.0
        assert options["MAXX"] == 600000.0
        assert options["MAXY"] == 3000000.0
        assert options["RESX"] == 10
        assert options["RESY"] == 10
        assert options["SORT_FIELD"] == "eo:cloud_cover"

    def test_filter_is_correct_day(self) -> None:
        options = _build_gti_options(
            band="red",
            date=datetime(2025, 12, 31),
            crs=None,
            bbox=None,
            resolution=None,
            sort_by=None,
        )
        assert "2025-12-31 00:00:00" in options["FILTER"]
        assert "2026-01-01 00:00:00" in options["FILTER"]

    def test_default_sort_field(self) -> None:
        options = _build_gti_options(
            band="red",
            date=datetime(2025, 6, 1),
            crs=None,
            bbox=None,
            resolution=None,
            sort_by=None,
        )
        assert options["SORT_FIELD"] == "datetime"
