# Recorrido por el código

Qué hace cada archivo, cada función y por qué está escrita así.

```
pipeline-erp-odoo-a-powerbi/
├── config.py                   rutas, credenciales (.env) y tablas de traducción
├── lib/
│   ├── odoo_cliente.py         API de Odoo: login, reintentos, paginado por id
│   ├── odoo_demo.py            Odoo simulado en memoria (misma interfaz)
│   ├── traduccion.py           facturas y NC de Odoo -> tabla de ventas de los informes
│   ├── escritura.py            parquet y Excel atómicos; archivo tomado = saltear
│   └── consola.py              salida a un log cuando la tarea corre sin ventana
├── actualizar_ventas.py        cada 1 min
├── actualizar_cobranza.py      cada 1 min
├── actualizar_stock.py         07:30 y 13:00
├── actualizar_clientes.py      cada 1 hora
├── programar_tareas.ps1        registra las 5 tareas
├── powerbi/
│   ├── actualizar_y_publicar.ps1   refresh + guardado + publicación, cada 10 min
│   └── guardar_credencial.ps1      la cuenta del servicio, cifrada, una vez
└── tests/                      14 tests contra el Odoo simulado
```

---

## `config.py` — lo que cambia de una empresa a otra

| Pieza | Para qué sirve |
|---|---|
| `ODOO_URL`, `ODOO_DB`, `ODOO_USUARIO`, `ODOO_PASSWORD` | Credenciales, leídas del `.env`. En la contraseña conviene una **API key** de Odoo: se revoca sin cambiar la clave del usuario. |
| `MODO_DEMO` | Sin `ODOO_URL`, todos los scripts usan el Odoo simulado. |
| `ERP_DESDE` | Desde qué día los datos salen de Odoo. Lo anterior es del sistema viejo y ningún script lo toca. |
| `ZONA_HORARIA` | Odoo guarda las horas en UTC; los informes las quieren en hora local. |
| `DIAS_RETROACTIVOS` | Cuántos días cerrados se re-extraen en cada corrida (cancelaciones tardías). |
| `VENDEDOR_POR_USUARIO` | Usuario de Odoo → código de vendedor. Se mantiene a mano. |
| `SUCURSAL_POR_DIARIO` | Diario de ventas → sucursal. |
| `CONDICION_POR_TERMINO` | Término de pago → contado / crédito. |
| `PRECIO_INCLUYE_IVA` | Si los precios de venta se cargan con IVA incluido (para sacar el precio neto). |
| `ESTADOS_PAGO_VALIDOS` | Qué estados de pago cuentan como cobrado (cambian entre versiones de Odoo). |
| `DEPOSITO_POR_UBICACION`, `ZONAS_CENTRAL` | Ubicación de Odoo → nombre de depósito de los informes. |

---

## `lib/odoo_cliente.py` — la única puerta a Odoo

**`Odoo.__init__`** autentica por `/xmlrpc/2/common` y guarda el `uid`. Todo lo
demás va por `/xmlrpc/2/object` con `execute_kw`.

**`_con_reintento(fn)`** reintenta ante errores transitorios (503, conexión
cortada, timeout) con espera creciente: 5, 10, 15 s. Incluye el login. Una
tarea que corre cada minuto no puede morir porque Odoo Online tuvo un mal
momento.

**`search_read(modelo, dominio, campos)`** pagina **por id**:

```python
parte = self.call(modelo, "search_read", [*dominio, ("id", ">", ultimo)],
                  fields=campos, order="id", limit=lote)
```

Con `offset`/`limit`, un registro que entra durante la lectura corre a todos
un lugar y uno se lee dos veces o se saltea. Con `id > último` eso no pasa.

**`read(modelo, ids, campos)`** lee por id en lotes de 2.000: una sola llamada
con decenas de miles de ids supera el tamaño máximo de respuesta.

**`conectar()`** devuelve el Odoo real o el simulado según el `.env`. Los
scripts nunca saben con cuál hablan.

**`m2o(valor)`** los campos many2one llegan como `[id, "nombre"]` o `False`;
esto los convierte en `(id, nombre)` o `(None, None)`.

---

## `lib/odoo_demo.py` — un Odoo para probar sin Odoo

