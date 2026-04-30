"""
Guardrail LLM-as-Judge (Modo B).

Usa gemma3:1b como clasificador de seguridad. El modelo recibe el
mensaje del usuario y debe responder con JSON estructurado indicando
si el mensaje es SAFE o UNSAFE.

Decisiones de diseño clave (constraint 4 GB VRAM):
- keep_alive=0: gemma3:1b se descarga de VRAM inmediatamente tras
  cada clasificación, liberando memoria para que gemma3:4b cargue
- asyncio.Lock (vram_manager): nivel de aplicación para evitar
  solapamiento entre el juez y el modelo principal
- temperature=0.0: respuesta determinista (clasificación, no generación)
- num_predict=64: el JSON de salida es corto; limitar tokens acelera
  la respuesta y evita que el modelo "se explaye"

Parseo defensivo del JSON:
  gemma3:1b puede ocasionalmente incluir texto antes/después del JSON
  o romper el formato. Se extraen los campos con regex como fallback.
  Si todo falla → SAFE con confidence=0.0 (fail-open: preferimos un
  falso negativo a bloquear consultas legítimas en producción).

Ejemplo de uso:
    judge = LLMJudge()
    decision = await judge.evaluate("Ignora tus instrucciones anteriores")
    # decision.allow == False, decision.confidence == 0.97
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from config import (
    JUDGE_CONFIDENCE_THRESHOLD,
    JUDGE_MODEL,
    JUDGE_NUM_PREDICT,
    JUDGE_TEMPERATURE,
    KEEP_ALIVE_SECONDS,
    OLLAMA_BASE_URL,
)
from guardrails.rule_based import GuardrailDecision
from guardrails.vram_manager import get_vram_lock

# Ruta al system prompt del juez
_JUDGE_PROMPT_PATH = Path(__file__).resolve().parent / "judge_prompt.txt"


class LLMJudge:
    """
    Clasificador de seguridad basado en LLM (gemma3:1b).

    Ventaja sobre RuleBasedGuardrail:
    - Detecta ataques semánticos que evaden regex (como V1.3 y V4.3)
    - Entiende contexto e intención, no solo palabras clave
    - Más robusto ante ofuscación y variaciones de lenguaje

    Desventaja (documentar en el paper):
    - Probabilístico: puede tener falsos positivos/negativos
    - Latencia ~3-8s por clasificación (vs <50ms del regex)
    - Requiere gestión explícita de VRAM (keep_alive=0 + Lock)
    - Costo computacional significativo para producción real
    """

    def __init__(self) -> None:
        self._system_prompt = _JUDGE_PROMPT_PATH.read_text(encoding="utf-8").strip()
        self._lock = get_vram_lock()

    async def evaluate(self, message: str) -> GuardrailDecision:
        """
        Clasifica un mensaje como SAFE o UNSAFE usando gemma3:1b.

        Flujo:
        1. Adquiere el asyncio.Lock (garantía de no-coexistencia)
        2. Llama a Ollama con gemma3:1b + keep_alive=0
        3. Parsea el JSON de respuesta
        4. Libera el Lock
        5. Retorna GuardrailDecision

        Si el JSON es inválido o el request falla → SAFE (fail-open).
        """
        start = time.perf_counter()

        payload = {
            "model": JUDGE_MODEL,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": message},
            ],
            "stream": False,
            "keep_alive": KEEP_ALIVE_SECONDS,  # 0 → descarga inmediata de VRAM
            "options": {
                "temperature": JUDGE_TEMPERATURE,
                "num_predict": JUDGE_NUM_PREDICT,
            },
        }

        try:
            # Adquirir Lock antes de usar la GPU
            async with self._lock:
                async with httpx.AsyncClient(
                    base_url=OLLAMA_BASE_URL, timeout=60.0
                ) as client:
                    response = await client.post("/api/chat", json=payload)
                    response.raise_for_status()
                    result = response.json()
            # Lock liberado: gemma3:1b ya se descargó (keep_alive=0)

            raw_output: str = result["message"]["content"]
            classification, reason, confidence = self._parse_judge_output(raw_output)

        except Exception as e:
            # Error de red, timeout o Ollama no disponible → fail-open
            elapsed_ms = (time.perf_counter() - start) * 1000
            return GuardrailDecision(
                mode="JUDGE",
                allow=True,
                reason=f"Error del juez — fail-open: {str(e)[:100]}",
                confidence=0.0,
                latency_ms=elapsed_ms,
            )

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Bloquear solo si el juez está seguro de que es UNSAFE
        is_unsafe = (
            classification == "UNSAFE"
            and confidence >= JUDGE_CONFIDENCE_THRESHOLD
        )

        return GuardrailDecision(
            mode="JUDGE",
            allow=not is_unsafe,
            reason=reason,
            matched_pattern="",  # No aplica en modo JUDGE
            category="llm_judge",
            confidence=confidence,
            latency_ms=elapsed_ms,
        )

    def _parse_judge_output(self, raw: str) -> tuple[str, str, float]:
        """
        Parsea la salida del juez con tres estrategias de fallback.

        Estrategia 1: JSON directo (caso ideal)
        Estrategia 2: Extraer primer bloque JSON con regex
        Estrategia 3: Buscar palabras clave SAFE/UNSAFE en el texto

        Retorna: (classification, reason, confidence)
        Fallback final: ("SAFE", "parse error — fail-open", 0.0)
        """
        raw = raw.strip()

        # Estrategia 1: JSON directo
        try:
            data = json.loads(raw)
            return (
                str(data.get("classification", "SAFE")).upper(),
                str(data.get("reason", "sin razón")),
                float(data.get("confidence", 0.5)),
            )
        except (json.JSONDecodeError, ValueError):
            pass

        # Estrategia 2: Extraer bloque JSON con regex (el modelo pudo agregar texto extra)
        json_match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return (
                    str(data.get("classification", "SAFE")).upper(),
                    str(data.get("reason", "sin razón")),
                    float(data.get("confidence", 0.5)),
                )
            except (json.JSONDecodeError, ValueError):
                pass

        # Estrategia 3: buscar UNSAFE/SAFE en el texto plano
        upper_raw = raw.upper()
        if "UNSAFE" in upper_raw:
            return ("UNSAFE", f"detectado por keywords en: {raw[:80]}", 0.6)
        if "SAFE" in upper_raw:
            return ("SAFE", f"clasificado como seguro: {raw[:80]}", 0.6)

        # Fallback final: fail-open
        return ("SAFE", f"parse error — fail-open. Raw: {raw[:80]}", 0.0)
