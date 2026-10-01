# Recorrido por el código

Qué hace cada archivo, cada función y por qué está escrita así. El orden es el
de la corrida: configuración, librerías, los tres pasos y la orquestación.

```
pipeline-sql-a-powerbi/
├── config.py                  rutas, credenciales (.env) y reglas de negocio
├── lib/
│   ├── extraccion.py          consulta SQL robusta: columnas, bloques, reintentos
│   ├── reglas_negocio.py      vendedores, sucursal dominante, prorrateo, IVA, correcciones
│   └── parquet_seguro.py      escritura atómica + lock entre procesos
├── 00_generar_datos_demo.py   base SQLite de una empresa inventada
├── 01_extraer_ventas.py       base SQL -> ventas_historicas.parquet
├── 02_conciliar_ventas.py     parquet vs. reporte oficial
├── 03_foto_stock.py           stock de hoy -> historial_stock.parquet
├── orquestacion/
│   ├── ejecutar_pipeline.ps1  la cadena diaria en serie, con reintentos
│   ├── programar_tarea.ps1    la registra en el Programador de tareas
│   └── flujo_prefect.py       la misma cadena con Prefect (opcional)
└── tests/                     15 tests con unittest
```

---

## `config.py` — una sola fuente para todo

| Pieza | Para qué sirve |
|---|---|
| `BASE_DIR`, `DATOS_DIR`, `PARQUET_*` | Todas las rutas relativas a la carpeta del proyecto: se puede mover o llevar a un servidor sin tocar código. |
| `_cargar_env()` | Lee `CLAVE=valor` de un `.env` sin dependencias externas. Usa `setdefault`, así una variable ya definida en el entorno (un servidor, un CI) manda sobre el archivo. |
| `DB_URL` | La conexión, en formato SQLAlchemy. Cambiar de SQLite a PostgreSQL o SQL Server es cambiar esta línea en el `.env`. Sin `.env` usa la base de demostración. |
| `DIAS_RETROACTIVOS` | Cuántos días hacia atrás se re-extraen en cada corrida (anulaciones tardías). |
| `DIAS_BLOQUE_MAX`, `MESES_POR_BLOQUE` | Cuándo y cómo se parte una extracción larga. |
| `COLUMNAS_VENTAS` | Las columnas que se piden; se validan contra la base antes de consultar. |
| `SUCURSALES_TEMPORALES`, `VENDEDOR_MAPPING`, `VENDEDORES_SIN_DOMINANCIA` | Las reglas comerciales. Están acá y no en los scripts para que todos las apliquen igual. |
| `IVA_TASAS` | Las tasas válidas (exento, 5 %, 10 %), para enganchar la tasa de cada línea. |
| `TOPE_CORRECCION_AUTOMATICA` | Por encima de este porcentaje, la conciliación no corrige sola. |

Al principio, `reconfigure(errors="replace")` evita que un carácter que la
consola de Windows (cp1252) no conoce tire abajo un script que corre sin
supervisión.

---

## `lib/extraccion.py` — la consulta que no se cae

**`verificar_columnas(engine, tabla, columnas)`**
Pregunta al catálogo de la base qué columnas tiene la tabla (o vista) y aborta
si falta alguna. Cuesta milisegundos; sin esto, un error de tipeo se descubre
después de esperar toda la extracción.

**`bloques_fecha(desde, hasta, dias_max, meses_por_bloque)`**
Si el rango es corto devuelve un solo bloque; si no, lo parte en trimestres.
Todos los bloques terminan como mucho en `hasta` (hoy 00:00), así que el día en
curso queda afuera por construcción. El tamaño es un compromiso: si la tabla de
origen es una vista pesada, cada consulta paga un costo fijo alto, y partir de
más lo paga muchas veces.

**`armar_consulta(...)`**
Arma el `SELECT` con SQLAlchemy Core, reflejando la tabla:

```python
t = Table(tabla, MetaData(), schema=esquema, autoload_with=engine)
select(*[t.c[c] for c in columnas]).where(and_(
    t.c.fecha_emision >= desde, t.c.fecha_emision < hasta,
    t.c.tipo_operacion.not_in(list(excluir_operaciones)),
))
```

Las fechas y la lista de exclusiones viajan como **parámetros**, no pegadas en
el texto: el mismo código funciona en cualquier motor, los nombres con
espacios se citan solos y no hay inyección SQL posible.

**`extraer_bloque(engine, consulta, reintentos, espera_s)`**
Ejecuta con reintentos. Entre intento e intento llama a `engine.dispose()`:
cuando un firewall corta un socket, la conexión muerta queda guardada en el
pool y el reintento la reusaría.

---

## `lib/reglas_negocio.py` — de "cómo opera" a "cómo se analiza"

Principio: **el dato crudo nunca se pisa**. La versión para el reporte va en
otra columna, así una regla nueva se recalcula sobre todo el histórico sin
volver a la base.

