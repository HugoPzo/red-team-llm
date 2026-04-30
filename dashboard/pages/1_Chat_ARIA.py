"""
Interfaz de chat interactiva con ARIA.

Permite conversar con el chatbot a través del target directo (modo NONE)
o a través del proxy guardrail (modos RULE y JUDGE), con un panel de
depuración lateral que muestra en tiempo real la decisión del guardrail.

Características:
- Selector de modo: NONE / RULE / JUDGE
- Panel debug (sidebar): decisión, confianza, latencia, patrón detectado
- Área de documento adjunto: simula el vector V3 (indirect injection)
- Historial multi-turno con session_id persistido entre mensajes
- Indicador visual de estado de los servicios (target, proxy)

Ejecución:
    streamlit run dashboard/app.py --server.port 8501
    (esta página aparece automáticamente en la navegación lateral)
"""

import sys
import time
from pathlib import Path
from typing import Optional

import httpx
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# ===== Constantes =====

TARGET_URL = "http://localhost:8000"
PROXY_URL = "http://localhost:8001"

URL_BY_MODE: dict[str, str] = {
    "NONE": TARGET_URL,
    "RULE": PROXY_URL,
    "JUDGE": PROXY_URL,
}

OWASP_BY_MODE: dict[str, str] = {
    "NONE": "Sin guardrail — modelo expuesto directamente",
    "RULE": "Guardrail A: Regex + heurísticas (CPU, <50ms)",
    "JUDGE": "Guardrail B: LLM-as-Judge con gemma3:1b (~3-8s)",
}


# ===== Configuración de página =====

