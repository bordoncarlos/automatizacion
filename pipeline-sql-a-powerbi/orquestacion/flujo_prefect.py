"""
Alternativa a ejecutar_pipeline.ps1: la misma cadena orquestada con Prefect.

PARA QUÉ SIRVE
--------------
El Programador de tareas alcanza para una PC. Cuando el pipeline pasa a un
servidor, Prefect agrega lo que le falta: una interfaz web con el historial
de cada corrida, los logs de cada paso, reintentos declarativos y alertas.

Cada script corre como un proceso aparte con el mismo Python que lo corre a
mano: Prefect sólo orquesta, no necesita las dependencias de los scripts.

USO
    pip install prefect
    python orquestacion/flujo_prefect.py
    prefect server start          # interfaz en http://127.0.0.1:4200
"""
import subprocess
import sys
from pathlib import Path

from prefect import flow, get_run_logger, task

RAIZ = Path(__file__).resolve().parents[1]


@task(retries=2, retry_delay_seconds=60)
def correr(script: str, *argumentos: str) -> None:
    """Corre un paso del pipeline; si termina con error, Prefect lo reintenta."""
    log = get_run_logger()
    r = subprocess.run([sys.executable, str(RAIZ / script), *argumentos], cwd=RAIZ,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout:
        log.info(r.stdout)
    if r.returncode != 0:
        log.error(r.stderr)
        raise RuntimeError(f"{script} terminó con código {r.returncode}")


@flow(name="Pipeline SQL diario")
def pipeline():
    # EN SERIE (llamar a la tarea la corre y espera): 01 y 02 escriben el mismo parquet.
    error = None
    try:
        correr("01_extraer_ventas.py")
        # sólo si la extracción salió bien: si no, daría diferencias falsas
        correr("02_conciliar_ventas.py", "--corregir")
    except Exception as e:  # noqa: BLE001
        error = e
        get_run_logger().error(f"Ventas: {e}")
    # La foto de stock corre siempre: un día sin foto se pierde.
    correr("03_foto_stock.py")
    if error:
        raise error            # la corrida queda en rojo en la interfaz


if __name__ == "__main__":
    pipeline()
