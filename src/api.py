"""Cliente HTTP de OpenAQ v3.

Contrato verificado en https://docs.openaq.org:
  - Autenticación por header X-API-Key.
  - Límite de 60 req/min y 2.000 req/hora por key.
  - Respuestas con forma {"meta": {...}, "results": [...]}.

El limitador de tasa no es opcional: descubrir sensores y luego pedir sus
mediciones genera decenas de requests por corrida.
"""

import time

import requests

from .config import CFG, require_api_key

BASE_URL = CFG["source"]["base_url"].rstrip("/")
_RPM = CFG["source"]["requests_per_minute"]
_MIN_INTERVAL = 60.0 / _RPM

# Contador global de requests: se reporta en mart.pipeline_runs.
REQUEST_COUNT = 0

_session = None
_last_call = 0.0


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(
            {"X-API-Key": require_api_key(), "Accept": "application/json"}
        )
    return _session


def get(path: str, params: dict | None = None, max_retries: int = 4) -> dict:
    """GET con throttling, reintentos y errores legibles."""
    global _last_call, REQUEST_COUNT

    url = f"{BASE_URL}/{path.lstrip('/')}"
    session = _get_session()

    for attempt in range(max_retries):
        elapsed = time.monotonic() - _last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

        resp = session.get(url, params=params or {}, timeout=60)
        _last_call = time.monotonic()
        REQUEST_COUNT += 1

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            # La API informa cuándo se reinicia la ventana; si no, backoff.
            wait = float(resp.headers.get("x-ratelimit-reset", 2 ** (attempt + 3)))
            print(f"  rate limit alcanzado, esperando {wait:.0f}s")
            time.sleep(min(wait, 120))
            continue

        if resp.status_code >= 500:
            wait = 2 ** (attempt + 1)
            print(f"  error {resp.status_code} del servidor, reintento en {wait}s")
            time.sleep(wait)
            continue

        # 4xx que no es rate limit: casi siempre un parámetro mal nombrado.
        # Mostramos el cuerpo porque ahí viene el detalle de la validación.
        raise RuntimeError(
            f"OpenAQ respondió {resp.status_code} a {resp.url}\n{resp.text[:800]}"
        )

    raise RuntimeError(f"Se agotaron los reintentos para {url}")


def paginate(path: str, params: dict, max_pages: int = 50):
    """Itera resultados siguiendo la paginación de `meta`."""
    params = dict(params)
    page = 1
    while page <= max_pages:
        params["page"] = page
        payload = get(path, params)
        results = payload.get("results", [])
        if not results:
            return
        for item in results:
            yield item

        meta = payload.get("meta", {})
        limit = meta.get("limit") or params.get("limit", len(results))
        found = meta.get("found")
        # `found` puede venir como ">1000" cuando el conteo es aproximado.
        if isinstance(found, int) and page * limit >= found:
            return
        if len(results) < limit:
            return
        page += 1