st.set_page_config(
    page_title="Chat ARIA",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ===== Inicialización del estado de sesión =====

def _init_state() -> None:
    """Inicializa el estado de la sesión de Streamlit si es la primera carga."""
    defaults: dict = {
        "messages": [],          # list[dict]: role, content, meta
        "session_id": None,      # str: ID de sesión del chatbot ARIA
        "guardrail_log": [],     # list[dict]: historial de decisiones del guardrail
        "mode": "NONE",          # str: modo activo al momento del último mensaje
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()


# ===== Helpers HTTP =====

def _check_service(url: str) -> bool:
    """Verifica si un servicio está disponible (timeout agresivo para UI)."""
    try:
        r = httpx.get(f"{url}/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def _send_chat(
    mode: str,
    message: str,
    session_id: Optional[str],
    document: Optional[str],
) -> dict:
    """
    Envía un mensaje al endpoint correspondiente según el modo.

    Retorna el dict de respuesta JSON completo.
    Lanza httpx.HTTPError en caso de fallo.
    """
    base_url = URL_BY_MODE[mode]
    payload: dict = {"message": message}
    if session_id:
        payload["session_id"] = session_id

    if document and document.strip():
        # Endpoint con documento (vector V3)
        payload["document"] = document.strip()
        endpoint = f"{base_url}/chat/with-document"
    else:
        endpoint = f"{base_url}/chat"

    with httpx.Client(timeout=120.0) as client:
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        return response.json()


# ===== Sidebar =====

with st.sidebar:
    st.title("ARIA — Panel de control")
    st.caption("Chatbot corporativo TecnoAragón S.A.")

    st.divider()

    # --- Selector de modo ---
    mode = st.selectbox(
        "Modo de guardrail",
        options=["NONE", "RULE", "JUDGE"],
        index=0,
        help="NONE: directo al modelo. RULE: proxy regex. JUDGE: proxy LLM-as-Judge.",
    )
    st.caption(OWASP_BY_MODE[mode])

    st.divider()

    # --- Estado de servicios ---
    st.markdown("**Estado de servicios**")
    target_ok = _check_service(TARGET_URL)
    proxy_ok = _check_service(PROXY_URL)

    st.markdown(
        f"{'🟢' if target_ok else '🔴'} Target (puerto 8000) — "
        + ("activo" if target_ok else "inactivo")
    )
    st.markdown(
        f"{'🟢' if proxy_ok else '🔴'} Proxy guardrail (puerto 8001) — "
        + ("activo" if proxy_ok else "inactivo")
    )

    if mode in ("RULE", "JUDGE") and not proxy_ok:
        st.warning(
            "El proxy no está activo. Inicia con:\n\n"
            f"`GUARDRAIL_MODE={mode} uvicorn guardrails.proxy:app --port 8001`"
        )

    st.divider()

    # --- Debug mode ---
    debug_mode = st.toggle("Modo debug", value=True)
    if debug_mode:
        st.caption("Muestra la decisión del guardrail tras cada mensaje.")

    st.divider()

    # --- Documento adjunto ---
    st.markdown("**Documento adjunto (opcional)**")
    st.caption("Simula el endpoint /chat/with-document (vector V3 — Indirect Injection).")
    document_text = st.text_area(
        label="Contenido del documento",
        placeholder=(
            "Pega aquí el contenido de un documento para que ARIA lo analice.\n"
            "Ejemplo de uso malicioso: incluir instrucciones ocultas dentro\n"
            "de un memorándum aparentemente legítimo."
        ),
        height=160,
        label_visibility="collapsed",
    )
    if document_text.strip():
        st.caption(f"Documento activo: {len(document_text)} caracteres")

    st.divider()

    # --- Sesión activa ---
    if st.session_state.session_id:
        st.markdown("**Sesión activa**")
        st.code(st.session_state.session_id[:16] + "...", language=None)

    # --- Botón de limpiar ---
    if st.button("Limpiar conversación", use_container_width=True):
        st.session_state.messages = []
        st.session_state.session_id = None
        st.session_state.guardrail_log = []
        st.rerun()


# ===== Área principal de chat =====

st.title("Chat con ARIA")
st.caption(
    f"Modo activo: **{mode}** — "
    + ("directo al modelo" if mode == "NONE" else f"proxy guardrail en {PROXY_URL}")
)

# --- Renderizar historial ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        # Panel de debug inline bajo cada respuesta del asistente
        if debug_mode and msg["role"] == "assistant" and msg.get("guardrail_info"):
            gi = msg["guardrail_info"]
            allowed = gi.get("allowed", True)
            _mode_label = gi.get("mode", "?")
            _reason = gi.get("reason", "")
            _conf = gi.get("confidence", 1.0)
            _lat = gi.get("latency_ms", 0.0)
            _pattern = gi.get("matched_pattern", "")
            _category = gi.get("category", "")

            decision_color = "green" if allowed else "red"
            decision_label = "PERMITIDO" if allowed else "BLOQUEADO"

            with st.expander(
                f"🔍 Guardrail [{_mode_label}] — :{decision_color}[{decision_label}]",
                expanded=False,
            ):
                dc1, dc2, dc3 = st.columns(3)
                with dc1:
                    st.metric("Decisión", decision_label)
                with dc2:
                    st.metric("Confianza", f"{_conf:.0%}")
                with dc3:
                    st.metric("Latencia guardrail", f"{_lat:.0f} ms")

                if _reason:
                    st.markdown(f"**Razón:** {_reason}")
                if _pattern:
                    st.markdown(f"**Patrón detectado:** `{_pattern}`")
                if _category:
                    st.markdown(f"**Categoría:** `{_category}`")

        # Si el mensaje fue bloqueado, mostrar aviso
        if msg.get("blocked"):
            st.error("Mensaje bloqueado por el guardrail — respuesta generada por el proxy.")


# --- Panel de debug lateral con historial de decisiones (sidebar) ---
if debug_mode and st.session_state.guardrail_log:
    with st.sidebar:
        st.divider()
        st.markdown("**Historial de decisiones del guardrail**")
        for i, entry in enumerate(reversed(st.session_state.guardrail_log[-5:])):
            allowed = entry.get("allowed", True)
            icon = "✅" if allowed else "🚫"
            label = "SAFE" if allowed else "UNSAFE"
            reason_short = entry.get("reason", "")[:40]
            lat = entry.get("latency_ms", 0)
            st.markdown(
                f"{icon} **#{len(st.session_state.guardrail_log) - i}** "
                f"`{label}` — {lat:.0f}ms  \n"
                f"<small>{reason_short}</small>",
                unsafe_allow_html=True,
            )


# ===== Input del usuario =====

user_input = st.chat_input(
    placeholder="Escribe tu mensaje a ARIA...",
    disabled=(not target_ok) or (mode in ("RULE", "JUDGE") and not proxy_ok),
)

if user_input:
    # Mostrar mensaje del usuario inmediatamente
    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    # Enviar al servicio y mostrar respuesta
    with st.chat_message("assistant"):
        with st.spinner("ARIA está respondiendo..."):
            try:
                t_start = time.perf_counter()
                data = _send_chat(
                    mode=mode,
                    message=user_input,
                    session_id=st.session_state.session_id,
                    document=document_text if document_text.strip() else None,
                )
                total_ms = (time.perf_counter() - t_start) * 1000

                response_text: str = data.get("response", "")
                returned_session_id: str = data.get("session_id", "")
                guardrail_info: Optional[dict] = data.get("guardrail")

                # El proxy retorna session_id="blocked" cuando bloquea.
                # Solo actualizar session_id cuando sea una sesión real del target.
                was_blocked = (
                    guardrail_info is not None
                    and not guardrail_info.get("allowed", True)
                )
                if not was_blocked and returned_session_id and returned_session_id != "blocked":
                    st.session_state.session_id = returned_session_id

                # Renderizar respuesta
                st.markdown(response_text)

                # Panel de debug inline (primera vez, antes de guardar en state)
                if debug_mode and guardrail_info:
                    allowed = guardrail_info.get("allowed", True)
                    decision_color = "green" if allowed else "red"
                    decision_label = "PERMITIDO" if allowed else "BLOQUEADO"
                    _lat = guardrail_info.get("latency_ms", 0.0)
                    _conf = guardrail_info.get("confidence", 1.0)
                    _reason = guardrail_info.get("reason", "")
                    _pattern = guardrail_info.get("matched_pattern", "")
                    _category = guardrail_info.get("category", "")
                    _mode_label = guardrail_info.get("mode", mode)

                    with st.expander(
                        f"🔍 Guardrail [{_mode_label}] — :{decision_color}[{decision_label}]",
                        expanded=True,   # Expandido en la respuesta más reciente
                    ):
                        dc1, dc2, dc3 = st.columns(3)
                        with dc1:
                            st.metric("Decisión", decision_label)
                        with dc2:
                            st.metric("Confianza", f"{_conf:.0%}")
                        with dc3:
                            st.metric("Latencia guardrail", f"{_lat:.0f} ms")

                        if _reason:
                            st.markdown(f"**Razón:** {_reason}")
                        if _pattern:
                            st.markdown(f"**Patrón detectado:** `{_pattern}`")
                        if _category:
                            st.markdown(f"**Categoría:** `{_category}`")

                if was_blocked:
                    st.error("Mensaje bloqueado — esta respuesta viene del proxy, no del modelo.")

            except httpx.HTTPStatusError as e:
                response_text = f"Error HTTP {e.response.status_code}: {e.response.text[:200]}"
                guardrail_info = None
                was_blocked = False
                total_ms = 0.0
                st.error(response_text)
            except Exception as e:
                response_text = f"Error de conexión: {str(e)}"
                guardrail_info = None
                was_blocked = False
                total_ms = 0.0
                st.error(response_text)

    # Guardar respuesta en historial con metadata
    st.session_state.messages.append({
        "role": "assistant",
        "content": response_text,
        "guardrail_info": guardrail_info,
        "blocked": was_blocked,
        "latency_ms": total_ms,
        "mode": mode,
    })

    # Guardar en log del guardrail (para el historial del sidebar)
    if guardrail_info:
        st.session_state.guardrail_log.append(guardrail_info)

    st.rerun()
