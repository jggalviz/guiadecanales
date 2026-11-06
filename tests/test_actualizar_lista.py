#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de ``actualizar_lista.py`` (solo libreria estandar).

Se ejecutan con::

    python -m unittest discover -s tests -v
    # o tambien
    python tests/test_actualizar_lista.py

Ninguna prueba sale a Internet: la comprobacion HTTP se simula sustituyendo
``actualizar_lista._probar``, que es la unica funcion que abre conexiones (junto
con ``_leer_texto``, tambien simulada cuando hace falta).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actualizar_lista  # noqa: E402  (import tras ajustar sys.path)
import generar_epg  # noqa: E402


#: Lista de ejemplo: dos senales del mismo canal (para comprobar que no se
#: separan al ordenar), un canal de otro grupo y una entrada con opciones.
LISTA_EJEMPLO = """#EXTM3U x-tvg-url="https://example.com/epg.xml"
#EXTINF:-1 tvg-id="espn.us" tvg-name="ESPN" group-title="Deportes",ESPN
#EXTVLCOPT:http-user-agent=MiPlayer/1.0
http://a.example/espn.m3u8
#EXTINF:-1 tvg-id="espn.us" tvg-name="ESPN" group-title="Deportes",ESPN Opc. 2
http://b.example/espn.m3u8
#EXTINF:-1 tvg-id="nba.us" tvg-name="NBA TV" group-title="NBA",NBA TV
http://c.example/nba.m3u8
#EXTINF:-1 tvg-id="canal5.mx" tvg-name="Canal 5" group-title="Cine / Peliculas",Canal 5
http://d.example/canal5.m3u8
"""


def _escribir_json(datos: dict, carpeta: str, nombre: str = "cabeceras.json") -> str:
    """Guarda ``datos`` como JSON en ``carpeta`` y devuelve la ruta."""
    ruta = os.path.join(carpeta, nombre)
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo)
    return ruta


class ParseoTests(unittest.TestCase):
    """Lectura de la lista: cabecera, opciones y atributos de cada entrada."""

    def setUp(self) -> None:
        self.cabecera, self.entradas = actualizar_lista.parsear_entradas(LISTA_EJEMPLO)

    def test_separa_la_cabecera_de_las_entradas(self) -> None:
        self.assertEqual(self.cabecera, ['#EXTM3U x-tvg-url="https://example.com/epg.xml"'])
        self.assertEqual(len(self.entradas), 4)

    def test_conserva_la_extinf_las_opciones_y_la_url(self) -> None:
        primera = self.entradas[0]
        self.assertIn('tvg-id="espn.us"', primera.extinf)
        self.assertTrue(primera.extinf.endswith(",ESPN"))
        self.assertEqual(primera.opciones, ("#EXTVLCOPT:http-user-agent=MiPlayer/1.0",))
        self.assertEqual(primera.url, "http://a.example/espn.m3u8")
        self.assertEqual(primera.lineas()[0], primera.extinf)

    def test_extrae_grupo_y_tvg_id(self) -> None:
        grupos = [entrada.grupo for entrada in self.entradas]
        self.assertEqual(grupos, ["Deportes", "Deportes", "NBA", "Cine / Peliculas"])
        self.assertEqual(self.entradas[2].tvg_id, "nba.us")

    def test_extinf_sin_url_no_se_publica(self) -> None:
        cabecera, entradas = actualizar_lista.parsear_entradas(
            "#EXTM3U\n#EXTINF:-1,Canal huerfano\n#EXTINF:-1,Canal bueno\nhttp://x.example/a.m3u8\n"
        )
        self.assertEqual(cabecera, ["#EXTM3U"])
        self.assertEqual([entrada.url for entrada in entradas], ["http://x.example/a.m3u8"])

    def test_texto_vacio_no_falla(self) -> None:
        self.assertEqual(actualizar_lista.parsear_entradas(""), ([], []))

    def test_preparar_cabecera_pone_extm3u_primero(self) -> None:
        cabecera = actualizar_lista.preparar_cabecera(
            ["# comentario del generador", '#EXTM3U x-tvg-url="https://example.com/epg.xml"']
        )
        self.assertEqual(cabecera[0], '#EXTM3U x-tvg-url="https://example.com/epg.xml"')
        self.assertEqual(cabecera[1], "# comentario del generador")

    def test_preparar_cabecera_anade_la_guia_si_falta(self) -> None:
        cabecera = actualizar_lista.preparar_cabecera([], "https://example.com/epg.xml")
        self.assertEqual(cabecera, ['#EXTM3U x-tvg-url="https://example.com/epg.xml" url-tvg="https://example.com/epg.xml"'])

    def test_preparar_cabecera_no_duplica_la_guia(self) -> None:
        original = ['#EXTM3U x-tvg-url="https://otra.example/epg.xml"']
        self.assertEqual(actualizar_lista.preparar_cabecera(original, "https://example.com/epg.xml"), original)


