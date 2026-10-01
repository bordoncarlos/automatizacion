"""
Configuración central del pipeline: rutas, credenciales y reglas de negocio.

PARA QUÉ SIRVE
--------------
Que ningún script tenga rutas, contraseñas ni reglas escritas "a mano".
Si cambia una regla (por ejemplo, a quién se le reasigna la cartera de un
vendedor que se fue), se cambia ACÁ y la toman todos los scripts a la vez.
Cuando cada script tenía su propia copia de una regla, la misma cifra salía
distinta según qué reporte se mirara.

CREDENCIALES
------------
Se leen de un archivo `.env` (que NO se sube al repositorio; ver `.env.example`).
El parser es propio, de 10 líneas, para no depender de python-dotenv en el
Python que usa el Programador de tareas.
"""
import os
import sys
from pathlib import Path

# La consola de Windows usa cp1252: un carácter que no exista ahí no debe
# tirar abajo el script, a lo sumo se ve como "?".
for _flujo in (sys.stdout, sys.stderr):
    if _flujo is not None and hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")

# =============================================================================
# RUTAS (todas relativas a la carpeta del proyecto: se puede mover sin tocar nada)
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
DATOS_DIR = BASE_DIR / "datos"
LOGS_DIR = BASE_DIR / "logs"

PARQUET_VENTAS = DATOS_DIR / "ventas_historicas.parquet"   # el histórico que lee Power BI
PARQUET_STOCK = DATOS_DIR / "historial_stock.parquet"      # una foto de stock por día
REPORTE_OFICIAL = DATOS_DIR / "reporte_oficial_ventas.csv" # exportado del sistema de origen
CORRECCIONES = DATOS_DIR / "correcciones.csv"              # bitácora de lo que se corrigió

DATOS_DIR.mkdir(exist_ok=True)


# =============================================================================
# CREDENCIALES (.env)
# =============================================================================
def _cargar_env(ruta: Path) -> None:
    """Carga `CLAVE=valor` de un .env en os.environ, sin pisar lo ya definido.

    Sin dependencias: ignora líneas vacías y comentarios (#). Las variables
    que ya existen en el entorno mandan (útil para un servidor o un CI).
    """
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env(BASE_DIR / ".env")

# URL de conexión SQLAlchemy. Ejemplos:
#   PostgreSQL : postgresql://usuario:clave@servidor:5432/base
#   SQL Server : mssql+pyodbc://usuario:clave@servidor/base?driver=ODBC+Driver+17+for+SQL+Server
# Sin .env se usa la base de demostración que crea 00_generar_datos_demo.py.
DB_URL = os.environ.get("DB_URL") or f"sqlite:///{(DATOS_DIR / 'erp_demo.db').as_posix()}"
DB_ESQUEMA = os.environ.get("DB_ESQUEMA") or None   # None = esquema por defecto
DB_TABLA_VENTAS = os.environ.get("DB_TABLA_VENTAS", "ventas")
DB_TABLA_STOCK = os.environ.get("DB_TABLA_STOCK", "stock_actual")


# =============================================================================
# PARÁMETROS DE LA EXTRACCIÓN
# =============================================================================
# Primera fecha que se trae cuando el parquet todavía no existe.
PRIMERA_FECHA = "2025-01-01"

# Cada corrida vuelve a traer los últimos N días (no sólo lo nuevo) y los
# REEMPLAZA enteros: el sistema de origen permite anular o corregir un
# comprobante días después de emitido, y sin esta ventana el cambio nunca
# llegaría al histórico.
DIAS_RETROACTIVOS = 3

# Un rango largo (una recarga de meses) se parte en bloques. Una sola consulta
# gigante tarda tanto que un firewall corta la conexión a mitad de camino y
# hay que empezar de cero; con bloques, si uno falla se reintenta sólo ese.
DIAS_BLOQUE_MAX = 45     # hasta este rango, una sola consulta
MESES_POR_BLOQUE = 3     # por encima, bloques trimestrales

# Tipos de operación que NO son venta (movimientos internos): se filtran en la
# consulta misma para no traer filas que después habría que tirar.
TIPOS_OPERACION_EXCLUIR = ("TRASLADO INTERNO", "MUESTRA")

# Columnas que se piden a la tabla de origen. Si alguna no existe, el script
# aborta ANTES de extraer (ver verificar_columnas en lib/extraccion.py).
COLUMNAS_VENTAS = [
    "fecha_emision", "nro_comprobante", "tipo_comprobante", "tipo_operacion",
    "cliente", "cod_vendedor", "cod_sucursal", "cod_articulo", "cantidad",
    "precio_unitario", "monto_neto", "monto_total", "monto_costo",
    "condicion_venta", "moneda", "cotizacion",
]


# =============================================================================
# REGLAS DE NEGOCIO (una sola fuente de verdad)
# =============================================================================
# Sucursales de la empresa ficticia de la demo.
SUCURSALES = {
    "01": "CASA CENTRAL",
    "02": "SUCURSAL NORTE",
    "03": "SUCURSAL SUR",
    "04": "SUCURSAL ESTE",
    "90": "FERIA",               # temporal
    "91": "CUENTAS CORRIENTES",  # temporal (cobranza / notas de crédito)
}

# Sucursales "de paso": un vendedor puede facturar ahí, pero nunca pueden ser
# la sucursal a la que se le imputa su venta del mes (ver sucursal_dominante).
SUCURSALES_TEMPORALES = {"90", "91"}

# Cuando un vendedor se va, su código viejo pasa al que heredó la cartera, para
# que el histórico se lea con la estructura comercial ACTUAL.
VENDEDOR_MAPPING = {
    "V07": "V12",
    "V15": "V03",
}

# Vendedores a los que NO se les aplica la sucursal dominante: se respeta la
# sucursal real de cada factura. El valor es el mes desde el que rige
# (None = siempre). Con fecha, para no mover cifras de meses ya reportados.
VENDEDORES_SIN_DOMINANCIA = {
    "V01": None,        # gerente comercial: vende en todas las sucursales
    "V09": "2026-08",   # desde agosto atiende dos sucursales a la vez
}

# Tasas de IVA posibles (total / neto): exento, 5% y 10%.
IVA_TASAS = (1.00, 1.05, 1.10)
IVA_ESTANDAR = 1.10

# La conciliación corrige sola sólo si la diferencia total es chica respecto
# de lo vendido en el período. Por encima, algo raro pasa (un día que no se
# extrajo, un reporte mal exportado) y corregir a ciegas sería peor.
TOPE_CORRECCION_AUTOMATICA = 0.02   # 2 %

# Taxonomía de depósitos para la foto de stock (se busca por nombre).
DEPOSITO_CENTRAL = "CENTRAL"
DEPOSITOS_SUCURSAL = {"NORTE", "SUR", "ESTE"}
PATRONES_NO_VENDIBLE = ("FALTANTE", "AVERIAD", "MUESTRA", "DIF INVENTARIO")
