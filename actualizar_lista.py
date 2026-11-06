#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mantenimiento automatico de ``lista.m3u``: comprueba de verdad cada senal.

``generar_epg.py`` construye la lista (nombres, ``tvg-id``, grupos y guia), pero
no sabe si los enlaces siguen vivos: los proveedores cambian de host, caducan
los tokens de sesion y los espejos se caen. Este script cierra ese circulo:

1. **Origen**: descarga la lista M3U (``--url`` o ``M3U_URL``) o lee la lista ya
   generada en disco (``--entrada``, ``lista.m3u`` por defecto).
2. **Comprobacion real de cada ``.m3u8``**: peticion ``GET`` con ``Range`` y
   ``timeout`` corto (los servidores HLS suelen ignorar ``HEAD``), en paralelo
   con un pool de hilos. Se distingue entre:

   * ``ok``: responde 200/206 y devuelve un manifiesto ``#EXTM3U`` (o el tipo
     ``mpegurl``). Los enlaces que no son HLS se dan por buenos con 200/206.
   * ``caido``: 404, 410, 400, dominio inexistente o un 200 con una pagina de
     error (HTML) en lugar del manifiesto.
   * ``protegido``: 401/403 incluso despues de probar todas las cabeceras.
   * ``dudoso``: ``timeout``, 5xx, 429 o error de red (puede ser un fallo
     puntual o un bloqueo geografico del ejecutor, no una senal muerta).

3. **Cabeceras**: si el enlace solo responde con un ``User-Agent``/``Referer``
   concretos, se prueban en cascada las cabeceras de la propia lista, las de
   ``cabeceras_streams.json`` (por host), un perfil de navegador con
   ``Referer``/``Origin`` y un perfil de VLC. Cuando la senal solo funciona con
   un perfil, se publican sus cabeceras como ``#EXTVLCOPT`` en la entrada, de
   modo que el reproductor tambien las use.
4. **Token de sesion**: si el host define ``renovar`` en
   ``cabeceras_streams.json`` y la URL lleva ``token``/``st``, se pide un token
   nuevo al endpoint y se reescribe la URL antes de darla por perdida.
5. **Politica**: por defecto (``normal``) se descartan ``caido`` y
   ``protegido``; ``conservadora`` solo descarta ``caido`` y ``estricta``
   descarta tambien ``dudoso``. Asi una caida puntual de red no borra canales.
