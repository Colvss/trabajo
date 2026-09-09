"""Ingesta incremental desde OpenAQ hacia la zona raw.

Estrategia (punto 2a del enunciado):
  - Descubrimiento: /locations devuelve estaciones con su lista de sensores.
  - Extracción: /sensors/{id}/hours devuelve promedios horarios ya agregados.
  - Incremental: por cada sensor se pide sólo desde su última hora almacenada
    (marca de agua). La primera corrida hace backfill de N días.
  - Idempotencia: raw es append-only y la deduplicación ocurre en staging, de
    forma que reejecutar el pipeline nunca corrompe la base y los duplicados
    quedan contabilizados como hallazgo de calidad.
"""

import datetime as dt
import json

import pandas as pd

from . import api
from .config import CFG, SCHEMA_RAW

SRC = CFG["source"]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _dig(record: dict, *paths, default=None):
    """Busca el primer camino que exista. La API cambió de forma entre
    versiones menores; esto evita que un rename rompa la ingesta."""
    for path in paths:
        cursor = record
        for key in path.split("."):
            if isinstance(cursor, dict) and key in cursor:
                cursor = cursor[key]
            else:
                cursor = None
                break
        if cursor is not None:
            return cursor
    return default


def fetch_locations() -> list[dict]:
    """Estaciones del país configurado, con sus sensores."""
    params = {
        "limit": 1000,
        SRC["country_param"]: SRC["country_iso"],
    }
    locations = list(api.paginate("locations", params, max_pages=5))
    print(f"  {len(locations)} estaciones devueltas por la API")
    return locations


def store_locations(con, locations: list[dict]) -> None:
    rows = []
    for loc in locations:
        coords = loc.get("coordinates") or {}
        rows.append(
            {
                "location_id": loc.get("id"),
                "name": loc.get("name"),
                "locality": loc.get("locality"),
                "country_code": _dig(loc, "country.code", default=SRC["country_iso"]),
                "timezone": loc.get("timezone"),
                "latitude": coords.get("latitude"),
                "longitude": coords.get("longitude"),
                "is_mobile": loc.get("isMobile"),
                "is_monitor": loc.get("isMonitor"),
                "provider": _dig(loc, "provider.name"),
                "payload": json.dumps(loc, ensure_ascii=False),
                "ingested_at": _utc_now(),
            }
        )
    if not rows:
        return
    df = pd.DataFrame(rows)
    con.register("df_locations", df)
    # Reemplaza la fila existente: los metadatos de estación cambian
    # (renombres, recalibraciones) y siempre queremos la versión vigente.
    con.execute(
        f"DELETE FROM {SCHEMA_RAW}.locations "
        f"WHERE location_id IN (SELECT location_id FROM df_locations)"
    )
    con.execute(
        f"INSERT INTO {SCHEMA_RAW}.locations SELECT location_id, name, locality, "
        f"country_code, timezone, latitude, longitude, is_mobile, is_monitor, "
        f"provider, payload::JSON, ingested_at FROM df_locations"
    )
    con.unregister("df_locations")


def select_sensors(con, locations: list[dict]) -> list[dict]:
    """Sensores que miden los parámetros configurados.

    Se priorizan las estaciones fijas (isMonitor) con más sensores útiles: son
    las de referencia oficial y las que tienen series más largas.
    """
    wanted = set(SRC["parameters"])
    candidates = []
    for loc in locations:
        sensors = [
            {
                "sensor_id": s.get("id"),
                "location_id": loc.get("id"),
                "location_name": loc.get("name"),
                "parameter": _dig(s, "parameter.name"),
                "units": _dig(s, "parameter.units"),
            }
            for s in loc.get("sensors", [])
            if _dig(s, "parameter.name") in wanted
        ]
        if sensors:
            candidates.append((loc.get("isMonitor") is True, len(sensors), sensors))

    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    selected: list[dict] = []
    for _, _, sensors in candidates[: SRC["max_locations"]]:
        selected.extend(sensors)

    if selected:
        df = pd.DataFrame(
            [{**s, "ingested_at": _utc_now()} for s in selected]
        )[["sensor_id", "location_id", "parameter", "units", "ingested_at"]]
        con.register("df_sensors", df)
        con.execute(
            f"DELETE FROM {SCHEMA_RAW}.sensors "
            f"WHERE sensor_id IN (SELECT sensor_id FROM df_sensors)"
        )
        con.execute(f"INSERT INTO {SCHEMA_RAW}.sensors SELECT * FROM df_sensors")
        con.unregister("df_sensors")

    print(
        f"  {len(selected)} sensores seleccionados "
        f"en hasta {SRC['max_locations']} estaciones"
    )
    return selected


