#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas basicas de ``generar_epg.py`` (solo libreria estandar).

Se ejecutan con::

    python -m unittest discover -s tests -v
    # o tambien
    python tests/test_generar_epg.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
