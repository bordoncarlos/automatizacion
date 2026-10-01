"""
Traducción: facturas y notas de crédito de Odoo -> la tabla de ventas de los informes.

PARA QUÉ SIRVE
--------------
Es el corazón del proyecto. Cuando la empresa cambió de ERP, los informes de
Power BI ya existían y leían una tabla de ventas con un esquema fijo (una
fila por línea de comprobante, montos en guaraníes, códigos de vendedor y de
sucursal). Esta capa convierte lo que devuelve Odoo EXACTAMENTE a ese
esquema, así los informes siguieron funcionando sin tocar una sola medida.

Odoo y los informes miran lo mismo de forma distinta:

| En los informes          | En Odoo                                               |
|--------------------------|-------------------------------------------------------|
| monto neto en Gs         | `balance` de la línea, con signo contable (-balance)  |
| precio sin IVA           | `price_unit`, que viene CON IVA                        |
| costo de la venta        | líneas `cogs` del asiento (contabilidad anglosajona)   |
| código de vendedor       | usuario de Odoo (tabla de traducción a mano)           |
| sucursal                 | diario de ventas con el que se facturó                 |
| contado / crédito        | término de pago                                        |
| cotización del dólar     | amount_untaxed_signed / amount_untaxed de la cabecera  |
"""
from datetime import datetime

import pandas as pd

import config
from lib.odoo_cliente import m2o

COLUMNAS = ["fecha_emision", "nro_comprobante", "tipo_comprobante", "cliente", "cod_vendedor",
            "cod_sucursal", "cod_articulo", "cantidad", "precio_unitario", "monto_neto",
            "monto_total", "monto_costo", "condicion_venta", "moneda", "cotizacion"]

CAMPOS_CABECERA = ["name", "move_type", "invoice_date", "create_date", "commercial_partner_id",
                   "invoice_user_id", "journal_id", "invoice_payment_term_id", "currency_id",
                   "amount_untaxed", "amount_untaxed_signed", "reversed_entry_id"]
CAMPOS_LINEA = ["move_id", "display_type", "product_id", "quantity", "price_unit", "discount",
                "tax_ids", "balance", "price_total", "account_id"]


def normalizar_ruc(vat) -> str:
    """RUC sin espacios y en mayúsculas.

    Power BI compara las claves de una relación SIN distinguir mayúsculas:
    'cli-0007' y 'CLI-0007' son la misma clave para el modelo y dos filas
    distintas para pandas. Si llegan las dos a la tabla de clientes, la
    relación deja de ser uno-a-varios y el modelo no carga.
    """
    return str(vat).strip().upper() if vat else ""


def condicion_de_termino(nombre_termino, avisos) -> str:
    """Término de pago de Odoo -> condición de venta. Sin término = contado."""
    if not nombre_termino:
        return config.CONTADO
    if nombre_termino not in config.CONDICION_POR_TERMINO:
        avisos.setdefault("terminos", set()).add(nombre_termino)
        return f"CREDITO ({nombre_termino})"
    return config.CONDICION_POR_TERMINO[nombre_termino]


def _hora_local(create_date):
    """Odoo guarda las horas en UTC: 21:30 UTC es 18:30 en Asunción."""
    return (pd.Timestamp(create_date).tz_localize("UTC").tz_convert(config.ZONA_HORARIA)
            .time() if create_date else datetime.min.time())


def _vacio():
    return pd.DataFrame(columns=COLUMNAS)


def unir(*partes) -> pd.DataFrame:
    """Concatena ignorando las partes vacías (mezclarlas cambia los tipos de las columnas)."""
    llenas = [p for p in partes if not p.empty]
    return pd.concat(llenas, ignore_index=True) if llenas else _vacio()


