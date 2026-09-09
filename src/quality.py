"""Control de calidad de datos con conteos (punto 3 del enunciado).

El enunciado pide, literalmente, "el criterio aplicado y cuántos registros se
vieron afectados". Este módulo produce exactamente eso: una fila por regla, con
el conteo, el porcentaje y la acción tomada.

Escribe tres salidas:
  - mart.dq_report      -> histórico consultable desde el dashboard
  - docs/dq_report.json -> artefacto versionable, se sube desde CI
  - docs/dq_report.md   -> tabla lista para pegar en el informe
"""

import datetime as dt
import json
from pathlib import Path

from .config import CFG, SCHEMA_MART, SCHEMA_RAW, SCHEMA_STG

DOCS = Path(__file__).resolve().parent.parent / "docs"

FRESHNESS_H = CFG["quality"]["freshness_hours"]
MIN_COV = CFG["quality"]["min_coverage_pct"]


def _checks() -> list[dict]:
    """Definición declarativa de las reglas.

    `afectados` y `total` son consultas que devuelven un único número.
    `max_pct` es el umbral por encima del cual la regla se considera fallada;
    None significa que la regla es informativa y nunca falla.
    """
    stg, mart, raw = SCHEMA_STG, SCHEMA_MART, SCHEMA_RAW
    universo = f"SELECT count(*) FROM {stg}.measurements"

    return [
        {
            "dimension": "Unicidad",
            "check_name": "registros_duplicados",
            "rule": "Misma combinación (sensor_id, datetime_utc) ingerida más de una vez",
            "afectados": f"SELECT count(*) FROM {stg}.measurements WHERE flag_duplicado",
            "total": universo,
            "action_taken": "Descartar: se conserva la versión más reciente",
            "max_pct": None,
        },
        {
            "dimension": "Completitud",
            "check_name": "valores_nulos",
            "rule": "value viene NULL desde la API",
            "afectados": f"SELECT count(*) FROM {stg}.measurements WHERE flag_nulo",
            "total": universo,
            "action_taken": "Descartar, salvo hueco de 1 hora que se imputa",
            "max_pct": 20.0,
        },
        {
            "dimension": "Validez",
            "check_name": "valores_centinela",
            "rule": f"value en {CFG['quality']['sentinel_values']} (códigos de 'sin dato')",
            "afectados": f"SELECT count(*) FROM {stg}.measurements WHERE flag_centinela",
            "total": universo,
            "action_taken": "Descartar: son huecos, no mediciones",
            "max_pct": 15.0,
        },
        {
            "dimension": "Exactitud",
            "check_name": "fuera_de_rango_fisico",
            "rule": "Valor normalizado fuera del rango plausible de config.yml",
            "afectados": f"SELECT count(*) FROM {stg}.measurements WHERE flag_fuera_rango",
            "total": universo,
            "action_taken": "Descartar: error de sensor, no evento extremo",
            "max_pct": 5.0,
        },
        {
            "dimension": "Validez",
            "check_name": "timestamp_futuro",
            "rule": "datetime_utc posterior a la hora actual + 2 h",
            "afectados": f"SELECT count(*) FROM {stg}.measurements WHERE flag_futuro",
            "total": universo,
            "action_taken": "Descartar: error de zona horaria en origen",
            "max_pct": 0.0,
        },
        {
            "dimension": "Completitud",
            "check_name": "hora_cobertura_parcial",
            "rule": f"coverage.percentComplete < {MIN_COV}% en la hora reportada",
            "afectados": f"SELECT count(*) FROM {stg}.measurements WHERE flag_cobertura_baja",
            "total": universo,
            "action_taken": "Conservar marcado: hora parcial pero informativa",
            "max_pct": None,
        },
        {
            "dimension": "Completitud",
            "check_name": "horas_ausentes",
            "rule": "Hora esperada en la serie del sensor que la fuente nunca reportó",
            "afectados": f"SELECT count(*) FROM {stg}.measurements_spined WHERE flag_hora_ausente",
            "total": f"SELECT count(*) FROM {stg}.measurements_spined",
            "action_taken": "Imputar si el hueco es de 1 h; dejar vacío si es mayor",
            "max_pct": 15.0,
        },
        {
            "dimension": "Completitud",
            "check_name": "registros_imputados",
            "rule": "Hueco aislado de 1 hora entre dos valores válidos",
            "afectados": f"SELECT count(*) FROM {stg}.measurements_clean WHERE es_imputado",
            "total": f"SELECT count(*) FROM {stg}.measurements_clean",
            "action_taken": "Imputar por interpolación lineal entre vecinos",
            "max_pct": 10.0,
        },
        {
            "dimension": "Integridad",
            "check_name": "sensores_sin_datos",
            "rule": "Sensor listado por la API que no devolvió ninguna medición",
            "afectados": f"""
                SELECT count(*) FROM {raw}.sensors s
                LEFT JOIN (SELECT DISTINCT sensor_id FROM {raw}.measurements) m
                       ON m.sensor_id = s.sensor_id
                WHERE m.sensor_id IS NULL
            """,
            "total": f"SELECT count(*) FROM {raw}.sensors",
            "action_taken": "Excluir del análisis y documentar el contaminante afectado",
            "max_pct": None,
        },
        {
            "dimension": "Exactitud",
            "check_name": "valores_atipicos",
            "rule": "|z-score| > 5 respecto de la media histórica del propio sensor",
            "afectados": f"""
                SELECT count(*) FROM {stg}.measurements_enriched
                WHERE abs(z_score) > 5
            """,
            "total": f"SELECT count(*) FROM {stg}.measurements_enriched",
            "action_taken": "Conservar: pueden ser episodios reales de contaminación",
            "max_pct": None,
        },
        {
            "dimension": "Consistencia",
            "check_name": "unidades_convertidas",
            "rule": "Gases reportados en ppm/ppb convertidos a ug/m3",
            "afectados": f"SELECT count(*) FROM {stg}.measurements WHERE fue_convertido",
            "total": universo,
            "action_taken": "Normalizar: toda la capa mart queda en ug/m3",
            "max_pct": None,
        },
        {
            "dimension": "Consistencia",
            "check_name": "unidades_distintas_por_parametro",
            "rule": "Un mismo contaminante llega con más de una unidad de origen",
            "afectados": f"""
                SELECT count(*) FROM (
                    SELECT parameter FROM {stg}.measurements
                    GROUP BY parameter HAVING count(DISTINCT units_original) > 1
                )
            """,
            "total": f"SELECT count(DISTINCT parameter) FROM {stg}.measurements",
            "action_taken": "Normalizar antes de agregar",
            "max_pct": None,
        },
        {
            "dimension": "Integridad",
            "check_name": "mediciones_sin_estacion",
            "rule": "location_id de la medición no existe en raw.locations",
            "afectados": f"""
                SELECT count(*) FROM {stg}.measurements m
                LEFT JOIN {raw}.locations l ON l.location_id = m.location_id
                WHERE l.location_id IS NULL
            """,
            "total": universo,
            "action_taken": "Investigar: rompería el join del dashboard",
            "max_pct": 0.0,
        },
        {
            "dimension": "Integridad",
            "check_name": "estaciones_sin_coordenadas",
            "rule": "latitude o longitude nulas",
            "afectados": f"""
                SELECT count(*) FROM {raw}.locations
                WHERE latitude IS NULL OR longitude IS NULL
            """,
            "total": f"SELECT count(*) FROM {raw}.locations",
            "action_taken": "Excluir del mapa, conservar en las series",
            "max_pct": 10.0,
        },
        {
            "dimension": "Completitud",
            "check_name": "dias_incompletos",
            "rule": "Día-estación con menos de 18 de 24 horas medidas",
            "afectados": f"""
                SELECT count(*) FROM {mart}.daily_air_quality WHERE horas_con_dato < 18
            """,
            "total": f"SELECT count(*) FROM {mart}.daily_air_quality",
            "action_taken": "Conservar, pero no se declara excedencia OMS",
            "max_pct": 40.0,
        },
        {
            "dimension": "Oportunidad",
            "check_name": "frescura_del_dato",
            "rule": f"La medición más reciente tiene más de {FRESHNESS_H} h",
            "afectados": f"""
                SELECT CASE
                    WHEN max(datetime_utc) IS NULL THEN 1
                    WHEN date_diff('hour', max(datetime_utc), current_timestamp)
                         > {FRESHNESS_H} THEN 1
                    ELSE 0 END
                FROM {stg}.measurements_clean
            """,
            "total": "SELECT 1",
            "action_taken": "Alertar: puede indicar ingesta caída",
            "max_pct": 0.0,
        },
    ]


