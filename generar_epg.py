#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 generar_epg.py  |  Generador de EPG (XMLTV) a partir de una lista M3U remota
===============================================================================

Descarga una lista M3U, extrae de cada canal el nombre (texto que va despues de
la ultima coma del #EXTINF) y sus atributos ``tvg-id``, ``tvg-name`` y
``tvg-logo``, unifica las senales repetidas (SD / HD / FHD / [Opc.2] / pais...)
y genera un ``epg.xml`` en formato XMLTV con 24 horas de programacion generica
por canal, para que la guia nunca aparezca vacia en el reproductor IPTV.

Caracteristicas
---------------
* Solo librerias estandar de Python (3.9+): urllib.request, re,
  xml.etree.ElementTree, datetime, argparse, unicodedata, html.
* Descarga con User-Agent de navegador, reintentos y deteccion de codificacion.
* El ``tvg-id`` del M3U tiene prioridad como ``<channel id="...">`` (es lo que
  compara el reproductor). Se descarta el sufijo ``@calidad/feed`` para agrupar
  las variantes de un mismo canal; ``--conservar-tvg-id`` lo respeta literal.
* Limpieza de nombres -> un ``id`` unico cuando no hay ``tvg-id`` valido.
* Varios ``<display-name>`` por canal (nombre limpio, ``tvg-name`` y nombre
  original del M3U) para que el emparejamiento por nombre tambien funcione.
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
URL_GENERADOR = os.environ.get("EPG_REPO_URL", "https://github.com/jggalviz/guiadecanales")

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
                    "tvg_name": (atributos.get("tvg-name") or "").strip(),
                    "url": linea,
                }
            )
        pendiente = None

    return canales


# =============================================================================
# 4. LIMPIEZA DE NOMBRES Y GENERACION DE IDS UNICOS
# =============================================================================

#: Marcadores de "senal duplicada": [Opc.2], (Opcion 3), Alternativa 2, Senal 2,
#: Feed 4, Servidor 2, Camara 1...
_RE_OPCION = re.compile(
    r"\b(?:opc(?:i[oó]n)?\.?\s*\d+|\d+\s*opc(?:i[oó]n)?|opc(?:i[oó]n)?|"
    r"alt(?:ernativ[oa])?\.?\s*\d*|respaldo|backup|espejo|mirror|"
    r"feed\s*\d*|se[ñn]al\s*\d+|server\s*\d*|servidor\s*\d*|link\s*\d+|"
    r"c[áa]mara\s*\d*|canal\s*alterno)\b",
    re.IGNORECASE,
)

#: Indicadores de calidad que no forman parte del nombre del canal.
#: Se eliminan solo como palabra completa ("Canal 5 HD" -> "Canal 5"), para no
#: romper nombres legitimos como "Cine Latino", "Discovery World" o
#: "ESPN Premium" (que es el nombre real de un canal, no una marca de calidad).
_RE_CALIDAD = re.compile(
    r"\b(?:FULL\s*HD|HIGH\s*DEFINITION|FHD|UHD|QHD|HD|SD|4K|8K|HEVC|HDR|"
    r"H\.?26[45]|AVC|X26[45]|\d{3,4}\s*[PI]|\d{2,3}\s*FPS|AAC|AC3|DOLBY)\b",
    re.IGNORECASE,
)

#: Etiquetas que solo se descartan cuando aparecen aisladas entre corchetes,
#: por ejemplo "[LAT]", "(Español)" o "[Multi]".
_RE_ETIQUETA_GRUPO = re.compile(
    r"(?:LATINO|LATAM|LAT|ESPA[ÑN]OL|CASTELLANO|SUB(?:TITULADO)?|DUAL|MULTI|"
    r"ORIGINAL|VOSE|VOD|VIP|IPV6|PROXY)",
    re.IGNORECASE,
)

