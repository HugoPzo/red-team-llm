"""
Configuración central del proyecto Red Team LLM.

Define modelos, endpoints, umbrales y rutas compartidas por todas las capas.
Cualquier valor "mágico" debe vivir aquí, no disperso en el código.
"""

from pathlib import Path

# ===== Rutas del proyecto =====
PROJECT_ROOT: Path = Path(__file__).resolve().parent
DATA_DIR: Path = PROJECT_ROOT / "data"
DB_PATH: Path = DATA_DIR / "results.db"
LOGS_DIR: Path = DATA_DIR / "logs"
SCHEMA_PATH: Path = DATA_DIR / "schema.sql"

# ===== Ollama =====
OLLAMA_BASE_URL: str = "http://localhost:11434"

# --- Modelo principal (chatbot ARIA) ---
TARGET_MODEL: str = "gemma3:4b"
TARGET_TEMPERATURE: float = 0.7
TARGET_NUM_PREDICT: int = 512  # tokens máximos de respuesta

# --- Modelo juez (guardrail LLM-as-Judge) ---
JUDGE_MODEL: str = "gemma3:1b"
JUDGE_TEMPERATURE: float = 0.0  # determinista para clasificación
JUDGE_NUM_PREDICT: int = 64     # respuesta JSON corta

# ===== Puertos de servicios =====
TARGET_PORT: int = 8000         # Capa 1 — chatbot vulnerable
GUARDRAIL_PORT: int = 8001      # Capa 3 — proxy guardrail
DASHBOARD_PORT: int = 8501      # Streamlit dashboard

# ===== Umbrales de clasificación =====
JUDGE_CONFIDENCE_THRESHOLD: float = 0.7  # umbral mínimo para bloqueo

# ===== VRAM constraint =====
# Regla absoluta: gemma3:4b y gemma3:1b NO coexisten en GPU.
# keep_alive=0 fuerza la descarga del modelo tras cada request.
KEEP_ALIVE_SECONDS: int = 0
