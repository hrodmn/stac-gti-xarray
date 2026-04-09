"""Core implementation for stac-gti-xarray."""

import asyncio
import atexit
import logging
import uuid
from datetime import datetime as _datetime
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import rioxarray  # noqa: F401 - registers the rioxarray backend
import rustac
import xarray as xr
from rasterio.warp import transform_bounds

logger = logging.getLogger(__name__)

RASTER_MEDIA_TYPES = frozenset(
    {
        "image/tiff",
        "image/tiff; application=geotiff",
        "image/tiff; application=geotiff; profile=cloud-optimized",
        "image/vnd.stac.geotiff; cloud-optimized=true",
        "image/x.geotiff",
        "image/jp2",
    }
)


def _normalize_datetime(datetime_str: str) -> str:
    """Normalize a datetime range string to include full HH:MM:SS components.

    Plain date strings like ``"2025-06-01/2025-06-05"`` are expanded to
    ``"2025-06-01T00:00:00Z/2025-06-05T23:59:59Z"`` so that STAC API servers
    that require full ISO 8601 datetimes behave correctly.  Strings that
    already include a time component are returned unchanged.

    Args:
        datetime_str: A ``/``-separated datetime range string.

    Returns:
        The normalized datetime range string.
    """
    start_str, end_str = datetime_str.split("/")

    def _expand(s: str, end_of_day: bool) -> str:
        if "T" in s or " " in s:
            return s
        suffix = "T23:59:59Z" if end_of_day else "T00:00:00Z"
        return s + suffix

    normalized = (
        f"{_expand(start_str, end_of_day=False)}/{_expand(end_str, end_of_day=True)}"
    )
    if normalized != datetime_str:
        logger.debug("normalized datetime %r -> %r", datetime_str, normalized)
    return normalized


def _parse_datetime_range(datetime_str: str) -> list[_datetime]:
    """Parse an ISO 8601 datetime range string into a list of dates.

    Handles both plain date format (``"2025-06-01/2025-06-05"``) and full
    ISO 8601 with time and timezone (``"2025-06-01T00:00:00Z/2025-06-05T23:59:59Z"``).

    Args:
        datetime_str: A ``/``-separated datetime range string.

    Returns:
        A list of :class:`datetime` objects, one per calendar day from start to
        end inclusive.
    """
    start_str, end_str = datetime_str.split("/")
    start = _datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    end = _datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    dates = []
    current = start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end_date = end.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    logger.debug("parsed datetime range %r -> %d dates", datetime_str, len(dates))
    return dates


def _discover_bands(parquet_path: Path) -> list[str]:
    """Discover raster asset keys from the first STAC item in a geoparquet file.

    Args:
        parquet_path: Path to a STAC geoparquet file.

    Returns:
        List of asset keys whose media type is a known raster type.

    Raises:
        ValueError: If no raster assets are found in the first item.
    """
    logger.debug("discovering bands from %s", parquet_path)
    result = rustac.read_sync(str(parquet_path))

    # read_sync may return a FeatureCollection dict, a list, or a single item
    if isinstance(result, dict):
        items = result.get("features", [result])
    elif hasattr(result, "__iter__"):
        items = list(result)
    else:
        items = [result]

    if not items:
        raise ValueError(f"No STAC items found in {parquet_path}")

    logger.debug("inspecting assets from first of %d items", len(items))
    first_item = items[0]
    assets: dict = first_item.get("assets", {}) if isinstance(first_item, dict) else {}

    raster_bands = [
        key
        for key, asset in assets.items()
        if isinstance(asset, dict) and asset.get("type", "") in RASTER_MEDIA_TYPES
    ]

    if not raster_bands:
        raise ValueError(
            f"No raster assets found in first STAC item. "
            f"Available asset types: {[a.get('type') for a in assets.values()]}"
        )

    logger.info("discovered %d raster bands: %s", len(raster_bands), raster_bands)
    return raster_bands


def _build_gti_options(
    band: str,
    date: _datetime,
    crs: str | None,
    bbox: tuple[float, float, float, float] | None,
    resolution: int | float | None,
    sort_by: str | None,
) -> dict:
    """Build GTI open options for a single band and date.

    Args:
        band: Asset key to use as the location field (e.g. ``"red"``).
        date: The calendar day to filter items to.
        crs: Target CRS for reprojection, or ``None`` to use native CRS.
        bbox: Spatial extent in the target CRS, or ``None`` for native extent.
        resolution: Output resolution in target CRS units, or ``None`` for native.
        sort_by: STAC property name for mosaic priority ordering.

    Returns:
        Dict of keyword arguments to pass to :func:`rioxarray.open_rasterio`.
    """
    next_date = date + timedelta(days=1)
    dt_filter = (
        f"datetime >= '{date:%Y-%m-%d 00:00:00}' "
        f"AND datetime < '{next_date:%Y-%m-%d 00:00:00}'"
    )
    options: dict = {
        "LOCATION_FIELD": f"assets.{band}.href",
        "SORT_FIELD": sort_by if sort_by else "datetime",
        "SORT_FIELD_ASC": "YES",
        "FILTER": dt_filter,
    }
    if crs is not None:
        options["SRS"] = crs
        options["SRS_BEHAVIOR"] = "REPROJECT"
    if bbox is not None:
        options["MINX"] = bbox[0]
        options["MINY"] = bbox[1]
        options["MAXX"] = bbox[2]
        options["MAXY"] = bbox[3]
    if resolution is not None:
        options["RESX"] = resolution
        options["RESY"] = resolution
    logger.debug("GTI options for band=%r date=%s: %s", band, date.date(), options)
    return options


