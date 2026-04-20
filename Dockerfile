FROM ghcr.io/osgeo/gdal:alpine-normal-latest
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/

# Install build dependencies for compiling rasterio from source
RUN apk add --no-cache \
  g++ \
  gcc \
  make \
  libc-dev \
  linux-headers \
  python3-dev

WORKDIR /app

ADD . /app

RUN uv sync --locked  --no-binary-package rasterio

RUN cat <<EOT > ~/.netrc
  machine urs.earthdata.nasa.gov
  login ${EARTHDATA_USERNAME}
  password ${EARTHDATA_PASSWORD}
EOT

