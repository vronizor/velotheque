ARG PG_MAJOR=18
FROM postgres:${PG_MAJOR}-bookworm
ARG PG_MAJOR
ARG POSTGIS_SERIES=3.6
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      "postgresql-${PG_MAJOR}-postgis-3=${POSTGIS_SERIES}.*" \
      "postgresql-${PG_MAJOR}-postgis-3-scripts=${POSTGIS_SERIES}.*" \
 && rm -rf /var/lib/apt/lists/*
