# Pipeline de calidad del aire — OpenAQ → MotherDuck → Streamlit

Pipeline ETL automatizado que ingiere mediciones horarias de contaminantes desde
la API pública de OpenAQ, las depura aplicando reglas de calidad explícitas y
cuantificadas, las carga en un repositorio analítico en MotherDuck y las expone
en un dashboard.

> El enunciado exige que un tercero pueda replicar la solución siguiendo
> únicamente el documento. Estas instrucciones se prueban en una carpeta limpia
> antes de cada entrega.

## Arquitectura

```
   OpenAQ API v3                GitHub Actions (cron cada 6 h)
        │                                  │
        └──────────► src/ingest.py ◄────────┘
                          │
                          ▼
                 raw.measurements          (zona de aterrizaje, append-only)
                          │
                  src/transform.py
                          │
                  ┌───────┴────────┐
                  ▼                ▼
          stg.measurements   stg.measurements_clean
          (banderas de       (descarte + imputación
           calidad)           + métricas derivadas)
                          │
                          ▼
              mart.daily_air_quality        MotherDuck
              mart.station_summary          (repositorio analítico)
              mart.hourly_profile
              mart.monthly_exceedance
              mart.dq_report
              mart.pipeline_runs
                          │
                          ▼
                 dashboard/app.py           (Streamlit)
```

### Por qué cada componente

| Componente | Rol | Por qué se eligió |
|---|---|---|
| OpenAQ API v3 | Fuente | Datos reales, públicos, horarios y con historial. El endpoint `/hours` ya entrega promedios con métrica de cobertura. |
| GitHub Actions | Orquestador | Cron gratuito, sin servidor que mantener, y cada ejecución queda registrada como evidencia auditable. |
| DuckDB / MotherDuck | Repositorio analítico | Motor columnar OLAP. El mismo código corre local (archivo) o en la nube (`md:`), lo que permite desarrollar sin red. Capa gratuita. |
| SQL en archivos | Transformación | Las transformaciones quedan legibles y versionadas, listas para el anexo del informe. |
| Streamlit | Visualización | Se conecta directo al repositorio analítico y se publica gratis en Streamlit Cloud. |

## Puesta en marcha

### 1. Requisitos

- Python 3.11 o superior
- Una cuenta en OpenAQ (gratuita) y otra en MotherDuck (capa gratuita)

### 2. Credenciales

Registrate y obtené las dos claves:

- **OpenAQ**: https://explore.openaq.org/register → Account settings → API key
- **MotherDuck**: https://app.motherduck.com → Settings → Access Tokens

```bash
cp .env.example .env
```

Editá `.env` y pegá ambas.

### 3. Instalación

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

En Windows PowerShell el activador es `.venv\Scripts\Activate.ps1`.

```bash
pip install -r requirements.txt
```

### 4. Verificar el contrato de la API

**Correr esto antes que nada.** La documentación de OpenAQ es inconsistente
sobre algunos nombres de parámetros; este script los prueba contra la API real
y te dice qué poner en `config.yml`.

```bash
python -m src.explore
```

Ajustá `country_param`, `datetime_from_param` y `datetime_to_param` en
`config.yml` con lo que el script reporte.

### 5. Primera corrida

La primera ejecución hace backfill de 90 días y tarda varios minutos por el
límite de 60 requests/minuto de OpenAQ.

```bash
python -m src.run_pipeline
```

### 6. Dashboard

```bash
streamlit run dashboard/app.py
```

## Prueba de humo

Valida las transformaciones y las 13 reglas de calidad con datos sintéticos,
sin credenciales ni acceso a la API. Inyecta problemas conocidos —un duplicado,
un centinela, un valor fuera de rango y un hueco de una hora— y verifica que el
pipeline los detecte con los conteos exactos esperados.

```bash
.venv\Scripts\python.exe tests/smoke_test.py
```

Sirve como verificación de carga (punto 2c): demuestra con un caso controlado
que las transformaciones producen lo esperado. La regla `frescura_del_dato`
siempre aparece fallando en esta prueba porque compara contra la hora actual y
los datos sintéticos tienen fecha fija.

## Desarrollo sin MotherDuck

Todo el pipeline funciona contra un archivo local, sin token ni red:

```bash
DB_TARGET=airq.duckdb python -m src.run_pipeline
```

## Automatización

El workflow `.github/workflows/pipeline.yml` corre cada 6 horas. Para activarlo:

