# Guía de canales → EPG XMLTV

Script en **Python (solo librerías estándar)** que descarga una lista **M3U**
remota y genera una **guía de programación `epg.xml` en formato XMLTV** con
24 horas de programación genérica por canal, para que la guía **nunca aparezca
vacía** en el reproductor IPTV.

```
lista.m3u  ──►  generar_epg.py  ──►  epg.xml (XMLTV)
```

## Características

- ✅ Descarga la lista M3U desde una URL (`urllib.request`) con User-Agent de
  navegador, reintentos y detección automática de codificación.
- ✅ Extrae de cada `#EXTINF` el **nombre del canal** (texto posterior a la
  última coma) y el atributo **`tvg-logo`**.
- ✅ **Unifica señales duplicadas**: `[Opc.2]`, `HD`, `SD`, `FHD`, `(1080p)`,
  `[LAT]`, `Señal 2`… y genera un **`id` único** (solo `a-z0-9`).
- ✅ Genera `epg.xml` con `<channel>` (`<display-name>` + `<icon src="...">`) y
  **6 bloques `<programme>` de 4 h** por canal = 24 h continuas.
- ✅ Fechas en formato `YYYYMMDDHHMMSS +0000`, arrancando en la **fecha actual
  del sistema a las 00:00:00**.
- ✅ UTF-8, declaración XML, `<!DOCTYPE tv SYSTEM "xmltv.dtd">` e indentación
  limpia de 2 espacios.
- ✅ **Cero dependencias externas** (`urllib.request`, `re`,
  `xml.etree.ElementTree`, `datetime`, `argparse`, `unicodedata`).
- ✅ 10 pruebas unitarias con `unittest` y workflow de GitHub Actions.

## Requisitos

- Python **3.9 o superior** (probado con 3.14 en Windows y 3.12 en Linux).

## Estructura del proyecto

```
guiadecanales/
├── generar_epg.py                  # script principal (todo el flujo)
├── epg.xml                         # guía generada (se versiona para publicarla)
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
# Prueba sin conexión: usa una lista M3U de ejemplo incluida en el script
python generar_epg.py --demo

# Con tu lista real
python generar_epg.py --url "https://raw.githubusercontent.com/USUARIO/REPOSITORIO/main/lista.m3u"

# Guardar en otra ruta y con otro nombre
python generar_epg.py --url "..." --salida "C:\ruta\epg.xml"
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
| `-t`, `--timeout` | Espera máxima de la descarga en segundos (30 por defecto). |
| `-f`, `--fecha` | Fecha de inicio `YYYY-MM-DD` (por defecto, hoy). |
| `--usar-tvg-id` | Usa el `tvg-id` del M3U como `id` del canal (mejor emparejado con la lista). |
| `--sin-doctype` | No escribir la línea `<!DOCTYPE tv SYSTEM "xmltv.dtd">`. |
| `--demo` | Genera la guía con una lista M3U de ejemplo, sin descargar nada. |
| `-q`, `--silencioso` | Muestra solo los errores. |


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
| `HBO SignatureHD` | `HBO Signature` | `hbosignature` |
| `ESPN 2 HD [LAT]` | `ESPN 2` | `espn2` |
| `Deportes 24/7 UHD` | `Deportes 24/7` | `deportes247` |

Las entradas que producen el **mismo `id`** se consideran la misma señal: se
publica un solo `<channel>` (con el logo de la primera aparición que lo tenga) y
un único bloque de programación, evitando duplicados en la guía.

## Parrilla genérica de 24 horas

Seis bloques continuos de 4 horas por canal, con títulos variables en español y
el nombre del canal al final:

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
  <channel id="canal5">
    <display-name lang="es">Canal 5</display-name>
    <icon src="https://upload.wikimedia.org/wikipedia/commons/thumb/1/1e/Canal_5_logo.svg/240px-Canal_5_logo.svg.png"/>
  </channel>
  <programme start="20260311000000 +0000" stop="20260311040000 +0000" channel="canal5">
    <title lang="es">Cine de Madrugada - Canal 5</title>
    <desc lang="es">Bloque de programacion de 4 horas de Canal 5 (Madrugada). ...</desc>
    <category lang="es">Madrugada</category>
  </programme>
  <!-- ... 5 bloques más por canal hasta completar 24 h ... -->
</tv>
```

## Ejecutar las pruebas

```powershell
python -m unittest discover -s tests -v
```

Cubren: limpieza de nombres, generación de `id`, parseo del M3U, unificación de
duplicados, presencia de `<display-name>`/`<icon>`, continuidad de los 6 bloques
de 4 h, formato `YYYYMMDDHHMMSS +0000` y cabecera UTF-8 del archivo.

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
pruebas, regenera el `epg.xml` cada día y hace *commit* del resultado.

1. En el repositorio: **Settings → Secrets and variables → Actions → New
   repository secret**.
2. Nombre: `M3U_URL` · Valor: la URL `raw` de tu lista M3U.
3. (Opcional) En **Settings → Secrets and variables → Actions → Variables**,
   crea `EPG_REPO_URL` con la URL pública de tu repositorio para que aparezca en
   `generator-info-url`.
4. Lanza el workflow manualmente desde **Actions → Actualizar EPG → Run
   workflow** para comprobar que funciona.

> Si tu lista M3U es privada, guárdala en un secreto (`M3U_URL`): el script solo
> necesita poder descargarla por HTTP(S).

### 2. Publicar el EPG con GitHub Pages

1. **Settings → Pages** → *Source*: `Deploy from a branch` → Branch: `main`,
   carpeta `/ (root)` → **Save**.
2. Tu guía queda disponible en:

```
https://USUARIO.github.io/REPOSITORIO/epg.xml
```

3. En el reproductor IPTV (o en tu lista M3U) apunta la guía a esa URL:

```m3u
#EXTM3U url-tvg="https://USUARIO.github.io/REPOSITORIO/epg.xml" x-tvg-url="https://USUARIO.github.io/REPOSITORIO/epg.xml"
```

> GitHub Pages tarda ~1 minuto en publicar cada cambio. Las cachés de algunos
> reproductores pueden tardar más: añade un parámetro al final
> (`.../epg.xml?v=2`) si necesitas forzar la recarga.

## Solución de problemas

| Síntoma | Causa / solución |
| --- | --- |
| `Debes indicar la URL real de tu lista M3U` | Sigue el mensaje: usa `--url`, `M3U_URL` o edita `URL_M3U`. |
| `HTTP Error 404` | La URL no es el enlace `raw` del archivo o el repositorio es privado. |
| `No se encontró ningún canal válido` | El contenido descargado no es un M3U (¿HTML de la página del repo?). |
| La guía aparece vacía en el reproductor | Comprueba la URL del EPG en el reproductor y que los `id` coincidan con tu M3U: prueba `--usar-tvg-id`, que usa el `tvg-id` original de la lista. |
| Nombres con calidad pegada al nombre | Ajusta `_RE_CALIDAD` y `_RE_SUFIJO_PEGADO` (bloque 4 del script). |
| Windows: `UnicodeEncodeError` en la consola | Usa Windows Terminal o PowerShell 7; el archivo `epg.xml` siempre se escribe en UTF-8. |

## Licencia

MIT. Puedes usarlo, modificarlo y redistribuirlo libremente.
