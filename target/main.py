"""
API FastAPI del chatbot vulnerable ARIA (Capa 1 — Target).

Expone los endpoints del sistema bajo prueba. Este servicio NO tiene
defensas propias — es intencionalmente vulnerable para permitir
que los vectores de ataque (V1-V5) lo exploten.

Ejecución:
    uvicorn target.main:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    POST /chat              — Conversación estándar
    POST /chat/with-document — Chat con documento adjunto (para V3)
    GET  /chat/history/{id}  — Historial de una sesión
    GET  /health             — Health check
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from target.chat_engine import (
    ChatEngine,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    DocumentChatRequest,
)

# Importar config para el puerto (usado en el bloque __main__)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import TARGET_PORT


# ===== Modelos de respuesta adicionales =====


class HistoryResponse(BaseModel):
    """Respuesta del endpoint de historial."""

    session_id: str
    messages: list[ChatMessage]
    message_count: int


class HealthResponse(BaseModel):
    """Respuesta del health check."""

    status: str = "ok"
    service: str = "aria-chatbot"
    model: str = ""


class ErrorResponse(BaseModel):
    """Respuesta de error estandarizada."""

    detail: str


# ===== Instancia global del motor de chat =====

# Decisión: instancia global porque FastAPI es single-process con async.
# El historial en memoria vive mientras el servidor esté corriendo.
engine = ChatEngine()


# ===== Lifespan (ciclo de vida de la app) =====


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Gestión del ciclo de vida de la aplicación.

    Al iniciar: log de arranque.
    Al cerrar: limpieza (futuro: flush de sesiones si se necesita).
    """
    print("🤖 ARIA Chatbot iniciado — Sistema vulnerable (sin defensas)")
    print(f"   Modelo: {engine._system_prompt[:50]}...")
    yield
    print("🤖 ARIA Chatbot detenido")


# ===== Aplicación FastAPI =====

app = FastAPI(
    title="ARIA — Chatbot RRHH de TecnoAragón S.A.",
    description=(
        "Sistema de chat vulnerable para pruebas de Red Teaming. "
        "Este servicio NO tiene defensas propias."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ===== Endpoints =====


@app.post(
    "/chat",
    response_model=ChatResponse,
    summary="Chat estándar con ARIA",
    responses={500: {"model": ErrorResponse}},
)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Endpoint principal de conversación.

    Recibe un mensaje y opcionalmente un session_id para continuar
    una conversación existente. Si no se envía session_id, se crea
    una sesión nueva.

    Este endpoint no tiene ninguna validación de seguridad sobre
    el contenido del mensaje — es vulnerable por diseño.
    """
    try:
        response = await engine.chat(
            message=request.message,
            session_id=request.session_id,
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/chat/with-document",
    response_model=ChatResponse,
    summary="Chat con documento adjunto",
    responses={500: {"model": ErrorResponse}},
)
async def chat_with_document(request: DocumentChatRequest) -> ChatResponse:
    """
    Chat con un documento adjunto — vector de Indirect Injection (V3).

    El contenido del documento se inyecta directamente al contexto
    del modelo sin sanitización. Simula un pipeline RAG inseguro.
    """
    try:
        response = await engine.chat_with_document(
            message=request.message,
            document=request.document,
            session_id=request.session_id,
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/chat/history/{session_id}",
    response_model=HistoryResponse,
    summary="Obtener historial de sesión",
    responses={404: {"model": ErrorResponse}},
)
async def get_history(session_id: str) -> HistoryResponse:
    """
    Retorna el historial de mensajes de una sesión.

    El system prompt NO se incluye en la respuesta (aunque los
    ataques V4 intentarán extraerlo por otros medios).
    """
    history = engine.get_history(session_id)

    if not history and session_id not in engine.get_session_ids():
        raise HTTPException(status_code=404, detail=f"Sesión '{session_id}' no encontrada")

    return HistoryResponse(
        session_id=session_id,
        messages=history,
        message_count=len(history),
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check del servicio",
)
async def health() -> HealthResponse:
    """Verifica que el servicio esté corriendo."""
    from config import TARGET_MODEL

    return HealthResponse(status="ok", service="aria-chatbot", model=TARGET_MODEL)


# ===== Ejecución directa =====

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "target.main:app",
        host="0.0.0.0",
        port=TARGET_PORT,
        reload=True,
    )
