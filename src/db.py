"""Conexión a DuckDB / MotherDuck y creación del esquema.

El mismo código sirve para las dos: MotherDuck es DuckDB con una cadena de
conexión distinta ("md:airq"). Eso hace que el pipeline se pueda desarrollar y
probar en local sin red, y desplegar en la nube cambiando una variable.
"""

import duckdb

from .config import (
    DB_TARGET,
    MOTHERDUCK_TOKEN,
    SCHEMA_MART,
    SCHEMA_RAW,
    SCHEMA_STG,
)


def connect() -> duckdb.DuckDBPyConnection:
    if DB_TARGET.startswith("md:"):
        if not MOTHERDUCK_TOKEN:
            raise SystemExit(
                "Falta MOTHERDUCK_TOKEN.\n"
                "  Conseguilo en https://app.motherduck.com -> Settings -> Access Tokens\n"
                "  O usá la base local: DB_TARGET=airq.duckdb"
            )
        con = duckdb.connect(f"{DB_TARGET}?motherduck_token={MOTHERDUCK_TOKEN}")
    else:
        con = duckdb.connect(DB_TARGET)
    ensure_schema(con)
    return con


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    for schema in (SCHEMA_RAW, SCHEMA_STG, SCHEMA_MART):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    # Zona de aterrizaje: append-only y fiel a la fuente. No se limpia ni se
    # deduplica acá a propósito -- los duplicados por reingesta son uno de los
    # hallazgos de calidad que el informe tiene que reportar (punto 3).
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_RAW}.measurements (
            sensor_id       BIGINT,
            location_id     BIGINT,
            parameter       VARCHAR,
            units           VARCHAR,
            value           DOUBLE,
            datetime_utc    TIMESTAMP,
            datetime_local  VARCHAR,
            coverage_pct    DOUBLE,
            payload         JSON,
            source_url      VARCHAR,
            ingested_at     TIMESTAMP,
            run_id          VARCHAR
        )
        """
    )

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_RAW}.locations (
            location_id   BIGINT PRIMARY KEY,
            name          VARCHAR,
            locality      VARCHAR,
            country_code  VARCHAR,
            timezone      VARCHAR,
            latitude      DOUBLE,
            longitude     DOUBLE,
            is_mobile     BOOLEAN,
            is_monitor    BOOLEAN,
            provider      VARCHAR,
            payload       JSON,
            ingested_at   TIMESTAMP
        )
        """
    )

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_RAW}.sensors (
            sensor_id    BIGINT PRIMARY KEY,
            location_id  BIGINT,
            parameter    VARCHAR,
            units        VARCHAR,
            ingested_at  TIMESTAMP
        )
        """
    )

    # Histórico de ejecuciones: evidencia de la automatización para el punto 2a.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_MART}.pipeline_runs (
            run_id             VARCHAR,
            started_at         TIMESTAMP,
            finished_at        TIMESTAMP,
            rows_ingested      BIGINT,
            api_requests       BIGINT,
            status             VARCHAR,
            error_message      VARCHAR
        )
        """
    )

    # Resultados de calidad, una fila por regla y por corrida (punto 3).
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_MART}.dq_report (
            run_id            VARCHAR,
            checked_at        TIMESTAMP,
            dimension         VARCHAR,
            check_name        VARCHAR,
            rule              VARCHAR,
            records_affected  BIGINT,
            records_total     BIGINT,
            pct_affected      DOUBLE,
            action_taken      VARCHAR,
            passed            BOOLEAN
        )
        """
    )
