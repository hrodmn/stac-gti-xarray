# stac-gti-xarray

## Project overview

A pip-installable Python package that lazily loads STAC mosaics as xarray DataArrays using GDAL's GTI (GDAL Tile Index) driver. The user provides STAC search parameters and spatial/temporal filters; the library returns a dask-backed, lazily evaluated `xr.DataArray` with `(time, band, y, x)` dimensions.

## Desired API

```python
import rasterio
import stac_gti_xarray

with rasterio.Env(...):
    da = stac_gti_xarray.open(
        href="https://earth-search.aws.element84.com/v1",  # STAC API or s3://path/to/stac.parquet
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

`da` is a lazily loaded `xr.DataArray` with dimensions `(time, band, y, x)`. No pixel data is read until part of the array is materialized. It is compatible with an active `rasterio.Env` for passing cloud credentials.

## Architecture and packaging

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full description of how the library works,
why a custom GDAL build is required, how to install for development, and the roadmap for
self-contained pip-installable wheels.

## Dependencies

- `rustac`: STAC API queries and geoparquet writing
- `rasterio`: GTI driver access, rioxarray backend
- `rioxarray`: `open_rasterio` with GTI driver
- `xarray`: DataArray assembly
- `dask`: lazy chunked computation
