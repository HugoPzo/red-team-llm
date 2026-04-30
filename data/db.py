"""
Capa de persistencia — SQLite + JSON logs.

Gestiona toda la interacción con la base de datos SQLite y la
escritura de logs JSON por sesión. Provee una interfaz async
para que el attack_runner persista resultados sin acoplarse
a los detalles de SQLite.

Decisiones de diseño:
- aiosqlite: async porque el runner ya es async (httpx, FastAPI).
  Mezclar sync e async haría el código más complejo sin beneficio.
- JSON logs: redundancia intencional. SQLite es para queries
  estructuradas (dashboard), JSON es para inspección manual
  y portabilidad (se puede compartir un archivo JSON sin la DB).
- init_db() es idempotente: usa CREATE IF NOT EXISTS.

Ejemplo de uso:
    db = Database()
    await db.init_db()
    session_id = await db.create_session("NONE", "gemma3:4b")
    await db.save_attack_result(session_id, attack_result)
    await db.close()
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

# Importaciones del proyecto
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH, LOGS_DIR, SCHEMA_PATH, TARGET_MODEL
from attacker.vectors.base import AttackResult


class Database:
    """
    Interfaz async para la persistencia de resultados de ataques.

    Responsabilidades:
    - Inicializar el esquema SQLite (idempotente)
    - Crear sesiones de ejecución
    - Persistir resultados de ataques (SQLite + JSON)
    - Proveer queries para el dashboard (F7)

    Ciclo de vida:
        db = Database()
        await db.init_db()
        ... usar ...
        await db.close()
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or DB_PATH
        self._connection: Optional[aiosqlite.Connection] = None

    async def init_db(self) -> None:
        """
        Inicializa la base de datos ejecutando schema.sql.

        Es idempotente: CREATE IF NOT EXISTS no falla si las tablas
        ya existen. Se puede llamar en cada arranque sin riesgo.
        """
        # Asegurar que el directorio data/ existe
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(str(self._db_path))
        # Habilitar foreign keys (deshabilitadas por defecto en SQLite)
        await self._connection.execute("PRAGMA foreign_keys = ON")

        # Ejecutar schema.sql
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        await self._connection.executescript(schema_sql)
        await self._connection.commit()

    async def close(self) -> None:
        """Cierra la conexión a la base de datos."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    # ===== Operaciones de sesión =====

    async def create_session(
        self,
        guardrail_mode: str = "NONE",
        target_model: str = TARGET_MODEL,
        notes: str = "",
    ) -> str:
        """
        Crea una nueva sesión de ejecución.

        Retorna el session_id (UUID) para vincular ataques posteriores.
        """
        assert self._connection, "Llamar a init_db() primero"

        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        await self._connection.execute(
            """INSERT INTO sessions (session_id, started_at, guardrail_mode, target_model, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, now, guardrail_mode, target_model, notes),
        )
        await self._connection.commit()
        return session_id

    async def finish_session(self, session_id: str) -> None:
        """Marca una sesión como finalizada con timestamp."""
        assert self._connection, "Llamar a init_db() primero"

        now = datetime.now(timezone.utc).isoformat()
        await self._connection.execute(
            "UPDATE sessions SET finished_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        await self._connection.commit()

    # ===== Persistencia de resultados =====

    async def save_attack_result(
        self,
        db_session_id: str,
        result: AttackResult,
    ) -> int:
        """
        Persiste un AttackResult en SQLite (tablas attacks + results).

        Flujo:
        1. Inserta en attacks (payload, metadatos del vector)
        2. Inserta en results (respuesta, clasificación)
        3. Retorna el attack_id generado

        Nota: guardrail_decisions se inserta por separado (F5/F6).
        """
        assert self._connection, "Llamar a init_db() primero"

        # Insertar en attacks
        cursor = await self._connection.execute(
            """INSERT INTO attacks
               (session_id, vector_id, owasp_category, variant, payload, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                db_session_id,
                result.vector_id,
                result.owasp_category,
                result.variant,
                result.payload,
                result.timestamp.isoformat(),
            ),
        )
        attack_id = cursor.lastrowid
        assert attack_id is not None

        # Insertar en results
        await self._connection.execute(
            """INSERT INTO results
               (attack_id, response_text, classification, blocked_by,
                evidence, total_latency_ms, vram_peak_mb)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                attack_id,
                result.response,
                result.classification,
                result.blocked_by,
                result.evidence,
                result.latency_ms,
                0.0,  # vram_peak_mb se mide en F6
            ),
        )
        await self._connection.commit()
        return attack_id

    async def save_batch(
        self,
        db_session_id: str,
        results: list[AttackResult],
        guardrail_decisions: Optional[list[dict]] = None,
    ) -> list[int]:
        """
        Persiste una lista de resultados (todos los de un vector o campaña).

        Si se proporcionan guardrail_decisions, también las persiste.
        Cada decisión se vincula al attack_id correspondiente por índice.

        Retorna la lista de attack_ids generados.
        """
        attack_ids: list[int] = []
        for i, result in enumerate(results):
            aid = await self.save_attack_result(db_session_id, result)
            attack_ids.append(aid)

            # Persistir decisión del guardrail si existe
            if guardrail_decisions and i < len(guardrail_decisions):
                gd = guardrail_decisions[i]
                if gd:  # puede ser None si no hubo guardrail
                    await self.save_guardrail_decision(aid, gd)

        return attack_ids

    async def save_guardrail_decision(
        self,
        attack_id: int,
        decision: dict,
    ) -> None:
        """
        Persiste una decisión del guardrail en la tabla guardrail_decisions.

        Parámetros:
            attack_id: FK al ataque evaluado
            decision: dict con keys: mode, allow, reason, matched_pattern,
                      confidence, latency_ms
        """
        assert self._connection, "Llamar a init_db() primero"

        await self._connection.execute(
            """INSERT INTO guardrail_decisions
               (attack_id, mode, allow, reason, matched_pattern, confidence, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                attack_id,
                decision.get("mode", "RULE"),
                1 if decision.get("allowed", True) else 0,
                decision.get("reason", ""),
                decision.get("matched_pattern", ""),
                decision.get("confidence", 1.0),
                decision.get("latency_ms", 0.0),
            ),
        )
        await self._connection.commit()

    # ===== JSON logs =====

    def save_json_log(
        self,
        db_session_id: str,
        results: list[AttackResult],
        guardrail_mode: str = "NONE",
    ) -> Path:
        """
        Escribe un archivo JSON con los resultados de la sesión.

        Formato: data/logs/{session_id}.json
        Contiene toda la información necesaria para reproducir
        el análisis sin acceder a SQLite.

        Nota: este método es sync porque json.dump es sync y no
        justifica la complejidad de aiofiles para un solo write.
        """
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        log_path = LOGS_DIR / f"{db_session_id}.json"
        log_data: dict[str, Any] = {
            "session_id": db_session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "guardrail_mode": guardrail_mode,
            "target_model": TARGET_MODEL,
            "total_attacks": len(results),
            "summary": {
                "success": sum(1 for r in results if r.classification == "SUCCESS"),
                "partial": sum(1 for r in results if r.classification == "PARTIAL"),
                "blocked": sum(1 for r in results if r.classification == "BLOCKED"),
            },
            "results": [
                {
                    "vector_id": r.vector_id,
                    "owasp_category": r.owasp_category,
                    "variant": r.variant,
                    "payload": r.payload,
                    "response": r.response,
                    "classification": r.classification,
                    "blocked_by": r.blocked_by,
                    "evidence": r.evidence,
                    "latency_ms": r.latency_ms,
                    "session_id": r.session_id,
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in results
            ],
        }

        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)

        return log_path

    # ===== Queries para el dashboard (F7) =====

    async def query_summary_pivot(self) -> list[dict[str, Any]]:
        """
        Vista 1 del dashboard: pivote vector × modo.

        Retorna conteo de SUCCESS/PARTIAL/BLOCKED agrupado por
        vector_id y guardrail_mode.
        """
        assert self._connection, "Llamar a init_db() primero"

        query = """
            SELECT
                a.vector_id,
                s.guardrail_mode,
                r.classification,
                COUNT(*) as count
            FROM attacks a
            JOIN sessions s ON a.session_id = s.session_id
            JOIN results r ON a.attack_id = r.attack_id
            GROUP BY a.vector_id, s.guardrail_mode, r.classification
            ORDER BY a.vector_id, s.guardrail_mode
        """
        async with self._connection.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "vector_id": row[0],
                    "guardrail_mode": row[1],
                    "classification": row[2],
                    "count": row[3],
                }
                for row in rows
            ]

    async def query_attack_details(
        self, vector_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """
        Vista 2 del dashboard: detalle por ataque con payload y respuesta.

        Opcionalmente filtra por vector_id.
        """
        assert self._connection, "Llamar a init_db() primero"

        query = """
            SELECT
                a.attack_id,
                a.vector_id,
                a.owasp_category,
                a.variant,
                a.payload,
                r.response_text,
                r.classification,
                r.blocked_by,
                r.evidence,
                r.total_latency_ms,
                s.guardrail_mode
            FROM attacks a
            JOIN results r ON a.attack_id = r.attack_id
            JOIN sessions s ON a.session_id = s.session_id
        """
        params: list[str] = []
        if vector_id:
            query += " WHERE a.vector_id = ?"
            params.append(vector_id)
        query += " ORDER BY a.attack_id"

        async with self._connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "attack_id": row[0],
                    "vector_id": row[1],
                    "owasp_category": row[2],
                    "variant": row[3],
                    "payload": row[4],
                    "response_text": row[5],
                    "classification": row[6],
                    "blocked_by": row[7],
                    "evidence": row[8],
                    "total_latency_ms": row[9],
                    "guardrail_mode": row[10],
                }
                for row in rows
            ]

    async def query_blocker_distribution(self) -> list[dict[str, Any]]:
        """
        Distribución de quién bloqueó los ataques por modo.

        Retorna conteo agrupado por guardrail_mode × blocked_by.
        Útil para la Vista 3 del dashboard: mostrar el rol de
        SHIELD_GEMMA vs GUARDRAIL_RULE vs GUARDRAIL_JUDGE.
        """
        assert self._connection, "Llamar a init_db() primero"

        query = """
            SELECT
                s.guardrail_mode,
                r.blocked_by,
                COUNT(*) as count
            FROM sessions s
            JOIN attacks a ON s.session_id = a.session_id
            JOIN results r ON a.attack_id = r.attack_id
            WHERE r.classification = 'BLOCKED'
            GROUP BY s.guardrail_mode, r.blocked_by
            ORDER BY s.guardrail_mode, r.blocked_by
        """
        async with self._connection.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "guardrail_mode": row[0],
                    "blocked_by": row[1],
                    "count": row[2],
                }
                for row in rows
            ]

    async def query_defense_metrics(self) -> list[dict[str, Any]]:
        """
        Vista 3 del dashboard: métricas de defensa (latencia, detección).

        Agrupa por guardrail_mode y calcula:
        - Ataques totales, bloqueados, tasa de detección
        - Latencia promedio y máxima
        """
        assert self._connection, "Llamar a init_db() primero"

        query = """
            SELECT
                s.guardrail_mode,
                COUNT(*) as total_attacks,
                SUM(CASE WHEN r.classification = 'BLOCKED' THEN 1 ELSE 0 END) as blocked,
                SUM(CASE WHEN r.classification = 'SUCCESS' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN r.classification = 'PARTIAL' THEN 1 ELSE 0 END) as partial,
                ROUND(AVG(r.total_latency_ms), 1) as avg_latency_ms,
                ROUND(MAX(r.total_latency_ms), 1) as max_latency_ms,
                ROUND(MIN(r.total_latency_ms), 1) as min_latency_ms
            FROM sessions s
            JOIN attacks a ON s.session_id = a.session_id
            JOIN results r ON a.attack_id = r.attack_id
            GROUP BY s.guardrail_mode
            ORDER BY s.guardrail_mode
        """
        async with self._connection.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "guardrail_mode": row[0],
                    "total_attacks": row[1],
                    "blocked": row[2],
                    "success": row[3],
                    "partial": row[4],
                    "detection_rate": round(row[2] / row[1] * 100, 1) if row[1] > 0 else 0,
                    "avg_latency_ms": row[5],
                    "max_latency_ms": row[6],
                    "min_latency_ms": row[7],
                }
                for row in rows
            ]