def run(con, run_id: str, write_artifacts: bool = True) -> list[dict]:
    """`write_artifacts=False` evita que una corrida de prueba pise los
    archivos de docs/ con cifras que no vienen de datos reales."""
    print("\n[3/3] CALIDAD DE DATOS")
    checked_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    results = []

    for check in _checks():
        afectados = con.execute(check["afectados"]).fetchone()[0] or 0
        total = con.execute(check["total"]).fetchone()[0] or 0
        pct = round(100.0 * afectados / total, 3) if total else 0.0
        passed = True if check["max_pct"] is None else pct <= check["max_pct"]

        results.append(
            {
                "run_id": run_id,
                "checked_at": checked_at,
                "dimension": check["dimension"],
                "check_name": check["check_name"],
                "rule": check["rule"].strip(),
                "records_affected": int(afectados),
                "records_total": int(total),
                "pct_affected": pct,
                "action_taken": check["action_taken"],
                "passed": bool(passed),
            }
        )

        marca = "ok  " if passed else "FALLA"
        print(
            f"  [{marca}] {check['check_name']:<34} "
            f"{afectados:>8} / {total:<8} ({pct:>6.2f}%)"
        )

    # Reprocesar la calidad de una corrida ya evaluada debe reemplazar su
    # resultado, no acumularlo: si no, el dashboard muestra cada regla repetida.
    con.execute(f"DELETE FROM {SCHEMA_MART}.dq_report WHERE run_id = ?", [run_id])

    con.executemany(
        f"INSERT INTO {SCHEMA_MART}.dq_report VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r["run_id"], r["checked_at"], r["dimension"], r["check_name"],
                r["rule"], r["records_affected"], r["records_total"],
                r["pct_affected"], r["action_taken"], r["passed"],
            )
            for r in results
        ],
    )

    if write_artifacts:
        _write_artifacts(results, run_id, checked_at)

    fallas = [r for r in results if not r["passed"]]
    if fallas:
        print(f"\n  {len(fallas)} regla(s) fuera de umbral: " +
              ", ".join(r["check_name"] for r in fallas))
    return results


