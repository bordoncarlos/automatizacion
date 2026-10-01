"""
Configuración central: rutas, credenciales de Odoo y tablas de traducción.

PARA QUÉ SIRVE
--------------
Todo lo que cambia de una empresa a otra vive acá: cómo se llaman los
diarios de venta, qué usuario de Odoo es qué vendedor, qué término de pago es
contado. Los scripts no tienen nada de eso escrito adentro.

MODO DEMO
---------
Si el `.env` no define ODOO_URL, todos los scripts trabajan contra un Odoo
SIMULADO en memoria (lib/odoo_demo.py) con datos inventados. Así el proyecto
se puede probar sin una instancia de Odoo.
"""
import os
import sys
from datetime import timedelta, timezone
from pathlib import Path

import pandas as pd

# =============================================================================
# RUTAS
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
DATOS_DIR = BASE_DIR / "datos"
LOGS_DIR = BASE_DIR / "logs"
DATOS_DIR.mkdir(exist_ok=True)

CACHE_VENTAS = DATOS_DIR / "ventas_cerradas.parquet"   # días cerrados ya extraídos
VENTAS_HOY = DATOS_DIR / "ventas_hoy.parquet"           # el día en curso (lo usa la cobranza)
EXCEL_VENTAS = DATOS_DIR / "ventas_erp.xlsx"            # lo que lee Power BI
COBRANZA = DATOS_DIR / "cobranza.parquet"
EXCEL_STOCK = DATOS_DIR / "stock_por_deposito.xlsx"
CLIENTES = DATOS_DIR / "base_clientes.parquet"


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

# Desde qué día los datos salen de Odoo. Lo anterior es del sistema anterior y
# NINGÚN script de este proyecto lo toca. En la demo: los últimos 45 días.
_hoy = pd.Timestamp.now().normalize()
ERP_DESDE = pd.Timestamp(os.environ.get("ERP_DESDE") or (_hoy - pd.Timedelta(days=45)))

# Odoo guarda las horas en UTC. Paraguay quedó en UTC-3 todo el año.
ZONA_HORARIA = timezone(timedelta(hours=int(os.environ.get("UTC_OFFSET", "-3"))))


# =============================================================================
# PARÁMETROS DE LA EXTRACCIÓN
# =============================================================================
LOTE = 2000                 # registros por llamada a la API (paginado por id)
DIAS_RETROACTIVOS = 10      # días cerrados que se re-extraen en cada corrida
API_REINTENTOS = 4          # Odoo Online devuelve 503 de a ratos
API_ESPERA_S = 5            # espera creciente: 5, 10, 15 s


# =============================================================================
# TABLAS DE TRADUCCIÓN  Odoo -> esquema de los informes
# =============================================================================
# Usuario de Odoo (id) -> código de vendedor. Se mantiene a mano: Odoo tiene
# nombres de usuario, los informes tienen códigos de vendedor. Un usuario que
# falte sale como 9000 + id y queda avisado en el log (nunca en silencio).
VENDEDOR_POR_USUARIO = {2: "V01", 6: "V02", 7: "V03", 8: "V04", 9: "V05"}
VENDEDOR_SIN_MAPEO_BASE = 9000

# Diario de ventas de Odoo -> código de sucursal (cada sucursal factura con su diario).
SUCURSAL_POR_DIARIO = {
    "Facturas Casa Central": "01",
    "Facturas Sucursal Norte": "02",
    "Facturas Sucursal Sur": "03",
}
SUCURSAL_DESCONOCIDA = "99"

# Término de pago de Odoo -> condición de venta. Sin término = contado.
CONDICION_POR_TERMINO = {
    "Immediate Payment": "CONTADO",
    "15 Days": "CREDITO 15 DIAS",
    "30 Days": "CREDITO 30 DIAS",
}
CONTADO = "CONTADO"

# Los precios de venta se cargan CON IVA en Odoo (impuesto "incluido en el precio").
PRECIO_INCLUYE_IVA = True

# Odoo 17 deja los pagos en 'posted'; Odoo 18+ en 'in_process' / 'paid'.
ESTADOS_PAGO_VALIDOS = ["posted", "in_process", "paid"]

# Ubicación de Odoo -> depósito de los informes. Los nombres de la derecha son
# EXACTAMENTE los que usan los filtros de Power BI ("CENTRAL", "NORTE"...).
DEPOSITO_POR_UBICACION = {
    "WH/Stock": "CENTRAL",
    "NOR/Stock": "NORTE",
    "SUR/Stock": "SUR",
    "WH/Averiados": "AVERIADOS CENTRAL",
}
# Zonas que se crearon dentro del depósito central: se suman a CENTRAL.
ZONAS_CENTRAL = {"WH/Stock/Pisos", "WH/Stock/Adhesivos"}

# Canal comercial de un cliente nuevo: Odoo no lo tiene, lo define el área comercial.
CANAL_NUEVO = "A DEFINIR"

# La consola de Windows usa cp1252: un carácter raro no debe tirar el script.
for _flujo in (sys.stdout, sys.stderr):
    if _flujo is not None and hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")