#: Calidad pegada al nombre sin espacio: "Canal5HD" -> "Canal5",
#: "TNT Sports ArgentinaHD" -> "TNT Sports Argentina".
_RE_SUFIJO_PEGADO = re.compile(
    r"(?:FULLHD|FHD|UHD|HD|SD|4K|8K|HEVC|HDR|(?:1080|720|576|480|360)[PI]|\d{2}FPS)(?:\d)?$",
    re.IGNORECASE,
)

#: Grupos entre corchetes, parentesis o llaves: [Opc.2], (1080p), [LAT]...
_RE_GRUPO = re.compile(r"[\[\(\{]\s*([^\[\]\(\)\{\}]{0,60}?)\s*[\]\)\}]")

#: Simbolos decorativos habituales en las listas IPTV.
_RE_ADORNOS = re.compile(r"[★☆•·▪►▶◄|]+")
_RE_ESPACIOS = re.compile(r"\s+")

#: Contenido de un grupo que solo indica region o numero: (MX), [ES], [2].
_RE_CODIGO_REGION = re.compile(r"(?:[A-Z]{2,3}|\d{1,2})")

#: Paises y regiones que las listas anaden al final del nombre ("TNT Sports
#: Chile", "Fox Sports - Argentina", "(Mexico)"). Se eliminan solo al final y
#: nunca si el nombre quedaria en una sola palabra: asi "Cine Latino" o
#: "Mega Chile" conservan su identidad.
_REGIONES = (
    "latinoamerica", "centroamerica", "sudamerica", "caribe",
    "latam", "latino", "lat", "andino",
    "mexico", "méxico", "mx", "argentina", "arg", "ar",
    "chile", "cl", "colombia", "co", "peru", "perú", "pe",
    "venezuela", "ve", "ecuador", "ec", "uruguay", "uy",
    "paraguay", "py", "bolivia", "bo", "brasil", "brazil", "br",
    "espana", "españa", "spain", "es", "portugal", "pt",
    "panama", "panamá", "pa", "costa rica", "cr", "guatemala", "gt",
    "honduras", "hn", "el salvador", "sv", "nicaragua", "ni",
    "republica dominicana", "república dominicana", "dominican republic", "do",
    "puerto rico", "pr", "cuba", "cu", "haiti", "haití", "ht",
    "estados unidos", "united states", "usa", "us", "canada", "canadá", "ca",
    "reino unido", "united kingdom", "uk", "inglaterra", "gb",
    "francia", "france", "fr", "italia", "italy", "it", "alemania", "germany", "de",
    "holanda", "netherlands", "nl", "belgica", "bélgica", "be", "suiza", "ch",
    "suecia", "se", "noruega", "no", "dinamarca", "dk", "polonia", "pl",
    "rusia", "russia", "ru", "turquia", "turquía", "tr", "india", "in",
    "japon", "japón", "jp", "china", "cn", "corea", "korea", "kr", "australia", "au",
)

#: Region escrita al final del nombre, con separador opcional (- / |).
_RE_REGION_FINAL = re.compile(
    r"(?:[\s\-\u2013|]+)(?:" + "|".join(re.escape(r) for r in _REGIONES) + r")\s*$",
    re.IGNORECASE,
)

#: Region entre corchetes o parentesis al final: "(Chile)", "[Argentina]".
_RE_REGION_GRUPO = re.compile(
    r"[\s\-]*[\[\(]\s*(?:" + "|".join(re.escape(r) for r in _REGIONES) + r")\s*[\]\)]\s*$",
    re.IGNORECASE,
)

#: Un ``tvg-id`` utilizable: sin espacios y con caracteres admitidos por los
#: reproductores (los ids de iptv-org son del tipo ``ESPN2.us@HD``).
_RE_TVG_ID_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+#/@-]{0,63}$")

#: Valores que algunos proveedores usan como "canal sin EPG".
_TVG_ID_INVALIDOS = {
    "0", "-", "none", "null", "n/a", "na", "unknown", "tv", "canal",
    "sin-epg", "sinepg", "no-epg", "x", "***",
}


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


