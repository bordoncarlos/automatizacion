"""
01 · Extracción incremental de ventas: base SQL -> parquet histórico.

PARA QUÉ SIRVE
--------------
Mantiene `datos/ventas_historicas.parquet` (lo que lee Power BI) al día,
trayendo de la base sólo lo necesario. Es el primer paso de la cadena diaria.

CÓMO LO HACE
------------
1. Toma un lock: si otro script está escribiendo el parquet, aborta.
2. Calcula la ventana: desde (última fecha del parquet - N días) hasta las
   00:00 de HOY. Los últimos N días se vuelven a traer y se REEMPLAZAN
   enteros, porque el sistema permite anular o corregir comprobantes días
   después de emitidos.
3. Verifica que existan todas las columnas antes de consultar.
4. Extrae (partiendo en bloques si el rango es largo, con reintentos).
5. Une: histórico ANTERIOR a la ventana + lo recién extraído.
6. Aplica las reglas de negocio (lib/reglas_negocio.py).
7. Escribe el parquet de forma atómica.

EL DÍA EN CURSO NO ENTRA NUNCA
------------------------------
El parquet cierra al final de AYER. En Power BI, el histórico se une con una
tabla que trae el día en curso al minuto desde el sistema. Si el parquet
también trajera filas de hoy, ese día se contaría DOS veces. Por eso el tope
de la consulta es `< hoy 00:00`, explícito: no depende de la hora a la que
corra el script.

USO
    python 01_extraer_ventas.py                     # corrida diaria
    python 01_extraer_ventas.py --desde 2026-01-01  # re-extraer un rango (backfill)
"""
import argparse
import sys
import time

import pandas as pd
from sqlalchemy import create_engine

import config
from lib import extraccion, parquet_seguro, reglas_negocio


def leer_argumentos():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--desde", metavar="AAAA-MM-DD",
                    help="re-extraer desde esta fecha (por defecto: ventana móvil de "
                         f"{config.DIAS_RETROACTIVOS} días)")
    ap.add_argument("--reintentos", type=int, default=3)
    ap.add_argument("--espera", type=int, default=120, help="segundos entre reintentos")
    return ap.parse_args()


def calcular_ventana(historico: pd.DataFrame, desde_arg, hoy: pd.Timestamp):
    """Devuelve (desde, hasta) de la extracción. `hasta` es siempre hoy 00:00."""
    if desde_arg:
        desde = pd.Timestamp(desde_arg)
        print(f"[BACKFILL] se re-extrae desde {desde:%d/%m/%Y}")
    elif historico.empty:
        desde = pd.Timestamp(config.PRIMERA_FECHA)
        print("Parquet inexistente o vacío: carga inicial")
    else:
        ultima = historico["fecha_emision"].max().normalize()
        desde = ultima - pd.Timedelta(days=config.DIAS_RETROACTIVOS)
    return desde, hoy


def resumen_por_dia(df: pd.DataFrame, desde: pd.Timestamp, max_dias=15):
    """Tabla corta para el log: qué días se reemplazaron y con cuánto."""
    ventana = df[df["fecha_emision"] >= desde]
    if ventana.empty:
        return
    tabla = (ventana.assign(dia=ventana["fecha_emision"].dt.date)
             .groupby("dia").agg(comprobantes=("nro_comprobante", "nunique"),
                                 lineas=("nro_comprobante", "size"),
                                 neto_gs=("monto_neto", "sum")))
    if len(tabla) > max_dias:
        print(f"(últimos {max_dias} de {len(tabla)} días extraídos)")
    print(tabla.tail(max_dias).to_string(formatters={"neto_gs": "{:,.0f}".format}))


def main():
    args = leer_argumentos()
    inicio = time.time()
    print("=" * 60)
    print("01 · EXTRACCIÓN DE VENTAS (base SQL -> parquet)")
    print("=" * 60)

    parquet_seguro.adquirir_lock_escritura(config.PARQUET_VENTAS)

    historico = pd.DataFrame()
    if config.PARQUET_VENTAS.exists():
        historico = pd.read_parquet(config.PARQUET_VENTAS)

    hoy = pd.Timestamp.now().normalize()
    desde, hasta = calcular_ventana(historico, args.desde, hoy)
    print(f"Ventana: {desde:%d/%m/%Y} -> {hasta - pd.Timedelta(days=1):%d/%m/%Y} "
          f"(el día en curso queda afuera)")
    if desde >= hasta:
        print("[OK] El parquet ya cierra en ayer. Nada que hacer.")
        return 0

    engine = create_engine(config.DB_URL)
    extraccion.verificar_columnas(engine, config.DB_TABLA_VENTAS, config.COLUMNAS_VENTAS,
                                  config.DB_ESQUEMA)

    bloques = extraccion.bloques_fecha(desde, hasta, config.DIAS_BLOQUE_MAX, config.MESES_POR_BLOQUE)
    partes = []
    for n, (ini, fin) in enumerate(bloques, start=1):
        print(f"-> Bloque {n}/{len(bloques)}: {ini:%d/%m/%Y} -> {fin:%d/%m/%Y}")
        consulta = extraccion.armar_consulta(engine, config.DB_TABLA_VENTAS, config.COLUMNAS_VENTAS,
                                             ini, fin, config.TIPOS_OPERACION_EXCLUIR, config.DB_ESQUEMA)
        partes.append(extraccion.extraer_bloque(engine, consulta, args.reintentos, args.espera))
    nuevos = pd.concat(partes, ignore_index=True)
    nuevos = reglas_negocio.normalizar_tipos(nuevos)

    # Unir: lo viejo que queda ANTES de la ventana + lo recién extraído.
    # Las filas de la ventana se descartan del histórico aunque la base ya no
    # las traiga: así un comprobante anulado desaparece también del parquet.
    if not historico.empty:
        historico = historico[historico["fecha_emision"] < desde]
    final = pd.concat([historico, nuevos], ignore_index=True)

    # Defensa extra: nada de hoy (ver el docstring).
    final = final[final["fecha_emision"] < hoy]

    final = reglas_negocio.postprocesar(final)
    final, n_corr = reglas_negocio.aplicar_correcciones_guardadas(final, config.CORRECCIONES)
    if n_corr:
        print(f"Correcciones de la conciliación reaplicadas: {n_corr:,} líneas")
    resumen_por_dia(final, desde)

    parquet_seguro.escribir_parquet_seguro(final, config.PARQUET_VENTAS)
    print(f"[OK] {len(final):,} líneas en {config.PARQUET_VENTAS.name} "
          f"({time.time() - inicio:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
