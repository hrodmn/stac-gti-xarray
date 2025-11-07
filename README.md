# stac-gti-xarray

A demonstration of using GDAL's GTI (raster tile index) driver with STAC geoparquet files to load geospatial data into xarray without any mosaicing or third-party library like odc-stac or stackstac.

## Notes

The Dockerfile uses the official GDAL Alpine image (which includes Parquet support) and explicitly builds rasterio from source with `--no-binary-package rasterio` to avoid the bundled GDAL library which does not come with GTI support for stac-geoparquet.

## Usage

Build and run with Docker Compose:

```bash
docker-compose up
```

Access JupyterLab at `http://localhost:8888` and open `demo.ipynb`.