def _region_final(nombre: str) -> str:
    """Devuelve la region o pais escrito al final del nombre ('' si no hay).

    Se usa para distinguir dos canales que solo se diferencian en el pais
    ("TNT Sports Chile" frente a "TNT Sports Argentina") y no fusionarlos por
    error.
    """
    for patron in (_RE_REGION_GRUPO, _RE_REGION_FINAL):
        coincidencia = patron.search(nombre)
        if coincidencia:
            texto = re.sub(r"[^\wáéíóúüñ ]", "", coincidencia.group(0))
            return _RE_ESPACIOS.sub(" ", texto).strip().lower()
    return ""


def _quitar_region_final(nombre: str) -> str:
    """Descarta el pais/region del final si el nombre sigue siendo reconocible.

    Solo se elimina cuando queda un nombre de dos o mas palabras, de modo que
    "TNT Sports Chile" -> "TNT Sports" pero "Cine Latino", "Mega Chile" o
    "ESPN Chile" se conservan intactos.
    """
    for patron in (_RE_REGION_GRUPO, _RE_REGION_FINAL):
        coincidencia = patron.search(nombre)
        if not coincidencia:
            continue
        base = nombre[: coincidencia.start()].strip(" -\u2013\u2014_:;,./*#°")
        if len(_RE_ESPACIOS.sub(" ", base).split()) >= 2:
            return base
    return nombre


def limpiar_nombre(nombre: str, quitar_region: bool = True) -> str:
    """Unifica el nombre de un canal eliminando duplicados y marcas de calidad.

    Ejemplos::

        "Canal 5 HD"                        -> "Canal 5"
        "Canal 5 FHD [Opc.2]"               -> "Canal 5"
        "Las Estrellas [Opc.2]"             -> "Las Estrellas"
        "Mega Noticias (1080p)"             -> "Mega Noticias"
        "Cine Latino SD"                    -> "Cine Latino"
        "ESPN 2 HD [LAT]"                   -> "ESPN 2"
        "ESPN Premium HD (1080p)"           -> "ESPN Premium"
        "TNT Sports ArgentinaHD (1080p)"    -> "TNT Sports"
        "Cine Latino"                       -> "Cine Latino"  (no se toca)

    Con ``quitar_region=False`` se conserva el pais/region del final, que es la
    variante "estricta" que se usa cuando dos senales del mismo nombre solo se
    diferencian en el pais.
    """
    limpio = _RE_GRUPO.sub(_reemplazo_grupo, nombre)
    limpio = _RE_OPCION.sub(" ", limpio)
    limpio = _RE_CALIDAD.sub(" ", limpio)
    limpio = _RE_ESPACIOS.sub(" ", limpio).strip()
    limpio = _quitar_sufijo_pegado(limpio)
    limpio = _RE_ADORNOS.sub(" ", limpio)
    limpio = _RE_ESPACIOS.sub(" ", limpio).strip()
    limpio = limpio.strip(" -\u2013\u2014_:;,./*#°")
    limpio = _RE_ESPACIOS.sub(" ", limpio).strip()
    if quitar_region:
        limpio = _quitar_region_final(limpio)
        limpio = _RE_ESPACIOS.sub(" ", limpio).strip(" -\u2013\u2014_:;,./*#°")
    return limpio or nombre.strip()


def generar_id(nombre: str) -> str:
    """Crea el id unico de la senal: minusculas sin espacios, acentos ni simbolos.

    Ejemplos: "Canal 5" -> "canal5", "Mega Noticias | HD" -> "meganoticias".
    """
    descompuesto = unicodedata.normalize("NFKD", nombre)
    sin_acentos = "".join(caracter for caracter in descompuesto if not unicodedata.combining(caracter))
    identificador = re.sub(r"[^a-z0-9]+", "", sin_acentos.lower())
    return identificador or "canal"


