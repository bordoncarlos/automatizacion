"""
Reglas de negocio que se aplican sobre las ventas antes de guardarlas.

PARA QUÉ SIRVE
--------------
El sistema de origen guarda los datos para OPERAR (quién facturó, en qué
caja), no para ANALIZAR (a qué sucursal se le cuenta la venta de un vendedor,
quién heredó la cartera de quién). Este módulo traduce de una cosa a la otra.

Principio de diseño: NUNCA se pisa la columna original. El dato crudo queda
(`cod_vendedor_original`, `cod_sucursal`) y la versión "para el reporte" va
en otra columna (`cod_vendedor`, `cod_sucursal_reporte`). Así, cuando cambia
una regla, se vuelve a calcular sobre todo el histórico en la corrida
siguiente, sin re-extraer nada de la base.
(La primera versión pisaba la columna: cambiar una regla obligaba a volver a
extraer meses enteros, una tarea de horas.)
"""
import numpy as np
import pandas as pd

import config

COLUMNAS_TEXTO = ["nro_comprobante", "cliente", "cod_vendedor_original", "cod_sucursal",
                  "cod_articulo", "tipo_comprobante", "condicion_venta", "moneda"]


# =============================================================================
# 1. TIPOS
# =============================================================================
def normalizar_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """Deja cada columna con un tipo fijo.

    Un código que un día llega como número (`1001`) y otro como texto
    (`"1001 "`) rompe las relaciones de Power BI y el parquet puede rechazar
    columnas con tipos mezclados. Todo código se guarda como texto sin espacios.
    """
    df = df.copy()
    if "cod_vendedor_original" not in df.columns and "cod_vendedor" in df.columns:
        df["cod_vendedor_original"] = df["cod_vendedor"]
    for col in COLUMNAS_TEXTO:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    df["fecha_emision"] = pd.to_datetime(df["fecha_emision"])
    return df


# =============================================================================
# 2. VENDEDORES
# =============================================================================
def aplicar_mapeo_vendedores(df: pd.DataFrame, mapeo=None) -> pd.DataFrame:
    """`cod_vendedor` = el vendedor ACTUAL de esa cartera.

    Siempre se calcula desde `cod_vendedor_original`, así que agregar o sacar
    un par del mapeo se refleja en todo el histórico en la próxima corrida.
    """
    mapeo = config.VENDEDOR_MAPPING if mapeo is None else mapeo
    df = df.copy()
    df["cod_vendedor"] = df["cod_vendedor_original"].replace(mapeo)
    return df


def mask_sin_dominancia(vendedores: pd.Series, meses: pd.Series, reglas=None) -> pd.Series:
    """True en las filas cuyo vendedor está exceptuado de la sucursal dominante.

    `meses` son textos 'AAAA-MM'. Una regla con mes de inicio sólo rige desde
    ese mes: los meses anteriores conservan la cifra que ya se reportó.
    """
    reglas = config.VENDEDORES_SIN_DOMINANCIA if reglas is None else reglas
    mask = pd.Series(False, index=vendedores.index)
    for vendedor, desde in reglas.items():
        es = vendedores == vendedor
        mask |= es if desde is None else (es & (meses >= desde))
    return mask


