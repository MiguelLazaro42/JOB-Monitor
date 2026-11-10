import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv_if_exists():
    """Carga variables desde .env local (ignorado en Git) sin requerir librerías externas."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


_load_dotenv_if_exists()

PORT = int(os.environ.get("RA_PORT", 5080))
HOST = os.environ.get("RA_HOST", "0.0.0.0")

# Credenciales para el Panel de Administrador
ADMIN_USERNAME = os.environ.get("RA_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("RA_ADMIN_PASS", "admin123")

# Token compartido para autenticar los agentes de las PCs de la empresa
AGENT_TOKEN = os.environ.get("RA_AGENT_TOKEN", "ra-secret-token-2026")

# Zona horaria oficial por defecto: Mérida, Yucatán, México (America/Merida)
DEFAULT_TIMEZONE = os.environ.get("RA_TIMEZONE", "America/Merida")

# Archivo de persistencia de alias de equipos (Fase 1 -> SQLite en Fase 2)
ALIASES_FILE = DATA_DIR / "device_aliases.json"

