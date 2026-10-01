"""
Existencias de Odoo -> Excel de stock por depósito. Corre a las 07:30 y a las 13:00.

PARA QUÉ SIRVE
--------------
Reemplaza la exportación manual del stock: deja en
`datos/stock_por_deposito.xlsx` cuánto hay de cada artículo en cada depósito,
con los nombres de depósito EXACTOS que usan los filtros de Power BI.

CÓMO LO HACE
------------
1. Lee las ubicaciones internas de Odoo (`stock.location`, usage = internal):
   las de clientes, proveedores y ajustes de inventario son virtuales.
2. Lee las existencias (`stock.quant`, campo `quantity` = existencia física)
   de esas ubicaciones.
3. Traduce cada ubicación a un depósito:
   - las del mapa DEPOSITO_POR_UBICACION, con su nombre de los informes;
   - las zonas creadas DENTRO del depósito central (estantes, pasillos) se
     suman a CENTRAL;
   - una ubicación nueva que nadie mapeó sale con su propio nombre y un
     [AVISO]. Si se descartara o se sumara en silencio, la medida
     "Existencias Central" de Power BI la perdería sin que nadie lo note.
4. Compara contra el archivo anterior, depósito por depósito: un salto raro
   (un depósito que quedó en cero) se ve en el log antes que en el informe.
"""
import sys
import time

import pandas as pd

import config
from lib import consola, escritura
from lib.odoo_cliente import conectar, m2o


def deposito_de(ubicacion: str, avisos: set) -> str:
    if ubicacion in config.DEPOSITO_POR_UBICACION:
        return config.DEPOSITO_POR_UBICACION[ubicacion]
    if ubicacion in config.ZONAS_CENTRAL:
        return config.DEPOSITO_POR_UBICACION["WH/Stock"]
    avisos.add(ubicacion)
    return ubicacion


def main():
    consola.preparar("stock")
    inicio = time.time()
    if escritura.archivo_tomado(config.EXCEL_STOCK):
        print(f"[SKIP] {config.EXCEL_STOCK.name} está abierto; se escribe en la próxima corrida")
        return escritura.CODIGO_SALTEADO

    odoo = conectar()
    ubicaciones = {u["id"]: u["complete_name"] for u in
                   odoo.search_read("stock.location", [("usage", "=", "internal")], ["complete_name"])}
    quants = odoo.search_read("stock.quant", [("location_id", "in", list(ubicaciones))],
                              ["product_id", "location_id", "quantity"])
    productos = {p["id"]: (p["default_code"] or "").strip() for p in odoo.read(
        "product.product", {m2o(q["product_id"])[0] for q in quants}, ["default_code"])}

    avisos = set()
    filas = [{"Deposito": deposito_de(ubicaciones[m2o(q["location_id"])[0]], avisos),
              "Cod Articulo": productos.get(m2o(q["product_id"])[0], ""),
              "Existencia": float(q["quantity"])} for q in quants]
    stock = (pd.DataFrame(filas).groupby(["Deposito", "Cod Articulo"], as_index=False)["Existencia"].sum())
    stock = stock[stock["Existencia"] != 0].sort_values(["Deposito", "Cod Articulo"])
    for ubicacion in sorted(avisos):
        print(f"   [AVISO] ubicación sin depósito asignado: '{ubicacion}' (sale con su propio nombre; "
              f"agregarla a DEPOSITO_POR_UBICACION o ZONAS_CENTRAL)")

    # Comparación con la foto anterior
    actual = stock.groupby("Deposito")["Existencia"].sum()
    if config.EXCEL_STOCK.exists():
        anterior = pd.read_excel(config.EXCEL_STOCK).groupby("Deposito")["Existencia"].sum()
        comp = pd.DataFrame({"anterior": anterior, "actual": actual}).fillna(0).astype(float)
        comp["diferencia"] = comp["actual"] - comp["anterior"]
        print(comp.sort_values("actual", ascending=False).to_string(float_format="{:,.0f}".format))
    else:
        print(actual.to_string(float_format="{:,.0f}".format))

    try:
        escritura.escribir_excel(stock, config.EXCEL_STOCK, hoja="Stock", formatos={"Existencia": "#,##0"})
    except escritura.ArchivoTomado:
        print(f"[SKIP] {config.EXCEL_STOCK.name} se tomó durante la corrida")
        return escritura.CODIGO_SALTEADO
    print(f"[OK] {len(stock):,} filas, {stock['Existencia'].sum():,.0f} unidades "
          f"en {time.time() - inicio:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
