-- ---------------------------------------------------------------------------
-- CAPA MART: tablas de consumo del dashboard.
--
-- El dashboard nunca toca raw ni stg. Lee sólo de aquí, que es lo que hace que
-- "repositorio analítico consultable" (punto 2c) signifique algo concreto.
-- ---------------------------------------------------------------------------

-- Serie diaria por estación y contaminante -----------------------------------
CREATE OR REPLACE TABLE {mart}.daily_air_quality AS
SELECT
    e.location_id,
    l.name                                           AS estacion,
    l.locality                                       AS comuna,
    l.latitude,
    l.longitude,
    e.parameter                                      AS contaminante,
    e.fecha,
    round(avg(e.valor), 2)                           AS valor_promedio,
    round(min(e.valor), 2)                           AS valor_minimo,
    round(max(e.valor), 2)                           AS valor_maximo,
    count(*)                                         AS horas_con_dato,
    -- Completitud diaria: cuántas de las 24 horas tuvieron medición válida.
    round(100.0 * count(*) / 24, 1)                  AS completitud_pct,
    sum(CASE WHEN e.es_imputado THEN 1 ELSE 0 END)   AS horas_imputadas,
    round(avg(e.indice_oms), 3)                      AS indice_oms_promedio,
    -- Una excedencia sólo se declara con al menos 18 de 24 horas medidas (75%),
    -- que es el criterio habitual en normativa de calidad del aire.
    CASE
        WHEN count(*) >= 18 AND avg(e.valor) > max(g.limite_24h) THEN TRUE
        WHEN count(*) >= 18                                      THEN FALSE
        ELSE NULL
    END                                              AS excede_guia_oms
FROM {stg}.measurements_enriched e
LEFT JOIN {raw}.locations l      ON l.location_id = e.location_id
LEFT JOIN {stg}.who_guidelines g ON g.parameter   = e.parameter
GROUP BY ALL;


-- Estado actual de cada estación (tarjetas y mapa del dashboard) -------------
CREATE OR REPLACE TABLE {mart}.station_summary AS
WITH ultima AS (
    SELECT
        location_id,
        parameter,
        valor,
        datetime_utc,
        row_number() OVER (
            PARTITION BY location_id, parameter ORDER BY datetime_utc DESC
        ) AS rn
    FROM {stg}.measurements_enriched
)
SELECT
    l.location_id,
    l.name              AS estacion,
    l.locality          AS comuna,
    l.provider          AS proveedor,
    l.latitude,
    l.longitude,
    u.parameter         AS contaminante,
    round(u.valor, 2)   AS ultimo_valor,
    u.datetime_utc      AS ultima_medicion_utc,
    date_diff('hour', u.datetime_utc, current_timestamp) AS horas_desde_ultima
FROM ultima u
JOIN {raw}.locations l ON l.location_id = u.location_id
WHERE u.rn = 1;


-- Perfil horario promedio: el patrón diario de contaminación -----------------
CREATE OR REPLACE TABLE {mart}.hourly_profile AS
SELECT
    e.parameter                     AS contaminante,
    l.locality                      AS comuna,
    e.hora_utc,
    round(avg(e.valor), 2)          AS valor_promedio,
    count(*)                        AS n_observaciones
FROM {stg}.measurements_enriched e
LEFT JOIN {raw}.locations l ON l.location_id = e.location_id
GROUP BY ALL;


-- Ranking mensual de excedencias --------------------------------------------
CREATE OR REPLACE TABLE {mart}.monthly_exceedance AS
SELECT
    estacion,
    comuna,
    contaminante,
    date_trunc('month', fecha)                       AS mes,
    count(*)                                         AS dias_medidos,
    sum(CASE WHEN excede_guia_oms THEN 1 ELSE 0 END) AS dias_excedidos,
    round(
        100.0 * sum(CASE WHEN excede_guia_oms THEN 1 ELSE 0 END)
        / nullif(count(*), 0), 1
    )                                                AS pct_dias_excedidos,
    round(avg(valor_promedio), 2)                    AS valor_promedio_mes
FROM {mart}.daily_air_quality
WHERE excede_guia_oms IS NOT NULL
GROUP BY ALL;
