"""Prueba de humo del pipeline sin tocar la API.

Inyecta datos sintéticos en la zona raw con problemas conocidos y verifica que
las reglas de calidad los detecten con los conteos exactos esperados.

    .venv\\Scripts\\python.exe tests/smoke_test.py

Sirve como verificación de carga (punto 2c del enunciado): demuestra que las
transformaciones producen lo que se espera, con un caso de prueba controlado.
"""

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Base local desechable: debe definirse ANTES de importar la configuración.
TMP_DB = Path(tempfile.gettempdir()) / "airq_smoke_test.duckdb"
TMP_DB.unlink(missing_ok=True)
os.environ["DB_TARGET"] = str(TMP_DB)

import pandas as pd  # noqa: E402

from src import quality, transform  # noqa: E402
from src.config import SCHEMA_MART, SCHEMA_RAW, SCHEMA_STG  # noqa: E402
from src.db import connect  # noqa: E402

BASE = dt.datetime(2026, 3, 1, 0, 0, 0)
SENSOR, LOCATION = 101, 9001

# Problemas plantados a propósito, con su conteo esperado.
#
# registros_imputados = 3 porque los TRES problemas plantados (centinela, fuera
# de rango y nulo) son huecos aislados de una hora rodeados de datos válidos,
# así que los tres se interpolan. Es el comportamiento buscado.
#
# fuera_de_rango_fisico = 1 y no 2: el centinela -999 también cae fuera del
# rango, pero se le aplica precedencia para que no se cuente dos veces.
ESPERADO = {
    "registros_duplicados": 1,
    "valores_centinela": 1,
    "fuera_de_rango_fisico": 1,
    "valores_nulos": 1,
    "registros_imputados": 3,
}

# La regla de frescura compara contra la hora actual, así que con datos
# sintéticos de una fecha fija siempre falla. No se verifica acá.
IGNORADAS = {"frescura_del_dato"}


def construir_datos() -> pd.DataFrame:
    filas = []
    ingesta = dt.datetime(2026, 3, 3, 12, 0, 0)

    for h in range(48):
        ts = BASE + dt.timedelta(hours=h)
        if h == 10:
            valor = -999.0          # centinela -> descartar
        elif h == 20:
            valor = 5000.0          # fuera de rango fisico -> descartar
        elif h == 30:
            valor = None            # nulo aislado -> imputar (vecinos validos)
        else:
            valor = 20.0 + (h % 7)
        filas.append(
            {
                "sensor_id": SENSOR,
                "location_id": LOCATION,
                "parameter": "pm25",
                "units": "µg/m³",
                "value": valor,
                "datetime_utc": ts,
                "datetime_local": None,
                "coverage_pct": 95.0,
                "payload": "{}",
                "source_url": "test",
                "ingested_at": ingesta,
                "run_id": "smoke",
            }
        )

    # Reingesta de una hora ya presente: duplicado por (sensor, datetime).
    repetida = dict(filas[5])
    repetida["value"] = 99.0
    repetida["ingested_at"] = ingesta + dt.timedelta(hours=1)
    filas.append(repetida)

    return pd.DataFrame(filas)


def main() -> int:
    con = connect()

    con.execute(
        f"INSERT INTO {SCHEMA_RAW}.locations VALUES "
        f"({LOCATION}, 'Estacion Test', 'Comuna Test', 'CL', 'UTC', "
        f"-33.45, -70.66, FALSE, TRUE, 'test', '{{}}', current_timestamp)"
    )

    df = construir_datos()
    con.register("df_test", df)
    con.execute(
        f"""
        INSERT INTO {SCHEMA_RAW}.measurements
        SELECT sensor_id, location_id, parameter, units, value, datetime_utc,
               datetime_local, coverage_pct, payload::JSON, source_url,
               ingested_at, run_id
        FROM df_test
        """
    )
    print(f"Insertadas {len(df)} filas sinteticas en la zona raw\n")

    transform.run(con)
    resultados = quality.run(con, "smoke", write_artifacts=False)

    print("\n" + "=" * 70)
    print("VERIFICACION DE CONTEOS")
    print("=" * 70)

    por_nombre = {r["check_name"]: r["records_affected"] for r in resultados}
    fallas = []
    for nombre, esperado in ESPERADO.items():
        obtenido = por_nombre.get(nombre)
        ok = obtenido == esperado
        print(f"  {'OK   ' if ok else 'FALLA'} {nombre:<28} "
              f"esperado={esperado}  obtenido={obtenido}")
        if not ok:
            fallas.append(nombre)

    # El valor duplicado (99.0) no debe sobrevivir: gana la version mas reciente,
    # pero la hora 5 sigue existiendo una sola vez.
    n_hora5 = con.execute(
        f"SELECT count(*) FROM {SCHEMA_STG}.measurements_clean "
        f"WHERE sensor_id = {SENSOR} AND datetime_utc = TIMESTAMP '2026-03-01 05:00:00'"
    ).fetchone()[0]
    ok = n_hora5 == 1
    print(f"  {'OK   ' if ok else 'FALLA'} {'deduplicacion_hora_5':<28} "
          f"esperado=1  obtenido={n_hora5}")
    if not ok:
        fallas.append("deduplicacion")

    # El hueco de la hora 30 debe quedar interpolado entre sus vecinos.
    # Los huecos de las horas 10, 20 y 30 deben quedar interpolados con el
    # promedio de sus vecinos: 23.0, 22.5 y 22.0 respectivamente.
    imputado = [
        round(v[0], 2) for v in con.execute(
            f"SELECT valor FROM {SCHEMA_STG}.measurements_clean "
            f"WHERE sensor_id = {SENSOR} AND es_imputado ORDER BY datetime_utc"
        ).fetchall()
    ]
    ok = imputado == [23.0, 22.5, 22.0]
    print(f"  {'OK   ' if ok else 'FALLA'} {'valores_interpolados':<28} "
          f"esperado=[23.0, 22.5, 22.0]  obtenido={imputado}")
    if not ok:
        fallas.append("interpolacion")

    # Las marts deben poblarse.
    dias = con.execute(f"SELECT count(*) FROM {SCHEMA_MART}.daily_air_quality").fetchone()[0]
    print(f"  {'OK   ' if dias > 0 else 'FALLA'} {'mart_poblado':<28} "
          f"{dias} filas diarias")
    if dias == 0:
        fallas.append("mart vacio")

    con.close()
    TMP_DB.unlink(missing_ok=True)

    print("=" * 70)
    if fallas:
        print(f"RESULTADO: {len(fallas)} verificacion(es) fallida(s): {fallas}")
        return 1
    print("RESULTADO: todas las verificaciones pasaron")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