def _normalizar_tvg_id(valor: str, conservar: bool = False) -> str:
    """Valida y normaliza el ``tvg-id`` del M3U (``''`` si no es utilizable).

    Muchos proveedores (y el propio iptv-org) escriben la calidad o el feed
    despues de una arroba: ``ESPN2.us@HD``, ``ESPN2.us@SD``,
    ``ESPNLatinAmerica.us@Argentina``. El id canonico del canal es la parte
    anterior a ``@``, asi que por defecto se descarta ese sufijo para que todas
    las variantes SD/HD/FHD terminen en una sola entrada de la guia.

    Con ``conservar=True`` se respeta el valor literal, para listas cuyo
    reproductor empareja por el ``tvg-id`` exacto.
    """
    limpio = _RE_ESPACIOS.sub(" ", valor or "").strip()
    if not limpio or limpio.lower() in _TVG_ID_INVALIDOS:
        return ""
    if not conservar and "@" in limpio:
        limpio = limpio.split("@", 1)[0].strip()
    if not _RE_TVG_ID_VALIDO.fullmatch(limpio):
        return ""
    return limpio


def consolidar_canales(
    canales: Iterable[dict[str, str]],
    usar_tvg_id: bool = True,
    conservar_tvg_id: bool = False,
) -> list[dict[str, str]]:
    """Limpia los nombres, unifica senales repetidas y asigna un id unico.

    Reglas de identificacion, por orden de prioridad:

    1. Si la entrada trae un ``tvg-id`` valido, ese es el ``id`` del canal (es
       lo que el reproductor usa para emparejar la guia con la lista). Las
       variantes de calidad de un mismo id (``ESPN2.us@HD`` / ``ESPN2.us@SD``)
       se agrupan en una sola entrada.
    2. Si no hay ``tvg-id``, el id se genera a partir del nombre limpio
       (``ESPN 2 HD [LAT]`` -> ``espn2``).

    El pais del final del nombre se descarta ("TNT Sports Chile" -> "TNT
    Sports"), salvo que dos senales del mismo nombre solo se diferencien en el
    pais: en ese caso se conserva para no fusionar canales distintos.
    """
    registros: list[dict[str, str]] = []
    for canal in canales:
        nombre_original = canal.get("nombre", "")
        amplio = limpiar_nombre(nombre_original, quitar_region=True)
        estricto = limpiar_nombre(nombre_original, quitar_region=False)
        registros.append(
            {
                "nombre_amplio": amplio,
                "nombre_estricto": estricto,
                "clave_amplia": generar_id(amplio),
                "region": _region_final(estricto),
                "logo": canal.get("logo", ""),
                "grupo": canal.get("grupo", ""),
                "tvg_id": (
                    _normalizar_tvg_id(canal.get("tvg_id", ""), conservar=conservar_tvg_id)
                    if usar_tvg_id
                    else ""
                ),
                "tvg_name": canal.get("tvg_name", ""),
                "nombre_m3u": nombre_original.strip(),
            }
        )

    # Canales que solo se diferencian en el pais: no se pueden fusionar.
    # Hay conflicto cuando un mismo nombre base aparece con dos paises
    # distintos, o cuando una senal lleva pais y otra del mismo nombre no.
    regiones_por_clave: dict[str, set[str]] = {}
    sin_region_por_clave: set[str] = set()
    for registro in registros:
        if registro["region"]:
            regiones_por_clave.setdefault(registro["clave_amplia"], set()).add(registro["region"])
        else:
            sin_region_por_clave.add(registro["clave_amplia"])
    en_conflicto = {
        clave
        for clave, regiones in regiones_por_clave.items()
        if len(regiones) > 1 or clave in sin_region_por_clave
    }

    unicos: dict[str, dict[str, str]] = {}
    id_por_nombre: dict[str, str] = {}

    for registro in registros:
        if registro["clave_amplia"] in en_conflicto:
            nombre_final = registro["nombre_estricto"]
        else:
            nombre_final = registro["nombre_amplio"]
        id_nombre = generar_id(nombre_final)

        if registro["tvg_id"]:
            clave = registro["tvg_id"]
            origen = "tvg-id"
        else:
            # Sin tvg-id: si el nombre ya pertenece a un canal registrado
            # (que si tenia tvg-id), se reutiliza esa entrada.
            clave = id_por_nombre.get(id_nombre, id_nombre)
            origen = "tvg-id" if clave in unicos and unicos[clave]["origen"] == "tvg-id" else "nombre"

        existente = unicos.get(clave)
        if existente is not None:
            if not existente["logo"] and registro["logo"]:
                existente["logo"] = registro["logo"]
            if not existente["grupo"] and registro["grupo"]:
                existente["grupo"] = registro["grupo"]
            if not existente["tvg_name"] and registro["tvg_name"]:
                existente["tvg_name"] = registro["tvg_name"]
            continue

        unicos[clave] = {
            "id": clave,
            "nombre": nombre_final,
            "logo": registro["logo"],
            "tvg_id": registro["tvg_id"],
            "tvg_name": registro["tvg_name"],
            "grupo": registro["grupo"],
            "nombre_m3u": registro["nombre_m3u"],
            "origen": origen,
        }
        id_por_nombre.setdefault(id_nombre, clave)

    return list(unicos.values())


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


