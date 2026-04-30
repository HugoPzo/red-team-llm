"""
Motor de chat para el chatbot ARIA.

Gestiona la comunicación async con Ollama y el historial de conversación
por sesión. Este módulo es el cliente directo al LLM — sin defensas.

Ejemplo de uso:
    engine = ChatEngine()
    response = await engine.chat("session-1", "¿Cuáles son los beneficios?")
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from pydantic import BaseModel, Field

# Importaciones del proyecto
import sys
from pathlib import Path

# Asegurar que config.py sea importable desde cualquier ubicación
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    OLLAMA_BASE_URL,
    TARGET_MODEL,
    TARGET_TEMPERATURE,
    TARGET_NUM_PREDICT,
)


# ===== Modelos Pydantic =====


class ChatMessage(BaseModel):
    """Representa un mensaje individual en la conversación."""

    role: str = Field(..., pattern="^(system|user|assistant)$")
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatRequest(BaseModel):
    """Solicitud de chat entrante al endpoint."""

    message: str = Field(..., min_length=1, max_length=32768)
    session_id: Optional[str] = Field(
        default=None,
        description="ID de sesión existente. Si es None, se crea una nueva.",
    )


class ChatResponse(BaseModel):
    """Respuesta del chatbot al usuario."""

    session_id: str
    response: str
    model: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DocumentChatRequest(BaseModel):
    """Solicitud de chat con documento adjunto (para V3 — indirect injection)."""

    message: str = Field(..., min_length=1, max_length=4096)
    document: str = Field(
        ...,
        min_length=1,
        max_length=8192,
        description="Contenido del documento a analizar.",
    )
    session_id: Optional[str] = Field(default=None)


# ===== Motor de Chat =====


class ChatEngine:
    """
    Cliente async para Ollama que gestiona sesiones de conversación.

    Cada sesión mantiene su propio historial de mensajes en memoria.
    El system prompt de ARIA se inyecta al inicio de cada sesión nueva.

    Decisión de diseño: el historial vive en memoria (dict) porque el
    Target NO necesita persistencia propia — la persistencia la maneja
    la Capa 4 (data/). Esto mantiene la Capa 1 simple e independiente.
    """

    def __init__(self) -> None:
        # Historial en memoria: session_id -> lista de mensajes
        self._sessions: dict[str, list[ChatMessage]] = {}
        # Cargar system prompt desde archivo
        prompt_path = Path(__file__).resolve().parent / "system_prompt.txt"
        self._system_prompt: str = prompt_path.read_text(encoding="utf-8")

    def _get_or_create_session(self, session_id: Optional[str] = None) -> str:
        """
        Obtiene una sesión existente o crea una nueva con el system prompt.

        Si session_id es None, genera un UUID nuevo.
        Si la sesión no existe, la inicializa con el system prompt de ARIA.
        """
        if session_id is None:
            session_id = str(uuid.uuid4())

        if session_id not in self._sessions:
            # Inicializar sesión con el system prompt
            self._sessions[session_id] = [
                ChatMessage(role="system", content=self._system_prompt)
            ]

        return session_id

    async def chat(self, message: str, session_id: Optional[str] = None) -> ChatResponse:
        """
        Envía un mensaje al modelo y retorna la respuesta.

        Flujo:
        1. Obtiene o crea la sesión
        2. Agrega el mensaje del usuario al historial
        3. Envía TODO el historial a Ollama (contexto completo)
        4. Agrega la respuesta del modelo al historial
        5. Retorna la respuesta formateada

        Importante: enviar el historial completo permite ataques multi-turno
        (V4 — System Prompt Extraction), que es intencional.
        """
        session_id = self._get_or_create_session(session_id)

        # Agregar mensaje del usuario al historial
        self._sessions[session_id].append(
            ChatMessage(role="user", content=message)
        )

        # Construir payload para Ollama API (formato /api/chat)
        ollama_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in self._sessions[session_id]
        ]

        payload = {
            "model": TARGET_MODEL,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "temperature": TARGET_TEMPERATURE,
                "num_predict": TARGET_NUM_PREDICT,
            },
        }

        # Llamada async a Ollama
        async with httpx.AsyncClient(
            base_url=OLLAMA_BASE_URL, timeout=120.0
        ) as client:
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()

        assistant_content: str = result["message"]["content"]

        # Agregar respuesta del modelo al historial
        self._sessions[session_id].append(
            ChatMessage(role="assistant", content=assistant_content)
        )

        return ChatResponse(
            session_id=session_id,
            response=assistant_content,
            model=TARGET_MODEL,
        )

    async def chat_with_document(
        self, message: str, document: str, session_id: Optional[str] = None
    ) -> ChatResponse:
        """
        Chat con un documento adjunto — usado para Indirect Injection (V3).

        El documento se concatena al mensaje del usuario sin sanitización.
        Esto es intencional: simula el patrón real de RAG inseguro donde
        contenido externo se inyecta directamente al contexto del modelo.
        """
        # Combinar mensaje y documento SIN sanitización (vulnerable por diseño)
        combined_message = (
            f"{message}\n\n"
            f"=== DOCUMENTO ADJUNTO ===\n"
            f"{document}\n"
            f"=== FIN DEL DOCUMENTO ==="
        )

        return await self.chat(message=combined_message, session_id=session_id)

    def get_history(self, session_id: str) -> list[ChatMessage]:
        """
        Retorna el historial de una sesión (excluyendo el system prompt).

        Excluye el system prompt para no exponerlo directamente via API
        (aunque los ataques V4 intentarán extraerlo por otros medios).
        """
        if session_id not in self._sessions:
            return []

        # Filtrar el system prompt — no lo exponemos via API
        return [
            msg for msg in self._sessions[session_id] if msg.role != "system"
        ]

    def get_session_ids(self) -> list[str]:
        """Retorna todas las sesiones activas."""
        return list(self._sessions.keys())
