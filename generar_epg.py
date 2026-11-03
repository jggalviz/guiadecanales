#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 generar_epg.py  |  Generador de EPG (XMLTV) a partir de una lista M3U remota
===============================================================================

Descarga una lista M3U, extrae de cada canal el nombre (texto que va despues de
la ultima coma del #EXTINF) y sus atributos ``tvg-id``, ``tvg-name`` y
``tvg-logo``, unifica las senales repetidas (SD / HD / FHD / [Opc.2] / pais...)
y genera un ``epg.xml`` en formato XMLTV con la **programacion real** descargada
de guias XMLTV publicas (por defecto una guia por pais de epgshare01).

Ademas publica ``lista.m3u``: una lista propia (ordenada, con atributos limpios,
``tvg-id`` sincronizado con la guia y todas las senales de respaldo) que se puede
usar directamente en OTT Navigator, VLC, TiviMate, IPTV Smarters...

Flujo de la programacion
------------------------
1. Se descargan las guias XMLTV configuradas (``--epg``, ``--epg-pais``,
   ``--epg-archivo`` o las de por defecto).
2. Cada canal del M3U se empareja con el canal de la guia real usando el
   ``tvg-id``, su id normalizado o el nombre normalizado (sin sufijos de
   calidad, sin grupos entre parentesis y sin el pais).
3. Se publican los ``<programme>`` reales de cada canal emparejado dentro de la
   ventana pedida (``--horas-ventana``, 72 h por defecto).
4. Los canales sin coincidencia se publican sin programas (no se inventa
   contenido). Con ``--relleno`` reciben la parrilla generica de 24 h. Si
   ninguna guia se puede usar, la guia se rellena con esa parrilla para que el
   reproductor nunca se quede sin datos.

Flujo de la lista propia
------------------------
1. Un **mapeo explicito** (``mapeo_canales.json`` + :data:`MAPEO_CANALES_POR_DEFECTO`)
   fija el ``tvg-id`` real de las senales dificiles: ``ESPN 2 HD``,
   ``ESPN 2 (Opc.2)`` y ``ESPN2 HD - (Opc.3)`` acaban todas en ``ESPN2.us``.
2. Los **duplicados/espejos no se eliminan**: cada senal (``Opc.2``, ``Backup``,
   ``Espejo``...) se publica como una entrada propia con el ``tvg-id`` y el
   ``tvg-name`` de su canal principal, asi que muestra la misma programacion.
3. Se ordenan los canales por grupo y nombre y se limpian los atributos
   (``tvg-id``, ``tvg-name``, ``tvg-logo`` solo si es http(s), ``group-title``),
   conservando los ``#EXTVLCOPT``/``#KODIPROP`` de la lista original.
4. La cabecera declara la guia publicada (``x-tvg-url``/``url-tvg``) y la salida
   se escribe en ``lista.m3u`` junto al ``epg.xml``.

Caracteristicas
---------------
* Solo librerias estandar de Python (3.9+): urllib.request, re, gzip, io, json,
  xml.etree.ElementTree, datetime, argparse, unicodedata, html.
* Descarga con User-Agent de navegador, reintentos, soporte de gzip y deteccion
  de codificacion.
* Saneado de mojibake al leer la lista: ``MÚxico`` -> ``México``, ``Espa±a`` ->
  ``España``, ``Panamß`` -> ``Panamá`` y ``MÃ©xico`` -> ``México`` (ver
  :func:`sanear_codificacion`), sin alterar los nombres correctos.
* El ``tvg-id`` del M3U tiene prioridad como ``<channel id="...">`` (es lo que
  compara el reproductor). Se descarta el sufijo ``@calidad/feed`` para agrupar
  las variantes de un mismo canal; ``--conservar-tvg-id`` lo respeta literal.
* Limpieza de nombres -> un ``id`` unico cuando no hay ``tvg-id`` valido.
* Varios ``<display-name>`` por canal (nombre limpio, ``tvg-name`` y nombre
  original del M3U) para que el emparejamiento por nombre tambien funcione.
* Emparejamiento por puntaje (id exacto, id normalizado, nombre contiguo) con
  preferencia por el mismo pais y sin reutilizar un canal de la guia dos veces.
* Salida UTF-8, declaracion XML, DOCTYPE xmltv.dtd e indentacion limpia.

Uso rapido
----------
    python generar_epg.py --demo          # prueba sin conexion (M3U + EPG de ejemplo)
    python generar_epg.py --url "https://raw.githubusercontent.com/USUARIO/REPO/RAMA/lista.m3u"
    python generar_epg.py                 # usa la constante URL_M3U o la variable M3U_URL
    python generar_epg.py -u "$M3U_URL" --epg-pais MX,ES,AR   # guias reales por pais
    python generar_epg.py -u "$M3U_URL" --mapeo mapeo_canales.json
    python generar_epg.py -u "$M3U_URL" --epg https://ejemplo.com/guia.xml.gz
    python generar_epg.py -h              # ayuda completa

Variables de entorno
--------------------
    M3U_URL            URL de la lista M3U (tiene prioridad sobre la constante).
    M3U_SALIDA         Ruta de la lista M3U propia (por defecto: lista.m3u).
    M3U_MAPEO          JSON con el mapeo explicito (por defecto: mapeo_canales.json).
    EPG_SALIDA         Ruta del archivo de salida (por defecto: epg.xml).
    EPG_REPO_URL       URL del repositorio que se publica en <tv generator-info-url>.
    EPG_PUBLICA_URL    URL publica del epg.xml que se declara en la cabecera del M3U.
    EPG_URL            Fuentes EPG reales separadas por comas (URL o ruta local).
    EPG_PAISES         Paises de las guias de por defecto (por defecto MX,ES,AR,...).
    EPG_URL_PLANTILLA  Plantilla de la guia por pais (``{pais}`` -> codigo ISO).
    EPG_HORAS          Horas de programacion real que se conservan (72).

Autor: guiadecanales
Licencia: MIT
===============================================================================
"""

from __future__ import annotations

import argparse
import gzip
import html
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
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

# -----------------------------------------------------------------------------
# EPG real (XMLTV externo)
# -----------------------------------------------------------------------------

#: Plantilla de las guias XMLTV publicas por pais. La de por defecto son las de
#: epgshare01 (XMLTV comprimido en gzip, actualizado a diario y con las cadenas
#: de television de cada pais). ``{pais}`` se sustituye por el codigo ISO de dos
#: letras en mayusculas (MX, ES, AR, CO, CL, PE...). Cualquier otra fuente
#: XMLTV (open-epg, i.mjh.nz, la guia del proveedor...) se puede indicar con
#: ``--epg`` o con la variable de entorno ``EPG_URL``.
PLANTILLA_EPG_PAIS = os.environ.get(
    "EPG_URL_PLANTILLA",
    "https://epgshare01.online/epgshare01/epg_ripper_{pais}1.xml.gz",
)

#: Paises cuyas guias reales se descargan cuando no se indica otra fuente.
#: Se pueden cambiar con ``--epg-pais`` o con la variable ``EPG_PAISES``.
#: Orden pensado para que ganen las guias mas especificas (Latinoamerica y
#: Espana) y quede al final la guia de EE. UU., que es la mas generica.
PAISES_EPG_POR_DEFECTO = tuple(
    codigo.strip().upper()
    for codigo in os.environ.get("EPG_PAISES", "").split(",")
    if codigo.strip()
) or (
    "MX", "ES", "AR", "CO", "CL", "PE", "EC", "UY", "CR", "PA", "DO", "SV",
    "PT", "IT", "DE", "FR", "US",
)

#: Guias por pais que no siguen :data:`PLANTILLA_EPG_PAIS`.
#: ``US`` no existe en epgshare01 (devuelve 404), asi que se usa la guia
#: publica de Estados Unidos de epg.pw; asi ``--epg-pais ...,US`` funciona.
GUIAS_EPG_POR_PAIS = {
    "US": os.environ.get("EPG_URL_US", "https://epg.pw/xmltv/epg_US.xml"),
}

#: Fuentes extra que se suman a las guias por pais (no cuando se indica
#: ``--epg``/``--epg-archivo``/``EPG_URL``). Son los canales FAST de EE. UU.
#: (Pluto TV, Samsung TV Plus y Roku) que muchas listas M3U incluyen.
#: Se pueden cambiar con ``EPG_URLS_EXTRA`` (vacio las desactiva) o con
#: ``--sin-extras``.
FUENTES_EPG_EXTRA_POR_DEFECTO = tuple(
    parte.strip()
    for parte in os.environ.get(
        "EPG_URLS_EXTRA",
        "https://i.mjh.nz/PlutoTV/all.xml.gz,"
        "https://i.mjh.nz/SamsungTVPlus/us.xml.gz,"
        "https://i.mjh.nz/Roku/all.xml.gz",
    ).split(",")
    if parte.strip()
)

#: Fuentes EPG adicionales indicadas por entorno (URL o ruta local, separadas
#: por comas). Si esta vacia se usan las guias por pais de arriba.
URL_EPG = os.environ.get("EPG_URL", "").strip()


def _entero_entorno(nombre: str, defecto: int) -> int:
    """Lee un entero de una variable de entorno y cae al valor por defecto."""
    try:
        return max(0, int(os.environ.get(nombre, str(defecto))))
    except (TypeError, ValueError):
        return defecto


#: Horas de programacion real que se conservan desde el inicio de la guia.
HORAS_GUIA_REAL = _entero_entorno("EPG_HORAS", 72)

#: Tamano maximo (ya descomprimido) que se acepta de una guia externa. La guia
#: de EE. UU. (epg.pw) ronda los 160 MB sin comprimir, por eso el limite es alto.
MAX_BYTES_EPG = 200 * 1024 * 1024

#: Puntaje minimo (0 a 1) para aceptar un emparejamiento por nombre.
#: 1.0 = nombres identicos; 0.75 admite como maximo un cuarto de tokens extra
#: ("Canal 5" frente a "Canal 5 de Mexico"), pero nunca "ESPN" frente a "ESPN 2".
PUNTAJE_MINIMO_EMPAREJAMIENTO = 0.75

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


# -----------------------------------------------------------------------------
# Mapeo explicito de canales y lista M3U propia
# -----------------------------------------------------------------------------

#: Mapeo explicito ``tvg-id real de la guia -> nombre/alias del canal``.
#:
#: Sirve para las senales que el emparejamiento automatico no puede resolver
#: (o resuelve mal): varias entradas de la lista que en realidad son el mismo
#: canal con distinto nombre ("ESPN 2 HD", "ESPN 2 (Opc.2)",
#: "ESPN2 HD - (Opc.3)") acaban todas en el ``tvg-id`` canonico de la guia
#: (``ESPN2.us``), que es lo que el reproductor usa para pedir la programacion.
#:
#: Los ids son los de iptv-org/epgshare01 (``Canal5.mx``, ``ESPN2.us``...), que
#: son los mismos que publican las guias XMLTV. Se pueden añadir o cambiar
#: canales en ``mapeo_canales.json`` sin tocar el codigo (ver
#: :func:`cargar_mapeo_canales`).
MAPEO_CANALES_POR_DEFECTO: dict[str, dict[str, object]] = {
    "Canal5.mx": {
        "nombre": "Canal 5",
        "alias": ("Canal 5", "Canal5", "Canal 5 Nacional"),
    },
    "LasEstrellas.mx": {
        "nombre": "Las Estrellas",
        "alias": (
            "Las Estrellas",
            "Las Estrellas Nacional",
            "Las Estrellas Nacional v1",
            "Las Estrellas Nacional V2",
        ),
    },
    "Azteca7.mx": {
        "nombre": "Azteca 7",
        "alias": ("Azteca 7", "Azteca Siete", "Azteca 7 Nacional"),
    },
    "AztecaUno.mx": {
        "nombre": "Azteca Uno",
        "alias": ("Azteca Uno", "Azteca 1", "Azteca Uno Nacional"),
    },
    "ESPN.us": {
        "nombre": "ESPN",
        "alias": ("ESPN",),
    },
    "ESPN2.us": {
        "nombre": "ESPN 2",
        "alias": (
            "ESPN 2",
            "ESPN2",
            "ESPN 2 (Opc.2)",
            "ESPN2 HD - (Opc.3)",
            "ESPN 2 (Opc.3)",
            "ESPN 2 (Opc.4)",
        ),
    },
    "ESPNDeportes.us": {
        "nombre": "ESPN Deportes",
        "alias": ("ESPN Deportes", "ESPN Deportes HD"),
    },
    "ESPNLatinAmerica.us": {
        "nombre": "ESPN Latin America",
        "alias": ("ESPN Latin America", "ESPN Latinoamerica"),
    },
}

#: Archivo JSON opcional que amplia :data:`MAPEO_CANALES_POR_DEFECTO`. Se busca
#: por defecto junto al script (raiz del repositorio) y se puede cambiar con
#: ``--mapeo`` o con la variable de entorno ``M3U_MAPEO``.
ARCHIVO_MAPEO_POR_DEFECTO = "mapeo_canales.json"

#: Nombre del archivo M3U propio que se genera junto al ``epg.xml``. Se puede
#: cambiar con ``--m3u-salida`` o con la variable de entorno ``M3U_SALIDA``.
LISTA_M3U_POR_DEFECTO = os.environ.get("M3U_SALIDA", "").strip() or "lista.m3u"

#: URL publica de la guia que se declara en la cabecera del M3U
#: (``x-tvg-url``/``url-tvg``) para que el reproductor descargue la EPG solo.
#: Se puede cambiar con ``--epg-publica`` o con ``EPG_PUBLICA_URL``.
URL_EPG_PUBLICA = os.environ.get("EPG_PUBLICA_URL", "").strip() or (
    URL_GENERADOR.rstrip("/") + "/raw/main/epg.xml"
)

#: Prefix que se añade al nombre visible de las senales de respaldo (espejos,
#: ``Opc.2``, ``Backup``...) para distinguirlas en el reproductor sin cambiar el
#: ``tvg-id`` ni el ``tvg-name`` con el que se pide la programacion.
ETIQUETA_SENAL_SECUNDARIA = "Opc. {numero}"


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


# --- Saneado de codificacion (mojibake) ------------------------------------
#
# Muchas listas IPTV se generan en cp1252/latin-1 y en algun punto se leen como
# cp850/cp437, dejando los acentos convertidos en simbolos: "México" aparece
# como "MÚxico", "España" como "Espa±a", "Panamá" como "Panamß", "Visión" como
# "Visi¾n", "Cúcuta" como "C·cuta" o "Amazonía" como "AmazonÝa". Tambien es
# habitual el doble codificado de UTF-8 visto como cp1252 ("MÃ©xico").
# Como esas secuencias son reversibles, se detectan y se reparan al leer la
# lista, justo antes de limpiar los nombres.

#: Caracteres que nunca aparecen en un nombre de canal bien codificado: son el
#: resultado de leer bytes cp1252 como cp850 (ß<-á, ±<-ñ, ·<-ú, ¾<-ó, Ý<-í...).
#: No incluye "°" ni "ª"/"º" porque son legitimos ("90° TV", "1º Canal").
_MOJIBAKE_SIEMPRE = frozenset(
    "ßµþÞ¯´≡±‗¾¶§÷¸¨·¹³²■¤⌐"
    "░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐"
)

#: Mayusculas acentuadas que en espanol solo son validas al inicio de palabra:
#: "MÚxico" o "AmazonÝa" las delatan, mientras "Único TV" o "Última Hora" no.
_MOJIBAKE_MAYUSCULA = frozenset("ÓÚÛÙÝÃÅÇ")

#: UTF-8 leido como cp1252: "MÃ©xico", "EspaÃ±a", "â€“", "Â¿".
_RE_MOJIBAKE_UTF8 = re.compile(
    r"Ã[\u0080-\u00bf]|Â[\u0080-\u00bf]"
    r"|â€[\u0080-\u00bf\u2013\u2014\u2018\u2019\u201c\u201d\u20ac\u2122]"
)

#: Palabras de la lista (se sanean una a una para no perder el resto de linea).
_RE_PALABRA = re.compile(r"\S+")


def _tiene_mojibake_cp850(palabra: str) -> bool:
    """Indica si la palabra parece cp1252 leido como cp850."""
    if any(caracter in _MOJIBAKE_SIEMPRE for caracter in palabra):
        return True
    # Una palabra escrita toda en mayusculas puede contener "Ú" legitima
    # ("PERÚ"), asi que esa pista solo se usa en texto con minusculas.
    if palabra.isupper():
        return False
    return any(
        caracter in _MOJIBAKE_MAYUSCULA and indice > 0
        for indice, caracter in enumerate(palabra)
    )


def _reparar_utf8(palabra: str) -> str:
    """Revierte el UTF-8 leido como cp1252/latin-1 (``MÃ©xico`` -> ``México``)."""
    for codificacion in ("cp1252", "latin-1"):
        try:
            return palabra.encode(codificacion).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return palabra


def _reparar_cp850(palabra: str) -> str:
    """Revierte el cp1252 leido como cp850 (``MÚxico`` -> ``México``)."""
    try:
        return palabra.encode("cp850").decode("cp1252")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return palabra


def _sanear_palabra(palabra: str) -> str:
    """Intenta reparar una sola palabra, validando que el resultado mejora."""
    # Los separadores y simbolos sueltos ("·", "|", "¾") no se tocan: un "·"
    # legitimo como separador jamas debe convertirse en "ú".
    if not any(caracter.isalnum() for caracter in palabra):
        return palabra

    if _RE_MOJIBAKE_UTF8.search(palabra):
        candidato = _reparar_utf8(palabra)
        if candidato != palabra and not _RE_MOJIBAKE_UTF8.search(candidato):
            palabra = candidato

    if _tiene_mojibake_cp850(palabra):
        candidato = _reparar_cp850(palabra)
        if candidato != palabra and not _tiene_mojibake_cp850(candidato):
            palabra = candidato

    return palabra


def sanear_codificacion(texto: str) -> str:
    """Corrige el mojibake habitual de las listas M3U antes de analizarlas.

    Repara los dos casos mas frecuentes sin tocar el texto que ya esta bien:

    * ``cp1252`` leido como ``cp850``/``cp437`` (el mas comun en listas IPTV
      generadas en Windows)::

          "Azteca 7 MÚxico"      -> "Azteca 7 México"
          "Anime Visi¾n Espa±a"  -> "Anime Visión España"
          "Hispania TV Panamß"   -> "Hispania TV Panamá"
          "NSTV C·cuta"          -> "NSTV Cúcuta"

    * UTF-8 leido como ``cp1252`` (doble codificado)::

          "Azteca 7 MÃ©xico"     -> "Azteca 7 México"
          "PanamÃ¡ TV"           -> "Panamá TV"

    La funcion es idempotente y conserva los acentos correctos, las mayusculas
    iniciales ("Único TV", "Última Hora") y los simbolos legitimos ("90° TV").
    Si una palabra no se puede reparar con seguridad, se deja tal cual.
    """
    if not texto:
        return texto
    return _RE_PALABRA.sub(
        lambda coincidencia: _sanear_palabra(coincidencia.group(0)), texto
    )


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


def parsear_m3u(texto: str) -> list[dict[str, object]]:
    """Extrae de la lista M3U la informacion de cada canal.

    Devuelve una lista de diccionarios con las claves ``nombre``, ``logo``,
    ``grupo``, ``tvg_id``, ``tvg_name``, ``opciones`` y ``url``. Se ignoran los
    comentarios (#EXTM3U...) y las lineas sin ``#EXTINF`` previo.

    ``opciones`` guarda las lineas de opciones de la entrada
    (``#EXTVLCOPT:http-user-agent=...``, ``#KODIPROP:...``, ``#EXTGRP:...``),
    que se publican tal cual en la lista propia para no romper los canales que
    necesitan un user-agent o un referer concreto.

    Antes de analizar el texto se aplica :func:`sanear_codificacion`, de modo
    que los nombres con mojibake (``MÚxico``, ``Espa±a``, ``Panamß``,
    ``MÃ©xico``...) lleguen corregidos al resto del proceso.
    """
    texto = sanear_codificacion(texto)
    canales: list[dict[str, object]] = []
    pendiente: str | None = None
    opciones: list[str] = []

    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if linea.upper().startswith("#EXTINF"):
            pendiente = linea
            opciones = []
            continue
        if linea.startswith("#"):
            # Opciones de la entrada pendiente (#EXTVLCOPT, #KODIPROP...).
            if pendiente is not None:
                opciones.append(linea)
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
                    "opciones": tuple(opciones),
                    "url": linea,
                }
            )
        pendiente = None
        opciones = []

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


#: Calificadores de señal que no identifican al canal y que, por tanto, se
#: ignoran al buscar en el mapeo explicito: "Canal 5 Nacional" y
#: "Las Estrellas Nacional V2" son el mismo canal que "Canal 5" y
#: "Las Estrellas" (el calificador solo indica la señal o el servidor).
_RE_CALIFICADOR_SENAL = re.compile(
    r"\b(?:nacional|regional|internacional|local|principal|altern[oa]|"
    r"espejo|mirror|backup|respaldo|se[ñn]al|feed|servidor|server|"
    r"v\d+|version\s*\d+)\b",
    re.IGNORECASE,
)


def _clave_mapeo(texto: str, sin_calificadores: bool = False) -> str:
    """Clave normalizada de un alias del mapeo explicito.

    Se calcula igual que el nombre limpio **sin** quitar el pais, de modo que
    ``"ESPN 2 (Opc.2)"`` -> ``espn2`` (el marcador de opcion se descarta) pero
    ``"ESPN 2 Netherlands"`` -> ``espn2netherlands`` y no roba el canal de
    ESPN 2 de Estados Unidos.

    Con ``sin_calificadores=True`` se descartan ademas los calificadores de
    senal (:data:`_RE_CALIFICADOR_SENAL`): ``"Canal 5 Nacional"`` -> ``canal5``.
    Ese es el segundo intento de la busqueda, para que las variantes
    "Nacional", "Regional", "v1", "v2"... tambien encuentren su canal.
    """
    limpio = limpiar_nombre(str(texto or ""), quitar_region=False)
    if sin_calificadores:
        limpio = _RE_CALIFICADOR_SENAL.sub(" ", limpio)
        limpio = _RE_ESPACIOS.sub(" ", limpio).strip()
    return generar_id(limpio)


def _entrada_mapeo(identificador: str, datos: object) -> dict[str, object]:
    """Normaliza una entrada del mapeo (``{id: datos}``) a la forma interna.

    Formatos admitidos en el JSON:

    * ``"ESPN2.us": ["ESPN 2 HD", "ESPN 2 (Opc.2)"]`` (solo alias).
    * ``"ESPN2.us": {"nombre": "ESPN 2", "logo": "...", "alias": [...]}``.
    """
    if isinstance(datos, (list, tuple)):
        datos = {"alias": list(datos)}
    if not isinstance(datos, dict):
        raise ValueError(f"el canal {identificador!r} del mapeo debe ser una lista o un objeto")

    nombre = str(datos.get("nombre") or datos.get("nombre_estandar") or "").strip()
    alias = [
        str(valor).strip()
        for valor in (datos.get("alias") or datos.get("aliases") or ())
        if str(valor).strip()
    ]
    if nombre:
        alias.insert(0, nombre)
    if not alias:
        raise ValueError(f"el canal {identificador!r} del mapeo no tiene nombre ni alias")

    return {
        "id": identificador.strip(),
        "nombre": nombre or alias[0],
        "logo": str(datos.get("logo") or datos.get("tvg_logo") or "").strip(),
        "alias": tuple(dict.fromkeys(alias)),
    }


def cargar_mapeo_canales(ruta: str | None = None) -> dict[str, dict[str, object]]:
    """Devuelve el mapeo explicito de canales: el de por defecto + el del JSON.

    ``ruta`` es el archivo JSON con canales adicionales (por defecto
    :data:`ARCHIVO_MAPEO_POR_DEFECTO`, en la raiz del repositorio). El JSON
    puede traer una clave ``"canales"`` o directamente ``{tvg_id: alias_o_datos}``
    y admite dos formatos por canal::

        {
          "canales": {
            "ESPN2.us": ["ESPN 2 HD", "ESPN 2 (Opc.2)", "ESPN2 HD - (Opc.3)"],
            "LasEstrellas.mx": {
              "nombre": "Las Estrellas",
              "logo": "https://ejemplo.invalid/las-estrellas.png",
              "alias": ["Las Estrellas", "Las Estrellas HD"]
            }
          }
        }

    Los alias del JSON **se suman** a los del mismo ``tvg-id`` definidos en el
    codigo, y el ``nombre``/``logo`` del JSON tienen prioridad. Si el archivo no
    existe se usan solo los canales de por defecto; un JSON invalido lanza
    :class:`ValueError` para que el llamador pueda avisar y continuar.
    """
    mapeo: dict[str, dict[str, object]] = {}
    for identificador, datos in MAPEO_CANALES_POR_DEFECTO.items():
        entrada = _entrada_mapeo(identificador, datos)
        mapeo[str(entrada["id"])] = entrada

    ruta = (ruta if ruta is not None else ARCHIVO_MAPEO_POR_DEFECTO) or ""
    if not ruta or not os.path.isfile(ruta):
        return mapeo

    with open(ruta, "r", encoding="utf-8") as archivo:
        contenido = archivo.read()
    try:
        datos_json = json.loads(contenido)
    except json.JSONDecodeError as error:
        raise ValueError(f"{ruta}: JSON no valido ({error})") from error
    if not isinstance(datos_json, dict):
        raise ValueError(f"{ruta}: se esperaba un objeto JSON con los canales")

    canales_json = datos_json.get("canales", datos_json)
    if not isinstance(canales_json, dict):
        raise ValueError(f"{ruta}: la clave 'canales' debe ser un objeto JSON")

    for identificador, datos in canales_json.items():
        if not isinstance(identificador, str) or identificador.startswith(("$", "_")):
            # Claves de documentacion del JSON (no son canales).
            continue
        creado = _entrada_mapeo(str(identificador), datos)
        clave = str(creado["id"])
        previa = mapeo.get(clave)
        if previa is not None:
            # Los alias se acumulan; el nombre y el logo del JSON mandan si vienen.
            creado["alias"] = tuple(
                dict.fromkeys(list(previa["alias"]) + list(creado["alias"]))  # type: ignore[arg-type]
            )
            creado["nombre"] = (
                str(datos.get("nombre") or datos.get("nombre_estandar") or "").strip()
                if isinstance(datos, dict)
                else ""
            ) or previa["nombre"]
            creado["logo"] = str(creado["logo"] or previa["logo"])
        mapeo[clave] = creado
    return mapeo


def _indice_mapeo(mapeo: "Mapping[str, Mapping[str, object]]") -> dict[str, "Mapping[str, object]"]:
    """Construye ``{clave_normalizada: entrada}`` para buscar alias rapido."""
    indice: dict[str, Mapping[str, object]] = {}
    for entrada in mapeo.values():
        for alias in entrada.get("alias") or ():
            for clave in (
                _clave_mapeo(str(alias)),
                _clave_mapeo(str(alias), sin_calificadores=True),
            ):
                if clave:
                    indice.setdefault(clave, entrada)
        for clave in (
            generar_id(str(entrada.get("nombre", ""))),
            _clave_mapeo(str(entrada.get("nombre", "")), sin_calificadores=True),
        ):
            if clave:
                indice.setdefault(clave, entrada)
    return indice


def buscar_en_mapeo(
    indice: "Mapping[str, Mapping[str, object]]",
    *nombres: str,
) -> "Mapping[str, object] | None":
    """Devuelve la entrada del mapeo que corresponde a alguno de los nombres.

    Se prueba primero la clave exacta (nombre limpio sin calidad ni opcion) y
    despues la variante sin calificadores de señal ("Nacional", "v2"...), de
    modo que una misma señal con distintos sufijos acaba en el mismo canal.
    """
    if not indice:
        return None
    for nombre in nombres:
        if not str(nombre or "").strip():
            continue
        for clave in (
            _clave_mapeo(nombre),
            _clave_mapeo(nombre, sin_calificadores=True),
            generar_id(str(nombre or "")),
        ):
            if clave and clave in indice:
                return indice[clave]
    return None



def consolidar_canales(
    canales: Iterable[Mapping[str, object]],
    usar_tvg_id: bool = True,
    conservar_tvg_id: bool = False,
    mapeo: "Mapping[str, Mapping[str, object]] | None" = None,
) -> list[dict[str, object]]:
    """Limpia los nombres, unifica senales repetidas y asigna un id unico.

    Reglas de identificacion, por orden de prioridad:

    0. Si el nombre coincide con un alias del ``mapeo`` explicito
       (:func:`cargar_mapeo_canales`), se usa su ``tvg-id`` canonico: todas las
       entradas dificiles ("ESPN 2 HD", "ESPN 2 (Opc.2)",
       "ESPN2 HD - (Opc.3)") quedan como ``ESPN2.us`` con el nombre estandar
       ``ESPN 2`` y piden la misma programacion real.
    1. Si la entrada trae un ``tvg-id`` valido, ese es el ``id`` del canal (es
       lo que el reproductor usa para emparejar la guia con la lista). Las
       variantes de calidad de un mismo id (``ESPN2.us@HD`` / ``ESPN2.us@SD``)
       se agrupan en una sola entrada.
    2. Si no hay ``tvg-id``, el id se genera a partir del nombre limpio
       (``ESPN 2 HD [LAT]`` -> ``espn2``).

    El pais del final del nombre se descarta ("TNT Sports Chile" -> "TNT
    Sports"), salvo que dos senales del mismo nombre solo se diferencien en el
    pais: en ese caso se conserva para no fusionar canales distintos.

    Cada canal conserva en ``senales`` **todas** las entradas de la lista
    original que representan el mismo canal (HD, espejos, ``Opc.2``,
    ``Backup``...), con su URL y sus opciones, para que la lista M3U propia las
    publique todas con el mismo ``tvg-id`` en vez de descartarlas.
    """
    indice_mapeo = _indice_mapeo(mapeo) if mapeo else {}
    registros: list[dict[str, object]] = []
    for canal in canales:
        nombre_original = str(canal.get("nombre", "") or "")
        tvg_name_original = str(canal.get("tvg_name", "") or "")
        mapeado = buscar_en_mapeo(
            indice_mapeo,
            nombre_original,
            tvg_name_original,
            str(canal.get("nombre_m3u", "") or ""),
        )
        if mapeado is not None:
            estricto = amplio = str(mapeado["nombre"])
            clave_amplia = generar_id(estricto)
            region = ""
            tvg_id = str(mapeado["id"])
            tvg_name = str(mapeado["nombre"])
            logo = str(mapeado.get("logo") or canal.get("logo") or "")
        else:
            amplio = limpiar_nombre(nombre_original, quitar_region=True)
            estricto = limpiar_nombre(nombre_original, quitar_region=False)
            clave_amplia = generar_id(amplio)
            region = _region_final(estricto)
            tvg_id = (
                _normalizar_tvg_id(str(canal.get("tvg_id", "") or ""), conservar=conservar_tvg_id)
                if usar_tvg_id
                else ""
            )
            tvg_name = tvg_name_original
            logo = str(canal.get("logo", "") or "")

        registros.append(
            {
                "nombre_amplio": amplio,
                "nombre_estricto": estricto,
                "clave_amplia": clave_amplia,
                "region": region,
                "logo": logo,
                "grupo": str(canal.get("grupo", "") or ""),
                "tvg_id": tvg_id,
                "tvg_name": tvg_name,
                "nombre_m3u": nombre_original.strip(),
                "mapeado": mapeado is not None,
                "senal": {
                    "url": str(canal.get("url", "") or ""),
                    "opciones": tuple(canal.get("opciones") or ()),
                    "nombre_m3u": nombre_original.strip(),
                },
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

    unicos: dict[str, dict[str, object]] = {}
    id_por_nombre: dict[str, str] = {}

    def agregar_senal(destino: dict[str, object], senal: Mapping[str, object]) -> None:
        """Añade una señal (URL + opciones) sin repetir la misma URL."""
        url = str(senal.get("url", "") or "")
        if not url:
            return
        lista = destino.setdefault("senales", [])
        if not isinstance(lista, list):
            return
        if any(str(previa.get("url", "")) == url for previa in lista):
            return
        lista.append(dict(senal))

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
            # Las senales de respaldo (Opc.2, espejo, backup...) se conservan:
            # la lista M3U propia las publica todas con el mismo tvg-id.
            agregar_senal(existente, registro["senal"])
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
            "mapeado": bool(registro.get("mapeado")),
            "senales": [],
        }
        agregar_senal(unicos[clave], registro["senal"])
        id_por_nombre.setdefault(id_nombre, clave)

    return list(unicos.values())


# =============================================================================
# 5. EPG REAL (XMLTV EXTERNO) Y EMPAREJAMIENTO CON LOS CANALES DEL M3U
# =============================================================================

#: Etiquetas de ``<programme>`` que se copian de la guia real. El resto de
#: etiquetas propias del proveedor se descarta para mantener el XMLTV limpio.
_ETIQUETAS_PROGRAMA = frozenset(
    {
        "title", "sub-title", "desc", "credits", "date", "category", "keyword",
        "language", "original-language", "country", "episode-num", "icon",
        "rating", "star-rating", "video", "audio", "previously-shown",
        "premiere", "new", "subtitles", "url",
    }
)

#: Palabras que no aportan identidad al comparar nombres de canales. Se
#: eliminan solo como token completo ("Canal 5" y "Canal Azteca Uno" siguen
#: siendo reconocibles, pero "Canal" y "de" dejan de ensuciar la comparacion).
_PALABRAS_SIN_VALOR = frozenset(
    {
        "canal", "channel", "de", "del", "la", "el", "los", "las", "un", "una",
        "en", "y", "e", "al", "por", "con", "the", "and", "tv", "television",
        "senal", "signal", "feed", "live", "vivo", "oficial", "official",
    }
)

#: Codigos de pais o de feed que las guias anaden como token suelto
#: ("ESPN MX", "TNT Sports AR"). Se descartan al comparar nombres porque no
#: aportan identidad: el pais se usa aparte como desempate. Se limita a los
#: mercados habituales de las listas en espanol para no descartar palabras
#: cortas con significado propio.
_CODIGOS_PAIS_TOKENS = frozenset(
    {
        "mx", "us", "ar", "co", "cl", "pe", "ve", "ec", "uy", "py", "bo", "br",
        "es", "pt", "it", "fr", "de", "gb", "uk", "ca", "pa", "cr", "gt", "sv",
        "hn", "ni", "do", "pr", "cu",
    }
)

#: Partes de un nombre compuesto: "Canal 2 (Canal Las Estrellas - XEW)".
#: No se divide por "/" para no romper nombres como "Deportes 24/7".
_RE_PARTES_NOMBRE = re.compile(r"[\[\]\(\)\{\}|\u2013\u2014]|\s+-\s+")

#: Token de comparacion: secuencia de letras o digitos ya normalizada.
_RE_TOKEN_NOMBRE = re.compile(r"[a-z0-9]+")

#: ``CANAL+`` es una marca francesa (``CANAL+LIVE 12``), no la palabra "canal"
#: seguida de un simbolo: si se separa, ``CANAL+LIVE 12`` queda como
#: ``("canal", "12")`` y roba los programas de "Canal 12", "CANAL 8 (Television
#: Chichicasteca)"/... de la lista. Se unifica a ``canalplus`` antes de tokenizar.
_RE_MARCA_CANAL_PLUS = re.compile(r"canal\s*(?:\+|plus)", re.IGNORECASE)

#: Nombres genericos de la guia francesa: Telerama publica canales sueltos
#: llamados "Canal 11", "Canal 21" o "Canal 32" que solo emiten el bucle de
#: Canal+ (titulos "Rediffusion en boucle"). Sin este filtro le roban la
#: programacion a canales como "CANAL 32 (Television Chichicasteca)". Solo se
#: aplica a la guia de Francia para no tocar los "Canal NN" reales de otros
#: paises ("Canal 7 de Mexico", "Canal 9" de Panama...).
_RE_GUIA_GENERICA_FR = re.compile(r"canal\s*\d+\s*$", re.IGNORECASE)

#: Marcas de calidad que, solas, no identifican ningun canal ("Canal HD").
_TOKENS_CALIDAD = frozenset({"hd", "fhd", "uhd", "qhd", "sd", "4k", "8k", "hdr", "hevc"})

#: Nombres de pais o region normalizados a un solo token ("Costa Rica" ->
#: "costarica"). Sirven para descartar variantes que no identifican al canal:
#: en "Canal Sony (Mexico)", la parte "(Mexico)" no debe emparejar con
#: cualquier canal mexicano.
_TOKENS_REGION = frozenset(generar_id(region) for region in _REGIONES)

#: Region o pais (normalizado con :func:`generar_id`) -> codigo ISO de dos
#: letras. Se usa solo como desempate entre candidatos con el mismo puntaje.
_CODIGO_POR_REGION = {
    "mexico": "mx", "mx": "mx", "argentina": "ar", "arg": "ar", "ar": "ar",
    "chile": "cl", "cl": "cl", "colombia": "co", "co": "co",
    "peru": "pe", "pe": "pe", "venezuela": "ve", "ve": "ve",
    "ecuador": "ec", "ec": "ec", "uruguay": "uy", "uy": "uy",
    "paraguay": "py", "py": "py", "bolivia": "bo", "bo": "bo",
    "brasil": "br", "brazil": "br", "br": "br", "espana": "es", "spain": "es",
    "es": "es", "portugal": "pt", "pt": "pt", "panama": "pa", "pa": "pa",
    "costarica": "cr", "cr": "cr", "guatemala": "gt", "gt": "gt",
    "honduras": "hn", "hn": "hn", "elsalvador": "sv", "sv": "sv",
    "nicaragua": "ni", "ni": "ni", "republicadominicana": "do", "do": "do",
    "puertorico": "pr", "pr": "pr", "cuba": "cu", "cu": "cu",
    "estadosunidos": "us", "usa": "us", "us": "us", "canada": "ca", "ca": "ca",
}


def urls_epg_por_defecto(paises: "Sequence[str] | None" = None) -> list[str]:
    """Devuelve las URLs de las guias XMLTV publicas de los paises indicados.

    Sin argumentos usa :data:`PAISES_EPG_POR_DEFECTO`. La plantilla se puede
    cambiar con la variable de entorno ``EPG_URL_PLANTILLA``. Los paises de
    :data:`GUIAS_EPG_POR_PAIS` (``US``) tienen su propia URL.
    """
    codigos = paises if paises else PAISES_EPG_POR_DEFECTO
    urls: list[str] = []
    for codigo in codigos:
        codigo = (codigo or "").strip().upper()
        if not codigo:
            continue
        url = GUIAS_EPG_POR_PAIS.get(codigo) or PLANTILLA_EPG_PAIS.replace("{pais}", codigo)
        if url not in urls:
            urls.append(url)
    return urls


def _descomprimir(datos: bytes) -> bytes:
    """Descomprime el contenido si viene en gzip (la guia real suele venir asi)."""
    if datos[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(datos)
        except OSError:
            return datos
    return datos


def _texto_epg(datos: bytes) -> str:
    """Convierte los bytes de una guia (posiblemente gzip) en texto XMLTV."""
    datos = _descomprimir(datos)
    if not datos:
        raise ValueError("la guia EPG esta vacia")
    if len(datos) > MAX_BYTES_EPG:
        raise ValueError(
            f"la guia EPG es demasiado grande ({len(datos) / 1024 / 1024:.0f} MB, "
            f"maximo {MAX_BYTES_EPG / 1024 / 1024:.0f} MB)"
        )
    return _decodificar(datos)


def descargar_epg(
    url: str,
    timeout: int = TIMEOUT_SEGUNDOS,
    reintentos: int = REINTENTOS,
) -> str:
    """Descarga una guia XMLTV real y devuelve su contenido como texto.

    Acepta respuestas en gzip (extension ``.gz`` o cabecera ``Content-Encoding``)
    y BOM UTF-8. A diferencia de :func:`descargar_m3u`, un fallo no termina el
    programa: lanza ``RuntimeError`` para que el llamador pueda probar otra
    fuente o seguir sin programacion real.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"la fuente EPG no es una URL valida: {url!r}")

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
            return _texto_epg(datos)
        except urllib.error.HTTPError as error:
            ultimo_error = error
            # Un 4xx (salvo 429) es un error permanente: reintentar solo hace
            # perder tiempo (por ejemplo un pais sin guia publicada).
            if error.code != 429 and 400 <= error.code < 500:
                break
            if intento < reintentos:
                time.sleep(1.5 * intento)
        except (urllib.error.URLError, OSError, ValueError) as error:
            ultimo_error = error
            if intento < reintentos:
                time.sleep(1.5 * intento)

    raise RuntimeError(f"no se pudo descargar la guia EPG ({ultimo_error})") from ultimo_error


def cargar_fuente_epg(
    fuente: str,
    timeout: int = TIMEOUT_SEGUNDOS,
    reintentos: int = REINTENTOS,
) -> str:
    """Carga una guia EPG desde una URL remota o desde un archivo local."""
    fuente = (fuente or "").strip()
    if fuente.lower().startswith(("http://", "https://")):
        return descargar_epg(fuente, timeout=timeout, reintentos=reintentos)

    ruta = os.path.abspath(fuente)
    with open(ruta, "rb") as archivo:
        return _texto_epg(archivo.read())


def parsear_fecha_xmltv(valor: str) -> datetime | None:
    """Convierte una fecha XMLTV (``20261102003000 -0600``) en ``datetime`` UTC.

    Admite los formatos que usan las guias reales (con o sin segundos, con o
    sin desfase horario). Devuelve ``None`` si el valor no es utilizable.
    """
    texto = (valor or "").strip()
    if not texto:
        return None
    partes = texto.split()
    numero = partes[0]
    desfase = partes[1] if len(partes) > 1 else "+0000"
    formato = {14: "%Y%m%d%H%M%S", 12: "%Y%m%d%H%M", 8: "%Y%m%d"}.get(len(numero))
    if formato is None:
        return None
    try:
        base = datetime.strptime(numero, formato)
        zona = datetime.strptime(desfase, "%z").tzinfo
    except ValueError:
        return None
    return base.replace(tzinfo=zona or timezone.utc).astimezone(timezone.utc)


def _texto_normalizado(texto: str) -> str:
    """Minusculas, sin acentos y solo con letras y digitos separados por espacios."""
    descompuesto = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(
        caracter for caracter in descompuesto if not unicodedata.combining(caracter)
    )
    return " ".join(_RE_TOKEN_NOMBRE.findall(sin_acentos.lower()))


def _palabras_clave(texto: str) -> int:
    """Numero de palabras del nombre, para desempatar entre candidatos."""
    return len(_texto_normalizado(texto).split())


def _tokens_nombre(texto: str) -> tuple[str, ...]:
    """Convierte un nombre de canal en tokens comparables.

    Se descartan la calidad (HD/FHD...), las opciones ([Opc.2]), el pais del
    final y las palabras sin valor ("Canal", "de", "TV"...), de modo que
    "Canal 5 de Mexico (XHGC)" y "Canal 5 HD" quedan como ``("5",)``. Tambien
    se unifican las grafias "TV UNAM"/"TVUNAM" y se ignoran los codigos de pais
    sueltos ("ESPN MX" -> ``("espn",)``).
    """
    # "CANAL+LIVE 12" se unifica antes de limpiar: ``limpiar_nombre`` descarta
    # el "+" y el nombre volveria a confundirse con "Canal 12".
    texto = _RE_MARCA_CANAL_PLUS.sub("canalplus", texto or "")
    limpio = limpiar_nombre(_RE_PARTES_NOMBRE.sub(" ", texto))
    tokens = _RE_TOKEN_NOMBRE.findall(_texto_normalizado(limpio))
    utiles: list[str] = []
    for token in tokens:
        if token in _PALABRAS_SIN_VALOR or token in _CODIGOS_PAIS_TOKENS:
            continue
        # "TVUNAM" -> "UNAM": el prefijo TV no distingue al canal.
        if len(token) > 4 and token.startswith("tv"):
            token = token[2:]
        utiles.append(token)
    if not utiles or set(utiles) <= _TOKENS_CALIDAD:
        return ()
    # Los canales que solo se llaman por un numero ("Canal 7") conservan la
    # palabra "canal": asi no se confunden con "La 7" ni con "7 TV".
    if all(token.isdigit() for token in utiles):
        if any(token in ("canal", "channel") for token in tokens):
            return ("canal", *utiles)
    return tuple(utiles)


def _variantes_tokens(*textos: str) -> list[tuple[str, ...]]:
    """Genera las variantes comparables de un nombre (completo y por partes).

    Las guias reales suelen concatenar varios alias en un mismo
    ``display-name`` ("Canal 2 de Mexico (Canal Las Estrellas - XEW)"), asi que
    tambien se comparan las partes por separado. Las partes que solo dicen un
    pais o region ("(Mexico)") se descartan: no identifican al canal y
    emparejarian con cualquier canal de ese pais.
    """
    variantes: list[tuple[str, ...]] = []
    for texto in textos:
        if not texto:
            continue
        partes = _RE_PARTES_NOMBRE.split(texto)
        for indice, candidato in enumerate([texto, *partes]):
            tokens = _tokens_nombre(candidato)
            if not tokens or tokens in variantes:
                continue
            if indice and all(token in _TOKENS_REGION for token in tokens):
                continue
            # Un token de una sola letra ("A&E" -> "a") no identifica nada.
            if all(len(token) < 2 for token in tokens):
                continue
            variantes.append(tokens)
    return variantes


def _clave_id(texto: str) -> str:
    """Normaliza un id de canal (``ESPN2.us@HD`` -> ``espn2us``) para comparar."""
    return generar_id((texto or "").split("@", 1)[0])


def _pais_de_id(identificador: str) -> str:
    """Devuelve el codigo de pais del final de un id (``canal5.mx`` -> ``mx``)."""
    identificador = (identificador or "").strip()
    if "." not in identificador:
        return ""
    sufijo = identificador.rsplit(".", 1)[-1].lower()
    return sufijo if len(sufijo) == 2 and sufijo.isalpha() else ""


def _pais_del_canal(canal: "Mapping[str, str]") -> str:
    """Pista de pais del canal del M3U: ``tvg-id`` (``canal5.mx``) o region."""
    pais = _pais_de_id(canal.get("tvg_id", ""))
    if pais:
        return pais
    region = _region_final(canal.get("nombre_m3u", "") or canal.get("nombre", ""))
    return _CODIGO_POR_REGION.get(generar_id(region), "") if region else ""


def _pais_de_fuente(identificador: str, nombres: "Sequence[str]") -> str:
    """Pais de un canal de la guia: el de su nombre o el del sufijo del id."""
    for nombre in nombres:
        region = _region_final(nombre or "")
        if not region:
            continue
        codigo = _CODIGO_POR_REGION.get(generar_id(region), "")
        if codigo:
            return codigo
    return _pais_de_id(identificador)


def _puntaje_coincidencia(nuestros: tuple[str, ...], ajenos: tuple[str, ...]) -> float:
    """Puntua dos conjuntos de tokens: 1.0 identicos, 0.0 si no se parecen.

    Solo se acepta coincidencia exacta o contigua y se exige que el nombre
    corto cubra al menos :data:`PUNTAJE_MINIMO_EMPAREJAMIENTO` del largo. Asi
    "Canal 5" empareja con "Canal 5 de Mexico", pero "ESPN" no empareja con
    "ESPN 2" (0.5 de cobertura).
    """
    if not nuestros or not ajenos:
        return 0.0
    if nuestros == ajenos:
        return 1.0
    corto, largo = (
        (nuestros, ajenos) if len(nuestros) <= len(ajenos) else (ajenos, nuestros)
    )
    cobertura = len(corto) / len(largo)
    if cobertura < PUNTAJE_MINIMO_EMPAREJAMIENTO:
        return 0.0
    for posicion in range(len(largo) - len(corto) + 1):
        if largo[posicion : posicion + len(corto)] == corto:
            return round(cobertura, 3)
    return 0.0


def _mejor_coincidencia(
    nuestras: "Sequence[tuple[str, ...]]",
    ajenas: "Sequence[tuple[str, ...]]",
) -> tuple[float, int]:
    """Devuelve ``(puntaje, tokens coincidentes)`` del mejor par de variantes.

    Los tokens coincidentes desempatan entre dos canales con el mismo puntaje:
    gana la coincidencia mas especifica ("canal 5 de mexico xhgc" antes que
    "xhgc"), que es la que identifica realmente al canal.
    """
    mejor: tuple[float, int] = (0.0, 0)
    for propia in nuestras:
        for ajena in ajenas:
            puntaje = _puntaje_coincidencia(propia, ajena)
            if puntaje <= 0:
                continue
            especificidad = max(len(propia), len(ajena))
            if (puntaje, especificidad) > mejor:
                mejor = (puntaje, especificidad)
    return mejor


def _es_canal_generico_frances(
    id_fuente: str, nombres: "Sequence[str]"
) -> bool:
    """Indica si un canal de la guia es un ``Canal NN`` generico de Francia.

    La guia francesa de Telerama incluye canales sueltos llamados "Canal 11",
    "Canal 21" o "Canal 32" que solo repiten el bucle de Canal+; si no se
    descartan le roban la programacion a canales como "Canal 32 (Television
    Chichicasteca)". Se limita a Francia: en otros paises "Canal 9" (TVMax de
    Panama) o "Canal 7" si son canales de verdad.
    """
    if _pais_de_fuente(id_fuente, nombres) != "fr":
        return False
    return any(
        _RE_GUIA_GENERICA_FR.match((nombre or "").strip()) for nombre in nombres
    )


def emparejar_canales(
    canales: "Sequence[Mapping[str, str]]",
    canales_fuente: "Mapping[str, Sequence[str]]",
) -> dict[str, str]:
    """Empareja los canales del M3U con los de una guia XMLTV real.

    Orden de prioridad:

    1. ``tvg-id`` identico al ``<channel id="...">`` de la guia.
    2. ``tvg-id`` normalizado (sin ``@calidad`` ni simbolos).
    3. Nombre normalizado (sin calidad, sin parentesis y sin pais) con puntaje
       mayor o igual a :data:`PUNTAJE_MINIMO_EMPAREJAMIENTO`.

    La coincidencia por nombre se resuelve **por puntaje global**: primero se
    emparejan los canales con nombres identicos y despues los parecidos, de
    modo que un nombre exacto nunca pierde su canal frente a uno aproximado.
    Un canal de la guia no se reutiliza y, en caso de empate, gana el candidato
    del mismo pais. Devuelve ``{id_canal: id_fuente}``.
    """
    indice_id: dict[str, str] = {}
    variantes: dict[str, list[tuple[str, ...]]] = {}
    por_token: dict[str, set[str]] = {}
    pais_fuente: dict[str, str] = {}
    palabras_fuente: dict[str, int] = {}

    for id_fuente, nombres in canales_fuente.items():
        if _es_canal_generico_frances(id_fuente, nombres):
            continue
        indice_id.setdefault(id_fuente.lower(), id_fuente)
        indice_id.setdefault(_clave_id(id_fuente), id_fuente)
        variantes[id_fuente] = _variantes_tokens(id_fuente, *nombres)
        pais_fuente[id_fuente] = _pais_de_fuente(id_fuente, nombres)
        palabras_fuente[id_fuente] = _palabras_clave(nombres[0] if nombres else id_fuente)
        for tokens in variantes[id_fuente]:
            for token in tokens:
                por_token.setdefault(token, set()).add(id_fuente)

    mapa: dict[str, str] = {}
    usados: set[str] = set()
    propuestas: list[tuple[float, int, int, str, str]] = []
    pendientes: list[Mapping[str, str]] = []

    for canal in canales:
        canal_id = canal.get("id", "")
        if not canal_id:
            continue

        # 1-2) Coincidencia por id (la que usan los reproductores).
        for clave in (canal.get("tvg_id", ""), canal_id):
            if not clave:
                continue
            fuente = indice_id.get(clave.lower()) or indice_id.get(_clave_id(clave))
            if fuente and fuente not in usados:
                mapa[canal_id] = fuente
                usados.add(fuente)
                break
        if canal_id not in mapa:
            pendientes.append(canal)

    # 3) Coincidencia por nombre normalizado: se acumulan las propuestas y se
    #    asignan de mayor a menor puntaje.
    for canal in pendientes:
        canal_id = canal["id"]
        nuestras = _variantes_tokens(
            canal.get("nombre", ""),
            canal.get("tvg_name", ""),
            canal.get("nombre_m3u", ""),
        )
        candidatos: set[str] = set()
        for tokens in nuestras:
            for token in tokens:
                candidatos.update(por_token.get(token, ()))
        if not candidatos:
            continue

        pais = _pais_del_canal(canal)
        palabras = _palabras_clave(canal.get("nombre", ""))
        for fuente in candidatos:
            if fuente in usados:
                continue
            puntaje, especificidad = _mejor_coincidencia(nuestras, variantes[fuente])
            if puntaje < PUNTAJE_MINIMO_EMPAREJAMIENTO:
                continue
            if pais and pais == pais_fuente.get(fuente):
                puntaje += 0.05
            # Desempate: el nombre con un numero de palabras mas parecido.
            cercania = -abs(palabras - palabras_fuente.get(fuente, palabras))
            propuestas.append((puntaje, especificidad, cercania, canal_id, fuente))

    for _puntaje, _especificidad, _cercania, canal_id, fuente in sorted(
        propuestas, reverse=True
    ):
        if canal_id in mapa or fuente in usados:
            continue
        mapa[canal_id] = fuente
        usados.add(fuente)

    return mapa


def parsear_epg_canales(texto: str) -> dict[str, list[str]]:
    """Extrae de una guia XMLTV ``{id_canal: [display-name, ...]}``.

    Usa ``iterparse`` para no cargar la guia completa en memoria (las guias
    reales ocupan decenas de MB) y libera los nodos ya procesados.
    """
    canales: dict[str, list[str]] = {}
    contexto = ET.iterparse(io.StringIO(texto), events=("start", "end"))
    try:
        _, raiz = next(contexto)
    except StopIteration:
        return canales

    for evento, elemento in contexto:
        if evento != "end":
            continue
        if elemento.tag == "channel":
            identificador = (elemento.get("id") or "").strip()
            if identificador:
                nombres = canales.setdefault(identificador, [])
                for hijo in elemento.findall("display-name"):
                    nombre = (hijo.text or "").strip()
                    if nombre and nombre not in nombres:
                        nombres.append(nombre)
        # Solo se liberan los nodos de primer nivel: si se vaciara un
        # <display-name> antes de tiempo se perderia su texto.
        if elemento.tag in ("channel", "programme"):
            elemento.clear()
            raiz.clear()

    return canales


def _programa_en_ventana(
    elemento: ET.Element,
    desde: datetime,
    hasta: datetime,
) -> ET.Element | None:
    """Copia un ``<programme>`` normalizando sus fechas a UTC (+0000).

    Devuelve ``None`` si el programa no se solapa con la ventana pedida, si no
    tiene una fecha de inicio valida o si no tiene titulo (XMLTV lo exige).
    """
    inicio = parsear_fecha_xmltv(elemento.get("start", ""))
    if inicio is None:
        return None
    fin = parsear_fecha_xmltv(elemento.get("stop", ""))
    if fin is not None and fin <= desde:
        return None
    if inicio > hasta:
        return None

    atributos = {"start": _sello(inicio)}
    if fin is not None and fin > inicio:
        atributos["stop"] = _sello(fin)
    programa = ET.Element("programme", atributos)
    for hijo in elemento:
        if hijo.tag in _ETIQUETAS_PROGRAMA:
            programa.append(hijo)
    if programa.find("title") is None:
        return None
    return programa


def parsear_epg_programas(
    texto: str,
    ids_fuente: "Sequence[str] | set[str]",
    desde: datetime,
    hasta: datetime,
) -> dict[str, list[ET.Element]]:
    """Extrae los ``<programme>`` de los canales indicados dentro de la ventana.

    Devuelve ``{id_canal_fuente: [<programme>, ...]}`` con las fechas ya
    normalizadas a UTC. Solo se conservan los programas de los canales que se
    han emparejado, de modo que el archivo de salida no crece de mas.
    """
    programas: dict[str, list[ET.Element]] = {}
    buscados = set(ids_fuente)
    if not buscados:
        return programas

    contexto = ET.iterparse(io.StringIO(texto), events=("start", "end"))
    try:
        _, raiz = next(contexto)
    except StopIteration:
        return programas

    for evento, elemento in contexto:
        if evento != "end":
            continue
        if elemento.tag == "programme":
            fuente = (elemento.get("channel") or "").strip()
            if fuente in buscados:
                programa = _programa_en_ventana(elemento, desde, hasta)
                if programa is not None:
                    programas.setdefault(fuente, []).append(programa)
        # Solo se liberan los nodos de primer nivel: si se vaciara un <title>
        # antes de tiempo se perderia el texto del programa.
        if elemento.tag in ("programme", "channel"):
            elemento.clear()
            raiz.clear()

    return programas


def integrar_epg(
    canales: "Sequence[Mapping[str, str]]",
    fuentes: "Sequence[str]",
    ventana: tuple[datetime, datetime],
    *,
    timeout: int = TIMEOUT_SEGUNDOS,
    reintentos: int = REINTENTOS,
    cargador=None,
    aviso=None,
) -> tuple[dict[str, list[ET.Element]], dict[str, str], list[dict[str, object]]]:
    """Descarga las guias reales y acumula la programacion de cada canal.

    Procesa las fuentes en orden: la primera que empareja un canal es la que
    aporta su programacion (las siguientes solo cubren los canales pendientes).
    Un fallo en una fuente no interrumpe el resto.

    Devuelve ``(programas, emparejados, informes)``:

    * ``programas``: ``{id_canal: [<programme>, ...]}`` listo para el XMLTV.
    * ``emparejados``: ``{id_canal: id_canal_fuente}``.
    * ``informes``: una entrada por fuente con canales, emparejados y error.
    """
    desde, hasta = ventana
    cargar = cargador or (
        lambda fuente: cargar_fuente_epg(fuente, timeout=timeout, reintentos=reintentos)
    )
    avisar = aviso or (lambda mensaje: None)

    programas: dict[str, list[ET.Element]] = {}
    emparejados: dict[str, str] = {}
    asignados: set[str] = set()
    informes: list[dict[str, object]] = []

    for fuente in fuentes:
        informe: dict[str, object] = {
            "fuente": fuente,
            "canales": 0,
            "emparejados": 0,
            "programas": 0,
            "error": "",
        }
        informes.append(informe)
        try:
            texto = cargar(fuente)
            canales_fuente = parsear_epg_canales(texto)
            if not canales_fuente:
                raise ValueError("la guia no contiene canales <channel>")

            # La primera guia que reconoce un canal decide su programacion; las
            # siguientes solo cubren los canales que aun no tienen pareja.
            pendientes = [canal for canal in canales if canal["id"] not in asignados]
            mapa = emparejar_canales(pendientes, canales_fuente)
            asignados.update(mapa)
            reales = parsear_epg_programas(texto, set(mapa.values()), desde, hasta)

            nuevos = 0
            for canal_id, fuente_id in mapa.items():
                lista = reales.get(fuente_id) or []
                if not lista:
                    continue
                programas[canal_id] = lista
                emparejados[canal_id] = fuente_id
                nuevos += len(lista)

            informe.update(
                {
                    "canales": len(canales_fuente),
                    "emparejados": len(mapa),
                    "programas": nuevos,
                }
            )
            avisar(
                f"      {fuente}\n"
                f"        canales en la guia: {len(canales_fuente)} | "
                f"emparejados: {len(mapa)} | programas: {nuevos}"
            )
        except (RuntimeError, OSError, ValueError, ET.ParseError) as error:
            informe["error"] = str(error)
            avisar(f"      Aviso: se omite {fuente} ({error})")

    return programas, emparejados, informes


def epg_de_ejemplo(inicio: datetime) -> str:
    """Guia XMLTV de ejemplo para ``--demo`` (sin red).

    Reproduce los casos tipicos del emparejamiento: un id identico
    (``canal5.mx``), un nombre con prefijo (``Canal Las Estrellas``), un nombre
    con calidad (``Cine Latino HD``) y dos canales de la lista sin guia
    (``deportes247`` y ``Canal Local``) para ver el fallback sin relleno.
    """
    canales = (
        (
            "canal5.mx",
            "Canal 5",
            (
                ("Noticiero Matutino", "Noticias"),
                ("Cine de la Tarde", "Cine"),
                ("Serie Nocturna", "Series"),
            ),
        ),
        (
            "Canal.Las.Estrellas.mx",
            "Canal Las Estrellas",
            (
                ("Hoy", "Variedades"),
                ("La Rosa de Guadalupe", "Drama"),
                ("Noticiero Estelar", "Noticias"),
            ),
        ),
        (
            "cinelatino.mx",
            "Cine Latino HD",
            (
                ("Cine Clasico", "Cine"),
                ("Cine de Accion", "Cine"),
                ("Cine de Autor", "Cine"),
            ),
        ),
        (
            "meganoticias.mx",
            "Mega Noticias",
            (
                ("Primera Edicion", "Noticias"),
                ("Mega Tarde", "Noticias"),
                ("Mega Noche", "Noticias"),
            ),
        ),
    )

    lineas = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="epg-de-ejemplo">',
    ]
    for id_fuente, nombre, _bloques in canales:
        lineas.append(f'  <channel id="{id_fuente}">')
        lineas.append(f'    <display-name lang="es">{nombre}</display-name>')
        lineas.append("  </channel>")
    for id_fuente, _nombre, bloques in canales:
        for indice, (titulo, categoria) in enumerate(bloques):
            comienzo = inicio + timedelta(hours=6 * indice)
            lineas.append(
                f'  <programme start="{_sello(comienzo)}" '
                f'stop="{_sello(comienzo + timedelta(hours=6))}" channel="{id_fuente}">'
                f'<title lang="es">{titulo}</title>'
                f'<category lang="es">{categoria}</category>'
                "</programme>"
            )
    lineas.append("</tv>")
    return "\n".join(lineas) + "\n"


# =============================================================================
# 6. CONSTRUCCION DEL XMLTV
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


def _agregar_programas_reales(
    raiz: ET.Element,
    programas: "Sequence[ET.Element]",
    canal_id: str,
) -> int:
    """Añade a la guia los ``<programme>`` reales de un canal.

    Se ordenan por hora de inicio y se descartan los duplicados
    (mismo canal y misma hora), que aparecen cuando dos guias se solapan.
    Devuelve el numero de programas añadidos.
    """
    vistos: set[str] = set()
    total = 0
    for programa in sorted(programas, key=lambda nodo: nodo.get("start", "")):
        comienzo = programa.get("start", "")
        if not comienzo or comienzo in vistos:
            continue
        vistos.add(comienzo)

        atributos = {"start": comienzo, "channel": canal_id}
        fin = programa.get("stop", "")
        if fin:
            atributos["stop"] = fin
        nodo = ET.SubElement(raiz, "programme", atributos)
        for hijo in programa:
            nodo.append(hijo)
        total += 1
    return total


def construir_xmltv(
    canales: Sequence[Mapping[str, str]],
    inicio: datetime,
    programas: "Mapping[str, Sequence[ET.Element]] | None" = None,
    relleno: bool = True,
) -> tuple[ET.Element, int]:
    """Construye el arbol XMLTV con los canales y su programacion.

    ``programas`` es el mapa ``{id_canal: [<programme>]}`` que devuelve
    :func:`integrar_epg`: los canales con programacion real se publican tal
    cual. ``relleno`` solo afecta a los canales **sin** programacion real: con
    ``True`` reciben la parrilla generica de 24 h (comportamiento historico) y
    con ``False`` se publican sin ``<programme>``, sin inventar contenido.

    Devuelve la raiz ``<tv>`` y el numero total de ``<programme>`` escritos.
    """
    programas_reales = programas or {}
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

    # 2) Programacion: la real de cada canal emparejado y, solo si se pide
    #    (o si no hubo ninguna guia utilizable), la parrilla generica de 24 h.
    total_programas = 0
    for canal in canales:
        reales = programas_reales.get(canal["id"]) or ()
        if reales:
            total_programas += _agregar_programas_reales(raiz, reales, canal["id"])
            continue
        if not relleno:
            continue

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
# 7. INTERFAZ DE LINEA DE COMANDOS
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
            "M3U remota y de guias XMLTV reales publicas, y publica una lista M3U "
            "propia (lista.m3u) con el tvg-id de cada canal ya emparejado."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python generar_epg.py --demo\n"
            "  python generar_epg.py --url https://raw.githubusercontent.com/USUARIO/REPO/main/lista.m3u\n"
            "  python generar_epg.py -u \"$M3U_URL\" -s epg.xml -m lista.m3u\n"
            "  python generar_epg.py -u \"$M3U_URL\" --epg-pais MX,ES,AR    # guias reales por pais\n"
            "  python generar_epg.py -u \"$M3U_URL\" --epg-pais MX,ES,US   # incluye la guia de EE. UU.\n"
            "  python generar_epg.py -u \"$M3U_URL\" --epg-pais MX --sin-extras  # solo la de MX\n"
            "  python generar_epg.py -u \"$M3U_URL\" --epg https://ejemplo.com/guia.xml.gz\n"
            "  python generar_epg.py -u \"$M3U_URL\" --epg-archivo guia.xml --horas-ventana 48\n"
            "  python generar_epg.py -u \"$M3U_URL\" --sin-epg --relleno     # parrilla generica\n"
            "  python generar_epg.py -u \"$M3U_URL\" --sin-tvg-id            # ids desde el nombre\n"
            "  python generar_epg.py -u \"$M3U_URL\" --mapeo mapeo_canales.json --sin-m3u\n"
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
        "-m", "--m3u-salida",
        default=LISTA_M3U_POR_DEFECTO,
        metavar="RUTA",
        help=(
            "Ruta de la lista M3U propia que se genera con el tvg-id de cada canal "
            f"(por defecto: {LISTA_M3U_POR_DEFECTO})."
        ),
    )
    parser.add_argument(
        "--sin-m3u",
        action="store_true",
        help="No generar la lista M3U propia: solo el XMLTV.",
    )
    parser.add_argument(
        "--mapeo",
        default=os.environ.get("M3U_MAPEO", "").strip() or ARCHIVO_MAPEO_POR_DEFECTO,
        metavar="RUTA",
        help=(
            "JSON con el mapeo explicito de alias -> tvg-id real "
            f"(por defecto: {ARCHIVO_MAPEO_POR_DEFECTO} si existe)."
        ),
    )
    parser.add_argument(
        "--sin-mapeo",
        action="store_true",
        help="Ignorar el mapeo explicito y emparejar solo con el tvg-id/nombre del M3U.",
    )
    parser.add_argument(
        "--epg-publica",
        default=URL_EPG_PUBLICA,
        metavar="URL",
        help=(
            "URL publica del epg.xml que se declara en la cabecera del M3U nuevo "
            "(por defecto: EPG_PUBLICA_URL o la del repositorio)."
        ),
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
        "-e", "--epg",
        action="append",
        default=None,
        metavar="URL",
        help=(
            "Fuente EPG real (XMLTV, admite gzip): URL o ruta local. Se puede repetir "
            "o separar varias con comas. Por defecto se usan las guias de --epg-pais."
        ),
    )
    parser.add_argument(
        "--epg-pais",
        default=None,
        metavar="CODIGOS",
        help=(
            "Codigos ISO de los paises cuyas guias publicas se descargan "
            "(por defecto: " + ",".join(PAISES_EPG_POR_DEFECTO) + ")."
        ),
    )
    parser.add_argument(
        "--epg-archivo",
        action="append",
        default=None,
        metavar="RUTA",
        help="Guia XMLTV local (XML o .gz) para emparejar los canales, sin descargarla.",
    )
    parser.add_argument(
        "--sin-epg",
        action="store_true",
        help="No usar ninguna guia real: genera solo la parrilla generica de 24 h.",
    )
    parser.add_argument(
        "--sin-extras",
        action="store_true",
        help=(
            "No descargar las guias extra que se suman a las de --epg-pais "
            "(Pluto TV, Samsung TV Plus y Roku; variable EPG_URLS_EXTRA)."
        ),
    )
    parser.add_argument(
        "--horas-ventana",
        type=int,
        default=HORAS_GUIA_REAL,
        metavar="N",
        help=(
            "Horas de programacion real que se conservan desde el inicio de la guia "
            f"(por defecto: {HORAS_GUIA_REAL})."
        ),
    )
    parser.add_argument(
        "--relleno",
        action="store_true",
        help=(
            "Rellenar con la parrilla generica los canales sin programacion real. "
            "Por defecto esos canales se publican sin programas."
        ),
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
        help=(
            "Generar la EPG con la lista M3U y la guia XMLTV de ejemplo, sin "
            "descargar nada."
        ),
    )
    parser.add_argument(
        "-q", "--silencioso",
        action="store_true",
        help="Mostrar solo los errores.",
    )
    return parser


# =============================================================================
# 8. LISTA M3U PROPIA SINCRONIZADA CON LA GUIA
# =============================================================================

def _limpiar_atributo_m3u(valor: object) -> str:
    """Normaliza un valor para que se pueda escribir entre comillas en el M3U.

    Se sanea la codificacion, se colapsan los espacios y se sustituyen las
    comillas por apostrofes: una comilla sin escapar rompe (y desalinea) todos
    los atributos de la linea ``#EXTINF``.
    """
    texto = sanear_codificacion(str(valor or ""))
    texto = texto.replace('"', "'").replace("\r", " ").replace("\n", " ")
    return _RE_ESPACIOS.sub(" ", texto).strip()


def _logo_publicable(logo: object) -> str:
    """Devuelve el logo solo si es una URL http(s) utilizable."""
    texto = _limpiar_atributo_m3u(logo)
    return texto if texto.lower().startswith(("http://", "https://")) else ""


def _senales_del_canal(canal: Mapping[str, object]) -> list[dict[str, object]]:
    """Señales (URL + opciones) que hay que publicar de un canal."""
    senales = canal.get("senales") or []
    if isinstance(senales, (list, tuple)) and senales:
        return [dict(senal) for senal in senales if isinstance(senal, Mapping)]
    url = str(canal.get("url", "") or "")
    return [{"url": url, "opciones": ()}] if url else []


def ordenar_canales_m3u(
    canales: "Sequence[Mapping[str, object]]",
) -> list[Mapping[str, object]]:
    """Ordena los canales por grupo y nombre (los sin grupo, al final)."""
    return sorted(
        canales,
        key=lambda canal: (
            _limpiar_atributo_m3u(canal.get("grupo", "")).casefold() or "\uffff",
            _limpiar_atributo_m3u(canal.get("nombre", "")).casefold(),
            str(canal.get("id", "")).casefold(),
        ),
    )


def _entradas_m3u(
    canales: "Sequence[Mapping[str, object]]",
    ordenar: bool = True,
) -> tuple[list[str], int, int, list[str]]:
    """Construye las lineas de las entradas y sus estadisticas.

    Devuelve ``(lineas, canales_publicados, entradas, canales_sin_url)``. Se
    publica **una entrada por señal** (HD, espejo, ``Opc.2``, ``Backup``...)
    con el mismo ``tvg-id`` del canal principal, de modo que el reproductor
    encuentra la programacion real en todas las opciones.
    """
    lineas: list[str] = []
    publicados = 0
    entradas = 0
    sin_url: list[str] = []

    for canal in ordenar_canales_m3u(canales) if ordenar else canales:
        canal_id = _limpiar_atributo_m3u(canal.get("id", ""))
        nombre = _limpiar_atributo_m3u(canal.get("nombre", "")) or canal_id
        tvg_name = _limpiar_atributo_m3u(canal.get("tvg_name", "")) or nombre
        logo = _logo_publicable(canal.get("logo", ""))
        grupo = _limpiar_atributo_m3u(canal.get("grupo", ""))
        senales = _senales_del_canal(canal)
        if not senales:
            sin_url.append(canal_id or nombre)
            continue

        publicados += 1
        for indice, senal in enumerate(senales):
            visible = nombre
            if indice:
                visible = f"{nombre} ({ETIQUETA_SENAL_SECUNDARIA.format(numero=indice + 1)})"
            atributos = [
                f'tvg-id="{canal_id}"',
                f'tvg-name="{tvg_name}"',
            ]
            if logo:
                atributos.append(f'tvg-logo="{logo}"')
            if grupo:
                atributos.append(f'group-title="{grupo}"')
            lineas.append(f"#EXTINF:-1 {' '.join(atributos)},{visible}")
            for opcion in senal.get("opciones") or ():
                opcion = str(opcion).strip()
                if opcion:
                    lineas.append(opcion)
            lineas.append(str(senal.get("url", "")).strip())
            entradas += 1

    return lineas, publicados, entradas, sin_url


def generar_m3u(
    canales: "Sequence[Mapping[str, object]]",
    url_epg: str = "",
    ordenar: bool = True,
) -> str:
    """Devuelve el texto de la lista M3U propia, sincronizada con el ``epg.xml``.

    Cada entrada lleva el ``tvg-id`` del canal (el mismo ``<channel id="...">``
    que publica la guia) y el ``tvg-name`` estandar, que es lo que usa el
    reproductor para pedir la programacion. En la cabecera se declara la URL
    publica de la guia (``x-tvg-url`` y ``url-tvg``) para que OTT Navigator,
    VLC, TiviMate... la descarguen automaticamente.
    """
    lineas, _publicados, _entradas, _sin_url = _entradas_m3u(canales, ordenar)
    url_epg = (url_epg or "").strip()
    if url_epg:
        cabecera = f'#EXTM3U x-tvg-url="{url_epg}" url-tvg="{url_epg}"'
    else:
        cabecera = "#EXTM3U"
    cuerpo = [cabecera, f"# Lista generada por {NOMBRE_GENERADOR} (generar_epg.py)"]
    cuerpo.extend(lineas)
    return "\n".join(cuerpo) + "\n"


def escribir_m3u(
    canales: "Sequence[Mapping[str, object]]",
    destino: str,
    url_epg: str = "",
    ordenar: bool = True,
) -> tuple[int, int, list[str]]:
    """Escribe la lista M3U propia y devuelve ``(canales, entradas, sin_url)``."""
    lineas, publicados, entradas, sin_url = _entradas_m3u(canales, ordenar)
    url_epg = (url_epg or "").strip()
    if url_epg:
        cabecera = f'#EXTM3U x-tvg-url="{url_epg}" url-tvg="{url_epg}"'
    else:
        cabecera = "#EXTM3U"
    texto = "\n".join(
        [cabecera, f"# Lista generada por {NOMBRE_GENERADOR} (generar_epg.py)", *lineas]
    )
    with open(destino, "w", encoding="utf-8", newline="\n") as archivo:
        archivo.write(texto + "\n")
    return publicados, entradas, sin_url


# =============================================================================
# 9. PROGRAMA PRINCIPAL
# =============================================================================

def _fuentes_epg_desde_argumentos(argumentos: argparse.Namespace) -> list[str]:
    """Construye la lista de fuentes EPG reales a partir de los argumentos.

    Prioridad: ``--epg`` y ``--epg-archivo`` (en ese orden) > ``--epg-pais`` >
    variable de entorno ``EPG_URL`` > guias publicas por pais por defecto.

    Cuando las fuentes salen de codigos de pais se anaden al final las guias
    extra de :data:`FUENTES_EPG_EXTRA_POR_DEFECTO` (Pluto TV, Samsung TV Plus y
    Roku) salvo que se use ``--sin-extras``. Si se indica una fuente concreta
    (``--epg``, ``--epg-archivo`` o ``EPG_URL``) solo se usa esa.
    """
    fuentes: list[str] = []
    for valor in argumentos.epg or []:
        for parte in str(valor).split(","):
            parte = parte.strip()
            if parte and parte not in fuentes:
                fuentes.append(parte)
    for ruta in argumentos.epg_archivo or []:
        ruta = (ruta or "").strip()
        if ruta and ruta not in fuentes:
            fuentes.append(ruta)
    if fuentes:
        return fuentes

    if argumentos.epg_pais is not None:
        fuentes = urls_epg_por_defecto(argumentos.epg_pais.split(","))
    elif URL_EPG:
        return [parte.strip() for parte in URL_EPG.split(",") if parte.strip()]
    else:
        fuentes = urls_epg_por_defecto()

    if not getattr(argumentos, "sin_extras", False):
        for extra in FUENTES_EPG_EXTRA_POR_DEFECTO:
            if extra not in fuentes:
                fuentes.append(extra)
    return fuentes


def main(argv: list[str] | None = None) -> int:
    """Flujo completo: M3U -> canales -> EPG real -> emparejamiento -> XMLTV."""
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
        aviso("[1/7] Usando la lista M3U de ejemplo (--demo), sin descargar nada.")
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
        aviso(f"[1/7] Descargando la lista M3U desde {url} ...")
        texto_m3u = descargar_m3u(url, timeout=argumentos.timeout)
        origen = url

    # --- 2. Analisis del M3U ------------------------------------------------
    aviso("[2/7] Analizando el archivo M3U (nombre + tvg-logo) ...")
    canales_brutos = parsear_m3u(texto_m3u)
    if not canales_brutos:
        raise SystemExit(
            "No se encontro ningun canal valido en la lista M3U.\n"
            "Comprueba que el contenido descargado contenga lineas #EXTINF:-1 ..."
        )
    aviso(f"      Entradas #EXTINF encontradas: {len(canales_brutos)}")

    # --- 3. Limpieza, deduplicacion y mapeo explicito -----------------------
    aviso("[3/7] Limpiando nombres (SD/HD/FHD/[Opc.N]/pais) y generando ids ...")
    mapeo: dict[str, dict[str, object]] = {}
    if argumentos.sin_mapeo:
        aviso("      Mapeo explicito desactivado (--sin-mapeo).")
    else:
        try:
            mapeo = cargar_mapeo_canales(argumentos.mapeo)
        except (OSError, ValueError) as error:
            aviso(f"      Aviso: se ignora el mapeo de canales ({error})")
            mapeo = {}
        if mapeo:
            aviso(f"      Mapeo explicito de canales: {len(mapeo)} tvg-id canonicos.")

    canales = consolidar_canales(
        canales_brutos,
        usar_tvg_id=not argumentos.sin_tvg_id,
        conservar_tvg_id=argumentos.conservar_tvg_id,
        mapeo=mapeo or None,
    )
    con_tvg_id = sum(1 for canal in canales if canal["origen"] == "tvg-id")
    espejos = sum(len(canal["senales"]) - 1 for canal in canales if len(canal["senales"]) > 1)
    aviso(f"      Canales unicos tras unificar senales: {len(canales)}")
    aviso(
        f"      Ids tomados del tvg-id del M3U: {con_tvg_id} | "
        f"generados desde el nombre: {len(canales) - con_tvg_id}"
    )
    if espejos:
        aviso(f"      Senales de respaldo (Opc.2/espejo/backup) conservadas: {espejos}")
    con_mapeo = sum(1 for canal in canales if canal.get("mapeado"))
    if con_mapeo:
        aviso(f"      Canales con tvg-id forzado por el mapeo: {con_mapeo}")

    # --- 4. Programacion real (EPG externo) --------------------------------
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
    ventana = (inicio, inicio + timedelta(hours=max(1, argumentos.horas_ventana)))
    programas: dict[str, list[ET.Element]] = {}
    emparejados: dict[str, str] = {}
    informes: list[dict[str, object]] = []

    if argumentos.sin_epg:
        aviso("[4/7] EPG real desactivado (--sin-epg): parrilla generica de 24 h.")
    else:
        fuentes = _fuentes_epg_desde_argumentos(argumentos)
        offline = argumentos.demo and not (
            argumentos.epg or argumentos.epg_archivo or argumentos.epg_pais
        )
        cargador = None
        if offline:
            fuentes = ["guia XMLTV de ejemplo (--demo)"]
            texto_ejemplo = epg_de_ejemplo(inicio)

            def cargador(_fuente: str) -> str:
                return texto_ejemplo

            aviso("[4/7] Usando la guia XMLTV de ejemplo (--demo), sin descargar nada.")
        else:
            aviso(f"[4/7] Cargando {len(fuentes)} guia(s) XMLTV reales ...")

        aviso(
            f"      Ventana de programacion: {ventana[0]:%Y-%m-%d %H:%M} a "
            f"{ventana[1]:%Y-%m-%d %H:%M} UTC"
        )
        programas, emparejados, informes = integrar_epg(
            canales,
            fuentes,
            ventana,
            timeout=argumentos.timeout,
            cargador=cargador,
            aviso=aviso,
        )
        sin_coincidencia = len(canales) - len(programas)
        aviso(
            f"      Canales con programacion real: {len(programas)} | "
            f"sin coincidencia: {sin_coincidencia}"
        )
        if sin_coincidencia and not argumentos.relleno:
            aviso("      (se publican sin programas; use --relleno para rellenarlos)")

    relleno = argumentos.relleno or not programas
    if not programas:
        aviso("      Sin programacion real: se usa la parrilla generica de 24 h.")

    # --- 5. Generacion del XMLTV -------------------------------------------
    aviso(f"[5/7] Generando el XMLTV desde {inicio:%Y-%m-%d 00:00:00} {DESFASE_UTC} ...")
    raiz, total_programas = construir_xmltv(
        canales,
        inicio,
        programas=programas,
        relleno=relleno,
    )

    # --- 6. Escritura y validacion -----------------------------------------
    destino = os.path.abspath(argumentos.salida)
    aviso(f"[6/7] Escribiendo {destino} ...")
    try:
        tamano = escribir_xmltv(raiz, destino, incluir_doctype=not argumentos.sin_doctype)
        validado = ET.parse(destino).getroot()
    except OSError as error:
        raise SystemExit(f"No se pudo escribir el archivo de salida: {error}") from error
    except ET.ParseError as error:
        raise SystemExit(f"El XML generado no es valido: {error}") from error

    if validado.tag != "tv":
        raise SystemExit("El XMLTV generado no tiene la etiqueta raiz <tv>.")

    # --- 7. Lista M3U propia, sincronizada con la guia ----------------------
    lista_m3u = ""
    m3u_canales = m3u_entradas = 0
    m3u_sin_url: list[str] = []
    if argumentos.sin_m3u:
        aviso("[7/7] Lista M3U propia desactivada (--sin-m3u).")
    else:
        lista_m3u = os.path.abspath(argumentos.m3u_salida)
        aviso(f"[7/7] Escribiendo {lista_m3u} (tvg-id sincronizado con la guia) ...")
        try:
            m3u_canales, m3u_entradas, m3u_sin_url = escribir_m3u(
                canales,
                lista_m3u,
                url_epg=argumentos.epg_publica,
            )
        except OSError as error:
            raise SystemExit(f"No se pudo escribir la lista M3U: {error}") from error
        aviso(
            f"      Entradas publicadas: {m3u_entradas} "
            f"({m3u_canales} canales, senales de respaldo incluidas)"
        )
        if m3u_sin_url:
            aviso(f"      Canales omitidos por no tener URL: {len(m3u_sin_url)}")

    aviso("")
    aviso("-" * 70)
    aviso(" RESUMEN")
    aviso("-" * 70)
    aviso(f" Origen              : {origen}")
    aviso(f" Entradas M3U        : {len(canales_brutos)}")
    aviso(f" Canales unicos      : {len(canales)}")
    aviso(
        f" Canales con EPG real: {len(programas)} | "
        f"sin coincidencia: {len(canales) - len(programas)}"
    )
    aviso(f" Programas <programme>: {total_programas}")
    for informe in informes:
        estado = informe["error"] or "ok"
        aviso(
            f"   - {informe['fuente']}: canales={informe['canales']} "
            f"emparejados={informe['emparejados']} "
            f"programas={informe['programas']} [{estado}]"
        )
    aviso(f" Archivo generado    : {destino}")
    aviso(f" Tamano              : {tamano / 1024:.1f} KB")
    aviso(f" XML valido          : si (leido de nuevo con ElementTree)")
    if lista_m3u:
        aviso(
            f" Lista M3U generada  : {lista_m3u} ({m3u_entradas} entradas, "
            f"{m3u_canales} canales)"
        )
        if argumentos.epg_publica:
            aviso(f" Guia declarada      : {argumentos.epg_publica}")
    aviso("")
    aviso(" Primeros canales generados:")
    for canal in canales[:10]:
        marca_logo = "logo si" if canal["logo"] else "sin logo"
        marca_epg = emparejados.get(canal["id"], "-")
        aviso(
            f"   - {canal['id']:<24} {canal['nombre']}  "
            f"[{marca_logo} / {canal['origen']} / epg: {marca_epg}]"
        )
    if len(canales) > 10:
        aviso(f"   ... y {len(canales) - 10} canales mas.")
    aviso("")
    aviso(" Listo. Publica 'epg.xml' y 'lista.m3u' en GitHub (o con GitHub Pages) y")
    aviso(" apunta tu reproductor IPTV a la URL de la lista: la EPG ya va incluida")
    aviso(" en su cabecera (x-tvg-url) y en el tvg-id de cada canal.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nOperacion cancelada por el usuario.", file=sys.stderr)
        sys.exit(130)

