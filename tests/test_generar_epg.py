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


class LimpiezaDeNombresTests(unittest.TestCase):
    """Comprueba la unificacion de nombres duplicados."""

    def test_elimina_calidad_y_opciones(self) -> None:
        casos = {
            "Canal 5 HD": "Canal 5",
            "Canal 5 FHD [Opc.2]": "Canal 5",
            "Las Estrellas [Opc.2]": "Las Estrellas",
            "Mega Noticias (1080p)": "Mega Noticias",
            "Cine Latino SD": "Cine Latino",
            "TNT Series FHD (Opcion 3)": "TNT Series",
            "HBO SignatureHD": "HBO Signature",
            "ESPN 2 HD [LAT]": "ESPN 2",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(generar_epg.limpiar_nombre(entrada), esperado)

    def test_no_rompe_nombres_legitimos(self) -> None:
        self.assertEqual(generar_epg.limpiar_nombre("Cine Latino"), "Cine Latino")
        self.assertEqual(generar_epg.limpiar_nombre("Deportes 24/7 UHD"), "Deportes 24/7")

    def test_id_unico_sin_espacios_ni_simbolos(self) -> None:
        for nombre in ("Canal 5", "Mega Noticias | HD", "Ñandú TV (1080p)", "A&E Mundo"):
            identificador = generar_epg.generar_id(nombre)
            with self.subTest(nombre=nombre):
                self.assertRegex(identificador, r"^[a-z0-9]+$")


class ParseoTests(unittest.TestCase):
    """Comprueba el analisis del archivo M3U."""

    def test_extrae_nombre_y_logo(self) -> None:
        canales = generar_epg.parsear_m3u(generar_epg.M3U_DE_EJEMPLO)
        self.assertEqual(len(canales), 8)
        self.assertEqual(canales[0]["nombre"], "Canal 5 HD")
        self.assertTrue(canales[0]["logo"].startswith("https://"))
        self.assertEqual(canales[0]["tvg_id"], "canal5.mx")

    def test_consolida_senales_duplicadas(self) -> None:
        canales = generar_epg.consolidar_canales(
            generar_epg.parsear_m3u(generar_epg.M3U_DE_EJEMPLO)
        )
        identificadores = [canal["id"] for canal in canales]
        self.assertEqual(len(canales), 6)
        self.assertEqual(len(set(identificadores)), 6)
        self.assertIn("canal5", identificadores)
        self.assertIn("cinelatino", identificadores)
        # El canal sin logo en el M3U no debe llevar <icon>.
        canal_local = next(canal for canal in canales if canal["id"] == "canallocal")
        self.assertEqual(canal_local["logo"], "")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
