FROM ghcr.io/osgeo/gdal:alpine-normal-3.12.1-amd64
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /uvx /bin/

# Install build dependencies for compiling rasterio from source
RUN apk add --no-cache \
  g++ \
  gcc \
  make \
  libc-dev \
  linux-headers

ARG NB_USER=jovyan
ARG NB_UID=1000
ENV USER=${NB_USER}
ENV NB_UID=${NB_UID}
ENV HOME=/home/${NB_USER}

RUN adduser --disabled-password \
  --gecos "Default user" \
  --uid ${NB_UID} \
  ${NB_USER}

WORKDIR ${HOME}

# Copy the project into the image
COPY . ${HOME}
USER root
RUN chown -R ${NB_UID} ${HOME}
USER ${NB_USER}

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked 

ENV PATH="${HOME}/.venv/bin:$PATH"

RUN cat <<EOT > ~/.netrc
  machine urs.earthdata.nasa.gov
  login ${EARTHDATA_USERNAME}
  password ${EARTHDATA_PASSWORD}
EOT

