"""
Tests de la extracción, la transformación y la actualización incremental.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actualizar_reporte  # noqa: E402
from lib import extraccion, transformacion  # noqa: E402


def filas(*datos):
    df = pd.DataFrame(datos, columns=["fecha", "documento", "linea", "cliente", "producto",
                                      "cantidad", "importe", "costo"])
    return transformacion.transformar(df)


class TestBloquesFecha(unittest.TestCase):
    def test_rango_corto_es_un_solo_bloque(self):
        b = extraccion.bloques_fecha(pd.Timestamp("2026-09-27"), pd.Timestamp("2026-10-01"), 45, 3)
        self.assertEqual(len(b), 1)

    def test_rango_largo_sin_huecos_ni_solapes(self):
        hoy = pd.Timestamp("2026-10-01")
        b = extraccion.bloques_fecha(pd.Timestamp("2026-01-01"), hoy, 45, 3)
        self.assertEqual(len(b), 3)                                     # ene-mar, abr-jun, jul-sep
        self.assertEqual(b[-1][1], hoy)
        for (_, fin), (ini, _) in zip(b, b[1:]):
            self.assertEqual(fin, ini)


class TestTransformacion(unittest.TestCase):
    def test_codigos_como_texto_limpio(self):
        df = filas(("2026-09-01", " f-1 ", 1, 7, "p1", 2, 100, 60))
        self.assertEqual(df.loc[0, "documento"], "F-1")
        self.assertEqual(df.loc[0, "cliente"], "7")
        self.assertEqual(df.loc[0, "periodo"], "2026-09")
        self.assertEqual(df.loc[0, "margen"], 40)

    def test_valida_clave_repetida_y_fecha_futura(self):
        df = filas(("2026-09-01", "F-1", 1, "C1", "P1", 1, 100, 60),
                   ("2026-09-01", "F-1", 1, "C1", "P1", 1, 100, 60),
                   ("2026-10-01", "F-2", 1, "C1", "P1", 1, 100, 60))
        problemas = transformacion.validar(df, hasta=pd.Timestamp("2026-10-01"))
        self.assertEqual(len(problemas), 2)
        self.assertIn("repetida", problemas[0])
        self.assertIn("posterior", problemas[1])

    def test_datos_buenos_no_tienen_problemas(self):
        df = filas(("2026-09-01", "F-1", 1, "C1", "P1", 1, 100, 60))
        self.assertEqual(transformacion.validar(df, hasta=pd.Timestamp("2026-10-01")), [])


class TestActualizacionIncremental(unittest.TestCase):
    def test_la_ventana_se_reemplaza_entera(self):
        """Un documento anulado en el sistema desaparece también del reporte."""
        reporte = filas(("2026-09-20", "F-1", 1, "C1", "P1", 1, 100, 60),
                        ("2026-09-29", "F-2", 1, "C1", "P1", 1, 100, 60))       # luego anulado
        nuevos = filas(("2026-09-30", "F-3", 1, "C1", "P1", 1, 100, 60))
        desde, _ = actualizar_reporte.calcular_ventana(reporte, hoy=pd.Timestamp("2026-10-01"))
        final = actualizar_reporte.unir(reporte, nuevos, desde)
        self.assertEqual(list(final["documento"]), ["F-1", "F-3"])

    def test_el_dia_en_curso_queda_afuera(self):
        _, hasta = actualizar_reporte.calcular_ventana(pd.DataFrame(), hoy=pd.Timestamp("2026-10-01 15:30"))
        self.assertEqual(hasta, pd.Timestamp("2026-10-01"))


if __name__ == "__main__":
    unittest.main()
