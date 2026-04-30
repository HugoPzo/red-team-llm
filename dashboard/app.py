"""
Dashboard de resultados del Red Team LLM.

Tres vistas:
  1. Resumen ejecutivo — pivote vector × modo, métricas globales
  2. Detalle por ataque — payload, respuesta y clasificación
  3. Métricas de defensa — latencia, tasa de detección, distribución de bloqueadores

Lectura: solo SQLite (data/results.db). No escribe nada.

Ejecución:
    streamlit run dashboard/app.py --server.port 8501

Decisiones de diseño:
- asyncio.new_event_loop() por solicitud: evita conflictos con el event loop
  interno de Streamlit (que puede o no tener uno activo según el runner).
- @st.cache_data(ttl=30): caché de 30s para no golpear SQLite en cada
  interacción. El botón "Actualizar" limpia el caché manualmente.
- Altair para charts complejos (stacked bars, latencia): bundled con Streamlit,
  no requiere dependencias adicionales.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

# Asegurar imports del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.db import Database


# ===== Helpers de acceso a datos =====


def _run_sync(coro: Any) -> Any:
    """
    Ejecuta una coroutine async de forma síncrona.

    Crea un event loop nuevo por llamada para evitar conflictos
    con el scheduler interno de Streamlit.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@st.cache_data(ttl=30)
def load_all_data() -> tuple[list, list, list, list]:
    """
    Carga todos los datos desde SQLite.

    Caché de 30s. Llama a st.cache_data.clear() + st.rerun()
    para forzar actualización.
    """
    async def _fetch():
        db = Database()
        await db.init_db()
        pivot = await db.query_summary_pivot()
        details = await db.query_attack_details()
        metrics = await db.query_defense_metrics()
        blockers = await db.query_blocker_distribution()
        await db.close()
        return pivot, details, metrics, blockers

    return _run_sync(_fetch())


# ===== Colores de clasificación =====

COLORS = {
    "SUCCESS": "#e74c3c",   # rojo
    "PARTIAL": "#f39c12",   # naranja
    "BLOCKED": "#27ae60",   # verde
}

MODE_ORDER = ["NONE", "RULE", "JUDGE"]
VECTOR_ORDER = ["V1", "V2", "V3", "V4", "V5"]


# ===== Configuración de la página =====

