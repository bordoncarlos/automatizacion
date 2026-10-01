"""
Actualiza el reporte: base SQL -> transformación -> archivo que lee Power BI.

PARA QUÉ SIRVE
--------------
Reemplaza el "exportar el reporte del sistema y pegarlo en Excel" de todos
los días. Mantiene `datos/reporte.parquet` al día trayendo de la base sólo lo
necesario, y no lo pisa nunca con datos que no pasaron la validación.

CÓMO LO HACE
------------
1. Toma un lock: si otra corrida está escribiendo el archivo, aborta.
2. Calcula la ventana: desde (última fecha del reporte - N días) hasta hoy.
   Los últimos N días se vuelven a traer y se REEMPLAZAN enteros, así un
   registro anulado o corregido en el sistema también cambia en el reporte.
3. Verifica que existan las columnas antes de consultar.
4. Extrae, partiendo en bloques si el rango es largo, con reintentos.
5. Une: lo que ya tenía ANTES de la ventana + lo recién extraído.
6. Transforma y VALIDA. Si algo no cierra, termina con error sin escribir.
7. Escribe el archivo de forma atómica.

USO
    python actualizar_reporte.py                     # corrida normal
    python actualizar_reporte.py --desde 2026-01-01  # recargar un rango
"""
import argparse
import sys
import time

import pandas as pd
from sqlalchemy import create_engine

import config
from lib import extraccion, parquet_seguro, transformacion


def calcular_ventana(reporte: pd.DataFrame, desde_arg=None, hoy=None):
    """(desde, hasta) de la extracción."""
    hoy = (hoy or pd.Timestamp.now()).normalize()
    hasta = hoy + pd.Timedelta(days=1) if config.INCLUIR_DIA_EN_CURSO else hoy
    if desde_arg:
        desde = pd.Timestamp(desde_arg)
    elif reporte.empty:
        desde = pd.Timestamp(config.PRIMERA_FECHA)
    else:
        desde = reporte[config.COLUMNA_FECHA].max().normalize() - pd.Timedelta(days=config.DIAS_RETROACTIVOS)
    return desde, hasta


def unir(reporte: pd.DataFrame, nuevos: pd.DataFrame, desde: pd.Timestamp) -> pd.DataFrame:
    """Lo anterior a la ventana + lo nuevo. Las filas de la ventana se
    descartan aunque la base ya no las traiga: así un anulado desaparece."""
    if reporte.empty:
        return nuevos.reset_index(drop=True)
    anterior = reporte[reporte[config.COLUMNA_FECHA] < desde]
    return pd.concat([anterior, nuevos], ignore_index=True)


def main():
    ap = argparse.ArgumentParser(description="Actualiza el reporte desde la base SQL")
    ap.add_argument("--desde", metavar="AAAA-MM-DD", help="recargar desde esta fecha")
    ap.add_argument("--reintentos", type=int, default=3)
    ap.add_argument("--espera", type=int, default=120, help="segundos entre reintentos")
    args = ap.parse_args()
    inicio = time.time()

    parquet_seguro.adquirir_lock_escritura(config.ARCHIVO_REPORTE)
    reporte = pd.read_parquet(config.ARCHIVO_REPORTE) if config.ARCHIVO_REPORTE.exists() else pd.DataFrame()

    desde, hasta = calcular_ventana(reporte, args.desde)
    print(f"ACTUALIZAR REPORTE: {desde:%d/%m/%Y} -> {hasta - pd.Timedelta(days=1):%d/%m/%Y}")
    if desde >= hasta:
        print("[OK] El reporte ya está al día.")
        return 0

    engine = create_engine(config.DB_URL)
    extraccion.verificar_columnas(engine, config.TABLA_ORIGEN, config.COLUMNAS, config.DB_ESQUEMA)
    bloques = extraccion.bloques_fecha(desde, hasta, config.DIAS_BLOQUE_MAX, config.MESES_POR_BLOQUE)
    partes = []
    for n, (ini, fin) in enumerate(bloques, start=1):
        print(f"-> Bloque {n}/{len(bloques)}: {ini:%d/%m/%Y} -> {fin:%d/%m/%Y}")
        consulta = extraccion.armar_consulta(engine, config.TABLA_ORIGEN, config.COLUMNAS,
                                             config.COLUMNA_FECHA, ini, fin, config.DB_ESQUEMA)
        partes.append(extraccion.extraer_bloque(engine, consulta, args.reintentos, args.espera))

    nuevos = transformacion.transformar(pd.concat(partes, ignore_index=True))
    final = unir(reporte, nuevos, desde)

    problemas = transformacion.validar(final, hasta)
    if problemas:
        print("[ERROR] Los datos no pasaron la validación; el reporte NO se modificó:")
        for p in problemas:
            print(f"   - {p}")
        return 1

    ultimos = (final[final[config.COLUMNA_FECHA] >= desde]
               .groupby(final[config.COLUMNA_FECHA].dt.date)
               .agg(documentos=("documento", "nunique"), filas=("documento", "size"), importe=("importe", "sum")))
    print(ultimos.tail(10).to_string(formatters={"importe": "{:,.0f}".format}))

    parquet_seguro.escribir_parquet_seguro(final, config.ARCHIVO_REPORTE)
    print(f"[OK] {len(final):,} filas en {config.ARCHIVO_REPORTE.name} ({time.time() - inicio:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
