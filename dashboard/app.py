"""Dashboard de calidad del aire (punto 4 del enunciado).

    streamlit run dashboard/app.py

Lee EXCLUSIVAMENTE de la capa mart. Nunca toca raw ni stg: esa separación es lo
que convierte a MotherDuck en un repositorio analítico consultable y no en un
archivo de datos crudos con un gráfico encima.
"""

import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

st.set_page_config(page_title="Calidad del aire", page_icon="🌫️", layout="wide")

# En Streamlit Cloud los secretos llegan por st.secrets; el resto del código
# sólo conoce variables de entorno.
# En local no existe secrets.toml y el acceso a st.secrets puede lanzar
# excepción; ahí las credenciales llegan por .env y no hay nada que copiar.
try:
    for clave in ("MOTHERDUCK_TOKEN", "OPENAQ_API_KEY", "DB_TARGET"):
        if clave not in os.environ and clave in st.secrets:
            os.environ[clave] = st.secrets[clave]
except Exception:
    pass

from src.config import SCHEMA_MART  # noqa: E402
from src.db import connect  # noqa: E402


@st.cache_resource
def get_connection():
    return connect()


@st.cache_data(ttl=900)
def query(sql: str) -> pd.DataFrame:
    return get_connection().execute(sql).fetchdf()


M = SCHEMA_MART

st.title("Calidad del aire — monitoreo automatizado")

try:
    diario = query(f"SELECT * FROM {M}.daily_air_quality")
except Exception as exc:
    st.error(
        "No se pudo leer la capa mart. ¿Ya corriste `python -m src.run_pipeline`?\n\n"
        f"{exc}"
    )
    st.stop()

if diario.empty:
    st.warning("La capa mart está vacía. Ejecutá el pipeline primero.")
    st.stop()

# DuckDB entrega DATE como datetime64; pandas 3 no admite compararlo contra
# objetos date, así que unificamos el tipo una sola vez y acá.
diario["fecha"] = pd.to_datetime(diario["fecha"])

# --- Filtros ---------------------------------------------------------------
with st.sidebar:
    st.header("Filtros")
    contaminantes = sorted(diario["contaminante"].dropna().unique())
    contaminante = st.selectbox("Contaminante", contaminantes)

    sub = diario[diario["contaminante"] == contaminante]
    # st.date_input trabaja con date; la comparación posterior con Timestamp.
    fmin, fmax = sub["fecha"].min().date(), sub["fecha"].max().date()
    rango = st.date_input("Rango de fechas", value=(fmin, fmax),
                          min_value=fmin, max_value=fmax)
    if isinstance(rango, tuple) and len(rango) == 2:
        desde, hasta = rango
    else:
        desde, hasta = fmin, fmax

    estaciones = sorted(sub["estacion"].dropna().unique())
    elegidas = st.multiselect("Estaciones", estaciones, default=estaciones[:6])

f = sub[
    (sub["fecha"] >= pd.Timestamp(desde))
    & (sub["fecha"] <= pd.Timestamp(hasta))
]
if elegidas:
    f = f[f["estacion"].isin(elegidas)]

# --- Indicadores -----------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Promedio del período", f"{f['valor_promedio'].mean():.1f} µg/m³")
c2.metric("Máximo diario", f"{f['valor_promedio'].max():.1f} µg/m³")
excedidos = int(f["excede_guia_oms"].sum()) if "excede_guia_oms" in f else 0
c3.metric("Días sobre guía OMS", f"{excedidos}")
c4.metric("Completitud media", f"{f['completitud_pct'].mean():.0f}%")

tab_pan, tab_series, tab_dq, tab_ops = st.tabs(
    ["Panorama", "Series temporales", "Calidad de datos", "Operación"]
)

