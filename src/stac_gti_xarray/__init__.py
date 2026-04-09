"""stac-gti-xarray: lazily load STAC mosaics as xarray DataArrays via GDAL's GTI driver."""

from stac_gti_xarray._core import open

__all__ = ["open"]