async def _open_one_array(
    parquet_path: Path,
    tempdir: Path,
    band: str,
    date: _datetime,
    options: dict,
    chunks: dict[str, int],
) -> xr.DataArray | None:
    """Open a single band/date slice via GTI in a thread pool.

    Creates a unique symlink to ``parquet_path`` to work around GTI's
    file-handle caching bug, then opens via :func:`rioxarray.open_rasterio`
    in a thread so multiple slices can be opened concurrently.

    Args:
        parquet_path: Path to the source STAC geoparquet.
        tempdir: Temp directory where the symlink is created.
        band: Asset key for this slice.
        date: Calendar day for this slice.
        options: GTI open options dict from :func:`_build_gti_options`.
        chunks: Dask chunk sizes.

    Returns:
        A 3-D ``(band=1, y, x)`` dask-backed :class:`xr.DataArray`, or
        ``None`` if the tile index contains no items matching the filter.
    """
    sym = tempdir / f"{uuid.uuid4()}.parquet"
    sym.symlink_to(parquet_path)
    logger.debug(
        "opening band=%r date=%s via GTI symlink %s", band, date.date(), sym.name
    )
    from rasterio.errors import RasterioIOError

    try:
        arr = await asyncio.to_thread(
            rioxarray.open_rasterio,
            sym,
            driver="GTI",
            chunks=chunks,
            **options,
        )
    except RasterioIOError as exc:
        logger.warning(
            "skipping band=%r date=%s (no items in tile index): %s",
            band,
            date.date(),
            exc,
        )
        return None
    logger.debug("opened band=%r date=%s shape=%s", band, date.date(), arr.shape)
    return arr.isel(band=0).expand_dims(dim={"band": [band]}).astype("uint16")


async def _open_async(
    href: str,
    collections: list[str],
    datetime: str,
    bbox: tuple[float, float, float, float],
    crs: str | None,
    resolution: int | float | None,
    bands: list[str] | None,
    chunks: dict[str, int] | None,
    sort_by: str | None,
    stac_timeout: float | None,
) -> xr.DataArray:
    """Async implementation of :func:`open`.

    Args:
        href: STAC API endpoint URL or path to a STAC geoparquet file.
        collections: STAC collection IDs to search.
        datetime: ISO 8601 datetime range (e.g. ``"2025-06-01/2025-06-05"``).
        bbox: Spatial extent in ``crs`` coordinates ``(minx, miny, maxx, maxy)``.
        crs: Target CRS for output (e.g. ``"EPSG:5070"``). If ``None``,
            the native CRS of the tile index is used.
        resolution: Output pixel resolution in ``crs`` units. If ``None``,
            the native resolution is used.
        bands: Asset keys to load. If ``None``, all raster assets are loaded.
        chunks: Dask chunk sizes (e.g. ``{"x": 2048, "y": 2048}``).
        sort_by: STAC item property to sort by for mosaic priority.

    Returns:
        A lazily-loaded dask-backed :class:`xr.DataArray` with dimensions
        ``(time, band, y, x)``.
    """
    _tempdir = TemporaryDirectory()
    atexit.register(_tempdir.cleanup)
    tempdir = Path(_tempdir.name)

    parquet_path = tempdir / "items.parquet"

    # Convert bbox to EPSG:4326 for the STAC API query
    if crs is not None:
        bbox_4326 = transform_bounds(crs, "epsg:4326", *bbox)
        logger.debug("reprojected bbox from %s to EPSG:4326: %s", crs, bbox_4326)
    else:
        bbox_4326 = bbox

    search_kwargs: dict = {
        "collections": collections,
        "datetime": _normalize_datetime(datetime),
        "bbox": bbox_4326,
        "compression": None,
    }
    if sort_by is not None:
        search_kwargs["sortby"] = f"properties.{sort_by}"

    logger.info("querying STAC at %s with %s", href, search_kwargs)
    coro = rustac.search_to(str(parquet_path), href, **search_kwargs)
    try:
        await asyncio.wait_for(coro, timeout=stac_timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"STAC query timed out after {stac_timeout}s. "
            "Try a smaller bbox, shorter datetime range, or increase stac_timeout."
        )

    if bands is not None:
        discovered = set(_discover_bands(parquet_path))
        missing = set(bands) - discovered
        if missing:
            raise ValueError(
                f"Requested bands not found in STAC items: {sorted(missing)}. "
                f"Available: {sorted(discovered)}"
            )
        resolved_bands = bands
        logger.info("using requested bands: %s", resolved_bands)
    else:
        resolved_bands = _discover_bands(parquet_path)

    dates = _parse_datetime_range(datetime)
    resolved_chunks = chunks if chunks is not None else {"x": 2048, "y": 2048}
    logger.debug("chunk sizes: %s", resolved_chunks)

    # Build all (date, band) tasks and open them concurrently
    tasks = []
    keys = []
    for date in dates:
        for band in resolved_bands:
            options = _build_gti_options(band, date, crs, bbox, resolution, sort_by)
            tasks.append(
                _open_one_array(
                    parquet_path, tempdir, band, date, options, resolved_chunks
                )
            )
            keys.append((date, band))

    logger.info(
        "opening %d arrays (%d dates x %d bands)",
        len(tasks),
        len(dates),
        len(resolved_bands),
    )
    results = await asyncio.gather(*tasks)
    logger.info("all arrays opened successfully")

    # Reshape flat list into (dates x bands) and concatenate
    n_bands = len(resolved_bands)
    date_arrays = []
    for i, date in enumerate(dates):
        band_slices = results[i * n_bands : (i + 1) * n_bands]
        valid = [arr for arr in band_slices if arr is not None]
        if not valid:
            logger.warning("skipping date=%s: no items found for any band", date.date())
            continue
        date_arr = xr.concat(valid, dim="band").expand_dims(dim={"time": [date]})
        date_arrays.append(date_arr)

    if not date_arrays:
        raise ValueError("No STAC items found for any date in the requested range.")

    da = xr.concat(date_arrays, dim="time")
    logger.info(
        "built DataArray with shape %s (time=%d, band=%d)",
        da.shape,
        len(dates),
        len(resolved_bands),
    )
    return da


