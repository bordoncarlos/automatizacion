"""
Tests contra el Odoo simulado (lib/odoo_demo.py). Cada uno fija un caso que
en la vida real rompió (o habría roto) un informe.

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actualizar_clientes  # noqa: E402
import actualizar_cobranza  # noqa: E402
import config  # noqa: E402
from lib import escritura, traduccion  # noqa: E402
from lib.odoo_cliente import m2o  # noqa: E402
from lib.odoo_demo import OdooDemo  # noqa: E402

HOY = pd.Timestamp("2026-10-01")
DESDE = pd.Timestamp("2026-08-17")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.odoo = OdooDemo(hoy=HOY, desde=DESDE)
        cls.ventas = traduccion.extraer_ventas(cls.odoo, DESDE, HOY + pd.Timedelta(days=1), log=lambda *a: None)
        cls.movs = cls.odoo.t["account.move"]


class TestTraduccion(Base):
    def test_solo_entran_las_publicadas(self):
        no_publicadas = {m["name"] for m in self.movs.values() if m["state"] != "posted"}
        self.assertTrue(no_publicadas)                                        # la demo las tiene
        self.assertFalse(no_publicadas & set(self.ventas["nro_comprobante"]))

    def test_el_neto_es_el_balance_contable(self):
        lineas = [l for l in self.odoo.t["account.move.line"].values()
                  if l["display_type"] == "product" and self.movs[l["move_id"][0]]["state"] == "posted"]
        self.assertAlmostEqual(self.ventas["monto_neto"].sum(), -sum(l["balance"] for l in lineas), places=2)

    def test_la_nc_hereda_la_condicion_de_la_factura(self):
        ncs = [m for m in self.movs.values() if m["move_type"] == "out_refund" and m["reversed_entry_id"]
               and m["state"] == "posted"]
        self.assertTrue(ncs)
        for nc in ncs:
            self.assertFalse(nc["invoice_payment_term_id"])                  # Odoo no lo copia
            original = self.movs[nc["reversed_entry_id"][0]]
            esperada = config.CONDICION_POR_TERMINO[m2o(original["invoice_payment_term_id"])[1]]
            obtenida = self.ventas.loc[self.ventas["nro_comprobante"] == nc["name"], "condicion_venta"]
            self.assertTrue((obtenida == esperada).all(), nc["name"])

    def test_nc_suma_negativo_y_factura_positivo(self):
        por_tipo = self.ventas.groupby("tipo_comprobante")[["monto_neto", "cantidad"]].sum()
        self.assertLess(por_tipo.loc["NOTA CREDITO", "monto_neto"], 0)
        self.assertLess(por_tipo.loc["NOTA CREDITO", "cantidad"], 0)
        self.assertGreater(por_tipo.loc["FACTURA", "monto_neto"], 0)

    def test_costo_de_la_ficha_si_odoo_no_genero_asiento(self):
        sin_asiento = self.ventas[self.ventas["cod_articulo"].isin(["A1031", "A1032", "A1033"])]
        self.assertTrue((sin_asiento["monto_costo"] != 0).all())
        flete = self.ventas[self.ventas["cod_articulo"] == "S9001"]
        self.assertTrue((flete["monto_costo"] == 0).all())                    # un servicio no tiene costo

    def test_factura_en_dolares_queda_en_guaranies(self):
        usd = self.ventas[self.ventas["moneda"] == "USD"]
        self.assertFalse(usd.empty)
        self.assertTrue(usd["cotizacion"].between(7_300, 7_800).all())

    def test_nada_se_pierde_en_silencio(self):
        self.assertIn("9010", set(self.ventas["cod_vendedor"]))              # usuario sin código
        self.assertIn(config.SUCURSAL_DESCONOCIDA, set(self.ventas["cod_sucursal"]))

    def test_ruc_normalizado(self):
        self.assertNotIn("cli-0007", " ".join(self.ventas["cliente"]))
        self.assertEqual(traduccion.normalizar_ruc(" cli-0007"), "CLI-0007")
        self.assertEqual(traduccion.normalizar_ruc(False), "")


class TestCobranza(Base):
    def test_el_credito_no_incluye_pagos_de_contado(self):
        credito = actualizar_cobranza.cobranza_credito(self.odoo, DESDE)
        contado = set(self.ventas.loc[self.ventas["condicion_venta"] == config.CONTADO, "nro_comprobante"])
        self.assertFalse(contado & set(credito["nro_comprobante"]))

    def test_borradores_no_cuentan(self):
        credito = actualizar_cobranza.cobranza_credito(self.odoo, DESDE)
        borradores = {p["name"] for p in self.odoo.t["account.payment"].values() if p["state"] == "draft"}
        self.assertFalse(borradores & set(credito["nro_recibo"]))

    def test_reparto_exacto(self):
        self.assertEqual(sum(actualizar_cobranza.repartir(100.0, [1, 1, 1])), 100.0)

    def test_contado_con_nc_resta(self):
        contado = actualizar_cobranza.cobranza_contado(self.ventas)
        ncs = contado[contado["nro_comprobante"].str.startswith("NC-")]
        self.assertFalse(ncs.empty)
        self.assertTrue((ncs["monto_cobrado"] < 0).all())


class TestClientes(unittest.TestCase):
    def test_un_contacto_por_ruc(self):
        contactos = [{"id": 1151, "name": "Juan Pérez (contacto)", "vat": "CLI-0005", "is_company": False, "customer_rank": 9},
                     {"id": 1005, "name": "Empresa", "vat": "CLI-0005", "is_company": True, "customer_rank": 1},
                     {"id": 1200, "name": "Otra", "vat": " cli-0005", "is_company": True, "customer_rank": 1}]
        elegido = actualizar_clientes.elegir_uno_por_ruc(contactos)
        self.assertEqual(list(elegido), ["CLI-0005"])
        self.assertEqual(elegido["CLI-0005"]["id"], 1005)      # compañía, y la más vieja en el empate


class TestEscritura(unittest.TestCase):
    def test_excel_tomado_no_es_un_error(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "ventas.xlsx")
            with mock.patch("os.replace", side_effect=PermissionError(5, "Acceso denegado")), \
                    mock.patch("time.sleep"):
                with self.assertRaises(escritura.ArchivoTomado):
                    escritura.escribir_excel(pd.DataFrame({"a": [1]}), ruta)
            self.assertEqual(os.listdir(d), [])                 # no deja el temporal


if __name__ == "__main__":
    unittest.main()
