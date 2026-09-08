-- ---------------------------------------------------------------------------
-- CAPA LIMPIA: imputación puntual y métricas derivadas.
--
-- Aquí se materializa la decisión sobre valores erróneos que pide el punto 3a:
--   - Duplicados, centinelas y fuera de rango  -> DESCARTAR
--   - Hueco aislado de exactamente una hora    -> IMPUTAR (interpolación)
--   - Hora de cobertura parcial                -> CONSERVAR marcada
-- ---------------------------------------------------------------------------

CREATE OR REPLACE TABLE {stg}.measurements_clean AS

WITH sin_duplicados AS (
    SELECT * FROM {stg}.measurements WHERE NOT flag_duplicado
),

vecinos AS (
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
    FROM sin_duplicados s
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
    units_std,
    value_original,
    CASE
        WHEN es_valido   THEN value_std
        WHEN es_imputado THEN (valor_anterior + valor_siguiente) / 2.0
    END                                              AS valor,
    es_imputado,
    fue_convertido,
    coverage_pct,
    flag_cobertura_baja,
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
        avg(valor)    AS media_historica,
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