def open(
    href: str,
    collections: list[str],
    datetime: str,
    bbox: tuple[float, float, float, float],
    crs: str | None = None,
    resolution: int | float | None = None,
    bands: list[str] | None = None,
    chunks: dict[str, int] | None = None,
    sort_by: str | None = None,
    stac_timeout: float | None = 60.0,
) -> xr.DataArray:
    """Open a STAC mosaic as a lazily-loaded xarray DataArray.

    Queries a STAC API (or reads a local STAC geoparquet), then opens the
    matching items as a dask-backed :class:`xr.DataArray` with dimensions
    ``(time, band, y, x)`` using GDAL's GTI raster driver. No pixel data is
    read until the array is materialized.

    Args:
        href: STAC API endpoint URL or path to a STAC geoparquet file.
        collections: STAC collection IDs to search.
        datetime: ISO 8601 datetime range (e.g. ``"2025-06-01/2025-06-05"``).
        bbox: Spatial extent in ``crs`` coordinates ``(minx, miny, maxx, maxy)``.
        crs: Target CRS for output (e.g. ``"EPSG:5070"``). If ``None``,
            the native CRS of the tile index is used.
        resolution: Output pixel resolution in ``crs`` units. If ``None``,
            the native resolution is used.
        bands: Asset keys to load (e.g. ``["red", "green", "blue"]``). If
            ``None``, all raster assets discovered in the first item are loaded.
        chunks: Dask chunk sizes (e.g. ``{"x": 2048, "y": 2048}``). Defaults
            to ``{"x": 2048, "y": 2048}``.
        sort_by: STAC item property to sort by for mosaic priority (e.g.
            ``"eo:cloud_cover"``). Lower values are preferred.
        stac_timeout: Seconds to wait for the STAC query before raising
            :class:`TimeoutError`. Defaults to ``60.0``. Pass ``None`` to
            disable the timeout.

    Returns:
        A lazily-loaded dask-backed :class:`xr.DataArray` with dimensions
        ``(time, band, y, x)``.

    Example:
        >>> import stac_gti_xarray
        >>> da = stac_gti_xarray.open(
        ...     href="https://earth-search.aws.element84.com/v1",
        ...     collections=["sentinel-2-c1-l2a"],
        ...     datetime="2025-06-01/2025-06-05",
        ...     bbox=(-150000, 2500000, 600000, 3000000),
        ...     crs="epsg:5070",
        ...     resolution=10,
        ...     bands=["red", "green", "blue"],
        ...     sort_by="eo:cloud_cover",
        ... )
    """
    return asyncio.run(
        _open_async(
            href=href,
            collections=collections,
            datetime=datetime,
            bbox=bbox,
            crs=crs,
            resolution=resolution,
            bands=bands,
            chunks=chunks,
            sort_by=sort_by,
            stac_timeout=stac_timeout,
        )
    )
