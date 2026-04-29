# PROJECT BRIEF — Red Teaming sobre Modelos de Lenguaje Locales

> **Lectura obligatoria al inicio de cada sesión.**
> Este archivo define qué se construye, bajo qué restricciones y con qué estándares.
> No modificar sin justificación. Para estado actual ver `PROGRESS.md`.

---

## 1. Identidad del proyecto

- **Título:** Red Teaming sobre Modelos de Lenguaje Locales: Ataques de Prompt Injection y Defensa con Guardrails
- **Materia:** Temas Especiales de Seguridad Informática
- **Carrera:** Ingeniería en Computación
- **Institución:** UNAM — FES Aragón
- **Dominio:** AI Security / Adversarial Machine Learning
- **Naturaleza:** Académico, controlado, 100% local, sin APIs externas

## 2. Objetivo

Diseñar, implementar y evaluar un entorno de Red Teaming sobre un LLM ejecutado
localmente con Ollama, demostrando la explotabilidad de vulnerabilidades del
OWASP LLM Top 10 (2025) y la efectividad comparativa de mecanismos de defensa
basados en Guardrails.

El sistema debe permitir comparar cuantitativamente tres escenarios bajo
condiciones idénticas de hardware:

1. Sin defensa (baseline)
2. Defensa Rule-Based (regex + heurísticas)
3. Defensa LLM-as-Judge (modelo clasificador)

## 3. Restricciones de hardware (CRÍTICAS — no negociables)

- **GPU:** NVIDIA GTX 1650 Ti — 4 GB VRAM (GDDR6)
- **Modelo principal:** `gemma3:4b` con cuantización Q4_K_M (~3.5 GB VRAM)
- **Modelo juez:** `gemma3:1b` Q4 (~1.5 GB VRAM)
- **Regla absoluta:** los dos modelos NO corren simultáneamente en GPU.
  El judge debe descargarse antes de que el modelo principal se cargue.
- **Fallbacks aceptados:** `phi4-mini`, `llama3.2:3b` Q4
- **Prohibido:** modelos > 4B parámetros bajo cualquier circunstancia
- Ollama corre directo sobre el SO (sin Docker)

El constraint de 4 GB **es parte del aporte académico**, no una limitación
a esconder. Demostrar Red Teaming profesional en hardware de consumo es valioso.

## 4. Stack tecnológico

| Capa | Tecnología |
|---|---|
| Inferencia LLM | Ollama (localhost:11434) |
| Backend API | Python 3.11+ con FastAPI |
| Cliente HTTP | httpx (async) |
| Validación | pydantic |
| Persistencia | SQLite + JSON logs |
| Dashboard | Streamlit |
| CLI output | rich |
| Versionado | Git |

## 5. Arquitectura — tres capas + dashboard

### Capa 1 — Sistema bajo prueba (`target/`)
Chatbot corporativo simulado "ARIA" (RRHH ficticio de TecnoAragón S.A.) con:
- System prompt con datos PII y credenciales ficticias
- FastAPI exponiendo `/chat`, `/chat/with-document`, `/chat/history/{id}`
- Historial de sesión en memoria
- **Sin defensas propias** (intencionalmente vulnerable)
- Puerto 8000

### Capa 2 — Red Team Agent (`attacker/`)
Orquestador que ejecuta automatizadamente 5 vectores OWASP LLM:

| ID | OWASP | Técnica |
|---|---|---|
| V1 | LLM01 | Direct Prompt Injection |
| V2 | LLM01 | Jailbreak por Roleplay |
| V3 | LLM02 | Indirect Prompt Injection (vía documento) |
| V4 | LLM06 | System Prompt Extraction (multi-turno) |
| V5 | LLM08 | Context Window Manipulation |

Cada vector implementa la clase `BaseAttack` con:
- 3 variantes de payload (creciente sofisticación)
- Criterio de éxito medible (regex, similitud, etc.)
- Clasificación: SUCCESS / PARTIAL / BLOCKED
- Registro de qué actor bloqueó: NONE / SHIELD_GEMMA / GUARDRAIL_RULE / GUARDRAIL_JUDGE

### Capa 3 — Guardrail Layer (`guardrails/`)
Middleware proxy interpuesto antes del modelo principal. Puerto 8001.

**Modo A — Rule-Based:**
- Regex agrupados por categoría: instruction_override, role_hijack,
  delimiter_injection, extraction_attempt, credential_request, grandma_jailbreak
- Patrones en español e inglés
- Costo: solo CPU

**Modo B — LLM-as-Judge:**
- `gemma3:1b` con system prompt de clasificación
- Salida JSON forzada: `{"classification": "SAFE"|"UNSAFE", "reason": str, "confidence": float}`
- temperature=0.0, num_predict=64
- **Gestión explícita de VRAM:** `keep_alive: 0` + `asyncio.Lock` compartido

### Dashboard (`dashboard/`)
Streamlit en puerto 8501, lectura solo de SQLite. Tres vistas:
1. Resumen ejecutivo (pivote vector × modo)
2. Detalle por ataque (con payload y respuesta)
3. Métricas de defensa (latencia, detección, falsos positivos)

