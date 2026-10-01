"""
Salida de los scripts cuando corren sin ventana.

PARA QUÉ SIRVE
--------------
Las tareas programadas corren con `pythonw.exe` para que no aparezca una
consola cada minuto en la pantalla del usuario. Pero sin consola
`sys.stdout` es None y el primer `print` hace caer el script SIN dejar
rastro. `preparar()` manda toda la salida a un log por día:

    logs/AAAA-MM/<nombre>_AAAAMMDD.log

Es la única forma de saber por qué falló una tarea que no tiene ventana.
"""
import sys
from datetime import datetime

import config


def preparar(nombre: str) -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return                                   # con consola: se ve en pantalla
    carpeta = config.LOGS_DIR / f"{datetime.now():%Y-%m}"
    carpeta.mkdir(parents=True, exist_ok=True)
    log = open(carpeta / f"{nombre}_{datetime.now():%Y%m%d}.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log                # buffering=1: línea por línea, aunque se caiga
    print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===")
