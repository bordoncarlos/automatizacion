"""
03 · Foto diaria de stock: base SQL -> historial_stock.parquet.

PARA QUÉ SIRVE
--------------
La tabla de stock del sistema sólo sabe cuánto hay AHORA: no guarda historia.
Sin historia no se puede responder "¿cuántos días estuvo este artículo sin
stock el mes pasado?" ni medir quiebres. Este paso le saca una foto por día
y la va acumulando.

Por eso es el paso que nunca se saltea: si falla la extracción de ventas, la
cadena sigue igual y saca la foto. Un día sin foto se pierde para siempre (la
fuente ya no lo tiene).

CÓMO LO HACE
------------
1. Lee las existencias actuales por depósito y artículo.
2. Clasifica cada depósito (central / sucursal / no vendible / otro) por su
   nombre, para que los informes no tengan que repetir esa lógica.
3. Agrega la foto con la fecha de hoy. Si ya había una de hoy (el script
   corrió dos veces) la reemplaza: correrlo de nuevo nunca duplica.

USO
    python 03_foto_stock.py
"""
import sys
import time

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, select

import config
from lib import parquet_seguro


def tipo_de_deposito(nombre: str) -> str:
    """Taxonomía de depósitos. El orden importa: 'AVERIADOS CENTRAL' contiene
    la palabra CENTRAL pero NO es el depósito central (match exacto)."""
    n = nombre.upper().strip()
    if any(p in n for p in config.PATRONES_NO_VENDIBLE):
        return "NO VENDIBLE"
    if n == config.DEPOSITO_CENTRAL:
        return "CENTRAL"
    if n in config.DEPOSITOS_SUCURSAL:
        return "SUCURSAL"
    return "OTRO"     # show room, outlet...: stock real que no se repone ni se traslada


def main():
    inicio = time.time()
    hoy = pd.Timestamp.now().normalize()
    print("=" * 60)
    print(f"03 · FOTO DE STOCK {hoy:%d/%m/%Y}")
    print("=" * 60)

    parquet_seguro.adquirir_lock_escritura(config.PARQUET_STOCK)
    engine = create_engine(config.DB_URL)
    tabla = Table(config.DB_TABLA_STOCK, MetaData(), schema=config.DB_ESQUEMA, autoload_with=engine)
    foto = pd.read_sql(select(tabla.c.deposito, tabla.c.cod_articulo, tabla.c.cantidad), engine)

    foto["deposito"] = foto["deposito"].astype(str).str.strip()
    foto["cod_articulo"] = foto["cod_articulo"].astype(str).str.strip()
    foto["tipo_deposito"] = foto["deposito"].map(tipo_de_deposito)
    foto.insert(0, "fecha", hoy)

    print(foto.groupby("tipo_deposito")["cantidad"].sum().to_string())

    historial = pd.DataFrame()
    if config.PARQUET_STOCK.exists():
        historial = pd.read_parquet(config.PARQUET_STOCK)
        historial = historial[historial["fecha"] != hoy]      # idempotente
    historial = pd.concat([historial, foto], ignore_index=True)

    parquet_seguro.escribir_parquet_seguro(historial, config.PARQUET_STOCK)
    print(f"[OK] {historial['fecha'].nunique()} días en el historial, "
          f"{len(foto):,} filas hoy ({time.time() - inicio:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
