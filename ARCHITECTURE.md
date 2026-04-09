# Architecture

## How it works

The library converts a STAC search into a lazily loaded 4D xarray DataArray backed by
GDAL's GTI (GDAL Tile Index) raster driver. No pixel data is read until the array is
materialized.

### Pipeline

1. **Query STAC** — `rustac.search_to` queries a STAC API or reads a stac-geoparquet
   file and writes results to a temp geoparquet file.

2. **Write per-band tile indexes** — Flatten nested asset HREFs into top-level columns
   (e.g. `assets.red.href` → `red_href`) and write one geoparquet file per band into a
   temp directory. Separate files per band avoid the GTI file-handle caching bug (see
   below). Each file has a `{band}_href` column as the location field.

3. **Open with GTI** — Call `rioxarray.open_rasterio(..., driver="GTI",
   LOCATION_FIELD="{band}_href", SRS="EPSG:...", SRS_BEHAVIOR="...", ...)` on each
   per-band geoparquet. GTI reprojects on the fly via `SRS` and `SRS_BEHAVIOR` open
   options — no external reprojection step needed.

4. **Assemble the DataArray** — Concatenate per-band arrays along a `band` dimension
   and per-date arrays along a `time` dimension. The result is a dask-backed DataArray
   with dimensions `(time, band, y, x)`.

### Known GTI quirks

- **Multi-asset items**: GTI doesn't know how to load multiple assets per item
  automatically. Each band must be opened separately using `LOCATION_FIELD` to point at
  the correct href column.

- **File-handle caching bug**: When multiple GTI opens point at the same filename, GDAL
  caches the datasource and all opens end up referencing the last `LOCATION_FIELD` set.
  Fix: write a separate geoparquet file per band.

- **Time dimension**: Build per-date arrays by passing a `FILTER` open option with an
  OGR SQL WHERE clause (e.g. `datetime >= '2025-06-01 00:00:00' AND datetime <
  '2025-06-02 00:00:00'`).

### Band auto-discovery

Inspect the first STAC item's assets. Filter to assets whose `type` is a raster media
type (e.g. `image/tiff; application=geotiff; profile=cloud-optimized`). Return the list
of asset keys as available bands. If the user specifies `bands=`, validate against the
discovered list.

---

## Why a custom GDAL build is required

rasterio's PyPI wheels bundle their own `libgdal.so`, but that build does **not** include
the OGR Arrow/Parquet driver needed to read geoparquet files directly with GTI. The GTI
raster driver is present in GDAL >= 3.7 (which rasterio 1.4+ ships), but geoparquet
support is missing.

We also use the `hrodmn/gdal` fork (`fix/gti-user-crs` branch), which contains a patch
for GTI's user-supplied CRS handling that has not yet been merged upstream.

### Custom GDAL cmake flags

```
-DGDAL_BUILD_OPTIONAL_DRIVERS=OFF
-DOGR_BUILD_OPTIONAL_DRIVERS=OFF
-DGDAL_ENABLE_DRIVER_GTiff=ON
-DGDAL_ENABLE_DRIVER_MEM=ON
-DGDAL_ENABLE_DRIVER_VRT=ON
-DGDAL_ENABLE_DRIVER_GTI=ON
-DOGR_ENABLE_DRIVER_PARQUET=ON
-DOGR_ENABLE_DRIVER_GPKG=ON
-DOGR_ENABLE_DRIVER_SQLite=ON
-DGDAL_USE_ARROW=ON
-DGDAL_USE_CURL=ON
-DGDAL_USE_PROJ=ON
-DGDAL_USE_TIFF_INTERNAL=ON
-DGDAL_USE_GEOTIFF_INTERNAL=ON
-DBUILD_TESTING=OFF
-DBUILD_APPS=OFF
```

Arrow/Parquet development libraries must be installed before building GDAL:
- Ubuntu/Debian: use the [Apache Arrow apt repo](https://arrow.apache.org/install/) and
  install `libarrow-dev libparquet-dev`
- macOS: `brew install apache-arrow`

---

## Installing for development

The easiest path is Docker, which handles the full GDAL + rasterio build:

```bash
docker compose up
```

For a manual install without Docker:

1. Clone and build the custom GDAL:
   ```bash
   git clone --depth 1 --branch fix/gti-user-crs https://github.com/hrodmn/gdal.git /tmp/gdal
   cmake -S /tmp/gdal -B /tmp/gdal-build \
     -DCMAKE_INSTALL_PREFIX=/opt/gdal \
     -DCMAKE_BUILD_TYPE=Release \
     # ... flags above ...
   cmake --build /tmp/gdal-build -j$(nproc)
   cmake --install /tmp/gdal-build
   ```

2. Build rasterio from source against the custom GDAL:
   ```bash
   export GDAL_CONFIG=/opt/gdal/bin/gdal-config
   export LD_LIBRARY_PATH=/opt/gdal/lib64:/opt/gdal/lib
   uv sync --no-binary-package rasterio
   ```

---

## Future: self-contained pip-installable wheels

The long-term goal is `pip install stac-gti-xarray` with no custom builds. Two viable
paths have been identified.

### Path A: Bundle rasterio inside the stac-gti-xarray wheel (preferred)

Use `cibuildwheel` + `auditwheel` to produce a compiled wheel that contains:
- `stac_gti_xarray/` (our code)
- `rasterio/` (rasterio Python package + C extensions compiled against custom GDAL)
- `rasterio.libs/` (custom `libgdal.so` with GTI + OGR Parquet, Arrow, PROJ, etc.)

Rasterio's C extensions are compiled from rasterio's own `.pyx` files against the custom
GDAL during the cibuildwheel build. pip's file-ownership tracking naturally prevents
co-installation of the PyPI rasterio alongside this wheel.

The build follows rasterio's own wheel-building pattern — `ci/config.sh` and
`.github/workflows/build-wheels.yaml` in the rasterio repo are the reference — adapted
to:
- Clone the `hrodmn/gdal` fork instead of upstream GDAL
- Enable OGR Parquet + GTI driver flags
- Install Arrow/Parquet development libraries from Apache Arrow's RPM/brew packages
  before building GDAL

### Path B: Separate custom rasterio wheel on a custom package index

Build two wheels in CI: a custom `rasterio` wheel (with our GDAL bundled, following
rasterio's own wheel-building approach exactly) and a pure-Python `stac-gti-xarray`
wheel. Host both on a GitHub Pages simple package index. Install becomes:

```bash
pip install stac-gti-xarray \
  --extra-index-url https://hrodmn.github.io/stac-gti-xarray/simple/
```

This keeps rasterio as a proper standalone package without merging namespaces, at the
cost of requiring the extra index URL flag. The custom rasterio wheel is fully functional
for any rasterio use case — it just ships more GDAL drivers than the PyPI version.
