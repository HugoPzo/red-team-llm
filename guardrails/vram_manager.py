"""
Gestor de VRAM — asyncio.Lock compartido.

Garantiza que solo un modelo ocupe la GPU a la vez, cumpliendo
el constraint de 4 GB del BRIEF §3:

    "los dos modelos NO corren simultáneamente en GPU"

Mecanismo de dos capas:
1. keep_alive=0 en cada llamada a Ollama → el modelo se descarga
   de VRAM inmediatamente al terminar la inferencia
2. asyncio.Lock → nivel de aplicación: ninguna coroutine puede
   iniciar una llamada al juez mientras otra esté en curso

El Lock es necesario aunque keep_alive=0 ya garantice la descarga
porque entre request de judge y carga del modelo principal puede
haber una ventana de solapamiento en sistemas con alta concurrencia.

Ejemplo de uso:
    from guardrails.vram_manager import get_vram_lock

    async with get_vram_lock():
        # única sección que usa GPU
        result = await ollama_call_to_judge(message)
    # lock liberado, VRAM disponible para el modelo principal
"""

import asyncio

# Singleton del lock — se crea al importar el módulo.
# Python 3.10+ permite crear asyncio.Lock a nivel de módulo
# sin necesidad de un event loop activo.
_vram_lock: asyncio.Lock = asyncio.Lock()


def get_vram_lock() -> asyncio.Lock:
    """
    Retorna el lock global de VRAM.

    El mismo objeto es compartido por todas las instancias del proxy
    que corran en el mismo proceso (que es nuestro caso con uvicorn
    en modo single-worker).
    """
    return _vram_lock
