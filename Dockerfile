FROM ghcr.io/osgeo/gdal:alpine-normal-latest
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install build dependencies for compiling rasterio from source
RUN apk add --no-cache \
  g++ \
  gcc \
  make \
  libc-dev \
  linux-headers

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
  --mount=type=bind,source=uv.lock,target=uv.lock \
  --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
  uv sync --locked --no-install-project --no-binary-package rasterio

# Copy the project into the image
ADD . /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked 

RUN cat <<EOT > ~/.netrc
  machine urs.earthdata.nasa.gov
  login ${EARTHDATA_USERNAME}
  password ${EARTHDATA_PASSWORD}
EOT

