"""
Crea una base de datos de DEMOSTRACIÓN para probar el script sin conectarse
a ningún sistema real.

Genera `datos/demo.db` (SQLite) con una tabla `ventas` de una empresa
inventada: una fila por línea de documento, desde 2025 hasta HOY inclusive
(el día en curso está a propósito, para ver que el reporte lo deja afuera).
Con la misma semilla se generan siempre los mismos datos.

USO
    python generar_datos_demo.py
"""
import random
from datetime import timedelta

import pandas as pd
from sqlalchemy import DateTime, create_engine

import config

rng = random.Random(42)
PRODUCTOS = {f"P{1001 + i}": round(rng.uniform(10_000, 500_000), -2) for i in range(50)}
CLIENTES = [f"C{n:04d}" for n in range(1, 301)]


def main():
    hoy = pd.Timestamp.now().normalize()
    filas, numero = [], 0
    dia = pd.Timestamp(config.PRIMERA_FECHA)
    while dia <= hoy:
        for _ in range(0 if dia.dayofweek == 6 else rng.randint(8, 20)):
            numero += 1
            hora = dia + timedelta(hours=rng.randint(8, 18), minutes=rng.randint(0, 59))
            cliente = rng.choice(CLIENTES)
            for linea, producto in enumerate(rng.sample(list(PRODUCTOS), rng.randint(1, 4)), start=1):
                cantidad = rng.randint(1, 30)
                importe = cantidad * PRODUCTOS[producto]
                filas.append({"fecha": hora, "documento": f"F-{numero:07d}", "linea": linea,
                              "cliente": cliente, "producto": producto, "cantidad": cantidad,
                              "importe": importe, "costo": round(importe * rng.uniform(0.55, 0.8))})
        dia += pd.Timedelta(days=1)

    df = pd.DataFrame(filas)
    ruta = config.DATOS_DIR / "demo.db"
    ruta.unlink(missing_ok=True)
    df.to_sql("ventas", create_engine(f"sqlite:///{ruta.as_posix()}"), index=False,
              dtype={"fecha": DateTime()})
    print(f"[OK] {ruta.name}: {len(df):,} filas, {df['documento'].nunique():,} documentos "
          f"({df['fecha'].min():%d/%m/%Y} -> {df['fecha'].max():%d/%m/%Y})")


if __name__ == "__main__":
    main()
