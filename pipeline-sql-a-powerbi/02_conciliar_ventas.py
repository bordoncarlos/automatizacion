"""
02 · Conciliación: parquet histórico vs. reporte oficial del sistema.

PARA QUÉ SIRVE
--------------
La base de datos y el reporte que el área comercial ve en el sistema no
siempre coinciden: descuentos cargados después, comprobantes anulados,
documentos que entraron tarde. Si el informe de Power BI no da lo mismo que
el reporte oficial, nadie confía en el informe. Este paso compara los dos
comprobante por comprobante y, si se pide, corrige el parquet: ante una
diferencia, MANDA EL REPORTE OFICIAL.

CÓMO LO HACE
------------
1. Suma el parquet por comprobante en el rango (por defecto, del 1 del mes
   hasta ayer).
2. Lo cruza con el reporte (un CSV exportado del sistema) y clasifica:
   OK · DIFERENCIA · SOLO EN REPORTE · SOLO EN PARQUET.
3. Lista CADA comprobante con diferencia (el total solo no alcanza para
   encontrar el problema) y guarda el detalle en un Excel.
4. Con --corregir:
   - DIFERENCIA: lleva el comprobante al monto oficial, repartiendo el
     nuevo total entre sus líneas (prorrateo con suma exacta) y
     recalculando el total con IVA de cada línea.
   - SOLO EN PARQUET (anulado en el sistema): lo pone en 0. No lo borra:
     las cantidades siguen sirviendo para el análisis de demanda.
   - SOLO EN REPORTE: no se puede inventar el detalle por artículo; se
     informa para re-extraer ese día.
   Antes de corregir, guarda en una bitácora el monto que tenía el parquet:
   una vez corregido, la evidencia de la diferencia desaparece.

TOPE DE SEGURIDAD: si la diferencia total supera el 2 % de lo vendido en el
período, no corrige nada. Algo grande está mal (un día sin extraer, un
reporte exportado a medias) y corregir a ciegas lo empeoraría.

USO
    python 02_conciliar_ventas.py
    python 02_conciliar_ventas.py --desde 2026-09-01 --hasta 2026-09-30 --corregir
"""
import argparse
import sys
from datetime import datetime

import pandas as pd

import config
from lib import parquet_seguro, reglas_negocio

TOLERANCIA_GS = 5   # redondeos de 1-5 Gs entre la base (entera) y el reporte (decimal)


def leer_argumentos():
    ayer = pd.Timestamp.now().normalize() - pd.Timedelta(days=1)
    ap = argparse.ArgumentParser(description="Concilia el parquet contra el reporte oficial")
    ap.add_argument("--desde", default=f"{ayer:%Y-%m}-01")
    ap.add_argument("--hasta", default=f"{ayer:%Y-%m-%d}", help="inclusive")
    ap.add_argument("--corregir", action="store_true", help="aplicar las correcciones al parquet")
    return ap.parse_args()


def comparar(parquet: pd.DataFrame, reporte: pd.DataFrame, desde, hasta) -> pd.DataFrame:
    """Una fila por comprobante con el monto de cada lado y su estado."""
    en_rango = parquet[(parquet["fecha_emision"] >= desde)
                       & (parquet["fecha_emision"] < hasta + pd.Timedelta(days=1))]
    lado_p = en_rango.groupby("nro_comprobante", as_index=False)["monto_neto"].sum()
    rep = reporte[(reporte["fecha"] >= desde) & (reporte["fecha"] <= hasta)]
    lado_r = rep.groupby("nro_comprobante", as_index=False)["monto_neto"].sum()

    cruce = lado_p.merge(lado_r, on="nro_comprobante", how="outer",
                         suffixes=("_parquet", "_reporte"), indicator=True)
    cruce["diferencia"] = cruce["monto_neto_reporte"].fillna(0) - cruce["monto_neto_parquet"].fillna(0)
    cruce["estado"] = "OK"
    cruce.loc[cruce["diferencia"].abs() > TOLERANCIA_GS, "estado"] = "DIFERENCIA"
    # sólo en el parquet y ya en 0 = un anulado que se corrigió antes: está bien
    cruce.loc[(cruce["_merge"] == "left_only") & (cruce["diferencia"].abs() > TOLERANCIA_GS),
              "estado"] = "SOLO EN PARQUET"
    cruce.loc[cruce["_merge"] == "right_only", "estado"] = "SOLO EN REPORTE"
    return cruce.drop(columns="_merge").sort_values(["estado", "nro_comprobante"])


