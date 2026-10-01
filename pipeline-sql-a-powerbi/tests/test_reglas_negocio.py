"""
Tests de las reglas de negocio y de la extracción.

Cada test fija un caso que pasó en datos reales (ver el docstring de la
función que prueba). Correr con:

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import extraccion, reglas_negocio  # noqa: E402


def lineas(*filas):
    cols = ["nro_comprobante", "tipo_comprobante", "monto_neto", "monto_total", "monto_costo"]
    return pd.DataFrame(filas, columns=cols)


class TestProrratear(unittest.TestCase):
    def test_suma_exacta_con_redondeo(self):
        df = lineas(("F1", "FACTURA", 100, 110, 60), ("F1", "FACTURA", 100, 110, 60),
                    ("F1", "FACTURA", 100, 110, 60))
        reparto = reglas_negocio.prorratear(df, {"F1": 100.00})
        self.assertAlmostEqual(reparto.sum(), 100.00, places=2)   # 33,33 x 3 + residuo

    def test_neto_cero_usa_el_costo(self):
        # comprobante pagado entero con anticipo: neto 0 en todas las líneas
        df = lineas(("F1", "FACTURA", 0, 0, 300), ("F1", "FACTURA", 0, 0, 100))
        reparto = reglas_negocio.prorratear(df, {"F1": 1000})
        self.assertEqual(list(reparto), [750.0, 250.0])

    def test_nota_de_credito_no_recibe_anticipo(self):
        df = lineas(("F1", "FACTURA", 500, 550, 300), ("F1", "NOTA CREDITO", -100, -110, -60))
        reparto = reglas_negocio.prorratear(df, {"F1": 200})
        self.assertEqual(list(reparto), [200.0, 0.0])

    def test_nota_de_credito_se_corrige_con_su_propio_signo(self):
        df = lineas(("NC1", "NOTA CREDITO", -300, -330, -200), ("NC1", "NOTA CREDITO", -100, -110, -50))
        reparto = reglas_negocio.prorratear(df, {"NC1": -200}, solo_facturas=False)
        self.assertEqual(list(reparto), [-150.0, -50.0])


class TestTasaIva(unittest.TestCase):
    def test_engancha_a_la_tasa_mas_cercana(self):
        df = pd.DataFrame({"monto_neto": [1000, 1000, 1000, 0],
                           "monto_total": [1099.8, 1000, 1050.4, 0]})
        self.assertEqual(list(reglas_negocio.tasa_iva(df)), [1.10, 1.00, 1.05, 1.10])

    def test_llevar_a_monto_es_idempotente_y_corrige_el_total(self):
        df = lineas(("F1", "FACTURA", 600, 660, 300), ("F1", "FACTURA", 400, 440, 200))
        una = reglas_negocio.llevar_a_monto(df, {"F1": 500})
        dos = reglas_negocio.llevar_a_monto(una, {"F1": 500})
        self.assertEqual(list(una["monto_neto"]), [300.0, 200.0])
        self.assertEqual(list(una["monto_total"]), [330.0, 220.0])
        pd.testing.assert_frame_equal(una, dos)


class TestSucursalDominante(unittest.TestCase):
    def ventas(self, filas):
        df = pd.DataFrame(filas, columns=["fecha_emision", "cod_vendedor", "cod_sucursal", "monto_neto"])
        df["fecha_emision"] = pd.to_datetime(df["fecha_emision"])
        return df

    def test_toda_la_venta_del_mes_va_a_la_sucursal_donde_mas_vendio(self):
        df = self.ventas([("2026-09-01", "V02", "01", 900), ("2026-09-02", "V02", "02", 100)])
        r = reglas_negocio.sucursal_dominante(df, temporales=set(), reglas_sin_dominancia={})
        self.assertEqual(list(r["cod_sucursal_reporte"]), ["01", "01"])
        self.assertEqual(list(r["cod_sucursal"]), ["01", "02"])          # el dato crudo no se pisa

    def test_la_feria_nunca_es_dominante(self):
        df = self.ventas([("2026-07-01", "V04", "90", 900), ("2026-07-02", "V04", "02", 100)])
        r = reglas_negocio.sucursal_dominante(df, temporales={"90"}, reglas_sin_dominancia={})
        self.assertEqual(set(r["cod_sucursal_reporte"]), {"02"})

    def test_mes_solo_en_feria_toma_la_sucursal_del_mes_anterior(self):
        df = self.ventas([("2026-06-10", "V04", "02", 100), ("2026-07-10", "V04", "90", 900)])
        r = reglas_negocio.sucursal_dominante(df, temporales={"90"}, reglas_sin_dominancia={})
        self.assertEqual(list(r["cod_sucursal_reporte"]), ["02", "02"])

    def test_excepcion_rige_desde_su_mes(self):
        df = self.ventas([("2026-07-01", "V09", "02", 900), ("2026-07-02", "V09", "03", 100),
                          ("2026-08-01", "V09", "02", 900), ("2026-08-02", "V09", "03", 100)])
        r = reglas_negocio.sucursal_dominante(df, temporales=set(), reglas_sin_dominancia={"V09": "2026-08"})
        self.assertEqual(list(r["cod_sucursal_reporte"]), ["02", "02", "02", "03"])


class TestBloquesFecha(unittest.TestCase):
    def test_rango_corto_es_un_solo_bloque(self):
        b = extraccion.bloques_fecha(pd.Timestamp("2026-09-27"), pd.Timestamp("2026-10-01"), 45, 3)
        self.assertEqual(len(b), 1)

    def test_rango_largo_se_parte_y_el_ultimo_termina_hoy(self):
        hoy = pd.Timestamp("2026-10-01")
        b = extraccion.bloques_fecha(pd.Timestamp("2026-01-01"), hoy, 45, 3)
        self.assertEqual(len(b), 3)                                     # ene-mar, abr-jun, jul-sep
        self.assertEqual(b[-1][1], hoy)
        for (_, fin), (ini, _) in zip(b, b[1:]):
            self.assertEqual(fin, ini)                                  # sin huecos ni solapes


if __name__ == "__main__":
    unittest.main()
