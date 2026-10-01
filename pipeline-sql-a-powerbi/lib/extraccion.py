"""
Extracción robusta desde una base SQL.

PARA QUÉ SIRVE
--------------
Las tres piezas que hacen que una extracción larga no se caiga ni traiga
basura, separadas del script principal para poder probarlas solas:

- `verificar_columnas()`: aborta ANTES de extraer si falta una columna.
- `bloques_fecha()`: parte un rango largo en bloques.
- `extraer_bloque()`: una consulta con reintentos.

La consulta se arma con SQLAlchemy Core (no concatenando texto): las fechas
y la lista de exclusiones viajan como PARÁMETROS, así que funciona igual en
PostgreSQL, SQL Server o SQLite y no hay riesgo de inyección SQL.
"""
import time

import pandas as pd
from sqlalchemy import MetaData, Table, and_, inspect, select


def verificar_columnas(engine, tabla, columnas, esquema=None):
    """Aborta si alguna columna pedida no existe en la tabla (o vista) de origen.

    Es una consulta barata al catálogo de la base. Sin esto, un error de
    tipeo en el nombre de una columna se descubre después de esperar toda la
    extracción.
    """
    disponibles = {c["name"] for c in inspect(engine).get_columns(tabla, schema=esquema)}
    faltantes = [c for c in columnas if c not in disponibles]
    if faltantes:
        raise SystemExit(f"[ERROR] Columnas inexistentes en {tabla}: {faltantes}\n"
                         f"        Disponibles: {sorted(disponibles)}")


def bloques_fecha(desde, hasta, dias_max, meses_por_bloque):
    """Parte [desde, hasta) en bloques de `meses_por_bloque` meses.

    Un rango corto (la corrida diaria) devuelve un solo bloque. TODOS los
    bloques terminan, como mucho, en `hasta` (00:00 de hoy): el día en curso
    queda afuera por construcción, a cualquier hora que corra el script.

    >>> bloques_fecha(pd.Timestamp("2026-09-28"), pd.Timestamp("2026-10-01"), 45, 3)
    [(Timestamp('2026-09-28 00:00:00'), Timestamp('2026-10-01 00:00:00'))]
    """
    if (hasta - desde).days <= dias_max:
        return [(desde, hasta)]
    bloques, inicio = [], desde
    while True:
        fin = inicio + pd.offsets.MonthBegin(meses_por_bloque)
        if fin >= hasta:
            bloques.append((inicio, hasta))
            return bloques
        bloques.append((inicio, fin))
        inicio = fin


def armar_consulta(engine, tabla, columnas, desde, hasta, excluir_operaciones, esquema=None):
    """SELECT columnas FROM tabla WHERE fecha en [desde, hasta) AND operación no excluida."""
    t = Table(tabla, MetaData(), schema=esquema, autoload_with=engine)
    return select(*[t.c[c] for c in columnas]).where(and_(
        t.c.fecha_emision >= desde.to_pydatetime(),
        t.c.fecha_emision < hasta.to_pydatetime(),
        t.c.tipo_operacion.not_in(list(excluir_operaciones)),
    ))


def extraer_bloque(engine, consulta, reintentos=3, espera_s=120):
    """Ejecuta la consulta con reintentos y devuelve un DataFrame.

    `engine.dispose()` entre intentos es a propósito: cuando un firewall
    corta el socket, la conexión muerta queda en el pool y el reintento la
    reusaría y volvería a fallar sin siquiera salir a la red.
    """
    for intento in range(1, reintentos + 1):
        t0 = time.time()
        try:
            df = pd.read_sql(consulta, engine)
            print(f"   [OK] {len(df):,} filas en {time.time() - t0:.1f} s")
            return df
        except Exception as e:   # noqa: BLE001 - cualquier error de red/BD se reintenta igual
            print(f"   [AVISO] intento {intento}/{reintentos} falló tras {time.time() - t0:.0f} s: "
                  f"{str(e).splitlines()[0]}")
            engine.dispose()
            if intento == reintentos:
                raise
            print(f"   ... reintento en {espera_s} s")
            time.sleep(espera_s)
