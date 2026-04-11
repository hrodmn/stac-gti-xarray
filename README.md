# stac-gti-xarray

A Python library for loading STAC mosaics as lazily-evaluated xarray DataArrays using GDAL's GTI (GDAL Tile Index) driver. No mosaicing logic, no third-party STAC loading library. Just GDAL doing the work.

## How it works

1. Query a STAC API (or read a local STAC geoparquet) with `rustac`, writing results to a temp geoparquet file.
2. For each band and date, open the geoparquet as a GTI tile index via `rioxarray.open_rasterio` with `driver="GTI"`. GTI handles reprojection, spatial filtering, and per-day filtering via OGR SQL.
3. Concatenate the per-band and per-date arrays into a single dask-backed `xr.DataArray` with dimensions `(time, band, y, x)`.

No pixel data is read until the array is materialized.

**Note:** GTI has a file-handle caching bug where multiple opens against the same filename will all end up using the last `LOCATION_FIELD` set. This library works around it by creating a unique symlink per band/date open.

## Installation

rasterio's PyPI wheels bundle their own `libgdal.so` but that build does not include the GTI raster driver or Parquet support. You need rasterio built against a GDAL that includes both.

The included `Dockerfile` and `docker-compose.yml` handle this by building rasterio from source against the latest GDAL Alpine image:

```bash
docker compose up
```

Access JupyterLab at `http://localhost:8888` and open `demo.ipynb`.

If installing outside Docker, set `no-binary-package = ["rasterio"]` in your `uv` config (or `--no-binary-package rasterio` on the command line) and ensure the system GDAL includes the GTI and Parquet drivers (GDAL >= 3.7).

## Usage

```python
import rasterio
import stac_gti_xarray

with rasterio.Env(...):
    da = stac_gti_xarray.open(
        href="https://earth-search.aws.element84.com/v1",
        collections=["sentinel-2-c1-l2a"],
        datetime="2025-06-01/2025-06-05",
        bbox=(-150000, 2500000, 600000, 3000000),
        crs="epsg:5070",
        resolution=10,
        bands=["red", "green", "blue"],  # optional; auto-discovered if omitted
        chunks={"x": 2048, "y": 2048},
        sort_by="eo:cloud_cover",        # optional; controls mosaic priority
    )
```

`da` is a dask-backed `xr.DataArray` with dimensions `(time, band, y, x)`. Pass a `rasterio.Env` context to supply cloud credentials.

### Parameters

| Parameter | Description |
|---|---|
| `href` | STAC API endpoint URL or path to a local STAC geoparquet file |
| `collections` | STAC collection IDs to search |
| `datetime` | ISO 8601 datetime range, e.g. `"2025-06-01/2025-06-05"` |
| `bbox` | Spatial extent in `crs` units `(minx, miny, maxx, maxy)` |
| `crs` | Target CRS, e.g. `"EPSG:5070"`. GTI reprojects on the fly. |
| `resolution` | Output pixel resolution in `crs` units |
| `bands` | Asset keys to load. Auto-discovered from the first item if omitted. |
| `chunks` | Dask chunk sizes. Defaults to `{"x": 2048, "y": 2048}`. |
| `sort_by` | STAC item property to sort by for mosaic priority. Lower values win. |

## Dependencies

- [`rustac`](https://github.com/stac-utils/rustac) for STAC API queries and geoparquet writing
- [`rasterio`](https://rasterio.readthedocs.io) for GTI driver access (must be built against GDAL >= 3.7 with GTI and Parquet support)
- [`rioxarray`](https://corteva.github.io/rioxarray) for `open_rasterio` with the GTI driver
- [`xarray`](https://xarray.pydata.org) and [`dask`](https://dask.org) for lazy array assembly
