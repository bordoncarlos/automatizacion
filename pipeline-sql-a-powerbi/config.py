"""
Configuración: rutas, conexión a la base y qué datos lleva el reporte.

PARA QUÉ SIRVE
--------------
Todo lo que cambia de un reporte a otro vive acá: la tabla de origen, sus
columnas, la ventana de días. El script no tiene nada de eso escrito adentro:
para armar otro reporte se cambia este archivo, no el código.

CREDENCIALES
------------
Se leen de un archivo `.env` (NO se sube al repositorio; ver `.env.example`).
Sin `.env` se usa la base de demostración que crea generar_datos_demo.py.
"""
import os
import sys
from pathlib import Path

# La consola de Windows usa cp1252: un carácter que no exista ahí no debe
# tirar abajo un script que corre sin supervisión.
for _flujo in (sys.stdout, sys.stderr):
    if _flujo is not None and hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")

# =============================================================================
# RUTAS (relativas a la carpeta del proyecto: se puede mover sin tocar nada)
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
DATOS_DIR = BASE_DIR / "datos"
LOGS_DIR = BASE_DIR / "logs"
ARCHIVO_REPORTE = DATOS_DIR / "reporte.parquet"     # lo que lee Power BI
DATOS_DIR.mkdir(exist_ok=True)


def _cargar_env(ruta: Path) -> None:
    """Carga `CLAVE=valor` de un .env en os.environ, sin pisar lo ya definido."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env(BASE_DIR / ".env")

# =============================================================================
# ORIGEN
# =============================================================================
# URL de conexión SQLAlchemy. Ejemplos:
#   PostgreSQL : postgresql://usuario:clave@servidor:5432/base
#   SQL Server : mssql+pyodbc://usuario:clave@servidor/base?driver=ODBC+Driver+17+for+SQL+Server
DB_URL = os.environ.get("DB_URL") or f"sqlite:///{(DATOS_DIR / 'demo.db').as_posix()}"
DB_ESQUEMA = os.environ.get("DB_ESQUEMA") or None
TABLA_ORIGEN = os.environ.get("TABLA_ORIGEN", "ventas")

COLUMNA_FECHA = "fecha"
COLUMNAS = ["fecha", "documento", "linea", "cliente", "producto", "cantidad", "importe", "costo"]
CLAVE = ["documento", "linea"]                 # identifica una fila: no puede repetirse
COLUMNAS_TEXTO = ["documento", "cliente", "producto"]
COLUMNAS_NUMERO = ["linea", "cantidad", "importe", "costo"]

# =============================================================================
# EXTRACCIÓN
# =============================================================================
PRIMERA_FECHA = "2025-01-01"   # desde dónde se carga la primera vez

# Cada corrida vuelve a traer los últimos N días (no sólo lo nuevo) y los
# REEMPLAZA enteros: en casi cualquier sistema se puede anular o corregir un
# registro días después, y sin esta ventana el cambio no llegaría al reporte.
DIAS_RETROACTIVOS = 3

# False = el reporte cierra AYER. Útil cuando el día en curso le llega a
# Power BI por otro camino (una tabla en vivo): si también viniera acá, ese
# día se contaría dos veces.
INCLUIR_DIA_EN_CURSO = False

# Un rango largo (la primera carga, una recarga de meses) se parte en bloques:
# una consulta gigante tarda tanto que un firewall puede cortar la conexión a
# mitad de camino. En bloques, si uno falla se reintenta sólo ese.
DIAS_BLOQUE_MAX = 45
MESES_POR_BLOQUE = 3
