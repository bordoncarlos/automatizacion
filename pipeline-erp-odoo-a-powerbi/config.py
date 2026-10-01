"""
Configuración: rutas, credenciales de Odoo y parámetros de la extracción.

PARA QUÉ SIRVE
--------------
Todo lo que cambia de una instalación a otra vive acá; el script no tiene
nada de eso escrito adentro.

MODO DEMO
---------
Si el `.env` no define ODOO_URL, el script trabaja contra un Odoo SIMULADO en
memoria (lib/odoo_demo.py) con datos inventados. Así se puede probar sin una
instancia de Odoo.
"""
import os
import sys
from datetime import timedelta, timezone
from pathlib import Path

import pandas as pd

# La consola de Windows usa cp1252: un carácter raro no debe tirar el script.
for _flujo in (sys.stdout, sys.stderr):
    if _flujo is not None and hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")

# =============================================================================
# RUTAS
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
DATOS_DIR = BASE_DIR / "datos"
LOGS_DIR = BASE_DIR / "logs"
DATOS_DIR.mkdir(exist_ok=True)

ARCHIVO_REPORTE = DATOS_DIR / "reporte.xlsx"            # lo que lee Power BI
CACHE = DATOS_DIR / "cache_dias_cerrados.parquet"      # días ya extraídos


# =============================================================================
# CREDENCIALES (.env)
# =============================================================================
def _cargar_env(ruta: Path) -> None:
    """Carga `CLAVE=valor` de un .env en os.environ sin pisar lo ya definido."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env(BASE_DIR / ".env")

ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/")
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USUARIO = os.environ.get("ODOO_USUARIO", "")
# Odoo acepta en este campo la contraseña del usuario o una API key
# (Mi perfil -> Seguridad de la cuenta). Mejor una API key: se revoca sola.
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "")
MODO_DEMO = not ODOO_URL

# Desde qué fecha se extrae. En la demo: los últimos 45 días.
_hoy = pd.Timestamp.now().normalize()
DESDE = pd.Timestamp(os.environ.get("DESDE") or (_hoy - pd.Timedelta(days=45)))

# Odoo guarda las horas en UTC; el reporte las quiere en hora local.
ZONA_HORARIA = timezone(timedelta(hours=int(os.environ.get("UTC_OFFSET", "-3"))))


# =============================================================================
# EXTRACCIÓN
# =============================================================================
LOTE = 2000                 # registros por llamada a la API (paginado por id)
DIAS_RETROACTIVOS = 10      # días cerrados que se vuelven a extraer en cada corrida
API_REINTENTOS = 4          # Odoo Online devuelve 503 de vez en cuando
API_ESPERA_S = 5            # espera creciente: 5, 10, 15 s

# Si los precios de venta se cargan con el impuesto incluido en Odoo.
PRECIO_INCLUYE_IMPUESTO = True
