"""
Cliente mínimo de la API de Odoo (XML-RPC).

PARA QUÉ SIRVE
--------------
Odoo expone todos sus modelos por XML-RPC: `account.move` (facturas),
`stock.quant` (existencias), `res.partner` (clientes)... Este módulo es la
única puerta de entrada: autentica una vez y ofrece `search_read` y `read`
con lo que una tarea que corre cada minuto necesita para no caerse.

- REINTENTOS: Odoo Online devuelve 503 o corta la conexión de vez en cuando
  (sobre todo con varias sesiones a la vez). Se reintenta con espera
  creciente, también el login.
- PAGINADO POR ID: `search_read` trae los registros en lotes ordenados por
  id y pide el siguiente lote con `id > último`. Con offset/limit, un
  registro que entra en el medio de la lectura corre todo un lugar y se lee
  uno dos veces (o se saltea uno).
- `read` en lotes: pedir 20.000 ids en una sola llamada revienta el tamaño
  máximo de la respuesta.

Sólo usa la biblioteca estándar (xmlrpc.client).
"""
import socket
import time
import xmlrpc.client
from http.client import HTTPException

import config

ERRORES_TRANSITORIOS = (xmlrpc.client.ProtocolError, HTTPException, ConnectionError,
                        socket.timeout, TimeoutError)


class Odoo:
    """Una instancia = una sesión autenticada."""

    def __init__(self, url=None, db=None, usuario=None, password=None):
        self.url = url or config.ODOO_URL
        self.db = db or config.ODOO_DB
        self.password = password or config.ODOO_PASSWORD
        usuario = usuario or config.ODOO_USUARIO
        comun = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = self._con_reintento(lambda: comun.authenticate(self.db, usuario, self.password, {}))
        if not self.uid:
            raise SystemExit(f"[ERROR] Odoo rechazó el login de {usuario} en {self.db}")
        self._obj = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    @staticmethod
    def _con_reintento(fn):
        for intento in range(1, config.API_REINTENTOS + 1):
            try:
                return fn()
            except ERRORES_TRANSITORIOS as e:
                if intento == config.API_REINTENTOS:
                    raise
                espera = config.API_ESPERA_S * intento
                print(f"   [AVISO] Odoo no respondió ({str(e)[:80]}); "
                      f"reintento {intento}/{config.API_REINTENTOS - 1} en {espera} s")
                time.sleep(espera)

    def call(self, modelo, metodo, *args, **kwargs):
        """Cualquier método de cualquier modelo (execute_kw)."""
        return self._con_reintento(lambda: self._obj.execute_kw(
            self.db, self.uid, self.password, modelo, metodo, list(args), kwargs))

    def search_read(self, modelo, dominio, campos, lote=config.LOTE):
        """Todos los registros que cumplen el dominio, paginando por id."""
        filas, ultimo = [], 0
        while True:
            parte = self.call(modelo, "search_read", [*dominio, ("id", ">", ultimo)],
                              fields=campos, order="id", limit=lote)
            filas.extend(parte)
            if len(parte) < lote:
                return filas
            ultimo = parte[-1]["id"]

    def read(self, modelo, ids, campos, lote=config.LOTE):
        """Lee registros por id, en lotes."""
        ids = sorted(set(ids))
        filas = []
        for i in range(0, len(ids), lote):
            filas.extend(self.call(modelo, "read", ids[i:i + lote], fields=campos))
        return filas


def conectar():
    """El Odoo real si hay credenciales en el .env; si no, el simulado."""
    if config.MODO_DEMO:
        from lib.odoo_demo import OdooDemo
        print("   [DEMO] sin ODOO_URL en .env: se usa un Odoo simulado con datos inventados")
        return OdooDemo()
    return Odoo()


def m2o(valor):
    """Un campo many2one de Odoo llega como [id, "nombre"] o False -> (id, nombre)."""
    return (valor[0], valor[1]) if valor else (None, None)
