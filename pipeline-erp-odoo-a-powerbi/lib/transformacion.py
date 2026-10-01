"""
Transformación: facturas y notas de crédito de Odoo -> una tabla plana para el reporte.

PARA QUÉ SIRVE
--------------
Odoo guarda los datos para la contabilidad, no para un reporte: montos con
signo contable, precios con impuesto incluido, el costo en otras líneas del
asiento, monedas distintas. Este módulo los deja en UNA fila por línea de
producto, con montos en la moneda de la empresa, lista para Power BI.

| En el reporte        | En Odoo                                                    |
|----------------------|------------------------------------------------------------|
| importe neto         | `balance` de la línea, con signo contable (-balance)       |
| precio sin impuesto  | `price_unit`, que puede venir con el impuesto incluido     |
| costo                | líneas `cogs` del asiento (contabilidad anglosajona)       |
| cotización           | amount_untaxed_signed / amount_untaxed de la cabecera      |
"""
from datetime import datetime

import pandas as pd

import config
from lib.odoo_cliente import m2o

COLUMNAS = ["fecha", "comprobante", "tipo", "cliente", "cliente_nombre", "vendedor", "diario",
            "termino_pago", "moneda", "cotizacion", "producto", "cantidad", "precio_unitario",
            "importe_neto", "importe_total", "costo"]

CAMPOS_CABECERA = ["name", "move_type", "invoice_date", "create_date", "commercial_partner_id",
                   "invoice_user_id", "journal_id", "invoice_payment_term_id", "currency_id",
                   "amount_untaxed", "amount_untaxed_signed"]
CAMPOS_LINEA = ["move_id", "display_type", "product_id", "quantity", "price_unit", "tax_ids",
                "balance", "price_total", "account_id"]


def normalizar_texto(valor) -> str:
    """Sin espacios y en mayúsculas.

    Power BI compara las claves de una relación SIN distinguir mayúsculas:
    'c0007' y 'C0007' son la misma clave para el modelo y dos filas distintas
    para pandas, y la relación deja de ser uno-a-varios.
    """
    return str(valor).strip().upper() if valor else ""


def _hora_local(create_date):
    """Odoo guarda las horas en UTC."""
    if not create_date:
        return datetime.min.time()
    return pd.Timestamp(create_date).tz_localize("UTC").tz_convert(config.ZONA_HORARIA).time()


def vacio() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNAS)


def unir(*partes) -> pd.DataFrame:
    """Concatena ignorando las partes vacías (mezclarlas cambia los tipos de las columnas)."""
    llenas = [p for p in partes if not p.empty]
    return pd.concat(llenas, ignore_index=True) if llenas else vacio()


