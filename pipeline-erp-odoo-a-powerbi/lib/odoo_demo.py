"""
Odoo SIMULADO en memoria, para probar el script sin una instancia real.

PARA QUÉ SIRVE
--------------
Tiene la misma interfaz que lib/odoo_cliente.Odoo (`search_read`, `read`) y
devuelve los registros con la misma forma que la API real: los many2one como
[id, "nombre"], los vacíos como False, las fechas como texto y las horas en
UTC. El resto del código no sabe si habla con Odoo o con esto.

Los datos (facturas de una empresa inventada) traen los casos que un
reporte tiene que resolver bien: facturas canceladas y en borrador, notas de
crédito, facturas en dólares, impuestos distintos y un servicio sin costo.
"""
import random

import pandas as pd

import config

USUARIOS = {2: "Ana Gómez", 6: "Bruno Díaz", 7: "Carla Benítez", 8: "Diego Ruiz"}
DIARIOS = {1: ("Facturas de clientes", "FV"), 2: ("Facturas sucursal", "FS")}
TERMINOS = {1: "Immediate Payment", 2: "30 Days"}


def _valor(reg, campo):
    v = reg.get(campo, False)
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], str):
        return v[0]                      # many2one: se compara por id, como en Odoo
    return v


def _cumple(reg, dominio):
    """Evalúa un dominio de Odoo (lista de tuplas, todas con AND)."""
    for campo, op, valor in dominio:
        v = _valor(reg, campo)
        if op == "=" and v != valor or op == "!=" and v == valor:
            return False
        if op == "in" and v not in valor or op == "not in" and v in valor:
            return False
        if op in ("<", "<=", ">", ">="):
            if v is False or v is None:
                return False
            if not {"<": v < valor, "<=": v <= valor, ">": v > valor, ">=": v >= valor}[op]:
                return False
    return True


