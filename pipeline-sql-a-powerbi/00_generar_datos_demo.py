"""
00 · Genera una base de datos de DEMOSTRACIÓN para probar el pipeline sin
acceso a ningún sistema real.

PARA QUÉ SIRVE
--------------
Crea `datos/erp_demo.db` (SQLite) con las dos tablas que lee el pipeline, como
las tendría el sistema de gestión de una ferretería mayorista ficticia:

- `ventas`: facturas y notas de crédito línea por línea, desde 2025 hasta HOY
  inclusive (el día en curso está a propósito: el pipeline lo tiene que dejar
  afuera).
- `stock_actual`: existencias por depósito; como en muchos sistemas, sin
  historia (sólo el valor de este momento).

Y `datos/reporte_oficial_ventas.csv`, el "reporte de ventas" que exportaría
el sistema, con diferencias sembradas a propósito para que la conciliación
(02) tenga algo que encontrar.

Todo es inventado: no hay datos de ninguna empresa real. Con la misma
semilla se generan siempre los mismos datos.

USO
    python 00_generar_datos_demo.py
"""
import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import DateTime, create_engine

import config

SEMILLA = 42
rng = random.Random(SEMILLA)
np_rng = np.random.default_rng(SEMILLA)

HOY = pd.Timestamp.now().normalize()
AYER = HOY - pd.Timedelta(days=1)
INICIO = pd.Timestamp(config.PRIMERA_FECHA)

# --- Catálogo: (código, descripción, precio sin IVA en Gs, tasa IVA) ----------
FAMILIAS = ["CEMENTO", "PINTURA", "CAÑO PVC", "GRIFERIA", "CERAMICO", "HERRAMIENTA",
            "ELECTRICIDAD", "ADHESIVO", "SANITARIO", "TORNILLERIA"]
ARTICULOS = []
for i in range(60):
    familia = FAMILIAS[i % len(FAMILIAS)]
    precio = round(rng.uniform(8_000, 900_000), -2)
    tasa = 1.10 if i % 20 not in (7, 13) else (1.05 if i % 20 == 7 else 1.00)
    ARTICULOS.append((f"A{1001 + i}", f"{familia} MODELO {i + 1:02d}", precio, tasa))

CLIENTES = [f"CLI-{n:04d}" for n in range(1, 401)]

# --- Vendedores: (código, sucursal propia, activo desde, activo hasta) -------
VENDEDORES = [
    ("V01", None, "2025-01-01", None),          # gerente: vende en todas
    ("V02", "01", "2025-01-01", None), ("V03", "01", "2025-01-01", None),
    ("V04", "02", "2025-01-01", None), ("V05", "02", "2025-01-01", None),
    ("V06", "03", "2025-01-01", None), ("V07", "03", "2025-01-01", "2025-06-30"),  # se fue
    ("V08", "04", "2025-01-01", None), ("V09", "02", "2025-01-01", None),
    ("V10", "01", "2025-01-01", None), ("V11", "04", "2025-01-01", None),
    ("V12", "03", "2025-07-01", None),          # heredó la cartera de V07
    ("V15", "01", "2025-01-01", "2025-03-31"),  # se fue; cartera a V03
]


def vendedores_activos(fecha):
    f = fecha.strftime("%Y-%m-%d")
    return [v for v in VENDEDORES if v[2] <= f and (v[3] is None or f <= v[3])]


def sucursal_de(vendedor, fecha):
    """Dónde factura un vendedor ese día (con las excepciones de la demo)."""
    cod, propia = vendedor[0], vendedor[1]
    if propia is None:                                       # V01, en cualquiera
        return rng.choice(["01", "02", "03", "04"])
    if cod == "V09" and fecha >= pd.Timestamp("2026-08-01"):  # atiende dos sucursales
        return rng.choice(["02", "03"])
    if fecha.month == 7 and cod in ("V04", "V08") and rng.random() < 0.7:
        return "90"                                          # julio: feria
    if rng.random() < 0.03:
        return rng.choice(["01", "02", "03", "04"])           # cubre a un compañero
    return propia


