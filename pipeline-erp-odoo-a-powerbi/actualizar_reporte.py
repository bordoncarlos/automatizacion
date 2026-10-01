"""
Actualiza el reporte: Odoo (API) -> transformación -> Excel que lee Power BI.

PARA QUÉ SIRVE
--------------
Reemplaza el "exportar de Odoo y pegar en Excel". Deja en `datos/reporte.xlsx`
las facturas y notas de crédito desde DESDE hasta este momento, una fila por
línea de producto. Puede correr cada minuto: el reporte ve el día casi en vivo.

CÓMO LO HACE SIN GASTAR LA API
------------------------------
Bajar todo cada minuto sería lento. Entonces:

- Los días CERRADOS se guardan en una caché (parquet).
- En cada corrida se vuelven a extraer sólo los últimos DIAS_RETROACTIVOS
  días cerrados y el día de HOY. La ventana existe porque en Odoo una factura
  se puede cancelar días después, y la cancelada desaparece: reemplazar la
  ventana entera la hace desaparecer también del reporte.
- `--completo` descarta la caché y baja todo de nuevo.

CÓDIGOS DE SALIDA: 0 OK · 3 el Excel está abierto (lo escribe la próxima corrida).
"""
import argparse
import sys
import time

import pandas as pd

import config
from lib import consola, escritura, transformacion
from lib.odoo_cliente import conectar

FORMATOS_EXCEL = {"fecha": "dd/mm/yyyy hh:mm", "cantidad": "#,##0", "precio_unitario": "#,##0",
                  "importe_neto": "#,##0", "importe_total": "#,##0", "costo": "#,##0",
                  "cotizacion": "#,##0.00"}


def dias_cerrados(odoo, hoy, completo=False):
    """Todo lo anterior a hoy, con caché: sólo se re-extrae la ventana."""
    cache = pd.DataFrame()
    if config.CACHE.exists() and not completo:
        cache = pd.read_parquet(config.CACHE)
    if cache.empty:
        print("   Caché vacía: se extraen todos los días cerrados")
        desde = config.DESDE
    else:
        desde = max(hoy - pd.Timedelta(days=config.DIAS_RETROACTIVOS), config.DESDE)
        cache = cache[cache["fecha"] < desde]                  # la ventana se reemplaza ENTERA
    nuevo = transformacion.extraer(odoo, desde, hoy) if desde < hoy else transformacion.vacio()
    cerrados = transformacion.unir(cache, nuevo)
    cerrados = cerrados[cerrados["fecha"] < hoy]
    escritura.escribir_parquet(cerrados, config.CACHE)
    return cerrados


def main():
    consola.preparar("reporte")
    ap = argparse.ArgumentParser(description="Actualiza el reporte desde Odoo")
    ap.add_argument("--completo", action="store_true", help="descartar la caché y bajar todo")
    args = ap.parse_args()
    inicio = time.time()
    hoy = pd.Timestamp.now().normalize()

    # Antes de llamar a la API: si el Excel está abierto, no tiene sentido
    # gastar 20 segundos de consultas para no poder escribir al final.
    if escritura.archivo_tomado(config.ARCHIVO_REPORTE):
        print(f"[SKIP] {config.ARCHIVO_REPORTE.name} está abierto; lo escribe la próxima corrida")
        return escritura.CODIGO_SALTEADO

    print(f"ACTUALIZAR REPORTE ODOO {config.DESDE:%d/%m/%Y} -> {hoy:%d/%m/%Y} (incluye hoy)")
    odoo = conectar()
    cerrados = dias_cerrados(odoo, hoy, args.completo)
    de_hoy = transformacion.extraer(odoo, hoy, hoy + pd.Timedelta(days=1))
    reporte = transformacion.unir(cerrados, de_hoy).sort_values(["fecha", "comprobante"]).reset_index(drop=True)

    print(transformacion.resumen_por_dia(reporte))
    print(f"   Total: {reporte['comprobante'].nunique():,} comprobantes, {len(reporte):,} líneas")

    try:
        escritura.escribir_excel(reporte, config.ARCHIVO_REPORTE, hoja="Reporte", formatos=FORMATOS_EXCEL)
    except escritura.ArchivoTomado:
        # Se abrió entre el chequeo del principio y ahora (típico: arrancó el
        # refresh de Power BI mientras se consultaba la API). No es un error.
        print(f"[SKIP] {config.ARCHIVO_REPORTE.name} se tomó durante la corrida; lo escribe la próxima")
        return escritura.CODIGO_SALTEADO
    print(f"[OK] {config.ARCHIVO_REPORTE.name} escrito en {time.time() - inicio:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