st.set_page_config(
    page_title="Red Team LLM Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("Red Team LLM — Dashboard de Resultados")
st.caption(
    "UNAM FES Aragón · Temas Especiales de Seguridad Informática · "
    "gemma3:4b + GTX 1650 Ti (4 GB VRAM)"
)

# Botón de actualización y carga
col_refresh, col_spacer = st.columns([1, 9])
with col_refresh:
    if st.button("Actualizar datos"):
        st.cache_data.clear()
        st.rerun()

pivot_raw, details_raw, metrics_raw, blockers_raw = load_all_data()

# Convertir a DataFrames
df_pivot = pd.DataFrame(pivot_raw)
df_details = pd.DataFrame(details_raw)
df_metrics = pd.DataFrame(metrics_raw)
df_blockers = pd.DataFrame(blockers_raw)

if df_pivot.empty:
    st.error("No hay datos en la base de datos. Ejecuta las campañas primero.")
    st.stop()


# ===== Tabs (3 vistas) =====

tab1, tab2, tab3 = st.tabs([
    "Vista 1 — Resumen ejecutivo",
    "Vista 2 — Detalle por ataque",
    "Vista 3 — Métricas de defensa",
])


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 1 — RESUMEN EJECUTIVO
# ─────────────────────────────────────────────────────────────────────────────

with tab1:
    st.subheader("Comparativa global: NONE vs RULE vs JUDGE")

    # --- Métricas grandes por modo ---
    mode_cols = st.columns(len(MODE_ORDER))
    for i, mode in enumerate(MODE_ORDER):
        mode_data = df_metrics[df_metrics["guardrail_mode"] == mode]
        with mode_cols[i]:
            if mode_data.empty:
                st.metric(
                    label=f"Modo {mode}",
                    value="Sin datos",
                    help="Campaña no ejecutada aún",
                )
            else:
                row = mode_data.iloc[0]
                total = int(row["total_attacks"])
                blocked = int(row["blocked"])
                success = int(row["success"])
                rate = float(row["detection_rate"])
                st.metric(
                    label=f"Modo {mode}",
                    value=f"{rate:.0f}% bloqueado",
                    delta=f"{blocked}/{total} ataques",
                    delta_color="normal" if mode != "NONE" else "off",
                )
                st.caption(f"Éxitos del atacante: {success}/{total}")

    st.divider()

    # --- Tabla pivote: vector × modo × clasificación ---
    st.markdown("#### Resultados por vector y modo de defensa")

    if not df_pivot.empty:
        # Construir tabla pivote legible
        pivot_table: dict[str, dict[str, str]] = {}
        for _, row in df_pivot.iterrows():
            vec = str(row["vector_id"])
            mode = str(row["guardrail_mode"])
            cls = str(row["classification"])
            count = int(row["count"])
            key = f"{vec}"
            if key not in pivot_table:
                pivot_table[key] = {}
            # Acumular por modo
            cell_key = mode
            existing = pivot_table[key].get(cell_key, "")
            label = f"{'✅' if cls == 'BLOCKED' else '🔴' if cls == 'SUCCESS' else '🟡'} {cls[:1]}={count}"
            pivot_table[key][cell_key] = (existing + "  " + label).strip()

        pivot_df = pd.DataFrame(pivot_table).T
        pivot_df.index.name = "Vector"
        # Ordenar columnas por MODE_ORDER
        available_modes = [m for m in MODE_ORDER if m in pivot_df.columns]
        pivot_df = pivot_df[available_modes]
        st.dataframe(pivot_df, use_container_width=True)

    st.divider()

    # --- Gráfico de barras apiladas: ataques por modo ---
    st.markdown("#### Distribución de clasificaciones por modo")

    if not df_pivot.empty:
        # Agregar por modo y clasificación
        agg = (
            df_pivot.groupby(["guardrail_mode", "classification"])["count"]
            .sum()
            .reset_index()
        )
        # Solo modos con datos
        agg = agg[agg["guardrail_mode"].isin(MODE_ORDER)]

        chart_pivot = (
            alt.Chart(agg)
            .mark_bar()
            .encode(
                x=alt.X(
                    "guardrail_mode:N",
                    sort=MODE_ORDER,
                    title="Modo de guardrail",
                    axis=alt.Axis(labelFontSize=13),
                ),
                y=alt.Y(
                    "count:Q",
                    title="Número de ataques",
                    stack="zero",
                ),
                color=alt.Color(
                    "classification:N",
                    scale=alt.Scale(
                        domain=["SUCCESS", "PARTIAL", "BLOCKED"],
                        range=["#e74c3c", "#f39c12", "#27ae60"],
                    ),
                    legend=alt.Legend(title="Clasificación"),
                ),
                tooltip=["guardrail_mode:N", "classification:N", "count:Q"],
            )
            .properties(height=320, title="Ataques bloqueados vs exitosos por modo")
        )
        st.altair_chart(chart_pivot, use_container_width=True)

    # --- Gráfico por vector ---
    st.markdown("#### Eficacia por vector de ataque")

    if not df_pivot.empty:
        agg_vec = (
            df_pivot[df_pivot["classification"] == "BLOCKED"]
            .groupby(["vector_id", "guardrail_mode"])["count"]
            .sum()
            .reset_index()
        )

        chart_vec = (
            alt.Chart(agg_vec)
            .mark_bar()
            .encode(
                x=alt.X("vector_id:N", sort=VECTOR_ORDER, title="Vector OWASP"),
                y=alt.Y("count:Q", title="Ataques bloqueados"),
                color=alt.Color(
                    "guardrail_mode:N",
                    sort=MODE_ORDER,
                    scale=alt.Scale(
                        domain=["NONE", "RULE", "JUDGE"],
                        range=["#95a5a6", "#3498db", "#9b59b6"],
                    ),
                    legend=alt.Legend(title="Modo"),
                ),
                xOffset="guardrail_mode:N",
                tooltip=["vector_id:N", "guardrail_mode:N", "count:Q"],
            )
            .properties(height=300, title="Ataques bloqueados por vector y modo")
        )
        st.altair_chart(chart_vec, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 2 — DETALLE POR ATAQUE
# ─────────────────────────────────────────────────────────────────────────────

with tab2:
    st.subheader("Detalle por ataque")

    if df_details.empty:
        st.info("No hay ataques registrados.")
    else:
        # Filtros en línea
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            vectors_available = sorted(df_details["vector_id"].unique().tolist())
            sel_vectors = st.multiselect(
                "Vector",
                options=vectors_available,
                default=vectors_available,
            )
        with f_col2:
            modes_available = sorted(df_details["guardrail_mode"].unique().tolist())
            sel_modes = st.multiselect(
                "Modo guardrail",
                options=modes_available,
                default=modes_available,
            )
        with f_col3:
            cls_available = sorted(df_details["classification"].unique().tolist())
            sel_cls = st.multiselect(
                "Clasificación",
                options=cls_available,
                default=cls_available,
            )

        # Filtrar DataFrame
        mask = (
            df_details["vector_id"].isin(sel_vectors)
            & df_details["guardrail_mode"].isin(sel_modes)
            & df_details["classification"].isin(sel_cls)
        )
        df_filtered = df_details[mask].copy()

        st.caption(f"{len(df_filtered)} ataques mostrados")

        # Tabla resumen con color
        display_cols = [
            "attack_id", "vector_id", "owasp_category", "variant",
            "guardrail_mode", "classification", "blocked_by",
            "total_latency_ms", "evidence",
        ]
        display_cols = [c for c in display_cols if c in df_filtered.columns]

        st.dataframe(
            df_filtered[display_cols].reset_index(drop=True),
            use_container_width=True,
            column_config={
                "attack_id": st.column_config.NumberColumn("ID", width="small"),
                "vector_id": st.column_config.TextColumn("Vector", width="small"),
                "owasp_category": st.column_config.TextColumn("OWASP", width="small"),
                "variant": st.column_config.NumberColumn("Var.", width="small"),
                "guardrail_mode": st.column_config.TextColumn("Modo", width="small"),
                "classification": st.column_config.TextColumn("Resultado", width="medium"),
                "blocked_by": st.column_config.TextColumn("Bloqueado por", width="medium"),
                "total_latency_ms": st.column_config.NumberColumn(
                    "Latencia (ms)", format="%.0f", width="small"
                ),
                "evidence": st.column_config.TextColumn("Evidencia", width="large"),
            },
            hide_index=True,
        )

        st.divider()
        st.markdown("#### Inspección de payload y respuesta")
        st.caption("Selecciona un ataque por su ID para ver el detalle completo.")

        if not df_filtered.empty:
            attack_ids = df_filtered["attack_id"].tolist()
            sel_id = st.selectbox(
                "Attack ID",
                options=attack_ids,
                format_func=lambda x: (
                    f"ID {x} — "
                    + df_filtered[df_filtered["attack_id"] == x]["vector_id"].values[0]
                    + " Var."
                    + str(df_filtered[df_filtered["attack_id"] == x]["variant"].values[0])
                    + " ["
                    + df_filtered[df_filtered["attack_id"] == x]["guardrail_mode"].values[0]
                    + "] "
                    + df_filtered[df_filtered["attack_id"] == x]["classification"].values[0]
                ),
            )

            row = df_filtered[df_filtered["attack_id"] == sel_id].iloc[0]
            det_c1, det_c2 = st.columns(2)
            with det_c1:
                st.markdown("**Payload enviado al sistema:**")
                st.text_area(
                    label="payload",
                    value=str(row.get("payload", "")),
                    height=250,
                    label_visibility="collapsed",
                )
            with det_c2:
                st.markdown("**Respuesta del sistema:**")
                st.text_area(
                    label="response",
                    value=str(row.get("response_text", "")),
                    height=250,
                    label_visibility="collapsed",
                )

            meta_c1, meta_c2, meta_c3 = st.columns(3)
            with meta_c1:
                st.metric("Clasificación", str(row.get("classification", "")))
            with meta_c2:
                st.metric("Bloqueado por", str(row.get("blocked_by", "")))
            with meta_c3:
                st.metric("Latencia", f"{row.get('total_latency_ms', 0):.0f} ms")


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 3 — MÉTRICAS DE DEFENSA
# ─────────────────────────────────────────────────────────────────────────────

with tab3:
    st.subheader("Métricas de defensa")

    if df_metrics.empty:
        st.info("Sin datos de métricas.")
    else:
        # --- Tabla de métricas por modo ---
        st.markdown("#### Resumen por modo de guardrail")

        display_metrics = df_metrics.copy()
        display_metrics = display_metrics[
            display_metrics["guardrail_mode"].isin(MODE_ORDER)
        ]
        # Reordenar por MODE_ORDER
        display_metrics["_order"] = display_metrics["guardrail_mode"].map(
            {m: i for i, m in enumerate(MODE_ORDER)}
        )
        display_metrics = display_metrics.sort_values("_order").drop("_order", axis=1)

        st.dataframe(
            display_metrics.reset_index(drop=True),
            use_container_width=True,
            column_config={
                "guardrail_mode": st.column_config.TextColumn("Modo", width="small"),
                "total_attacks": st.column_config.NumberColumn("Total ataques", width="small"),
                "blocked": st.column_config.NumberColumn("Bloqueados", width="small"),
                "success": st.column_config.NumberColumn("Éxitos atacante", width="small"),
                "partial": st.column_config.NumberColumn("Parciales", width="small"),
                "detection_rate": st.column_config.NumberColumn(
                    "Tasa detección (%)", format="%.1f", width="medium"
                ),
                "avg_latency_ms": st.column_config.NumberColumn(
                    "Latencia media (ms)", format="%.0f", width="medium"
                ),
                "max_latency_ms": st.column_config.NumberColumn(
                    "Latencia máx (ms)", format="%.0f", width="medium"
                ),
                "min_latency_ms": st.column_config.NumberColumn(
                    "Latencia mín (ms)", format="%.0f", width="medium"
                ),
            },
            hide_index=True,
        )

        st.divider()

        # --- Gráfico: tasa de detección por modo ---
        c_rate, c_lat = st.columns(2)

        with c_rate:
            st.markdown("#### Tasa de detección por modo")
            rate_df = display_metrics[["guardrail_mode", "detection_rate"]].copy()
            chart_rate = (
                alt.Chart(rate_df)
                .mark_bar()
                .encode(
                    x=alt.X(
                        "guardrail_mode:N",
                        sort=MODE_ORDER,
                        title="Modo",
                        axis=alt.Axis(labelFontSize=13),
                    ),
                    y=alt.Y(
                        "detection_rate:Q",
                        scale=alt.Scale(domain=[0, 100]),
                        title="Tasa de detección (%)",
                    ),
                    color=alt.Color(
                        "guardrail_mode:N",
                        sort=MODE_ORDER,
                        scale=alt.Scale(
                            domain=["NONE", "RULE", "JUDGE"],
                            range=["#95a5a6", "#3498db", "#9b59b6"],
                        ),
                        legend=None,
                    ),
                    tooltip=["guardrail_mode:N", "detection_rate:Q"],
                )
                .properties(height=280)
            )
            st.altair_chart(chart_rate, use_container_width=True)

        with c_lat:
            st.markdown("#### Latencia media por modo (ms)")
            lat_df = display_metrics[
                ["guardrail_mode", "avg_latency_ms", "min_latency_ms", "max_latency_ms"]
            ].copy()
            # Barra de latencia media con rango
            chart_lat = (
                alt.Chart(lat_df)
                .mark_bar()
                .encode(
                    x=alt.X(
                        "guardrail_mode:N",
                        sort=MODE_ORDER,
                        title="Modo",
                        axis=alt.Axis(labelFontSize=13),
                    ),
                    y=alt.Y("avg_latency_ms:Q", title="Latencia media (ms)"),
                    color=alt.Color(
                        "guardrail_mode:N",
                        sort=MODE_ORDER,
                        scale=alt.Scale(
                            domain=["NONE", "RULE", "JUDGE"],
                            range=["#95a5a6", "#3498db", "#9b59b6"],
                        ),
                        legend=None,
                    ),
                    tooltip=[
                        "guardrail_mode:N",
                        alt.Tooltip("avg_latency_ms:Q", format=".0f", title="Media (ms)"),
                        alt.Tooltip("min_latency_ms:Q", format=".0f", title="Mín (ms)"),
                        alt.Tooltip("max_latency_ms:Q", format=".0f", title="Máx (ms)"),
                    ],
                )
                .properties(height=280)
            )
            st.altair_chart(chart_lat, use_container_width=True)

        st.divider()

        # --- Distribución de bloqueadores ---
        st.markdown("#### ¿Quién bloqueó? — Distribución de actores defensivos")

        if df_blockers.empty:
            st.info("Sin datos de bloqueadores.")
        else:
            df_blockers_filtered = df_blockers[
                df_blockers["guardrail_mode"].isin(MODE_ORDER)
            ]

            blocker_order = ["GUARDRAIL_RULE", "GUARDRAIL_JUDGE", "SHIELD_GEMMA"]

            chart_blockers = (
                alt.Chart(df_blockers_filtered)
                .mark_bar()
                .encode(
                    x=alt.X(
                        "guardrail_mode:N",
                        sort=MODE_ORDER,
                        title="Modo de guardrail",
                    ),
                    y=alt.Y("count:Q", title="Ataques bloqueados", stack="zero"),
                    color=alt.Color(
                        "blocked_by:N",
                        sort=blocker_order,
                        scale=alt.Scale(
                            domain=["GUARDRAIL_RULE", "GUARDRAIL_JUDGE", "SHIELD_GEMMA"],
                            range=["#3498db", "#9b59b6", "#e67e22"],
                        ),
                        legend=alt.Legend(title="Actor defensivo"),
                    ),
                    tooltip=["guardrail_mode:N", "blocked_by:N", "count:Q"],
                )
                .properties(
                    height=300,
                    title="Distribución de bloqueadores por modo",
                )
            )
            st.altair_chart(chart_blockers, use_container_width=True)

            st.caption(
                "GUARDRAIL_RULE / GUARDRAIL_JUDGE: bloqueado por el proxy antes de llegar al modelo. "
                "SHIELD_GEMMA: el guardrail permitió el paso pero el filtro nativo de Gemma 3 rechazó."
            )

        st.divider()

        # --- Nota sobre datos incompletos ---
        none_data = df_metrics[df_metrics["guardrail_mode"] == "NONE"]
        if none_data.empty or (not none_data.empty and int(none_data.iloc[0]["total_attacks"]) < 15):
            n_none = 0 if none_data.empty else int(none_data.iloc[0]["total_attacks"])
            st.warning(
                f"Modo NONE tiene {n_none}/15 ataques en la DB. "
                "El baseline completo se pobla ejecutando: "
                "`python -m attacker.attack_runner --mode NONE`"
            )
