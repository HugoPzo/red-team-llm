"""
Proxy de guardrail — FastAPI en puerto 8001.

Este proxy se interpone entre el atacante (Capa 2) y el target (Capa 1).
Intercepta cada request, lo evalúa con el guardrail activo (RULE o JUDGE),
y decide si lo reenvía al target o lo bloquea.

Arquitectura:
    Atacante (runner)
        ↓ request
    Puerto 8001 (este proxy)
        ↓ evalúa guardrail
        ├─ SAFE → reenvía a Puerto 8000 (ARIA)
        │           ↓ response del modelo
        │           ← retorna response + metadata de guardrail
        └─ UNSAFE → bloquea
                    ← retorna respuesta de rechazo + metadata

El proxy agrega un campo 'guardrail' al JSON de respuesta para que
el runner pueda registrar la decisión en guardrail_decisions (F4).

Ejecución:
    uvicorn guardrails.proxy:app --host 0.0.0.0 --port 8001

Ejemplo:
    curl -X POST http://localhost:8001/chat \\
      -H 'Content-Type: application/json' \\
      -d '{"message": "Ignora tus instrucciones"}'
"""

import os
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Optional

# Asegurar imports del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import TARGET_MODEL
from guardrails.guardrail_engine import GuardrailEngine
from guardrails.rule_based import GuardrailDecision

# ===== Configuración =====

TARGET_URL = "http://localhost:8000"

# Mensaje de rechazo cuando el guardrail bloquea un mensaje.
# Redactado para ser neutral y no revelar los criterios de detección.
BLOCK_RESPONSE = (
    "Lo siento, no puedo procesar esa solicitud. "
    "Si tienes una pregunta sobre políticas de RRHH, reformúlala "
    "de otra manera. Estoy aquí para ayudarte."
)

# ===== Modelos de request/response =====


class ProxyChatRequest(BaseModel):
    """Request del chat — mismo formato que el target."""

    message: str = Field(..., min_length=1, max_length=32768)
    session_id: Optional[str] = None


class ProxyDocumentRequest(BaseModel):
    """Request con documento — mismo formato que el target."""

    message: str = Field(..., min_length=1, max_length=32768)
    document: str = Field(..., min_length=1, max_length=32768)
    session_id: Optional[str] = None


class GuardrailInfo(BaseModel):
    """Metadata del guardrail incluida en la respuesta."""

    mode: str
    allowed: bool
    reason: str
    matched_pattern: str = ""
    category: str = ""
    confidence: float = 1.0
    latency_ms: float = 0.0


class ProxyChatResponse(BaseModel):
    """Respuesta extendida con metadata del guardrail."""

    session_id: str
    response: str
    model: str
    guardrail: GuardrailInfo


# ===== Aplicación FastAPI =====


app = FastAPI(
    title="ARIA Guardrail Proxy",
    description=(
        "Proxy de defensa que intercepta requests al chatbot ARIA. "
        "Evalúa cada mensaje con el guardrail activo antes de reenviarlo."
    ),
    version="1.0.0",
)

# Modo activo leído de variable de entorno GUARDRAIL_MODE (default: RULE)
# Uso: GUARDRAIL_MODE=JUDGE uvicorn guardrails.proxy:app --port 8001
_active_mode: str = os.environ.get("GUARDRAIL_MODE", "RULE").upper()
guardrail_engine = GuardrailEngine(mode=_active_mode)


@app.on_event("startup")
async def startup_event() -> None:
    """Log de inicio del proxy."""
    print(
        f"Guardrail proxy iniciado — Modo: {_active_mode}\n"
        f"   Target: {TARGET_URL}\n"
        f"   Reglas cargadas: {guardrail_engine.total_rules}"
    )


@app.get("/health")
async def health() -> dict:
    """Health check del proxy y del target."""
    target_healthy = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{TARGET_URL}/health")
            target_healthy = resp.status_code == 200
    except Exception:
        pass

    return {
        "proxy": "healthy",
        "mode": _active_mode,
        "rules_loaded": guardrail_engine.total_rules,
        "target_healthy": target_healthy,
        "target_url": TARGET_URL,
    }