def extraer(odoo, desde, hasta, log=None) -> pd.DataFrame:
    """Facturas y notas de crédito PUBLICADAS con fecha en [desde, hasta).

    Las canceladas no se piden (state = 'posted'). En Odoo una factura
    cancelada DESAPARECE: no deja una fila que la anule. Por eso quien llama
    re-extrae una ventana de días: así desaparece también del reporte.
    """
    log = log or print
    desde, hasta = pd.Timestamp(desde).strftime("%Y-%m-%d"), pd.Timestamp(hasta).strftime("%Y-%m-%d")

    # 1. Cabeceras
    cabeceras = odoo.search_read("account.move", [
        ("move_type", "in", ["out_invoice", "out_refund"]), ("state", "=", "posted"),
        ("invoice_date", ">=", desde), ("invoice_date", "<", hasta)], CAMPOS_CABECERA)
    log(f"   Odoo: {len(cabeceras):,} comprobantes publicados en [{desde}, {hasta})")
    if not cabeceras:
        return vacio()

    # 2. Líneas de producto y de costo, por comprobante y en lotes
    ids = [m["id"] for m in cabeceras]
    lineas = []
    for i in range(0, len(ids), config.LOTE):
        lineas += odoo.search_read("account.move.line", [
            ("move_id", "in", ids[i:i + config.LOTE]), ("display_type", "in", ["product", "cogs"])], CAMPOS_LINEA)

    # 3. Catálogos: sólo lo que aparece en estas líneas, no el catálogo entero
    productos = {p["id"]: p for p in odoo.read(
        "product.product", {m2o(l["product_id"])[0] for l in lineas if l["product_id"]}, ["default_code"])}
    clientes = {p["id"]: p for p in odoo.read(
        "res.partner", {m2o(m["commercial_partner_id"])[0] for m in cabeceras}, ["name", "vat"])}
    tasas = {t["id"]: float(t["amount"]) for t in odoo.read(
        "account.tax", {t for l in lineas for t in (l["tax_ids"] or [])}, ["amount"])}
    tipo_cuenta = {c["id"]: c["account_type"] for c in odoo.read(
        "account.account", {m2o(l["account_id"])[0] for l in lineas if l["account_id"]}, ["account_type"])}

    # 4. Costo por (comprobante, producto): cada línea vendida genera dos líneas
    #    cogs (costo de ventas al debe, mercaderías al haber). Se toma la de la
    #    cuenta de costo y se reparte por cantidad si el producto se repite.
    costo, cantidad_producto = {}, {}
    for l in lineas:
        if not l["product_id"]:
            continue
        clave = (m2o(l["move_id"])[0], m2o(l["product_id"])[0])
        if l["display_type"] == "cogs":
            if tipo_cuenta.get(m2o(l["account_id"])[0]) == "expense_direct_cost":
                costo[clave] = costo.get(clave, 0.0) + float(l["balance"])
        else:
            cantidad_producto[clave] = cantidad_producto.get(clave, 0.0) + abs(float(l["quantity"]))

    # 5. Cabeceras
    cab = {}
    for m in cabeceras:
        es_nc = m["move_type"] == "out_refund"
        _, moneda = m2o(m["currency_id"])
        cotizacion = 1.0
        if m["amount_untaxed"] and abs(float(m["amount_untaxed_signed"])) != abs(float(m["amount_untaxed"])):
            # lo que Odoo convirtió de verdad en esta factura, no una cotización del día
            cotizacion = abs(float(m["amount_untaxed_signed"]) / float(m["amount_untaxed"]))
        cliente = clientes.get(m2o(m["commercial_partner_id"])[0], {})
        cab[m["id"]] = {
            "fecha": datetime.combine(pd.Timestamp(m["invoice_date"]).date(), _hora_local(m["create_date"])),
            "comprobante": m["name"],
            "tipo": "Nota de crédito" if es_nc else "Factura",
            "cliente": normalizar_texto(cliente.get("vat")),
            "cliente_nombre": cliente.get("name") or "",
            "vendedor": m2o(m["invoice_user_id"])[1] or "",
            "diario": m2o(m["journal_id"])[1] or "",
            "termino_pago": m2o(m["invoice_payment_term_id"])[1] or "",
            "moneda": moneda,
            "cotizacion": cotizacion,
            "_signo": -1 if es_nc else 1,
        }

    # 6. Una fila por línea de producto
    filas = []
    for l in lineas:
        c = cab.get(m2o(l["move_id"])[0])
        if l["display_type"] != "product" or c is None:
            continue
        pid, _ = m2o(l["product_id"])
        tasa = tasas.get((l["tax_ids"] or [None])[0], 0.0)
        cantidad = abs(float(l["quantity"]))
        precio = float(l["price_unit"])
        if config.PRECIO_INCLUYE_IMPUESTO:
            precio /= 1 + tasa / 100
        clave = (m2o(l["move_id"])[0], pid)
        costo_linea = costo[clave] * cantidad / cantidad_producto[clave] if clave in costo else 0.0
        filas.append({
            **{k: v for k, v in c.items() if not k.startswith("_")},
            "producto": (productos.get(pid, {}).get("default_code") or "").strip(),
            "cantidad": c["_signo"] * cantidad,
            "precio_unitario": round(precio * c["cotizacion"], 2),
            "importe_neto": -float(l["balance"]),       # la factura queda positiva y la NC negativa solas
            "importe_total": round(c["_signo"] * float(l["price_total"]) * c["cotizacion"], 2),
            "costo": round(costo_linea, 2),
        })

    df = pd.DataFrame(filas, columns=COLUMNAS) if filas else vacio()
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df


def resumen_por_dia(df: pd.DataFrame, max_dias=10) -> str:
    """Tabla para el log: comprobantes, líneas e importe de los últimos días."""
    if df.empty:
        return "   (sin datos)"
    t = (df.assign(dia=df["fecha"].dt.date).groupby("dia")
         .agg(comprobantes=("comprobante", "nunique"), lineas=("comprobante", "size"),
              importe_neto=("importe_neto", "sum"))).tail(max_dias)
    return t.to_string(formatters={"importe_neto": "{:,.0f}".format})