6. **Salida**: la lista limpia (sin senales muertas y sin duplicados exactos),
   con las entradas ordenadas por la jerarquia de grupos de
   :data:`generar_epg.ORDEN_GRUPOS_M3U` y la cabecera ``x-tvg-url``/``url-tvg``
   intacta. El formato de cada entrada (``#EXTINF`` y ``#EXTVLCOPT``) se
   conserva tal cual, asi que el ``tvg-id`` sigue sincronizado con ``epg.xml``.

Uso::

    python actualizar_lista.py                        # depura la lista.m3u actual
    python actualizar_lista.py -u "$env:M3U_URL"      # desde la lista del proveedor
    python actualizar_lista.py --sin-verificar        # solo ordena y limpia
    python actualizar_lista.py --informe informe.json # resumen en JSON
    python actualizar_lista.py --politica estricta    # descarta tambien timeouts
    python actualizar_lista.py --limite-pruebas 50    # probar el script rapido

Variables de entorno: ``M3U_URL`` (lista de origen), ``M3U_CABECERAS`` (JSON de
cabeceras por host) y ``M3U_SALIDA`` (ruta de la lista publicada).

Codigos de salida: ``0`` todo correcto; ``1`` no se pudo leer el origen; ``2``
la comprobacion descarto demasiadas entradas (o ninguna) y **no se escribio**
la lista, para no publicar una lista vacia por un fallo de red.

Solo libreria estandar (``urllib``, ``concurrent.futures``, ``json``...), igual
que ``generar_epg.py``.

Autor: guiadecanales
Licencia: MIT
===============================================================================
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone

import generar_epg

# =============================================================================
# 1. CONFIGURACION
# =============================================================================

#: Lista M3U que se comprueba y se reescribe cuando no se indica otra.
ARCHIVO_ENTRADA_POR_DEFECTO = generar_epg.LISTA_M3U_POR_DEFECTO

#: JSON opcional con las cabeceras por host y los endpoints de renovacion de
#: token. Se puede cambiar con ``--cabeceras`` o con ``M3U_CABECERAS``.
ARCHIVO_CABECERAS_POR_DEFECTO = "cabeceras_streams.json"

#: Parametros de red de la comprobacion de senales (independientes de los de
#: la descarga de la lista, que usa :data:`generar_epg.TIMEOUT_SEGUNDOS`).
TIMEOUT_SEGUNDOS = 8
INTENTOS = 2
MAXIMO_TRABAJADORES = 24

#: Bytes que se leen de cada senal: basta con el primer bloque para ver el
#: ``#EXTM3U`` del manifiesto sin descargar el canal entero.
BYTES_MUESTRA = 2048

#: Porcentaje maximo de entradas que se pueden descartar en una ejecucion. Si
#: se supera, el script **no escribe** la lista (evita publicar una lista vacia
#: porque el ejecutor se haya quedado sin salida a Internet). ``100`` lo
#: desactiva.
MAXIMO_DESCARTES_POR_DEFECTO = 90.0

#: User-Agents de los perfiles de reserva.
USER_AGENT_NAVEGADOR = generar_epg.USER_AGENT
USER_AGENT_VLC = "VLC/3.0.18 LibVLC/3.0.18"
USER_AGENT_MOVIL = (
    "Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)

#: Estados posibles de una senal.
ESTADO_OK = "ok"
ESTADO_CAIDO = "caido"
ESTADO_PROTEGIDO = "protegido"
ESTADO_DUDOSO = "dudoso"
ESTADO_SIN_COMPROBAR = "sin_comprobar"

#: Estados que se descartan segun la politica elegida. ``normal`` es el
#: equilibrio por defecto: fuera los enlaces que ya no existen y los que el
#: reproductor tampoco podria abrir (401/403), pero se conservan los ``timeout``
#: y los 5xx, que muchas veces son fallos puntuales del ejecutor (o bloqueos
#: geograficos de los servidores, que no afectan al usuario final).
POLITICAS: dict[str, frozenset[str]] = {
    "conservadora": frozenset({ESTADO_CAIDO}),
    "normal": frozenset({ESTADO_CAIDO, ESTADO_PROTEGIDO}),
    "estricta": frozenset({ESTADO_CAIDO, ESTADO_PROTEGIDO, ESTADO_DUDOSO}),
}
POLITICA_POR_DEFECTO = "normal"

#: Gravedad de cada estado al elegir el motivo que se informa cuando ninguna
#: peticion funciona (se informa el mas definitivo).
GRAVEDAD_ESTADO = {
    ESTADO_OK: 0,
    ESTADO_SIN_COMPROBAR: 1,
    ESTADO_DUDOSO: 2,
    ESTADO_PROTEGIDO: 3,
    ESTADO_CAIDO: 4,
}

#: Nombres de los parametros de la URL que pueden llevar un token renovable.
CAMPOS_TOKEN = ("token", "st", "sesion", "session", "auth", "key")

#: Cabeceras que se pueden definir por host en ``cabeceras_streams.json``
#: (``{clave en el JSON: cabecera HTTP}``). Cualquier otra clave del JSON que
#: no sea ``renovar`` se envia tal cual como cabecera HTTP.
CABECERAS_CONOCIDAS = {
    "user-agent": "User-Agent",
    "useragent": "User-Agent",
    "referer": "Referer",
    "referrer": "Referer",
    "origin": "Origin",
    "cookie": "Cookie",
    "authorization": "Authorization",
    "accept": "Accept",
}

#: Cabeceras base de toda peticion de comprobacion.
CABECERAS_BASE = {
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "close",
}

#: Marcas de una URL que piden comprobar el manifiesto HLS.
_MARCAS_HLS = (".m3u8", ".m3u", "f=.m3u8", "f=.m3u", "/play/", "lista3.php", "lista4.php")

#: Opciones de la entrada que fijan el User-Agent / Referer del reproductor.
_RE_OPCION_UA = re.compile(r"^#EXTVLCOPT:\s*http-user-agent\s*=\s*(.+)$", re.IGNORECASE)
_RE_OPCION_REFERER = re.compile(r"^#EXTVLCOPT:\s*http-referrer\s*=\s*(.+)$", re.IGNORECASE)
_RE_OPCION_ORIGIN = re.compile(r"^#EXTVLCOPT:\s*http-origin\s*=\s*(.+)$", re.IGNORECASE)

#: Atributos de la linea ``#EXTINF`` del tipo ``nombre="valor"``.
_RE_ATRIBUTOS = generar_epg._RE_ATRIBUTOS

#: Token devuelto por un endpoint de renovacion: ``{"token": "..."}`` o
#: ``...?token=...``.
_RE_TOKEN_RENOVADO = re.compile(
    r'"\s*(?:token|st|session)\s*"\s*:\s*"([A-Za-z0-9%._~:/-]{6,})"'
    r'|[?&](?:token|st)=([A-Za-z0-9%._~:/-]{6,})',
    re.IGNORECASE,
)


# =============================================================================
# 2. ENTRADAS DE LA LISTA Y RESULTADOS DE LA COMPROBACION
# =============================================================================

@dataclass
class Entrada:
    """Una entrada de la lista M3U: ``#EXTINF``, opciones y URL.

    El texto de ``extinf`` y de cada opcion se conserva **tal cual** llega, de
    modo que la lista publicada mantiene los mismos atributos (``tvg-id``,
    ``tvg-name``, ``tvg-logo``, ``group-title``) y el mismo nombre visible, es
    decir, sigue sincronizada con el ``epg.xml``.
    """

    extinf: str
    url: str
    opciones: tuple[str, ...] = ()
    grupo: str = ""
    tvg_id: str = ""

    def clave(self) -> "tuple[str, tuple[str, ...]]":
        """Clave de comprobacion: una misma URL con las mismas opciones."""
        return (self.url, self.opciones)

    def lineas(self) -> list[str]:
        """Lineas que ocupa la entrada en el archivo M3U."""
        return [self.extinf, *self.opciones, self.url]


@dataclass
class Resultado:
    """Resultado de comprobar una senal."""

    url: str
    estado: str = ESTADO_SIN_COMPROBAR
    codigo: int = 0
    detalle: str = ""
    url_final: str = ""
    user_agent: str = ""
    referer: str = ""
    perfil: str = ""
    intentos: int = 0

    def es_descartable(self, politica: str = POLITICA_POR_DEFECTO) -> bool:
        """Indica si la senal se descarta con la politica indicada."""
        return self.estado in POLITICAS.get(politica, POLITICAS[POLITICA_POR_DEFECTO])


# =============================================================================
# 3. LECTURA Y ESCRITURA DE LA LISTA M3U
# =============================================================================

def parsear_entradas(texto: str) -> tuple[list[str], list[Entrada]]:
    """Separa la lista en ``(cabecera, entradas)`` conservando el texto original.

    La cabecera son las lineas ``#`` anteriores a la primera entrada (el
    ``#EXTM3U`` con ``x-tvg-url`` y los comentarios del generador). Las lineas
    de opciones (``#EXTVLCOPT``, ``#KODIPROP``, ``#EXTGRP``...) se guardan con
    su entrada, y una ``#EXTINF`` sin URL no se publica.
    """
    cabecera: list[str] = []
    entradas: list[Entrada] = []
    extinf: str | None = None
    opciones: list[str] = []

    for linea_bruta in (texto or "").splitlines():
        linea = linea_bruta.strip()
        if not linea:
            continue
        if linea.upper().startswith("#EXTINF"):
            # Una #EXTINF nueva sin URL anula la anterior (entrada huerfana).
            extinf, opciones = linea, []
            continue
        if linea.startswith("#"):
            if extinf is None:
                cabecera.append(linea)
            else:
                opciones.append(linea)
            continue
        if extinf is None:
            continue
        atributos = {clave.lower(): valor for clave, valor in _RE_ATRIBUTOS.findall(extinf)}
        entradas.append(
            Entrada(
                extinf=extinf,
                url=linea,
                opciones=tuple(opciones),
                grupo=(atributos.get("group-title") or "").strip(),
                tvg_id=(atributos.get("tvg-id") or "").strip(),
            )
        )
        extinf, opciones = None, []

    return cabecera, entradas


def preparar_cabecera(cabecera: "list[str] | tuple[str, ...]", url_epg: str = "") -> list[str]:
    """Devuelve la cabecera con ``#EXTM3U`` primero y la guia declarada.

    Si el origen no trae ``x-tvg-url``/``url-tvg`` se anaden con ``url_epg``,
    para que el reproductor descargue la guia solo. Si ya los trae, se respetan.
    """
    lineas = [linea.strip() for linea in cabecera or () if linea.strip()]
    posicion = next(
        (indice for indice, linea in enumerate(lineas) if linea.upper().startswith("#EXTM3U")),
        None,
    )
    url_epg = (url_epg or "").strip()

    if posicion is None:
        primera = f'#EXTM3U x-tvg-url="{url_epg}" url-tvg="{url_epg}"' if url_epg else "#EXTM3U"
        return [primera, *lineas]

    linea = lineas.pop(posicion)
    if url_epg and "x-tvg-url" not in linea.lower():
        linea = f'{linea.rstrip()} x-tvg-url="{url_epg}" url-tvg="{url_epg}"'
    return [linea, *lineas]


def ordenar_entradas(entradas: "list[Entrada] | tuple[Entrada, ...]") -> list[Entrada]:
    """Ordena las entradas por la jerarquia de grupos de ``lista.m3u``.

    Se usa :func:`generar_epg.orden_del_grupo` (el mismo criterio que
    ``generar_epg.py``) y, dentro de cada grupo, se mantiene **junta** cada
    senal de un mismo canal (``Opc. 2``, ``Opc. 3``...) en el orden original.
    El orden es estable: aplicarlo a una lista ya ordenada no cambia nada.
    """
    indice_canal: dict[tuple[str, str], int] = {}
    claves: list[tuple[tuple[int, str], int, int]] = []
    for posicion, entrada in enumerate(entradas):
        clave = (entrada.grupo, entrada.tvg_id or entrada.extinf)
        indice_canal.setdefault(clave, len(indice_canal))
        claves.append((generar_epg.orden_del_grupo(entrada.grupo), indice_canal[clave], posicion))
    return [entradas[posicion] for _grupo, _canal, posicion in sorted(claves)]


def quitar_duplicados(entradas: "list[Entrada] | tuple[Entrada, ...]") -> list[Entrada]:
    """Descarta las entradas identicas (misma ``#EXTINF``, URL y opciones)."""
    vistas: set[tuple[str, str, tuple[str, ...]]] = set()
    unicas: list[Entrada] = []
    for entrada in entradas:
        clave = (entrada.extinf, entrada.url, entrada.opciones)
        if clave in vistas:
            continue
        vistas.add(clave)
        unicas.append(entrada)
    return unicas


def escribir_lista(
    destino: str,
    cabecera: "list[str] | tuple[str, ...]",
    entradas: "list[Entrada] | tuple[Entrada, ...]",
) -> int:
    """Escribe la lista M3U (UTF-8 y saltos de linea LF) y devuelve las entradas.

    Se escribe en un archivo temporal y se renombra al final: si algo falla a
    mitad, la lista anterior queda intacta.
    """
    lineas = list(cabecera)
    for entrada in entradas:
        lineas.extend(entrada.lineas())
    texto = "\n".join(lineas) + "\n"

    temporal = f"{destino}.tmp"
    with open(temporal, "w", encoding="utf-8", newline="\n") as archivo:
        archivo.write(texto)
    os.replace(temporal, destino)
    return len(entradas)


# =============================================================================
# 4. CABECERAS POR HOST (``cabeceras_streams.json``)
# =============================================================================

def _cabeceras_de_opciones(opciones: "tuple[str, ...] | list[str]") -> dict[str, str]:
    """Extrae el ``User-Agent``/``Referer``/``Origin`` de las opciones de la entrada.

    Reconoce ``#EXTVLCOPT:http-user-agent=...``, ``http-referrer=`` y
    ``http-origin=``, que es como ``generar_epg.py`` publica las cabeceras de
    ``mapeo_canales.json`` y como las entienden VLC, TiviMate e IPTV Smarters.
    """
    cabeceras: dict[str, str] = {}
    for linea in opciones or ():
        texto = str(linea).strip()
        for patron, cabecera in (
            (_RE_OPCION_UA, "User-Agent"),
            (_RE_OPCION_REFERER, "Referer"),
            (_RE_OPCION_ORIGIN, "Origin"),
        ):
            coincidencia = patron.match(texto)
            if not coincidencia:
                continue
            valor = coincidencia.group(1).strip().strip('"').strip("'").strip()
            if valor:
                cabeceras[cabecera] = valor
            break
    return cabeceras


def opciones_con_cabeceras(
    opciones: "tuple[str, ...] | list[str]",
    user_agent: str = "",
    referer: str = "",
) -> tuple[str, ...]:
    """Devuelve las opciones de la entrada con las cabeceras que han funcionado.

    Se sustituyen las opciones de ``User-Agent``/``Referer`` previas (eran las
    que fallaban) y se anaden al final las nuevas, en el orden que esperan los
    reproductores: primero el ``User-Agent`` y despues el ``Referer``.
    """
    resultado: list[str] = []
    for linea in opciones or ():
        texto = str(linea).strip()
        if not texto:
            continue
        if user_agent and _RE_OPCION_UA.match(texto):
            continue
        if referer and _RE_OPCION_REFERER.match(texto):
            continue
        resultado.append(texto)
    if user_agent:
        resultado.append(f"#EXTVLCOPT:http-user-agent={user_agent}")
    if referer:
        resultado.append(f"#EXTVLCOPT:http-referrer={referer}")
    return tuple(resultado)


def _cabeceras_regla(regla: dict) -> dict[str, str]:
    """Convierte una regla del JSON en cabeceras HTTP listas para enviar."""
    cabeceras: dict[str, str] = {}
    for clave, valor in (regla or {}).items():
        if not isinstance(valor, (str, int, float)):
            continue
        nombre = str(clave).strip()
        if nombre.lower() == "renovar":
            continue
        valor = str(valor).strip()
        if not valor:
            continue
        cabeceras[CABECERAS_CONOCIDAS.get(nombre.lower(), nombre)] = valor
    return cabeceras


def cargar_cabeceras(ruta: str) -> tuple[dict[str, dict], dict[str, str]]:
    """Lee ``cabeceras_streams.json`` y devuelve ``(reglas_por_host, por_defecto)``.

    Formato admitido (los bloques que empiezan por ``$`` o ``_`` son comentarios
    y se ignoran)::

        {
          "$esquema": "https://... (documentacion)",
          "por_defecto": {"user-agent": "..."},
          "hosts": {
            "cdn.ejemplo.tv": {"user-agent": "...", "referer": "...",
                               "renovar": "https://cdn.ejemplo.tv/token?st={token}"}
          }
        }

    Tambien se acepta el formato plano, con los hosts en la raiz del objeto. Si
    el archivo no existe o no es JSON valido se devuelve ``({}, {})``: el script
    funciona igual, solo que sin cabeceras especificas.
    """
    ruta = (ruta or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return {}, {}
    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(datos, dict):
        return {}, {}

    if isinstance(datos.get("hosts"), dict):
        crudos = datos["hosts"]
    else:
        crudos = {
            clave: valor
            for clave, valor in datos.items()
            if not str(clave).startswith(("$", "_")) and isinstance(valor, dict)
        }

    reglas: dict[str, dict] = {}
    for host, regla in crudos.items():
        if not isinstance(regla, dict):
            continue
        clave = str(host).strip().lower()
        if not clave or clave.endswith("."):
            continue
        reglas[clave] = regla

    por_defecto = datos.get("por_defecto") if isinstance(datos.get("por_defecto"), dict) else {}
    return reglas, dict(por_defecto)


def _regla_del_host(url: str, reglas: dict[str, dict]) -> dict:
    """Devuelve la regla del host de la URL, admitiendo subdominios."""
    host = (urllib.parse.urlsplit(url or "").netloc or "").lower()
    host = host.rsplit("@", 1)[-1].split(":")[0]
    if not host or not reglas:
        return {}
    if host in reglas:
        return reglas[host]
    for dominio, regla in reglas.items():
        if host.endswith("." + dominio):
            return regla
    return {}


# =============================================================================
# 5. COMPROBACION DE SEÑALES (HTTP)
# =============================================================================

def parece_hls(url: str) -> bool:
    """Indica si la URL apunta a un manifiesto HLS (``.m3u8``)."""
    return any(marca in (url or "").lower() for marca in _MARCAS_HLS)


def _estado_del_codigo(codigo: int) -> tuple[str, str]:
    """Traduce un codigo HTTP a ``(estado, detalle)``.

    Los 2xx se dan por buenos (el cuerpo del manifiesto solo se revisa en
    :func:`_evaluar_respuesta`); un 401/403/407/451 se considera proteccion, un
    408/429 o un 5xx pasajero, y el resto de 4xx una senal que ya no existe.
    """
    if 200 <= codigo < 300:
        return ESTADO_OK, f"http {codigo}"
    if codigo in (401, 403, 407, 451):
        return ESTADO_PROTEGIDO, f"http {codigo}"
    if codigo in (408, 429):
        return ESTADO_DUDOSO, f"http {codigo}"
    if 400 <= codigo < 500:
        return ESTADO_CAIDO, f"http {codigo}"
    return ESTADO_DUDOSO, f"http {codigo}"


def _evaluar_respuesta(url: str, codigo: int, tipo: str, muestra: bytes) -> tuple[str, str]:
    """Decide el estado de una respuesta 2xx leyendo el primer bloque recibido.

    Un 200 con una pagina HTML (tipico de un panel caducado o de un secuestro
    por el proveedor) cuenta como caido; un manifiesto ``#EXTM3U`` o un
    ``Content-Type: application/vnd.apple.mpegurl`` cuenta como correcto.
    """
    if not (200 <= codigo < 300):
        return _estado_del_codigo(codigo)
    if not parece_hls(url):
        return ESTADO_OK, f"http {codigo}"

    texto = (muestra or b"").lower()
    if b"#extm3u" in texto or "mpegurl" in (tipo or ""):
        return ESTADO_OK, f"http {codigo} hls"
    if not texto:
        return ESTADO_OK, f"http {codigo} sin datos"
    if texto.lstrip().startswith((b"<", b"{", b"[")) or b"<html" in texto or b"<body" in texto:
        return ESTADO_CAIDO, f"http {codigo} respuesta no hls"
    return ESTADO_OK, f"http {codigo} datos"


def _es_error_dns(razon: object) -> bool:
    """Indica si el error de red es un fallo de resolucion de nombre."""
    texto = str(razon or "").lower()
    marcas = (
        "name or service not known",
        "nodename nor servname",
        "getaddrinfo failed",
        "no address associated with hostname",
        "name does not resolve",
        "temporary failure in name resolution",
    )
    return any(marca in texto for marca in marcas)


def _fallo_definitivo(resultado: Resultado) -> bool:
    """Indica si el fallo no se arregla probando otras cabeceras.

    Un 400/404/410 lo decide el servidor y no depende del ``User-Agent``; un
    dominio que no existe tampoco. Un 401/403, un ``timeout`` o un 5xx si
    pueden cambiar segun las cabeceras (o ser pasajeros), asi que se sigue
    probando el resto de perfiles.
    """
    if "dominio inexistente" in (resultado.detalle or ""):
        return True
    return resultado.codigo in (400, 404, 410)


def perfiles_de_peticion(
    url: str,
    opciones: "tuple[str, ...] | list[str]" = (),
    reglas: "dict[str, dict] | None" = None,
    por_defecto: "dict[str, str] | None" = None,
) -> list[tuple[str, dict[str, str]]]:
    """Lista ordenada de ``(perfil, cabeceras)`` que se prueban para una URL.

    El orden va de lo mas especifico a lo mas generico: cabeceras de la propia
    entrada, regla del host en ``cabeceras_streams.json``, cabeceras por
    defecto, navegador con ``Referer``/``Origin`` del host, navegador sin
    ``Referer``, VLC y un navegador movil. Se eliminan los perfiles repetidos.
    """
    partes = urllib.parse.urlsplit(url or "")
    host = (partes.netloc or "").lower()
    esquema = partes.scheme or "http"
    origen = f"{esquema}://{host}" if host else ""

    perfiles: list[tuple[str, dict[str, str]]] = []
    vistos: set[tuple[tuple[str, str], ...]] = set()

    def agregar(etiqueta: str, cabeceras: dict[str, str]) -> None:
        limpias = {clave: valor for clave, valor in cabeceras.items() if valor}
        if not limpias:
            return
        firma = tuple(sorted((clave.lower(), valor) for clave, valor in limpias.items()))
        if firma in vistos:
            return
        vistos.add(firma)
        perfiles.append((etiqueta, {**CABECERAS_BASE, **limpias}))

    agregar("lista", _cabeceras_de_opciones(opciones))
    if reglas:
        agregar("host", _cabeceras_regla(_regla_del_host(url, reglas)))
    if por_defecto:
        agregar("por_defecto", _cabeceras_regla(por_defecto))
    if origen:
        agregar("navegador", {"User-Agent": USER_AGENT_NAVEGADOR, "Referer": origen + "/", "Origin": origen})
    agregar("navegador-simple", {"User-Agent": USER_AGENT_NAVEGADOR})
    if origen:
        agregar("vlc", {"User-Agent": USER_AGENT_VLC, "Referer": origen + "/"})
        agregar("movil", {"User-Agent": USER_AGENT_MOVIL, "Referer": origen + "/"})
    agregar("vlc-simple", {"User-Agent": USER_AGENT_VLC})
    return perfiles


def _probar(url: str, cabeceras: dict[str, str], timeout: int) -> Resultado:
    """Hace **una** peticion ``GET`` a la URL y clasifica la respuesta."""
    resultado = Resultado(url=url, intentos=1)
    peticion = urllib.request.Request(url, headers=dict(cabeceras), method="GET")
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            codigo = int(getattr(respuesta, "status", 0) or 0)
            tipo = str(respuesta.headers.get("Content-Type") or "").lower()
            resultado.url_final = respuesta.geturl() or url
            resultado.codigo = codigo
            try:
                muestra = respuesta.read(BYTES_MUESTRA) or b""
            except (OSError, ValueError, http.client.HTTPException):
                muestra = b""
    except urllib.error.HTTPError as error:
        resultado.codigo = int(getattr(error, "code", 0) or 0)
        resultado.estado, resultado.detalle = _estado_del_codigo(resultado.codigo)
        return resultado
    except urllib.error.URLError as error:
        razon = getattr(error, "reason", error)
        if isinstance(razon, (socket.timeout, TimeoutError)):
            resultado.estado, resultado.detalle = ESTADO_DUDOSO, "timeout"
        elif _es_error_dns(razon):
            resultado.estado, resultado.detalle = ESTADO_CAIDO, "dominio inexistente"
        else:
            resultado.estado, resultado.detalle = ESTADO_DUDOSO, f"red: {razon}"
        return resultado
    except (socket.timeout, TimeoutError):
        resultado.estado, resultado.detalle = ESTADO_DUDOSO, "timeout"
        return resultado
    except ssl.SSLError as error:
        resultado.estado, resultado.detalle = ESTADO_DUDOSO, f"tls: {error}"
        return resultado
    except (OSError, ValueError, http.client.HTTPException) as error:
        resultado.estado, resultado.detalle = ESTADO_DUDOSO, f"red: {error}"
        return resultado

    resultado.estado, resultado.detalle = _evaluar_respuesta(url, codigo, tipo, muestra)
    return resultado


def _leer_texto(url: str, timeout: int, cabeceras: "dict[str, str] | None" = None) -> str:
    """Descarga el cuerpo de una URL como texto (para los endpoints de token)."""
    peticion = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT_NAVEGADOR, "Accept": "*/*", **(cabeceras or {})},
        method="GET",
    )
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return (respuesta.read(65536) or b"").decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return ""


def renovar_token(url: str, regla: dict, timeout: int = TIMEOUT_SEGUNDOS) -> str:
    """Devuelve la URL con el token de sesion renovado, o ``""`` si no se puede.

    Solo actua si el host tiene una plantilla ``renovar`` en
    ``cabeceras_streams.json`` y la URL lleva un parametro del tipo ``token`` o
    ``st``. La plantilla puede usar los parametros de la URL como marcadores
    (``https://api.ejemplo.tv/sesion?v={token}&canal={id}``); si no lleva
    marcadores se pide tal cual. El token nuevo se toma de la respuesta
    (``{"token": "..."}`` o ``...?token=...``) y se reescribe en la URL.
    """
    plantilla = str((regla or {}).get("renovar") or "").strip()
    if not plantilla:
        return ""

    partes = urllib.parse.urlsplit(url or "")
    parametros = urllib.parse.parse_qsl(partes.query, keep_blank_values=True)
    if not any(clave.lower() in CAMPOS_TOKEN for clave, _valor in parametros):
        return ""

    try:
        endpoint = plantilla.format(**dict(parametros)) if "{" in plantilla else plantilla
    except (KeyError, IndexError, ValueError):
        endpoint = plantilla
    if not endpoint.lower().startswith(("http://", "https://")):
        return ""

    respuesta = _leer_texto(endpoint, timeout, _cabeceras_regla(regla))
    tokens = [nuevo or de_url for nuevo, de_url in _RE_TOKEN_RENOVADO.findall(respuesta or "")]
    if not tokens:
        return ""
    nuevo_token = tokens[0]

    consulta = [
        (clave, nuevo_token if clave.lower() in CAMPOS_TOKEN else valor)
        for clave, valor in parametros
    ]
    return urllib.parse.urlunsplit(
        (partes.scheme, partes.netloc, partes.path, urllib.parse.urlencode(consulta), partes.fragment)
    )


def comprobar_enlace(
    url: str,
    opciones: "tuple[str, ...] | list[str]" = (),
    timeout: int = TIMEOUT_SEGUNDOS,
    intentos: int = INTENTOS,
    reglas: "dict[str, dict] | None" = None,
    por_defecto: "dict[str, str] | None" = None,
    renovar: bool = True,
) -> Resultado:
    """Comprueba una senal probando los perfiles de cabeceras en cascada.

    Devuelve el primer :class:`Resultado` correcto; si ninguno funciona, el
    fallo mas definitivo (404 antes que 403, y antes que un ``timeout``). Antes
    de descartar la senal se intenta renovar el token de sesion si el host lo
    permite.
    """
    intentos = max(1, int(intentos))
    mejor = Resultado(url=url, estado=ESTADO_SIN_COMPROBAR, detalle="sin probar")
    total_intentos = 0

    for perfil, cabeceras in perfiles_de_peticion(url, opciones, reglas, por_defecto):
        ultimo: Resultado | None = None
        for _intento in range(intentos):
            # Solo se reintenta lo que puede ser pasajero (timeout, 5xx...).
            ultimo = _probar(url, cabeceras, timeout)
            total_intentos += 1
            if ultimo.estado != ESTADO_DUDOSO:
                break
        if ultimo is None:
            continue
        ultimo.perfil = perfil
        if ultimo.estado == ESTADO_OK:
            ultimo.intentos = total_intentos
            ultimo.user_agent = str(cabeceras.get("User-Agent") or "")
            ultimo.referer = str(cabeceras.get("Referer") or "")
            return ultimo
        if GRAVEDAD_ESTADO.get(ultimo.estado, 0) > GRAVEDAD_ESTADO.get(mejor.estado, 0):
            mejor = ultimo
        if _fallo_definitivo(ultimo):
            # 404/410 o un dominio que no existe: ninguna cabecera lo arregla.
            mejor.intentos = total_intentos
            return mejor

    mejor.intentos = total_intentos

    if renovar:
        regla = _regla_del_host(url, reglas or {})
        nueva_url = renovar_token(url, regla, timeout)
        if nueva_url and nueva_url != url:
            cabeceras = {**CABECERAS_BASE, **_cabeceras_regla(regla)}
            prueba = _probar(nueva_url, cabeceras, timeout)
            if prueba.estado == ESTADO_OK:
                prueba.url = url
                prueba.url_final = nueva_url
                prueba.perfil = "renovado"
                prueba.user_agent = str(cabeceras.get("User-Agent") or "")
                prueba.referer = str(cabeceras.get("Referer") or "")
                return prueba

    return mejor


def comprobar_todas(
    entradas: "list[Entrada] | tuple[Entrada, ...]",
    timeout: int = TIMEOUT_SEGUNDOS,
    intentos: int = INTENTOS,
    trabajadores: int = MAXIMO_TRABAJADORES,
    reglas: "dict[str, dict] | None" = None,
    por_defecto: "dict[str, str] | None" = None,
    limite: int = 0,
    renovar: bool = True,
    aviso=None,
) -> dict[tuple[str, tuple[str, ...]], Resultado]:
    """Comprueba todas las senales de la lista en paralelo.

    Cada URL se comprueba **una sola vez** (aunque varias entradas la
    compartan) y el resultado se reutiliza. Con ``limite`` mayor que cero solo
    se comprueban las primeras senales: el resto quedan como ``sin_comprobar`` y
    se conservan, para poder probar el script en segundos.
    """
    claves: list[tuple[str, tuple[str, ...]]] = []
    vistas: set[tuple[str, tuple[str, ...]]] = set()
    for entrada in entradas:
        clave = entrada.clave()
        if clave in vistas:
            continue
        vistas.add(clave)
        claves.append(clave)

    resultados: dict[tuple[str, tuple[str, ...]], Resultado] = {}
    probar = claves[:limite] if limite and limite > 0 else list(claves)
    for clave in claves[len(probar):]:
        resultados[clave] = Resultado(
            url=clave[0], estado=ESTADO_SIN_COMPROBAR, detalle="sin comprobar (--limite-pruebas)"
        )

    total = len(probar)
    if not total:
        return resultados

    with ThreadPoolExecutor(max_workers=max(1, int(trabajadores))) as ejecutor:
        futuros = {
            ejecutor.submit(
                comprobar_enlace, url, opciones, timeout, intentos, reglas, por_defecto, renovar
            ): (url, opciones)
            for url, opciones in probar
        }
        hechas = 0
        for futuro in as_completed(futuros):
            clave = futuros[futuro]
            try:
                resultados[clave] = futuro.result()
            except Exception as error:  # pragma: no cover - red de seguridad
                resultados[clave] = Resultado(
                    url=clave[0], estado=ESTADO_DUDOSO, detalle=f"error: {error}"
                )
            hechas += 1
            if aviso and (hechas % 100 == 0 or hechas == total):
                aviso(f"      comprobadas {hechas}/{total} senales...")

    return resultados


# =============================================================================
# 6. DEPURACION DE LA LISTA Y RESUMEN
# =============================================================================

def aplicar_resultado(entrada: Entrada, resultado: Resultado) -> Entrada:
    """Devuelve la entrada con la URL y las cabeceras que han funcionado.

    Si el enlace necesitaba un ``User-Agent``/``Referer`` distinto del que trae
    la lista, se publica como ``#EXTVLCOPT`` para que el reproductor tambien lo
    envie. Si el token se ha renovado, se publica la URL nueva.
    """
    url = entrada.url
    opciones = entrada.opciones

    if resultado.perfil == "renovado" and resultado.url_final and resultado.url_final != url:
        url = resultado.url_final

    if resultado.perfil and resultado.perfil != "lista":
        nuevas = opciones_con_cabeceras(opciones, resultado.user_agent, resultado.referer)
        if nuevas != opciones:
            opciones = nuevas

    return Entrada(
        extinf=entrada.extinf,
        url=url,
        opciones=opciones,
        grupo=entrada.grupo,
        tvg_id=entrada.tvg_id,
    )


def depurar_entradas(
    entradas: "list[Entrada] | tuple[Entrada, ...]",
    resultados: dict[tuple[str, tuple[str, ...]], Resultado],
    politica: str = POLITICA_POR_DEFECTO,
) -> tuple[list[Entrada], list[tuple[Entrada, Resultado]], dict[str, int]]:
    """Quita las senales descartables y devuelve ``(vivas, descartadas, conteo)``.

    El conteo incluye cuantas senales han quedado en cada estado, para el
    resumen final.
    """
    vivas: list[Entrada] = []
    descartadas: list[tuple[Entrada, Resultado]] = []
    conteo = {
        ESTADO_OK: 0,
        ESTADO_CAIDO: 0,
        ESTADO_PROTEGIDO: 0,
        ESTADO_DUDOSO: 0,
        ESTADO_SIN_COMPROBAR: 0,
    }

    for entrada in entradas:
        resultado = resultados.get(entrada.clave())
        if resultado is None:
            vivas.append(entrada)
            continue
        conteo[resultado.estado] = conteo.get(resultado.estado, 0) + 1
        if resultado.es_descartable(politica):
            descartadas.append((entrada, resultado))
            continue
        vivas.append(aplicar_resultado(entrada, resultado))

    return vivas, descartadas, conteo


def construir_informe(
    *,
    origen: str,
    salida: str,
    politica: str,
    entradas_origen: int,
    entradas_finales: int,
    duplicadas: int,
    resultados: dict[tuple[str, tuple[str, ...]], Resultado],
    conteo: dict[str, int],
    descartadas: "list[tuple[Entrada, Resultado]]",
    duracion: float,
    maximo_detalles: int = 500,
) -> dict:
    """Construye el resumen de la ejecucion (el que se guarda con ``--informe``)."""
    senales = len(resultados)
    ok = conteo.get(ESTADO_OK, 0)
    cabeceras = sum(
        1
        for resultado in resultados.values()
        if resultado.estado == ESTADO_OK
        and resultado.perfil not in ("", "lista")
        and (resultado.user_agent or resultado.referer)
    )
    renovados = sum(
        1 for resultado in resultados.values() if resultado.estado == ESTADO_OK and resultado.perfil == "renovado"
    )
    porcentaje = (100.0 * len(descartadas) / entradas_origen) if entradas_origen else 0.0

    detalles = [
        {
            "url": entrada.url,
            "grupo": entrada.grupo,
            "tvg_id": entrada.tvg_id,
            "estado": resultado.estado,
            "codigo": resultado.codigo,
            "detalle": resultado.detalle,
        }
        for entrada, resultado in descartadas[:maximo_detalles]
    ]

    return {
        "generado": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "origen": origen,
        "salida": salida,
        "politica": politica,
        "entradas_origen": entradas_origen,
        "senales_distintas": senales,
        "entradas_finales": entradas_finales,
        "duplicadas_eliminadas": duplicadas,
        "descartadas": len(descartadas),
        "porcentaje_descartes": round(porcentaje, 2),
        "estados": {
            "ok": ok,
            "caido": conteo.get(ESTADO_CAIDO, 0),
            "protegido": conteo.get(ESTADO_PROTEGIDO, 0),
            "dudoso": conteo.get(ESTADO_DUDOSO, 0),
            "sin_comprobar": conteo.get(ESTADO_SIN_COMPROBAR, 0),
        },
        "cabeceras_aplicadas": cabeceras,
        "tokens_renovados": renovados,
        "duracion_segundos": round(duracion, 1),
        "detalle_descartes": detalles,
    }


def escribir_informe(ruta: str, informe: dict) -> None:
    """Escribe el informe en JSON (UTF-8), creando la carpeta si hace falta."""
    carpeta = os.path.dirname(os.path.abspath(ruta))
    if carpeta and not os.path.isdir(carpeta):
        os.makedirs(carpeta, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="\n") as archivo:
        json.dump(informe, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def formatear_resumen(informe: dict) -> list[str]:
    """Devuelve el resumen de la ejecucion como lineas de texto."""
    estados = informe.get("estados") or {}
    minutos, segundos = divmod(int(informe.get("duracion_segundos") or 0), 60)
    lineas = [
        "RESUMEN DE LA ACTUALIZACION",
        f"  origen                : {informe.get('origen')}",
        f"  entradas de origen    : {informe.get('entradas_origen')}",
        f"  senales distintas     : {informe.get('senales_distintas')}",
        f"  ok                    : {estados.get('ok', 0)}",
        f"  caidas (404/410/400)  : {estados.get('caido', 0)}",
        f"  protegidas (401/403)  : {estados.get('protegido', 0)}",
        f"  dudosas (timeout/5xx) : {estados.get('dudoso', 0)}",
        f"  sin comprobar         : {estados.get('sin_comprobar', 0)}",
        f"  descartadas           : {informe.get('descartadas')} ({informe.get('porcentaje_descartes')} %)",
        f"  duplicadas eliminadas : {informe.get('duplicadas_eliminadas')}",
        f"  cabeceras aplicadas   : {informe.get('cabeceras_aplicadas')}",
        f"  tokens renovados      : {informe.get('tokens_renovados')}",
        f"  entradas publicadas   : {informe.get('entradas_finales')}",
        f"  politica              : {informe.get('politica')}",
        f"  duracion              : {minutos} min {segundos} s",
        f"  salida                : {informe.get('salida')}",
    ]
    detalles = informe.get("detalle_descartes") or []
    if detalles:
        lineas.append("")
        lineas.append("Primeras senales descartadas:")
        for detalle in detalles[:10]:
            motivo = detalle.get("detalle") or detalle.get("estado") or ""
            grupo = detalle.get("grupo") or "-"
            lineas.append(f"  [{grupo}] {detalle.get('url')} -> {motivo}")
        if len(detalles) > 10:
            lineas.append(f"  ... y {len(detalles) - 10} mas (ver --informe)")
    return lineas


# =============================================================================
# 7. LINEA DE COMANDOS
# =============================================================================

def _crear_parser() -> argparse.ArgumentParser:
    """Define las opciones de linea de comandos del script."""
    parser = argparse.ArgumentParser(
        prog="actualizar_lista.py",
        description=(
            "Comprueba las senales de la lista M3U (HTTP real, con cabeceras y "
            "token), descarta las que ya no responden y publica la lista limpia "
            "y ordenada, lista para el reproductor."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python actualizar_lista.py\n"
            "  python actualizar_lista.py -u \"$env:M3U_URL\"\n"
            "  python actualizar_lista.py --sin-verificar --salida lista_ordenada.m3u\n"
            "  python actualizar_lista.py --politica estricta --informe informe.json\n"
            "  python actualizar_lista.py --limite-pruebas 40 -q\n"
            "\n"
            "Politicas: conservadora (solo 404/410/400), normal (ademas 401/403),\n"
            "estricta (ademas timeout y 5xx).\n"
        ),
    )
    parser.add_argument(
        "-u",
        "--url",
        default=os.environ.get("M3U_URL", "").strip(),
        help="URL de la lista M3U de origen (si falta, se lee --entrada).",
    )
    parser.add_argument(
        "-e",
        "--entrada",
        default="",
        help=f"lista M3U local de origen (por defecto: {ARCHIVO_ENTRADA_POR_DEFECTO}).",
    )
    parser.add_argument(
        "-l",
        "--salida",
        default=ARCHIVO_ENTRADA_POR_DEFECTO,
        help=f"lista M3U de salida (por defecto: {ARCHIVO_ENTRADA_POR_DEFECTO}).",
    )
    parser.add_argument(
        "--epg-publica",
        default=generar_epg.URL_EPG_PUBLICA,
        help="URL de la guia que se declara en la cabecera si el origen no la trae.",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=TIMEOUT_SEGUNDOS,
        help=f"segundos de espera por senal (por defecto: {TIMEOUT_SEGUNDOS}).",
    )
    parser.add_argument(
        "--timeout-descarga",
        type=int,
        default=generar_epg.TIMEOUT_SEGUNDOS,
        help=f"segundos de espera al descargar la lista (por defecto: {generar_epg.TIMEOUT_SEGUNDOS}).",
    )
    parser.add_argument(
        "--intentos",
        type=int,
        default=INTENTOS,
        help=f"intentos por perfil de cabeceras (por defecto: {INTENTOS}).",
    )
    parser.add_argument(
        "-j",
        "--trabajadores",
        type=int,
        default=MAXIMO_TRABAJADORES,
        help=f"peticiones en paralelo (por defecto: {MAXIMO_TRABAJADORES}).",
    )
    parser.add_argument(
        "--politica",
        choices=sorted(POLITICAS),
        default=POLITICA_POR_DEFECTO,
        help=f"que senales se descartan (por defecto: {POLITICA_POR_DEFECTO}).",
    )
    parser.add_argument(
        "--cabeceras",
        default=os.environ.get("M3U_CABECERAS", "").strip() or ARCHIVO_CABECERAS_POR_DEFECTO,
        help=f"JSON con cabeceras por host (por defecto: {ARCHIVO_CABECERAS_POR_DEFECTO}).",
    )
    parser.add_argument(
        "--limite-pruebas",
        type=int,
        default=0,
        help="comprobar solo las primeras N senales y conservar el resto (0 = todas).",
    )
    parser.add_argument(
        "--maximo-descartes",
        type=float,
        default=MAXIMO_DESCARTES_POR_DEFECTO,
        help=(
            "porcentaje maximo de entradas descartables; si se supera no se escribe "
            f"la lista (por defecto: {MAXIMO_DESCARTES_POR_DEFECTO:.0f}, 100 = sin limite)."
        ),
    )
    parser.add_argument(
        "--informe",
        default="",
        help="ruta del JSON donde guardar el resumen de la ejecucion.",
    )
    parser.add_argument("--sin-verificar", action="store_true", help="no comprobar las senales (solo limpiar y ordenar).")
    parser.add_argument("--sin-renovar", action="store_true", help="no intentar renovar los tokens de sesion.")
    parser.add_argument("--sin-ordenar", action="store_true", help="no reordenar las entradas por grupo.")
    parser.add_argument("-q", "--silencioso", action="store_true", help="solo mostrar errores.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Ejecuta el mantenimiento de la lista y devuelve el codigo de salida."""
    argumentos = _crear_parser().parse_args(argv)
    inicio = time.monotonic()

    def aviso(mensaje: str) -> None:
        if not argumentos.silencioso:
            print(mensaje, flush=True)

    # ---- 1. Origen de la lista ---------------------------------------------
    origen = (argumentos.url or "").strip()
    if origen and generar_epg._es_url_placeholder(origen):
        print(
            "ERROR: la URL de la lista M3U parece el valor de ejemplo.\n"
            "  Indica la URL 'raw' real con --url o con la variable M3U_URL.",
            file=sys.stderr,
        )
        return 1

    if origen:
        aviso(f"1/5 Descargando la lista M3U de {origen}")
        try:
            texto = generar_epg.descargar_m3u(origen, timeout=max(1, argumentos.timeout_descarga))
        except SystemExit as salida:
            print(f"ERROR: no se pudo descargar la lista M3U: {salida}", file=sys.stderr)
            return 1
        descripcion_origen = origen
    else:
        ruta_entrada = (argumentos.entrada or ARCHIVO_ENTRADA_POR_DEFECTO).strip()
        aviso(f"1/5 Leyendo la lista local {ruta_entrada}")
        try:
            with open(ruta_entrada, "r", encoding="utf-8", errors="replace") as archivo:
                texto = archivo.read()
        except OSError as error:
            print(f"ERROR: no se pudo leer {ruta_entrada}: {error}", file=sys.stderr)
            return 1
        descripcion_origen = ruta_entrada

    cabecera, entradas = parsear_entradas(texto)
    if not entradas:
        print(
            f"ERROR: la lista de origen no tiene entradas (#EXTINF + URL): {descripcion_origen}",
            file=sys.stderr,
        )
        return 1
    aviso(f"      {len(entradas)} entradas leidas")

    # ---- 2. Comprobacion de las senales ------------------------------------
    if argumentos.sin_verificar:
        aviso("2/5 Comprobacion omitida (--sin-verificar)")
        resultados = {
            entrada.clave(): Resultado(
                url=entrada.url, estado=ESTADO_SIN_COMPROBAR, detalle="sin comprobar"
            )
            for entrada in entradas
        }
    else:
        reglas, por_defecto = cargar_cabeceras(argumentos.cabeceras)
        aviso(
            f"2/5 Comprobando senales (timeout {max(1, argumentos.timeout)} s, "
            f"{max(1, argumentos.trabajadores)} hilos, politica {argumentos.politica})"
        )
        if reglas:
            aviso(f"      cabeceras especificas para {len(reglas)} hosts")
        try:
            resultados = comprobar_todas(
                entradas,
                timeout=max(1, argumentos.timeout),
                intentos=max(1, argumentos.intentos),
                trabajadores=max(1, argumentos.trabajadores),
                reglas=reglas,
                por_defecto=por_defecto,
                limite=max(0, argumentos.limite_pruebas),
                renovar=not argumentos.sin_renovar,
                aviso=aviso,
            )
        except KeyboardInterrupt:
            print("\nInterrumpido por el usuario.", file=sys.stderr)
            return 130

    # ---- 3. Depuracion y guardas -------------------------------------------
    aviso("3/5 Depurando la lista")
    vivas, descartadas, conteo = depurar_entradas(entradas, resultados, argumentos.politica)

    total = len(entradas)
    porcentaje = (100.0 * len(descartadas) / total) if total else 0.0
    if not vivas:
        print(
            "ERROR: la comprobacion no dejo ninguna entrada viva; no se escribe la lista.",
            file=sys.stderr,
        )
        return 2
    if porcentaje > argumentos.maximo_descartes:
        print(
            f"ERROR: se descartarian {len(descartadas)} entradas ({porcentaje:.1f} %), "
            f"por encima del limite del {argumentos.maximo_descartes:.0f} %.\n"
            "  No se escribe la lista. Revisa la conexion, usa --politica conservadora\n"
            "  o sube el limite con --maximo-descartes 100.",
            file=sys.stderr,
        )
        return 2

    # ---- 4. Orden, deduplicado y escritura ---------------------------------
    if not argumentos.sin_ordenar:
        vivas = ordenar_entradas(vivas)
    antes_de_deduplicar = len(vivas)
    vivas = quitar_duplicados(vivas)
    duplicadas = antes_de_deduplicar - len(vivas)

    cabecera_final = preparar_cabecera(cabecera, argumentos.epg_publica)
    salida = (argumentos.salida or ARCHIVO_ENTRADA_POR_DEFECTO).strip()
    aviso(f"4/5 Escribiendo {salida}")
    try:
        escribir_lista(salida, cabecera_final, vivas)
    except OSError as error:
        print(f"ERROR: no se pudo escribir {salida}: {error}", file=sys.stderr)
        return 1

    # ---- 5. Resumen --------------------------------------------------------
    informe = construir_informe(
        origen=descripcion_origen,
        salida=salida,
        politica="sin verificar" if argumentos.sin_verificar else argumentos.politica,
        entradas_origen=total,
        entradas_finales=len(vivas),
        duplicadas=duplicadas,
        resultados=resultados,
        conteo=conteo,
        descartadas=descartadas,
        duracion=time.monotonic() - inicio,
    )
    if argumentos.informe:
        try:
            escribir_informe(argumentos.informe, informe)
        except OSError as error:
            print(
                f"AVISO: no se pudo escribir el informe {argumentos.informe}: {error}",
                file=sys.stderr,
            )
    if not argumentos.silencioso:
        print()
        for linea in formatear_resumen(informe):
            print(linea)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nOperacion cancelada por el usuario.", file=sys.stderr)
        sys.exit(130)
