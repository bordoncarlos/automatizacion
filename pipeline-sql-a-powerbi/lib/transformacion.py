"""
Transformación y validación de los datos antes de escribir el reporte.

PARA QUÉ SIRVE
--------------
1. Dejar cada columna con un tipo fijo y columnas listas para el reporte.
2. VALIDAR antes de escribir. Si los datos vienen mal (claves repetidas,
   fechas fuera de rango, importes vacíos), el script NO pisa el archivo:
   Power BI sigue mostrando los datos buenos de la corrida anterior y el log
   dice qué pasó. Un reporte desactualizado se nota; uno con datos malos,
   muchas veces no.
"""
import pandas as pd

import config


def normalizar_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """Un tipo fijo por columna.

    Un código que un día llega como número (1001) y otro como texto ("1001 ")
    rompe las relaciones del modelo de Power BI. Todo código se guarda como
    texto sin espacios y en mayúsculas (Power BI compara las claves sin
    distinguir mayúsculas: "abc" y "ABC" serían dos filas para pandas y una
    sola clave para el modelo).
    """
    df = df.copy()
    for col in config.COLUMNAS_TEXTO:
        df[col] = df[col].astype(str).str.strip().str.upper()
    for col in config.COLUMNAS_NUMERO:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df[config.COLUMNA_FECHA] = pd.to_datetime(df[config.COLUMNA_FECHA])
    return df


def agregar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """Columnas que el reporte usa y conviene calcular una sola vez acá."""
    df = df.copy()
    df["periodo"] = df[config.COLUMNA_FECHA].dt.to_period("M").astype(str)   # "2026-09"
    df["margen"] = df["importe"] - df["costo"]
    return df


def transformar(df: pd.DataFrame) -> pd.DataFrame:
    return agregar_columnas(normalizar_tipos(df))


def validar(df: pd.DataFrame, hasta: pd.Timestamp) -> list:
    """Lista de problemas (vacía = todo bien). No corrige nada: avisa."""
    problemas = []
    vacias = df[config.CLAVE].isna().any(axis=1) | (df[config.CLAVE].astype(str) == "").any(axis=1)
    if vacias.any():
        problemas.append(f"{vacias.sum():,} filas sin clave ({', '.join(config.CLAVE)})")
    repetidas = df.duplicated(config.CLAVE, keep=False)
    if repetidas.any():
        ejemplo = df.loc[repetidas, config.CLAVE].head(3).to_dict("records")
        problemas.append(f"{repetidas.sum():,} filas con la clave repetida, por ejemplo {ejemplo}")
    futuras = df[config.COLUMNA_FECHA] >= hasta
    if futuras.any():
        problemas.append(f"{futuras.sum():,} filas con fecha igual o posterior a {hasta:%d/%m/%Y}")
    sin_importe = df["importe"].isna()
    if sin_importe.any():
        problemas.append(f"{sin_importe.sum():,} filas sin importe")
    return problemas
