"""Sonda de contrato: se corre UNA vez, antes de todo lo demás.

La documentación de OpenAQ es inconsistente sobre algunos nombres de
parámetros (`iso` vs `countries_id`, `datetime_from` vs `date_from`). En vez de
adivinar, este script los prueba contra la API real y te dice cuáles poner en
config.yml.

    python -m src.explore
"""

import json

from .api import get
from .config import CFG

ISO = CFG["source"]["country_iso"]


def _try(label: str, path: str, params: dict):
    """Devuelve el payload si el request funciona, None si la API lo rechaza."""
    try:
        payload = get(path, params)
        n = len(payload.get("results", []))
        print(f"  [OK]    {label}  ->  {n} resultados")
        return payload
    except RuntimeError as exc:
        first_line = str(exc).splitlines()[0]
        print(f"  [FALLA] {label}  ->  {first_line}")
        return None


def probe_country_param() -> str | None:
    print(f"\n1. Filtro de país (buscando estaciones en {ISO})")
    for candidate in ("iso", "countries_id", "country"):
        params = {"limit": 5, candidate: ISO}
        if _try(f"/locations?{candidate}={ISO}", "locations", params):
            print(f"\n  --> poné  country_param: {candidate}  en config.yml")
            return candidate
    print("\n  Ningún filtro funcionó. Revisá el código ISO o probá sin filtro.")
    return None


def probe_datetime_params(sensor_id: int) -> tuple[str, str] | None:
    print(f"\n4. Rango temporal (sensor {sensor_id})")
    pairs = [("datetime_from", "datetime_to"), ("date_from", "date_to")]
    for p_from, p_to in pairs:
        params = {"limit": 3, p_from: "2026-01-01T00:00:00Z"}
        if _try(f"/sensors/{sensor_id}/hours?{p_from}=...", f"sensors/{sensor_id}/hours", params):
            print(f"\n  --> poné  datetime_from_param: {p_from}")
            print(f"            datetime_to_param: {p_to}  en config.yml")
            return p_from, p_to
    return None


def main() -> None:
    print("=" * 70)
    print("SONDA DE CONTRATO - OpenAQ v3")
    print("=" * 70)

    country_param = probe_country_param()
    if not country_param:
        return

    print("\n2. Estaciones encontradas")
    payload = get("locations", {"limit": 10, country_param: ISO})
    locations = payload.get("results", [])
    for loc in locations[:10]:
        sensores = ", ".join(s["parameter"]["name"] for s in loc.get("sensors", []))
        print(f"  #{loc['id']:>7}  {loc.get('name', '?')[:34]:<34} [{sensores}]")

    if not locations:
        print("  Sin estaciones. Probá otro país en config.yml.")
        return

    print("\n3. Estructura de una estación (para el anexo del informe)")
    print(json.dumps(locations[0], indent=2, ensure_ascii=False)[:1500])

    sensor = next(
        (s for loc in locations for s in loc.get("sensors", [])
         if s["parameter"]["name"] in CFG["source"]["parameters"]),
        None,
    )
    if not sensor:
        print("\n  Ninguna estación mide los parámetros configurados.")
        return

    dt_params = probe_datetime_params(sensor["id"])

    print("\n5. Estructura de una medición horaria")
    payload = get(f"sensors/{sensor['id']}/hours", {"limit": 2})
    results = payload.get("results", [])
    if results:
        print(json.dumps(results[0], indent=2, ensure_ascii=False))
    else:
        print("  El sensor no devolvió mediciones (puede estar inactivo).")

    print("\n" + "=" * 70)
    print("RESUMEN PARA config.yml")
    print(f"  country_param:        {country_param}")
    if dt_params:
        print(f"  datetime_from_param:  {dt_params[0]}")
        print(f"  datetime_to_param:    {dt_params[1]}")
    print("=" * 70)


if __name__ == "__main__":
    main()
