FROM pgvector/pgvector:pg16

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        make gcc postgresql-server-dev-16 wget ca-certificates && \
    wget -O pg_bigm.tar.gz https://github.com/pgbigm/pg_bigm/archive/refs/tags/v1.2-20240606.tar.gz && \
    tar zxf pg_bigm.tar.gz && \
    cd pg_bigm-1.2-20240606 && \
    make USE_PGXS=1 && \
    make USE_PGXS=1 install && \
    cd .. && \
    rm -rf pg_bigm.tar.gz pg_bigm-1.2-20240606 && \
    apt-get remove -y make gcc postgresql-server-dev-16 wget && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*
