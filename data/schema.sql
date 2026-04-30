-- ===== Esquema de base de datos — Red Team LLM =====
-- Referencia: PROJECT_BRIEF.md §7
--
-- Cuatro tablas normalizadas para registrar campañas de ataque:
-- 1. sessions: una por ejecución del runner (agrupa ataques bajo un modo)
-- 2. attacks: un registro por cada payload enviado
-- 3. guardrail_decisions: decisión del guardrail (si aplica, F5/F6)
-- 4. results: respuesta del modelo y clasificación del ataque
--
-- Relaciones:
--   sessions 1───N attacks 1───1 results
--                          1───0..1 guardrail_decisions
--
-- Se usa INTEGER PRIMARY KEY AUTOINCREMENT para IDs numéricos.
-- Los timestamps son ISO 8601 (TEXT en SQLite).

-- Tabla de sesiones: agrupa una ejecución completa del runner
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,           -- UUID generado por el runner
    started_at      TEXT NOT NULL,              -- ISO 8601
    finished_at     TEXT,                       -- NULL si no ha terminado
    guardrail_mode  TEXT NOT NULL DEFAULT 'NONE', -- NONE | RULE | JUDGE
    target_model    TEXT NOT NULL,              -- ej: gemma3:4b
    notes           TEXT DEFAULT ''             -- observaciones libres
);

-- Tabla de ataques: un registro por cada payload enviado
CREATE TABLE IF NOT EXISTS attacks (
    attack_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,              -- FK → sessions
    vector_id       TEXT NOT NULL,              -- V1, V2, V3, V4, V5
    owasp_category  TEXT NOT NULL,              -- LLM01, LLM02, LLM06, LLM08
    variant         INTEGER NOT NULL,           -- 1, 2 o 3
    payload         TEXT NOT NULL,              -- texto completo enviado
    timestamp       TEXT NOT NULL,              -- ISO 8601
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

-- Tabla de decisiones del guardrail (se puebla en F5/F6)
-- Relación 0..1 con attacks: solo existe si hay guardrail activo
CREATE TABLE IF NOT EXISTS guardrail_decisions (
    decision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    attack_id       INTEGER NOT NULL UNIQUE,    -- FK → attacks (1:1)
    mode            TEXT NOT NULL,              -- RULE | JUDGE
    allow           INTEGER NOT NULL,           -- 1 = permitió, 0 = bloqueó
    reason          TEXT DEFAULT '',            -- explicación del guardrail
    matched_pattern TEXT DEFAULT '',            -- patrón regex que matcheó (modo RULE)
    confidence      REAL DEFAULT 0.0,          -- confianza del juez (modo JUDGE)
    latency_ms      REAL NOT NULL DEFAULT 0.0, -- tiempo de evaluación del guardrail
    FOREIGN KEY (attack_id) REFERENCES attacks(attack_id)
);

-- Tabla de resultados: respuesta y clasificación de cada ataque
CREATE TABLE IF NOT EXISTS results (
    result_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    attack_id       INTEGER NOT NULL UNIQUE,    -- FK → attacks (1:1)
    response_text   TEXT NOT NULL,              -- respuesta completa del modelo
    classification  TEXT NOT NULL,              -- SUCCESS | PARTIAL | BLOCKED
    blocked_by      TEXT NOT NULL DEFAULT 'NONE', -- NONE | SHIELD_GEMMA | GUARDRAIL_RULE | GUARDRAIL_JUDGE
    evidence        TEXT DEFAULT '',            -- fragmento que demuestra éxito/fallo
    total_latency_ms REAL NOT NULL DEFAULT 0.0, -- latencia total del ataque
    vram_peak_mb    REAL DEFAULT 0.0,          -- pico de VRAM (se mide en F6)
    FOREIGN KEY (attack_id) REFERENCES attacks(attack_id)
);

-- ===== Índices para queries del dashboard =====

-- Pivote vector × modo (vista 1 del dashboard)
CREATE INDEX IF NOT EXISTS idx_attacks_vector_session
    ON attacks(vector_id, session_id);

-- Filtrar por clasificación (vista 2)
CREATE INDEX IF NOT EXISTS idx_results_classification
    ON results(classification);

-- Buscar decisiones por modo (vista 3)
CREATE INDEX IF NOT EXISTS idx_guardrail_mode
    ON guardrail_decisions(mode);

-- Sesiones por modo de guardrail (para comparativas)
CREATE INDEX IF NOT EXISTS idx_sessions_mode
    ON sessions(guardrail_mode);