def aplicar_correcciones(parquet: pd.DataFrame, cruce: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """Lleva el parquet a los montos oficiales. Devuelve (parquet, bitácora)."""
    bitacora = []
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    # DIFERENCIA y SOLO EN PARQUET: el nuevo total del comprobante es el del
    # reporte (0 si el reporte no lo trae: anulado).
    a_corregir = cruce[cruce["estado"].isin(["DIFERENCIA", "SOLO EN PARQUET"])]
    objetivos = dict(zip(a_corregir["nro_comprobante"], a_corregir["monto_neto_reporte"].fillna(0)))
    if not objetivos:
        return parquet, bitacora

    parquet = reglas_negocio.llevar_a_monto(parquet, objetivos)

    for fila in a_corregir.itertuples(index=False):
        bitacora.append({"fecha_correccion": ahora, "nro_comprobante": fila.nro_comprobante,
                         "estado": fila.estado, "monto_parquet_previo": fila.monto_neto_parquet,
                         "monto_oficial": fila.monto_neto_reporte if pd.notna(fila.monto_neto_reporte) else 0})
    return parquet, bitacora


def main():
    args = leer_argumentos()
    desde, hasta = pd.Timestamp(args.desde), pd.Timestamp(args.hasta)
    print("=" * 60)
    print(f"02 · CONCILIACIÓN {desde:%d/%m/%Y} -> {hasta:%d/%m/%Y}")
    print("=" * 60)

    if args.corregir:
        parquet_seguro.adquirir_lock_escritura(config.PARQUET_VENTAS)
    parquet = pd.read_parquet(config.PARQUET_VENTAS)
    reporte = pd.read_csv(config.REPORTE_OFICIAL, dtype={"nro_comprobante": str})
    reporte["fecha"] = pd.to_datetime(reporte["fecha"])

    cruce = comparar(parquet, reporte, desde, hasta)
    print(cruce["estado"].value_counts().to_string())
    problemas = cruce[cruce["estado"] != "OK"]
    total_dif = problemas["diferencia"].sum()
    vendido = cruce["monto_neto_reporte"].sum()
    print(f"\nDiferencia total: {total_dif:,.0f} Gs ({abs(total_dif) / vendido:.3%} de lo vendido)")

    if problemas.empty:
        print("[OK] El parquet coincide con el reporte oficial.")
        return 0

    # Cada comprobante, no sólo el total: es lo que permite ir a buscarlo.
    print("\nComprobantes con diferencia:")
    print(problemas[["nro_comprobante", "estado", "monto_neto_parquet", "monto_neto_reporte",
                     "diferencia"]].to_string(index=False, float_format="{:,.0f}".format))
    salida = config.DATOS_DIR / f"conciliacion_{datetime.now():%Y%m%d}.xlsx"
    cruce.to_excel(salida, index=False)
    print(f"\nDetalle: {salida}")

    solo_reporte = problemas[problemas["estado"] == "SOLO EN REPORTE"]
    if not solo_reporte.empty:
        print(f"[AVISO] {len(solo_reporte)} comprobante(s) sólo en el reporte: re-extraer esos días "
              f"con 01_extraer_ventas.py --desde")

    if not args.corregir:
        print("(sin --corregir: no se modificó el parquet)")
        return 0
    if abs(total_dif) > config.TOPE_CORRECCION_AUTOMATICA * vendido:
        print(f"[ABORTADO] La diferencia supera el {config.TOPE_CORRECCION_AUTOMATICA:.0%}: "
              f"revisar a mano antes de corregir.")
        return 1

    parquet, bitacora = aplicar_correcciones(parquet, cruce)
    if bitacora:
        nueva = pd.DataFrame(bitacora)
        if config.CORRECCIONES.exists():
            nueva = pd.concat([pd.read_csv(config.CORRECCIONES), nueva], ignore_index=True)
        nueva.to_csv(config.CORRECCIONES, index=False)
        parquet_seguro.escribir_parquet_seguro(parquet, config.PARQUET_VENTAS)
        print(f"[OK] {len(bitacora)} comprobante(s) corregidos; bitácora en {config.CORRECCIONES.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
