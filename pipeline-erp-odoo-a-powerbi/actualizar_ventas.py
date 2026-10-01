"""
Ventas de Odoo -> Excel que lee Power BI. Corre CADA MINUTO (08:00 a 19:00).

PARA QUÉ SIRVE
--------------
Deja en `datos/ventas_erp.xlsx` todas las ventas desde que la empresa usa
Odoo, incluido el día en curso, línea por línea y con el mismo esquema que
el histórico del sistema anterior. En Power BI esa tabla se APENDEA al
histórico: el informe muestra todo junto y la venta del día casi en vivo.

Antes, ese Excel lo pegaba alguien a mano. El script escribe el MISMO
archivo con las MISMAS columnas y tipos: el modelo de Power BI no se tocó.

CÓMO LO HACE SIN GASTAR LA API
------------------------------
Bajar todo Odoo cada minuto sería lento. Entonces:

- Los días CERRADOS se guardan en una caché (`ventas_cerradas.parquet`).
- En cada corrida se re-extraen sólo los últimos DIAS_RETROACTIVOS días
  cerrados (en Odoo se puede cancelar una factura días después y la
  cancelada desaparece: re-extraer la ventana entera la hace desaparecer
  también acá) y el día de HOY.
- `--completo` descarta la caché y baja todo (por ejemplo, si cambió una
  regla de traducción).

Las ventas de hoy quedan además en `ventas_hoy.parquet`: la tarea de cobranza
las usa para el contado sin volver a pedírselas a Odoo.

CÓDIGOS DE SALIDA: 0 OK · 3 el Excel está abierto (se escribe en el próximo minuto).
"""
import argparse
import sys
import time

import pandas as pd

import config
from lib import consola, escritura, traduccion
from lib.odoo_cliente import conectar

FORMATOS_EXCEL = {"fecha_emision": "dd/mm/yyyy hh:mm", "cantidad": "#,##0",
                  "precio_unitario": "#,##0", "monto_neto": "#,##0", "monto_total": "#,##0",
                  "monto_costo": "#,##0", "cotizacion": "#,##0.00"}


def dias_cerrados(odoo, hoy, completo=False):
    """Ventas de todos los días cerrados (< hoy), con caché."""
    cache = pd.DataFrame()
    if config.CACHE_VENTAS.exists() and not completo:
        cache = pd.read_parquet(config.CACHE_VENTAS)
    if cache.empty:
        print("   Caché vacía: se extraen todos los días cerrados")
        desde = config.ERP_DESDE
    else:
        desde = max(hoy - pd.Timedelta(days=config.DIAS_RETROACTIVOS), config.ERP_DESDE)
        cache = cache[cache["fecha_emision"] < desde]       # la ventana se reemplaza ENTERA
    nuevo = traduccion.extraer_ventas(odoo, desde, hoy) if desde < hoy else traduccion._vacio()
    cerrados = traduccion.unir(cache, nuevo)
    cerrados = cerrados[cerrados["fecha_emision"] < hoy]   # por si la caché traía un "hoy" viejo
    escritura.escribir_parquet(cerrados, config.CACHE_VENTAS)
    return cerrados


def main():
    consola.preparar("ventas")
    ap = argparse.ArgumentParser(description="Ventas de Odoo -> Excel de Power BI")
    ap.add_argument("--completo", action="store_true", help="descartar la caché y bajar todo")
    args = ap.parse_args()
    inicio = time.time()
    hoy = pd.Timestamp.now().normalize()

    if escritura.archivo_tomado(config.EXCEL_VENTAS):
        print(f"[SKIP] {config.EXCEL_VENTAS.name} está abierto (Excel o refresh de Power BI); "
              f"se escribe en la próxima corrida")
        return escritura.CODIGO_SALTEADO

    print(f"VENTAS ODOO {config.ERP_DESDE:%d/%m/%Y} -> {hoy:%d/%m/%Y} (incluye hoy)")
    odoo = conectar()
    cerrados = dias_cerrados(odoo, hoy, args.completo)
    de_hoy = traduccion.extraer_ventas(odoo, hoy, hoy + pd.Timedelta(days=1))
    escritura.escribir_parquet(de_hoy, config.VENTAS_HOY)

    ventas = traduccion.unir(cerrados, de_hoy)
    ventas = ventas.sort_values(["fecha_emision", "nro_comprobante"]).reset_index(drop=True)
    print(traduccion.resumen_por_dia(ventas))
    print(f"   Total: {ventas['nro_comprobante'].nunique():,} comprobantes, {len(ventas):,} líneas, "
          f"{ventas['monto_neto'].sum():,.0f} Gs")

    try:
        escritura.escribir_excel(ventas, config.EXCEL_VENTAS, hoja="Ventas", formatos=FORMATOS_EXCEL)
    except escritura.ArchivoTomado:
        # Se abrió entre el chequeo del principio y ahora (típico: arrancó el
        # refresh de Power BI mientras se consultaba la API). No es un error.
        print(f"[SKIP] {config.EXCEL_VENTAS.name} se tomó durante la corrida; se escribe en la próxima")
        return escritura.CODIGO_SALTEADO
    print(f"[OK] {config.EXCEL_VENTAS.name} escrito en {time.time() - inicio:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