# =============================================================================
# 3. SUCURSAL DOMINANTE
# =============================================================================
def sucursal_dominante(df: pd.DataFrame, temporales=None, reglas_sin_dominancia=None) -> pd.DataFrame:
    """Imputa TODA la venta del mes de un vendedor a UNA sola sucursal.

    Regla comercial: un vendedor pertenece a una sucursal. Si en un mes
    facturó en dos (cubriendo a un compañero, en una feria), su venta entera
    se cuenta en la sucursal donde más vendió. Si no, aparecería partido en
    dos sucursales y las metas por sucursal no cerrarían.

    Pasos:
      a) monto por vendedor + mes + sucursal;
      b) la sucursal de mayor monto es la dominante;
      c) si la dominante es TEMPORAL (feria, cuentas corrientes) se rescata
         la mejor sucursal real del mismo mes;
      d) si ese mes sólo vendió en temporales, se toma la última sucursal real
         de meses anteriores (o la primera de meses siguientes);
      e) los vendedores exceptuados conservan la sucursal de cada factura.

    El resultado va a `cod_sucursal_reporte`; `cod_sucursal` no se toca.
    """
    temporales = config.SUCURSALES_TEMPORALES if temporales is None else temporales
    df = df.copy()
    mes = df["fecha_emision"].dt.to_period("M").astype(str)
    df["cod_sucursal_reporte"] = df["cod_sucursal"]
    validas = ~mask_sin_dominancia(df["cod_vendedor"], mes, reglas_sin_dominancia)
    if not validas.any():
        return df

    base = df.loc[validas, ["cod_vendedor", "cod_sucursal", "monto_neto"]].assign(mes=mes[validas])

    # a) y b)
    montos = base.groupby(["cod_vendedor", "mes", "cod_sucursal"], as_index=False)["monto_neto"].sum()
    idx = montos.groupby(["cod_vendedor", "mes"])["monto_neto"].idxmax()
    dominantes = montos.loc[idx, ["cod_vendedor", "mes", "cod_sucursal"]].rename(
        columns={"cod_sucursal": "asignada"}).reset_index(drop=True)

    # c) rescate dentro del mismo mes
    reales = montos[~montos["cod_sucursal"].isin(temporales)]
    if dominantes["asignada"].isin(temporales).any() and not reales.empty:
        idx_r = reales.groupby(["cod_vendedor", "mes"])["monto_neto"].idxmax()
        rescate = reales.loc[idx_r, ["cod_vendedor", "mes", "cod_sucursal"]].rename(
            columns={"cod_sucursal": "rescate"})
        dominantes = dominantes.merge(rescate, on=["cod_vendedor", "mes"], how="left")
        usar = dominantes["asignada"].isin(temporales) & dominantes["rescate"].notna()
        dominantes.loc[usar, "asignada"] = dominantes.loc[usar, "rescate"]
        dominantes = dominantes.drop(columns="rescate")

    # d) rescate con otros meses
    for i in dominantes.index[dominantes["asignada"].isin(temporales)]:
        vend, m = dominantes.at[i, "cod_vendedor"], dominantes.at[i, "mes"]
        propias = reales[reales["cod_vendedor"] == vend].sort_values("mes")
        antes, despues = propias[propias["mes"] < m], propias[propias["mes"] > m]
        if not antes.empty:
            dominantes.at[i, "asignada"] = antes.iloc[-1]["cod_sucursal"]
        elif not despues.empty:
            dominantes.at[i, "asignada"] = despues.iloc[0]["cod_sucursal"]
        # si nunca vendió en una sucursal real, queda la temporal

    # aplicar (sólo a las filas válidas; e) las exceptuadas quedan con la suya)
    clave = base[["cod_vendedor", "mes"]].reset_index()
    asignada = clave.merge(dominantes, on=["cod_vendedor", "mes"], how="left").set_index("index")["asignada"]
    df.loc[asignada.index, "cod_sucursal_reporte"] = asignada
    return df


# =============================================================================
# 4. PRORRATEO DE UN MONTO DE CABECERA ENTRE LAS LÍNEAS
# =============================================================================
def prorratear(df: pd.DataFrame, montos: dict, solo_facturas: bool = True) -> pd.Series:
    """Reparte un monto por comprobante entre sus líneas, con suma EXACTA.

    `montos` = {nro_comprobante: monto a repartir}. Devuelve una Series
    alineada a `df` (0 en las líneas que no reciben nada).

    Por qué no alcanza con `neto_línea / SUM(neto)`: en datos reales hay
    comprobantes cuyo neto suma CERO (pagados entero con un anticipo:
    división por cero), comprobantes con líneas de signo mixto y notas de
    crédito bajo el mismo número que la factura. Entonces:

      - con `solo_facturas` (repartir un anticipo, por ejemplo) se reparte
        sólo entre líneas FACTURA: una nota de crédito no lo consumió;
      - se usa la primera base que sirva:
        neto > 0  ->  neto < 0 (nota de crédito)  ->  total > 0  ->  |costo|  ->  parejo;
      - el residuo del redondeo se suma a la línea de mayor peso, así la
        suma por comprobante cierra exacta al centésimo.
    """
    reparto = pd.Series(0.0, index=df.index)
    elegibles = df["nro_comprobante"].isin(montos)
    if solo_facturas:
        elegibles &= df["tipo_comprobante"].astype(str).str.upper().eq("FACTURA")
    candidatas = df.index[elegibles]
    if len(candidatas) == 0:
        return reparto

    sub = df.loc[candidatas, ["nro_comprobante", "monto_neto", "monto_total", "monto_costo"]]
    for nro, g in sub.groupby("nro_comprobante", sort=False):
        monto = float(montos[nro])
        peso = None
        for serie in (g["monto_neto"].clip(lower=0), (-g["monto_neto"]).clip(lower=0),
                      g["monto_total"].clip(lower=0), g["monto_costo"].abs()):
            s = serie.fillna(0.0)
            if float(s.sum()) > 0:
                peso = s
                break
        if peso is None:
            peso = pd.Series(1.0, index=g.index)
        parte = (peso / float(peso.sum()) * monto).round(2)
        residuo = round(monto - float(parte.sum()), 2)
        if residuo:
            parte.loc[peso.idxmax()] += residuo
        reparto.loc[g.index] = parte.values
    return reparto


