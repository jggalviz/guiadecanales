#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 generar_epg.py  |  Generador de EPG (XMLTV) a partir de una lista M3U remota
===============================================================================

Descarga una lista M3U, extrae de cada canal el nombre (texto que va despues de
la ultima coma del #EXTINF) y su atributo ``tvg-logo``, unifica las senales
repetidas (SD / HD / FHD / [Opc.2] ...) y genera un ``epg.xml`` en formato
XMLTV con 24 horas de programacion generica por canal, para que la guia nunca
aparezca vacia en el reproductor IPTV.

Caracteristicas
---------------
* Solo librerias estandar de Python (3.9+): urllib.request, re,
  xml.etree.ElementTree, datetime, argparse, unicodedata, html.
* Descarga con User-Agent de navegador, reintentos y deteccion de codificacion.
* Limpieza de nombres -> un ``id`` unico por senal (solo letras y numeros).
* 6 bloques continuos de 4 horas (24 h) con titulos realistas en espanol.
* Salida UTF-8, declaracion XML, DOCTYPE xmltv.dtd e indentacion limpia.

Uso rapido
----------
    python generar_epg.py --demo          # prueba sin conexion (datos de ejemplo)
    python generar_epg.py --url "https://raw.githubusercontent.com/USUARIO/REPO/RAMA/lista.m3u"
    python generar_epg.py                 # usa la constante URL_M3U o la variable M3U_URL
    python generar_epg.py -h              # ayuda completa

Variables de entorno
--------------------
    M3U_URL        URL de la lista M3U (tiene prioridad sobre la constante).
    EPG_SALIDA     Ruta del archivo de salida (por defecto: epg.xml).
    EPG_REPO_URL   URL del repositorio que se publica en <tv generator-info-url>.

Autor: guiadecanales
Licencia: MIT
===============================================================================
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

# =============================================================================
# 1. CONFIGURACION
# =============================================================================

#: URL de la lista M3U.
#: IMPORTANTE: debe ser el enlace *raw* del archivo (texto plano con #EXTM3U),
#: por ejemplo:
#:     https://raw.githubusercontent.com/USUARIO/REPOSITORIO/main/lista.m3u
#: Un enlace del tipo ``https://githubusercontent.com`` devuelve HTML y no una
#: lista M3U, por eso el script avisa cuando detecta un valor de ejemplo.
URL_M3U = os.environ.get(
    "M3U_URL",
    "https://raw.githubusercontent.com/usuario/repositorio/main/lista.m3u",
)

#: Nombre del archivo XMLTV que se genera.
ARCHIVO_SALIDA = os.environ.get("EPG_SALIDA", "epg.xml")

#: Identificacion del generador dentro de la etiqueta <tv>.
NOMBRE_GENERADOR = "guiadecanales-epg"
URL_GENERADOR = os.environ.get("EPG_REPO_URL", "https://github.com/usuario/repositorio")

#: Parametros de red.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT_SEGUNDOS = 30
REINTENTOS = 3

#: Formato de fecha exigido por XMLTV: YYYYMMDDHHMMSS +0000
FORMATO_FECHA_XMLTV = "%Y%m%d%H%M%S"
DESFASE_UTC = "+0000"

#: Duracion de cada bloque de programacion (6 bloques x 4 h = 24 h).
HORAS_BLOQUE = 4

#: Bloques de 24 horas con sus titulos candidatos (se elige uno por canal para
#: que la parrilla no sea identica en todas las senales).
BLOQUES_24H = (
    {
        "hora": 0,
        "categoria": "Madrugada",
        "titulos": (
            "Cine de Madrugada",
            "Insomnio Musical",
            "Noticiero de Madrugada",
            "Series de Medianoche",
        ),
    },
    {
        "hora": 4,
        "categoria": "Matutino",
        "titulos": (
            "Noticiero Matutino",
            "Primera Hora Informativa",
            "Buenos Dias con Nosotros",
            "Rutina de Ejercicio Matutino",
        ),
    },
    {
        "hora": 8,
        "categoria": "Variedades",
        "titulos": (
            "Magazine Matutino",
            "Cocina y Hogar",
            "Consultorio de la Salud",
            "Aventura y Viajes",
        ),
    },
    {
        "hora": 12,
        "categoria": "Tarde",
        "titulos": (
            "Noticiero del Mediodia",
            "Cine Familiar de la Tarde",
            "Telenovela de la Tarde",
            "Documental de la Tarde",
        ),
    },
    {
        "hora": 16,
        "categoria": "Vespertino",
        "titulos": (
            "Deportes en Vivo",
            "Noticiero Vespertino",
            "Serie de la Tarde",
            "Concurso de la Tarde",
        ),
    },
    {
        "hora": 20,
        "categoria": "Estelar",
        "titulos": (
            "Bloque Estelar Nocturno",
            "Pelicula de la Noche",
            "Late Night Show",
            "Noticiero de la Noche",
        ),
    },
)

#: Datos de ejemplo usados con ``--demo`` (permiten probar el script sin red y
#: sin exponer una URL privada). Incluye senales duplicadas a proposito.
M3U_DE_EJEMPLO = """#EXTM3U
#EXTINF:-1 tvg-id="canal5.mx" tvg-name="Canal 5" tvg-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/1/1e/Canal_5_logo.svg/240px-Canal_5_logo.svg.png" group-title="Nacional",Canal 5 HD
#EXTVLCOPT:http-user-agent=MiPlayer/1.0
http://ejemplo.invalid/canal5/index.m3u8
#EXTINF:-1 tvg-id="canal5.mx" tvg-name="Canal 5" tvg-logo="" group-title="Nacional",Canal 5 FHD [Opc.2]
http://ejemplo.invalid/canal5-opcion2/index.m3u8
#EXTINF:-1 tvg-id="lasestrellas.mx" tvg-name="Las Estrellas" tvg-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/Las_Estrellas_logo.svg/240px-Las_Estrellas_logo.svg.png" group-title="Nacional",Las Estrellas [Opc.2]
http://ejemplo.invalid/lasestrellas/index.m3u8
#EXTINF:-1 tvg-id="cinelatino" tvg-name="Cine Latino" tvg-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/2/26/Cine_Latino_logo.svg/240px-Cine_Latino_logo.svg.png" group-title="Cine",Cine Latino FHD
http://ejemplo.invalid/cinelatino/index.m3u8
#EXTINF:-1 tvg-id="cinelatino" tvg-name="Cine Latino" group-title="Cine",Cine Latino SD
http://ejemplo.invalid/cinelatino-sd/index.m3u8
#EXTINF:-1 tvg-id="meganoticias" tvg-name="Mega Noticias" tvg-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Meganoticias_logo.svg/240px-Meganoticias_logo.svg.png" group-title="Noticias",Mega Noticias (1080p)
http://ejemplo.invalid/meganoticias/index.m3u8
#EXTINF:-1 tvg-id="deportes247" tvg-name="Deportes 24/7" tvg-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/9/9b/Deportes_247_logo.svg/240px-Deportes_247_logo.svg.png" group-title="Deportes",Deportes 24/7 UHD
http://ejemplo.invalid/deportes247/index.m3u8
#EXTINF:-1 tvg-id="" tvg-name="Canal Local" group-title="Local",Canal Local SD
http://ejemplo.invalid/canallocal/index.m3u8
"""


# =============================================================================
# 2. DESCARGA DEL ARCHIVO M3U
# =============================================================================

def _decodificar(datos: bytes) -> str:
    """Convierte los bytes descargados en texto probando las codificaciones habituales."""
    for codificacion in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


def descargar_m3u(
    url: str,
    timeout: int = TIMEOUT_SEGUNDOS,
    reintentos: int = REINTENTOS,
) -> str:
    """Descarga la lista M3U remota y devuelve su contenido como texto.

    Se usan cabeceras de navegador porque muchos proveedores rechazan el
    User-Agent por defecto de urllib. Si la descarga falla, se reintenta con
    una espera creciente antes de abortar.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise SystemExit(
            "La URL de la lista M3U no es valida.\n"
            f"  Valor recibido: {url!r}\n"
            "Debe empezar por http:// o https:// y apuntar al archivo M3U."
        )

    cabeceras = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }

    ultimo_error: Exception | None = None
    for intento in range(1, reintentos + 1):
        try:
            peticion = urllib.request.Request(url, headers=cabeceras)
            with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
                datos = respuesta.read()
            if not datos:
                raise ValueError("la respuesta esta vacia")
            return _decodificar(datos)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as error:
            ultimo_error = error
            print(f"  ! Intento {intento}/{reintentos} fallido: {error}", file=sys.stderr)
            if intento < reintentos:
                time.sleep(1.5 * intento)

    raise SystemExit(
        "No se pudo descargar la lista M3U.\n"
        f"  URL  : {url}\n"
        f"  Error: {ultimo_error}\n"
        "Revisa la conexion, que el repositorio sea publico y que el enlace sea "
        "el 'raw' del archivo (no la pagina del repositorio)."
    )


# =============================================================================
# 3. ANALISIS DEL ARCHIVO M3U
# =============================================================================

#: Atributos de la linea #EXTINF del tipo nombre="valor".
_RE_ATRIBUTOS = re.compile(r'([A-Za-z0-9_.:-]+)\s*=\s*"([^"]*)"')


def _extraer_nombre(linea_extinf: str) -> str:
    """Devuelve el nombre del canal: el texto que va despues de la ultima coma.

    En el formato M3U el nombre aparece al final de la linea ``#EXTINF``::

        #EXTINF:-1 tvg-id="x" tvg-logo="y" group-title="z",Nombre del Canal

    Si el nombre contiene comas, se respeta el bloque posterior a las comillas
    de los atributos (que es el comportamiento correcto del estandar M3U).
    """
    resto = linea_extinf.split(":", 1)[1] if ":" in linea_extinf else linea_extinf
    posicion_comilla = resto.rfind('"')
    if posicion_comilla != -1 and "," in resto[posicion_comilla:]:
        nombre = resto[posicion_comilla + 1:].split(",", 1)[1]
    elif "," in resto:
        nombre = resto.rsplit(",", 1)[1]
    else:
        nombre = ""
    return html.unescape(nombre).strip()


def parsear_m3u(texto: str) -> list[dict[str, str]]:
    """Extrae de la lista M3U la informacion de cada canal.

    Devuelve una lista de diccionarios con las claves ``nombre``, ``logo``,
    ``grupo``, ``tvg_id`` y ``url``. Se ignoran los comentarios (#EXTM3U,
    #EXTVLCOPT, #EXTGRP...) y las lineas sin ``#EXTINF`` previo.
    """
    canales: list[dict[str, str]] = []
    pendiente: str | None = None

    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if linea.upper().startswith("#EXTINF"):
            pendiente = linea
            continue
        if linea.startswith("#"):
            continue
        if pendiente is None:
            continue

        nombre = _extraer_nombre(pendiente)
        atributos = {clave.lower(): valor for clave, valor in _RE_ATRIBUTOS.findall(pendiente)}
        if nombre:
            canales.append(
                {
                    "nombre": nombre,
                    "logo": (atributos.get("tvg-logo") or atributos.get("logo") or "").strip(),
                    "grupo": atributos.get("group-title", "").strip(),
                    "tvg_id": atributos.get("tvg-id", "").strip(),
                    "url": linea,
                }
            )
        pendiente = None

    return canales


# =============================================================================
# 4. LIMPIEZA DE NOMBRES Y GENERACION DE IDS UNICOS
# =============================================================================

#: Marcadores de "senal duplicada": [Opc.2], (Opcion 3), Alternativa 2, Senal 2...
_RE_OPCION = re.compile(
    r"\b(?:opc(?:i[oó]n)?\.?\s*\d+|\d+\s*opc(?:i[oó]n)?|opc(?:i[oó]n)?|"
    r"alt(?:ernativ[oa])?\.?\s*\d*|respaldo|backup|espejo|mirror|"
    r"se[ñn]al\s*\d+|canal\s*alterno)\b",
    re.IGNORECASE,
)

#: Indicadores de calidad que no forman parte del nombre del canal.
#: Se eliminan solo como palabra completa ("Canal 5 HD" -> "Canal 5"), para no
#: romper nombres legitimos como "Cine Latino" o "Discovery World".
_RE_CALIDAD = re.compile(
    r"\b(?:FHD|UHD|QHD|HD|SD|4K|8K|HEVC|H\.?26[45]|AVC|X26[45]|"
    r"(?:1080|720|576|480|360)[PI]|\d{2}\s*FPS|VIP|PREMIUM)\b",
    re.IGNORECASE,
)

#: Etiquetas que solo se descartan cuando aparecen aisladas entre corchetes,
#: por ejemplo "[LAT]", "(Español)" o "[Multi]".
_RE_ETIQUETA_GRUPO = re.compile(
    r"(?:LATINO|LATAM|LAT|ESPA[ÑN]OL|CASTELLANO|SUB(?:TITULADO)?|DUAL|MULTI|"
    r"ORIGINAL|VOSE|VOD)",
    re.IGNORECASE,
)

#: Calidad pegada al nombre sin espacio: "Canal5HD" -> "Canal5".
_RE_SUFIJO_PEGADO = re.compile(
    r"(?:FHD|UHD|HD|SD|4K|HEVC|1080P|720P|OPC\d+)$",
    re.IGNORECASE,
)

#: Grupos entre corchetes, parentesis o llaves: [Opc.2], (1080p), [LAT]...
_RE_GRUPO = re.compile(r"[\[\(\{]\s*([^\[\]\(\)\{\}]{0,60}?)\s*[\]\)\}]")

#: Simbolos decorativos habituales en las listas IPTV.
_RE_ADORNOS = re.compile(r"[★☆•·▪►▶◄|]+")
_RE_ESPACIOS = re.compile(r"\s+")

#: Contenido de un grupo que solo indica region o numero: (MX), [ES], [2].
_RE_CODIGO_REGION = re.compile(r"(?:[A-Z]{2,3}|\d{1,2})")


def _reemplazo_grupo(coincidencia: "re.Match[str]") -> str:
    """Decide si el contenido de un grupo entre corchetes se elimina o se conserva."""
    contenido = coincidencia.group(1).strip()
    if not contenido:
        return " "
    if (
        _RE_CALIDAD.fullmatch(contenido)
        or _RE_OPCION.fullmatch(contenido)
        or _RE_ETIQUETA_GRUPO.fullmatch(contenido)
        or _RE_CODIGO_REGION.fullmatch(contenido)
    ):
        return " "
    return f" {contenido} "


def _quitar_sufijo_pegado(nombre: str) -> str:
    """Elimina indicadores de calidad unidos al nombre, sin dejarlo demasiado corto."""
    coincidencia = _RE_SUFIJO_PEGADO.search(nombre)
    if not coincidencia or coincidencia.start() < 3:
        return nombre
    return nombre[: coincidencia.start()].rstrip()


def limpiar_nombre(nombre: str) -> str:
    """Unifica el nombre de un canal eliminando duplicados y marcas de calidad.

    Ejemplos::

        "Canal 5 HD"                -> "Canal 5"
        "Canal 5 FHD [Opc.2]"       -> "Canal 5"
        "Las Estrellas [Opc.2]"     -> "Las Estrellas"
        "Mega Noticias (1080p)"     -> "Mega Noticias"
        "Cine Latino SD"            -> "Cine Latino"
    """
    limpio = _RE_GRUPO.sub(_reemplazo_grupo, nombre)
    limpio = _RE_OPCION.sub(" ", limpio)
    limpio = _RE_CALIDAD.sub(" ", limpio)
    limpio = _quitar_sufijo_pegado(limpio)
    limpio = _RE_ADORNOS.sub(" ", limpio)
    limpio = _RE_ESPACIOS.sub(" ", limpio).strip()
    limpio = limpio.strip(" -–—_:;,./*#°")
    limpio = _RE_ESPACIOS.sub(" ", limpio).strip()
    return limpio or nombre.strip()


def generar_id(nombre: str) -> str:
    """Crea el id unico de la senal: minusculas sin espacios, acentos ni simbolos.

    Ejemplos: "Canal 5" -> "canal5", "Mega Noticias | HD" -> "meganoticias".
    """
    descompuesto = unicodedata.normalize("NFKD", nombre)
    sin_acentos = "".join(caracter for caracter in descompuesto if not unicodedata.combining(caracter))
    identificador = re.sub(r"[^a-z0-9]+", "", sin_acentos.lower())
    return identificador or "canal"


def _normalizar_tvg_id(valor: str) -> str:
    """Limpia el tvg-id original del M3U (solo se usa con --usar-tvg-id)."""
    return re.sub(r"\s+", "", valor.strip())


def consolidar_canales(
    canales: Iterable[dict[str, str]],
    usar_tvg_id: bool = False,
) -> list[dict[str, str]]:
    """Limpia los nombres, unifica senales repetidas y asigna un id unico.

    Dos entradas del M3U que solo difieren en la calidad o en el marcador de
    opcion ("Canal 5 HD" y "Canal 5 FHD [Opc.2]") se consideran la misma senal:
    se conserva la primera aparicion y se completa el logo si faltaba.
    """
    unicos: dict[str, dict[str, str]] = {}
    orden: list[str] = []

    for canal in canales:
        nombre_limpio = limpiar_nombre(canal["nombre"])
        clave = generar_id(nombre_limpio)
        existente = unicos.get(clave)
        if existente is not None:
            if not existente["logo"] and canal["logo"]:
                existente["logo"] = canal["logo"]
            continue
        unicos[clave] = {
            "id": clave,
            "nombre": nombre_limpio,
            "logo": canal["logo"],
            "tvg_id": canal["tvg_id"],
            "grupo": canal["grupo"],
        }
        orden.append(clave)

    resultado: list[dict[str, str]] = []
    ids_usados: set[str] = set()
    for clave in orden:
        canal = dict(unicos[clave])
        if usar_tvg_id and canal["tvg_id"]:
            canal["id"] = _normalizar_tvg_id(canal["tvg_id"]) or clave
        base = canal["id"]
        sufijo = 2
        while canal["id"] in ids_usados:
            canal["id"] = f"{base}{sufijo}"
            sufijo += 1
        ids_usados.add(canal["id"])
        resultado.append(canal)

    return resultado


# =============================================================================
# 5. CONSTRUCCION DEL XMLTV
# =============================================================================

def _sello(fecha: datetime) -> str:
    """Devuelve la fecha en el formato XMLTV: YYYYMMDDHHMMSS +0000."""
    return f"{fecha.strftime(FORMATO_FECHA_XMLTV)} {DESFASE_UTC}"


def _inicio_del_dia(fecha: date | None = None) -> datetime:
    """Punto de partida de la guia: la fecha actual del sistema a las 00:00:00.

    El instante se marca como UTC (+0000) porque asi lo exige XMLTV y asi lo
    interpretan los reproductores IPTV.
    """
    fecha = fecha or datetime.now().date()
    return datetime(fecha.year, fecha.month, fecha.day, 0, 0, 0, tzinfo=timezone.utc)


def _semilla(canal_id: str) -> int:
    """Semilla estable (sin random) para variar la parrilla entre canales."""
    return sum(ord(caracter) for caracter in canal_id)


def construir_xmltv(
    canales: Sequence[Mapping[str, str]],
    inicio: datetime,
) -> tuple[ET.Element, int]:
    """Construye el arbol XMLTV con los canales y sus 24 h de programacion.

    Devuelve la raiz ``<tv>`` y el numero total de bloques ``<programme>``
    generados (6 por canal).
    """
    raiz = ET.Element(
        "tv",
        {
            "generator-info-name": NOMBRE_GENERADOR,
            "generator-info-url": URL_GENERADOR,
        },
    )

    # 1) Canales: <channel> con <display-name> y, si existe, <icon src="...">.
    for canal in canales:
        nodo_canal = ET.SubElement(raiz, "channel", {"id": canal["id"]})
        ET.SubElement(nodo_canal, "display-name", {"lang": "es"}).text = canal["nombre"]
        logo = canal["logo"]
        if logo and logo.lower().startswith(("http://", "https://")):
            ET.SubElement(nodo_canal, "icon", {"src": logo})

    # 2) Parrilla generica: 6 bloques continuos de 4 horas = 24 h por canal.
    total_programas = 0
    for canal in canales:
        semilla = _semilla(canal["id"])
        for indice, bloque in enumerate(BLOQUES_24H):
            comienzo = inicio + timedelta(hours=int(bloque["hora"]))
            final = comienzo + timedelta(hours=HORAS_BLOQUE)
            titulos: Sequence[str] = bloque["titulos"]
            titulo = titulos[(semilla + indice) % len(titulos)]

            programa = ET.SubElement(
                raiz,
                "programme",
                {
                    "start": _sello(comienzo),
                    "stop": _sello(final),
                    "channel": canal["id"],
                },
            )
            ET.SubElement(programa, "title", {"lang": "es"}).text = f"{titulo} - {canal['nombre']}"
            ET.SubElement(programa, "desc", {"lang": "es"}).text = (
                f"Bloque de programacion de {HORAS_BLOQUE} horas de {canal['nombre']} "
                f"({bloque['categoria']}). Contenido generico de referencia para "
                "mantener la guia activa en el reproductor IPTV."
            )
            ET.SubElement(programa, "category", {"lang": "es"}).text = str(bloque["categoria"])
            total_programas += 1

    return raiz, total_programas


def _indentar(elemento: ET.Element, nivel: int = 0, espacio: str = "  ") -> None:
    """Aplica una indentacion limpia al arbol XML (equivalente a ET.indent)."""
    sangria = "\n" + espacio * nivel
    hijos = list(elemento)
    if not hijos:
        return
    if not elemento.text or not elemento.text.strip():
        elemento.text = sangria + espacio
    for posicion, hijo in enumerate(hijos):
        _indentar(hijo, nivel + 1, espacio)
        if not hijo.tail or not hijo.tail.strip():
            hijo.tail = sangria if posicion == len(hijos) - 1 else sangria + espacio


def escribir_xmltv(raiz: ET.Element, destino: str, incluir_doctype: bool = True) -> int:
    """Escribe el archivo XMLTV en UTF-8 con declaracion XML e indentacion.

    Devuelve el tamano en bytes del archivo escrito.
    """
    _indentar(raiz)
    cuerpo = ET.tostring(raiz, encoding="unicode").replace(" />", "/>")

    lineas = ['<?xml version="1.0" encoding="UTF-8"?>']
    if incluir_doctype:
        lineas.append('<!DOCTYPE tv SYSTEM "xmltv.dtd">')
    lineas.append(cuerpo)
    contenido = "\n".join(lineas) + "\n"

    with open(destino, "w", encoding="utf-8", newline="\n") as archivo:
        archivo.write(contenido)

    return len(contenido.encode("utf-8"))


# =============================================================================
# 6. INTERFAZ DE LINEA DE COMANDOS
# =============================================================================

def _es_url_placeholder(url: str) -> bool:
    """Detecta si la URL sigue siendo el valor de ejemplo del script."""
    if not url:
        return True
    if "usuario/repositorio" in url or "USUARIO/REPO" in url:
        return True
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host in ("", "githubusercontent.com", "www.githubusercontent.com")


def _crear_parser() -> argparse.ArgumentParser:
    """Define las opciones de linea de comandos del script."""
    parser = argparse.ArgumentParser(
        prog="generar_epg.py",
        description=(
            "Genera una guia EPG en formato XMLTV (epg.xml) a partir de una lista "
            "M3U remota, con 24 h de programacion generica por canal."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python generar_epg.py --demo\n"
            "  python generar_epg.py --url https://raw.githubusercontent.com/USUARIO/REPO/main/lista.m3u\n"
            "  python generar_epg.py -u \"$M3U_URL\" -s epg.xml --usar-tvg-id\n"
        ),
    )
    parser.add_argument(
        "-u", "--url",
        default=URL_M3U,
        help="URL 'raw' de la lista M3U (por defecto: constante URL_M3U o variable M3U_URL).",
    )
    parser.add_argument(
        "-s", "--salida",
        default=ARCHIVO_SALIDA,
        help="Ruta del archivo XMLTV de salida (por defecto: epg.xml).",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=TIMEOUT_SEGUNDOS,
        help=f"Tiempo maximo de espera de la descarga en segundos (por defecto: {TIMEOUT_SEGUNDOS}).",
    )
    parser.add_argument(
        "-f", "--fecha",
        default=None,
        help="Fecha de inicio de la guia en formato YYYY-MM-DD (por defecto: hoy).",
    )
    parser.add_argument(
        "--usar-tvg-id",
        action="store_true",
        help="Usar el tvg-id del M3U como id del canal (util para emparejar con la lista).",
    )
    parser.add_argument(
        "--sin-doctype",
        action="store_true",
        help="No escribir la linea DOCTYPE xmltv.dtd.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Generar la EPG con la lista M3U de ejemplo, sin descargar nada.",
    )
    parser.add_argument(
        "-q", "--silencioso",
        action="store_true",
        help="Mostrar solo los errores.",
    )
    return parser


# =============================================================================
# 7. PROGRAMA PRINCIPAL
# =============================================================================

def main(argv: list[str] | None = None) -> int:
    """Ejecuta el flujo completo: descarga -> analisis -> limpieza -> XMLTV."""
    argumentos = _crear_parser().parse_args(argv)
    hablador = not argumentos.silencioso

    def aviso(mensaje: str = "") -> None:
        if hablador:
            print(mensaje)

    aviso("=" * 70)
    aviso(" Generador de EPG XMLTV  |  guiadecanales")
    aviso("=" * 70)

    # --- 1. Origen de datos -------------------------------------------------
    if argumentos.demo:
        aviso("[1/5] Usando la lista M3U de ejemplo (--demo), sin descargar nada.")
        texto_m3u = M3U_DE_EJEMPLO
        origen = "lista de ejemplo (--demo)"
    else:
        url = (argumentos.url or "").strip()
        if _es_url_placeholder(url):
            raise SystemExit(
                "Debes indicar la URL real de tu lista M3U.\n"
                f"  Valor actual: {url!r}\n\n"
                "La URL tiene que ser el enlace 'raw' del archivo, por ejemplo:\n"
                "  https://raw.githubusercontent.com/USUARIO/REPOSITORIO/main/lista.m3u\n\n"
                "Puedes pasarla de tres formas:\n"
                "  1) python generar_epg.py --url \"https://raw.githubusercontent.com/...\"\n"
                "  2) variable de entorno M3U_URL\n"
                "  3) editando la constante URL_M3U al inicio de este archivo\n\n"
                "Nota: 'https://githubusercontent.com' no es una lista M3U valida "
                "(sirve HTML, no el archivo). Para probar sin conexion usa --demo."
            )
        aviso(f"[1/5] Descargando la lista M3U desde {url} ...")
        texto_m3u = descargar_m3u(url, timeout=argumentos.timeout)
        origen = url

    # --- 2. Analisis del M3U ------------------------------------------------
    aviso("[2/5] Analizando el archivo M3U (nombre + tvg-logo) ...")
    canales_brutos = parsear_m3u(texto_m3u)
    if not canales_brutos:
        raise SystemExit(
            "No se encontro ningun canal valido en la lista M3U.\n"
            "Comprueba que el contenido descargado contenga lineas #EXTINF:-1 ..."
        )
    aviso(f"      Entradas #EXTINF encontradas: {len(canales_brutos)}")

    # --- 3. Limpieza y deduplicacion ---------------------------------------
    aviso("[3/5] Limpiando nombres (SD/HD/FHD/[Opc.N]) y generando ids unicos ...")
    canales = consolidar_canales(canales_brutos, usar_tvg_id=argumentos.usar_tvg_id)
    aviso(f"      Canales unicos tras unificar senales: {len(canales)}")

    # --- 4. Generacion del XMLTV -------------------------------------------
    if argumentos.fecha:
        try:
            fecha_inicio = datetime.strptime(argumentos.fecha, "%Y-%m-%d").date()
        except ValueError as error:
            raise SystemExit(
                "La fecha indicada no es valida. Usa el formato YYYY-MM-DD "
                f"(ejemplo: {datetime.now():%Y-%m-%d})."
            ) from error
    else:
        fecha_inicio = datetime.now().date()

    inicio = _inicio_del_dia(fecha_inicio)
    aviso(f"[4/5] Generando 24 h de programacion desde {inicio:%Y-%m-%d 00:00:00} {DESFASE_UTC} ...")
    raiz, total_programas = construir_xmltv(canales, inicio)

    # --- 5. Escritura y validacion -----------------------------------------
    destino = os.path.abspath(argumentos.salida)
    aviso(f"[5/5] Escribiendo {destino} ...")
    try:
        tamano = escribir_xmltv(raiz, destino, incluir_doctype=not argumentos.sin_doctype)
        validado = ET.parse(destino).getroot()
    except OSError as error:
        raise SystemExit(f"No se pudo escribir el archivo de salida: {error}") from error
    except ET.ParseError as error:
        raise SystemExit(f"El XML generado no es valido: {error}") from error

    if validado.tag != "tv":
        raise SystemExit("El XMLTV generado no tiene la etiqueta raiz <tv>.")

    aviso("")
    aviso("-" * 70)
    aviso(" RESUMEN")
    aviso("-" * 70)
    aviso(f" Origen              : {origen}")
    aviso(f" Entradas M3U        : {len(canales_brutos)}")
    aviso(f" Canales unicos      : {len(canales)}")
    aviso(f" Bloques <programme> : {total_programas} ({len(canales)} x {len(BLOQUES_24H)} bloques)")
    aviso(f" Archivo generado    : {destino}")
    aviso(f" Tamano              : {tamano / 1024:.1f} KB")
    aviso(f" XML valido          : si (leido de nuevo con ElementTree)")
    aviso("")
    aviso(" Primeros canales generados:")
    for canal in canales[:10]:
        marca_logo = "logo si" if canal["logo"] else "sin logo"
        aviso(f"   - {canal['id']:<24} {canal['nombre']}  [{marca_logo}]")
    if len(canales) > 10:
        aviso(f"   ... y {len(canales) - 10} canales mas.")
    aviso("")
    aviso(" Listo. Sube 'epg.xml' a GitHub (o publica con GitHub Pages) y apunta")
    aviso(" tu reproductor IPTV a la URL del archivo.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nOperacion cancelada por el usuario.", file=sys.stderr)
        sys.exit(130)

