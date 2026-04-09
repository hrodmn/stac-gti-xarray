# syntax=docker/dockerfile:1
ARG GDAL_REPO=https://github.com/osgeo/gdal.git
ARG GDAL_REF=master

# ── Stage 1: build minimal GDAL from source ───────────────────────────────────
# Requires Arrow/Parquet for the OGR Parquet (geoparquet) driver, which is
# absent from pre-built rasterio wheels and the osgeo/gdal Alpine image.
FROM ubuntu:24.04 AS gdal-builder

ARG GDAL_REPO
ARG GDAL_REF

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make cmake curl ca-certificates gnupg git \
    libproj-dev \
    libsqlite3-dev \
    libcurl4-openssl-dev \
    libtiff-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://apache.jfrog.io/artifactory/arrow/ubuntu/apache-arrow-apt-source-latest-noble.deb \
    -o /tmp/arrow-apt.deb \
    && apt-get install -y /tmp/arrow-apt.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends libarrow-dev libparquet-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/arrow-apt.deb

RUN git clone --depth 1 --branch "${GDAL_REF}" "${GDAL_REPO}" /tmp/gdal

RUN cmake \
    -S /tmp/gdal \
    -B /tmp/gdal-build \
    -DCMAKE_INSTALL_PREFIX=/opt/gdal \
    -DCMAKE_BUILD_TYPE=Release \
    -DGDAL_BUILD_OPTIONAL_DRIVERS=OFF \
    -DOGR_BUILD_OPTIONAL_DRIVERS=OFF \
    -DGDAL_ENABLE_DRIVER_GTiff=ON \
    -DGDAL_ENABLE_DRIVER_MEM=ON \
    -DGDAL_ENABLE_DRIVER_VRT=ON \
    -DGDAL_ENABLE_DRIVER_GTI=ON \
    -DOGR_ENABLE_DRIVER_PARQUET=ON \
    -DOGR_ENABLE_DRIVER_GPKG=ON \
    -DOGR_ENABLE_DRIVER_SQLite=ON \
    -DGDAL_USE_ARROW=ON \
    -DGDAL_USE_CURL=ON \
    -DGDAL_USE_PROJ=ON \
    -DGDAL_USE_TIFF_INTERNAL=ON \
    -DGDAL_USE_GEOTIFF_INTERNAL=ON \
    -DBUILD_TESTING=OFF \
    -DBUILD_APPS=OFF \
    && cmake --build /tmp/gdal-build -j$(nproc) \
    && cmake --install /tmp/gdal-build

# Collect Arrow/Parquet runtime libs and their non-standard transitive deps
# (libthrift etc. come from the Arrow apt repo, not Ubuntu's standard packages)
RUN mkdir -p /opt/arrow-libs && \
    find /usr/lib/x86_64-linux-gnu -maxdepth 1 \
        \( -name 'libarrow*.so*' \
        -o -name 'libparquet*.so*' \
        -o -name 'libthrift*.so*' \
        -o -name 'libutf8proc*.so*' \) \
        -exec cp -P {} /opt/arrow-libs/ \;

# ── Stage 2: build Python environment ─────────────────────────────────────────
# Separate stage so build tools (gcc, -dev headers) don't land in the runtime
# image. The compiled .venv is copied to the final stage below.
FROM ubuntu:24.04 AS python-builder

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/
COPY --from=gdal-builder /opt/gdal /opt/gdal
COPY --from=gdal-builder /opt/arrow-libs/ /usr/lib/x86_64-linux-gnu/

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make \
    libproj-dev \
    libsqlite3-dev \
    libcurl4-openssl-dev \
    libtiff-dev \
    && rm -rf /var/lib/apt/lists/*

RUN ldconfig

ENV GDAL_CONFIG=/opt/gdal/bin/gdal-config
ENV LD_LIBRARY_PATH=/opt/gdal/lib64:/opt/gdal/lib

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
  --mount=type=bind,source=uv.lock,target=uv.lock \
  --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
  uv sync --locked --no-install-project --no-binary-package rasterio

ADD . /app

RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked

# ── Stage 3: runtime image ────────────────────────────────────────────────────
FROM ubuntu:24.04

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/
COPY --from=gdal-builder /opt/gdal /opt/gdal
COPY --from=gdal-builder /opt/arrow-libs/ /usr/lib/x86_64-linux-gnu/
COPY --from=python-builder /root/.local/share/uv/python /root/.local/share/uv/python
COPY --from=python-builder /app /app

# Runtime-only packages (no gcc, no -dev headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libproj25 \
    libsqlite3-0 \
    libcurl4t64 \
    libtiff6 \
    libxml2 \
    libabsl20220623t64 \
    libprotobuf32t64 \
    libsnappy1v5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN ldconfig

ENV GDAL_CONFIG=/opt/gdal/bin/gdal-config
ENV LD_LIBRARY_PATH=/opt/gdal/lib64:/opt/gdal/lib

WORKDIR /app

RUN cat <<EOT > ~/.netrc
  machine urs.earthdata.nasa.gov
  login ${EARTHDATA_USERNAME}
  password ${EARTHDATA_PASSWORD}
EOT
