"""
Tests contra el Odoo simulado (lib/odoo_demo.py).

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actualizar_reporte  # noqa: E402
import config  # noqa: E402
from lib import escritura, transformacion  # noqa: E402
from lib.odoo_demo import OdooDemo  # noqa: E402

HOY = pd.Timestamp("2026-10-01")
DESDE = pd.Timestamp("2026-08-17")
silencio = lambda *a, **k: None   # noqa: E731


class TestTransformacion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.odoo = OdooDemo(hoy=HOY, desde=DESDE)
        cls.df = transformacion.extraer(cls.odoo, DESDE, HOY + pd.Timedelta(days=1), log=silencio)
        cls.movs = cls.odoo.t["account.move"]

    def test_solo_entran_las_publicadas(self):
        no_publicadas = {m["name"] for m in self.movs.values() if m["state"] != "posted"}
        self.assertTrue(no_publicadas)                                        # la demo las tiene
        self.assertFalse(no_publicadas & set(self.df["comprobante"]))

    def test_el_importe_cierra_contra_la_contabilidad(self):
        lineas = [l for l in self.odoo.t["account.move.line"].values()
                  if l["display_type"] == "product" and self.movs[l["move_id"][0]]["state"] == "posted"]
        self.assertAlmostEqual(self.df["importe_neto"].sum(), -sum(l["balance"] for l in lineas), places=2)

    def test_nota_de_credito_negativa_factura_positiva(self):
        por_tipo = self.df.groupby("tipo")[["importe_neto", "cantidad"]].sum()
        self.assertLess(por_tipo.loc["Nota de crédito", "importe_neto"], 0)
        self.assertLess(por_tipo.loc["Nota de crédito", "cantidad"], 0)
        self.assertGreater(por_tipo.loc["Factura", "importe_neto"], 0)

    def test_dolares_convertidos_con_la_cotizacion_de_la_factura(self):
        usd = self.df[self.df["moneda"] == "USD"]
        self.assertFalse(usd.empty)
        self.assertTrue(usd["cotizacion"].between(7_000, 8_000).all())
        self.assertTrue((self.df.loc[self.df["moneda"] != "USD", "cotizacion"] == 1).all())

    def test_costo_desde_el_asiento_y_servicio_sin_costo(self):
        productos = self.df[self.df["producto"] != "S9001"]
        self.assertTrue((productos["costo"] != 0).all())
        self.assertTrue((self.df.loc[self.df["producto"] == "S9001", "costo"] == 0).all())

    def test_precio_sin_impuesto(self):
        p = self.odoo.t["product.product"][101]
        fila = self.df[(self.df["producto"] == "P1001") & (self.df["moneda"] == "PYG")].iloc[0]
        self.assertAlmostEqual(fila["precio_unitario"], p["list_price"], delta=1)

    def test_clave_de_cliente_normalizada(self):
        self.assertIn("C0007", set(self.df["cliente"]))
        self.assertEqual(transformacion.normalizar_texto(" c0007"), "C0007")
        self.assertEqual(transformacion.normalizar_texto(False), "")


class TestCacheYVentana(unittest.TestCase):
    def test_una_cancelacion_tardia_desaparece_del_reporte(self):
        odoo = OdooDemo(hoy=HOY, desde=DESDE)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(config, "CACHE", Path(d) / "cache.parquet"), \
                mock.patch.object(config, "DESDE", DESDE), \
                mock.patch("builtins.print"):
            primera = actualizar_reporte.dias_cerrados(odoo, HOY)
            # alguien cancela en Odoo una factura de hace 3 días
            cancelada = next(m for m in odoo.t["account.move"].values() if m["state"] == "posted"
                             and m["invoice_date"] == (HOY - pd.Timedelta(days=3)).strftime("%Y-%m-%d"))
            cancelada["state"] = "cancel"
            segunda = actualizar_reporte.dias_cerrados(odoo, HOY)
        self.assertIn(cancelada["name"], set(primera["comprobante"]))
        self.assertNotIn(cancelada["name"], set(segunda["comprobante"]))
        self.assertEqual(len(primera["comprobante"].unique()) - 1, len(segunda["comprobante"].unique()))


class TestEscritura(unittest.TestCase):
    def test_excel_tomado_no_es_un_error(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "reporte.xlsx")
            with mock.patch("os.replace", side_effect=PermissionError(5, "Acceso denegado")), \
                    mock.patch("time.sleep"):
                with self.assertRaises(escritura.ArchivoTomado):
                    escritura.escribir_excel(pd.DataFrame({"a": [1]}), ruta)
            self.assertEqual(os.listdir(d), [])                 # no deja el temporal


if __name__ == "__main__":
    unittest.main()
