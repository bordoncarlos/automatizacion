"""
Cobranza (contado + crédito) -> cobranza.parquet. Corre CADA MINUTO, 30 s
después de la de ventas.

PARA QUÉ SIRVE
--------------
Arma la tabla de cobranza por vendedor y sucursal que lee Power BI, con el
mismo esquema que el sistema anterior.

LA DECISIÓN CLAVE: CONTADO Y CRÉDITO SALEN DE LUGARES DISTINTOS
---------------------------------------------------------------
- CONTADO: la venta y el cobro son el MISMO hecho. Sale de las ventas (la
  caché que deja actualizar_ventas.py, sin volver a llamar a la API), con el
  mismo vendedor y la misma sucursal que en el informe de ventas. Si se
  calcularan por separado, un vendedor podría salir en una sucursal en ventas
  y en otra en cobranza.
- CRÉDITO: el cobro es un hecho POSTERIOR, con su propia fecha (la del
  recibo). Sale de los pagos registrados en Odoo (`account.payment`).

Por qué no sumar simplemente todos los pagos: la caja TAMBIÉN registra un pago
por cada venta de contado. Si se sumaran los pagos más el contado, el contado
se contaría dos veces. Por eso de los pagos sólo se toma la parte que cancela
facturas a CRÉDITO.

Las notas de crédito: las de una venta de contado restan en el contado (se
devolvió plata de la caja); las de una venta a crédito no son cobranza (bajan
la deuda, no entra ni sale plata).

IDEMPOTENTE: reemplaza las filas desde ERP_DESDE y nunca toca las anteriores
(las del sistema anterior). Correrla dos veces da lo mismo.

CÓDIGOS DE SALIDA: 0 OK · 3 el parquet está tomado (se escribe en el próximo minuto).
"""
import sys
import time

import pandas as pd

import config
from lib import consola, escritura, traduccion
from lib.odoo_cliente import conectar, m2o

COLUMNAS = ["fecha", "tipo", "nro_recibo", "nro_comprobante", "cliente", "cod_vendedor",
            "cod_sucursal", "monto_cobrado", "monto_neto"]


def cobranza_contado(ventas: pd.DataFrame) -> pd.DataFrame:
    """Contado = las ventas de contado, por comprobante (facturas positivas, NC negativas)."""
    contado = ventas[ventas["condicion_venta"] == config.CONTADO]
    if contado.empty:
        return pd.DataFrame(columns=COLUMNAS)
    g = (contado.assign(fecha=contado["fecha_emision"].dt.normalize())
         .groupby(["fecha", "nro_comprobante", "cliente", "cod_vendedor", "cod_sucursal"], as_index=False)
         .agg(monto_cobrado=("monto_total", "sum"), monto_neto=("monto_neto", "sum")))
    g["tipo"], g["nro_recibo"] = "CONTADO", g["nro_comprobante"]
    return g[COLUMNAS]


def repartir(monto: float, pesos: list) -> list:
    """Reparte `monto` según `pesos` con suma exacta (el residuo va al mayor)."""
    total = sum(pesos)
    if not total:
        return [0.0] * len(pesos)
    partes = [round(monto * p / total, 2) for p in pesos]
    partes[pesos.index(max(pesos))] += round(monto - sum(partes), 2)
    return partes