class OrdenYDuplicadosTests(unittest.TestCase):
    """Orden por jerarquia de grupos y eliminacion de entradas identicas."""

    def setUp(self) -> None:
        _cabecera, self.entradas = actualizar_lista.parsear_entradas(LISTA_EJEMPLO)

    def test_ordena_por_la_jerarquia_de_grupos(self) -> None:
        ordenadas = actualizar_lista.ordenar_entradas(self.entradas)
        claves = [generar_epg.orden_del_grupo(entrada.grupo) for entrada in ordenadas]
        self.assertEqual(claves, sorted(claves))
        # "NBA" esta en ORDEN_GRUPOS_M3U y "Cine / Peliculas" no: va antes.
        grupos = [entrada.grupo for entrada in ordenadas]
        self.assertLess(grupos.index("NBA"), grupos.index("Cine / Peliculas"))

    def test_mantiene_juntas_las_senales_del_mismo_canal(self) -> None:
        ordenadas = actualizar_lista.ordenar_entradas(self.entradas)
        indices = [indice for indice, entrada in enumerate(ordenadas) if entrada.tvg_id == "espn.us"]
        self.assertEqual(len(indices), 2)
        self.assertEqual(indices[1] - indices[0], 1)
        # Y se conserva el orden original entre ellas (la "Opc. 2" despues).
        self.assertEqual(ordenadas[indices[0]].url, "http://a.example/espn.m3u8")
        self.assertTrue(ordenadas[indices[1]].extinf.endswith(",ESPN Opc. 2"))

    def test_el_orden_es_estable(self) -> None:
        una_vez = actualizar_lista.ordenar_entradas(self.entradas)
        dos_veces = actualizar_lista.ordenar_entradas(una_vez)
        self.assertEqual([entrada.lineas() for entrada in una_vez], [entrada.lineas() for entrada in dos_veces])

    def test_quita_las_entradas_identicas(self) -> None:
        repetida = actualizar_lista.Entrada(
            extinf=self.entradas[2].extinf,
            url=self.entradas[2].url,
            opciones=self.entradas[2].opciones,
        )
        unicas = actualizar_lista.quitar_duplicados([*self.entradas, repetida])
        self.assertEqual(len(unicas), len(self.entradas))

    def test_no_quita_dos_calidades_distintas_del_mismo_canal(self) -> None:
        unicas = actualizar_lista.quitar_duplicados(self.entradas)
        self.assertEqual(len(unicas), len(self.entradas))


class EscrituraTests(unittest.TestCase):
    """La lista publicada se conserva linea a linea (UTF-8 y saltos LF)."""

    def test_ida_y_vuelta_sin_perdidas(self) -> None:
        cabecera, entradas = actualizar_lista.parsear_entradas(LISTA_EJEMPLO)
        with tempfile.TemporaryDirectory() as carpeta:
            destino = os.path.join(carpeta, "lista.m3u")
            escritas = actualizar_lista.escribir_lista(destino, cabecera, entradas)
            self.assertEqual(escritas, len(entradas))
            with open(destino, "r", encoding="utf-8", newline="") as archivo:
                texto = archivo.read()
            self.assertNotIn("\r", texto)
            self.assertTrue(texto.endswith("\n"))
            cabecera2, entradas2 = actualizar_lista.parsear_entradas(texto)
            self.assertEqual(cabecera2, cabecera)
            self.assertEqual(
                [entrada.lineas() for entrada in entradas],
                [entrada.lineas() for entrada in entradas2],
            )
            self.assertFalse(os.path.exists(destino + ".tmp"))

    def test_conserva_los_acentos(self) -> None:
        cabecera, entradas = actualizar_lista.parsear_entradas(
            "#EXTM3U\n#EXTINF:-1 tvg-name=\"Acción\",Acción TV\nhttp://x.example/a.m3u8\n"
        )
        with tempfile.TemporaryDirectory() as carpeta:
            destino = os.path.join(carpeta, "lista.m3u")
            actualizar_lista.escribir_lista(destino, cabecera, entradas)
            with open(destino, "r", encoding="utf-8") as archivo:
                self.assertIn("Acción TV", archivo.read())


