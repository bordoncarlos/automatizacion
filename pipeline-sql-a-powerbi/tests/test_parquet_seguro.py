"""Tests de la escritura segura del parquet."""
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import parquet_seguro  # noqa: E402


class TestEscrituraSegura(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ruta = os.path.join(self.dir.name, "ventas.parquet")
        self.df = pd.DataFrame({"a": [1, 2, 3]})

    def tearDown(self):
        self.dir.cleanup()

    def test_escribe_y_no_deja_temporales(self):
        parquet_seguro.escribir_parquet_seguro(self.df, self.ruta)
        pd.testing.assert_frame_equal(pd.read_parquet(self.ruta), self.df)
        self.assertEqual(os.listdir(self.dir.name), ["ventas.parquet"])

    def test_reintenta_si_el_archivo_esta_tomado(self):
        """Power BI refrescando: las dos primeras veces el destino está bloqueado."""
        reemplazo_real = os.replace
        llamadas = {"n": 0}

        def replace_bloqueado(origen, destino):
            llamadas["n"] += 1
            if llamadas["n"] <= 2:
                raise PermissionError(5, "Acceso denegado")
            reemplazo_real(origen, destino)

        with mock.patch("os.replace", replace_bloqueado), mock.patch("time.sleep"):
            parquet_seguro.escribir_parquet_seguro(self.df, self.ruta)
        self.assertEqual(llamadas["n"], 3)
        pd.testing.assert_frame_equal(pd.read_parquet(self.ruta), self.df)

    def test_si_sigue_tomado_falla_y_el_original_queda_intacto(self):
        parquet_seguro.escribir_parquet_seguro(self.df, self.ruta)
        nuevo = pd.DataFrame({"a": [9]})
        with mock.patch("os.replace", side_effect=PermissionError(5, "Acceso denegado")), \
                mock.patch("time.sleep"):
            with self.assertRaises(PermissionError):
                parquet_seguro.escribir_parquet_seguro(nuevo, self.ruta)
        pd.testing.assert_frame_equal(pd.read_parquet(self.ruta), self.df)   # el viejo, entero
        self.assertEqual(os.listdir(self.dir.name), ["ventas.parquet"])       # sin temporal


if __name__ == "__main__":
    unittest.main()
