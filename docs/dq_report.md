<!-- Generado automáticamente el 2026-09-09 20:56 UTC (corrida reproceso). No editar a mano. -->

| Dimensión | Regla aplicada | Registros afectados | % | Acción tomada |
|---|---|---:|---:|---|
| Unicidad | Misma combinación (sensor_id, datetime_utc) ingerida más de una vez | 0 | 0.00% | Descartar: se conserva la versión más reciente |
| Completitud | value viene NULL desde la API | 0 | 0.00% | Descartar, salvo hueco de 1 hora que se imputa |
| Validez | value en [-999, -9999, 999, 999999] (códigos de 'sin dato') | 0 | 0.00% | Descartar: son huecos, no mediciones |
| Exactitud | Valor normalizado fuera del rango plausible de config.yml | 0 | 0.00% | Descartar: error de sensor, no evento extremo |
| Validez | datetime_utc posterior a la hora actual + 2 h | 0 | 0.00% | Descartar: error de zona horaria en origen |
| Completitud | coverage.percentComplete < 50% en la hora reportada | 0 | 0.00% | Conservar marcado: hora parcial pero informativa |
| Completitud | Hora esperada en la serie del sensor que la fuente nunca reportó | 2,650 | 4.55% | Imputar si el hueco es de 1 h; dejar vacío si es mayor |
| Completitud | Hueco aislado de 1 hora entre dos valores válidos | 5 | 0.01% | Imputar por interpolación lineal entre vecinos |
| Integridad | Sensor listado por la API que no devolvió ninguna medición | 53 | 66.25% | Excluir del análisis y documentar el contaminante afectado |
| Exactitud | \|z-score\| > 5 respecto de la media histórica del propio sensor | 41 | 0.07% | Conservar: pueden ser episodios reales de contaminación |
| Consistencia | Gases reportados en ppm/ppb convertidos a ug/m3 | 0 | 0.00% | Normalizar: toda la capa mart queda en ug/m3 |
| Consistencia | Un mismo contaminante llega con más de una unidad de origen | 0 | 0.00% | Normalizar antes de agregar |
| Integridad | location_id de la medición no existe en raw.locations | 0 | 0.00% | Investigar: rompería el join del dashboard |
| Integridad | latitude o longitude nulas | 0 | 0.00% | Excluir del mapa, conservar en las series |
| Completitud | Día-estación con menos de 18 de 24 horas medidas | 123 | 5.11% | Conservar, pero no se declara excedencia OMS |
| Oportunidad | La medición más reciente tiene más de 48 h | 0 | 0.00% | Alertar: puede indicar ingesta caída |