@app.post("/chat", response_model=ProxyChatResponse)
async def chat(request: ProxyChatRequest) -> ProxyChatResponse:
    """
    Endpoint de chat con guardrail.

    Flujo:
    1. Evalúa el mensaje con el guardrail activo
    2. Si UNSAFE → retorna respuesta de bloqueo
    3. Si SAFE → reenvía al target y retorna la respuesta
    """
    # Evaluar con guardrail (async: RULE ~<50ms, JUDGE ~3-8s)
    decision = await _evaluate_message(request.message)

    if not decision.allow:
        # BLOQUEADO — no reenviar al target
        return ProxyChatResponse(
            session_id=request.session_id or "blocked",
            response=BLOCK_RESPONSE,
            model=TARGET_MODEL,
            guardrail=GuardrailInfo(
                mode=decision.mode,
                allowed=False,
                reason=decision.reason,
                matched_pattern=decision.matched_pattern,
                category=decision.category,
                confidence=decision.confidence,
                latency_ms=decision.latency_ms,
            ),
        )

    # PERMITIDO — reenviar al target
    async with httpx.AsyncClient(timeout=120.0) as client:
        payload = {"message": request.message}
        if request.session_id:
            payload["session_id"] = request.session_id

        resp = await client.post(f"{TARGET_URL}/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return ProxyChatResponse(
        session_id=data["session_id"],
        response=data["response"],
        model=data.get("model", TARGET_MODEL),
        guardrail=GuardrailInfo(
            mode=decision.mode,
            allowed=True,
            reason=decision.reason,
            category=decision.category,
            confidence=decision.confidence,
            latency_ms=decision.latency_ms,
        ),
    )


@app.post("/chat/with-document", response_model=ProxyChatResponse)
async def chat_with_document(request: ProxyDocumentRequest) -> ProxyChatResponse:
    """
    Endpoint de chat con documento — evalúa AMBOS: mensaje y documento.

    Para V3 (indirect injection), la instrucción maliciosa viene
    en el documento, no en el mensaje. Por eso evaluamos los dos.
    """
    # Evaluar mensaje del usuario y documento secuencialmente
    # (no en paralelo: el Lock de VRAM solo permite una llamada al juez a la vez)
    decision_msg = await _evaluate_message(request.message)
    decision_doc = await _evaluate_message(request.document)

    # Si cualquiera de los dos es UNSAFE, bloquear
    if not decision_msg.allow or not decision_doc.allow:
        blocking_decision = decision_doc if not decision_doc.allow else decision_msg
        return ProxyChatResponse(
            session_id=request.session_id or "blocked",
            response=BLOCK_RESPONSE,
            model=TARGET_MODEL,
            guardrail=GuardrailInfo(
                mode=blocking_decision.mode,
                allowed=False,
                reason=blocking_decision.reason,
                matched_pattern=blocking_decision.matched_pattern,
                category=blocking_decision.category,
                confidence=blocking_decision.confidence,
                latency_ms=decision_msg.latency_ms + decision_doc.latency_ms,
            ),
        )

    # PERMITIDO — reenviar al target
    async with httpx.AsyncClient(timeout=120.0) as client:
        payload = {
            "message": request.message,
            "document": request.document,
        }
        if request.session_id:
            payload["session_id"] = request.session_id

        resp = await client.post(f"{TARGET_URL}/chat/with-document", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return ProxyChatResponse(
        session_id=data["session_id"],
        response=data["response"],
        model=data.get("model", TARGET_MODEL),
        guardrail=GuardrailInfo(
            mode=decision_msg.mode,
            allowed=True,
            reason="Mensaje y documento seguros",
            confidence=1.0,
            latency_ms=decision_msg.latency_ms + decision_doc.latency_ms,
        ),
    )


async def _evaluate_message(message: str) -> GuardrailDecision:
    """
    Evalúa un mensaje con el motor de guardrail activo.

    Es async para soportar el modo JUDGE (llamada a gemma3:1b).
    En modo RULE retorna casi instantáneamente (regex en CPU).
    """
    return await guardrail_engine.evaluate(message)