def _write_artifacts(results: list[dict], run_id: str, checked_at) -> None:
    DOCS.mkdir(exist_ok=True)

    serializable = [{**r, "checked_at": r["checked_at"].isoformat()} for r in results]
    (DOCS / "dq_report.json").write_text(
        json.dumps(
            {"run_id": run_id, "checked_at": checked_at.isoformat(), "checks": serializable},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Tabla lista para pegar en el informe: las cifras nunca se copian a mano.
    lineas = [
        f"<!-- Generado automáticamente el {checked_at:%Y-%m-%d %H:%M} UTC "
        f"(corrida {run_id}). No editar a mano. -->",
        "",
        "| Dimensión | Regla aplicada | Registros afectados | % | Acción tomada |",
        "|---|---|---:|---:|---|",
    ]
    # Las barras dentro del texto de una regla (por ejemplo |z-score|) partirían
    # la fila en columnas extra al renderizar el Markdown.
    def esc(texto) -> str:
        return str(texto).replace("|", "\\|")

    for r in results:
        lineas.append(
            f"| {esc(r['dimension'])} | {esc(r['rule'])} | {r['records_affected']:,} "
            f"| {r['pct_affected']:.2f}% | {esc(r['action_taken'])} |"
        )
    (DOCS / "dq_report.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(f"  artefactos escritos en docs/dq_report.json y docs/dq_report.md")
