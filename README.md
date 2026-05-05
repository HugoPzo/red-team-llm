# Red Teaming sobre Modelos de Lenguaje Locales

Proyecto Final — Temas Especiales de Seguridad Informática
Ingeniería en Computación, UNAM — FES Aragón

---

## Descripcion

Entorno de Red Teaming sobre un LLM ejecutado localmente con Ollama, que demuestra la explotabilidad de vulnerabilidades del **OWASP LLM Top 10 (2025)** y la efectividad comparativa de mecanismos de defensa basados en Guardrails.

El sistema compara cuantitativamente tres escenarios bajo condiciones identicas de hardware:

| Modo | Descripcion |
|---|---|
| NONE | Sin defensa — baseline vulnerable |
| RULE | Guardrail basado en regex + heuristicas |
| JUDGE | Guardrail LLM-as-Judge (gemma3:1b clasificador) |

---

## Hardware y modelos

- **GPU:** NVIDIA GTX 1650 Ti — 4 GB VRAM
- **Modelo principal:** `gemma3:4b` (Q4_K_M, ~3.5 GB VRAM)
- **Modelo juez:** `gemma3:1b` (Q4, ~1.5 GB VRAM)
- Los dos modelos **nunca corren simultaneamente** en GPU (`keep_alive: 0` + `asyncio.Lock`)

---

## Arquitectura

```
red-team-llm/
├── target/          # Chatbot ARIA (FastAPI, puerto 8000) — intencionalmente vulnerable
├── attacker/        # Red Team Agent — 5 vectores OWASP automatizados
│   └── vectors/     # V1-V5: Direct Injection, Jailbreak, Indirect, Extraction, Context
├── guardrails/      # Middleware proxy (FastAPI, puerto 8001) — Rule-Based y LLM-Judge
├── data/            # SQLite (results.db) + JSON logs por sesion
├── dashboard/       # Streamlit (puerto 8501) — 3 vistas + chat interactivo con ARIA
├── scripts/         # Script de reproducibilidad completa
├── config.py        # Modelos, endpoints, umbrales
└── requirements.txt
```

### Sistema bajo prueba — ARIA

Chatbot corporativo simulado de RRHH (TecnoAragon S.A.) con PII y credenciales ficticias en el system prompt. Sin defensas propias, expuesto via FastAPI en `/chat`, `/chat/with-document` y `/chat/history/{id}`.

### Vectores de ataque implementados

| ID | OWASP | Tecnica | Variantes |
|---|---|---|---|
| V1 | LLM01 | Direct Prompt Injection | 3 |
| V2 | LLM01 | Jailbreak por Roleplay | 3 |
| V3 | LLM02 | Indirect Prompt Injection (via documento) | 3 |
| V4 | LLM06 | System Prompt Extraction (multi-turno) | 3 |
| V5 | LLM08 | Context Window Manipulation | 3 |

---

## Resultados

| Modo | SUCCESS | BLOCKED | Tasa de bloqueo |
|---|---|---|---|
| NONE | 10/15 (67%) | 5/15 (33%) | 33% (solo ShieldGemma) |
| RULE | 2/15 (13%) | 13/15 (87%) | 87% |
| JUDGE | 4/15 (26%) | 11/15 (73%) | 73% |

**Hallazgo clave:** RULE y JUDGE son complementarios.
- RULE bloquea ataques explicitos con palabras clave (latencia: 9–43 ms).
- JUDGE bloquea ataques semanticos/indirectos que evaden regex (latencia: ~2,500–4,700 ms).
- Combinados habrian bloqueado 14/15 ataques (93%).

---

## Stack tecnologico

| Capa | Tecnologia |
|---|---|
| Inferencia LLM | Ollama (localhost:11434) |
| Backend API | FastAPI + Python 3.11 |
| Cliente HTTP | httpx (async) |
| Validacion | Pydantic |
| Persistencia | SQLite + JSON logs |
| Dashboard | Streamlit |
| CLI output | rich |

---

## Ejecucion rapida

```bash
# 1. Sistema bajo prueba (Target)
uvicorn target.main:app --host 0.0.0.0 --port 8000

# 2. Guardrail proxy (elegir modo: NONE | RULE | JUDGE)
GUARDRAIL_MODE=RULE uvicorn guardrails.proxy:app --host 0.0.0.0 --port 8001

# 3. Campana de ataques
python -m attacker.attack_runner --mode RULE

# 4. Dashboard
streamlit run dashboard/app.py --server.port 8501
```

---

## Referencia normativa

- OWASP Top 10 for LLM Applications (2025)
- NIST AI 100-2 (2024) — taxonomia de ataques adversariales
- Modalidad: caja negra, fase de inferencia, tipo targeted