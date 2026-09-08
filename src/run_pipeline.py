"""Orquestador. Un solo punto de entrada, para la máquina y para la persona.

    python -m src.run_pipeline

Es el comando que ejecuta GitHub Actions y también el que corrés vos a mano.
Que sean el mismo es lo que hace el trabajo reproducible.
"""

import datetime as dt
import sys
import uuid

from . import api, ingest, quality, transform
from .config import DB_TARGET, SCHEMA_MART
from .db import connect


def main() -> int:
    run_id = uuid.uuid4().hex[:12]
    started = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    print("=" * 70)
    print(f"PIPELINE CALIDAD DEL AIRE  |  corrida {run_id}")
    print(f"destino: {DB_TARGET}  |  inicio: {started:%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 70)

    con = connect()
    filas, estado, error = 0, "success", None

    try:
        filas = ingest.run(con, run_id)
        transform.run(con)
        resultados = quality.run(con, run_id)
        if any(not r["passed"] for r in resultados):
            estado = "success_with_warnings"
    except Exception as exc:
        estado, error = "failed", str(exc)[:1000]
        print(f"\nERROR: {exc}", file=sys.stderr)
    finally:
        finished = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        con.execute(
            f"INSERT INTO {SCHEMA_MART}.pipeline_runs VALUES (?,?,?,?,?,?,?)",
            [run_id, started, finished, filas, api.REQUEST_COUNT, estado, error],
        )
        duracion = (finished - started).total_seconds()
        print("\n" + "=" * 70)
        print(
            f"{estado.upper()}  |  {filas} filas  |  "
            f"{api.REQUEST_COUNT} requests  |  {duracion:.0f}s"
        )
        print("=" * 70)
        con.close()

    return 1 if estado == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