class OdooDemo:
    def __init__(self, hoy=None, desde=None, semilla=7):
        self.hoy = pd.Timestamp(hoy or pd.Timestamp.now()).normalize()
        self.desde = pd.Timestamp(desde or config.DESDE).normalize()
        self.rng = random.Random(semilla)
        self.t = {m: {} for m in ("res.users", "account.journal", "account.payment.term", "res.currency",
                                  "account.tax", "account.account", "product.product", "res.partner",
                                  "account.move", "account.move.line")}
        self._seq = {}
        self._catalogos()
        self._facturas()

    # ------------------------------------------------------------------ API --
    def search_read(self, modelo, dominio, campos, lote=None):
        regs = sorted((r for r in self.t[modelo].values() if _cumple(r, dominio)), key=lambda r: r["id"])
        return [{"id": r["id"], **{c: r.get(c, False) for c in campos}} for r in regs]

    def read(self, modelo, ids, campos, lote=None):
        tabla = self.t[modelo]
        return [{"id": i, **{c: tabla[i].get(c, False) for c in campos}} for i in sorted(set(ids)) if i in tabla]

    # ------------------------------------------------------------ datos --
    def _nuevo(self, modelo, **campos):
        nuevo_id = self._seq.get(modelo, 0) + 1
        self._seq[modelo] = nuevo_id
        self.t[modelo][nuevo_id] = {"id": nuevo_id, **campos}
        return nuevo_id

    def _m2o(self, modelo, i):
        return [i, self.t[modelo][i]["name"]] if i else False

    def _catalogos(self):
        rng = self.rng
        for i, nombre in USUARIOS.items():
            self.t["res.users"][i] = {"id": i, "name": nombre}
        for i, (nombre, codigo) in DIARIOS.items():
            self.t["account.journal"][i] = {"id": i, "name": nombre, "code": codigo}
        for i, nombre in TERMINOS.items():
            self.t["account.payment.term"][i] = {"id": i, "name": nombre}
        self.t["res.currency"] = {1: {"id": 1, "name": "PYG"}, 2: {"id": 2, "name": "USD"}}
        self.t["account.tax"] = {1: {"id": 1, "name": "IVA 10%", "amount": 10.0},
                                 2: {"id": 2, "name": "IVA 5%", "amount": 5.0},
                                 3: {"id": 3, "name": "Exento", "amount": 0.0}}
        self.t["account.account"] = {
            1: {"id": 1, "name": "Ventas", "account_type": "income"},
            2: {"id": 2, "name": "Costo de ventas", "account_type": "expense_direct_cost"},
            3: {"id": 3, "name": "Mercaderías", "account_type": "asset_current"},
            4: {"id": 4, "name": "Deudores", "account_type": "asset_receivable"}}
        for n in range(30):
            precio = round(rng.uniform(10_000, 500_000), -2)
            self.t["product.product"][101 + n] = {
                "id": 101 + n, "default_code": f"P{1001 + n}", "name": f"Producto {n + 1:02d}",
                "type": "consu", "list_price": precio, "standard_price": round(precio * rng.uniform(0.55, 0.75)),
                "_impuesto": 3 if n == 7 else (2 if n == 13 else 1)}
        self.t["product.product"][131] = {"id": 131, "default_code": "S9001", "name": "Servicio de envío",
                                          "type": "service", "list_price": 50_000, "standard_price": 0,
                                          "_impuesto": 1}
        for n in range(1, 101):
            self.t["res.partner"][1000 + n] = {"id": 1000 + n, "name": f"Cliente {n:03d}",
                                               "vat": f" c{n:04d}" if n == 7 else f"C{n:04d}"}

    def _factura(self, fecha, cliente, usuario, diario, termino, moneda, cotizacion, estado, items, original=None):
        """Una factura (o nota de crédito si `original`) con sus líneas, como las arma Odoo."""
        es_nc = original is not None
        signo = -1 if es_nc else 1
        clave = (diario, es_nc)
        self._seq[clave] = self._seq.get(clave, 0) + 1
        nombre = f"{'R' if es_nc else ''}{DIARIOS[diario][1]}/{fecha.year}/{self._seq[clave]:05d}"
        hora_utc = fecha + pd.Timedelta(hours=self.rng.randint(10, 21), minutes=self.rng.randint(0, 59))
        mid = self._nuevo(
            "account.move", name=nombre, move_type="out_refund" if es_nc else "out_invoice", state=estado,
            invoice_date=fecha.strftime("%Y-%m-%d"), create_date=hora_utc.strftime("%Y-%m-%d %H:%M:%S"),
            commercial_partner_id=self._m2o("res.partner", cliente), invoice_user_id=self._m2o("res.users", usuario),
            journal_id=self._m2o("account.journal", diario),
            invoice_payment_term_id=self._m2o("account.payment.term", termino),
            currency_id=self._m2o("res.currency", moneda),
            reversed_entry_id=self._m2o("account.move", original) if original else False)

        sin_imp = con_imp = 0.0
        for pid, cant in items:
            p = self.t["product.product"][pid]
            tasa = self.t["account.tax"][p["_impuesto"]]["amount"]
            precio = round(p["list_price"] * (1 + tasa / 100) / cotizacion, 2)     # con impuesto
            subtotal = cant * precio / (1 + tasa / 100)
            sin_imp += subtotal
            con_imp += subtotal * (1 + tasa / 100)
            self._nuevo("account.move.line", move_id=[mid, nombre], display_type="product",
                        product_id=self._m2o("product.product", pid), quantity=float(cant), price_unit=precio,
                        tax_ids=[p["_impuesto"]], account_id=[1, "Ventas"],
                        price_total=round(subtotal * (1 + tasa / 100), 2),
                        balance=round(-signo * subtotal * cotizacion, 2))
            if p["type"] == "consu":     # contabilidad anglosajona: costo de ventas (debe) / mercaderías (haber)
                costo = p["standard_price"] * cant
                for cuenta, s in (([2, "Costo de ventas"], 1), ([3, "Mercaderías"], -1)):
                    self._nuevo("account.move.line", move_id=[mid, nombre], display_type="cogs",
                                product_id=self._m2o("product.product", pid), quantity=float(cant),
                                account_id=cuenta, balance=float(s * signo * costo), tax_ids=[])
        self._nuevo("account.move.line", move_id=[mid, nombre], display_type="payment_term", product_id=False,
                    quantity=1.0, account_id=[4, "Deudores"], balance=round(signo * con_imp * cotizacion, 2),
                    tax_ids=[])
        self.t["account.move"][mid].update(
            amount_untaxed=round(sin_imp, 2), amount_untaxed_signed=round(signo * sin_imp * cotizacion, 2))
        return mid

    def _facturas(self):
        rng = self.rng
        productos = list(range(101, 131))
        fecha = self.desde
        while fecha <= self.hoy:
            if fecha.dayofweek != 6:
                for _ in range(rng.randint(5, 12) if fecha.dayofweek < 5 else rng.randint(2, 5)):
                    usd = rng.random() < 0.05
                    items = [(pid, rng.randint(1, 20)) for pid in rng.sample(productos, rng.randint(1, 4))]
                    if rng.random() < 0.1:
                        items.append((131, 1))                                       # servicio
                    # la primera es del cliente con el RUC cargado en minúsculas y con espacios
                    cliente = 1007 if not self.t["account.move"] else rng.randint(1001, 1100)
                    self._factura(fecha, cliente, rng.choice(list(USUARIOS)),
                                  rng.choice([1, 1, 2]), rng.choice([1, 1, 2]), 2 if usd else 1,
                                  round(rng.uniform(7_000, 8_000)) if usd else 1.0,
                                  rng.choices(["posted", "cancel", "draft"], weights=[94, 4, 2])[0], items)
            fecha += pd.Timedelta(days=1)

        # Notas de crédito: ~5 % de las facturas publicadas tienen una devolución.
        publicadas = [m for m in list(self.t["account.move"].values())
                      if m["move_type"] == "out_invoice" and m["state"] == "posted"]
        for m in rng.sample(publicadas, max(1, len(publicadas) // 20)):
            fecha_nc = pd.Timestamp(m["invoice_date"]) + pd.Timedelta(days=rng.randint(1, 7))
            if fecha_nc > self.hoy:
                continue
            linea = next(l for l in self.t["account.move.line"].values()
                         if l["move_id"][0] == m["id"] and l["display_type"] == "product")
            cotizacion = abs(m["amount_untaxed_signed"] / m["amount_untaxed"]) if m["amount_untaxed"] else 1.0
            self._factura(fecha_nc, m["commercial_partner_id"][0], m["invoice_user_id"][0], m["journal_id"][0],
                          None, m["currency_id"][0], cotizacion, "posted",
                          [(linea["product_id"][0], max(1, int(linea["quantity"]) // 2))], original=m["id"])