1. Subí el repositorio a GitHub.
2. Settings → Secrets and variables → Actions → New repository secret.
3. Creá `OPENAQ_API_KEY` y `MOTHERDUCK_TOKEN`.
4. Actions → Pipeline calidad del aire → Run workflow, para probarlo a mano.

Cada corrida deja una fila en `mart.pipeline_runs` y publica `docs/dq_report.*`
como artefacto descargable.

## Estructura

```
config.yml                 Toda la parametrización: país, contaminantes, umbrales
src/api.py                 Cliente HTTP con limitador de tasa y reintentos
src/explore.py             Sonda de contrato de la API (correr una vez)
src/ingest.py              Extracción incremental con marca de agua
src/transform.py           Orquesta el SQL y materializa las reglas del YAML
src/quality.py             13 reglas de calidad con conteos
src/run_pipeline.py        Punto de entrada único
sql/01_staging.sql         Tipado, normalización de unidades, banderas
sql/02_clean.sql           Descarte, imputación y métricas derivadas
sql/03_marts.sql           Tablas de consumo del dashboard
dashboard/app.py           Streamlit
tests/smoke_test.py        Prueba de humo con datos sintéticos, sin credenciales
docs/dq_report.md          Tabla de calidad generada, para pegar en el informe
```

## Decisiones de calidad de datos

### Dónde está realmente la suciedad en esta fuente

La primera carga reveló algo que conviene decir explícitamente: **OpenAQ ya filtra
en la capa de agregación horaria**. El endpoint `/hours` sólo devuelve horas que
midió y validó, así que `coverage.percentComplete` llega en 100 % en todas las
filas y no aparece un solo valor centinela, negativo o fuera de rango físico.

Las reglas clásicas de validez existen igual en el pipeline y reportan cero. Eso
no es redundante: documenta una propiedad de la fuente, y protege el día que se
sume otro proveedor que sí entregue crudo.

El problema real de esta fuente es la **completitud**. Un corte de tres horas no
llega como tres filas malas: llega como tres filas que no existen. Por eso el
pipeline genera una malla horaria explícita por sensor (`stg.hourly_spine`) y
compara contra ella. Sin esa malla, 2.650 horas ausentes serían invisibles.

De esas 2.650 horas, sólo 5 son huecos aislados de una hora. El resto son bloques
largos —caídas de sensor, no lecturas sueltas perdidas—, lo que confirma que
interpolarlos habría sido inventar series enteras.

| Situación | Decisión | Justificación |
|---|---|---|
| Fila duplicada por reingesta | Descartar, conservar la más reciente | La API corrige valores recientes; gana la última versión. |
| Hora ausente en la serie esperada | Imputar sólo si el hueco es de 1 h | Los bloques largos son caídas de sensor; rellenarlos fabricaría datos. |
| Sensor registrado pero mudo | Excluir y documentar | 53 de 80 sensores seleccionados no reportan; casi todos de O₃ y NO₂. |
| Valor centinela (`-999`) | Descartar | Es un hueco disfrazado de número; promediarlo hunde la media. |
| Valor fuera del rango físico | Descartar | Un PM2.5 de 5.000 µg/m³ es una falla de sensor, no un evento. |
| Hueco aislado de 1 hora | Imputar por interpolación | Preserva la continuidad de la serie sin inventar tramos largos. |
| Hueco de más de 1 hora | Dejar vacío | Rellenarlo sería fabricar datos que nadie midió. |
| Hora de cobertura parcial | Conservar marcada | Informativa, pero se excluye del criterio de excedencia. |
| Gas en ppm/ppb | Convertir a µg/m³ | Sin esto se promedian escalas que difieren en 1.000×. |

Las reglas se aplican con **precedencia**, no de forma independiente: un valor
centinela como `-999` también cae fuera del rango físico, pero se contabiliza
sólo bajo su propia regla. Sin esa exclusión, los totales por regla sumarían más
que los descartes reales y la tabla del informe sería engañosa.

Los conteos exactos de cada regla se generan en cada corrida y quedan en
`docs/dq_report.md` y en la tabla `mart.dq_report`.

## Fuente de datos

OpenAQ agrega mediciones de redes gubernamentales de monitoreo y sensores de
investigación. Los datos se usan bajo las licencias declaradas por cada
proveedor, disponibles en el campo `licenses` de cada estación.

- Documentación: https://docs.openaq.org
- Explorador: https://explore.openaq.org