def extraer_ventas(odoo, desde, hasta, log=print) -> pd.DataFrame:
    """Facturas y NC PUBLICADAS con fecha en [desde, hasta), una fila por línea de producto.

    Las canceladas no se piden (state = 'posted'): en Odoo una factura
    cancelada desaparece, no deja una fila espejo que la anule. Por eso quien
    llama re-extrae una ventana de días: una cancelación tardía tiene que
    hacer desaparecer la factura también de los informes.
    """
    desde, hasta = pd.Timestamp(desde).strftime("%Y-%m-%d"), pd.Timestamp(hasta).strftime("%Y-%m-%d")

    # 1. Cabeceras -----------------------------------------------------------
    cabeceras = odoo.search_read("account.move", [
        ("move_type", "in", ["out_invoice", "out_refund"]), ("state", "=", "posted"),
        ("invoice_date", ">=", desde), ("invoice_date", "<", hasta)], CAMPOS_CABECERA)
    log(f"   Odoo: {len(cabeceras):,} comprobantes publicados en [{desde}, {hasta})")
    if not cabeceras:
        return _vacio()

    # 2. Líneas de producto y de costo, por comprobante (en lotes) --------------
    ids = [m["id"] for m in cabeceras]
    lineas = []
    for i in range(0, len(ids), config.LOTE):
        lineas += odoo.search_read("account.move.line", [
            ("move_id", "in", ids[i:i + config.LOTE]),
            ("display_type", "in", ["product", "cogs"])], CAMPOS_LINEA)

    # 3. Catálogos: sólo los registros que aparecen en estas líneas --------------
    productos = {p["id"]: p for p in odoo.read(
        "product.product", {m2o(l["product_id"])[0] for l in lineas if l["product_id"]},
        ["default_code", "type", "standard_price"])}
    clientes = {p["id"]: normalizar_ruc(p["vat"]) for p in odoo.read(
        "res.partner", {m2o(m["commercial_partner_id"])[0] for m in cabeceras}, ["vat"])}
    tasas = {t["id"]: float(t["amount"]) for t in odoo.read(
        "account.tax", {t for l in lineas for t in (l["tax_ids"] or [])}, ["amount"])}
    tipo_cuenta = {c["id"]: c["account_type"] for c in odoo.read(
        "account.account", {m2o(l["account_id"])[0] for l in lineas if l["account_id"]}, ["account_type"])}

    # 4. La NC hereda vendedor, sucursal y CONDICIÓN de la factura que revierte.
    #    El asistente de Odoo no copia el término de pago al revertir: sin esto,
    #    la NC de una venta a crédito salía CONTADO y restaba en la caja plata
    #    que nunca había entrado por caja.
    ids_originales = {m2o(m["reversed_entry_id"])[0] for m in cabeceras if m["reversed_entry_id"]}
    originales = {o["id"]: o for o in odoo.read(
        "account.move", ids_originales, ["invoice_user_id", "journal_id", "invoice_payment_term_id"])}

    # 5. Costo por (comprobante, producto) desde las líneas cogs -----------------
    #    Cada línea vendida genera dos líneas cogs: costo de ventas (debe) y
    #    mercaderías (haber). Se toma la de la cuenta de costo y se reparte por
    #    cantidad si el producto aparece en varias líneas del comprobante.
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

    # 6. Cabeceras traducidas -------------------------------------------------------
    avisos = {}
    cab = {}
    for m in cabeceras:
        es_nc = m["move_type"] == "out_refund"
        fuente = originales.get(m2o(m["reversed_entry_id"])[0], m) if es_nc else m

        uid, nombre_usuario = m2o(fuente["invoice_user_id"])
        vendedor = config.VENDEDOR_POR_USUARIO.get(uid)
        if vendedor is None:
            vendedor = str(config.VENDEDOR_SIN_MAPEO_BASE + (uid or 0))
            avisos.setdefault("vendedores", {})[uid] = nombre_usuario

        _, diario = m2o(fuente["journal_id"])
        sucursal = config.SUCURSAL_POR_DIARIO.get(diario)
        if sucursal is None:
            sucursal = config.SUCURSAL_DESCONOCIDA
            avisos.setdefault("diarios", set()).add(diario)

        _, moneda = m2o(m["currency_id"])
        cotizacion = 1.0
        if moneda != "PYG" and m["amount_untaxed"]:
            cotizacion = abs(float(m["amount_untaxed_signed"]) / float(m["amount_untaxed"]))

        cab[m["id"]] = {
            "fecha_emision": datetime.combine(pd.Timestamp(m["invoice_date"]).date(),
                                              _hora_local(m["create_date"])),
            "nro_comprobante": m["name"],
            "tipo_comprobante": "NOTA CREDITO" if es_nc else "FACTURA",
            "cliente": clientes.get(m2o(m["commercial_partner_id"])[0], ""),
            "cod_vendedor": vendedor,
            "cod_sucursal": sucursal,
            "condicion_venta": condicion_de_termino(m2o(fuente["invoice_payment_term_id"])[1], avisos),
            "moneda": "GS" if moneda == "PYG" else moneda,
            "cotizacion": cotizacion,
            "_signo": -1 if es_nc else 1,
        }

    # 7. Una fila por línea de producto ------------------------------------------------
    filas, sin_costo_contable = [], 0
    for l in lineas:
        c = cab.get(m2o(l["move_id"])[0])
        if l["display_type"] != "product" or c is None:
            continue
        pid, _ = m2o(l["product_id"])
        producto = productos.get(pid, {})
        tasa = tasas.get((l["tax_ids"] or [None])[0], 0.0)
        cantidad = abs(float(l["quantity"]))
        precio = float(l["price_unit"]) / (1 + tasa / 100) if config.PRECIO_INCLUYE_IVA else float(l["price_unit"])

        clave = (m2o(l["move_id"])[0], pid)
        if clave in costo and cantidad_producto.get(clave):
            costo_linea = costo[clave] * cantidad / cantidad_producto[clave]
        elif producto.get("type") == "service":
            costo_linea = 0.0                    # un flete no tiene costo de mercadería
        else:
            # Odoo no genera las líneas cogs si el producto tiene costo 0 en
            # la compañía que factura: se completa con el costo de la ficha.
            costo_linea = c["_signo"] * float(producto.get("standard_price") or 0) * cantidad
            sin_costo_contable += 1

        filas.append({
            **{k: v for k, v in c.items() if not k.startswith("_")},
            "cod_articulo": (producto.get("default_code") or "").strip() or "-1",
            "cantidad": c["_signo"] * cantidad,
            "precio_unitario": round(precio * c["cotizacion"], 2),
            "monto_neto": -float(l["balance"]),         # la factura queda positiva y la NC negativa solas
            "monto_total": round(c["_signo"] * float(l["price_total"]) * c["cotizacion"], 2),
            "monto_costo": round(costo_linea, 2),
        })

    # 8. Nada se pierde en silencio -----------------------------------------------------
    if sin_costo_contable:
        log(f"   Líneas sin asiento de costo en Odoo: {sin_costo_contable} (costo tomado de la ficha)")
    for uid, nombre in avisos.get("vendedores", {}).items():
        log(f"   [AVISO] usuario {uid} '{nombre}' sin código de vendedor: sale como "
            f"{config.VENDEDOR_SIN_MAPEO_BASE + (uid or 0)} (agregarlo a VENDEDOR_POR_USUARIO)")
    if avisos.get("diarios"):
        log(f"   [AVISO] diarios sin sucursal (salen como {config.SUCURSAL_DESCONOCIDA}): "
            f"{sorted(avisos['diarios'])}")
    if avisos.get("terminos"):
        log(f"   [AVISO] términos de pago sin traducir: {sorted(avisos['terminos'])}")

    df = pd.DataFrame(filas, columns=COLUMNAS) if filas else _vacio()
    df["fecha_emision"] = pd.to_datetime(df["fecha_emision"])
    return df


def resumen_por_dia(df: pd.DataFrame, max_dias=10) -> str:
    """Tabla para el log: comprobantes, líneas, neto y costo de los últimos días."""
    if df.empty:
        return "   (sin ventas)"
    t = (df.assign(dia=df["fecha_emision"].dt.date).groupby("dia")
         .agg(comprobantes=("nro_comprobante", "nunique"), lineas=("nro_comprobante", "size"),
              neto_gs=("monto_neto", "sum"), costo_gs=("monto_costo", "sum"))).tail(max_dias)
    return t.to_string(formatters={"neto_gs": "{:,.0f}".format, "costo_gs": "{:,.0f}".format})