def cobranza_credito(odoo, desde) -> pd.DataFrame:
    """Crédito = la parte de cada pago que cancela facturas a crédito."""
    pagos = odoo.search_read("account.payment", [
        ("payment_type", "=", "inbound"), ("partner_type", "=", "customer"),
        ("state", "in", config.ESTADOS_PAGO_VALIDOS), ("date", ">=", desde.strftime("%Y-%m-%d"))],
        ["name", "date", "amount", "reconciled_invoice_ids"])
    ids_facturas = {i for p in pagos for i in (p["reconciled_invoice_ids"] or [])}
    facturas = {f["id"]: f for f in odoo.read("account.move", ids_facturas, [
        "name", "commercial_partner_id", "invoice_user_id", "journal_id", "invoice_payment_term_id",
        "amount_total_signed", "amount_untaxed_signed"])}
    clientes = {p["id"]: traduccion.normalizar_ruc(p["vat"]) for p in odoo.read(
        "res.partner", {m2o(f["commercial_partner_id"])[0] for f in facturas.values()}, ["vat"])}

    filas = []
    for p in pagos:
        aplicadas = [facturas[i] for i in (p["reconciled_invoice_ids"] or []) if i in facturas]
        if not aplicadas:
            continue          # anticipo o pago sin conciliar: todavía no cancela ninguna factura
        # Odoo guarda cuánto del pago fue a cada factura en account.partial.reconcile;
        # acá se reparte en proporción al total de cada una, con suma exacta.
        partes = repartir(float(p["amount"]), [abs(f["amount_total_signed"]) for f in aplicadas])
        for f, parte in zip(aplicadas, partes):
            condicion = traduccion.condicion_de_termino(m2o(f["invoice_payment_term_id"])[1], {})
            if condicion == config.CONTADO:
                continue      # ya está en el contado: sumarlo sería contarlo dos veces
            uid = m2o(f["invoice_user_id"])[0]
            proporcion_neta = f["amount_untaxed_signed"] / f["amount_total_signed"] if f["amount_total_signed"] else 1
            filas.append({
                "fecha": pd.Timestamp(p["date"]), "tipo": "CREDITO", "nro_recibo": p["name"],
                "nro_comprobante": f["name"], "cliente": clientes.get(m2o(f["commercial_partner_id"])[0], ""),
                "cod_vendedor": config.VENDEDOR_POR_USUARIO.get(uid, str(config.VENDEDOR_SIN_MAPEO_BASE + (uid or 0))),
                "cod_sucursal": config.SUCURSAL_POR_DIARIO.get(m2o(f["journal_id"])[1], config.SUCURSAL_DESCONOCIDA),
                "monto_cobrado": parte, "monto_neto": round(parte * proporcion_neta, 2),
            })
    return pd.DataFrame(filas, columns=COLUMNAS)


def main():
    consola.preparar("cobranza")
    inicio = time.time()
    if escritura.archivo_tomado(config.COBRANZA):
        print(f"[SKIP] {config.COBRANZA.name} tomado (¿refresh de Power BI?); se escribe en la próxima corrida")
        return escritura.CODIGO_SALTEADO

    print(f"COBRANZA ODOO desde {config.ERP_DESDE:%d/%m/%Y}")
    ventas = traduccion.unir(*(pd.read_parquet(r) for r in (config.CACHE_VENTAS, config.VENTAS_HOY)
                               if r.exists()))
    contado = cobranza_contado(ventas)
    credito = cobranza_credito(conectar(), config.ERP_DESDE)
    nueva = pd.concat([d for d in (contado, credito) if not d.empty], ignore_index=True)

    # Idempotente: lo del sistema anterior (< ERP_DESDE) queda como estaba.
    previa = pd.read_parquet(config.COBRANZA) if config.COBRANZA.exists() else pd.DataFrame(columns=COLUMNAS)
    previa = previa[previa["fecha"] < config.ERP_DESDE]
    cobranza = pd.concat([d for d in (previa, nueva) if not d.empty], ignore_index=True)

    resumen = (nueva.assign(dia=nueva["fecha"].dt.date)
               .pivot_table(index="dia", columns="tipo", values="monto_cobrado", aggfunc="sum", fill_value=0))
    print(resumen.tail(10).to_string(float_format="{:,.0f}".format))
    try:
        escritura.escribir_parquet(cobranza, config.COBRANZA)
    except escritura.ArchivoTomado:
        print(f"[SKIP] {config.COBRANZA.name} se tomó durante la corrida; se escribe en la próxima")
        return escritura.CODIGO_SALTEADO
    print(f"[OK] {len(cobranza):,} filas ({len(contado):,} contado, {len(credito):,} crédito) "
          f"en {time.time() - inicio:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