class ClasificacionTests(unittest.TestCase):
    """Traduccion de codigos y cuerpos HTTP a estados de la senal."""

    def test_reconoce_las_urls_hls(self) -> None:
        self.assertTrue(actualizar_lista.parece_hls("http://a.example/x.m3u8?token=1"))
        self.assertTrue(actualizar_lista.parece_hls("http://a.example/lista3.php?c=41&f=.m3u8"))
        self.assertFalse(actualizar_lista.parece_hls("http://a.example/canal.ts"))

    def test_codigos_http(self) -> None:
        casos = {
            200: actualizar_lista.ESTADO_OK,
            206: actualizar_lista.ESTADO_OK,
            400: actualizar_lista.ESTADO_CAIDO,
            404: actualizar_lista.ESTADO_CAIDO,
            410: actualizar_lista.ESTADO_CAIDO,
            401: actualizar_lista.ESTADO_PROTEGIDO,
            403: actualizar_lista.ESTADO_PROTEGIDO,
            429: actualizar_lista.ESTADO_DUDOSO,
            500: actualizar_lista.ESTADO_DUDOSO,
            503: actualizar_lista.ESTADO_DUDOSO,
        }
        for codigo, esperado in casos.items():
            with self.subTest(codigo=codigo):
                self.assertEqual(actualizar_lista._estado_del_codigo(codigo)[0], esperado)

    def test_manifiesto_hls_es_ok(self) -> None:
        estado, detalle = actualizar_lista._evaluar_respuesta(
            "http://a.example/x.m3u8", 200, "application/vnd.apple.mpegurl", b"#EXTM3U\n#EXT-X-VERSION:3\n"
        )
        self.assertEqual(estado, actualizar_lista.ESTADO_OK)
        self.assertIn("hls", detalle)

    def test_tipo_mpegurl_vale_aunque_no_llegue_cuerpo(self) -> None:
        estado, _detalle = actualizar_lista._evaluar_respuesta(
            "http://a.example/x.m3u8", 200, "application/x-mpegURL", b""
        )
        self.assertEqual(estado, actualizar_lista.ESTADO_OK)

    def test_pagina_html_en_lugar_del_manifiesto_es_caido(self) -> None:
        for cuerpo in (b"<!DOCTYPE html><html><body>Cuenta caducada</body></html>", b'{"error": "sin sesion"}'):
            with self.subTest(cuerpo=cuerpo[:12]):
                estado, detalle = actualizar_lista._evaluar_respuesta(
                    "http://a.example/x.m3u8", 200, "text/html", cuerpo
                )
                self.assertEqual(estado, actualizar_lista.ESTADO_CAIDO)
                self.assertIn("no hls", detalle)

    def test_enlace_que_no_es_hls_basta_con_200(self) -> None:
        estado, _detalle = actualizar_lista._evaluar_respuesta(
            "http://a.example/canal.ts", 200, "video/mp2t", b"G@"
        )
        self.assertEqual(estado, actualizar_lista.ESTADO_OK)

    def test_detecta_el_fallo_de_dns(self) -> None:
        self.assertTrue(actualizar_lista._es_error_dns("[Errno -2] Name or service not known"))
        self.assertFalse(actualizar_lista._es_error_dns("Connection reset by peer"))

    def test_fallo_definitivo_solo_con_404_410_400_o_dns(self) -> None:
        definitivos = (404, 410, 400)
        for codigo in definitivos:
            with self.subTest(codigo=codigo):
                self.assertTrue(actualizar_lista._fallo_definitivo(actualizar_lista.Resultado(url="u", codigo=codigo)))
        self.assertTrue(
            actualizar_lista._fallo_definitivo(
                actualizar_lista.Resultado(url="u", estado=actualizar_lista.ESTADO_CAIDO, detalle="dominio inexistente")
            )
        )
        for codigo in (0, 403, 500):
            with self.subTest(codigo=codigo):
                self.assertFalse(actualizar_lista._fallo_definitivo(actualizar_lista.Resultado(url="u", codigo=codigo)))

    def test_politicas_de_descarte(self) -> None:
        casos = {
            (actualizar_lista.ESTADO_CAIDO, "conservadora"): True,
            (actualizar_lista.ESTADO_PROTEGIDO, "conservadora"): False,
            (actualizar_lista.ESTADO_PROTEGIDO, "normal"): True,
            (actualizar_lista.ESTADO_DUDOSO, "normal"): False,
            (actualizar_lista.ESTADO_DUDOSO, "estricta"): True,
            (actualizar_lista.ESTADO_OK, "estricta"): False,
            (actualizar_lista.ESTADO_SIN_COMPROBAR, "estricta"): False,
        }
        for (estado, politica), esperado in casos.items():
            with self.subTest(estado=estado, politica=politica):
                self.assertEqual(actualizar_lista.Resultado(url="u", estado=estado).es_descartable(politica), esperado)


