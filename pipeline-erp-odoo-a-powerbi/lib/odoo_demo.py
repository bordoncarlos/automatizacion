"""
Odoo SIMULADO en memoria, para probar el pipeline sin una instancia real.

PARA QUÉ SIRVE
--------------
Tiene la misma interfaz que lib/odoo_cliente.Odoo (`search_read`, `read`) y
devuelve los registros con la misma forma que la API real: los many2one como
[id, "nombre"], los vacíos como False, las fechas como texto, las horas en
UTC. El resto del código no sabe si habla con Odoo o con esto.

Los datos son de una ferretería mayorista inventada y traen a propósito los
casos que en la vida real rompen un pipeline:

- facturas canceladas y en borrador (no deben entrar);
- notas de crédito SIN término de pago (el asistente de Odoo no lo copia al
  revertir): tienen que heredar la condición de la factura original;
- productos sin asiento de costo (Odoo no lo genera si el costo del producto
  es 0 en esa compañía): el costo se completa desde la ficha;
- facturas en dólares;
- un usuario de Odoo sin código de vendedor y un diario sin sucursal;
- pagos de facturas de CONTADO registrados en caja (no son cobranza a crédito);
- dos contactos con el mismo RUC, uno de ellos en minúsculas;
- una zona de depósito nueva que nadie mapeó.
"""
import random

import pandas as pd

import config

USUARIOS = {2: "Ana Gómez", 6: "Bruno Díaz", 7: "Carla Benítez", 8: "Diego Ruiz",
            9: "Elena Vera", 10: "Fabián Rojas"}            # 10: sin código de vendedor
DIARIOS = {1: ("Facturas Casa Central", "sale", "CC"), 2: ("Facturas Sucursal Norte", "sale", "NO"),
           3: ("Facturas Sucursal Sur", "sale", "SU"), 4: ("Facturas Feria", "sale", "FE"),
           5: ("Banco", "bank", "BNK"), 6: ("Caja", "cash", "CJA")}
TERMINOS = {1: "Immediate Payment", 2: "15 Days", 3: "30 Days"}
UBICACIONES = {1: ("WH/Stock", "internal"), 2: ("WH/Stock/Pisos", "internal"),
               3: ("WH/Stock/Adhesivos", "internal"), 4: ("NOR/Stock", "internal"),
               5: ("SUR/Stock", "internal"), 6: ("WH/Averiados", "internal"),
               7: ("WH/Stock/Zona Nueva", "internal"),       # zona que nadie mapeó
               8: ("Partners/Customers", "customer"), 9: ("Virtual Locations/Inventory adjustment", "inventory")}
RUBROS = ["Ferretería", "Constructora", "Corralón", "Pinturería", "Comercial", "Sanitarios"]
LUGARES = ["San José", "Del Este", "Las Palmas", "Central", "La Unión", "Santa Ana", "El Puente",
           "Los Pinos", "Del Sur", "Mburucuyá", "La Colina", "Santa Rosa", "El Molino",
           "Las Lomas", "Del Lago", "Primavera", "San Miguel", "Los Robles", "La Esperanza",
           "El Sol", "Itá", "Tres Cerros", "La Victoria", "San Blas", "Monte Alto"]


def _valor(reg, campo):
    v = reg.get(campo, False)
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], str):
        return v[0]                      # many2one: se compara por id, como en Odoo
    return v


