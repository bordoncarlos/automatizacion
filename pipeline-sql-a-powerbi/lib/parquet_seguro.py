"""
Escritura segura de archivos parquet.

PARA QUÉ SIRVE
--------------
El parquet histórico es lo que lee Power BI. Si se corrompe, se cae el
informe entero. Este módulo resuelve los tres problemas que aparecieron en
producción:

1. DOS SCRIPTS ESCRIBIENDO A LA VEZ. Si la extracción y la conciliación
   escriben el mismo archivo al mismo tiempo, se pisan y el parquet queda
   ilegible. -> `adquirir_lock_escritura()`: sólo un proceso escribe a la vez.

2. UN PROCESO QUE SE CORTA A LA MITAD. Si se escribe directo sobre el archivo
   y el proceso muere (corte de luz, timeout), queda un archivo a medias.
   -> se escribe en un temporal y se reemplaza de una sola vez (`os.replace`
   es atómico: el archivo es el viejo entero o el nuevo entero, nunca mezcla).

3. POWER BI TIENE EL ARCHIVO ABIERTO. Mientras refresca, Power BI Desktop
   toma el archivo y el reemplazo falla con PermissionError. -> se reintenta
   unos segundos: los choques son de borde y se resuelven solos.
"""
import os
import sys
import time

try:                    # Windows
    import msvcrt
except ImportError:     # Linux / macOS
    msvcrt = None
    import fcntl

# Los handles de los locks tienen que vivir mientras viva el proceso: si el
# objeto se libera, el sistema operativo suelta el lock.
_locks_activos = []

REINTENTOS_REEMPLAZO = 5
ESPERA_REEMPLAZO_S = 3


def adquirir_lock_escritura(ruta_parquet, esperar_segundos: float = 0):
    """Toma el lock de escritor de `ruta_parquet` o aborta con un mensaje claro.

    El lock lo mantiene el sistema operativo sobre un archivo `.lock` y se
    libera SOLO cuando el proceso termina, aunque termine con un error: no
    hay que acordarse de soltarlo y nunca queda un lock "huérfano".
    """
    ruta_lock = f"{ruta_parquet}.lock"
    fh = open(ruta_lock, "a+")
    inicio = time.time()
    while True:
        try:
            if msvcrt is not None:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _locks_activos.append(fh)
            return fh
        except OSError:
            if time.time() - inicio >= esperar_segundos:
                fh.close()
                sys.exit(f"[ABORTADO] Otro proceso está actualizando "
                         f"{os.path.basename(str(ruta_parquet))}. Esperar a que termine.")
            time.sleep(2)


def reemplazar_con_reintento(temporal, destino,
                             reintentos=REINTENTOS_REEMPLAZO, espera=ESPERA_REEMPLAZO_S):
    """`os.replace` que tolera que el destino esté tomado unos segundos."""
    for intento in range(1, reintentos + 1):
        try:
            os.replace(temporal, destino)
            return
        except PermissionError:
            if intento == reintentos:
                raise
            print(f"[AVISO] {os.path.basename(str(destino))} está tomado por otro proceso "
                  f"(¿refresh de Power BI?); reintento {intento}/{reintentos - 1} en {espera} s",
                  flush=True)
            time.sleep(espera)


def escribir_parquet_seguro(df, ruta_parquet, **kwargs):
    """Escribe `df` a parquet de forma atómica.

    El temporal lleva el PID del proceso: dos procesos nunca comparten
    temporal. (La primera versión usaba `archivo.parquet.tmp` fijo, y dos
    scripts que corrieron a la vez se pisaron justamente ese temporal.)
    """
    ruta_parquet = str(ruta_parquet)
    kwargs.setdefault("index", False)
    temporal = f"{ruta_parquet}.tmp.{os.getpid()}"
    try:
        df.to_parquet(temporal, **kwargs)
        reemplazar_con_reintento(temporal, ruta_parquet)
    finally:
        if os.path.exists(temporal):      # sólo queda si algo falló
            try:
                os.remove(temporal)
            except OSError:
                pass