class CabecerasTests(unittest.TestCase):
    """Cabeceras de la entrada, reglas por host y cascada de perfiles."""

    def test_lee_las_cabeceras_de_las_opciones(self) -> None:
        opciones = (
            "#EXTVLCOPT:http-user-agent=MiPlayer/1.0",
            "#EXTVLCOPT:http-referrer=http://a.example/",
            "#KODIPROP:inputstream.adaptive.license_type=clearkey",
        )
        self.assertEqual(
            actualizar_lista._cabeceras_de_opciones(opciones),
            {"User-Agent": "MiPlayer/1.0", "Referer": "http://a.example/"},
        )

    def test_opciones_con_cabeceras_sustituye_y_ordena(self) -> None:
        opciones = (
            "#EXTVLCOPT:http-user-agent=Viejo/1.0",
            "#EXTVLCOPT:http-referrer=http://viejo.example/",
            "#KODIPROP:inputstream.adaptive.license_type=clearkey",
        )
        nuevas = actualizar_lista.opciones_con_cabeceras(opciones, "Nuevo/2.0", "http://nuevo.example/")
        self.assertEqual(
            nuevas,
            (
                "#KODIPROP:inputstream.adaptive.license_type=clearkey",
                "#EXTVLCOPT:http-user-agent=Nuevo/2.0",
                "#EXTVLCOPT:http-referrer=http://nuevo.example/",
            ),
        )

    def test_opciones_con_cabeceras_sin_cabeceras_no_cambia_nada(self) -> None:
        opciones = ("#EXTVLCOPT:http-user-agent=MiPlayer/1.0",)
        self.assertEqual(actualizar_lista.opciones_con_cabeceras(opciones), opciones)

    def test_carga_el_json_de_cabeceras(self) -> None:
        datos = {
            "$esquema": "documentacion",
            "por_defecto": {"user-agent": "PorDefecto/1.0"},
            "hosts": {
                "CDN.Ejemplo.TV": {"user-agent": "Cdn/1.0", "referer": "http://cdn.ejemplo.tv/"},
                "_nota": "se ignora",
            },
        }
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = _escribir_json(datos, carpeta)
            reglas, por_defecto = actualizar_lista.cargar_cabeceras(ruta)
        self.assertEqual(sorted(reglas), ["cdn.ejemplo.tv"])
        self.assertEqual(reglas["cdn.ejemplo.tv"]["referer"], "http://cdn.ejemplo.tv/")
        self.assertEqual(por_defecto, {"user-agent": "PorDefecto/1.0"})

    def test_carga_el_formato_plano_de_cabeceras(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = _escribir_json({"cdn.ejemplo.tv": {"user-agent": "Cdn/1.0"}}, carpeta)
            reglas, por_defecto = actualizar_lista.cargar_cabeceras(ruta)
        self.assertEqual(list(reglas), ["cdn.ejemplo.tv"])
        self.assertEqual(por_defecto, {})

    def test_sin_archivo_o_json_roto_devuelve_vacio(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            roto = os.path.join(carpeta, "roto.json")
            with open(roto, "w", encoding="utf-8") as archivo:
                archivo.write("{ no es json ")
            for ruta in ("", os.path.join(carpeta, "no-existe.json"), roto):
                with self.subTest(ruta=ruta):
                    self.assertEqual(actualizar_lista.cargar_cabeceras(ruta), ({}, {}))

    def test_regla_del_host_admite_subdominios_y_puertos(self) -> None:
        reglas = {"ejemplo.tv": {"user-agent": "Cdn/1.0"}}
        for url in (
            "http://ejemplo.tv/a.m3u8",
            "https://cdn.ejemplo.tv/a.m3u8",
            "http://cdn.ejemplo.tv:8080/a.m3u8",
            "http://usuario:clave@ejemplo.tv/a.m3u8",
        ):
            with self.subTest(url=url):
                self.assertEqual(actualizar_lista._regla_del_host(url, reglas), reglas["ejemplo.tv"])
        self.assertEqual(actualizar_lista._regla_del_host("http://otro.example/a.m3u8", reglas), {})

    def test_los_hosts_parecidos_no_se_confunden(self) -> None:
        reglas = {"ejemplo.tv": {"user-agent": "Cdn/1.0"}}
        self.assertEqual(actualizar_lista._regla_del_host("http://noejemplo.tv/a.m3u8", reglas), {})

    def test_cascada_de_perfiles_va_de_lo_especifico_a_lo_generico(self) -> None:
        url = "http://cdn.ejemplo.tv/canal.m3u8"
        perfiles = actualizar_lista.perfiles_de_peticion(
            url,
            ("#EXTVLCOPT:http-user-agent=Lista/1.0",),
            {"ejemplo.tv": {"referer": "http://cdn.ejemplo.tv/"}},
            {"user-agent": "PorDefecto/1.0"},
        )
        etiquetas = [etiqueta for etiqueta, _cabeceras in perfiles]
        self.assertEqual(
            etiquetas,
            ["lista", "host", "por_defecto", "navegador", "navegador-simple", "vlc", "movil", "vlc-simple"],
        )
        cabeceras = dict(perfiles)
        self.assertEqual(cabeceras["lista"]["User-Agent"], "Lista/1.0")
        self.assertEqual(cabeceras["host"]["Referer"], "http://cdn.ejemplo.tv/")
        self.assertEqual(cabeceras["navegador"]["Origin"], "http://cdn.ejemplo.tv")
        self.assertEqual(cabeceras["navegador"]["Referer"], "http://cdn.ejemplo.tv/")
        self.assertEqual(cabeceras["vlc"]["User-Agent"], actualizar_lista.USER_AGENT_VLC)
        # Las cabeceras base van siempre, para no descargar el canal entero.
        for _etiqueta, enviadas in perfiles:
            self.assertEqual(enviadas["Accept-Encoding"], "identity")

    def test_cascada_sin_reglas_ni_opciones(self) -> None:
        perfiles = actualizar_lista.perfiles_de_peticion("http://cdn.ejemplo.tv/canal.m3u8")
        self.assertEqual(
            [etiqueta for etiqueta, _cabeceras in perfiles],
            ["navegador", "navegador-simple", "vlc", "movil", "vlc-simple"],
        )

    def test_cascada_elimina_los_perfiles_repetidos(self) -> None:
        # La regla del host usa el mismo UA que el perfil de navegador simple,
        # asi que ese perfil sobra y no se vuelve a pedir.
        perfiles = actualizar_lista.perfiles_de_peticion(
            "http://cdn.ejemplo.tv/canal.m3u8",
            (),
            {"cdn.ejemplo.tv": {"user-agent": actualizar_lista.USER_AGENT_NAVEGADOR}},
        )
        etiquetas = [etiqueta for etiqueta, _cabeceras in perfiles]
        self.assertEqual(etiquetas[:2], ["host", "navegador"])
        self.assertNotIn("navegador-simple", etiquetas)


def _resultado(url: str, estado: str, codigo: int = 0, detalle: str = "") -> actualizar_lista.Resultado:
    """Atajo para construir un resultado de comprobacion en las pruebas."""
    return actualizar_lista.Resultado(url=url, estado=estado, codigo=codigo, detalle=detalle or estado)


class ComprobacionTests(unittest.TestCase):
    """Cascada de cabeceras y politica de reintentos (sin salir a Internet)."""

    URL = "http://cdn.ejemplo.tv/canal.m3u8"

    def _fallo(self, estado: str, codigo: int = 0, detalle: str = ""):
        """Devuelve una funcion que simula ``_probar`` fallando siempre igual."""
        return lambda url, cabeceras, timeout: _resultado(url, estado, codigo, detalle)

    def test_usa_el_primer_perfil_que_funciona(self) -> None:
        fallo = self._fallo(actualizar_lista.ESTADO_OK, 200)
        with mock.patch.object(actualizar_lista, "_probar", side_effect=fallo) as falso:
            resultado = actualizar_lista.comprobar_enlace(self.URL)
        self.assertEqual(falso.call_count, 1)
        self.assertEqual(resultado.estado, actualizar_lista.ESTADO_OK)
        self.assertEqual(resultado.perfil, "navegador")
        self.assertEqual(resultado.user_agent, actualizar_lista.USER_AGENT_NAVEGADOR)
        self.assertEqual(resultado.referer, "http://cdn.ejemplo.tv/")

    def test_devuelve_las_cabeceras_del_perfil_que_funciona(self) -> None:
        respuestas = [
            _resultado(self.URL, actualizar_lista.ESTADO_PROTEGIDO, 403),
            _resultado(self.URL, actualizar_lista.ESTADO_OK, 200),
        ]
        with mock.patch.object(actualizar_lista, "_probar", side_effect=respuestas) as falso:
            resultado = actualizar_lista.comprobar_enlace(self.URL)
        self.assertEqual(falso.call_count, 2)
        self.assertEqual(resultado.estado, actualizar_lista.ESTADO_OK)
        self.assertEqual(resultado.perfil, "navegador-simple")
        self.assertEqual(resultado.user_agent, actualizar_lista.USER_AGENT_NAVEGADOR)
        self.assertEqual(resultado.referer, "")

    def test_un_404_no_prueba_las_demas_cabeceras(self) -> None:
        fallo = self._fallo(actualizar_lista.ESTADO_CAIDO, 404)
        with mock.patch.object(actualizar_lista, "_probar", side_effect=fallo) as falso:
            resultado = actualizar_lista.comprobar_enlace(self.URL)
        self.assertEqual(falso.call_count, 1)
        self.assertEqual(resultado.estado, actualizar_lista.ESTADO_CAIDO)
        self.assertEqual(resultado.codigo, 404)
        self.assertEqual(resultado.intentos, 1)

    def test_un_dominio_inexistente_tampoco_se_reintenta(self) -> None:
        fallo = self._fallo(actualizar_lista.ESTADO_CAIDO, 0, "dominio inexistente")
        with mock.patch.object(actualizar_lista, "_probar", side_effect=fallo) as falso:
            resultado = actualizar_lista.comprobar_enlace(self.URL)
        self.assertEqual(falso.call_count, 1)
        self.assertEqual(resultado.detalle, "dominio inexistente")

    def test_prueba_todas_las_cabeceras_antes_de_darla_por_protegida(self) -> None:
        perfiles = actualizar_lista.perfiles_de_peticion(self.URL)
        fallo = self._fallo(actualizar_lista.ESTADO_PROTEGIDO, 403)
        with mock.patch.object(actualizar_lista, "_probar", side_effect=fallo) as falso:
            resultado = actualizar_lista.comprobar_enlace(self.URL)
        self.assertEqual(falso.call_count, len(perfiles))
        self.assertEqual(resultado.estado, actualizar_lista.ESTADO_PROTEGIDO)

    def test_reintenta_los_fallos_pasajeros(self) -> None:
        respuestas = [
            _resultado(self.URL, actualizar_lista.ESTADO_DUDOSO, 0, "timeout"),
            _resultado(self.URL, actualizar_lista.ESTADO_OK, 200),
        ]
        with mock.patch.object(actualizar_lista, "_probar", side_effect=respuestas) as falso:
            resultado = actualizar_lista.comprobar_enlace(self.URL, intentos=2)
        self.assertEqual(falso.call_count, 2)
        self.assertEqual(resultado.estado, actualizar_lista.ESTADO_OK)
        self.assertEqual(resultado.perfil, "navegador")

    def test_informa_del_motivo_mas_definitivo(self) -> None:
        respuestas = [_resultado(self.URL, actualizar_lista.ESTADO_DUDOSO, 0)]
        respuestas.append(_resultado(self.URL, actualizar_lista.ESTADO_PROTEGIDO, 403))
        respuestas += [_resultado(self.URL, actualizar_lista.ESTADO_DUDOSO, 0)] * 5
        with mock.patch.object(actualizar_lista, "_probar", side_effect=respuestas):
            resultado = actualizar_lista.comprobar_enlace(self.URL, intentos=1)
        self.assertEqual(resultado.estado, actualizar_lista.ESTADO_PROTEGIDO)

    def test_renueva_el_token_cuando_el_host_lo_permite(self) -> None:
        reglas = {"ejemplo.tv": {"renovar": "http://api.ejemplo.tv/sesion?canal=demo"}}
        original = "http://cdn.ejemplo.tv/canal.m3u8?token=viejo"
        nueva = "http://cdn.ejemplo.tv/canal.m3u8?token=nuevo123456"

        def falso(url, cabeceras, timeout):
            if "token=nuevo123456" in url:
                return _resultado(url, actualizar_lista.ESTADO_OK, 200)
            return _resultado(url, actualizar_lista.ESTADO_PROTEGIDO, 403)

        with mock.patch.object(actualizar_lista, "_probar", side_effect=falso):
            with mock.patch.object(actualizar_lista, "_leer_texto", return_value='{"token": "nuevo123456"}'):
                resultado = actualizar_lista.comprobar_enlace(original, reglas=reglas)
        self.assertEqual(resultado.estado, actualizar_lista.ESTADO_OK)
        self.assertEqual(resultado.perfil, "renovado")
        self.assertEqual(resultado.url, original)
        self.assertEqual(resultado.url_final, nueva)

    def test_sin_renovar_no_pide_tokens(self) -> None:
        reglas = {"ejemplo.tv": {"renovar": "http://api.ejemplo.tv/sesion"}}
        fallo = self._fallo(actualizar_lista.ESTADO_PROTEGIDO, 403)
        with mock.patch.object(actualizar_lista, "_probar", side_effect=fallo):
            with mock.patch.object(actualizar_lista, "_leer_texto") as lector:
                resultado = actualizar_lista.comprobar_enlace(
                    "http://cdn.ejemplo.tv/canal.m3u8?token=viejo", reglas=reglas, renovar=False
                )
        lector.assert_not_called()
        self.assertEqual(resultado.estado, actualizar_lista.ESTADO_PROTEGIDO)


class RenovacionDeTokenTests(unittest.TestCase):
    """Reescritura del token de sesion a partir de ``cabeceras_streams.json``."""

    def test_sin_plantilla_no_hace_nada(self) -> None:
        self.assertEqual(actualizar_lista.renovar_token("http://a.example/x.m3u8?token=1", {}), "")
        self.assertEqual(
            actualizar_lista.renovar_token("http://a.example/x.m3u8?token=1", {"renovar": "  "}), ""
        )

    def test_sin_parametro_de_sesion_no_hace_nada(self) -> None:
        regla = {"renovar": "http://api.example/sesion"}
        with mock.patch.object(actualizar_lista, "_leer_texto") as lector:
            self.assertEqual(actualizar_lista.renovar_token("http://a.example/x.m3u8?c=1", regla), "")
        lector.assert_not_called()

    def test_usa_los_parametros_de_la_url_en_la_plantilla(self) -> None:
        regla = {"renovar": "http://api.example/sesion?canal={c}&token={token}"}
        with mock.patch.object(actualizar_lista, "_leer_texto", return_value='{"token": "abcdef123456"}') as lector:
            nueva = actualizar_lista.renovar_token("http://a.example/x.m3u8?c=41&token=viejo", regla)
        self.assertEqual(lector.call_args[0][0], "http://api.example/sesion?canal=41&token=viejo")
        self.assertEqual(nueva, "http://a.example/x.m3u8?c=41&token=abcdef123456")

    def test_toma_el_token_de_una_redireccion(self) -> None:
        regla = {"renovar": "http://api.example/sesion"}
        with mock.patch.object(actualizar_lista, "_leer_texto", return_value="?st=fresco987654"):
            nueva = actualizar_lista.renovar_token("http://a.example/x.m3u8?st=viejo123456", regla)
        self.assertEqual(nueva, "http://a.example/x.m3u8?st=fresco987654")

    def test_sin_token_en_la_respuesta_no_reescribe(self) -> None:
        regla = {"renovar": "http://api.example/sesion"}
        with mock.patch.object(actualizar_lista, "_leer_texto", return_value="<html>error</html>"):
            self.assertEqual(actualizar_lista.renovar_token("http://a.example/x.m3u8?token=viejo", regla), "")


class DepuracionTests(unittest.TestCase):
    """Aplicacion de los resultados de la comprobacion a la lista."""

    def setUp(self) -> None:
        _cabecera, self.entradas = actualizar_lista.parsear_entradas(LISTA_EJEMPLO)

    def _resultados(self, estados: dict[str, str], **extra) -> dict:
        """Construye el mapa ``clave -> Resultado`` con un estado por URL."""
        resultados = {}
        for entrada in self.entradas:
            estado = estados.get(entrada.url, actualizar_lista.ESTADO_OK)
            resultados[entrada.clave()] = actualizar_lista.Resultado(
                url=entrada.url, estado=estado, **{clave: valor for clave, valor in extra.items() if clave == "detalle"}
            )
        return resultados

    def test_descarta_las_caidas_y_conserva_el_resto(self) -> None:
        resultados = self._resultados({"http://b.example/espn.m3u8": actualizar_lista.ESTADO_CAIDO})
        vivas, descartadas, conteo = actualizar_lista.depurar_entradas(self.entradas, resultados)
        self.assertEqual(len(vivas), 3)
        self.assertEqual(len(descartadas), 1)
        self.assertEqual(descartadas[0][0].url, "http://b.example/espn.m3u8")
        self.assertEqual(conteo[actualizar_lista.ESTADO_CAIDO], 1)
        self.assertEqual(conteo[actualizar_lista.ESTADO_OK], 3)

    def test_la_politica_decide_que_se_descarta(self) -> None:
        resultados = self._resultados(
            {
                "http://b.example/espn.m3u8": actualizar_lista.ESTADO_PROTEGIDO,
                "http://c.example/nba.m3u8": actualizar_lista.ESTADO_DUDOSO,
            }
        )
        casos = {"conservadora": 0, "normal": 1, "estricta": 2}
        for politica, esperadas in casos.items():
            with self.subTest(politica=politica):
                vivas, descartadas, _conteo = actualizar_lista.depurar_entradas(
                    self.entradas, resultados, politica
                )
                self.assertEqual(len(descartadas), esperadas)
                self.assertEqual(len(vivas), len(self.entradas) - esperadas)

    def test_las_senales_sin_comprobar_se_conservan(self) -> None:
        resultados = self._resultados({}, detalle="sin comprobar (--limite-pruebas)")
        for resultado in resultados.values():
            resultado.estado = actualizar_lista.ESTADO_SIN_COMPROBAR
        vivas, descartadas, _conteo = actualizar_lista.depurar_entradas(
            self.entradas, resultados, "estricta"
        )
        self.assertEqual(len(descartadas), 0)
        self.assertEqual(len(vivas), len(self.entradas))

    def test_publica_las_cabeceras_que_han_funcionado(self) -> None:
        entrada = self.entradas[2]
        resultado = actualizar_lista.Resultado(
            url=entrada.url,
            estado=actualizar_lista.ESTADO_OK,
            perfil="host",
            user_agent="Cdn/1.0",
            referer="http://c.example/",
        )
        nueva = actualizar_lista.aplicar_resultado(entrada, resultado)
        self.assertEqual(nueva.extinf, entrada.extinf)
        self.assertEqual(nueva.url, entrada.url)
        self.assertEqual(
            nueva.opciones,
            ("#EXTVLCOPT:http-user-agent=Cdn/1.0", "#EXTVLCOPT:http-referrer=http://c.example/"),
        )

    def test_no_publica_cabeceras_si_valia_la_de_la_lista(self) -> None:
        entrada = self.entradas[0]
        resultado = actualizar_lista.Resultado(
            url=entrada.url, estado=actualizar_lista.ESTADO_OK, perfil="lista"
        )
        self.assertEqual(actualizar_lista.aplicar_resultado(entrada, resultado), entrada)

    def test_publica_la_url_con_el_token_renovado(self) -> None:
        entrada = self.entradas[0]
        resultado = actualizar_lista.Resultado(
            url=entrada.url,
            estado=actualizar_lista.ESTADO_OK,
            perfil="renovado",
            url_final="http://a.example/espn.m3u8?token=nuevo",
        )
        nueva = actualizar_lista.aplicar_resultado(entrada, resultado)
        self.assertEqual(nueva.url, "http://a.example/espn.m3u8?token=nuevo")
        # El #EXTINF no se toca: el tvg-id sigue apuntando a la misma guia.
        self.assertEqual(nueva.extinf, entrada.extinf)
        self.assertEqual(nueva.tvg_id, entrada.tvg_id)


class InformeTests(unittest.TestCase):
    """Resumen de la ejecucion: JSON y texto para la consola."""

    def _informe(self) -> dict:
        entrada = actualizar_lista.Entrada(
            extinf='#EXTINF:-1 tvg-id="nba.us" group-title="NBA",NBA TV',
            url="http://c.example/nba.m3u8",
            grupo="NBA",
            tvg_id="nba.us",
        )
        muerta = actualizar_lista.Resultado(
            url=entrada.url, estado=actualizar_lista.ESTADO_CAIDO, codigo=404, detalle="http 404"
        )
        resultados = {
            (entrada.url, ()): muerta,
            ("http://a.example/espn.m3u8", ()): actualizar_lista.Resultado(
                url="http://a.example/espn.m3u8",
                estado=actualizar_lista.ESTADO_OK,
                perfil="host",
                user_agent="Cdn/1.0",
            ),
            ("http://d.example/canal5.m3u8", ()): actualizar_lista.Resultado(
                url="http://d.example/canal5.m3u8", estado=actualizar_lista.ESTADO_OK, perfil="renovado"
            ),
        }
        return actualizar_lista.construir_informe(
            origen="lista.m3u",
            salida="lista.m3u",
            politica="normal",
            entradas_origen=4,
            entradas_finales=2,
            duplicadas=1,
            resultados=resultados,
            conteo={
                actualizar_lista.ESTADO_OK: 2,
                actualizar_lista.ESTADO_CAIDO: 1,
                actualizar_lista.ESTADO_PROTEGIDO: 0,
                actualizar_lista.ESTADO_DUDOSO: 0,
                actualizar_lista.ESTADO_SIN_COMPROBAR: 0,
            },
            descartadas=[(entrada, muerta)],
            duracion=125.0,
        )

    def test_resume_la_ejecucion(self) -> None:
        informe = self._informe()
        self.assertEqual(informe["origen"], "lista.m3u")
        self.assertEqual(informe["senales_distintas"], 3)
        self.assertEqual(informe["descartadas"], 1)
        self.assertEqual(informe["porcentaje_descartes"], 25.0)
        self.assertEqual(informe["estados"]["caido"], 1)
        self.assertEqual(informe["cabeceras_aplicadas"], 1)
        self.assertEqual(informe["tokens_renovados"], 1)
        self.assertEqual(informe["duplicadas_eliminadas"], 1)
        self.assertEqual(informe["detalle_descartes"][0]["codigo"], 404)
        self.assertEqual(informe["detalle_descartes"][0]["grupo"], "NBA")
        self.assertIn("generado", informe)

    def test_sin_entradas_no_divide_por_cero(self) -> None:
        informe = actualizar_lista.construir_informe(
            origen="origen",
            salida="salida",
            politica="normal",
            entradas_origen=0,
            entradas_finales=0,
            duplicadas=0,
            resultados={},
            conteo={},
            descartadas=[],
            duracion=0.0,
        )
        self.assertEqual(informe["porcentaje_descartes"], 0.0)
        self.assertEqual(informe["estados"]["ok"], 0)

    def test_el_resumen_de_texto_tiene_las_cifras(self) -> None:
        lineas = actualizar_lista.formatear_resumen(self._informe())
        texto = "\n".join(lineas)
        self.assertEqual(lineas[0], "RESUMEN DE LA ACTUALIZACION")
        self.assertIn("entradas de origen    : 4", texto)
        self.assertIn("descartadas           : 1 (25.0 %)", texto)
        self.assertIn("duracion              : 2 min 5 s", texto)
        self.assertIn("Primeras senales descartadas:", texto)
        self.assertIn("http 404", texto)

    def test_guarda_el_informe_en_json(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "sub", "informe.json")
            actualizar_lista.escribir_informe(ruta, self._informe())
            with open(ruta, "r", encoding="utf-8") as archivo:
                datos = json.load(archivo)
        self.assertEqual(datos["descartadas"], 1)
        self.assertEqual(datos["politica"], "normal")


class FlujoCompletoTests(unittest.TestCase):
    """Ejecucion de ``main`` de principio a fin, con la red simulada."""

    def setUp(self) -> None:
        self.carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(self.carpeta.cleanup)
        self.entrada = os.path.join(self.carpeta.name, "entrada.m3u")
        with open(self.entrada, "w", encoding="utf-8") as archivo:
            archivo.write(LISTA_EJEMPLO)
        self.salida = os.path.join(self.carpeta.name, "lista.m3u")
        self.informe = os.path.join(self.carpeta.name, "informe.json")

    def _main(self, *extra: str) -> int:
        """Ejecuta el script sobre la lista de ejemplo, sin cabeceras externas."""
        argumentos = [
            "--url", "",
            "--entrada", self.entrada,
            "--salida", self.salida,
            "--cabeceras", "",
            "--informe", self.informe,
            "-q",
            *extra,
        ]
        return actualizar_lista.main(argumentos)

    def _leer(self, ruta: str) -> str:
        with open(ruta, "r", encoding="utf-8") as archivo:
            return archivo.read()

    def test_sin_verificar_ordena_y_conserva_las_entradas(self) -> None:
        self.assertEqual(self._main("--sin-verificar"), 0)
        cabecera, entradas = actualizar_lista.parsear_entradas(self._leer(self.salida))
        self.assertEqual(cabecera, ['#EXTM3U x-tvg-url="https://example.com/epg.xml"'])
        self.assertEqual(len(entradas), 4)
        grupos = [entrada.grupo for entrada in entradas]
        self.assertLess(grupos.index("NBA"), grupos.index("Cine / Peliculas"))
        with open(self.informe, "r", encoding="utf-8") as archivo:
            informe = json.load(archivo)
        self.assertEqual(informe["politica"], "sin verificar")
        self.assertEqual(informe["entradas_finales"], 4)

    def test_descarta_solo_la_senal_caida(self) -> None:
        def falso(url, cabeceras, timeout):
            if "b.example" in url:
                return _resultado(url, actualizar_lista.ESTADO_CAIDO, 404, "http 404")
            return _resultado(url, actualizar_lista.ESTADO_OK, 200, "http 200 hls")

        with mock.patch.object(actualizar_lista, "_probar", side_effect=falso):
            self.assertEqual(self._main(), 0)

        _cabecera, entradas = actualizar_lista.parsear_entradas(self._leer(self.salida))
        self.assertEqual(len(entradas), 3)
        self.assertNotIn("b.example", "".join(entrada.url for entrada in entradas))
        with open(self.informe, "r", encoding="utf-8") as archivo:
            informe = json.load(archivo)
        self.assertEqual(informe["descartadas"], 1)
        self.assertEqual(informe["estados"]["caido"], 1)
        self.assertEqual(informe["detalle_descartes"][0]["url"], "http://b.example/espn.m3u8")
        self.assertIn("#EXTVLCOPT", self._leer(self.salida))

    def test_publica_las_cabeceras_de_reserva(self) -> None:
        # El primer perfil falla y el segundo (navegador) funciona: la lista
        # publicada debe llevar el #EXTVLCOPT con el User-Agent que sirvio.
        def falso(url, cabeceras, timeout):
            if cabeceras.get("User-Agent") == actualizar_lista.USER_AGENT_VLC:
                return _resultado(url, actualizar_lista.ESTADO_OK, 200, "http 200 hls")
            return _resultado(url, actualizar_lista.ESTADO_PROTEGIDO, 403, "http 403")

        with mock.patch.object(actualizar_lista, "_probar", side_effect=falso):
            self.assertEqual(self._main(), 0)

        texto = self._leer(self.salida)
        self.assertIn(f"#EXTVLCOPT:http-user-agent={actualizar_lista.USER_AGENT_VLC}", texto)
        with open(self.informe, "r", encoding="utf-8") as archivo:
            informe = json.load(archivo)
        self.assertEqual(informe["cabeceras_aplicadas"], 4)

    def test_no_escribe_si_se_descartaria_casi_todo(self) -> None:
        def falso(url, cabeceras, timeout):
            return _resultado(url, actualizar_lista.ESTADO_CAIDO, 404, "http 404")

        with mock.patch.object(actualizar_lista, "_probar", side_effect=falso):
            self.assertEqual(self._main(), 2)
        self.assertFalse(os.path.exists(self.salida))

    def test_origen_inexistente_devuelve_1(self) -> None:
        argumentos = ["--url", "", "--entrada", os.path.join(self.carpeta.name, "nada.m3u"), "-q"]
        self.assertEqual(actualizar_lista.main(argumentos), 1)

    def test_lista_sin_entradas_devuelve_1(self) -> None:
        vacia = os.path.join(self.carpeta.name, "vacia.m3u")
        with open(vacia, "w", encoding="utf-8") as archivo:
            archivo.write("#EXTM3U\n")
        argumentos = ["--url", "", "--entrada", vacia, "--salida", self.salida, "-q"]
        self.assertEqual(actualizar_lista.main(argumentos), 1)
        self.assertFalse(os.path.exists(self.salida))


if __name__ == "__main__":
    unittest.main()