def watermark(con, sensor_id: int) -> dt.datetime | None:
    row = con.execute(
        f"SELECT max(datetime_utc) FROM {SCHEMA_RAW}.measurements WHERE sensor_id = ?",
        [sensor_id],
    ).fetchone()
    return row[0] if row and row[0] else None


def parse_measurement(record: dict, sensor: dict) -> dict | None:
    """Aplana una medición horaria. Devuelve None si no hay timestamp usable."""
    ts_raw = _dig(
        record,
        "period.datetimeFrom.utc",
        "period.datetimeTo.utc",
        "datetime.utc",
        "date.utc",
    )
    if not ts_raw:
        return None
    try:
        ts = pd.to_datetime(ts_raw, utc=True).tz_localize(None).to_pydatetime()
    except (ValueError, TypeError):
        return None

    return {
        "sensor_id": sensor["sensor_id"],
        "location_id": sensor["location_id"],
        "parameter": _dig(record, "parameter.name", default=sensor["parameter"]),
        "units": _dig(record, "parameter.units", default=sensor["units"]),
        "value": record.get("value"),
        "datetime_utc": ts,
        "datetime_local": _dig(
            record, "period.datetimeFrom.local", "datetime.local", default=None
        ),
        "coverage_pct": _dig(record, "coverage.percentComplete"),
        "payload": json.dumps(record, ensure_ascii=False),
    }


def ingest_sensor(con, sensor: dict, run_id: str) -> int:
    since = watermark(con, sensor["sensor_id"])
    if since is None:
        since = _utc_now() - dt.timedelta(days=SRC["backfill_days"])
        mode = f"backfill {SRC['backfill_days']}d"
    else:
        # Un pequeño solape reingesta la última hora: la API a veces corrige
        # valores recientes. La deduplicación en staging se queda con el último.
        since = since - dt.timedelta(hours=1)
        mode = f"incremental desde {since:%Y-%m-%d %H:%M}"

    params = {
        "limit": SRC["page_limit"],
        SRC["datetime_from_param"]: since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        SRC["datetime_to_param"]: _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    rows, skipped = [], 0
    for record in api.paginate(f"sensors/{sensor['sensor_id']}/hours", params):
        parsed = parse_measurement(record, sensor)
        if parsed is None:
            skipped += 1
            continue
        rows.append(parsed)

    note = f" ({skipped} sin timestamp)" if skipped else ""
    print(
        f"  sensor {sensor['sensor_id']:>8} {sensor['parameter']:<5} "
        f"{len(rows):>5} filas  [{mode}]{note}"
    )

    if not rows:
        return 0

    df = pd.DataFrame(rows)
    df["source_url"] = f"{api.BASE_URL}/sensors/{sensor['sensor_id']}/hours"
    df["ingested_at"] = _utc_now()
    df["run_id"] = run_id

    con.register("df_meas", df)
    con.execute(
        f"""
        INSERT INTO {SCHEMA_RAW}.measurements
        SELECT sensor_id, location_id, parameter, units, value, datetime_utc,
               datetime_local, coverage_pct, payload::JSON, source_url,
               ingested_at, run_id
        FROM df_meas
        """
    )
    con.unregister("df_meas")
    return len(rows)


def run(con, run_id: str) -> int:
    print("\n[1/3] INGESTA")
    locations = fetch_locations()
    store_locations(con, locations)
    sensors = select_sensors(con, locations)

    total = 0
    for sensor in sensors:
        total += ingest_sensor(con, sensor, run_id)

    print(f"  total ingerido: {total} filas")
    return total
