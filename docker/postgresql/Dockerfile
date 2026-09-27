FROM pgvector/pgvector:pg18

ARG AGE_VERSION=PG18/v1.7.0-rc0

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    flex \
    bison \
    postgresql-server-dev-18 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --branch ${AGE_VERSION} https://github.com/apache/age.git /tmp/age && \
    cd /tmp/age && \
    make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config && \
    make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config install && \
    rm -rf /tmp/age

RUN echo "shared_preload_libraries = 'age'" >> /usr/share/postgresql/18/postgresql.conf.sample