Misma interfaz (`search_read`, `read`) y misma forma de los datos que la API
real. `_cumple()` evalúa dominios de Odoo (`=`, `!=`, `in`, `not in`, `<`,
`<=`, `>`, `>=`, `ilike`, y `in` sobre campos x2many). Genera, con semilla
fija, una ferretería mayorista inventada con los casos que rompen un pipeline:
facturas canceladas y en borrador, notas de crédito sin término de pago,
productos sin asiento de costo, facturas en dólares, un usuario sin código de
vendedor, un diario sin sucursal, pagos de contado registrados en caja, RUC
duplicados (uno en minúsculas) y una zona de depósito sin mapear.

---

## `lib/traduccion.py` — de Odoo al esquema de los informes

**`extraer_ventas(odoo, desde, hasta)`**, en ocho pasos:

1. **Cabeceras**: `account.move` de cliente (`out_invoice`, `out_refund`)
   **publicadas** en el rango. Las canceladas no se piden: en Odoo una
   cancelada desaparece, por eso quien llama re-extrae una ventana de días.
2. **Líneas** de producto y de costo (`display_type` `product` y `cogs`),
   pedidas por comprobante en lotes.
3. **Catálogos**: sólo los productos, clientes, impuestos y cuentas que
   aparecen en esas líneas, con `read` (no el catálogo entero).
4. **Originales de las NC** (`reversed_entry_id`): la NC hereda vendedor,
   sucursal y **condición** de la factura que revierte, porque el asistente de
   Odoo no copia el término de pago.
5. **Costo** por (comprobante, producto) desde las líneas `cogs` de la cuenta
   de costo de ventas (`expense_direct_cost`), repartido por cantidad si el
   producto aparece en varias líneas.
6. **Cabeceras traducidas**: vendedor por usuario, sucursal por diario,
   condición por término, cotización = `amount_untaxed_signed / amount_untaxed`
   (lo que Odoo convirtió de verdad, no una cotización del día), fecha =
   fecha de la factura + hora local de creación.
7. **Una fila por línea de producto**:
   - `monto_neto = -balance`: con el signo contable, la factura queda positiva
     y la NC negativa sin ningún `if`;
   - precio sin IVA = `price_unit / (1 + tasa)` (Odoo lo trae con IVA);
   - si Odoo no generó el asiento de costo, el costo de la ficha; un servicio
     (flete) va sin costo.
8. **Avisos**: usuarios sin vendedor, diarios sin sucursal, términos sin
   traducir. Nada se descarta en silencio.

**`normalizar_ruc(vat)`** sin espacios y en mayúsculas: Power BI compara las
claves sin distinguir mayúsculas y dos variantes del mismo RUC rompen la
relación con la tabla de clientes.

**`condicion_de_termino(nombre)`** sin término = contado; un término que no
está en la tabla sale como `CREDITO (<nombre>)` y se avisa.

**`unir(*partes)`** concatena ignorando las partes vacías (mezclarlas con
DataFrames vacíos cambia los tipos de las columnas).

---

## `lib/escritura.py` — lo que lee Power BI nunca queda a medias

- **`archivo_tomado(ruta)`** intenta abrir el archivo para escritura: si Excel
  o Power BI lo tienen, devuelve `True`. Los scripts lo preguntan **antes** de
  llamar a la API.
- **`escribir_parquet` / `escribir_excel`** escriben en un temporal con el PID
  y lo reemplazan de una vez (`os.replace`, atómico). Si el destino sigue
  tomado después de 5 intentos, borran el temporal y lanzan `ArchivoTomado`.
- **`escribir_excel`** pone encabezado en negrita, formato de número por
  columna y fija la primera fila. Los tipos importan: Power Query castea cada
  columna y una celda con el tipo equivocado hace fallar el refresh.
- **`CODIGO_SALTEADO = 3`**: el código con el que termina una corrida que no
  pudo escribir. Es un estado normal, no un error.

## `lib/consola.py`

**`preparar(nombre)`**: con `pythonw.exe` (sin ventana) `sys.stdout` es
`None` y el primer `print` hace caer el script sin rastro. Redirige toda la
salida a `logs/AAAA-MM/<nombre>_AAAAMMDD.log`, línea por línea.

---

## `actualizar_ventas.py` — cada minuto

1. Si el Excel está tomado → `[SKIP]`, código 3, sin gastar API.
2. `dias_cerrados()`: lee la caché; re-extrae los últimos 10 días cerrados y
   **reemplaza la ventana entera** (así una factura cancelada desaparece);
   guarda la caché. Sin caché (o con `--completo`), baja todo desde
   `ERP_DESDE`.
3. Extrae el día de hoy y lo guarda aparte (`ventas_hoy.parquet`) para la
   cobranza.
