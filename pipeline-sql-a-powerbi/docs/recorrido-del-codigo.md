# Recorrido por el código

```
pipeline-sql-a-powerbi/
├── config.py                    rutas, conexión (.env) y qué datos lleva el reporte
├── actualizar_reporte.py        el script: extraer -> transformar -> validar -> escribir
├── lib/
│   ├── extraccion.py            columnas, bloques, consulta parametrizada, reintentos
│   ├── transformacion.py        tipos, columnas calculadas y validación
│   └── parquet_seguro.py        escritura atómica + lock entre procesos
├── generar_datos_demo.py        base SQLite de ejemplo
├── orquestacion/
│   ├── ejecutar_con_reintentos.ps1   corre el script con reintentos, log y estado
│   └── programar_tarea.ps1           lo registra en el Programador de tareas
└── tests/                       10 tests
```

---

## `config.py`

| Pieza | Para qué sirve |
|---|---|
| `_cargar_env()` | Lee `CLAVE=valor` de un `.env` sin dependencias. Las variables ya definidas en el entorno mandan. |
| `DB_URL` | La conexión, en formato SQLAlchemy. Cambiar de motor es cambiar esta línea en el `.env`. Sin `.env`, la base de demostración. |
| `TABLA_ORIGEN`, `COLUMNAS`, `COLUMNA_FECHA` | Qué se consulta. Para otro reporte se cambia esto, no el código. |
| `CLAVE` | Las columnas que identifican una fila: la validación verifica que no se repitan. |
| `DIAS_RETROACTIVOS` | Cuántos días hacia atrás se re-extraen en cada corrida. |
| `INCLUIR_DIA_EN_CURSO` | Si el reporte llega hasta ahora o cierra en ayer. |
| `DIAS_BLOQUE_MAX`, `MESES_POR_BLOQUE` | Cuándo y cómo se parte una extracción larga. |

## `actualizar_reporte.py`

**`calcular_ventana(reporte, desde_arg, hoy)`** devuelve `(desde, hasta)`:
desde la última fecha del reporte menos `DIAS_RETROACTIVOS` (o `PRIMERA_FECHA`
si el reporte no existe, o `--desde` para recargar un rango) hasta hoy 00:00.

**`unir(reporte, nuevos, desde)`** se queda con lo anterior a la ventana y le
suma lo recién extraído. Las filas de la ventana se descartan aunque la base
ya no las traiga: así un anulado desaparece del reporte.

**`main()`**: lock → ventana → verificar columnas → extraer por bloques →
transformar → unir → **validar** (si falla, termina con código 1 sin
escribir) → resumen de los últimos días en el log → escritura atómica.

## `lib/extraccion.py`

- **`verificar_columnas`** pregunta al catálogo de la base qué columnas tiene
  la tabla y aborta si falta alguna, antes de esperar toda la extracción.
- **`bloques_fecha`** parte un rango largo en trimestres; un rango corto es un
  solo bloque.
- **`armar_consulta`** refleja la tabla y arma
  `SELECT columnas WHERE fecha >= :desde AND fecha < :hasta` con SQLAlchemy
  Core: las fechas viajan como parámetros.
- **`extraer_bloque`** ejecuta con reintentos y llama a `engine.dispose()`
  entre intentos, para no reusar una conexión que cortó un firewall.

## `lib/transformacion.py`

- **`normalizar_tipos`**: códigos como texto sin espacios y en mayúsculas,
  números como números, fechas como fechas. Power BI compara claves sin
  distinguir mayúsculas: `abc` y `ABC` romperían una relación.
- **`agregar_columnas`**: columnas que el reporte usa (período `AAAA-MM`,
  margen), calculadas una sola vez.
- **`validar`**: devuelve la lista de problemas (claves vacías o repetidas,
  fechas fuera de rango, importes vacíos). No corrige: avisa y frena.

## `lib/parquet_seguro.py`

- **`adquirir_lock_escritura`**: lock entre procesos sobre un archivo `.lock`
  (`msvcrt` en Windows, `fcntl` en Linux). Lo suelta el sistema operativo
  cuando el proceso termina, aunque termine con error.
- **`escribir_parquet_seguro`**: escribe en `archivo.tmp.<PID>` y lo reemplaza
  con `os.replace`, que es atómico.
- **`reemplazar_con_reintento`**: si Power BI tiene el archivo abierto, el
  reemplazo falla con `PermissionError`; reintenta 5 veces cada 3 s.

## `orquestacion/`

- **`ejecutar_con_reintentos.ps1`**: corre el script, guarda la salida en
  `logs/AAAA-MM/reporte_AAAAMMDD.log` y una línea de estado en
  `logs/estado.txt`; si falla, reintenta (1 min antes del segundo intento,
  5 min entre los demás) con tope de intentos y de minutos. Fija la
  codificación a UTF-8 para que los acentos lleguen bien al log.
- **`programar_tarea.ps1`**: registra la tarea diaria (o repetida cada N
  minutos) con `StartWhenAvailable` y `MultipleInstances IgnoreNew`.

## `tests/`

| Test | Qué fija |
|---|---|
| `TestBloquesFecha` | Rango corto = un bloque; rango largo sin huecos ni solapes |
| `TestTransformacion` | Códigos limpios, columnas calculadas; la validación detecta claves repetidas y fechas futuras |
| `TestActualizacionIncremental` | La ventana se reemplaza entera (un anulado desaparece); el día en curso queda afuera |
| `TestEscrituraSegura` | Sin temporales; reintenta si el archivo está tomado; si sigue tomado, el original queda intacto |