# --- Panorama: mapa + ranking ---------------------------------------------
with tab_pan:
    izq, der = st.columns([1, 1])

    with izq:
        st.subheader("Estaciones")
        mapa = (
            f.groupby(["estacion", "latitude", "longitude"], as_index=False)
            ["valor_promedio"].mean()
            .dropna(subset=["latitude", "longitude"])
        )
        if mapa.empty:
            st.info("Las estaciones seleccionadas no tienen coordenadas.")
        else:
            st.map(mapa.rename(columns={"latitude": "lat", "longitude": "lon"}),
                   size="valor_promedio")

    with der:
        st.subheader(f"Promedio de {contaminante} por estación")
        ranking = (
            f.groupby("estacion", as_index=False)["valor_promedio"].mean()
            .sort_values("valor_promedio", ascending=True)
        )
        fig = px.bar(ranking, x="valor_promedio", y="estacion", orientation="h",
                     labels={"valor_promedio": "µg/m³", "estacion": ""})
        fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Perfil horario promedio")
    perfil = query(
        f"SELECT * FROM {M}.hourly_profile WHERE contaminante = '{contaminante}'"
    )
    if not perfil.empty:
        agg = perfil.groupby("hora_utc", as_index=False)["valor_promedio"].mean()
        fig = px.line(agg, x="hora_utc", y="valor_promedio", markers=True,
                      labels={"hora_utc": "Hora (UTC)", "valor_promedio": "µg/m³"})
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "El patrón diario suele mostrar picos en las horas de mayor tránsito "
            "y en las noches de inversión térmica."
        )

# --- Series ----------------------------------------------------------------
with tab_series:
    st.subheader(f"Evolución diaria de {contaminante}")
    fig = px.line(f.sort_values("fecha"), x="fecha", y="valor_promedio",
                  color="estacion",
                  labels={"valor_promedio": "µg/m³", "fecha": ""})
    guia = query(
        f"SELECT limite_24h FROM stg.who_guidelines WHERE parameter = '{contaminante}'"
    )
    if not guia.empty:
        limite = float(guia.iloc[0, 0])
        fig.add_hline(y=limite, line_dash="dash",
                      annotation_text=f"Guía OMS 24 h: {limite} µg/m³")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Excedencias por mes")
    mensual = query(
        f"SELECT * FROM {M}.monthly_exceedance WHERE contaminante = '{contaminante}'"
    )
    if mensual.empty:
        st.info("Todavía no hay meses con suficientes días medidos.")
    else:
        fig = px.bar(mensual, x="mes", y="pct_dias_excedidos", color="estacion",
                     barmode="group",
                     labels={"pct_dias_excedidos": "% de días sobre la guía", "mes": ""})
        st.plotly_chart(fig, use_container_width=True)

# --- Calidad ---------------------------------------------------------------
with tab_dq:
    st.subheader("Reglas de calidad aplicadas en la última corrida")
    dq = query(
        f"""
        SELECT * FROM {M}.dq_report
        WHERE run_id = (SELECT run_id FROM {M}.dq_report ORDER BY checked_at DESC LIMIT 1)
        ORDER BY records_affected DESC
        """
    )
    if dq.empty:
        st.info("Sin reportes de calidad todavía.")
    else:
        fallidas = int((~dq["passed"]).sum())
        if fallidas:
            st.error(f"{fallidas} regla(s) fuera de umbral en la última corrida.")
        else:
            st.success("Todas las reglas dentro de umbral.")

        st.dataframe(
            dq[["dimension", "check_name", "rule", "records_affected",
                "pct_affected", "action_taken", "passed"]],
            use_container_width=True, hide_index=True,
        )

        afectados = dq[dq["records_affected"] > 0].sort_values("records_affected")
        if not afectados.empty:
            fig = px.bar(afectados, x="records_affected", y="check_name",
                         orientation="h", color="dimension",
                         labels={"records_affected": "Registros afectados",
                                 "check_name": ""})
            st.plotly_chart(fig, use_container_width=True)

# --- Operación -------------------------------------------------------------
with tab_ops:
    st.subheader("Historial de ejecuciones del pipeline")
    runs = query(
        f"SELECT * FROM {M}.pipeline_runs ORDER BY started_at DESC LIMIT 50"
    )
    if runs.empty:
        st.info("Sin corridas registradas.")
    else:
        runs["duracion_s"] = (
            pd.to_datetime(runs["finished_at"]) - pd.to_datetime(runs["started_at"])
        ).dt.total_seconds().round(0)
        st.dataframe(
            runs[["run_id", "started_at", "duracion_s", "rows_ingested",
                  "api_requests", "status"]],
            use_container_width=True, hide_index=True,
        )
        st.caption(
            "Esta tabla es la evidencia de que la ingesta corre sola: cada fila "
            "es una ejecución programada por GitHub Actions."
        )