## 6. Estructura de directorios

```
red-team-llm/
├── target/
│   ├── main.py                     # FastAPI app vulnerable
│   ├── chat_engine.py              # Cliente Ollama
│   └── system_prompt.txt           # Prompt del chatbot ARIA
├── attacker/
│   ├── attack_runner.py            # Orquestador
│   └── vectors/
│       ├── base.py                 # BaseAttack + AttackResult
│       ├── v1_direct_injection.py
│       ├── v2_jailbreak_roleplay.py
│       ├── v3_indirect_injection.py
│       ├── v4_prompt_extraction.py
│       └── v5_context_manipulation.py
├── guardrails/
│   ├── proxy.py                    # FastAPI proxy en puerto 8001
│   ├── guardrail_engine.py         # Selector de modo
│   ├── rule_based.py               # Modo A
│   ├── llm_judge.py                # Modo B
│   ├── vram_manager.py             # asyncio.Lock compartido
│   └── judge_prompt.txt            # System prompt del juez
├── data/
│   ├── schema.sql
│   ├── results.db                  # SQLite (gitignored)
│   └── logs/                       # JSON por sesión
├── dashboard/
│   └── app.py                      # Streamlit
├── docs/                           # Capturas y evidencia
├── scripts/
│   └── run_full_experiment.sh      # Reproducibilidad
├── config.py                       # Modelos, endpoints, umbrales
├── requirements.txt
├── PROJECT_BRIEF.md                # Este archivo
├── PROGRESS.md                     # Estado actual
└── README.md
```

## 7. Esquema de base de datos

```sql
sessions(session_id PK, started_at, guardrail_mode, target_model, notes)

attacks(attack_id PK, session_id FK, vector_id, owasp_category,
        variant, payload, timestamp)

guardrail_decisions(decision_id PK, attack_id FK, mode, allow,
                    reason, matched_pattern, confidence, latency_ms)

results(result_id PK, attack_id FK, response_text, classification,
        blocked_by, total_latency_ms, vram_peak_mb)
```

## 8. Estándares y referencias normativas

- **OWASP Top 10 for LLM Applications (2025)** — clasificación de vectores
- **NIST AI 100-2 (2024)** — taxonomía de ataques adversariales
- Modalidad: caja negra, fase de inferencia, tipo targeted
- Considerar que **ShieldGemma** (filtro nativo de Gemma 3) puede bloquear
  ataques antes del guardrail. Esto es evidencia válida y debe registrarse
  en el campo `blocked_by`.

## 9. Estándares de código

- Python 3.11+ con type hints obligatorios
- Pydantic para todos los modelos de datos cruzando capas
- async/await en todo lo que toque la red (FastAPI, httpx)
- Cada componente debe poder ejecutarse de forma independiente
- Logs estructurados (JSON) además de SQLite
- Comentarios en español, código en inglés (variables, funciones, clases)
- No optimización prematura: claridad > performance
- Cada módulo público necesita docstring con propósito y ejemplo de uso

## 10. Fases del proyecto

| Fase | Entregable de evidencia |
|---|---|
| F0 — Setup | `ollama list` con ambos modelos, conversación manual exitosa |
| F1 — Target vulnerable | `curl` contra `/chat` con respuesta legítima |
| F2 — V1 Direct Injection | Primera demo de injection registrada |
| F3 — V2-V5 vectores | Tabla con los 5 vectores en modo NONE |
| F4 — SQLite + logging | DB poblada, queries del dashboard funcionan |
| F5 — Rule-Based | Comparativa cuantitativa NONE vs RULE |
| F6 — LLM-as-Judge + VRAM | Las 3 modalidades con métricas de latencia y VRAM |
| F7 — Dashboard | 3 vistas con screenshots |
| F8 — Documento académico | Entregable final |

## 11. Reglas de interacción para el asistente de código

**Al inicio de cada sesión:**
1. Leer este archivo (`PROJECT_BRIEF.md`) completo
2. Leer `PROGRESS.md` para conocer el estado actual
3. Confirmar al usuario en qué fase está y qué viene después
4. NO avanzar a una fase siguiente sin que la anterior tenga evidencia

**Durante la implementación:**
- Respetar la estructura de directorios sin desviaciones
- Si se detecta un constraint que no está documentado, preguntar antes de asumir
- Generar código que pueda ejecutarse de forma aislada
- Después de cada cambio significativo, sugerir actualización de `PROGRESS.md`
- No introducir dependencias nuevas sin justificarlas

**Al cerrar una fase:**
- Listar la evidencia generada
- Proponer el contenido a escribir en `PROGRESS.md`
- Identificar deuda técnica generada (si la hay)

## 12. Lo que NO se debe hacer

- No usar APIs externas (OpenAI, Anthropic, etc.)
- No usar modelos > 4B parámetros
- No mantener el judge y el modelo principal cargados simultáneamente
- No introducir defensas en la Capa 1 (Target debe seguir vulnerable)
- No optimizar antes de tener mediciones
- No saltarse la persistencia: todo ataque ejecutado debe quedar en SQLite
- No usar datos reales de personas; todo es ficticio
