# Postgres 17 with pgvector (base image) and pg_textsearch (Tiger Data BM25, built from a pinned,
# checksum-verified release). Research prototype. Synthetic data only. Not for clinical use.
ARG PGVECTOR_IMAGE=pgvector/pgvector:0.8.6-pg17-bookworm@sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f

FROM ${PGVECTOR_IMAGE} AS build
ARG PG_TEXTSEARCH_VERSION=1.4.0
ARG PG_TEXTSEARCH_SHA256=cab02b4e8af11f98b428d17cca9c5235ab55e12d79c20fa8fbb950708c4a1e92
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl postgresql-server-dev-17 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o pg_textsearch.tar.gz \
      "https://github.com/timescale/pg_textsearch/archive/refs/tags/v${PG_TEXTSEARCH_VERSION}.tar.gz" \
 && echo "${PG_TEXTSEARCH_SHA256}  pg_textsearch.tar.gz" | sha256sum -c - \
 && tar -xzf pg_textsearch.tar.gz \
 && cd "pg_textsearch-${PG_TEXTSEARCH_VERSION}" \
 && make -j"$(nproc)" \
 && make install DESTDIR=/out

FROM ${PGVECTOR_IMAGE}
COPY --from=build /out/ /
COPY init/ /docker-entrypoint-initdb.d/
