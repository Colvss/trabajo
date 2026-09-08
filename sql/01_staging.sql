-- ---------------------------------------------------------------------------
-- CAPA STAGING: tipado, normalización y marcado de problemas.
--
-- Principio de diseño: esta capa NO borra nada. Marca cada fila con banderas
-- booleanas y conserva el valor original junto al normalizado. Así el informe
-- puede reportar cuántos registros afectó cada regla (punto 3) en vez de
-- limitarse a decir que "se limpiaron los datos".
-- ---------------------------------------------------------------------------

CREATE OR REPLACE TABLE {stg}.measurements AS

WITH deduplicado AS (
    -- La zona raw es append-only, así que una reingesta del mismo período
    -- genera filas repetidas. Nos quedamos con la más reciente y marcamos el
    -- resto: el conteo de descartes es un hallazgo del informe.
    SELECT
        m.*,
        row_number() OVER (
            PARTITION BY m.sensor_id, m.datetime_utc
            ORDER BY m.ingested_at DESC
        ) AS version_rank
    FROM {raw}.measurements m
),

tipado AS (
    -- Normalización de formatos (punto 3b): tipos explícitos, texto sin
    -- espacios ni mayúsculas accidentales, timestamps a timestamp real.
    SELECT
        d.sensor_id,
        d.location_id,
        lower(trim(d.parameter))                    AS parameter,
        trim(d.units)                               AS units_original,
        CAST(d.value AS DOUBLE)                     AS value_original,
        CAST(d.datetime_utc AS TIMESTAMP)           AS datetime_utc,
        CAST(d.coverage_pct AS DOUBLE)              AS coverage_pct,
        d.version_rank,
        d.run_id,
        d.ingested_at
    FROM deduplicado d
),

normalizado AS (
    -- Normalización de unidades (punto 3b): los gases llegan en ppm o ppb
    -- según el proveedor. Sin esto, promediar dos estaciones mezcla escalas
    -- que difieren por tres órdenes de magnitud.
    SELECT
        t.*,
        CASE
            WHEN c.factor IS NULL                     THEN t.value_original
            WHEN lower(t.units_original) = 'ppm'      THEN t.value_original * 1000 * c.factor
            WHEN lower(t.units_original) = 'ppb'      THEN t.value_original * c.factor
            ELSE t.value_original
        END AS value_std,
        CASE
            WHEN c.factor IS NOT NULL
                 AND lower(t.units_original) IN ('ppm', 'ppb') THEN TRUE
            ELSE FALSE
        END AS fue_convertido
    FROM tipado t
    LEFT JOIN {stg}.unit_conversion c
           ON c.parameter = t.parameter
)

SELECT
    n.sensor_id,
    n.location_id,
    n.parameter,
    n.datetime_utc,
    n.value_original,
    n.units_original,
    n.value_std,
    'ug/m3'                                          AS units_std,
    n.fue_convertido,
    n.coverage_pct,
    n.run_id,
    n.ingested_at,

    -- ---- Banderas de calidad -------------------------------------------
    n.version_rank > 1                               AS flag_duplicado,
    n.datetime_utc IS NULL                           AS flag_sin_timestamp,
    n.value_original IS NULL                         AS flag_nulo,
    n.value_original IN (SELECT value FROM {stg}.sentinel_values)
                                                     AS flag_centinela,
    (r.min_value IS NOT NULL
     AND n.value_std IS NOT NULL
     AND (n.value_std < r.min_value OR n.value_std > r.max_value))
                                                     AS flag_fuera_rango,
    (n.coverage_pct IS NOT NULL AND n.coverage_pct < {min_coverage})
                                                     AS flag_cobertura_baja,
    n.datetime_utc > current_timestamp + INTERVAL 2 HOUR
                                                     AS flag_futuro,

    -- Una fila es válida si sobrevive a todas las reglas de descarte.
    -- La cobertura baja NO invalida: es una hora parcial pero informativa,
    -- se conserva marcada y se excluye sólo de los agregados estrictos.
    NOT (
        n.version_rank > 1
        OR n.datetime_utc IS NULL
        OR n.value_original IS NULL
        OR n.value_original IN (SELECT value FROM {stg}.sentinel_values)
        OR (r.min_value IS NOT NULL
            AND n.value_std IS NOT NULL
            AND (n.value_std < r.min_value OR n.value_std > r.max_value))
        OR n.datetime_utc > current_timestamp + INTERVAL 2 HOUR
    )                                                AS es_valido

FROM normalizado n
LEFT JOIN {stg}.param_rules r
       ON r.parameter = n.parameter;
