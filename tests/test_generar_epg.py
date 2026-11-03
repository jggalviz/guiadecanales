#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas basicas de ``generar_epg.py`` (solo libreria estandar).

Se ejecutan con::

    python -m unittest discover -s tests -v
    # o tambien
    python tests/test_generar_epg.py
"""

from __future__ import annotations

import gzip
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generar_epg  # noqa: E402  (import tras ajustar sys.path)


class SaneadoDeCodificacionTests(unittest.TestCase):
    """Comprueba el filtro de mojibake aplicado al leer la lista M3U."""

    def test_corrige_cp1252_leido_como_cp850(self) -> None:
        casos = {
            "Azteca 7 MÚxico": "Azteca 7 México",
            "Anime Visi¾n Espa±a": "Anime Visión España",
            "Hispania TV Panamß": "Hispania TV Panamá",
            "Pantalla Clßsica No 24/7": "Pantalla Clásica No 24/7",
            "NSTV C·cuta": "NSTV Cúcuta",
            "Canal Trece AmazonÝa": "Canal Trece Amazonía",
            "M·sica Total": "Música Total",
        }
        for roto, esperado in casos.items():
            with self.subTest(roto=roto):
                self.assertEqual(generar_epg.sanear_codificacion(roto), esperado)

    def test_corrige_utf8_leido_como_cp1252(self) -> None:
        casos = {
            "Azteca 7 MÃ©xico": "Azteca 7 México",
            "Anime VisiÃ³n EspaÃ±a": "Anime Visión España",
            "PanamÃ¡ TV": "Panamá TV",
            "SeÃ±al 2": "Señal 2",
        }
        for roto, esperado in casos.items():
            with self.subTest(roto=roto):
                self.assertEqual(generar_epg.sanear_codificacion(roto), esperado)

    def test_no_toca_el_texto_bien_codificado(self) -> None:
        casos = (
            "Azteca 7 México",
            "Anime Visión España",
            "Panamá TV",
            "NSTV Cúcuta",
            "Fútbol Total",
            "Perú TV",
            # Mayusculas acentuadas legitimas (solo al inicio de palabra).
            "Único TV",
            "Última Hora",
            # Simbolos legitimos.
            "90° TV",
            "1º Canal",
            # Nombres ya procesados por el limpiador.
            "Canal 5",
            "ESPN 2",
            "Cine Latino",
        )
        for texto in casos:
            with self.subTest(texto=texto):
                self.assertEqual(generar_epg.sanear_codificacion(texto), texto)

    def test_es_idempotente(self) -> None:
        textos = ("Azteca 7 MÚxico", "MÃ©xico TV", "Canal 5 HD [LAT]", "Espa±a Total")
        for texto in textos:
            with self.subTest(texto=texto):
                una_vez = generar_epg.sanear_codificacion(texto)
                self.assertEqual(generar_epg.sanear_codificacion(una_vez), una_vez)

    def test_conserva_texto_vacio(self) -> None:
        self.assertEqual(generar_epg.sanear_codificacion(""), "")

    def test_se_aplica_al_leer_la_lista(self) -> None:
        m3u = "\n".join(
            [
                "#EXTM3U",
                '#EXTINF:-1 tvg-name="Azteca 7 MÚxico" group-title="MÚxico",Azteca 7 MÚxico HD',
                "http://ejemplo.invalid/azteca7/index.m3u8",
                '#EXTINF:-1 group-title="Cine",Cine Espa±a FHD',
                "http://ejemplo.invalid/cine-espana/index.m3u8",
                "",
            ]
        )
        canales = generar_epg.parsear_m3u(m3u)
        self.assertEqual([canal["nombre"] for canal in canales], ["Azteca 7 México HD", "Cine España FHD"])
        self.assertEqual(canales[0]["tvg_name"], "Azteca 7 México")
        self.assertEqual(canales[0]["grupo"], "México")

    def test_nombres_corregidos_generan_ids_esperados(self) -> None:
        canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(
                "\n".join(
                    [
                        "#EXTM3U",
                        "#EXTINF:-1,Azteca 7 MÚxico HD",
                        "http://ejemplo.invalid/azteca7/index.m3u8",
                        "#EXTINF:-1,Azteca 7 MÃ©xico SD",
                        "http://ejemplo.invalid/azteca7-sd/index.m3u8",
                        "#EXTINF:-1,Cine Espa±a FHD",
                        "http://ejemplo.invalid/cine-espana/index.m3u8",
                        "#EXTINF:-1,Cine EspaÃ±a SD",
                        "http://ejemplo.invalid/cine-espana-sd/index.m3u8",
                        "",
                    ]
                )
            )
        )
        identificadores = {canal["id"]: canal["nombre"] for canal in canales}
        # Las dos variantes rotas ("MÚxico" y "MÃ©xico") acaban en el mismo canal.
        self.assertEqual(len(canales), 2)
        self.assertEqual(identificadores["azteca7"], "Azteca 7")
        self.assertEqual(identificadores["cineespana"], "Cine España")

    def test_nombre_corregido_pierde_el_pais_redundante(self) -> None:
        # Antes del saneado, "MÚxico" no se reconocia como pais y se quedaba.
        sin_sanear = generar_epg.limpiar_nombre("Azteca 7 MÚxico")
        saneado = generar_epg.limpiar_nombre(generar_epg.sanear_codificacion("Azteca 7 MÚxico"))
        self.assertEqual(sin_sanear, "Azteca 7 MÚxico")
        self.assertEqual(saneado, "Azteca 7")
        self.assertEqual(generar_epg.limpiar_nombre("Azteca 7 México"), "Azteca 7")


class LimpiezaDeNombresTests(unittest.TestCase):
    """Comprueba la unificacion de nombres duplicados."""

    def test_elimina_calidad_y_opciones(self) -> None:
        casos = {
            "Canal 5 HD": "Canal 5",
            "Canal 5 FHD [Opc.2]": "Canal 5",
            "Canal 5 Full HD": "Canal 5",
            "Canal 5 HD2": "Canal 5",
            "Las Estrellas [Opc.2]": "Las Estrellas",
            "Mega Noticias (1080p)": "Mega Noticias",
            "Cine Latino SD": "Cine Latino",
            "TNT Series FHD (Opcion 3)": "TNT Series",
            "TNT Series (Señal 2)": "TNT Series",
            "TNT Series Feed 4": "TNT Series",
            "HBO SignatureHD": "HBO Signature",
            "ESPN 2 HD [LAT]": "ESPN 2",
            "ESPN 2 FHD": "ESPN 2",
            "ESPN 2 [VIP]": "ESPN 2",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(generar_epg.limpiar_nombre(entrada), esperado)

    def test_no_rompe_nombres_legitimos(self) -> None:
        casos = {
            "Cine Latino": "Cine Latino",
            "Cine Latino SD": "Cine Latino",
            "Deportes 24/7 UHD": "Deportes 24/7",
            # "Premium" es parte del nombre real del canal, no una calidad.
            "ESPN Premium HD (1080p)": "ESPN Premium",
            # Un pais al final nunca deja el nombre en una sola palabra.
            "Mega Chile": "Mega Chile",
            "ESPN Chile": "ESPN Chile",
            # "Deportes" no es una region.
            "ESPN Deportes HD (720p)": "ESPN Deportes",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(generar_epg.limpiar_nombre(entrada), esperado)

    def test_elimina_pais_redundante_al_final(self) -> None:
        casos = {
            "TNT Sports Chile": "TNT Sports",
            "TNT Sports (Argentina)": "TNT Sports",
            "Fox Sports - México": "Fox Sports",
            "TNT Sports ArgentinaHD (1080p)": "TNT Sports",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(generar_epg.limpiar_nombre(entrada), esperado)

    def test_quitar_region_desactivable(self) -> None:
        self.assertEqual(
            generar_epg.limpiar_nombre("TNT Sports Chile", quitar_region=False),
            "TNT Sports Chile",
        )

    def test_id_unico_sin_espacios_ni_simbolos(self) -> None:
        for nombre in ("Canal 5", "Mega Noticias | HD", "Ñandú TV (1080p)", "A&E Mundo"):
            identificador = generar_epg.generar_id(nombre)
            with self.subTest(nombre=nombre):
                self.assertRegex(identificador, r"^[a-z0-9]+$")

    def test_variantes_de_espn_generan_el_mismo_id(self) -> None:
        variantes = (
            "ESPN 2 HD [LAT]",
            "ESPN 2 FHD",
            "ESPN2",
            "ESPN 2",
            "ESPN 2 SD (1080p)",
            "ESPN 2 Opción 2",
            "ESPN 2 Chile",
        )
        identificadores = {
            generar_epg.generar_id(generar_epg.limpiar_nombre(nombre)) for nombre in variantes
        }
        self.assertEqual(identificadores, {"espn2"})


class ParseoTests(unittest.TestCase):
    """Comprueba el analisis del archivo M3U."""

    def test_extrae_nombre_y_logo(self) -> None:
        canales = generar_epg.parsear_m3u(generar_epg.M3U_DE_EJEMPLO)
        self.assertEqual(len(canales), 8)
        self.assertEqual(canales[0]["nombre"], "Canal 5 HD")
        self.assertTrue(canales[0]["logo"].startswith("https://"))
        self.assertEqual(canales[0]["tvg_id"], "canal5.mx")
        self.assertEqual(canales[0]["tvg_name"], "Canal 5")

    def test_consolida_senales_duplicadas(self) -> None:
        canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(generar_epg.M3U_DE_EJEMPLO)
        )
        identificadores = [canal["id"] for canal in canales]
        self.assertEqual(len(canales), 6)
        self.assertEqual(len(set(identificadores)), 6)
        # El tvg-id del M3U tiene prioridad sobre el id generado.
        self.assertIn("canal5.mx", identificadores)
        self.assertIn("cinelatino", identificadores)
        # El canal sin logo en el M3U no debe llevar <icon>.
        canal_local = next(canal for canal in canales if canal["id"] == "canallocal")
        self.assertEqual(canal_local["logo"], "")
        self.assertEqual(canal_local["origen"], "nombre")


M3U_ESPN = "\n".join(
    [
        "#EXTM3U",
        '#EXTINF:-1 tvg-id="ESPN2.us@HD" tvg-name="ESPN 2" group-title="Deportes",ESPN 2 HD [LAT]',
        "http://ejemplo.invalid/espn2-hd/index.m3u8",
        '#EXTINF:-1 tvg-id="ESPN2.us@SD" tvg-name="ESPN 2" group-title="Deportes",ESPN2 (720p)',
        "http://ejemplo.invalid/espn2-sd/index.m3u8",
        '#EXTINF:-1 tvg-id="ESPN2.us" group-title="Deportes",ESPN 2 FHD',
        "http://ejemplo.invalid/espn2-fhd/index.m3u8",
        '#EXTINF:-1 tvg-id="ESPN2.nl@HD" group-title="Deportes",ESPN 2 (Netherlands) HD',
        "http://ejemplo.invalid/espn2-nl/index.m3u8",
        '#EXTINF:-1 tvg-id="0" group-title="Deportes",ESPN 3 SD',
        "http://ejemplo.invalid/espn3/index.m3u8",
        "#EXTINF:-1 group-title=\"Deportes\",ESPN 4 HD",
        "http://ejemplo.invalid/espn4/index.m3u8",
        "",
    ]
)


class TvgIdTests(unittest.TestCase):
    """Comprueba la prioridad del ``tvg-id`` y el mapeo de canales."""

    def _consolidar(self, texto: str = M3U_ESPN, **opciones: object) -> list[dict[str, str]]:
        return generar_epg.consolidar_canales(generar_epg.parsear_m3u(texto), **opciones)

    def test_tvg_id_es_el_id_prioritario_y_agrupa_calidades(self) -> None:
        canales = {canal["id"]: canal for canal in self._consolidar()}
        # @HD, @SD y sin sufijo son el mismo canal -> una sola entrada.
        espn2 = canales["ESPN2.us"]
        self.assertEqual(espn2["nombre"], "ESPN 2")
        self.assertEqual(espn2["origen"], "tvg-id")
        # Otro pais del mismo nombre sigue siendo un canal distinto.
        self.assertIn("ESPN2.nl", canales)
        self.assertEqual(canales["ESPN2.nl"]["nombre"], "ESPN 2 Netherlands")

    def test_tvg_id_invalido_cae_al_nombre(self) -> None:
        canales = {canal["id"]: canal for canal in self._consolidar()}
        self.assertEqual(canales["espn3"]["origen"], "nombre")
        self.assertEqual(canales["espn3"]["nombre"], "ESPN 3")

    def test_sin_tvg_id_genera_ids_desde_el_nombre(self) -> None:
        canales = {canal["id"]: canal for canal in self._consolidar(usar_tvg_id=False)}
        self.assertIn("espn2", canales)
        self.assertIn("espn3", canales)
        self.assertIn("espn4", canales)
        self.assertTrue(all(canal["origen"] == "nombre" for canal in canales.values()))

    def test_conservar_tvg_id_literal(self) -> None:
        canales = {canal["id"] for canal in self._consolidar(conservar_tvg_id=True)}
        self.assertIn("ESPN2.us@HD", canales)
        self.assertIn("ESPN2.us@SD", canales)
        self.assertIn("ESPN2.nl@HD", canales)

    def test_normaliza_tvg_id(self) -> None:
        normalizar = generar_epg._normalizar_tvg_id
        self.assertEqual(normalizar("ESPN2.us@HD"), "ESPN2.us")
        self.assertEqual(normalizar("ESPN2.us@HD", conservar=True), "ESPN2.us@HD")
        for invalido in ("", "  ", "0", "sin-epg", "null", "con espacios", "<x>"):
            with self.subTest(valor=invalido):
                self.assertEqual(normalizar(invalido), "")

    def test_paises_distintos_no_se_fusionan(self) -> None:
        texto = "\n".join(
            [
                "#EXTM3U",
                "#EXTINF:-1,TNT Sports Chile HD",
                "http://ejemplo.invalid/tnt-cl/index.m3u8",
                "#EXTINF:-1,TNT Sports Argentina HD",
                "http://ejemplo.invalid/tnt-ar/index.m3u8",
                "",
            ]
        )
        identificadores = [canal["id"] for canal in self._consolidar(texto)]
        self.assertEqual(identificadores, ["tntsportschile", "tntsportsargentina"])

    def test_pais_redundante_se_descarta(self) -> None:
        texto = "\n".join(
            [
                "#EXTM3U",
                "#EXTINF:-1,TNT Sports Chile HD",
                "http://ejemplo.invalid/tnt-cl/index.m3u8",
                "#EXTINF:-1,TNT Sports Chile SD",
                "http://ejemplo.invalid/tnt-cl-sd/index.m3u8",
                "",
            ]
        )
        canales = self._consolidar(texto)
        self.assertEqual(len(canales), 1)
        self.assertEqual(canales[0]["id"], "tntsports")
        self.assertEqual(canales[0]["nombre"], "TNT Sports")


class XmltvTests(unittest.TestCase):
    """Valida la estructura del archivo XMLTV generado."""

    def setUp(self) -> None:
        self.canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(generar_epg.M3U_DE_EJEMPLO)
        )
        self.inicio = generar_epg._inicio_del_dia()
        self.raiz, self.total = generar_epg.construir_xmltv(self.canales, self.inicio)
        self.directorio = tempfile.TemporaryDirectory()
        self.ruta = os.path.join(self.directorio.name, "epg.xml")
        generar_epg.escribir_xmltv(self.raiz, self.ruta)
        self.arbol = ET.parse(self.ruta).getroot()

    def tearDown(self) -> None:
        self.directorio.cleanup()

    def test_inicio_a_medianoche_utc(self) -> None:
        self.assertEqual(self.inicio.hour, 0)
        self.assertEqual(self.inicio.minute, 0)
        self.assertEqual(self.inicio.tzinfo, timezone.utc)

    def test_canales_con_display_name_e_icon(self) -> None:
        canales = self.arbol.findall("channel")
        self.assertEqual(len(canales), len(self.canales))
        for canal in canales:
            self.assertIsNotNone(canal.find("display-name"))
            if canal.get("id") != "canallocal":
                icono = canal.find("icon")
                self.assertIsNotNone(icono, f"falta <icon> en {canal.get('id')}")
                self.assertTrue(icono.get("src", "").startswith("http"))

    def test_bloques_de_4_horas_sin_huecos(self) -> None:
        self.assertEqual(self.total, len(self.canales) * 6)
        for canal in self.canales:
            programas = [
                programa
                for programa in self.arbol.findall("programme")
                if programa.get("channel") == canal["id"]
            ]
            self.assertEqual(len(programas), 6)
            anterior = None
            for programa in programas:
                inicio = datetime.strptime(programa.get("start"), "%Y%m%d%H%M%S %z")
                fin = datetime.strptime(programa.get("stop"), "%Y%m%d%H%M%S %z")
                self.assertEqual(fin - inicio, timedelta(hours=4))
                if anterior is not None:
                    self.assertEqual(inicio, anterior)
                anterior = fin
            self.assertEqual(anterior, self.inicio + timedelta(hours=24))

    def test_titulos_incluyen_el_nombre_del_canal(self) -> None:
        for programa in self.arbol.findall("programme"):
            titulo = programa.findtext("title") or ""
            canal = next(
                canal["nombre"] for canal in self.canales if canal["id"] == programa.get("channel")
            )
            self.assertIn(canal, titulo)

    def test_archivo_con_declaracion_y_utf8(self) -> None:
        with open(self.ruta, encoding="utf-8") as archivo:
            cabecera = archivo.readline().strip()
        self.assertEqual(cabecera, '<?xml version="1.0" encoding="UTF-8"?>')

    def test_display_names_incluyen_alias_para_el_match(self) -> None:
        canal = next(
            nodo for nodo in self.arbol.findall("channel") if nodo.get("id") == "canal5.mx"
        )
        nombres = [nodo.text for nodo in canal.findall("display-name")]
        self.assertEqual(nombres[0], "Canal 5")
        # El nombre original del M3U se publica como alias (match por nombre).
        self.assertIn("Canal 5 HD", nombres)

    def test_todo_programa_apunta_a_un_canal_existente(self) -> None:
        identificadores = {canal.get("id") for canal in self.arbol.findall("channel")}
        for programa in self.arbol.findall("programme"):
            self.assertIn(programa.get("channel"), identificadores)


def _xmltv_de_prueba(inicio: datetime) -> str:
    """Guia XMLTV de prueba (misma forma que las guias reales, sin red)."""
    canales = (
        ("canal5.mx", "Canal 5"),
        (
            "Canal.2.de.México.(Canal.Las.Estrellas.-.XEW).mx",
            "Canal 2 de México (Canal Las Estrellas - XEW)",
        ),
        ("ESPN.2.mx", "ESPN 2"),
        ("ESPN.mx", "ESPN"),
        ("La.7.es", "La 7"),
        ("Canal.7.de.México.mx", "Canal 7 de México"),
    )

    def programa(canal: str, titulo: str, desde: float, hasta: float) -> str:
        return (
            f'<programme start="{generar_epg._sello(inicio + timedelta(hours=desde))}" '
            f'stop="{generar_epg._sello(inicio + timedelta(hours=hasta))}" '
            f'channel="{canal}"><title lang="es">{titulo}</title>'
            '<desc lang="es">Sinopsis de prueba con acentos: México, España.</desc>'
            '<category lang="es">Series</category></programme>'
        )

    partes = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="prueba">']
    for identificador, nombre in canales:
        partes.append(
            f'<channel id="{identificador}"><display-name>{nombre}</display-name></channel>'
        )
    partes.extend(
        [
            # Programas dentro de la ventana (72 h desde el inicio).
            programa("canal5.mx", "Noticiero Matutino", 0, 2),
            programa("canal5.mx", "Cine de la Tarde", 2, 4),
            # Empezo antes de la ventana y sigue en emision.
            programa("canal5.mx", "Programa en curso", -1, 1),
            # Muy anterior y muy posterior: fuera de la ventana.
            programa("canal5.mx", "Programa viejo", -96, -92),
            programa("canal5.mx", "Programa futuro", 200, 202),
            # Sin <title>: se descarta.
            '<programme start="'
            + generar_epg._sello(inicio + timedelta(hours=5))
            + '" stop="'
            + generar_epg._sello(inicio + timedelta(hours=6))
            + '" channel="canal5.mx"><desc>sin titulo</desc></programme>',
            programa(
                "Canal.2.de.México.(Canal.Las.Estrellas.-.XEW).mx",
                "La Rosa de Guadalupe",
                0,
                1,
            ),
            programa("ESPN.2.mx", "Futbol de la Liga MX", 0, 3),
            programa("ESPN.mx", "SportsCenter", 0, 3),
            programa("La.7.es", "Informativos La 7", 0, 3),
            programa("Canal.7.de.México.mx", "Azteca 7 Noticias", 0, 3),
        ]
    )
    partes.append("</tv>")
    return "\n".join(partes)


M3U_DE_PRUEBA = "\n".join(
    [
        "#EXTM3U",
        '#EXTINF:-1 tvg-id="canal5.mx" tvg-name="Canal 5" group-title="Nacional",Canal 5 HD',
        "http://ejemplo.invalid/canal5/index.m3u8",
        '#EXTINF:-1 tvg-name="Las Estrellas" group-title="Nacional",Las Estrellas FHD [LAT]',
        "http://ejemplo.invalid/estrellas/index.m3u8",
        '#EXTINF:-1 tvg-name="ESPN 2" group-title="Deportes",ESPN 2 HD',
        "http://ejemplo.invalid/espn2/index.m3u8",
        '#EXTINF:-1 tvg-name="ESPN" group-title="Deportes",ESPN HD',
        "http://ejemplo.invalid/espn/index.m3u8",
        '#EXTINF:-1 tvg-name="Canal 7" group-title="Nacional",Canal 7 HD',
        "http://ejemplo.invalid/canal7/index.m3u8",
        '#EXTINF:-1 tvg-name="Canal Local" group-title="Local",Canal Local HD',
        "http://ejemplo.invalid/local/index.m3u8",
        "",
    ]
)


class ParseoDeGuiasRealesTests(unittest.TestCase):
    """Comprueba la lectura de las guias XMLTV reales."""

    def setUp(self) -> None:
        self.inicio = generar_epg._inicio_del_dia()
        self.texto = _xmltv_de_prueba(self.inicio)

    def test_extrae_canales_y_display_names(self) -> None:
        canales = generar_epg.parsear_epg_canales(self.texto)
        self.assertEqual(len(canales), 6)
        self.assertEqual(canales["canal5.mx"], ["Canal 5"])
        self.assertIn(
            "Canal 2 de México (Canal Las Estrellas - XEW)",
            canales["Canal.2.de.México.(Canal.Las.Estrellas.-.XEW).mx"],
        )

    def test_normaliza_las_fechas_a_utc(self) -> None:
        parsear = generar_epg.parsear_fecha_xmltv
        self.assertEqual(
            parsear("20261102003000 -0600"),
            datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parsear("20261102003000 +0000"),
            datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc),
        )
        # Formatos alternativos: sin segundos y sin desfase.
        self.assertEqual(
            parsear("202611020030"), datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc)
        )
        for invalido in ("", "   ", "no-es-fecha", "2026"):
            with self.subTest(valor=invalido):
                self.assertIsNone(parsear(invalido))

    def test_conserva_solo_los_programas_de_la_ventana(self) -> None:
        ventana = (self.inicio, self.inicio + timedelta(hours=72))
        programas = generar_epg.parsear_epg_programas(self.texto, {"canal5.mx"}, *ventana)
        titulos = [programa.findtext("title") for programa in programas["canal5.mx"]]
        self.assertIn("Noticiero Matutino", titulos)
        self.assertIn("Programa en curso", titulos)
        self.assertNotIn("Programa viejo", titulos)
        self.assertNotIn("Programa futuro", titulos)
        self.assertNotIn(None, titulos)
        self.assertEqual(len(programas["canal5.mx"]), 3)

    def test_programas_normalizados_en_utc_y_con_hijos(self) -> None:
        ventana = (self.inicio, self.inicio + timedelta(hours=72))
        programas = generar_epg.parsear_epg_programas(self.texto, {"canal5.mx"}, *ventana)
        programa = next(
            p for p in programas["canal5.mx"] if p.findtext("title") == "Noticiero Matutino"
        )
        self.assertEqual(programa.get("start"), generar_epg._sello(self.inicio))
        self.assertEqual(
            programa.get("stop"), generar_epg._sello(self.inicio + timedelta(hours=2))
        )
        self.assertEqual(
            programa.findtext("desc"), "Sinopsis de prueba con acentos: México, España."
        )
        self.assertEqual(programa.findtext("category"), "Series")

    def test_solo_lee_los_canales_pedidos(self) -> None:
        ventana = (self.inicio, self.inicio + timedelta(hours=72))
        programas = generar_epg.parsear_epg_programas(self.texto, {"ESPN.mx"}, *ventana)
        self.assertEqual(list(programas), ["ESPN.mx"])
        self.assertEqual(len(programas["ESPN.mx"]), 1)

    def test_descomprime_gzip(self) -> None:
        self.assertEqual(generar_epg._descomprimir(gzip.compress(b"<tv/>")), b"<tv/>")
        self.assertEqual(generar_epg._descomprimir(b"<tv/>"), b"<tv/>")


class EmparejamientoDeCanalesTests(unittest.TestCase):
    """Comprueba el cruce entre los canales del M3U y la guia real."""

    def setUp(self) -> None:
        self.inicio = generar_epg._inicio_del_dia()
        self.fuente = generar_epg.parsear_epg_canales(_xmltv_de_prueba(self.inicio))
        self.canales = generar_epg.consolidar_canales(generar_epg.parsear_m3u(M3U_DE_PRUEBA))

    def _mapa(self) -> dict[str, str]:
        return generar_epg.emparejar_canales(self.canales, self.fuente)

    def test_empareja_por_tvg_id_exacto(self) -> None:
        self.assertEqual(self._mapa()["canal5.mx"], "canal5.mx")

    def test_empareja_por_nombre_normalizado(self) -> None:
        mapa = self._mapa()
        # "Las Estrellas FHD [LAT]" -> display-name con prefijo y alias.
        self.assertEqual(
            mapa["lasestrellas"],
            "Canal.2.de.México.(Canal.Las.Estrellas.-.XEW).mx",
        )
        self.assertEqual(mapa["espn2"], "ESPN.2.mx")
        self.assertEqual(mapa["espn"], "ESPN.mx")

    def test_numero_suelto_no_se_confunde_con_otra_cadena(self) -> None:
        # "Canal 7" debe emparejar con "Canal 7 de Mexico", nunca con "La 7".
        self.assertEqual(self._mapa()["canal7"], "Canal.7.de.México.mx")

    def test_canal_sin_coincidencia_no_se_inventa(self) -> None:
        self.assertNotIn("canallocal", self._mapa())

    def test_cada_canal_de_la_guia_se_usa_una_sola_vez(self) -> None:
        valores = list(self._mapa().values())
        self.assertEqual(len(valores), len(set(valores)))

    def test_canal_plus_es_una_marca_y_no_roba_canales(self) -> None:
        # La guia francesa trae "CANAL+LIVE 12": si el "+" se trata como
        # separador y "live" como relleno, queda ("canal", "12") y le roba los
        # programas a "Canal 12".
        canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-name="Canal 12",Canal 12 HD',
                        "http://ejemplo.invalid/canal12/index.m3u8",
                        '#EXTINF:-1 tvg-name="CANAL 8 (TELEVISION CHICHICASTECA)",'
                        "CANAL 8 TELEVISION CHICHICASTECA",
                        "http://ejemplo.invalid/canal8/index.m3u8",
                        "",
                    ]
                )
            )
        )
        fuente = {
            "CANAL+LIVE.12.fr": ["CANAL+LIVE 12"],
            "CANAL+LIVE.8.fr": ["CANAL+LIVE 8"],
        }
        self.assertEqual(generar_epg.emparejar_canales(canales, fuente), {})
        # Con una guia que si es el canal, el emparejamiento sigue funcionando.
        fuente["Canal.12.uy"] = ["Canal 12"]
        self.assertEqual(
            generar_epg.emparejar_canales(canales, fuente), {"canal12": "Canal.12.uy"}
        )

    def test_los_canal_nn_genericos_de_francia_no_roban_canales(self) -> None:
        # La guia francesa publica "Canal 32" como bucle de Canal+: no puede
        # quedarse con la programacion de un canal 32 latinoamericano.
        canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-name="CANAL 32 (TELEVISION CHICHICASTECA)",'
                        "CANAL 32 TELEVISION CHICHICASTECA",
                        "http://ejemplo.invalid/canal32/index.m3u8",
                        '#EXTINF:-1 tvg-name="Canal 9",Canal 9 HD',
                        "http://ejemplo.invalid/canal9/index.m3u8",
                        "",
                    ]
                )
            )
        )
        fuente = {
            "Canal.32.fr": ["Canal 32"],
            "Canal.9.pa": ["Canal 9"],
        }
        # El "Canal 9" de Panama si es un canal real: se empareja.
        self.assertEqual(
            generar_epg.emparejar_canales(canales, fuente), {"canal9": "Canal.9.pa"}
        )

    def test_codigos_de_pais_y_barras_no_confunden(self) -> None:
        canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-name="ESPN MX",ESPN MX HD',
                        "http://ejemplo.invalid/espn-mx/index.m3u8",
                        '#EXTINF:-1 tvg-name="Deportes 24/7",Deportes 24/7 HD',
                        "http://ejemplo.invalid/deportes/index.m3u8",
                        "",
                    ]
                )
            )
        )
        mapa = generar_epg.emparejar_canales(canales, self.fuente)
        # El codigo de pais del nombre ("ESPN MX") no impide el cruce.
        self.assertEqual(mapa["espnmx"], "ESPN.mx")
        # "Deportes 24/7" no acaba en "La 7" por dividir el nombre en la "/".
        self.assertNotIn("deportes247", mapa)


class IntegracionEpgRealTests(unittest.TestCase):
    """Comprueba el flujo completo M3U + guia real + XMLTV."""

    def setUp(self) -> None:
        self.inicio = generar_epg._inicio_del_dia()
        self.ventana = (self.inicio, self.inicio + timedelta(hours=72))
        self.texto_epg = _xmltv_de_prueba(self.inicio)
        self.canales = generar_epg.consolidar_canales(generar_epg.parsear_m3u(M3U_DE_PRUEBA))
        self.programas, self.emparejados, self.informes = generar_epg.integrar_epg(
            self.canales,
            ["guia-de-prueba"],
            self.ventana,
            cargador=lambda _fuente: self.texto_epg,
        )

    def test_recupera_la_programacion_de_cada_canal_emparejado(self) -> None:
        self.assertEqual(len(self.programas), 5)
        for canal in ("canal5.mx", "lasestrellas", "espn2", "espn", "canal7"):
            self.assertIn(canal, self.programas)
        self.assertEqual(self.informes[0]["emparejados"], 5)
        self.assertEqual(self.informes[0]["error"], "")
        titulos = {programa.findtext("title") for programa in self.programas["canal5.mx"]}
        self.assertIn("Noticiero Matutino", titulos)

    def test_publica_los_programas_con_el_id_del_canal(self) -> None:
        raiz, total = generar_epg.construir_xmltv(
            self.canales, self.inicio, programas=self.programas, relleno=False
        )
        programas = raiz.findall("programme")
        self.assertEqual(total, len(programas))
        identificadores = {canal.get("id") for canal in raiz.findall("channel")}
        for programa in programas:
            self.assertIn(programa.get("channel"), identificadores)
            self.assertIsNotNone(programa.find("title"))
            self.assertRegex(programa.get("start"), r"^\d{14} \+0000$")
        titulos = {programa.findtext("title") for programa in programas}
        self.assertIn("Futbol de la Liga MX", titulos)
        self.assertNotIn("Programa viejo", titulos)

    def test_canal_sin_epg_se_publica_sin_programas(self) -> None:
        raiz, _total = generar_epg.construir_xmltv(
            self.canales, self.inicio, programas=self.programas, relleno=False
        )
        con_programas = {programa.get("channel") for programa in raiz.findall("programme")}
        self.assertIn("canallocal", {canal.get("id") for canal in raiz.findall("channel")})
        self.assertNotIn("canallocal", con_programas)

    def test_relleno_opcional_solo_para_los_canales_sin_epg(self) -> None:
        raiz, _total = generar_epg.construir_xmltv(
            self.canales, self.inicio, programas=self.programas, relleno=True
        )
        programas = raiz.findall("programme")
        del_local = [p for p in programas if p.get("channel") == "canallocal"]
        self.assertEqual(len(del_local), len(generar_epg.BLOQUES_24H))
        del_canal5 = [p for p in programas if p.get("channel") == "canal5.mx"]
        self.assertEqual(len(del_canal5), 3)
        self.assertTrue(
            all("Contenido generico" not in (p.findtext("desc") or "") for p in del_canal5)
        )

    def test_una_fuente_rota_no_interrumpe_el_proceso(self) -> None:
        def cargador(_fuente: str) -> str:
            raise RuntimeError("sin conexion")

        programas, emparejados, informes = generar_epg.integrar_epg(
            self.canales, ["fuente-rota"], self.ventana, cargador=cargador
        )
        self.assertEqual(programas, {})
        self.assertEqual(emparejados, {})
        self.assertIn("sin conexion", informes[0]["error"])

    def test_el_xmltv_escrito_es_valido_y_utf8(self) -> None:
        raiz, _total = generar_epg.construir_xmltv(
            self.canales, self.inicio, programas=self.programas, relleno=False
        )
        with tempfile.TemporaryDirectory() as directorio:
            ruta = os.path.join(directorio, "epg.xml")
            generar_epg.escribir_xmltv(raiz, ruta)
            arbol = ET.parse(ruta).getroot()
            with open(ruta, encoding="utf-8") as archivo:
                self.assertEqual(
                    archivo.readline().strip(), '<?xml version="1.0" encoding="UTF-8"?>'
                )
            with open(ruta, encoding="utf-8") as archivo:
                self.assertIn("México", archivo.read())
        self.assertEqual(arbol.tag, "tv")
        identificadores = {canal.get("id") for canal in arbol.findall("channel")}
        for programa in arbol.findall("programme"):
            self.assertIn(programa.get("channel"), identificadores)
        self.assertGreater(len(arbol.findall("programme")), 0)


class GuiaDeEjemploTests(unittest.TestCase):
    """La guia de ``--demo`` debe alimentar el flujo real sin red."""

    def test_la_guia_de_ejemplo_empareja_los_canales_de_ejemplo(self) -> None:
        inicio = generar_epg._inicio_del_dia()
        ventana = (inicio, inicio + timedelta(hours=72))
        canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(generar_epg.M3U_DE_EJEMPLO)
        )
        programas, emparejados, informes = generar_epg.integrar_epg(
            canales,
            ["guia de ejemplo"],
            ventana,
            cargador=lambda _fuente: generar_epg.epg_de_ejemplo(inicio),
        )
        self.assertEqual(informes[0]["error"], "")
        self.assertEqual(len(programas), 4)
        self.assertEqual(programas["canal5.mx"][0].findtext("title"), "Noticiero Matutino")
        self.assertEqual(emparejados["lasestrellas.mx"], "Canal.Las.Estrellas.mx")
        # Los canales sin guia se quedan sin programacion (nada inventado).
        self.assertNotIn("deportes247", programas)
        self.assertNotIn("canallocal", programas)

    def test_urls_por_defecto_sustituyen_el_pais(self) -> None:
        urls = generar_epg.urls_epg_por_defecto(["mx", "ES"])
        self.assertEqual(len(urls), 2)
        self.assertIn("MX", urls[0])
        self.assertIn("ES", urls[1])
        self.assertNotIn("{pais}", urls[0])


class FuentesEpgPorDefectoTests(unittest.TestCase):
    """Cobertura amplia: guias por pais, guia de EE. UU. y guias FAST extra."""

    def test_los_paises_por_defecto_cubren_america_y_europa(self) -> None:
        paises = generar_epg.PAISES_EPG_POR_DEFECTO
        for codigo in ("MX", "ES", "AR", "CO", "CL", "PE", "PT", "IT", "DE", "US"):
            with self.subTest(codigo=codigo):
                self.assertIn(codigo, paises)
        # La guia mas generica (EE. UU.) va al final para no robar canales.
        self.assertEqual(paises[-1], "US")

    def test_el_pais_us_usa_la_guia_de_epg_pw(self) -> None:
        # epgshare01 no publica guia de EE. UU. (devuelve 404).
        urls = generar_epg.urls_epg_por_defecto(["us"])
        self.assertEqual(urls, ["https://epg.pw/xmltv/epg_US.xml"])

    def test_mezcla_de_paises_mantiene_el_orden(self) -> None:
        urls = generar_epg.urls_epg_por_defecto(["MX", "US"])
        self.assertIn("epg_ripper_MX1.xml.gz", urls[0])
        self.assertEqual(urls[1], "https://epg.pw/xmltv/epg_US.xml")

    def test_las_guias_extra_se_suman_a_los_paises(self) -> None:
        argumentos = generar_epg._crear_parser().parse_args(["--epg-pais", "MX"])
        fuentes = generar_epg._fuentes_epg_desde_argumentos(argumentos)
        self.assertIn("epg_ripper_MX1.xml.gz", fuentes[0])
        self.assertEqual(len(fuentes), 1 + len(generar_epg.FUENTES_EPG_EXTRA_POR_DEFECTO))
        for extra in generar_epg.FUENTES_EPG_EXTRA_POR_DEFECTO:
            self.assertIn(extra, fuentes)

    def test_sin_extras_deja_solo_las_guias_de_pais(self) -> None:
        argumentos = generar_epg._crear_parser().parse_args(
            ["--epg-pais", "MX,US", "--sin-extras"]
        )
        fuentes = generar_epg._fuentes_epg_desde_argumentos(argumentos)
        self.assertEqual(
            fuentes,
            [
                "https://epgshare01.online/epgshare01/epg_ripper_MX1.xml.gz",
                "https://epg.pw/xmltv/epg_US.xml",
            ],
        )

    def test_una_fuente_explicita_no_lleva_extras(self) -> None:
        argumentos = generar_epg._crear_parser().parse_args(
            ["--epg", "https://ejemplo.invalid/guia1.xml,https://ejemplo.invalid/guia2.xml"]
        )
        fuentes = generar_epg._fuentes_epg_desde_argumentos(argumentos)
        self.assertEqual(
            fuentes,
            ["https://ejemplo.invalid/guia1.xml", "https://ejemplo.invalid/guia2.xml"],
        )

    def test_un_404_no_se_reintenta(self) -> None:
        llamadas = []

        def urlopen_falso(peticion, timeout=None):
            llamadas.append(peticion.full_url)
            raise generar_epg.urllib.error.HTTPError(
                peticion.full_url, 404, "Not Found", {}, None
            )

        with mock.patch("urllib.request.urlopen", side_effect=urlopen_falso):
            with self.assertRaises(RuntimeError):
                generar_epg.descargar_epg("https://ejemplo.invalid/epg_XX1.xml.gz", reintentos=3)
        self.assertEqual(len(llamadas), 1)


M3U_CON_MAPEO = "\n".join(
    [
        "#EXTM3U",
        '#EXTINF:-1 tvg-id="espn2.mx" tvg-name="ESPN 2" '
        'tvg-logo="https://logos.invalid/espn2.png" group-title="Deportes",ESPN 2 HD',
        "#EXTVLCOPT:http-user-agent=OTT Navigator/1.0",
        "http://ejemplo.invalid/espn2-hd/index.m3u8",
        '#EXTINF:-1 group-title="Deportes",ESPN 2 (Opc.2)',
        "http://ejemplo.invalid/espn2-opc2/index.m3u8",
        '#EXTINF:-1 group-title="Deportes",ESPN2 HD - (Opc.3)',
        "http://ejemplo.invalid/espn2-opc3/index.m3u8",
        '#EXTINF:-1 group-title="Deportes",ESPN 2 (Netherlands) HD',
        "http://ejemplo.invalid/espn2-nl/index.m3u8",
        '#EXTINF:-1 group-title="Cine",Cine Latino Backup',
        "http://ejemplo.invalid/cinelatino/index.m3u8",
        '#EXTINF:-1 group-title="Cine",Cine Latino',
        "http://ejemplo.invalid/cinelatino-2/index.m3u8",
        "#EXTINF:-1,Canal Local SD",
        "http://ejemplo.invalid/canallocal/index.m3u8",
        "",
    ]
)


class MapeoExplicitoTests(unittest.TestCase):
    """Comprueba el diccionario de alias -> tvg-id real de la guia."""

    def setUp(self) -> None:
        # Con ruta vacia solo se usan los canales definidos en el codigo, para
        # que la prueba no dependa del mapeo_canales.json del repositorio.
        self.mapeo = generar_epg.cargar_mapeo_canales("")
        self.canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(M3U_CON_MAPEO), mapeo=self.mapeo
        )

    def test_el_mapeo_por_defecto_carga_los_canales_dificiles(self) -> None:
        self.assertIn("ESPN2.us", self.mapeo)
        indice = generar_epg._indice_mapeo(self.mapeo)
        for alias in ("ESPN 2 HD", "ESPN 2 (Opc.2)", "ESPN2 HD - (Opc.3)"):
            with self.subTest(alias=alias):
                entrada = generar_epg.buscar_en_mapeo(indice, alias)
                self.assertIsNotNone(entrada)
                self.assertEqual(entrada["id"], "ESPN2.us")

    def test_todas_las_opciones_apuntan_al_mismo_tvg_id(self) -> None:
        por_id = {canal["id"]: canal for canal in self.canales}
        espn2 = por_id["ESPN2.us"]
        self.assertEqual(espn2["nombre"], "ESPN 2")
        self.assertEqual(espn2["tvg_name"], "ESPN 2")
        self.assertEqual(espn2["tvg_id"], "ESPN2.us")
        self.assertEqual(len(espn2["senales"]), 3)
        self.assertEqual(
            [senal["url"] for senal in espn2["senales"]],
            [
                "http://ejemplo.invalid/espn2-hd/index.m3u8",
                "http://ejemplo.invalid/espn2-opc2/index.m3u8",
                "http://ejemplo.invalid/espn2-opc3/index.m3u8",
            ],
        )

    def test_el_mapeo_no_roba_canales_de_otro_pais(self) -> None:
        identificadores = {canal["id"] for canal in self.canales}
        self.assertIn("espn2netherlands", identificadores)
        self.assertNotIn("ESPN2.nl", identificadores)

    def test_sin_mapeo_se_usa_el_tvg_id_del_m3u(self) -> None:
        sin_mapeo = generar_epg.consolidar_canales(generar_epg.parsear_m3u(M3U_CON_MAPEO))
        identificadores = {canal["id"] for canal in sin_mapeo}
        # Sin el diccionario manda el tvg-id de la lista (espn2.mx), no el canonico.
        self.assertIn("espn2.mx", identificadores)
        self.assertNotIn("ESPN2.us", identificadores)

    def test_el_json_se_fusiona_con_los_canales_del_codigo(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "mapeo_canales.json")
            with open(ruta, "w", encoding="utf-8") as archivo:
                archivo.write(
                    '{"canales": {'
                    '"ESPN2.us": ["ESPN 2 Extreme"],'
                    '"TVN.cl": {"nombre": "TVN", "alias": ["TVN HD"], "logo": "https://x.invalid/tvn.png"}'
                    "}}"
                )
            mapeo = generar_epg.cargar_mapeo_canales(ruta)
            indice = generar_epg._indice_mapeo(mapeo)
            # El alias nuevo y los del codigo conviven en el mismo tvg-id.
            self.assertEqual(generar_epg.buscar_en_mapeo(indice, "ESPN 2 (Opc.2)")["id"], "ESPN2.us")
            self.assertEqual(generar_epg.buscar_en_mapeo(indice, "ESPN 2 Extreme")["id"], "ESPN2.us")
            self.assertEqual(mapeo["TVN.cl"]["nombre"], "TVN")
            self.assertEqual(mapeo["TVN.cl"]["logo"], "https://x.invalid/tvn.png")

    def test_un_json_invalido_se_informa(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "mapeo_canales.json")
            with open(ruta, "w", encoding="utf-8") as archivo:
                archivo.write("{esto no es json")
            with self.assertRaises(ValueError):
                generar_epg.cargar_mapeo_canales(ruta)

    def test_el_mapeo_del_repositorio_es_valido(self) -> None:
        ruta = Path(__file__).resolve().parent.parent / generar_epg.ARCHIVO_MAPEO_POR_DEFECTO
        self.assertTrue(ruta.is_file(), f"falta {ruta}")
        mapeo = generar_epg.cargar_mapeo_canales(str(ruta))
        indice = generar_epg._indice_mapeo(mapeo)
        for alias in ("ESPN 2 HD", "ESPN 2 (Opc.2)", "ESPN2 HD - (Opc.3)"):
            with self.subTest(alias=alias):
                entrada = generar_epg.buscar_en_mapeo(indice, alias)
                self.assertEqual(entrada["id"], "ESPN2.us")
        self.assertNotIn("$esquema", mapeo)
        self.assertNotIn("_nota", mapeo)


class ListaM3UTests(unittest.TestCase):
    """Comprueba la lista M3U propia generada y sincronizada con la guia."""

    def setUp(self) -> None:
        self.mapeo = generar_epg.cargar_mapeo_canales("")
        self.canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(M3U_CON_MAPEO), mapeo=self.mapeo
        )
        self.url_epg = "https://ejemplo.invalid/guiadecanales/epg.xml"
        self.texto = generar_epg.generar_m3u(self.canales, url_epg=self.url_epg)
        self.entradas = generar_epg.parsear_m3u(self.texto)

    def _lineas_extinf(self) -> list[str]:
        return [linea for linea in self.texto.splitlines() if linea.startswith("#EXTINF")]

    def test_la_cabecera_declara_la_guia(self) -> None:
        cabecera = self.texto.splitlines()[0]
        self.assertTrue(cabecera.startswith("#EXTM3U"))
        self.assertIn(f'x-tvg-url="{self.url_epg}"', cabecera)
        self.assertIn(f'url-tvg="{self.url_epg}"', cabecera)

    def test_publica_una_entrada_por_senal_de_respaldo(self) -> None:
        espn2 = [entrada for entrada in self.entradas if entrada["tvg_id"] == "ESPN2.us"]
        self.assertEqual(len(espn2), 3)
        # Mismo tvg-id y tvg-name en todas: la EPG es la misma en las 3 opciones.
        self.assertEqual({entrada["tvg_name"] for entrada in espn2}, {"ESPN 2"})
        self.assertEqual(
            [entrada["url"] for entrada in espn2],
            [
                "http://ejemplo.invalid/espn2-hd/index.m3u8",
                "http://ejemplo.invalid/espn2-opc2/index.m3u8",
                "http://ejemplo.invalid/espn2-opc3/index.m3u8",
            ],
        )
        # El nombre visible distingue las opciones sin tocar el tvg-name.
        nombres = [
            generar_epg._extraer_nombre(linea)
            for linea in self._lineas_extinf()
            if 'tvg-id="ESPN2.us"' in linea
        ]
        self.assertEqual(nombres, ["ESPN 2", "ESPN 2 (Opc. 2)", "ESPN 2 (Opc. 3)"])

    def test_vincula_cada_canal_con_el_xmltv(self) -> None:
        raiz, _total = generar_epg.construir_xmltv(self.canales, generar_epg._inicio_del_dia())
        identificadores = {canal.get("id") for canal in raiz.findall("channel")}
        self.assertTrue(self.entradas)
        for entrada in self.entradas:
            with self.subTest(canal=entrada["tvg_id"]):
                self.assertIn(entrada["tvg_id"], identificadores)

    def test_atributos_limpios(self) -> None:
        self.assertNotIn('tvg-logo=""', self.texto)
        for entrada in self.entradas:
            with self.subTest(canal=entrada["nombre"]):
                self.assertTrue(entrada["tvg_id"])
                self.assertNotIn('"', entrada["tvg_name"])
                self.assertNotIn('"', entrada["nombre"])
        logos = [entrada["logo"] for entrada in self.entradas if entrada["logo"]]
        self.assertTrue(logos and all(logo.startswith("https://") for logo in logos))

    def test_orden_por_grupo_y_nombre_y_sin_grupo_al_final(self) -> None:
        grupos = [entrada["grupo"] for entrada in self.entradas]
        self.assertEqual(grupos, sorted(grupos, key=lambda grupo: (grupo or "\uffff").casefold()))
        # El canal sin grupo queda al final de la lista.
        self.assertEqual(self.entradas[-1]["nombre"], "Canal Local")
        cine = [entrada["nombre"] for entrada in self.entradas if entrada["grupo"] == "Cine"]
        self.assertEqual(cine, ["Cine Latino", "Cine Latino (Opc. 2)"])


    def test_escribir_m3u_en_disco(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            destino = os.path.join(carpeta, "lista.m3u")
            publicados, entradas, sin_url = generar_epg.escribir_m3u(
                self.canales, destino, url_epg=self.url_epg
            )
            self.assertEqual(publicados, len(self.canales))
            self.assertEqual(entradas, len(self.entradas))
            self.assertEqual(sin_url, [])
            with open(destino, "r", encoding="utf-8") as archivo:
                self.assertEqual(archivo.read(), self.texto)

    def test_omite_los_canales_sin_url(self) -> None:
        canales = [
            {"id": "sinurl", "nombre": "Sin URL", "senales": []},
            {
                "id": "conurl",
                "nombre": "Con URL",
                "senales": [{"url": "http://ejemplo.invalid/x/index.m3u8", "opciones": ()}],
            },
        ]
        texto = generar_epg.generar_m3u(canales, url_epg=self.url_epg)
        self.assertEqual(texto.count("#EXTINF"), 1)
        self.assertIn('tvg-id="conurl"', texto)
        self.assertNotIn("sinurl", texto)
        with tempfile.TemporaryDirectory() as carpeta:
            destino = os.path.join(carpeta, "lista.m3u")
            publicados, entradas, sin_url = generar_epg.escribir_m3u(canales, destino)
            self.assertEqual((publicados, entradas, sin_url), (1, 1, ["sinurl"]))

    def test_main_genera_epg_y_lista_sincronizados(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            epg = os.path.join(carpeta, "epg.xml")
            lista = os.path.join(carpeta, "lista.m3u")
            codigo = generar_epg.main(
                [
                    "--demo",
                    "--sin-epg",
                    "--sin-mapeo",
                    "--silencioso",
                    "--salida", epg,
                    "--m3u-salida", lista,
                    "--epg-publica", self.url_epg,
                ]
            )
            self.assertEqual(codigo, 0)
            self.assertTrue(os.path.isfile(epg))
            self.assertTrue(os.path.isfile(lista))

            raiz = ET.parse(epg).getroot()
            identificadores = {canal.get("id") for canal in raiz.findall("channel")}
            with open(lista, "r", encoding="utf-8") as archivo:
                texto = archivo.read()
            self.assertIn(f'x-tvg-url="{self.url_epg}"', texto.splitlines()[0])
            entradas = generar_epg.parsear_m3u(texto)
            # Todas las senales de la lista apuntan a un canal de la guia.
            self.assertEqual(len(entradas), 8)
            for entrada in entradas:
                with self.subTest(canal=entrada["tvg_id"]):
                    self.assertIn(entrada["tvg_id"], identificadores)

    def test_main_puede_omitir_la_lista(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            epg = os.path.join(carpeta, "epg.xml")
            lista = os.path.join(carpeta, "lista.m3u")
            codigo = generar_epg.main(
                [
                    "--demo",
                    "--sin-epg",
                    "--sin-m3u",
                    "--silencioso",
                    "--salida", epg,
                    "--m3u-salida", lista,
                ]
            )
            self.assertEqual(codigo, 0)
            self.assertTrue(os.path.isfile(epg))
            self.assertFalse(os.path.exists(lista))


M3U_CALIFICADORES = "\n".join(
    [
        "#EXTM3U",
        '#EXTINF:-1 group-title="México",Canal 5 Nacional HD',
        "http://ejemplo.invalid/c5-nacional/index.m3u8",
        '#EXTINF:-1 group-title="México",Las Estrellas Nacional V2',
        "http://ejemplo.invalid/le-v2/index.m3u8",
        '#EXTINF:-1 group-title="México",Azteca 7 Regional (Opc.2)',
        "http://ejemplo.invalid/a7-regional/index.m3u8",
        '#EXTINF:-1 group-title="México",Azteca Uno Nacional v3',
        "http://ejemplo.invalid/a1-v3/index.m3u8",
        '#EXTINF:-1 group-title="México",Canal 5 Del Pueblo Alcorta',
        "http://ejemplo.invalid/c5-pueblo/index.m3u8",
        "",
    ]
)


class MapeoConCalificadoresTests(unittest.TestCase):
    """Comprueba que los calificadores de señal (Nacional, V2...) no rompen el mapeo."""

    def setUp(self) -> None:
        self.canales = {
            canal["id"]: canal
            for canal in generar_epg.consolidar_canales(
                generar_epg.parsear_m3u(M3U_CALIFICADORES),
                mapeo=generar_epg.cargar_mapeo_canales(""),
            )
        }

    def test_las_variantes_nacional_y_vn_mapean_al_canal_principal(self) -> None:
        esperados = {
            "Canal5.mx": "Canal 5",
            "LasEstrellas.mx": "Las Estrellas",
            "Azteca7.mx": "Azteca 7",
            "AztecaUno.mx": "Azteca Uno",
        }
        for identificador, nombre in esperados.items():
            with self.subTest(canal=identificador):
                self.assertIn(identificador, self.canales)
                self.assertEqual(self.canales[identificador]["nombre"], nombre)
                self.assertTrue(self.canales[identificador]["mapeado"])

    def test_un_canal_parecido_no_se_roba(self) -> None:
        # "Canal 5 Del Pueblo Alcorta" es otro canal: conserva su id propio.
        self.assertIn("canal5delpuebloalcorta", self.canales)
        self.assertFalse(self.canales["canal5delpuebloalcorta"]["mapeado"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