# =============================================================================
# 5. IVA
# =============================================================================
def tasa_iva(df: pd.DataFrame, tasas=None, estandar=None) -> pd.Series:
    """Tasa de IVA de cada línea (total / neto), ENGANCHADA a la tasa válida más cercana.

    La razón cruda no sirve: el redondeo de la conversión de moneda la deja
    en 1,0998 y, peor, una línea con el neto ya corregido la dejaría en
    1,1017 haciéndose pasar por buena. Se engancha a 1,00 / 1,05 / 1,10 si
    está a menos de 0,01; si no cae cerca de ninguna (o el neto es 0), se
    usa la estándar. Así una línea EXENTA (1,00) no se infla al 10 %.
    """
    tasas = config.IVA_TASAS if tasas is None else tasas
    estandar = config.IVA_ESTANDAR if estandar is None else estandar
    razon = df["monto_total"] / df["monto_neto"].replace(0, np.nan)
    enganchada = pd.Series(np.nan, index=df.index)
    for t in tasas:
        enganchada = enganchada.mask((razon - t).abs() <= 0.01, t)
    return enganchada.fillna(estandar)


# =============================================================================
# 6. CORRECCIONES (las decide la conciliación, las reaplica la extracción)
# =============================================================================
def llevar_a_monto(df: pd.DataFrame, objetivos: dict) -> pd.DataFrame:
    """Lleva cada comprobante de `objetivos` ({nro: neto oficial}) a ese neto.

    Reparte el nuevo neto entre las líneas (suma exacta) y recalcula el total
    con IVA de cada una con su propia tasa. Corregir sólo el neto dejaba el
    total viejo: un comprobante anulado quedaba en 0 de neto pero seguía
    sumando su total con IVA en el informe.
    Es idempotente: aplicarla dos veces da lo mismo que una.
    """
    lineas = df["nro_comprobante"].isin(objetivos)
    if not lineas.any():
        return df
    df = df.copy()
    tasa = tasa_iva(df.loc[lineas])                    # ANTES de tocar el neto
    nuevo = prorratear(df, objetivos, solo_facturas=False)
    df.loc[lineas, "monto_neto"] = nuevo[lineas]
    df.loc[lineas, "monto_total"] = (nuevo[lineas] * tasa).round(0) + 0.0   # +0.0: sin "-0"
    return df


def aplicar_correcciones_guardadas(df: pd.DataFrame, ruta_bitacora) -> tuple[pd.DataFrame, int]:
    """Vuelve a aplicar las correcciones de la bitácora.

    La extracción re-trae los últimos días desde la base, que sigue con el
    monto viejo: sin esto, cada corrida deshacería lo que corrigió la
    conciliación.
    """
    if not ruta_bitacora.exists():
        return df, 0
    bitacora = pd.read_csv(ruta_bitacora, dtype={"nro_comprobante": str})
    ultimas = bitacora.drop_duplicates("nro_comprobante", keep="last")
    objetivos = dict(zip(ultimas["nro_comprobante"], ultimas["monto_oficial"]))
    return llevar_a_monto(df, objetivos), int(df["nro_comprobante"].isin(objetivos).sum())


# =============================================================================
# TODO JUNTO
# =============================================================================
def postprocesar(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica las reglas en orden. La usan la extracción y la conciliación,
    para que las dos dejen el parquet exactamente igual."""
    df = normalizar_tipos(df)
    df = aplicar_mapeo_vendedores(df)
    df = sucursal_dominante(df)
    return df
