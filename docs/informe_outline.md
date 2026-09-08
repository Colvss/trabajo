# Guía del informe — dónde está la evidencia de cada punto

Mapa entre lo que pide el enunciado y lo que produce el repositorio. La idea es
que ninguna cifra del informe se escriba a mano: todas salen de un artefacto
generado por el pipeline, así el documento nunca queda desactualizado respecto
del código.

## 1. Arquitectura de alto nivel

| Sub-punto | Dónde sale |
|---|---|
| a. Sistemas y servicios involucrados | Tabla "Por qué cada componente" del README |
| b. Dataset utilizado | Sección "Fuente de datos"; volumen real: `SELECT count(*) FROM raw.measurements` |
| c. Diagrama de la solución | Diagrama del README, **redibujado al final** para que coincida con lo implementado |

Para describir las variables (punto 1b), la salida de `python -m src.explore`
imprime el JSON completo de una estación y de una medición: va al anexo.

Preguntas que el dataset permite responder, y que conviene declarar
explícitamente en la introducción:

- ¿Qué estaciones superan con más frecuencia las guías de la OMS?
- ¿Cómo varía la contaminación a lo largo del día?
- ¿Qué tan completa es la red de monitoreo, y dónde tiene huecos?

## 2. Modelo ETL

| Sub-punto | Dónde sale |
|---|---|
| a. Ingesta | `src/ingest.py`. Frecuencia: cron cada 6 h en `.github/workflows/pipeline.yml`. Evidencia: tabla `mart.pipeline_runs` y el historial de Actions |
| b. Transformación | `sql/01_staging.sql` y `sql/02_clean.sql`, al anexo completos |
| c. Carga y verificación | `src/db.py` para el destino; verificación mediante las reglas de integridad de `src/quality.py` |

El argumento de la ingesta incremental — marca de agua por sensor, solape de
una hora, deduplicación en staging — es lo que distingue un pipeline de un
script que baja un CSV. Vale la pena dedicarle un párrafo propio.

## 3. Calidad de datos

**Toda esta sección sale de `docs/dq_report.md`**, que se regenera en cada
corrida con los conteos reales. Pegar esa tabla; no transcribir números.

| Sub-punto | Dónde sale |
|---|---|
| a. Valores erróneos | Filas de dimensión Validez y Exactitud del reporte. La justificación de cada decisión está en la tabla "Decisiones de calidad de datos" del README |
| b. Normalización de formatos | `sql/01_staging.sql`, bloques `tipado` y `normalizado`. Conteo en la regla `unidades_convertidas` |
| c. Métricas derivadas | `sql/02_clean.sql`, tabla `stg.measurements_enriched` |

Las tres métricas derivadas, con su fórmula para el informe:

- **Media móvil de 24 h** — `avg(valor)` sobre una ventana de 23 horas previas
  más la actual. Las guías de la OMS están definidas sobre 24 h, no sobre
  lecturas horarias sueltas, así que sin esta métrica la comparación no es
  válida.
- **Índice OMS** — `valor / límite_24h`. Adimensional, y por lo tanto comparable
  entre contaminantes con umbrales distintos.
- **Z-score por estación** — `(valor − media_estación) / desvío_estación`.
  Permite comparar estaciones con niveles base diferentes, algo que el valor
  absoluto no deja hacer.

## 4. Visualización

`dashboard/app.py`, cuatro pestañas. Para el informe conviene capturar:

- Panorama (mapa + ranking) — figura del estado general de la red
- Series temporales con la línea de la guía OMS — figura de la evolución
- Calidad de datos — figura que respalda el punto 3
- Operación — figura que respalda la automatización del punto 2a

## Estructura sugerida del documento

1. **Portada** e **índice**
2. **Introducción** — problema, preguntas de investigación, alcance
3. **Arquitectura** — punto 1, con el diagrama como Figura 1
4. **Implementación del ETL** — punto 2, con fragmentos de código en el cuerpo y
   los archivos completos en anexos
5. **Calidad de datos** — punto 3, con la tabla generada como Tabla 1
6. **Resultados y visualización** — punto 4, con las capturas del dashboard
7. **Conclusiones** — qué respondieron los datos y qué limitaciones quedaron
8. **Referencias** — documentación de OpenAQ, guías OMS 2021, herramientas
9. **Anexos** — SQL completo, JSON de ejemplo de la API, reporte de calidad

## Guion de la presentación (10 minutos)

| Minutos | Contenido |
|---|---|
| 0–1 | Problema y pregunta que responde el trabajo |
| 1–3 | Arquitectura sobre el diagrama |
| 3–5 | Demo en vivo: disparar el workflow y mostrar la corrida |
| 5–7 | Dashboard, empezando por un hallazgo concreto, no por la herramienta |
| 7–9 | Calidad de datos con las cifras reales del reporte |
| 9–10 | Conclusiones y limitaciones |

Dos consejos para la demo: tené el dashboard **ya abierto** en una pestaña por
si la red falla, y una captura de una corrida exitosa de Actions por si el
disparo en vivo tarda. La pregunta más probable del cierre es "¿qué pasa si la
API se cae?" — la respuesta está en los reintentos de `src/api.py` y en el
registro de estado de `mart.pipeline_runs`.
