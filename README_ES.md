# 🎮 Detector de Highlights de Videojuegos (FFmpeg + Python)

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-Required-green.svg)](https://ffmpeg.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![English Version](https://img.shields.io/badge/Language-English-red.svg)](README.md)

> [!NOTE]
> Para leer la documentación en inglés, consulta el archivo [README.md](README.md).

Herramienta de línea de comandos (CLI) de alto rendimiento en Python diseñada para procesar grabaciones extensas de videojuegos (partidas de 1 a 2 horas en formatos `.mp4`, `.mkv`, `.avi`, `.mov`, `.ts`, etc.) y detectar automáticamente momentos destacados (gritos, jugadas épicas, festejos y reacciones) mediante el análisis de picos de volumen de audio.

---

## ⚡ Características Principales

- **Velocidad Extrema (300x – 600x tiempo real):** Analiza el flujo de audio sin decodificar fotogramas de video (`-vn` en FFmpeg). Una grabación de 2 horas se analiza en cuestión de segundos.
- **Consumo de Memoria Constante $\mathcal{O}(1)$:** Transmite y procesa el flujo de FFmpeg línea por línea en tiempo real a través de `subprocess.Popen`. No satura la memoria RAM ni con archivos de 50+ GB o 4K/60fps.
- **Doble Motor de Medición:**
  - **`astats` (Predeterminado):** Medición de potencia RMS en dBFS exacto por segundo.
  - **`ebur128`:** Sonoridad perceptual humana normalizada en LUFS (estándar ITU-R BS.1770 / EBU R128).
- **Lógica Inteligente Anti-Spam (Cooldown):** Agrupa festejos prolongados en un único highlight rastreando el pico máximo y suprime falsos positivos recurrentes con un cooldown configurable.
- **Soporte para Pistas Separadas en OBS (`--audio-track`):** Permite aislar la pista del micrófono del jugador para evitar que explosiones del juego interfieran con la detección de reacciones vocales.
- **Recorte Automático sin Pérdida (`--cut-clips`):** Extrae los fragmentos destacados en segundos usando *stream-copy* (`-c copy`) de FFmpeg sin recodificar y sin pérdida de calidad.
- **Exportación Flexible:** Exporta resultados detallados en **JSON**, **TXT** (listas de tiempo para edición) y **CSV** (para hojas de cálculo).
- **Cero Dependencias Externas:** Funciona con la librería estándar de Python (`sys`, `subprocess`, `argparse`, `json`, `csv`, `pathlib`) y FFmpeg del sistema.

---

## 📋 Requisitos Previos

1. **Python 3.8 o superior**
2. **FFmpeg** instalado y disponible en las variables de entorno del sistema (`PATH`).

### Verificación de FFmpeg:
Abre una terminal y ejecuta:
```bash
ffmpeg -version
```
Si no lo tienes instalado:
- **Windows:** `winget install Gyan.FFmpeg` o descárgalo de [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
- **Linux (Ubuntu/Debian):** `sudo apt update && sudo apt install ffmpeg`
- **macOS:** `brew install ffmpeg`

---

## 🚀 Ejemplos de Uso

### 1. Detección básica (parámetros por defecto)
Analiza el video con umbral de `-18.0 dBFS`, cooldown de `15s` y buffer pre-jugada de `10s`:
```bash
python highlight_detector.py "partida_completa.mp4"
```

### 2. Grabación de OBS con micrófono en pista separada
Si en OBS configuraste el juego en la Pista 1 (índice 0) y tu micrófono en la Pista 2 (índice 1):
```bash
python highlight_detector.py "stream_valorant.mkv" --audio-track 1 --threshold -16.0
```

### 3. Ajuste fino de sensibilidad y buffers
Para un juego donde quieres 15 segundos previos al grito y 7 segundos de festejo posterior:
```bash
python highlight_detector.py "cs2_clutch.mp4" --threshold -14.0 --cooldown 20 --pre-buffer 15 --post-buffer 7
```

### 4. Detección y corte automático de clips (`--cut-clips`)
Extrae directamente los clips recortados en la carpeta configurada:
```bash
python highlight_detector.py "warzone.mp4" --cut-clips --output-dir "./mis_highlights"
```

### 5. Exportar reportes completos (JSON, TXT y CSV)
Genera tablas de timestamps listas para importar o documentar:
```bash
python highlight_detector.py "gameplay.mp4" --export-json --export-txt --export-csv
```

### 6. Uso del motor perceptual EBU R128 (LUFS)
```bash
python highlight_detector.py "gameplay.mp4" --method ebur128 --threshold -16.0
```

---

## 📊 Parámetros de la Línea de Comandos

| Argumento | Tipo | Por Defecto | Descripción |
| :--- | :---: | :---: | :--- |
| `video` | `str` | *Obligatorio* | Ruta al archivo de video (`.mp4`, `.mkv`, etc.). |
| `-t, --threshold` | `float` | `-18.0` | Umbral de volumen en decibelios (dBFS/LUFS). |
| `-c, --cooldown` | `float` | `15.0` | Segundos de enfriamiento anti-spam tras un evento. |
| `--pre-buffer` | `float` | `10.0` | Segundos previos a restar del pico (contexto de la jugada). |
| `--post-buffer` | `float` | `5.0` | Segundos posteriores a sumar tras el pico (reacción). |
| `-a, --audio-track`| `int` | `0` | Índice de la pista de audio a analizar (0 = primera pista). |
| `-m, --method` | `str` | `astats` | Motor de detección: `astats` (RMS dBFS) o `ebur128` (LUFS). |
| `--step` | `float` | `1.0` | Intervalo temporal de muestreo en segundos (`astats`). |
| `--cut-clips` | `flag` | `False` | Recorta automáticamente los fragmentos con FFmpeg (`-c copy`). |
| `-o, --output-dir`| `str` | `./highlights` | Carpeta destino para clips y reportes (configurable vía `.env`). |
| `--export-json` | `str?` | `None` | Exporta metadatos completos en formato JSON. |
| `--export-txt` | `str?` | `None` | Exporta marcas de tiempo en formato texto plano para edición. |
| `--export-csv` | `str?` | `None` | Exporta tabla con marcas de tiempo en formato CSV. |
| `--no-progress` | `flag` | `False` | Desactiva la barra de progreso en vivo. |

---

## 🧪 Pruebas Automatizadas

El proyecto incluye una suite de pruebas completa en `test_detector.py` que genera videos sintéticos con ondas senoidales moduladas matemáticamente (`aevalsrc`) para verificar:
1. Precisión de detección de picos en dBFS.
2. Comprobación del cooldown anti-spam.
3. Extracción de fragmentos de video y verificación en disco.
4. Exportación a JSON, TXT y CSV.
5. Funcionamiento del motor perceptual `ebur128`.
6. Selección de pista de audio en archivos con múltiples canales/streams.

Para ejecutar las pruebas:
```bash
python test_detector.py
```

---

## 💡 Consejos de Calibración para Creadores de Contenido

- **Volumen habitual de voz:** Una locución normal suele oscilar entre `-24 dBFS` y `-20 dBFS`.
- **Gritos y reacciones intensas:** Suelen alcanzar entre `-16 dBFS` y `-8 dBFS`.
- **Recomendación para shooters / acción:** Comienza con `-16.0 dBFS` o `-18.0 dBFS`. Si el script detecta demasiadas jugadas menores o disparos fuertes del juego, sube el umbral hacia `-14.0 dBFS` o utiliza la pista separada del micrófono (`--audio-track 1`).