**`normalizar_tipos(df)`**
Todo código (comprobante, cliente, vendedor, artículo) como texto sin
espacios. Un código que un día llega como `1001` y otro como `"1001 "` rompe
las relaciones del modelo de Power BI. Guarda `cod_vendedor_original` la
primera vez.

**`aplicar_mapeo_vendedores(df)`**
`cod_vendedor` = el vendedor que hoy tiene esa cartera, calculado siempre desde
`cod_vendedor_original`.

**`mask_sin_dominancia(vendedores, meses)`**
Marca a los vendedores exceptuados de la regla siguiente. La excepción puede
regir **desde un mes**: los meses ya reportados no cambian.

**`sucursal_dominante(df)`**
Regla comercial: un vendedor pertenece a una sucursal por mes, la de mayor
venta. Si facturó en dos, toda su venta del mes va a una; si no, aparecería
partido y las metas por sucursal no cerrarían. Casos borde resueltos:

1. La dominante es una sucursal **temporal** (feria, cuentas corrientes) →
   se toma la mejor sucursal real del mismo mes.
2. Ese mes sólo vendió en temporales → la última sucursal real de meses
   anteriores (o la primera de los siguientes).
3. Vendedor exceptuado → conserva la sucursal de cada factura.

El resultado va a `cod_sucursal_reporte`; `cod_sucursal` queda intacta.

**`prorratear(df, montos, solo_facturas=True)`**
Reparte un monto de cabecera (un anticipo, un total corregido) entre las
líneas del comprobante con **suma exacta**:

- base de reparto en cascada: neto positivo → neto negativo (nota de crédito)
  → total → |costo| → parejo. Cubre los comprobantes cuyo neto suma cero, que
  con `neto / SUM(neto)` darían división por cero;
- con `solo_facturas`, una nota de crédito bajo el mismo número no recibe
  parte (no consumió el anticipo);
- el residuo del redondeo se suma a la línea de mayor peso.

**`tasa_iva(df)`**
Tasa de cada línea = total / neto, **enganchada** a 1,00 / 1,05 / 1,10 si está
a menos de 0,01. La razón cruda arrastra el redondeo de la conversión de
moneda (1,0998) y, peor, en una línea ya corregida puede dar 1,1017 y pasar
por buena. Así una línea exenta no se infla al 10 %.

**`llevar_a_monto(df, objetivos)`**
Lleva cada comprobante a su neto oficial: reparte con `prorratear` y recalcula
el total con IVA de cada línea con su propia tasa. Corregir sólo el neto dejaba
el total viejo, y un anulado seguía sumando en el informe. Es idempotente.

**`aplicar_correcciones_guardadas(df, ruta)`**
Reaplica la bitácora de la conciliación. Sin esto, la extracción del día
siguiente re-trae de la base el monto viejo y deshace la corrección.

**`postprocesar(df)`**
Las reglas en orden. La usan la extracción y la conciliación, para que las dos
dejen el parquet exactamente igual.

---

## `lib/parquet_seguro.py` — que el archivo de Power BI nunca se rompa

**`adquirir_lock_escritura(ruta)`**
Lock entre procesos sobre un archivo `.lock` (`msvcrt` en Windows, `fcntl` en
Linux). Si otro script está escribiendo, aborta con un mensaje claro. Lo
libera el sistema operativo al terminar el proceso, aunque termine con error:
nunca queda un lock huérfano.

**`escribir_parquet_seguro(df, ruta)`**
Escribe en `ruta.tmp.<PID>` y lo reemplaza con `os.replace`, que es atómico:
el archivo es el viejo entero o el nuevo entero. El PID en el nombre evita que
dos procesos compartan temporal (con un `.tmp` fijo, dos scripts que corrieron
a la vez se pisaron el temporal y el parquet quedó corrupto).

**`reemplazar_con_reintento(temporal, destino)`**
Si Power BI está refrescando, tiene el archivo tomado y `os.replace` falla con
`PermissionError`. Reintenta 5 veces cada 3 s: los choques son de borde y se
resuelven solos.

---

## `00_generar_datos_demo.py` — una empresa inventada

Crea `datos/erp_demo.db` (SQLite) con semilla fija:

- **ventas**: ~27.000 líneas desde 2025 **hasta hoy inclusive** (el día en
  curso está a propósito, para ver que el pipeline lo deja afuera); 3 % de
  notas de crédito; algunos movimientos internos que la consulta debe
  filtrar; casos para las reglas: un vendedor que se fue y su sucesor, otro que
  vende en todas las sucursales, uno que desde agosto atiende dos, y una feria
  en julio.
- **stock_actual**: existencias por depósito, incluidos depósitos no vendibles
  (averiados, muestras).
- **reporte_oficial_ventas.csv**: el total por comprobante que exportaría el
  ERP, con 5 diferencias sembradas en el último mes: 3 descuentos cargados
  después, 1 comprobante que la base no tiene y 1 anulado.

---

## `01_extraer_ventas.py` — extracción incremental