def _cumple(reg, dominio):
    """Evalúa un dominio de Odoo (lista de tuplas, todas con AND)."""
    for campo, op, valor in dominio:
        v = _valor(reg, campo)
        if campo.endswith("_ids"):       # x2many: "in" = contiene alguno
            if not set(v or []) & set(valor if isinstance(valor, (list, tuple)) else [valor]):
                return False
            continue
        if op == "=" and v != valor or op == "!=" and v == valor:
            return False
        if op == "in" and v not in valor or op == "not in" and v in valor:
            return False
        if op == "ilike" and not (v and str(valor).lower() in str(v).lower()):
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
        self.desde = pd.Timestamp(desde or config.ERP_DESDE).normalize()
        self.rng = random.Random(semilla)
        self.t = {m: {} for m in ("res.users", "account.journal", "account.payment.term", "res.currency",
                                  "account.tax", "account.account", "product.product", "res.partner",
                                  "account.move", "account.move.line", "account.payment",
                                  "stock.location", "stock.quant")}
        self._seq = {}
        self._catalogos()
        self._facturas()
        self._pagos()
        self._stock()

    # ------------------------------------------------------------------ API --
    def search_read(self, modelo, dominio, campos, lote=None):
        regs = sorted((r for r in self.t[modelo].values() if _cumple(r, dominio)), key=lambda r: r["id"])
        return [self._proyectar(r, campos) for r in regs]

    def read(self, modelo, ids, campos, lote=None):
        tabla = self.t[modelo]
        return [self._proyectar(tabla[i], campos) for i in sorted(set(ids)) if i in tabla]

    @staticmethod
    def _proyectar(reg, campos):
        return {"id": reg["id"], **{c: reg.get(c, False) for c in campos}}

    # ------------------------------------------------------------ helpers --
    def _nuevo(self, modelo, **campos):
        nuevo_id = self._seq.get(modelo, 0) + 1
        self._seq[modelo] = nuevo_id
        self.t[modelo][nuevo_id] = {"id": nuevo_id, **campos}
        return nuevo_id

    def _m2o(self, modelo, i):
        if not i:
            return False
        r = self.t[modelo][i]
        return [i, r.get("name") or r.get("complete_name")]

    # --------------------------------------------------------- catálogos --
    def _catalogos(self):
        rng = self.rng
        for i, nombre in USUARIOS.items():
            self.t["res.users"][i] = {"id": i, "name": nombre}
        for i, (nombre, tipo, codigo) in DIARIOS.items():
            self.t["account.journal"][i] = {"id": i, "name": nombre, "type": tipo, "code": codigo}
        for i, nombre in TERMINOS.items():
            self.t["account.payment.term"][i] = {"id": i, "name": nombre}
        self.t["res.currency"] = {1: {"id": 1, "name": "PYG"}, 2: {"id": 2, "name": "USD"}}
        self.t["account.tax"] = {1: {"id": 1, "name": "IVA 10%", "amount": 10.0},
                                 2: {"id": 2, "name": "IVA 5%", "amount": 5.0},
                                 3: {"id": 3, "name": "Exento", "amount": 0.0}}
        self.t["account.account"] = {
            1: {"id": 1, "name": "Ventas de mercaderías", "account_type": "income"},
            2: {"id": 2, "name": "Costo de ventas", "account_type": "expense_direct_cost"},
            3: {"id": 3, "name": "Mercaderías", "account_type": "asset_current"},
            4: {"id": 4, "name": "Deudores por ventas", "account_type": "asset_receivable"}}

        familias = ["CEMENTO", "PINTURA", "CAÑO PVC", "GRIFERIA", "CERAMICO", "HERRAMIENTA",
                    "ELECTRICIDAD", "ADHESIVO", "SANITARIO", "TORNILLERIA"]
        for n in range(40):
            precio = round(rng.uniform(10_000, 700_000), -2)
            self.t["product.product"][101 + n] = {
                "id": 101 + n, "default_code": f"A{1001 + n}", "type": "consu",
                "name": f"{familias[n % 10]} MODELO {n + 1:02d}", "list_price": precio,
                "standard_price": round(precio * rng.uniform(0.55, 0.75)),
                "_impuesto": 3 if n in (7, 27) else (2 if n in (13, 33) else 1),
                "_sin_costo_contable": n in (30, 31, 32),     # Odoo no genera el asiento de costo
            }
        self.t["product.product"][141] = {"id": 141, "default_code": "S9001", "type": "service",
                                          "name": "FLETE", "list_price": 80_000, "standard_price": 0,
                                          "_impuesto": 1, "_sin_costo_contable": True}

        for n in range(1, 151):
            nombre = f"{RUBROS[n % len(RUBROS)]} {LUGARES[n % len(LUGARES)]} {n:03d}"
            self.t["res.partner"][1000 + n] = {"id": 1000 + n, "name": nombre, "vat": f"CLI-{n:04d}",
                                               "is_company": True, "customer_rank": rng.randint(1, 60)}
        # el mismo RUC en dos contactos (una persona de la empresa cargada aparte)...
        self.t["res.partner"][1151] = {"id": 1151, "name": "Juan Pérez (contacto)", "vat": "CLI-0005",
                                       "is_company": False, "customer_rank": 1}
        # ...el mismo RUC en minúsculas y con espacios...
        self.t["res.partner"][1152] = {"id": 1152, "name": "Constructora Los Pinos 007 (duplicado)",
                                       "vat": " cli-0007", "is_company": True, "customer_rank": 0}
        # ...y un consumidor final sin RUC.
        self.t["res.partner"][1153] = {"id": 1153, "name": "Consumidor final", "vat": False,
                                       "is_company": False, "customer_rank": 3}

    # ---------------------------------------------------------- facturas --
    def _factura(self, fecha, cliente, usuario, diario, termino, moneda, cotizacion, estado,
                 items, original=None):
        """Crea una factura (o NC si `original`) con sus líneas, como lo haría Odoo."""
        es_nc = original is not None          # original=0: NC cargada a mano, sin factura de origen
        signo = -1 if es_nc else 1
        codigo = DIARIOS[diario][2]
        clave = (codigo, es_nc, fecha.year)
        self._seq[clave] = self._seq.get(clave, 0) + 1
        nombre = f"{'NC-' if es_nc else ''}{codigo}/{fecha.year}/{self._seq[clave]:05d}"
        hora_local = fecha + pd.Timedelta(hours=self.rng.randint(7, 18), minutes=self.rng.randint(0, 59))
        hora_utc = hora_local + pd.Timedelta(hours=3)
        mid = self._nuevo(
            "account.move", name=nombre, move_type="out_refund" if es_nc else "out_invoice",
            state=estado, invoice_date=fecha.strftime("%Y-%m-%d"),
            create_date=hora_utc.strftime("%Y-%m-%d %H:%M:%S"),
            commercial_partner_id=self._m2o("res.partner", cliente),
            invoice_user_id=self._m2o("res.users", usuario),
            journal_id=self._m2o("account.journal", diario),
            invoice_payment_term_id=self._m2o("account.payment.term", termino),
            currency_id=self._m2o("res.currency", moneda),
            reversed_entry_id=self._m2o("account.move", original) if original else False)

        sin_iva = con_iva = 0.0
        for pid, cant, descuento in items:
            p = self.t["product.product"][pid]
            tasa = self.t["account.tax"][p["_impuesto"]]["amount"]
            precio_con_iva = round(p["list_price"] * (1 + tasa / 100) / cotizacion, 2)
            subtotal = cant * precio_con_iva / (1 + tasa / 100) * (1 - descuento / 100)
            total = subtotal * (1 + tasa / 100)
            sin_iva += subtotal
            con_iva += total
            self._nuevo("account.move.line", move_id=[mid, nombre], display_type="product",
                        product_id=self._m2o("product.product", pid), quantity=float(cant),
                        price_unit=precio_con_iva, discount=float(descuento), tax_ids=[p["_impuesto"]],
                        account_id=[1, "Ventas de mercaderías"], price_total=round(total, 2),
                        balance=round(-signo * subtotal * cotizacion, 2), name=p["name"])
            if p["type"] == "consu" and not p["_sin_costo_contable"]:
                costo = p["standard_price"] * cant
                self._nuevo("account.move.line", move_id=[mid, nombre], display_type="cogs",
                            product_id=self._m2o("product.product", pid), quantity=float(cant),
                            account_id=[2, "Costo de ventas"], balance=float(signo * costo), tax_ids=[])
                self._nuevo("account.move.line", move_id=[mid, nombre], display_type="cogs",
                            product_id=self._m2o("product.product", pid), quantity=float(cant),
                            account_id=[3, "Mercaderías"], balance=float(-signo * costo), tax_ids=[])
        self._nuevo("account.move.line", move_id=[mid, nombre], display_type="payment_term",
                    product_id=False, quantity=1.0, account_id=[4, "Deudores por ventas"],
                    balance=round(signo * con_iva * cotizacion, 2), tax_ids=[])
        mov = self.t["account.move"][mid]
        mov.update(amount_untaxed=round(sin_iva, 2), amount_untaxed_signed=round(signo * sin_iva * cotizacion, 2),
                   amount_total=round(con_iva, 2), amount_total_signed=round(signo * con_iva * cotizacion, 2))
        return mid

    def _facturas(self):
        rng = self.rng
        productos = [p for p in self.t["product.product"] if p != 141]
        clientes = list(range(1001, 1151))
        fecha = self.desde
        while fecha <= self.hoy:
            if fecha.dayofweek != 6:                                     # domingo cerrado
                for _ in range(rng.randint(6, 14) if fecha.dayofweek < 5 else rng.randint(3, 6)):
                    diario = rng.choices([1, 2, 3, 4], weights=[50, 25, 22, 3])[0]
                    usuario = rng.choices(list(USUARIOS), weights=[15, 20, 20, 20, 20, 5])[0]
                    cliente = rng.choice(clientes + [1152, 1153])
                    termino = rng.choices([1, 2, 3], weights=[60, 15, 25])[0]
                    usd = rng.random() < 0.05
                    estado = rng.choices(["posted", "cancel", "draft"], weights=[94, 4, 2])[0]
                    items = [(pid, rng.randint(1, 30), rng.choice([0, 0, 0, 5, 10]))
                             for pid in rng.sample(productos, rng.randint(1, 4))]
                    if rng.random() < 0.1:
                        items.append((141, 1, 0))                         # flete
                    self._factura(fecha, cliente, usuario, diario, termino, 2 if usd else 1,
                                  round(rng.uniform(7_300, 7_800)) if usd else 1.0, estado, items)
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
            # termino=None: el asistente de Odoo NO copia el término de pago al revertir
            self._factura(fecha_nc, m["commercial_partner_id"][0], m["invoice_user_id"][0],
                          m["journal_id"][0], None, m["currency_id"][0], cotizacion, "posted",
                          [(linea["product_id"][0], max(1, int(linea["quantity"]) // 2), linea["discount"])],
                          original=m["id"])
        # una NC cargada a mano, sin factura de origen ni término: queda CONTADO
        self._factura(self.hoy - pd.Timedelta(days=2), 1010, 6, 1, None, 1, 1.0, "posted",
                      [(105, 2, 0)], original=0)

    # ------------------------------------------------------------- pagos --
    def _pagos(self):
        rng = self.rng
        facturas = [m for m in self.t["account.move"].values()
                    if m["move_type"] == "out_invoice" and m["state"] == "posted"]
        pendientes = {}
        for m in facturas:
            fecha = pd.Timestamp(m["invoice_date"])
            termino = m["invoice_payment_term_id"][1] if m["invoice_payment_term_id"] else None
            if termino == "Immediate Payment":
                if rng.random() < 0.5:     # la caja registra el pago del contado: NO es cobranza a crédito
                    self._pago(fecha, m["commercial_partner_id"], 6, [m], "paid")
                continue
            dias = rng.randint(3, 40)
            if fecha + pd.Timedelta(days=dias) <= self.hoy and rng.random() < 0.75:
                pendientes.setdefault(m["commercial_partner_id"][0], []).append((fecha + pd.Timedelta(days=dias), m))
        for _, lista in pendientes.items():
            lista.sort(key=lambda x: x[0])
            while lista:
                fecha, m = lista.pop(0)
                grupo = [m]
                if lista and rng.random() < 0.3:           # un recibo que cancela dos facturas
                    grupo.append(lista.pop(0)[1])
                estado = "draft" if rng.random() < 0.03 else "paid"
                self._pago(fecha, m["commercial_partner_id"], 5, grupo, estado)

    def _pago(self, fecha, cliente, diario, facturas, estado):
        n = self._seq.get("pago", 0) + 1
        self._seq["pago"] = n
        self._nuevo("account.payment", name=f"PCOB/{fecha.year}/{n:05d}", date=fecha.strftime("%Y-%m-%d"),
                    partner_id=cliente, payment_type="inbound", partner_type="customer", state=estado,
                    journal_id=self._m2o("account.journal", diario),
                    amount=round(sum(f["amount_total_signed"] for f in facturas), 2),
                    reconciled_invoice_ids=[f["id"] for f in facturas])

    # ------------------------------------------------------------- stock --
    def _stock(self):
        for i, (nombre, uso) in UBICACIONES.items():
            self.t["stock.location"][i] = {"id": i, "complete_name": nombre, "name": nombre, "usage": uso}
        for pid, p in self.t["product.product"].items():
            if p["type"] != "consu":
                continue
            for loc, maximo in ((1, 400), (2, 120), (3, 80), (4, 60), (5, 60), (6, 5), (7, 10), (8, 0)):
                if self.rng.random() < 0.7:
                    self._nuevo("stock.quant", product_id=self._m2o("product.product", pid),
                                location_id=self._m2o("stock.location", loc),
                                quantity=float(self.rng.randint(-2 if loc == 8 else 0, max(maximo, 1))))
