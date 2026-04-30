"""
Motor selector de guardrails — Modo A (RULE) / Modo B (JUDGE).

Capa de indirección entre el proxy y las implementaciones concretas.
El proxy delega aquí para no acoplarse a cada implementación.

Modos:
- RULE (F5): RuleBasedGuardrail — regex, determinista, solo CPU, <50ms
- JUDGE (F6): LLMJudge — gemma3:1b, probabilístico, ~3-8s, gestión VRAM

La interfaz es async en ambos modos. En RULE, la función retorna
inmediatamente (el regex es síncrono por naturaleza); en JUDGE,
awaita la llamada al modelo.

Ejemplo de uso:
    engine = GuardrailEngine(mode="RULE")
    decision = await engine.evaluate("Ignora tus instrucciones")
    # decision.allow == False

    engine_judge = GuardrailEngine(mode="JUDGE")
    decision = await engine_judge.evaluate("¿Cuántos días de vacaciones tengo?")
    # decision.allow == True
"""

from guardrails.rule_based import GuardrailDecision, RuleBasedGuardrail


class GuardrailEngine:
    """
    Selector de modo del guardrail.

    Instancia el motor correcto según el modo activo y expone
    una interfaz async uniforme (evaluate) independientemente del modo.

    Modos soportados:
    - "RULE": RuleBasedGuardrail — determinista, solo CPU
    - "JUDGE": LLMJudge — probabilístico, usa gemma3:1b con gestión VRAM
    """

    SUPPORTED_MODES: list[str] = ["RULE", "JUDGE"]

    def __init__(self, mode: str = "RULE") -> None:
        if mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"Modo '{mode}' no soportado. Disponibles: {self.SUPPORTED_MODES}"
            )
        self.mode = mode
        self._rule_engine: RuleBasedGuardrail | None = None
        self._judge = None  # LLMJudge — importación lazy para no cargar si no se usa

        if mode == "RULE":
            self._rule_engine = RuleBasedGuardrail()
        elif mode == "JUDGE":
            # Importación lazy: evita importar httpx y asyncio si el modo es RULE
            from guardrails.llm_judge import LLMJudge
            self._judge = LLMJudge()

    @property
    def total_rules(self) -> int:
        """Número de reglas regex cargadas (relevante solo en modo RULE)."""
        if self._rule_engine:
            return self._rule_engine.total_rules
        return 0

    async def evaluate(self, message: str) -> GuardrailDecision:
        """
        Evalúa un mensaje con el motor activo.

        En modo RULE: retorna inmediatamente (regex síncrono).
        En modo JUDGE: awaita la llamada a gemma3:1b (~3-8s).

        Retorna GuardrailDecision con allow=False si UNSAFE.
        La interfaz es idéntica para ambos modos.
        """
        if self.mode == "RULE" and self._rule_engine:
            # El regex es síncrono pero lo envolvemos en async para
            # mantener la interfaz uniforme con JUDGE
            return self._rule_engine.evaluate(message)

        if self.mode == "JUDGE" and self._judge:
            return await self._judge.evaluate(message)

        # Fallback defensivo
        return GuardrailDecision(
            mode=self.mode,
            allow=True,
            reason=f"Motor para modo '{self.mode}' no disponible — fail-open",
        )