def generar_ventas():
    filas, numeradores = [], {}
    fecha = INICIO
    while fecha <= HOY:
        n_facturas = 0 if fecha.dayofweek == 6 else int(np_rng.poisson(18 if fecha.dayofweek < 5 else 8))
        for _ in range(n_facturas):
            vend = rng.choice(vendedores_activos(fecha))
            suc = sucursal_de(vend, fecha)
            numeradores[suc] = numeradores.get(suc, 0) + 1
            nro = f"{suc}-001-{numeradores[suc]:07d}"
            hora = fecha + timedelta(hours=rng.randint(7, 18), minutes=rng.randint(0, 59))
            cliente = rng.choice(CLIENTES)
            condicion = "CONTADO" if rng.random() < 0.6 else "CREDITO 30 DIAS"
            usd = rng.random() < 0.03
            cotiz = round(rng.uniform(7_300, 7_800), 0) if usd else 1.0
            operacion = "TRASLADO INTERNO" if rng.random() < 0.01 else "VENTA"
            for cod, _, precio, tasa in rng.sample(ARTICULOS, rng.randint(1, 5)):
                cant = rng.randint(1, 40)
                desc = rng.choice([0, 0, 0, 5, 10])
                neto = round(cant * precio * (1 - desc / 100))
                filas.append({
                    "fecha_emision": hora, "nro_comprobante": nro, "tipo_comprobante": "FACTURA",
                    "tipo_operacion": operacion, "cliente": cliente, "cod_vendedor": vend[0],
                    "cod_sucursal": suc, "cod_articulo": cod, "cantidad": cant,
                    "precio_unitario": precio, "monto_neto": neto, "monto_total": round(neto * tasa),
                    "monto_costo": round(cant * precio * rng.uniform(0.55, 0.8)),
                    "condicion_venta": condicion, "moneda": "USD" if usd else "GS", "cotizacion": cotiz,
                })
        fecha += pd.Timedelta(days=1)
    df = pd.DataFrame(filas)

    # Notas de crédito: ~3 % de las facturas tienen una devolución días después.
    facturas = df[df["tipo_operacion"] == "VENTA"].drop_duplicates("nro_comprobante")
    devueltas = facturas.sample(frac=0.03, random_state=SEMILLA)
    ncs = []
    for n, f in enumerate(devueltas.itertuples(index=False), start=1):
        linea = df[df["nro_comprobante"] == f.nro_comprobante].iloc[0].to_dict()
        fecha_nc = min(f.fecha_emision + timedelta(days=rng.randint(1, 10)), HOY + timedelta(hours=9))
        cant = max(1, linea["cantidad"] // 2)
        factor = cant / linea["cantidad"]
        linea.update({
            "fecha_emision": fecha_nc, "nro_comprobante": f"NC-{n:06d}", "tipo_comprobante": "NOTA CREDITO",
            "cantidad": -cant, "monto_neto": -round(linea["monto_neto"] * factor),
            "monto_total": -round(linea["monto_total"] * factor),
            "monto_costo": -round(linea["monto_costo"] * factor),
        })
        ncs.append(linea)
    return pd.concat([df, pd.DataFrame(ncs)], ignore_index=True).sort_values("fecha_emision")


def generar_stock():
    depositos = ["CENTRAL", "NORTE", "SUR", "ESTE", "AVERIADOS CENTRAL", "MUESTRAS", "SHOW ROOM"]
    filas = []
    for cod, *_ in ARTICULOS:
        for dep in depositos:
            if rng.random() < 0.8:
                base = 400 if dep == "CENTRAL" else (60 if dep in ("NORTE", "SUR", "ESTE") else 5)
                filas.append({"deposito": dep, "cod_articulo": cod, "cantidad": rng.randint(0, base)})
    return pd.DataFrame(filas)


def generar_reporte_oficial(ventas):
    """El reporte que exportaría el sistema: un total por comprobante.

    Se siembran tres tipos de diferencia en el mes de ayer, como las que
    aparecen en la vida real:
      - 3 comprobantes con otro monto (un descuento cargado después);
      - 1 comprobante que el reporte trae y la base no (se cargó tarde);
      - 1 comprobante que la base trae y el reporte no (anulado).
    """
    validas = ventas[(ventas["tipo_operacion"] == "VENTA") & (ventas["fecha_emision"] < HOY)]
    rep = (validas.groupby(["nro_comprobante", "tipo_comprobante"], as_index=False)
           .agg(fecha=("fecha_emision", "min"), monto_neto=("monto_neto", "sum")))
    rep["fecha"] = rep["fecha"].dt.date

    del_mes = rep[(pd.to_datetime(rep["fecha"]) >= AYER.replace(day=1))
                  & (rep["tipo_comprobante"] == "FACTURA")].sample(5, random_state=SEMILLA)
    con_descuento, anulada = del_mes.index[:3], del_mes.index[3]
    rep.loc[con_descuento, "monto_neto"] = (rep.loc[con_descuento, "monto_neto"] * 0.95).round()
    rep = rep.drop(index=anulada)
    extra = pd.DataFrame([{"nro_comprobante": "04-001-9999999", "tipo_comprobante": "FACTURA",
                           "fecha": AYER.date(), "monto_neto": 1_250_000}])
    rep = pd.concat([rep, extra], ignore_index=True)
    return rep[["fecha", "nro_comprobante", "tipo_comprobante", "monto_neto"]]


def main():
    print("Generando datos de demostración (semilla fija)...")
    ventas = generar_ventas()
    stock = generar_stock()
    reporte = generar_reporte_oficial(ventas)

    ruta_db = config.DATOS_DIR / "erp_demo.db"
    ruta_db.unlink(missing_ok=True)
    engine = create_engine(f"sqlite:///{ruta_db.as_posix()}")
    ventas.to_sql("ventas", engine, index=False, dtype={"fecha_emision": DateTime()})
    stock.to_sql("stock_actual", engine, index=False)
    reporte.to_csv(config.REPORTE_OFICIAL, index=False, encoding="utf-8")

    print(f"  ventas       : {len(ventas):,} líneas, {ventas['nro_comprobante'].nunique():,} comprobantes "
          f"({ventas['fecha_emision'].min():%d/%m/%Y} -> {ventas['fecha_emision'].max():%d/%m/%Y})")
    print(f"  stock_actual : {len(stock):,} filas")
    print(f"  reporte      : {len(reporte):,} comprobantes (con 5 diferencias sembradas)")
    print(f"[OK] {ruta_db}")


if __name__ == "__main__":
    main()