4. Escribe el Excel. Si se tomó durante la corrida (arrancó el refresh de
   Power BI mientras se consultaba la API), `[SKIP]` y código 3 en vez de un
   error con traceback.

## `actualizar_cobranza.py` — cada minuto

- **`cobranza_contado(ventas)`**: las ventas de contado agrupadas por
  comprobante. Las NC de contado restan (salió plata de la caja). No llama a
  la API.
- **`cobranza_credito(odoo, desde)`**: pagos de clientes (`account.payment`
  entrantes, en estado válido) con las facturas que cancelan
  (`reconciled_invoice_ids`). Cada pago se reparte entre sus facturas
  (`repartir`, suma exacta) y **se descarta la parte que cancela facturas de
  contado**: ya está en el contado. Vendedor y sucursal son los de la factura,
  no los de quien registró el pago.
- **`main()`**: reemplaza las filas desde `ERP_DESDE` y deja intactas las del
  sistema anterior. Idempotente.

## `actualizar_stock.py` — 07:30 y 13:00

1. Ubicaciones **internas** de Odoo (las de clientes, proveedores y ajustes
   son virtuales).
2. Existencias físicas (`stock.quant.quantity`) de esas ubicaciones.
3. **`deposito_de(ubicacion)`**: el mapa de `config`; las zonas internas del
   central suman a CENTRAL; una ubicación desconocida sale con su nombre y un
   `[AVISO]`.
4. Compara contra el archivo anterior depósito por depósito y escribe el Excel.

## `actualizar_clientes.py` — cada hora

1. **`ruc_de_los_hechos()`**: los RUC de ventas y cobranza. Un RUC vacío
   (consumidor final) no es un cliente.
2. Los que faltan en la base se buscan en Odoo: una llamada con todos y, para
   los que no aparecen tal cual, una búsqueda `ilike` (RUC cargado en
   minúsculas o con espacios).
3. **`elegir_uno_por_ruc()`**: el RUC no es único en Odoo; regla fija:
   compañía antes que contacto, el que más compra y, si empatan, el más viejo.
4. Se agregan con canal `A DEFINIR` (Odoo no tiene el canal comercial). La
   base vieja nunca pierde una fila. Si no falta nadie, no escribe.

---

## `programar_tareas.ps1`

Registra las 5 tareas con una función por tipo de disparador:

- `pythonw.exe` para que no aparezca una consola por minuto;
- la cobranza arranca 30 s después que las ventas (usa el archivo que la de
  ventas acaba de escribir);
- `MultipleInstances IgnoreNew` (nunca dos corridas de la misma tarea);
- un `ExecutionTimeLimit` por tarea como respaldo;
- `-Daily` con repetición copiada de un disparador `-Once`, el patrón que
  PowerShell 5.1 registra bien.

## `powerbi/`

Ver su [README](../powerbi/README.md): cómo se refresca, guarda y publica un
`.pbix` cada 10 minutos con Pro y sin gateway, y cómo se defiende de un
refresh colgado.

---

## `tests/test_pipeline_odoo.py`

| Test | Qué fija |
|---|---|
| `test_solo_entran_las_publicadas` | Canceladas y borradores no llegan al informe |
| `test_el_neto_es_el_balance_contable` | La suma del neto cierra contra la contabilidad |
| `test_la_nc_hereda_la_condicion_de_la_factura` | NC sin término → condición de la factura original |
| `test_nc_suma_negativo_y_factura_positivo` | Signos de montos y cantidades |
| `test_costo_de_la_ficha_si_odoo_no_genero_asiento` | Sin asiento → costo de la ficha; servicio → 0 |
| `test_factura_en_dolares_queda_en_guaranies` | La cotización sale de la propia factura |
| `test_nada_se_pierde_en_silencio` | Usuario sin código → 9000+id; diario sin sucursal → 99 |
| `test_ruc_normalizado` | Mayúsculas, espacios, RUC vacío |
| `test_el_credito_no_incluye_pagos_de_contado` | Sin doble conteo del contado |
| `test_borradores_no_cuentan` | Un pago en borrador no es cobranza |
| `test_reparto_exacto` | El reparto de un pago entre facturas suma exacto |
| `test_contado_con_nc_resta` | La devolución de contado resta |
| `test_un_contacto_por_ruc` | La regla de elección entre RUC duplicados |
| `test_excel_tomado_no_es_un_error` | Archivo tomado → `ArchivoTomado`, sin temporal colgado |
