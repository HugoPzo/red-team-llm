"""
Orquestador del Red Team Agent.

Ejecuta vectores de ataque contra el target, muestra resultados
con formato rico en la terminal, y persiste todo en SQLite + JSON.

Ejecución:
    python -m attacker.attack_runner              # todos los vectores
    python -m attacker.attack_runner V1           # solo V1
    python -m attacker.attack_runner V1 V3        # V1 y V3

Ejemplo de uso programático:
    runner = AttackRunner(target_url="http://localhost:8000")
    results = await runner.run_campaign()
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Asegurar imports del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attacker.vectors.base import AttackResult, BaseAttack
from attacker.vectors.v1_direct_injection import DirectInjection
from attacker.vectors.v2_jailbreak_roleplay import JailbreakRoleplay
from attacker.vectors.v3_indirect_injection import IndirectInjection
from attacker.vectors.v4_prompt_extraction import PromptExtraction
from attacker.vectors.v5_context_manipulation import ContextManipulation
from data.db import Database


# ===== Registro de vectores disponibles =====

VECTOR_REGISTRY: dict[str, type[BaseAttack]] = {
    "V1": DirectInjection,
    "V2": JailbreakRoleplay,
    "V3": IndirectInjection,
    "V4": PromptExtraction,
    "V5": ContextManipulation,
}

# Mapeo de colores por clasificación para la terminal
CLASSIFICATION_COLORS: dict[str, str] = {
    "SUCCESS": "red bold",
    "PARTIAL": "yellow bold",
    "BLOCKED": "green bold",
}

console = Console()


class AttackRunner:
    """
    Orquestador de ataques del Red Team.

    Responsabilidades:
    - Instanciar y ejecutar vectores de ataque
    - Formatear y mostrar resultados en terminal (rich)
    - Persistir resultados en SQLite y JSON (data/db.py)

    Decisión de diseño: el runner es independiente del target.
    Solo necesita la URL. No importa si hay guardrails o no;
    el vector envía su payload y clasifica la respuesta.
    """

    def __init__(
        self,
        target_url: str = "http://localhost:8000",
        guardrail_mode: str = "NONE",
    ) -> None:
        self.target_url = target_url
        self.guardrail_mode = guardrail_mode
        self._db = Database()

    async def run_vector(self, vector_id: str) -> list[AttackResult]:
        """
        Ejecuta un vector específico por su ID.

        Busca el vector en el registro, lo instancia, ejecuta sus
        3 variantes y retorna los resultados.
        """
        vector_class = VECTOR_REGISTRY.get(vector_id)
        if not vector_class:
            available = ", ".join(VECTOR_REGISTRY.keys())
            raise ValueError(
                f"Vector '{vector_id}' no registrado. Disponibles: {available}"
            )

        attack = vector_class()

        console.print(
            Panel(
                f"[bold]{attack.description}[/bold]\n"
                f"Vector: {attack.vector_id} | OWASP: {attack.owasp_category} | "
                f"Modo: {self.guardrail_mode}",
                title=f"🎯 Ejecutando {attack.vector_id}",
                border_style="red",
            )
        )

        results = await attack.execute(
            target_url=self.target_url,
            guardrail_mode=self.guardrail_mode,
        )

        self._display_results(results)
        return results

    async def run_campaign(
        self, vector_ids: Optional[list[str]] = None
    ) -> list[AttackResult]:
        """
        Ejecuta una campaña completa con persistencia.

        Flujo:
        1. Inicializa la base de datos (idempotente)
        2. Crea una sesión nueva
        3. Ejecuta los vectores solicitados (o todos)
        4. Persiste cada resultado en SQLite
        5. Escribe JSON log de la sesión
        6. Muestra resumen
        7. Cierra la DB

        Este es el método principal — reemplaza a run_all().
        """
        # Inicializar DB
        await self._db.init_db()

        # Crear sesión
        db_session_id = await self._db.create_session(
            guardrail_mode=self.guardrail_mode,
        )
        console.print(
            f"\n[dim]📁 Sesión: {db_session_id}[/dim]"
            f"\n[dim]💾 Persistencia: SQLite + JSON[/dim]\n"
        )

        # Determinar qué vectores ejecutar
        targets = vector_ids or list(VECTOR_REGISTRY.keys())
        all_results: list[AttackResult] = []

        for vector_id in targets:
            results = await self.run_vector(vector_id)
            all_results.extend(results)

            # Persistir cada lote de resultados inmediatamente
            await self._db.save_batch(db_session_id, results)

        # Finalizar sesión
        await self._db.finish_session(db_session_id)

        # Escribir JSON log
        log_path = self._db.save_json_log(
            db_session_id, all_results, self.guardrail_mode
        )

        # Mostrar resumen
        self._display_summary(all_results, db_session_id, log_path)

        # Cerrar DB
        await self._db.close()

        return all_results

    def _display_results(self, results: list[AttackResult]) -> None:
        """Muestra resultados detallados de un vector en tabla rich."""
        table = Table(
            title=f"Resultados {results[0].vector_id}" if results else "Sin resultados",
            show_lines=True,
        )
        table.add_column("Var.", style="cyan", width=4)
        table.add_column("Clasificación", width=14)
        table.add_column("Bloqueado por", style="dim", width=16)
        table.add_column("Latencia", style="magenta", width=10)
        table.add_column("Evidencia", max_width=50)

        for r in results:
            color = CLASSIFICATION_COLORS.get(r.classification, "white")
            table.add_row(
                str(r.variant),
                f"[{color}]{r.classification}[/{color}]",
                r.blocked_by,
                f"{r.latency_ms:.0f}ms",
                r.evidence[:50] + ("..." if len(r.evidence) > 50 else ""),
            )

        console.print(table)

    def _display_summary(
        self,
        results: list[AttackResult],
        db_session_id: str = "",
        log_path: Optional[Path] = None,
    ) -> None:
        """Muestra resumen final de todos los vectores ejecutados."""
        console.print("\n")

        # Conteo por clasificación
        counts = {"SUCCESS": 0, "PARTIAL": 0, "BLOCKED": 0}
        for r in results:
            counts[r.classification] += 1

        total = len(results)
        summary_table = Table(title="📊 Resumen de la campaña de ataque")
        summary_table.add_column("Métrica", style="bold")
        summary_table.add_column("Valor")

        summary_table.add_row("Total de ataques", str(total))
        summary_table.add_row("Modo guardrail", self.guardrail_mode)
        summary_table.add_row(
            "Exitosos (SUCCESS)",
            f"[red bold]{counts['SUCCESS']}[/red bold] ({counts['SUCCESS']*100//total}%)" if total else "0",
        )
        summary_table.add_row(
            "Parciales (PARTIAL)",
            f"[yellow bold]{counts['PARTIAL']}[/yellow bold] ({counts['PARTIAL']*100//total}%)" if total else "0",
        )
        summary_table.add_row(
            "Bloqueados (BLOCKED)",
            f"[green bold]{counts['BLOCKED']}[/green bold] ({counts['BLOCKED']*100//total}%)" if total else "0",
        )

        if db_session_id:
            summary_table.add_row("", "")
            summary_table.add_row("Sesión DB", f"[dim]{db_session_id}[/dim]")
        if log_path:
            summary_table.add_row("JSON log", f"[dim]{log_path}[/dim]")

        console.print(summary_table)


# ===== Ejecución directa =====


async def main(vector_ids: Optional[list[str]] = None) -> None:
    """
    Punto de entrada principal.

    Si se pasan vector_ids, ejecuta solo esos vectores.
    Si no, ejecuta todos los registrados.
    Siempre persiste a SQLite + JSON.
    """
    runner = AttackRunner()
    await runner.run_campaign(vector_ids=vector_ids or None)


if __name__ == "__main__":
    # Permitir pasar vectores como argumentos:
    #   python -m attacker.attack_runner V1         (solo V1)
    #   python -m attacker.attack_runner V1 V3      (V1 y V3)
    #   python -m attacker.attack_runner             (todos)
    vectors = sys.argv[1:] if len(sys.argv) > 1 else None
    asyncio.run(main(vectors))