1. `adquirir_lock_escritura` — nadie más escribe el parquet mientras tanto.
2. `calcular_ventana` — desde (última fecha del parquet − `DIAS_RETROACTIVOS`)
   hasta hoy 00:00. Con `--desde` se re-extrae un rango a mano (backfill).
3. Si `desde >= hasta`, ya está todo: termina sin tocar nada.
4. `verificar_columnas` → `bloques_fecha` → `armar_consulta` + `extraer_bloque`
   por bloque.
5. **Unir**: el histórico *anterior* a la ventana + lo recién extraído. Las
   filas de la ventana se descartan del histórico aunque la base ya no las
   traiga: así un anulado desaparece.
6. Defensa extra: se descarta cualquier fila de hoy.
7. `postprocesar` + `aplicar_correcciones_guardadas`.
8. `resumen_por_dia` deja en el log los últimos días con comprobantes, líneas
   y neto: si un día viene raro, se ve sin abrir Power BI.
9. `escribir_parquet_seguro`.

## `02_conciliar_ventas.py` — el reporte oficial manda

1. `comparar` suma el parquet y el reporte por comprobante en el rango (por
   defecto, del 1 del mes a ayer), los cruza con un *outer join* y clasifica:
   `OK`, `DIFERENCIA`, `SOLO EN PARQUET`, `SOLO EN REPORTE`. Tolera 5 Gs de
   redondeo.
2. Imprime **cada** comprobante con diferencia (el total solo no sirve para ir
   a buscar el problema) y guarda el detalle en un Excel.
3. Con `--corregir`, si la diferencia total no supera el tope del 2 %,
   `aplicar_correcciones`:
   - `DIFERENCIA` → al monto oficial;
   - `SOLO EN PARQUET` (anulado) → a 0, sin borrarlo: las cantidades siguen
     sirviendo para el análisis de demanda;
   - `SOLO EN REPORTE` → no se puede inventar el detalle por artículo: se
     avisa para re-extraer ese día.
4. Cada corrección queda en `correcciones.csv` con el **monto previo**: una vez
   corregido, es la única evidencia de que hubo diferencia.

## `03_foto_stock.py` — historia que la fuente no guarda

1. Lee el stock actual por depósito y artículo.
2. `tipo_de_deposito` clasifica por nombre: `NO VENDIBLE` (faltantes,
   averiados, muestras), `CENTRAL` (match **exacto**: "AVERIADOS CENTRAL"
   también contiene la palabra), `SUCURSAL` u `OTRO`.
3. Agrega la foto con la fecha de hoy, reemplazando una foto de hoy si ya
   existía: correrlo dos veces no duplica.

---

## `orquestacion/ejecutar_pipeline.ps1` — la cadena diaria

- `Correr` ejecuta un script, guarda su salida en el log y reintenta hasta
  lograrlo, con tope de intentos y de minutos (1 min antes del 2.º intento,
  5 min entre los demás).
- Si la extracción falla, **salta la conciliación** (daría diferencias falsas
  y las "corregiría") pero **corre la foto de stock** igual.
- `Estado` reescribe una sola línea en `logs/pipeline_estado.txt`: en qué paso
  va o cómo terminó, para mirarlo de un vistazo.
- Guarda anti-duplicado: si el log de hoy ya dice `=== fin`, no vuelve a correr.
- Borra los logs de más de 60 días.
- `[Console]::OutputEncoding = UTF8`: sin eso, PowerShell lee la salida de
  Python con otra codificación y los acentos llegan rotos al log.
- Código de salida = cantidad de pasos que fallaron.

## `orquestacion/programar_tarea.ps1`

Registra la tarea con dos disparadores (07:30 y al iniciar sesión, por si la
PC estaba apagada), `StartWhenAvailable`, `MultipleInstances IgnoreNew` (nunca
dos cadenas a la vez) y un tope de 6 horas como respaldo.

## `orquestacion/flujo_prefect.py`

La misma cadena en Prefect, para cuando el pipeline pasa a un servidor: suma
una interfaz web con el historial de corridas, los logs de cada paso y
reintentos declarativos. Mantiene las mismas reglas: en serie, conciliación
sólo si la extracción salió bien, foto de stock siempre.

---

## `tests/`

| Test | Qué fija |
|---|---|
| `TestProrratear` | Suma exacta con redondeo; neto cero → reparte por costo; la nota de crédito no recibe anticipo; corrección de una nota de crédito con su signo |
| `TestTasaIva` | Enganche a 1,10 / 1,00 / 1,05; `llevar_a_monto` corrige el total y es idempotente |
| `TestSucursalDominante` | La venta va a la sucursal mayor sin pisar la real; la feria nunca es dominante; mes sólo en feria → mes anterior; excepción desde un mes |
| `TestBloquesFecha` | Rango corto = un bloque; rango largo sin huecos ni solapes y terminando hoy |
| `TestEscrituraSegura` | No deja temporales; reintenta si el archivo está tomado; si sigue tomado, el original queda intacto |

```bash
python -m unittest discover -s tests -v
```
