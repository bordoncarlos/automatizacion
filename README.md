# Automatización de datos: del ERP a Power BI

Dos pipelines que reemplazan el trabajo manual de exportar reportes del ERP y
pegarlos en Excel: extraen los datos, les aplican las reglas de negocio, los
validan contra los reportes oficiales y mantienen los informes de Power BI
actualizados solos, desde temprano a la mañana hasta el cierre del día.

> Son versiones genéricas de pipelines que corren en producción en una empresa
> comercial. El código se reescribió sin ningún dato, regla ni nombre de esa
> empresa. Cada proyecto trae datos de demostración (una ferretería mayorista
> inventada) para probarlo en un par de minutos, sin conectarse a nada.

---

## Los proyectos

### 1. [Pipeline SQL → Parquet → Power BI](pipeline-sql-a-powerbi/)

La cadena diaria que alimenta los informes desde la **base de datos** del ERP.

- **Extracción incremental** que no duplica ni pierde: el histórico cierra
  ayer, cada corrida re-extrae una ventana de días para captar anulaciones
  tardías y los rangos largos se parten en bloques con reintentos.
- **Reglas de negocio con una sola fuente**: sucursal dominante por vendedor y
  mes, carteras reasignadas, prorrateo con suma exacta, tasa de IVA enganchada.
- **Conciliación contra el reporte oficial**, comprobante por comprobante, con
  corrección automática sólo por debajo de un tope.
- **Foto diaria del stock**, porque la tabla de origen no guarda historia.
- Orquestación **en serie con reintentos** (PowerShell + Programador de tareas,
  o Prefect).

[README](pipeline-sql-a-powerbi/) · [Recorrido por el código](pipeline-sql-a-powerbi/docs/recorrido-del-codigo.md)

### 2. [Pipeline ERP (Odoo) → Power BI, al minuto](pipeline-erp-odoo-a-powerbi/)

Cuando la empresa cambió de ERP a Odoo, los informes siguieron funcionando el
mismo día.

- **Integración por API** (XML-RPC de Odoo) con paginado estable, reintentos y
  caché de días cerrados para no gastar llamadas.
- **Capa de traducción** de las facturas de Odoo al esquema que ya usaban los
  informes: signos contables, precios con IVA incluido, costo desde los
  asientos de costo de ventas, notas de crédito que heredan la condición de la
  factura.
- **Ventas y cobranza cada minuto**, stock y clientes durante el día.
- **Power BI publicado cada 10 minutos con licencia Pro y sin gateway**:
  refresh por XMLA al motor de Desktop, guardado por UI Automation y
  publicación con PowerShell, con defensa contra refresh colgados.

[README](pipeline-erp-odoo-a-powerbi/) · [Recorrido por el código](pipeline-erp-odoo-a-powerbi/docs/recorrido-del-codigo.md) · [Publicación sin gateway](pipeline-erp-odoo-a-powerbi/powerbi/)

---

## Probarlo

Python 3.10 o superior.

```bash
# Proyecto 1: crea una base SQLite de demostración y corre la cadena
cd pipeline-sql-a-powerbi
pip install -r requirements.txt
python 00_generar_datos_demo.py
python 01_extraer_ventas.py
python 02_conciliar_ventas.py
python 03_foto_stock.py
python -m unittest discover -s tests

# Proyecto 2: sin .env, todo corre contra un Odoo simulado
cd ../pipeline-erp-odoo-a-powerbi
pip install -r requirements.txt
python actualizar_ventas.py
python actualizar_cobranza.py
python -m unittest discover -s tests
```

## Lo que tienen en común

- **El histórico nunca se cuenta dos veces.** Lo cerrado y lo del día llegan
  por caminos separados y cada script reemplaza exactamente las filas que le
  corresponden: correrlo dos veces da el mismo resultado.
- **El reporte oficial manda.** Lo que muestra Power BI tiene que coincidir con
  lo que muestra el ERP, o nadie confía en el informe.
- **Nada se pierde en silencio.** Un vendedor, una sucursal o un depósito que
  no está mapeado sale con un código visible y un aviso en el log.
- **Escritura atómica.** Power BI nunca lee un archivo a medio escribir, y si
  lo tiene abierto, el script espera o lo deja para la próxima corrida.
- **Credenciales fuera del código**, en un `.env` que no se sube.
- **Tests** que fijan los casos que en producción rompieron un informe.

## Tecnologías

Python (pandas, SQLAlchemy, pyarrow, openpyxl, `xmlrpc`) · SQL · Odoo ·
Parquet · PowerShell · Programador de tareas de Windows · Prefect · Power BI
(XMLA, TOM, Service) · `unittest`

---

**Carlos Bordón** — Analista de BI