def _nombres_alternativos(canal: Mapping[str, str]) -> list[str]:
    """Alias que ayudan al reproductor a emparejar la guia con la lista.

    Se publican como ``<display-name>`` adicionales: el ``tvg-name`` del M3U y
    el nombre original de la entrada (``ESPN 2 HD [LAT]``), que es justo el que
    muestran muchos reproductores al emparejar por nombre.
    """
    principal = canal.get("nombre", "")
    alias: list[str] = []
    for candidato in (canal.get("tvg_name", ""), canal.get("nombre_m3u", "")):
        candidato = (candidato or "").strip()
        if not candidato:
            continue
        if candidato.lower() == principal.lower():
            continue
        if any(candidato.lower() == previo.lower() for previo in alias):
            continue
        alias.append(candidato)
    return alias


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

    # 1) Canales: <channel id="tvg-id|id-generado"> con nombres e icono.
    for canal in canales:
        nodo_canal = ET.SubElement(raiz, "channel", {"id": canal["id"]})
        ET.SubElement(nodo_canal, "display-name", {"lang": "es"}).text = canal["nombre"]
        for alias in _nombres_alternativos(canal):
            ET.SubElement(nodo_canal, "display-name").text = alias
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
            "  python generar_epg.py -u \"$M3U_URL\" -s epg.xml\n"
            "  python generar_epg.py -u \"$M3U_URL\" --sin-tvg-id       # ids desde el nombre\n"
            "  python generar_epg.py -u \"$M3U_URL\" --conservar-tvg-id  # tvg-id literal (@HD)\n"
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
        "--sin-tvg-id",
        action="store_true",
        help="Ignorar el tvg-id del M3U y generar los ids desde el nombre limpio.",
    )
    parser.add_argument(
        "--conservar-tvg-id",
        action="store_true",
        help=(
            "Respetar el tvg-id literal (incluido el sufijo @HD/@SD). Por defecto "
            "se descarta ese sufijo para agrupar las variantes de calidad."
        ),
    )
    parser.add_argument(
        "--usar-tvg-id",
        action="store_true",
        help=(
            "(obsoleto) Antes activaba el uso del tvg-id. Ahora es el comportamiento "
            "por defecto; use --sin-tvg-id para desactivarlo."
        ),
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
    aviso("[3/5] Limpiando nombres (SD/HD/FHD/[Opc.N]/pais) y generando ids ...")
    canales = consolidar_canales(
        canales_brutos,
        usar_tvg_id=not argumentos.sin_tvg_id,
        conservar_tvg_id=argumentos.conservar_tvg_id,
    )
    con_tvg_id = sum(1 for canal in canales if canal["origen"] == "tvg-id")
    aviso(f"      Canales unicos tras unificar senales: {len(canales)}")
    aviso(
        f"      Ids tomados del tvg-id del M3U: {con_tvg_id} | "
        f"generados desde el nombre: {len(canales) - con_tvg_id}"
    )

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
        aviso(f"   - {canal['id']:<24} {canal['nombre']}  [{marca_logo} / {canal['origen']}]")
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

