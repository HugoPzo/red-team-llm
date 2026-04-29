# PROGRESS — Estado actual del proyecto

> Este archivo se actualiza al final de cada sesión de trabajo.
> El asistente de código debe leerlo al inicio para no perder contexto.

---

## Estado actual

- **Fase activa:** F0 — Setup del entorno ✅ COMPLETADA
- **Última actualización:** 2026-04-28
- **Próximo hito:** F1 — Sistema vulnerable (Target) — implementar chatbot ARIA

---

## Decisiones arquitectónicas tomadas

> Registra aquí las decisiones no triviales para no re-discutirlas.
> Formato: fecha — decisión — justificación.

- 2026-04-28 — Ollama instalado directo sobre SO (sin Docker) — alineado con constraint del BRIEF §3
- 2026-04-28 — `keep_alive: 0` definido en config.py — garantiza descarga de modelos de VRAM después de cada request, cumpliendo regla de no-coexistencia
- 2026-04-28 — Verificación VRAM via API HTTP (`/api/generate` con `keep_alive:0`) en lugar de CLI — más limpio y reproducible para el código futuro

---

## Deuda técnica conocida

> TODOs que conscientemente postergamos. No son bugs; son "lo dejamos para después".

- _(vacío)_

---

## Bitácora por fase

### F0 — Setup del entorno
**Estado:** [x] completada

Tareas:
- [x] Instalar Ollama (v0.22.0)
- [x] `ollama pull gemma3:4b` (3.3 GB, Q4_K_M)
- [x] `ollama pull gemma3:1b` (815 MB, Q4)
- [x] Verificar VRAM con `nvidia-smi` (GTX 1650 Ti, 4096 MiB, driver 580.142, CUDA 13.0)
- [x] Crear estructura de directorios (target/, attacker/vectors/, guardrails/, data/logs/, dashboard/, docs/, scripts/)
- [x] Inicializar Git + `requirements.txt` + `config.py` + `.gitignore`
- [x] Conversación manual exitosa con cada modelo

Evidencia generada:
- `nvidia-smi`: GTX 1650 Ti confirmada, 52 MiB base usage
- `ollama list`: ambos modelos descargados y verificados
- API test `gemma3:4b`: responde "Hola, soy Gemma 4B y funciono correctamente."
- API test `gemma3:1b`: responde "Hola, soy Gemma 1B."
- VRAM post-descarga: 1015 MiB (solo overhead del sistema)

Notas:
- La primera carga de gemma3:4b tarda ~30s (carga a GPU). Requests posteriores son más rápidos mientras el modelo esté en VRAM.
- gemma3:1b es notablemente más conciso en sus respuestas (esperado para 1B params).
- `keep_alive: 0` funciona correctamente para descargar modelos de VRAM.

---

### F1 — Sistema vulnerable (Target)
**Estado:** [ ] no iniciada

Tareas:
- [ ] Redactar `target/system_prompt.txt`
- [ ] Implementar `target/chat_engine.py`
- [ ] Implementar `target/main.py` con endpoints
- [ ] Probar con `curl` o cliente HTTP

---

### F2 — V1 Direct Injection
**Estado:** [ ] no iniciada

Tareas:
- [ ] `attacker/vectors/base.py` con `BaseAttack` y `AttackResult`
- [ ] `attacker/vectors/v1_direct_injection.py` con 3 variantes
- [ ] `attacker/attack_runner.py` mínimo
- [ ] Primera ejecución exitosa registrada

---

### F3 — Vectores V2-V5
**Estado:** [ ] no iniciada

Tareas:
- [ ] V2 Jailbreak por Roleplay
- [ ] V3 Indirect Injection (con documento)
- [ ] V4 System Prompt Extraction (multi-turno)
- [ ] V5 Context Window Manipulation
- [ ] Tabla de resultados en modo NONE

---

### F4 — Persistencia y logging
**Estado:** [ ] no iniciada

Tareas:
- [ ] `data/schema.sql` ejecutado
- [ ] Logging integrado en attack_runner
- [ ] JSON logs por sesión
- [ ] Queries SQL del dashboard validadas

---

### F5 — Guardrail Rule-Based
**Estado:** [ ] no iniciada

Tareas:
- [ ] `guardrails/rule_based.py` con todas las categorías
- [ ] `guardrails/guardrail_engine.py` modo RULE
- [ ] `guardrails/proxy.py` FastAPI en puerto 8001
- [ ] Comparativa NONE vs RULE en los 5 vectores

---

### F6 — Guardrail LLM-as-Judge + VRAM
**Estado:** [ ] no iniciada

Tareas:
- [ ] `guardrails/judge_prompt.txt`
- [ ] `guardrails/llm_judge.py`
- [ ] `guardrails/vram_manager.py` con asyncio.Lock
- [ ] Validar que ambos modelos no coexistan en VRAM
- [ ] Métricas de latencia y VRAM peak capturadas
- [ ] Comparativa final NONE / RULE / JUDGE

---

### F7 — Dashboard
**Estado:** [ ] no iniciada

Tareas:
- [ ] Vista 1 — Resumen ejecutivo
- [ ] Vista 2 — Detalle por ataque
- [ ] Vista 3 — Métricas de defensa
- [ ] Screenshots de las 3 vistas

---

### F8 — Documento académico
**Estado:** [ ] no iniciada

Tareas:
- [ ] Marco teórico con referencias OWASP/NIST
- [ ] Metodología y arquitectura
- [ ] Análisis de resultados
- [ ] Discusión de limitaciones (incluida ShieldGemma)
- [ ] Referencias en formato APA
- [ ] Capturas de evidencia integradas

---

## Comando rápido para retomar el trabajo

```
Lee PROJECT_BRIEF.md y PROGRESS.md.
Confirma en qué fase estamos y qué tarea sigue.
Pregúntame antes de empezar a escribir código.
```
