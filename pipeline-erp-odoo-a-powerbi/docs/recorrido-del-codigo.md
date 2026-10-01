# Recorrido por el código

```
pipeline-erp-odoo-a-powerbi/
├── config.py                credenciales (.env) y parámetros de la extracción
├── actualizar_reporte.py    el script: caché + ventana + hoy -> Excel
├── lib/
│   ├── odoo_cliente.py      API de Odoo: login, reintentos, paginado por id
│   ├── odoo_demo.py         Odoo simulado en memoria (misma interfaz)
│   ├── transformacion.py    facturas y NC de Odoo -> una fila por línea
│   ├── escritura.py         parquet y Excel atómicos; archivo tomado = saltear
│   └── consola.py           salida a un log cuando la tarea corre sin ventana
├── programar_tarea.ps1      registra la tarea (cada minuto)
└── tests/                   9 tests contra el Odoo simulado
```

---

## `config.py`

| Pieza | Para qué sirve |
|---|---|
| `ODOO_URL`, `ODOO_DB`, `ODOO_USUARIO`, `ODOO_PASSWORD` | Credenciales, del `.env`. En la contraseña conviene una **API key**: se revoca sin cambiar la clave del usuario. |
| `MODO_DEMO` | Sin `ODOO_URL`, el script usa el Odoo simulado. |
| `DESDE` | Desde qué fecha se extrae. |
| `ZONA_HORARIA` | Odoo guarda las horas en UTC; el reporte las quiere en hora local. |
| `DIAS_RETROACTIVOS` | Cuántos días cerrados se re-extraen en cada corrida. |
| `PRECIO_INCLUYE_IMPUESTO` | Si los precios se cargan con el impuesto incluido. |

## `actualizar_reporte.py`

1. Si el Excel está abierto → `[SKIP]`, código 3, sin llamar a la API.
2. **`dias_cerrados(odoo, hoy)`**: lee la caché, re-extrae los últimos 10 días
   cerrados y **reemplaza la ventana entera** (así una factura cancelada
   desaparece), y guarda la caché. Sin caché, o con `--completo`, baja todo
   desde `DESDE`.
3. Extrae el día de hoy.
4. Escribe el Excel. Si se abrió durante la corrida (arrancó el refresh de
   Power BI mientras se consultaba la API), `[SKIP]` y código 3 en vez de un
   error con traceback.

## `lib/odoo_cliente.py`

- **`Odoo.__init__`** autentica por `/xmlrpc/2/common`; todo lo demás va por
  `/xmlrpc/2/object` con `execute_kw`.
- **`_con_reintento`** reintenta ante errores transitorios (503, conexión
  cortada, timeout) con espera de 5, 10 y 15 s, login incluido.
- **`search_read`** pagina por id (`id > último`, orden por id): estable
  aunque entren registros durante la lectura.
- **`read`** lee por id en lotes de 2.000, para no superar el tamaño máximo de
  respuesta.
- **`conectar()`** devuelve el Odoo real o el simulado según el `.env`.
- **`m2o`** convierte un many2one (`[id, "nombre"]` o `False`) en `(id, nombre)`.

## `lib/transformacion.py`

**`extraer(odoo, desde, hasta)`**, en seis pasos:

1. **Cabeceras**: facturas y notas de crédito de cliente **publicadas** en el
   rango. Las canceladas no se piden.
2. **Líneas** de producto y de costo (`display_type` `product` y `cogs`),
   pedidas por comprobante en lotes.
3. **Catálogos**: sólo los productos, clientes, impuestos y cuentas que
   aparecen en esas líneas, no el catálogo entero.
4. **Costo** por (comprobante, producto) desde la línea de costo de ventas
   (`expense_direct_cost`), repartido por cantidad si el producto se repite.
5. **Cabeceras**: fecha (de la factura) + hora local de creación, cliente,
   vendedor, diario, término de pago, moneda y cotización
   (`amount_untaxed_signed / amount_untaxed`: la que Odoo usó en esa factura).
6. **Una fila por línea de producto**: `importe_neto = -balance` (factura
   positiva, nota de crédito negativa, sin ningún `if`), precio sin impuesto,
   cantidad con signo, importe total y costo convertidos a la moneda de la
   empresa.

**`normalizar_texto`**: sin espacios y en mayúsculas. Power BI compara las
claves sin distinguir mayúsculas y dos variantes de la misma clave rompen una
relación.

**`unir`**: concatena ignorando las partes vacías (mezclarlas cambia los tipos
de las columnas). **`resumen_por_dia`**: la tabla del log.

## `lib/odoo_demo.py`

Misma interfaz que el cliente real (`search_read`, `read`) y misma forma de
los datos. `_cumple()` evalúa dominios de Odoo (`=`, `!=`, `in`, `not in`,
`<`, `<=`, `>`, `>=`). Genera con semilla fija facturas publicadas,
canceladas y en borrador, notas de crédito, facturas en dólares, tres tasas de
impuesto, un servicio sin costo y un cliente con la clave en minúsculas.

## `lib/escritura.py`

- **`archivo_tomado`**: intenta abrir el archivo para escritura; si otro
  proceso lo tiene, `True`.
- **`escribir_parquet` / `escribir_excel`**: temporal con el PID y
  `os.replace` (atómico). Si el destino sigue tomado tras 5 intentos, borran el
  temporal y lanzan `ArchivoTomado`.
- **`escribir_excel`**: encabezado en negrita, formato de número por columna y
  primera fila fija. Los tipos importan: Power Query castea cada columna.

## `lib/consola.py`

**`preparar`**: con `pythonw.exe` `sys.stdout` es `None` y el primer `print`
haría caer el script. Redirige la salida a `logs/AAAA-MM/<nombre>_AAAAMMDD.log`.

## `programar_tarea.ps1`

Registra la tarea con `pythonw.exe`, repetición cada N minutos (copiada de un
disparador `-Once`, el patrón que PowerShell 5.1 registra bien),
`MultipleInstances IgnoreNew` y un tope de 5 minutos como respaldo.

## `tests/test_reporte.py`

| Test | Qué fija |
|---|---|
| `test_solo_entran_las_publicadas` | Canceladas y borradores no llegan al reporte |
| `test_el_importe_cierra_contra_la_contabilidad` | La suma del importe es la de los asientos |
| `test_nota_de_credito_negativa_factura_positiva` | Signos de importes y cantidades |
| `test_dolares_convertidos_con_la_cotizacion_de_la_factura` | La cotización sale de la propia factura |
| `test_costo_desde_el_asiento_y_servicio_sin_costo` | El costo viene de las líneas de costo de ventas |
| `test_precio_sin_impuesto` | Se descuenta el impuesto incluido en el precio |
| `test_clave_de_cliente_normalizada` | Mayúsculas y espacios |
| `test_una_cancelacion_tardia_desaparece_del_reporte` | La ventana re-extraída saca una factura cancelada días después |
| `test_excel_tomado_no_es_un_error` | Archivo tomado → `ArchivoTomado`, sin temporal colgado |
