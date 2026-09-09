-- ---------------------------------------------------------------------------
-- CAPA LIMPIA: malla horaria, imputación puntual y métricas derivadas.
--
-- Aquí se materializa la decisión sobre valores erróneos que pide el punto 3a:
--   - Duplicados, centinelas y fuera de rango    -> DESCARTAR
--   - Hora ausente aislada, con vecinos válidos  -> IMPUTAR (interpolación)
--   - Hueco de más de una hora                   -> DEJAR VACÍO
--   - Hora de cobertura parcial                  -> CONSERVAR marcada
--
-- La malla horaria es lo que hace posible la primera decisión. La API sólo
-- devuelve las horas que midió: un corte de energía de tres horas no llega
-- como tres filas malas, llega como tres filas que no existen. Sin generar la
-- serie horaria esperada, esos huecos son invisibles y no se pueden contar ni
-- reportar. Con 2.650 horas ausentes sobre 58.179 esperadas en la primera
-- carga, esta es la dimensión de calidad dominante en esta fuente.
-- ---------------------------------------------------------------------------

-- Serie horaria esperada por sensor, de su primera a su última medición.
CREATE OR REPLACE TABLE {stg}.hourly_spine AS
WITH rango AS (
    SELECT
        sensor_id,
        any_value(location_id)  AS location_id,
        any_value(parameter)    AS parameter,
        min(datetime_utc)       AS desde,
        max(datetime_utc)       AS hasta
    FROM {stg}.measurements
    WHERE NOT flag_duplicado
      AND datetime_utc IS NOT NULL
    GROUP BY sensor_id
)
SELECT
    r.sensor_id,
    r.location_id,
    r.parameter,
    unnest(generate_series(r.desde, r.hasta, INTERVAL 1 HOUR)) AS datetime_utc
FROM rango r;


-- Serie completa: cada hora esperada, tenga o no medición.
CREATE OR REPLACE TABLE {stg}.measurements_spined AS
SELECT
    e.sensor_id,
    e.location_id,
    e.parameter,
    e.datetime_utc,
    m.value_original,
    m.value_std,
    m.units_original,
    m.fue_convertido,
    m.coverage_pct,
    m.flag_cobertura_baja,
    m.run_id,
    m.sensor_id IS NULL                              AS flag_hora_ausente,
    coalesce(m.es_valido, FALSE)                     AS es_valido
FROM {stg}.hourly_spine e
LEFT JOIN {stg}.measurements m
       ON m.sensor_id    = e.sensor_id
      AND m.datetime_utc = e.datetime_utc
      AND NOT m.flag_duplicado;


CREATE OR REPLACE TABLE {stg}.measurements_clean AS

WITH vecinos AS (
    -- Último y próximo valor VÁLIDO de la serie del sensor, ignorando los
    -- huecos. IGNORE NULLS hace que la ventana salte las filas inválidas.
    SELECT
        s.*,
        last_value(CASE WHEN es_valido THEN value_std END IGNORE NULLS) OVER (
            PARTITION BY sensor_id ORDER BY datetime_utc
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS valor_anterior,
        last_value(CASE WHEN es_valido THEN datetime_utc END IGNORE NULLS) OVER (
            PARTITION BY sensor_id ORDER BY datetime_utc
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS ts_anterior,
        first_value(CASE WHEN es_valido THEN value_std END IGNORE NULLS) OVER (
            PARTITION BY sensor_id ORDER BY datetime_utc
            ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING
        ) AS valor_siguiente,
        first_value(CASE WHEN es_valido THEN datetime_utc END IGNORE NULLS) OVER (
            PARTITION BY sensor_id ORDER BY datetime_utc
            ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING
        ) AS ts_siguiente
    FROM {stg}.measurements_spined s
),

imputado AS (
    SELECT
        v.*,
        -- Sólo se imputa el hueco de UNA hora rodeado de datos buenos.
        -- Rellenar huecos largos inventaría una serie que nadie midió.
        (NOT v.es_valido
         AND v.valor_anterior  IS NOT NULL
         AND v.valor_siguiente IS NOT NULL
         AND v.ts_anterior  = v.datetime_utc - INTERVAL 1 HOUR
         AND v.ts_siguiente = v.datetime_utc + INTERVAL 1 HOUR
        ) AS es_imputado
    FROM vecinos v
)

SELECT
    sensor_id,
    location_id,
    parameter,
    datetime_utc,
    CAST(datetime_utc AS DATE)                       AS fecha,
    extract('hour' FROM datetime_utc)                AS hora_utc,
    'ug/m3'                                          AS units_std,
    value_original,
    CASE
        WHEN es_valido   THEN value_std
        WHEN es_imputado THEN (valor_anterior + valor_siguiente) / 2.0
    END                                              AS valor,
    es_imputado,
    flag_hora_ausente,
    coalesce(fue_convertido, FALSE)                  AS fue_convertido,
    coverage_pct,
    coalesce(flag_cobertura_baja, FALSE)             AS flag_cobertura_baja,
    run_id
FROM imputado
WHERE es_valido OR es_imputado;


-- ---------------------------------------------------------------------------
-- MÉTRICAS DERIVADAS (punto 3c)
--
--   media_movil_24h : media móvil de 24 horas. Es la base de comparación con
--                     las guías de la OMS, que están definidas sobre 24 h y no
--                     sobre lecturas horarias sueltas.
--   z_score         : desvíos estándar respecto de la media histórica de ESA
--                     estación y ESE contaminante. Permite comparar estaciones
--                     con niveles base distintos.
--   indice_oms      : valor dividido por la guía OMS. >1 significa excedencia,
--                     y es comparable entre contaminantes distintos.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE TABLE {stg}.measurements_enriched AS

WITH estadisticas_base AS (
    SELECT
        sensor_id,
        avg(valor)         AS media_historica,
        stddev_samp(valor) AS desvio_historico
    FROM {stg}.measurements_clean
    GROUP BY sensor_id
)

SELECT
    c.*,
    avg(c.valor) OVER (
        PARTITION BY c.sensor_id ORDER BY c.datetime_utc
        RANGE BETWEEN INTERVAL 23 HOURS PRECEDING AND CURRENT ROW
    )                                                AS media_movil_24h,

    count(*) OVER (
        PARTITION BY c.sensor_id ORDER BY c.datetime_utc
        RANGE BETWEEN INTERVAL 23 HOURS PRECEDING AND CURRENT ROW
    )                                                AS horas_en_ventana,

    CASE
        WHEN b.desvio_historico IS NULL OR b.desvio_historico = 0 THEN NULL
        ELSE (c.valor - b.media_historica) / b.desvio_historico
    END                                              AS z_score,

    CASE
        WHEN g.limite_24h IS NULL OR g.limite_24h = 0 THEN NULL
        ELSE c.valor / g.limite_24h
    END                                              AS indice_oms

FROM {stg}.measurements_clean c
LEFT JOIN estadisticas_base b ON b.sensor_id = c.sensor_id
LEFT JOIN {stg}.who_guidelines g ON g.parameter = c.parameter;
