"""
Base de clientes: agrega los clientes nuevos desde Odoo. Corre CADA HORA.

PARA QUÉ SIRVE
--------------
La tabla de clientes es la dimensión del modelo de Power BI: cada venta y
cada cobro se relaciona con un cliente por su RUC. Un RUC que aparece en las
ventas y no está en la tabla queda "en blanco" en todos los informes.

TRES COSAS QUE MANDAN EN EL DISEÑO
----------------------------------
1. La base vieja es el PISO. Tiene clientes del sistema anterior que Odoo no
   tiene pero que el histórico de ventas sí usa: nunca se borra una fila.
   Sólo se AGREGAN los RUC que aparecen en ventas o cobranza y faltan.
2. El RUC NO es único en Odoo. La misma empresa puede estar cargada como
   compañía y como contacto, o con el RUC en minúsculas. Se elige uno por
   RUC con una regla fija: compañía antes que contacto, el que más compra
   (`customer_rank`) y, si empatan, el más viejo.
3. El canal comercial (mayorista, minorista...) no existe en Odoo. Un
   cliente nuevo entra con canal "A DEFINIR" para que el área comercial lo
   vea y lo complete; nunca se inventa.

Si no falta ningún RUC, no escribe nada (la corrida tarda un par de segundos).
"""
import sys
import time

import pandas as pd

import config
from lib import consola, escritura, traduccion
from lib.odoo_cliente import conectar

COLUMNAS = ["cliente", "razon_social", "canal", "fecha_alta"]


def ruc_de_los_hechos() -> set:
    """Todos los RUC que aparecen en ventas y cobranza."""
    rucs = set()
    for ruta in (config.CACHE_VENTAS, config.VENTAS_HOY, config.COBRANZA):
        if ruta.exists():
            rucs |= set(pd.read_parquet(ruta, columns=["cliente"])["cliente"].map(traduccion.normalizar_ruc))
    return {r for r in rucs if r}            # un RUC vacío (consumidor final) no es un cliente


def elegir_uno_por_ruc(contactos: list) -> dict:
    """{RUC: contacto elegido}. Regla: compañía > más compras > id más viejo."""
    elegidos = {}
    orden = sorted(contactos, key=lambda c: (not c["is_company"], -(c["customer_rank"] or 0), c["id"]))
    for c in orden:
        elegidos.setdefault(traduccion.normalizar_ruc(c["vat"]), c)
    return elegidos


def main():
    consola.preparar("clientes")
    inicio = time.time()
    base = pd.read_parquet(config.CLIENTES) if config.CLIENTES.exists() else pd.DataFrame(columns=COLUMNAS)
    existentes = set(base["cliente"].map(traduccion.normalizar_ruc))
    faltantes = sorted(ruc_de_los_hechos() - existentes)
    if not faltantes:
        print(f"[OK] no falta ningún cliente ({len(base):,} en la base, {time.time() - inicio:.1f} s)")
        return 0
    if escritura.archivo_tomado(config.CLIENTES):
        print(f"[SKIP] {config.CLIENTES.name} tomado; se agrega en la próxima corrida")
        return escritura.CODIGO_SALTEADO

    # Sólo se buscan en Odoo los RUC que faltan, no el padrón entero: una
    # llamada con todos. Los que no aparecen tal cual (RUC cargado en
    # minúsculas o con espacios) se reintentan uno por uno con "ilike".
    odoo = conectar()
    campos = ["name", "vat", "is_company", "customer_rank"]
    contactos = odoo.search_read("res.partner", [("vat", "in", faltantes)], campos)
    encontrados = {traduccion.normalizar_ruc(c["vat"]) for c in contactos}
    for ruc in (r for r in faltantes if r not in encontrados):
        contactos += odoo.search_read("res.partner", [("vat", "ilike", ruc)], campos)
    elegidos = elegir_uno_por_ruc([c for c in contactos if traduccion.normalizar_ruc(c["vat"]) in faltantes])

    hoy = pd.Timestamp.now().normalize()
    nuevos = pd.DataFrame([{
        "cliente": ruc,
        "razon_social": elegidos[ruc]["name"] if ruc in elegidos else "SIN DATO EN ODOO",
        "canal": config.CANAL_NUEVO,
        "fecha_alta": hoy,
    } for ruc in faltantes], columns=COLUMNAS)
    base = pd.concat([d for d in (base, nuevos) if not d.empty], ignore_index=True)

    try:
        escritura.escribir_parquet(base, config.CLIENTES)
    except escritura.ArchivoTomado:
        print(f"[SKIP] {config.CLIENTES.name} se tomó durante la corrida")
        return escritura.CODIGO_SALTEADO
    base.to_csv(config.CLIENTES.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    print(nuevos.head(10).to_string(index=False))
    print(f"[OK] {len(nuevos):,} clientes agregados con canal '{config.CANAL_NUEVO}' "
          f"({len(base):,} en la base, {time.time() - inicio:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
