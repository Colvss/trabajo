"""Transformación: raw -> staging -> clean -> mart.

Las reglas de calidad no están escritas en el SQL: se materializan como tablas
a partir de config.yml y el SQL las consulta. De esa forma cambiar un umbral
es editar el YAML, y el informe puede citar la tabla de reglas vigente.
"""

from pathlib import Path

import pandas as pd

from .config import CFG, SCHEMA_MART, SCHEMA_RAW, SCHEMA_STG

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def build_rule_tables(con) -> None:
    """Convierte config.yml en tablas consultables desde SQL."""
    quality = CFG["quality"]

    sentinels = pd.DataFrame({"value": [float(v) for v in quality["sentinel_values"]]})
    con.register("df_sentinels", sentinels)
    con.execute(
        f"CREATE OR REPLACE TABLE {SCHEMA_STG}.sentinel_values AS SELECT * FROM df_sentinels"
    )
    con.unregister("df_sentinels")

    rules = pd.DataFrame(
        [
            {"parameter": p, "min_value": float(lo), "max_value": float(hi)}
            for p, (lo, hi) in quality["plausible_range"].items()
        ]
    )
    con.register("df_rules", rules)
    con.execute(
        f"CREATE OR REPLACE TABLE {SCHEMA_STG}.param_rules AS SELECT * FROM df_rules"
    )
    con.unregister("df_rules")

    # ug/m3 = ppb * (peso_molecular / 24.45)  a 25 grados C y 1 atm
    conv = pd.DataFrame(
        [
            {"parameter": p, "molecular_weight": float(mw), "factor": float(mw) / 24.45}
            for p, mw in CFG["molecular_weights"].items()
        ]
    )
    con.register("df_conv", conv)
    con.execute(
        f"CREATE OR REPLACE TABLE {SCHEMA_STG}.unit_conversion AS SELECT * FROM df_conv"
    )
    con.unregister("df_conv")

    who = pd.DataFrame(
        [
            {"parameter": p, "limite_24h": float(v)}
            for p, v in CFG["who_guidelines_24h"].items()
        ]
    )
    con.register("df_who", who)
    con.execute(
        f"CREATE OR REPLACE TABLE {SCHEMA_STG}.who_guidelines AS SELECT * FROM df_who"
    )
    con.unregister("df_who")


def run_sql_file(con, filename: str) -> None:
    sql = (SQL_DIR / filename).read_text(encoding="utf-8")
    sql = sql.format(
        raw=SCHEMA_RAW,
        stg=SCHEMA_STG,
        mart=SCHEMA_MART,
        min_coverage=CFG["quality"]["min_coverage_pct"],
    )
    # DuckDB ejecuta varias sentencias seguidas, pero las partimos para poder
    # señalar cuál falló si algo se rompe.
    for statement in [s.strip() for s in sql.split(";") if s.strip()]:
        try:
            con.execute(statement)
        except Exception as exc:
            head = statement.splitlines()[0][:90]
            raise RuntimeError(f"Falló una sentencia de {filename}\n  {head}\n  {exc}")
    print(f"  {filename} ejecutado")


def run(con) -> None:
    print("\n[2/3] TRANSFORMACIÓN")
    build_rule_tables(con)
    print("  tablas de reglas materializadas desde config.yml")
    for filename in ("01_staging.sql", "02_clean.sql", "03_marts.sql"):
        run_sql_file(con, filename)

    filas = con.execute(
        f"SELECT count(*) FROM {SCHEMA_STG}.measurements_enriched"
    ).fetchone()[0]
    dias = con.execute(
        f"SELECT count(*) FROM {SCHEMA_MART}.daily_air_quality"
    ).fetchone()[0]
    print(f"  {filas} mediciones limpias -> {dias} filas diarias en el mart")
