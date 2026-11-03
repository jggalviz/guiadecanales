# Guía de canales → EPG XMLTV

Script en **Python (solo librerías estándar)** que descarga una lista **M3U**
remota, cruza sus canales con **guías XMLTV reales públicas** y genera
**`epg.xml` en formato XMLTV** con la **programación real** de cada canal. Los
canales que no aparecen en ninguna guía se publican **sin programas** (nada de
contenido inventado); la parrilla genérica de 24 h queda solo como respaldo
opcional o para cuando ninguna guía se puede descargar.

Además genera **`lista.m3u`**: una lista propia, ordenada y con los atributos
limpios, en la que **cada canal lleva el `tvg-id` real de la guía** (incluidas
todas sus señales de respaldo: `[Opc.2]`, `Backup`, `Espejo`…) y la cabecera
declara la EPG publicada, de modo que OTT Navigator, TiviMate, VLC o IPTV
Smarters muestran la programación sin configurar nada.

```
lista.m3u (proveedor) ─┐
                       ├──►  generar_epg.py  ──►  epg.xml  (XMLTV con programación real)
guía EPG (XMLTV) ──────┘                   └──►  lista.m3u (propia, tvg-id sincronizado)
```

## Características

- ✅ **Descarga guías XMLTV reales** (por defecto una por país de
  [epgshare01](https://epgshare01.online/): `MX`, `ES`, `AR`, `CO`, `CL`, `PE`,
  `EC`, `UY`, `CR`, `PA`, `DO`, `SV`, `PT`, `IT`, `DE`; y la de **EE. UU.**
  de [epg.pw](https://epg.pw/), que no existe en epgshare01), admite **gzip**,
  BOM y desfases horarios, y normaliza todas las fechas a **UTC (`+0000`)**.
- ✅ **Guías extra automáticas** para los canales FAST que traen muchas listas
  (Pluto TV, Samsung TV Plus y Roku, vía `i.mjh.nz`) **y para DSports/DIRECTV
  Sports** (`epg_ripper_DIRECTVSPORTS1` de epgshare01, la única fuente que
  publica la parrilla de `DSports`, `DSports 2`, `DSports +` y `DSports Motor`),
  configurables con `EPG_URLS_EXTRA` y desactivables con `--sin-extras`.
- ✅ **Emparejamiento inteligente** canal ↔ programa: `tvg-id` exacto, `tvg-id`
  normalizado y, si no hay, **nombre normalizado** (sin `HD/FHD/SD`, sin
  `[Opc.2]`, sin paréntesis tipo `(XHGC)` y sin país), con puntuación y
  desempate por país. Un canal de la guía no se reutiliza dos veces.
- ✅ **Fallback limpio**: los canales sin coincidencia se publican **sin
  `<programme>`** (no se inventa programación). `--relleno` les añade la
  parrilla genérica y, si **ninguna** guía se puede usar, la guía completa se
  rellena para que el reproductor nunca quede sin datos.
- ✅ Ventana temporal configurable (`--horas-ventana`, 72 h por defecto) y solo
  se conservan los programas de los canales emparejados: el archivo no crece de
  más aunque la guía original tenga una semana completa.
- ✅ Compatible con **cualquier fuente XMLTV** (open-epg, `i.mjh.nz`, la guía de
  tu proveedor…) mediante `--epg URL`, `--epg-archivo ruta.xml.gz` o las
  variables `EPG_URL` / `EPG_PAISES`.
- ✅ Descarga la lista M3U desde una URL (`urllib.request`) con User-Agent de
  navegador, reintentos, detección automática de codificación y **saneado de
  mojibake** (`MÚxico` → `México`, `Espa±a` → `España`, `MÃ©xico` → `México`).
- ✅ Extrae de cada `#EXTINF` el **nombre del canal** (texto posterior a la
  última coma), los atributos **`tvg-logo`**, **`tvg-id`** y **`tvg-name`**.
- ✅ **Prioriza el `tvg-id` de la lista** como `id` del `<channel>` (es lo que
  usan TiviMate, OTT Navigator, IPTV Smarters o Kodi para emparejar), y agrupa
  las variantes de calidad de un mismo id (`ESPN2.us@HD` / `ESPN2.us@SD`).
- ✅ **Unifica señales duplicadas**: `[Opc.2]`, `HD`, `SD`, `FHD`, `4K`,
  `(1080p)`, `[LAT]`, `Señal 2`, `Feed 4`, `Full HD`, país al final…
- ✅ Publica **alias `<display-name>`** (nombre limpio + `tvg-name` + nombre
  original del M3U) para que el reproductor empareje también por nombre.
- ✅ Genera `epg.xml` con `<channel>` (`<display-name>` + `<icon src="...">`) y
  los `<programme>` **reales** de cada canal emparejado, conservando título,
  subtítulo, descripción, categoría, episodio, icono…
- ✅ Fechas en formato `YYYYMMDDHHMMSS +0000`, con la ventana arrancando en la
  **fecha actual del sistema a las 00:00:00**.
- ✅ UTF-8, declaración XML, `<!DOCTYPE tv SYSTEM "xmltv.dtd">` e indentación
  limpia de 2 espacios.
- ✅ **Publica una lista M3U propia (`lista.m3u`)** ordenada por grupo y nombre,
  con `tvg-id` (el mismo `<channel id="...">` del `epg.xml`), `tvg-name`
  estándar, `tvg-logo` limpio y `group-title`, más `x-tvg-url`/`url-tvg` en la
  cabecera.
- ✅ **No elimina los duplicados/espejos**: las señales de respaldo (`[Opc.2]`,
  `Backup`, `Espejo`, `Señal 2`…) se conservan como entradas propias con el
  `tvg-id`/`tvg-name` de su canal principal, así que todas muestran la misma
  programación real.
- ✅ **Mapeo explícito de señales difíciles** en `mapeo_canales.json` (o en el
  código): `ESPN 2 HD`, `ESPN 2 (Opc.2)` y `ESPN2 HD - (Opc.3)` apuntan todos a
  `ESPN2.us`. Editable sin tocar el script y desactivable con `--sin-mapeo`.
- ✅ **Cero dependencias externas** (`urllib.request`, `re`, `gzip`, `io`, `json`,
  `xml.etree.ElementTree`, `datetime`, `argparse`, `unicodedata`).
- ✅ **77 pruebas unitarias** con `unittest` y workflow de GitHub Actions.

## Requisitos

- Python **3.9 o superior** (probado con 3.14 en Windows y 3.12 en Linux).

## Estructura del proyecto

```
guiadecanales/
├── generar_epg.py                  # script principal (todo el flujo)
├── epg.xml                         # guía generada (se versiona para publicarla)
├── lista.m3u                       # lista propia generada, con el tvg-id de la guía
├── mapeo_canales.json              # alias -> tvg-id real de las señales difíciles
├── tests/
│   └── test_generar_epg.py         # pruebas unitarias (unittest)
├── .github/workflows/actualizar-epg.yml   # regeneración diaria + commit automático
├── .vscode/launch.json             # ejecutar/depurar con F5 en VS Code
├── .gitignore
├── LICENSE                         # MIT
└── README.md
```

## Uso rápido en VS Code

1. Abre la carpeta del proyecto en VS Code.
2. `Ctrl+Shift+P` → **Python: Select Interpreter** → elige tu Python 3.9+.
3. Abre `generar_epg.py` y pulsa **F5** (o `Ctrl+F5`), o usa la terminal:

```powershell
# Prueba sin conexión: lista M3U + guía EPG de ejemplo incluidas en el script
python generar_epg.py --demo

# Con tu lista real y las guías reales por país (por defecto
# MX,ES,AR,CO,CL,PE,EC,UY,CR,PA,DO,SV,PT,IT,DE,US + Pluto/Samsung/Roku)
python generar_epg.py --url "https://raw.githubusercontent.com/USUARIO/REPOSITORIO/main/lista.m3u"

# Solo los países que te interesan (sin las guías extra FAST)
python generar_epg.py -u "$env:M3U_URL" --epg-pais MX,ES,US --sin-extras

# Una guía XMLTV concreta (URL o archivo local, XML o .gz)
python generar_epg.py -u "$env:M3U_URL" --epg https://www.open-epg.com/files/mexico1.xml
python generar_epg.py -u "$env:M3U_URL" --epg-archivo C:\guias\mexico1.xml

# Guardar en otra ruta y con otro nombre, con 7 días de programación
python generar_epg.py --url "..." --salida "C:\ruta\epg.xml" --horas-ventana 168

# Volver a la parrilla genérica (sin descargar guías reales)
python generar_epg.py -u "$env:M3U_URL" --sin-epg --relleno

# Cambiar la ruta de la lista propia (por defecto: lista.m3u)
python generar_epg.py -u "$env:M3U_URL" -m "C:\ruta\lista.m3u"

# Solo la guía XMLTV, sin regenerar la lista M3U
python generar_epg.py -u "$env:M3U_URL" --sin-m3u
```

Ver todas las opciones:

```powershell
python generar_epg.py -h
```

## Configurar la URL de la lista M3U

La URL debe ser el **enlace `raw`** del archivo (texto plano que empieza por
`#EXTM3U`), por ejemplo:

```
https://raw.githubusercontent.com/USUARIO/REPOSITORIO/main/lista.m3u
```

> ⚠️ `https://githubusercontent.com` **no** es una lista M3U: es el dominio base
> de GitHub y devuelve HTML, no el archivo. Usa siempre la URL `raw` completa
> (`raw.githubusercontent.com/...`). Si el script detecta el valor de ejemplo,
> se detiene con un mensaje explicando cómo indicar la URL correcta.

Puedes indicarla de tres formas (de mayor a menor prioridad):

| Forma | Ejemplo |
| --- | --- |
| Argumento `--url` | `python generar_epg.py --url "https://raw.githubusercontent.com/..."` |
| Variable de entorno | PowerShell: `$env:M3U_URL="https://..."; python generar_epg.py` |
| Constante del script | Editar `URL_M3U` al inicio de `generar_epg.py` |

## Opciones de línea de comandos

| Opción | Descripción |
| --- | --- |
| `-u`, `--url` | URL `raw` de la lista M3U. |
| `-s`, `--salida` | Archivo XMLTV de salida (por defecto `epg.xml`). |
| `-m`, `--m3u-salida` | Lista M3U propia de salida (por defecto `lista.m3u`). |
| `--sin-m3u` | No generar la lista M3U propia: solo el XMLTV. |
| `--mapeo` | JSON con el mapeo alias → `tvg-id` real (por defecto `mapeo_canales.json`). |
| `--sin-mapeo` | Ignorar el mapeo explícito (usa solo el `tvg-id`/nombre del M3U). |
| `--epg-publica` | URL pública del `epg.xml` que se declara en la cabecera del M3U. |
| `-t`, `--timeout` | Espera máxima de la descarga en segundos (30 por defecto). |
| `-f`, `--fecha` | Fecha de inicio `YYYY-MM-DD` (por defecto, hoy). |
| `-e`, `--epg` | Fuente EPG real (URL o ruta local, XML o `.gz`). Repetible o separada por comas. |
| `--epg-pais` | Países ISO de las guías públicas (`MX,ES,AR`…). Por defecto `MX,ES,AR,CO,CL,PE,EC,UY,CR,PA,DO,SV,PT,IT,DE,US`. |
| `--epg-archivo` | Guía XMLTV local para emparejar los canales (no se descarga nada). |
| `--sin-epg` | No usar guías reales: solo parrilla genérica de 24 h. |
| `--sin-extras` | No descargar las guías extra (DSports/DIRECTV Sports, Pluto TV, Samsung TV Plus y Roku). |
| `--horas-ventana` | Horas de programación real que se conservan (72 por defecto). |
| `--relleno` | Rellenar con la parrilla genérica los canales **sin** programación real. |
| `--sin-tvg-id` | Ignora el `tvg-id` y genera los `id` desde el nombre limpio. |
| `--conservar-tvg-id` | Respeta el `tvg-id` literal (con el sufijo `@HD`/`@SD`). |
| `--usar-tvg-id` | *Obsoleto*: ahora el `tvg-id` se usa siempre que exista (ver `--sin-tvg-id`). |
| `--sin-doctype` | No escribir la línea `<!DOCTYPE tv SYSTEM "xmltv.dtd">`. |
| `--demo` | Genera la guía con la lista M3U **y la EPG de ejemplo**, sin descargar nada. |
| `-q`, `--silencioso` | Muestra solo los errores. |

Variables de entorno equivalentes: `M3U_URL`, `M3U_SALIDA`, `M3U_MAPEO`,
`EPG_SALIDA`, `EPG_REPO_URL`, `EPG_PUBLICA_URL`, `EPG_URL` (fuentes separadas
por comas), `EPG_PAISES`, `EPG_URL_PLANTILLA` (plantilla con `{pais}`),
`EPG_URL_US` (guía de EE. UU.), `EPG_URLS_EXTRA` (guías extra; vacía o sin
definir deja las de por defecto, `--sin-extras` las desactiva) y `EPG_HORAS`.

## Programación real (EPG externo)

### Fuentes

| Fuente | Cómo se usa |
| --- | --- |
| **Guías públicas por país** (por defecto) | `https://epgshare01.online/epgshare01/epg_ripper_{PAIS}1.xml.gz` para cada código de `--epg-pais` (`MX,ES,AR,CO,CL,PE,EC,UY,CR,PA,DO,SV,PT,IT,DE,FR`). |
| **Estados Unidos** | `US` no existe en epgshare01 (404), así que se descarga `https://epg.pw/xmltv/epg_US.xml` (~160 MB sin comprimir, ~5.500 canales); se puede cambiar con `EPG_URL_US`. |
| **Guías extra** (se suman a las de país) | **DSports/DIRECTV Sports** (`https://epgshare01.online/epgshare01/epg_ripper_DIRECTVSPORTS1.xml.gz`, ~31 KB: `DSPORTS (ARG)`, `DSports 2`, `DSports +`, `DSports Motor`) y los canales FAST de Pluto TV, Samsung TV Plus y Roku (`https://i.mjh.nz/...`). `EPG_URLS_EXTRA` las cambia y `--sin-extras` las quita. |
| Cualquier XMLTV | `--epg URL`, `--epg URL1,URL2`, `--epg-archivo guia.xml.gz` o `EPG_URL`. Con una fuente explícita **no** se añaden las extra. |
| `--demo` | Usa `epg_de_ejemplo()`, una guía XMLTV incluida en el script: sirve para ver el cruce completo sin red. |

Cada fuente se procesa en orden y **un fallo no detiene el proceso**: se avisa y
se continúa con la siguiente (un error 404 no se reintenta). Un canal solo se da
por resuelto cuando la guía le aportó `<programme>`: si una guía publica el
`<channel>` pero sin parrilla (es lo que pasa con `DSPORTS`/`DSPORTS 2` en las
guías de país), una fuente posterior sí puede emparejarlo. Si ninguna se puede
usar, la guía se genera con la parrilla genérica para que el reproductor no quede
sin datos. Con una lista de 1.875 entradas, el conjunto por defecto (17 países +
DSports + Pluto TV + Samsung TV Plus + Roku) tarda menos de un minuto y publica
**353 canales con programación real** (11,4 MB): 65 más que antes de añadir la
guía de DSports y de dejar de dar por resuelto un canal cuando una guía solo lo
lista sin parrilla. La guía de `FR` se incluye
porque aporta canales internacionales (`Trace Latina`, `24h`, `ON TV`…); sus
canales `CANAL+LIVE NN` se distinguen del "Canal NN" latinoamericano porque
`CANAL+` se trata como **marca**, no como la palabra «canal» seguida de «live».

Cifras medidas del último run (1.875 entradas en la lista del proveedor):

| Archivo | Resultado |
| --- | --- |
| `epg.xml` | 1.432 canales, **353 con EPG real**, 26.236 `<programme>`, 11,4 MB |
| `lista.m3u` | **1.875 entradas**: 1.432 canales + **443 señales de respaldo** conservadas, 46 grupos, **0** `tvg-id` fuera de la guía |

Con el mapeo de DSports, la lista publica sus señales con la parrilla real:
`DSPORTS.(ARG).dtvsp` (`DSports` + `Opc. 2`…`Opc. 5`, 5 entradas) con 45
`<programme>`, `DSPORTS.2.HD.dtvsp` (`DSports 2` + 2 respaldos) con 42 y
`DSPORTS.+.HD.dtvsp` (`DSports +` + 1 respaldo) con 55.

> Al mapear variantes (`Canal 5 Nacional`, `Las Estrellas Nacional v1/v2`…) el
> número de canales puede bajar unas unidades: son el mismo canal, que antes se
> publicaba dos veces con ids distintos. La programación real no se pierde.

### Emparejamiento (canal del M3U ↔ canal de la guía)

1. **`tvg-id` exacto** con el `id` de la guía (es el mecanismo que usan los
   reproductores cuando la guía y la lista comparten ids, p. ej. iptv-org).
2. **`tvg-id` normalizado** (sin `@HD`, sin símbolos, sin mayúsculas).
3. **Nombre normalizado**: se comparan los tokens del nombre después de
   quitar calidad (`HD`, `FHD`, `4K`…), grupos (`[Opc.2]`, `(1080p)`), el país
   del final, los códigos de país sueltos (`ESPN MX` = `ESPN`) y palabras sin
   valor (`Canal`, `de`, `TV`…). También se comparan las **partes** del
   `display-name` de la guía, porque muchas concatenan alias
   (`Canal 2 de México (Canal Las Estrellas - XEW)`). También se comparan los
   alias del M3U (`tvg-name` y nombre original).

La coincidencia por nombre exige un **puntaje mínimo (0,75)**: el nombre corto
debe cubrir al menos 3/4 del largo y aparecer de forma contigua. Así:

| Canal del M3U | `display-name` de la guía | ¿Empareja? |
| --- | --- | --- |
| `Canal 5 HD` | `Canal 5 de México (XHGC)` | ✅ sí (`canal 5`) |
| `Las Estrellas FHD [LAT]` | `Canal 2 de México (Canal Las Estrellas - XEW)` | ✅ sí (alias entre paréntesis) |
| `TV UNAM HD` | `Canal TVUNAM` | ✅ sí (`TV UNAM` = `TVUNAM`) |
| `ESPN MX` | `ESPN` | ✅ sí (código de país descartado) |
| `FOX TUBI CEAM` | `FOX` | ⚠️ sí (falso positivo: el alias `FOX` está en el nombre) |
| `TVO [No 24/7] (Quetzaltenango)` | `TVO HD` | ⚠️ sí (falso positivo: el alias `TVO` está en el nombre) |
| `ESPN` | `ESPN 2` | ❌ no (cobertura 1/2) |
| `Canal 12` | `CANAL+LIVE 12` (Francia) | ❌ no (`CANAL+` es una marca, no «canal» + «live») |
| `Canal 7 HD` | `Canal 7 de México` | ✅ sí |
| `Canal 7 HD` | `La 7` (España) | ❌ no (uno dice `canal`, el otro no) |
| `Deportes 24/7` | `La 7` | ❌ no (no se divide por `/`) |
| `Canal Local` | *(no está en la guía)* | ❌ no → sin programas |

Los dos casos ⚠️ son el precio de cubrir los canales que llevan calificadores
que la guía no repite (`Nacional`, `Geo-Blocked`, `No 24/7`, `Regional`,
`v2`…): filtrar los alias de una sola palabra quitaría también ~50 canales
legítimos (`Las Estrellas Nacional v1`, `UFC EE.UU`, `Teleantioquia Antioquia /
Regional`…), así que se prefiere el par de falsos positivos.

Por el lado de la guía, se descartan los `Canal NN` **genéricos de Francia**
(`Canal 11`, `Canal 21`, `Canal 32` de Telerama emiten el bucle de Canal+), que
si no le roban la programación a `CANAL 32 (Televisión Chichicasteca)`. El
filtro solo se aplica a la guía `FR`, para no tocar los `Canal NN` reales de
otros países (`Canal 7 de México`, `Canal 9` de Panamá = TVMax).

Además, **un canal de la guía no se reutiliza** para dos canales del M3U y la
asignación se resuelve **por puntaje global**: primero se emparejan los nombres
idénticos y después los parecidos, así que un nombre exacto nunca pierde su
canal frente a uno aproximado. En caso de empate gana el candidato del mismo
país (`tvg-id` con sufijo `.mx`, `.es`… o el país del nombre) y, si sigue
empatado, el nombre con un número de palabras más parecido.

### Ventana temporal y salida

- Se conserva la programación que **se solapa** con la ventana
  `[hoy 00:00 UTC, +N horas]` (N = `--horas-ventana`, 72 por defecto). Los
  programas que empezaron antes pero siguen en emisión se mantienen.
- Las fechas se **normalizan a UTC (`+0000`)**: las guías suelen venir con
  desfases locales (`-0600`, `+0100`…).
- Se copian del original `title`, `sub-title`, `desc`, `category`,
  `episode-num`, `date`, `icon`, `rating`… y se descarta el resto.
- Se eliminan duplicados (`mismo canal + misma hora`) y los programas sin
  `<title>` (XMLTV lo exige).
- Los canales **sin coincidencia** se publican con su `<channel>` y **sin
  `<programme>`**; con `--relleno` reciben la parrilla genérica.



## Cómo se empareja la guía con la lista (`tvg-id`)

1. **Si la entrada trae un `tvg-id` válido, ese es el `id` del `<channel>`.**
   Es el valor que el reproductor compara con la lista, así que es el que
   garantiza que la guía no salga vacía.
2. El sufijo posterior a `@` se descarta porque identifica la **calidad o el
   feed**, no el canal: `ESPN2.us@HD`, `ESPN2.us@SD` y `ESPN2.us` son el mismo
   `<channel>` (`ESPN2.us`); `ESPNLatinAmerica.us@Argentina` y
   `ESPNLatinAmerica.us@South` también. Con `--conservar-tvg-id` se respeta el
   valor literal (para listas que emparejan de forma estricta).
3. Si el `tvg-id` no existe o no es utilizable (`""`, `0`, `none`, `sin-epg`,
   con espacios…), el `id` se genera desde el nombre limpio (`ESPN 2 HD` →
   `espn2`).
4. Cada `<channel>` publica varios `<display-name>`: el nombre limpio (con
   `lang="es"`), el `tvg-name` del M3U y el nombre original de la entrada
   (`ESPN 2 HD [LAT]`), para que el emparejamiento por nombre también funcione.

## Lista M3U propia (`lista.m3u`)

Cada ejecución escribe, junto al `epg.xml`, una lista **`lista.m3u`** derivada de
la lista original del proveedor:

```m3u
#EXTM3U x-tvg-url="https://USUARIO.github.io/REPOSITORIO/epg.xml" url-tvg="https://USUARIO.github.io/REPOSITORIO/epg.xml"
# Lista generada por guiadecanales-epg (generar_epg.py)
#EXTINF:-1 tvg-id="ESPN2.us" tvg-name="ESPN 2" tvg-logo="https://.../espn2.png" group-title="Deportes",ESPN 2
#EXTVLCOPT:http-user-agent=Mozilla/5.0
http://proveedor.example/espn2-hd/index.m3u8
#EXTINF:-1 tvg-id="ESPN2.us" tvg-name="ESPN 2" tvg-logo="https://.../espn2.png" group-title="Deportes",ESPN 2 (Opc. 2)
http://proveedor.example/espn2-opc2/index.m3u8
```

Qué hace exactamente:

1. **Ordena** los canales por `group-title` y nombre (los que no tienen grupo, al
   final), de modo que el orden es estable entre ejecuciones y los *diffs* del
   repositorio son mínimos.
2. **Limpia los atributos**: `tvg-id` (el `id` real del canal, idéntico al
   `<channel id="...">` del `epg.xml`), `tvg-name` estándar, `tvg-logo` solo si es
   una URL `http(s)` válida y `group-title` sin mojibake ni comillas sueltas.
3. **Conserva las señales de respaldo.** Los duplicados/espejos de distintos
   servidores (`[Opc.2]`, `[Opc.3]`, `Backup`, `Espejo`, `Señal 2`…) **no se
   eliminan**: se publican como entradas propias con el **mismo `tvg-id` y
   `tvg-name`** de su canal principal y un sufijo visible (`(Opc. 2)`, `(Opc. 3)`)
   para distinguirlas en el reproductor. Así OTT Navigator muestra la
   programación real en **todas** las opciones y puedes cambiar de servidor sin
   perder la EPG.
4. **Conserva las opciones de reproducción** de cada entrada
   (`#EXTVLCOPT:http-user-agent=...`, `#KODIPROP:...`, `#EXTGRP:...`), necesarias
   para los canales que exigen un user-agent o un referer concreto.
5. **Declara la guía** en la cabecera (`x-tvg-url` y `url-tvg`), así que el
   reproductor descarga la EPG automáticamente. La URL sale de `--epg-publica` o
   de `EPG_PUBLICA_URL` (por defecto, el `raw` de tu repositorio).

### Mapeo explícito de señales difíciles (`mapeo_canales.json`)

Algunas entradas no se pueden emparejar por nombre porque el mismo canal aparece
con nombres muy distintos. El diccionario de mapeo fuerza el `tvg-id` real y el
nombre estándar:

```json
{
  "canales": {
    "ESPN2.us": {
      "nombre": "ESPN 2",
      "alias": ["ESPN 2 HD", "ESPN 2 (Opc.2)", "ESPN2 HD - (Opc.3)", "ESPN 2 (Señal 2)"]
    },
    "LasEstrellas.mx": ["Las Estrellas", "Las Estrellas HD"]
  }
}
```

- Formatos admitidos por canal: lista de alias o un objeto con
  `nombre`/`logo`/`alias`.
- Los alias del JSON **se suman** a los definidos en el código
  (`MAPEO_CANALES_POR_DEFECTO`), y el `nombre`/`logo` del JSON tienen prioridad.
- Se busca con el nombre ya normalizado (sin `HD`, `[Opc.2]`, `(1080p)`…), pero
  **conservando el país**: `ESPN 2 (Netherlands)` no se confunde con `ESPN 2`
  (`ESPN2.us`). Las claves que empiezan por `$` o `_` se ignoran (documentación).
- En un segundo intento se ignoran los **calificadores de señal** (`Nacional`,
  `Regional`, `v1`, `v2`, `Espejo`, `Backup`…): `Canal 5 Nacional` y
  `Las Estrellas Nacional V2` caen en `Canal5.mx` y `LasEstrellas.mx`. Un nombre
  parecido pero distinto (`Canal 5 Del Pueblo Alcorta`) **no** se ve afectado.
- Usa `--mapeo otra_ruta.json` para otro archivo o `--sin-mapeo` para desactivarlo.

#### DSports / DIRECTV Sports

Los ids del proveedor (`dsports`, `dsports2`) **no existen en ninguna guía por
país**, así que estos canales se publicaban sin programación. Se mapean a los ids
reales de la guía de deportes de epgshare01 (`epg_ripper_DIRECTVSPORTS1`), que
es la única que trae su parrilla:

| `tvg-id` resultante | Nombre estándar | Alias que se mapean |
| --- | --- | --- |
| `DSPORTS.(ARG).dtvsp` | `DSports` | `DSports`, `DSports HD/SD/FHD/4K`, `DSports (Opc. 2)`, `DSports Espejo`, `DSports Backup`, `DSports Argentina`, `DIRECTV Sports`, `DIRECTV Sports HD` |
| `DSPORTS.2.HD.dtvsp` | `DSports 2` | `DSports 2`, `DSports2`, `DSports 2 HD`, `DSports 2 (Opc. 2)`, `DIRECTV Sports 2`, `DIRECTV Sports 2 HD` |
| `DSPORTS.+.HD.dtvsp` | `DSports +` | `DSports +`, `DSports+`, `DSports Plus`, `DSports Plus HD`, `DSports Plus (Opc. 2)`, `DIRECTV Sports +` |
| `DSports.Motor.dtvsp` | `DSports Motor` | `DSports Motor`, `DSports Motor HD`, `DIRECTV Sports Motor` |

- El `+` **forma parte del nombre**: al calcular la clave del mapeo se convierte
  en `plus`, así `DSports +` (`dsportsplus`) no se confunde con `DSports`
  (`dsports`). Lo mismo al emparejar por nombre: `DSPORTS +(DTS+)` de la guía
  colombiana ya no le roba la parrilla a `DSports`.
- `DSports Fight`, `DSports Radio` o `DSports Uruguay` se quedan con su id
  propio: no hay fuente pública con su parrilla y no se les asigna la de otro
  canal.



Muchas listas IPTV se generan en `cp1252`/`latin-1` y en algún punto se leen
como `cp850`/`cp437`, de modo que los acentos se convierten en símbolos. Antes
de analizar el M3U, `parsear_m3u()` aplica `sanear_codificacion()`, que repara
esos textos de forma reversible y **sin tocar los nombres que ya están bien**:

| Nombre en el M3U (roto) | Nombre corregido | Tipo de mojibake |
| --- | --- | --- |
| `Azteca 7 MÚxico` | `Azteca 7 México` | `cp1252` leído como `cp850` (`é` → `Ú`) |
| `Anime Visi¾n Espa±a` | `Anime Visión España` | `ñ` → `±`, `ó` → `¾` |
| `Hispania TV Panamß` | `Hispania TV Panamá` | `á` → `ß` |
| `NSTV C·cuta` | `NSTV Cúcuta` | `ú` → `·` |
| `Canal Trece AmazonÝa` | `Canal Trece Amazonía` | `í` → `Ý` |
| `Azteca 7 MÃ©xico` | `Azteca 7 México` | UTF-8 leído como `cp1252` |
| `PanamÃ¡ TV` | `Panamá TV` | UTF-8 leído como `cp1252` |

Reglas de seguridad para no estropear nombres correctos:

- Solo se intenta reparar cuando aparece un carácter imposible en un nombre bien
  codificado (`ß`, `±`, `·`, `¾`, `Ý`, recuadros `░▒▓│`…) o una secuencia de
  doble codificado (`Ã©`, `Ã±`, `â€“`).
- La reparación se valida palabra por palabra: si el resultado sigue teniendo
  señales de mojibake, se descarta y se deja el texto original.
- Se respetan las mayúsculas acentuadas legítimas al inicio de palabra
  (`Único TV`, `Última Hora`), las palabras en mayúsculas (`PERÚ`) y los
  símbolos válidos (`90° TV`, `1º Canal`, separadores `·` o `|` sueltos).
- Es idempotente: aplicarlo dos veces produce el mismo resultado.

Como el saneado ocurre **antes** de limpiar los nombres, los países que antes
quedaban sin reconocer ya se descartan bien: `Azteca 7 MÚxico` → `Azteca 7`
(antes se quedaba como `Azteca 7 MÚxico`) y dos variantes rotas del mismo canal
(`MÚxico` y `MÃ©xico`) se unifican en una sola señal.

## Cómo se limpian los nombres

| Nombre en el M3U | Nombre limpio | `id` generado |
| --- | --- | --- |
| `Canal 5 HD` | `Canal 5` | `canal5` |
| `Canal 5 FHD [Opc.2]` | `Canal 5` | `canal5` |
| `Las Estrellas [Opc.2]` | `Las Estrellas` | `lasestrellas` |
| `Cine Latino FHD` | `Cine Latino` | `cinelatino` |
| `Cine Latino SD` | `Cine Latino` | `cinelatino` |
| `Mega Noticias (1080p)` | `Mega Noticias` | `meganoticias` |
| `TNT Series FHD (Opcion 3)` | `TNT Series` | `tntseries` |
| `TNT Series Feed 4` | `TNT Series` | `tntseries` |
| `HBO SignatureHD` | `HBO Signature` | `hbosignature` |
| `ESPN 2 HD [LAT]` | `ESPN 2` | `espn2` |
| `ESPN2` | `ESPN2` | `espn2` |
| `ESPN 2 FHD` | `ESPN 2` | `espn2` |
| `ESPN Premium HD (1080p)` | `ESPN Premium` | `espnpremium` |
| `TNT Sports Chile HD` | `TNT Sports` | `tntsports` |
| `Deportes 24/7 UHD` | `Deportes 24/7` | `deportes247` |

\* Las variantes `ESPN2`, `ESPN 2`, `ESPN 2 HD [LAT]` y `ESPN 2 FHD` comparten el
mismo `id` (`espn2`), que es lo que importa para unificar la guía; el nombre
publicado es el de la primera aparición en la lista.

> ℹ️ El país del final (`Chile`, `(Argentina)`, `- México`, `[LAT]`) se descarta
> **solo si el nombre sigue teniendo al menos dos palabras**: `TNT Sports Chile`
> → `TNT Sports`, pero `Cine Latino`, `Mega Chile` o `ESPN Chile` se conservan.
> Y si dos señales del mismo nombre solo se diferencian en el país (`TNT Sports
> Chile` y `TNT Sports Argentina`), **no se fusionan**: se quedan como dos
> canales con su país en el nombre.

Las entradas que producen el **mismo `id`** se consideran la misma señal: se
publica un solo `<channel>` (con el logo de la primera aparición que lo tenga) y
un único bloque de programación, evitando duplicados en la guía.

## Parrilla genérica de respaldo (24 horas)

Solo se usa para los canales **sin** programación real cuando se pasa
`--relleno`, o para toda la guía cuando no hay ninguna fuente EPG utilizable
(`--sin-epg`, fallo de red…). Son seis bloques continuos de 4 horas por canal,
con títulos variables en español y el nombre del canal al final:

| Horario (UTC) | Categoría | Títulos posibles (ejemplos) |
| --- | --- | --- |
| 00:00 – 04:00 | Madrugada | Cine de Madrugada · Insomnio Musical · Noticiero de Madrugada |
| 04:00 – 08:00 | Matutino | **Noticiero Matutino** · Primera Hora Informativa · Buenos Días con Nosotros |
| 08:00 – 12:00 | Variedades | Magazine Matutino · Cocina y Hogar · Consultorio de la Salud |
| 12:00 – 16:00 | Tarde | Noticiero del Mediodía · **Cine Familiar de la Tarde** · Telenovela de la Tarde |
| 16:00 – 20:00 | Vespertino | Deportes en Vivo · Noticiero Vespertino · Concurso de la Tarde |
| 20:00 – 24:00 | Estelar | **Bloque Estelar Nocturno** · Película de la Noche · Late Night Show |

Los títulos se eligen de forma determinista a partir del `id` del canal, así que
la parrilla es estable entre ejecuciones pero distinta entre señales.

## Ejemplo de salida (`epg.xml`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE tv SYSTEM "xmltv.dtd">
<tv generator-info-name="guiadecanales-epg" generator-info-url="https://github.com/usuario/repositorio">
  <channel id="canal5.mx">
    <display-name lang="es">Canal 5</display-name>
    <display-name>Canal 5 HD</display-name>
    <icon src="https://upload.wikimedia.org/wikipedia/commons/thumb/1/1e/Canal_5_logo.svg/240px-Canal_5_logo.svg.png"/>
  </channel>
  <programme start="20260311003000 +0000" stop="20260311023000 +0000" channel="canal5.mx">
    <title lang="es">Las crónicas de Spiderwick</title>
  </programme>
  <programme start="20260311023000 +0000" stop="20260311043000 +0000" channel="canal5.mx">
    <title lang="es">Me caigo de risa</title>
  </programme>
  <!-- ... programación real del resto de canales emparejados ... -->
  <!-- Los canales sin coincidencia en la guía no llevan <programme> -->
</tv>
```

Para inspeccionar el resultado de una ejecución, la salida del script resume
cuántos canales se emparejaron con cada guía:

```
[4/6] Descargando 2 guia(s) XMLTV reales ...
      Ventana de programacion: 2026-11-03 00:00 a 2026-11-06 00:00 UTC
      https://epgshare01.online/epgshare01/epg_ripper_MX1.xml.gz
        canales en la guia: 178 | emparejados: 96 | programas: 5211
      Canales con programacion real: 118 | sin coincidencia: 1433
```

## Ejecutar las pruebas

```powershell
python -m unittest discover -s tests -v
```

Cubren: limpieza de nombres (calidades, opciones, `Full HD`, país al final),
generación de `id`, prioridad y normalización del `tvg-id`, unificación de
duplicados, parseo del M3U, **saneado de mojibake** (`MÚxico`, `Espa±a`,
`Panamß`, `MÃ©xico` y los casos que no deben tocarse: `Único TV`, `90° TV`,
`Cine Latino`), **lectura de guías XMLTV reales** (canales, `display-name`,
fechas con desfase a UTC, ventana temporal, gzip), **emparejamiento**
(`tvg-id`, nombre normalizado, alias entre paréntesis, `ESPN` ≠ `ESPN 2`,
`Canal 7` ≠ `La 7`), **integración completa** (programas reales con el `id` del
canal, canales sin EPG sin contenido inventado, `--relleno` opcional, fuente
rota que no interrumpe, XMLTV válido en UTF-8), presencia de
`<display-name>`/`<icon>`, continuidad de los 6 bloques de 4 h, formato
`YYYYMMDDHHMMSS +0000`, integridad `programme` → `channel` y cabecera UTF-8 del
archivo.

Además, las pruebas de la **lista M3U propia** comprueban que: el `mapeo_canales.json`
del repositorio es válido y `ESPN 2 HD`/`ESPN 2 (Opc.2)`/`ESPN2 HD - (Opc.3)`
caen en `ESPN2.us`; las variantes con calificador (`Canal 5 Nacional`,
`Las Estrellas Nacional V2`, `Azteca 7 Regional`) caen en su canal principal
mientras `Canal 5 Del Pueblo Alcorta` se mantiene aparte; el mapeo no roba el
canal de otro país (`ESPN 2 (Netherlands)`); las señales de respaldo se
conservan (una entrada por señal con el mismo `tvg-id`/`tvg-name`) junto con sus
`#EXTVLCOPT`; cada `tvg-id` del M3U existe como `<channel>` del `epg.xml`; los
atributos salen limpios; el orden es por grupo y nombre; y `main` escribe ambos
archivos (`--sin-m3u` los separa).

Y las de **DSports** (`CoberturaDSportsTests`) cubren el caso real: `DSports`,
`DSports HD`, `DSports (Opc. 2)`, `DSports 4K`, `DIRECTV Sports`… acaban en
`DSPORTS.(ARG).dtvsp`; `DSports 2`/`DIRECTV Sports 2` en `DSPORTS.2.HD.dtvsp`;
`DSports +`/`DSports Plus` en `DSPORTS.+.HD.dtvsp` **sin** confundirse con el
canal principal; `DSports Motor` en `DSports.Motor.dtvsp`; `DSports Fight`
conserva su id; la guía deportiva aporta `<programme>` reales; una guía que
publica el canal sin parrilla no bloquea a la siguiente; la lista publicada se
puede regenerar sin perder ids; y la guía `DIRECTVSPORTS1` está entre las fuentes
por defecto (también cuando `EPG_URLS_EXTRA` llega vacía).

## Publicar en GitHub (pasos)

```powershell
git init
git add .
git commit -m "feat: generador de EPG XMLTV desde lista M3U"
git branch -M main
git remote add origin https://github.com/USUARIO/REPOSITORIO.git
git push -u origin main
```

### 1. Automatizar la regeneración diaria

El workflow `.github/workflows/actualizar-epg.yml` ya está incluido: ejecuta las
pruebas, regenera el `epg.xml` **y la `lista.m3u`** cada día y hace *commit* de
ambos archivos.

1. En el repositorio: **Settings → Secrets and variables → Actions → New
   repository secret**.
2. Nombre: `M3U_URL` · Valor: la URL `raw` de tu lista M3U.
3. (Opcional) En **Settings → Secrets and variables → Actions → Variables**,
   crea `EPG_REPO_URL` con la URL pública de tu repositorio para que aparezca en
   `generator-info-url`.
4. (Opcional) En la misma pantalla de *Variables* puedes ajustar las fuentes EPG:
   `EPG_PAISES` (`MX,ES,AR,CO,CL,PE,EC,UY,CR,PA,DO,SV,PT,IT,DE,US` por defecto),
   `EPG_URLS_EXTRA` (guías extra: DSports + FAST; vacío = las de por defecto) y/o `EPG_URL`
   (URLs separadas por comas, sustituye por completo a los países). Sin valores
   se usan todas las guías públicas por defecto, incluida la de EE. UU.
   También puedes fijar `EPG_PUBLICA_URL` si sirves el `epg.xml` con otro dominio
   (por defecto se usa el `raw` de tu repositorio en la cabecera del M3U).
5. Lanza el workflow manualmente desde **Actions → Actualizar EPG → Run
   workflow** para comprobar que funciona.

> El paso de verificación del workflow comprueba que el XMLTV tiene canales,
> que **hay programación**, que **ningún `<programme>` apunta a un canal
> inexistente**, que cada programa tiene `<title>` y que no hay dos programas
> del mismo canal a la misma hora. Además valida la `lista.m3u`: cabecera
> `#EXTM3U` con `x-tvg-url`, entradas con URL `http(s)`, `tvg-id` en todas las
> entradas y **todos esos `tvg-id` presentes en el `epg.xml`**.
>
> Si tu lista M3U es privada, guárdala en un secreto (`M3U_URL`): el script solo
> necesita poder descargarla por HTTP(S).

### 2. Publicar el EPG y la lista con GitHub Pages

1. **Settings → Pages** → *Source*: `Deploy from a branch` → Branch: `main`,
   carpeta `/ (root)` → **Save**.
2. Tus archivos quedan disponibles en:

```
https://USUARIO.github.io/REPOSITORIO/epg.xml
https://USUARIO.github.io/REPOSITORIO/lista.m3u
```

3. Apunta el reproductor IPTV a la **lista**; la EPG ya va incluida en su
   cabecera (y en el `tvg-id` de cada canal):

```
https://USUARIO.github.io/REPOSITORIO/lista.m3u
```

> Si prefieres tu propia lista, añade la cabecera con la guía:
>
> ```m3u
> #EXTM3U url-tvg="https://USUARIO.github.io/REPOSITORIO/epg.xml" x-tvg-url="https://USUARIO.github.io/REPOSITORIO/epg.xml"
> ```

> GitHub Pages tarda ~1 minuto en publicar cada cambio. Las cachés de algunos
> reproductores pueden tardar más: añade un parámetro al final
> (`.../epg.xml?v=2`) si necesitas forzar la recarga.

## Solución de problemas

| Síntoma | Causa / solución |
| --- | --- |
| `Debes indicar la URL real de tu lista M3U` | Sigue el mensaje: usa `--url`, `M3U_URL` o edita `URL_M3U`. |
| `HTTP Error 404` | La URL no es el enlace `raw` del archivo o el repositorio es privado. |
| `No se encontró ningún canal válido` | El contenido descargado no es un M3U (¿HTML de la página del repo?). |
| La guía aparece vacía en el reproductor | Comprueba la URL del EPG y que los `id` coincidan con los `tvg-id` de tu lista. Por defecto ya se usan esos ids; si tu lista empareja de forma estricta, prueba `--conservar-tvg-id`. |
| Muchos canales salen **sin programación** | No se encontraron en las guías descargadas: añade el país que falta (`--epg-pais MX,ES,AR,US,…`) o una guía propia (`--epg URL`). Revisa el resumen: muestra los emparejados por fuente. |
| Un canal muestra programación de otro | El emparejamiento por nombre falló; ponle a ese canal un `tvg-id` que exista en la guía o usa `--epg-archivo` con una guía que lo contenga. |
| Quiero volver a la parrilla genérica | `--sin-epg --relleno` (o solo `--relleno` para rellenar únicamente los canales sin EPG). |
| `Aviso: se omite <url>` en la salida | Esa guía no se pudo descargar o no es XMLTV válido; el resto sigue funcionando. Se puede fijar otra con `--epg`. |
| La guía tarda mucho en generarse | Baja el número de países (`--epg-pais MX,ES`), quita las guías extra (`--sin-extras`) y/o reduce la ventana (`--horas-ventana 24`). |
| DSports / DIRECTV Sports sin programación | Comprueba que no uses `--sin-extras` (la parrilla viene de `epg_ripper_DIRECTVSPORTS1`, que se suma a las guías de país) y que el canal se llama `DSports`, `DSports 2`, `DSports +` o `DSports Motor`; `DSports Fight`/`Radio` no tienen guía pública. |
| Un canal de EE. UU. sale sin programación | La guía de EE. UU. es la de `epg.pw` (`EPG_URL_US`) y solo se usa si `US` está en `--epg-pais`/`EPG_PAISES`; comprueba el aviso de esa fuente en el resumen. |
| Los `id` salen como `espn2` en vez del `tvg-id` | Tu lista no trae `tvg-id` válido para ese canal, o usaste `--sin-tvg-id`. |
| Varios canales aparecen duplicados en la guía | Son señales distintas (mismo nombre, distinto `tvg-id`/país); con `--sin-tvg-id` se fusionan por nombre. |
| La `lista.m3u` no muestra EPG en OTT Navigator | Comprueba que la cabecera lleva `x-tvg-url`/`url-tvg` (`--epg-publica URL`) y que el reproductor puede abrir esa URL. Todos los `tvg-id` de la lista existen en el `epg.xml`. |
| Un canal sigue con nombre raro en la lista | Añádelo al `mapeo_canales.json` con su `tvg-id` real y un alias; se publicará con el nombre estándar. |
| Faltan las señales de respaldo (`Opc.2`, `Backup`…) | No se pierden: cada señal se publica con el mismo `tvg-id` y un sufijo `(Opc. N)` en el nombre visible. Si dos entradas tienen la **misma URL**, se publica una sola. |
| Nombres con calidad pegada al nombre | Ajusta `_RE_CALIDAD` y `_RE_SUFIJO_PEGADO` (bloque 4 del script). |
| Los nombres salen con símbolos raros en mi PC (`MÚxico`, `Espa±a`, `Panamß`) | El script ya sanea el mojibake al leer el M3U (ver [Saneado de codificación](#saneado-de-codificación-mojibake)). Si aún los ves así, es la consola de Windows (cp850/cp437): abre el `epg.xml` en un editor UTF-8 o usa Windows Terminal. |
| Windows: `UnicodeEncodeError` en la consola | Usa Windows Terminal o PowerShell 7; el archivo `epg.xml` siempre se escribe en UTF-8. |

## Licencia

MIT. Puedes usarlo, modificarlo y redistribuirlo libremente.
