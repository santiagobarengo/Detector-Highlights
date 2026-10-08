#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
=============================================================================
Detector de Highlights para Grabaciones de Videojuegos
=============================================================================
Herramienta CLI de alto rendimiento para procesar videos largos (MP4, MKV, etc.)
y detectar jugadas destacadas basadas en picos de volumen de audio (gritos,
reacciones, festejos).

Características principales:
- Análisis ultrarrápido sin decodificar video (-vn en FFmpeg).
- Consumo mínimo de memoria (O(1) RAM) mediante streaming línea por línea de stderr.
- Detección precisa de picos RMS (dBFS) con filtro 'astats' o sonoridad con 'ebur128'.
- Sistema inteligente anti-spam con cooldown y ventana de agregación.
- Soporte para selección de pista de audio (ej. aislar micrófono en OBS).
- Cálculo automático de timestamps de corte (pre-buffer y post-buffer).
- Tabla limpia en consola y exportación a JSON, TXT y CSV.
- Extracción opcional de clips con FFmpeg (-c copy) sin recodificar.
- 100% Python Standard Library (sin dependencias externas tipo pip).
=============================================================================
"""

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


# =============================================================================
# Modelos de Datos
# =============================================================================

@dataclass
class Highlight:
    """Representa un momento destacado detectado."""
    id: int
    peak_time: float         # Segundo exacto del pico de volumen
    peak_db: float           # Nivel máximo alcanzado en dBFS o LUFS
    start_time: float        # Inicio sugerido de corte (peak - pre_buffer)
    end_time: float          # Fin sugerido de corte (peak + post_buffer)
    duration: float          # Duración total del fragmento sugerido
    clip_path: Optional[str] = None  # Ruta al archivo recortado si se generó

    @property
    def peak_time_str(self) -> str:
        return format_timestamp(self.peak_time)

    @property
    def start_time_str(self) -> str:
        return format_timestamp(self.start_time)

    @property
    def end_time_str(self) -> str:
        return format_timestamp(self.end_time)

    def to_dict(self) -> Dict[str, Any]:
        """Convierte la instancia a un diccionario serializable."""
        return {
            "id": self.id,
            "peak_time_sec": round(self.peak_time, 2),
            "peak_time_str": self.peak_time_str,
            "peak_db": round(self.peak_db, 2),
            "start_time_sec": round(self.start_time, 2),
            "start_time_str": self.start_time_str,
            "end_time_sec": round(self.end_time, 2),
            "end_time_str": self.end_time_str,
            "duration_sec": round(self.duration, 2),
            "clip_path": self.clip_path,
        }


# =============================================================================
# Utilidades de Formato y Verificación de Entorno
# =============================================================================

def format_timestamp(seconds: float, include_millis: bool = False) -> str:
    """
    Convierte una cantidad de segundos a formato HH:MM:SS o HH:MM:SS.mmm.
    """
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if include_millis:
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def check_ffmpeg_dependencies() -> Tuple[bool, bool]:
    """
    Verifica si FFmpeg y FFprobe se encuentran instalados y disponibles en el PATH.
    Retorna una tupla (ffmpeg_disponible, ffprobe_disponible).
    """
    has_ffmpeg = shutil.which("ffmpeg") is not None
    has_ffprobe = shutil.which("ffprobe") is not None
    return has_ffmpeg, has_ffprobe


def get_video_metadata(video_path: Path) -> Dict[str, Any]:
    """
    Obtiene la duración total y las pistas de audio disponibles usando ffprobe.
    Si ffprobe no está disponible o falla, retorna valores por defecto seguros.
    """
    info: Dict[str, Any] = {
        "duration": None,
        "audio_tracks_count": 0,
        "audio_streams": [],
    }

    if not shutil.which("ffprobe"):
        return info

    try:
        # Consultar duración del contenedor
        cmd_duration = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        res = subprocess.run(cmd_duration, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        raw_dur = res.stdout.strip()
        if raw_dur:
            info["duration"] = float(raw_dur)
    except Exception:
        pass

    try:
        # Consultar pistas de audio
        cmd_streams = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index,codec_name,channels",
            "-of", "json",
            str(video_path),
        ]
        res_streams = subprocess.run(cmd_streams, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res_streams.stdout)
        streams = data.get("streams", [])
        info["audio_tracks_count"] = len(streams)
        info["audio_streams"] = streams
    except Exception:
        pass

    return info


# =============================================================================
# Motor de Detección de Highlights
# =============================================================================

class HighlightDetector:
    """
    Escanea el audio de un archivo multimedia usando FFmpeg sin decodificar video,
    analizando línea por línea el flujo de salida con mínimo consumo de RAM.
    """

    def __init__(
        self,
        video_path: Path,
        threshold_db: float = -18.0,
        cooldown: float = 15.0,
        pre_buffer: float = 10.0,
        post_buffer: float = 5.0,
        audio_track: int = 0,
        method: str = "astats",
        step_seconds: float = 1.0,
        show_progress: bool = True,
    ):
        self.video_path = video_path.resolve()
        self.threshold_db = threshold_db
        self.cooldown = cooldown
        self.pre_buffer = pre_buffer
        self.post_buffer = post_buffer
        self.audio_track = audio_track
        self.method = method.lower()
        self.step_seconds = step_seconds
        self.show_progress = show_progress

        if not self.video_path.exists():
            raise FileNotFoundError(f"El archivo de video no existe: {self.video_path}")

        # Metadatos del video
        self.metadata = get_video_metadata(self.video_path)
        self.total_duration = self.metadata.get("duration")

    def _build_ffmpeg_command(self) -> List[str]:
        """
        Construye el comando FFmpeg optimizado según el método seleccionado.
        """
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-vn",                       # Desactiva completamente la decodificación de video
            "-i", str(self.video_path),
            "-map", f"0:a:{self.audio_track}",  # Selecciona la pista de audio específica
        ]

        if self.method == "ebur128":
            # Sonoridad perceptual momentánea (M) según estándar ITU-R BS.1770 / EBU R128
            # Emite metadatos en lavfi.r128.M
            audio_filter = "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M"
        else:
            # astats: Potencia RMS en dBFS exacta por ventana de tiempo
            # Muestreamos a 16kHz y agrupamos muestras según step_seconds para velocidad óptima
            sample_rate = 16000
            num_samples = max(1600, int(sample_rate * self.step_seconds))
            audio_filter = (
                f"aresample={sample_rate},"
                f"asetnsamples=n={num_samples},"
                f"astats=metadata=1:reset=1,"
                f"ametadata=print:key=lavfi.astats.Overall.RMS_level"
            )

        cmd.extend(["-af", audio_filter, "-f", "null", "-"])
        return cmd

    def detect(self) -> List[Highlight]:
        """
        Ejecuta el escaneo de audio transmitiendo stderr línea por línea y
        aplica la lógica de cooldown y detección de picos.
        """
        cmd = self._build_ffmpeg_command()

        # Iniciar subproceso FFmpeg con buffer de línea
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        highlights: List[Highlight] = []
        next_highlight_id = 1

        # Expresiones regulares para capturar tiempo y nivel
        re_time = re.compile(r"pts_time:([0-9.]+)")
        if self.method == "ebur128":
            re_vol = re.compile(r"lavfi\.r128\.M=(-?[0-9.]+|-[iI]nf)")
        else:
            re_vol = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[0-9.]+|-[iI]nf)")

        current_time: Optional[float] = None
        last_highlight_peak_time: float = -999999.0

        # Estado del evento de pico activo (scream / hype actual)
        active_event: Optional[Dict[str, float]] = None

        def finalize_active_event(event: Dict[str, float]) -> Optional[Highlight]:
            """Cierra un evento de pico y calcula sus marcas de corte."""
            nonlocal next_highlight_id, last_highlight_peak_time

            peak_t = event["peak_time"]
            peak_db = event["peak_db"]

            # Comprobar si respeta el cooldown del evento anterior
            if peak_t < last_highlight_peak_time + self.cooldown:
                return None

            # Calcular timestamps con buffers de contexto
            start_t = max(0.0, peak_t - self.pre_buffer)
            end_t = peak_t + self.post_buffer
            if self.total_duration is not None and end_t > self.total_duration:
                end_t = self.total_duration

            dur = max(0.0, end_t - start_t)

            hl = Highlight(
                id=next_highlight_id,
                peak_time=peak_t,
                peak_db=peak_db,
                start_time=start_t,
                end_time=end_t,
                duration=dur,
            )
            next_highlight_id += 1
            last_highlight_peak_time = peak_t
            return hl

        start_wall_time = time.time()
        last_progress_render = 0.0

        try:
            assert process.stderr is not None
            for line in process.stderr:
                # 1. Buscar tiempo de presentación (pts_time)
                m_time = re_time.search(line)
                if m_time:
                    try:
                        current_time = float(m_time.group(1))
                    except ValueError:
                        pass
                    continue

                # 2. Buscar nivel de volumen
                m_vol = re_vol.search(line)
                if m_vol and current_time is not None:
                    raw_val = m_vol.group(1).lower()
                    volume_db = -120.0 if "inf" in raw_val else float(raw_val)

                    # --- Lógica de Detección y Cooldown ---
                    if volume_db >= self.threshold_db:
                        # Si el tiempo actual aún está dentro del cooldown de un highlight ya registrado,
                        # descartamos nuevos picos para evitar spam del mismo momento.
                        if current_time >= last_highlight_peak_time + self.cooldown:
                            if active_event is None:
                                # Inicia un nuevo evento de pico
                                active_event = {
                                    "start_trigger": current_time,
                                    "peak_time": current_time,
                                    "peak_db": volume_db,
                                    "last_above_threshold": current_time,
                                }
                            else:
                                # Evento ya en curso: actualizar si el volumen actual es aún mayor
                                if volume_db > active_event["peak_db"]:
                                    active_event["peak_db"] = volume_db
                                    active_event["peak_time"] = current_time
                                active_event["last_above_threshold"] = current_time

                                # Si el evento continuo excede una duración razonable (ej. cooldown),
                                # finalizarlo preventivamente para no postergarlo indefinidamente.
                                if (current_time - active_event["start_trigger"]) >= self.cooldown:
                                    hl = finalize_active_event(active_event)
                                    if hl:
                                        highlights.append(hl)
                                    active_event = None
                    else:
                        # El volumen cayó por debajo del umbral
                        if active_event is not None:
                            # Si pasó al menos 1 segundo desde la última vez que estuvo sobre el umbral,
                            # consideramos que la jugada/grito concluyó.
                            if (current_time - active_event["last_above_threshold"]) >= 1.0:
                                hl = finalize_active_event(active_event)
                                if hl:
                                    highlights.append(hl)
                                active_event = None

                    # Actualizar barra de progreso en terminal
                    if self.show_progress:
                        now = time.time()
                        if now - last_progress_render >= 0.2:
                            self._render_progress(current_time, len(highlights), start_wall_time)
                            last_progress_render = now

            # Si quedó un evento abierto al finalizar el video, cerrarlo
            if active_event is not None:
                hl = finalize_active_event(active_event)
                if hl:
                    highlights.append(hl)

            process.wait()
            if process.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg finalizó con código de error {process.returncode}. "
                    "Verifica que la pista de audio seleccionada exista en el archivo."
                )

        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            print("\n\n[!] Análisis cancelado por el usuario (Ctrl+C).", file=sys.stderr)
            sys.exit(130)
        finally:
            if self.show_progress:
                # Limpiar la línea de progreso al terminar
                sys.stdout.write("\r" + " " * 80 + "\r")
                sys.stdout.flush()

        return highlights

    def _render_progress(self, current_sec: float, highlights_count: int, start_time: float) -> None:
        """Imprime una barra de progreso limpia en una sola línea."""
        elapsed = time.time() - start_time
        speed_factor = (current_sec / elapsed) if elapsed > 0 else 0.0

        cur_str = format_timestamp(current_sec)

        if self.total_duration and self.total_duration > 0:
            b = get_box_drawing_chars(sys.stdout)
            pct = min(100.0, (current_sec / self.total_duration) * 100.0)
            tot_str = format_timestamp(self.total_duration)
            bar_len = 25
            filled = int(bar_len * (pct / 100.0))
            bar = b["bar_fill"] * filled + b["bar_empty"] * (bar_len - filled)
            msg = (
                f"\rProgreso: [{bar}] {pct:5.1f}% | {cur_str} / {tot_str} | "
                f"Velocidad: {speed_factor:4.0f}x | Highlights: {highlights_count}"
            )
        else:
            msg = (
                f"\rAnalizando: {cur_str} | "
                f"Velocidad: {speed_factor:4.0f}x | Highlights: {highlights_count}"
            )

        sys.stdout.write(msg)
        sys.stdout.flush()


# =============================================================================
# Cortador de Clips (Stream Copy / Sin Pérdida de Rendimiento)
# =============================================================================

class ClipCutter:
    """Extrae los fragmentos destacados con FFmpeg en modo stream-copy (-c copy)."""

    def __init__(self, video_path: Path, output_dir: Path):
        self.video_path = video_path.resolve()
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def cut_highlight(self, highlight: Highlight, index_padding: int = 3) -> Optional[Path]:
        """
        Corta un fragmento individual utilizando stream copy ultrarrápido.
        """
        safe_name = self.video_path.stem
        start_fmt = highlight.start_time_str.replace(":", "-")
        end_fmt = highlight.end_time_str.replace(":", "-")
        filename = f"{safe_name}_highlight_{highlight.id:0{index_padding}d}_{start_fmt}_a_{end_fmt}{self.video_path.suffix}"
        clip_path = self.output_dir / filename

        # -ss antes de -i realiza un seek rápido por fotograma clave (keyframe)
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-nostdin",
            "-ss", f"{highlight.start_time:.3f}",
            "-i", str(self.video_path),
            "-t", f"{highlight.duration:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(clip_path),
        ]

        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            highlight.clip_path = str(clip_path)
            return clip_path
        except subprocess.CalledProcessError as e:
            print(f"\n[!] Error recortando highlight #{highlight.id}: {e.stderr.decode(errors='replace')}", file=sys.stderr)
            return None


# =============================================================================
# Renderizado de Tabla en Consola y Exportadores
# =============================================================================

def get_box_drawing_chars(stream=sys.stdout) -> Dict[str, str]:
    """
    Retorna los caracteres para bordes de tablas (Unicode si el terminal lo soporta,
    o ASCII estándar si el encoding del sistema es limitado como cp1252).
    """
    encoding = getattr(stream, "encoding", "utf-8") or "utf-8"
    unicode_chars = {
        "tl": "┌", "tm": "┬", "tr": "┐",
        "ml": "├", "mm": "┼", "mr": "┤",
        "bl": "└", "bm": "┴", "br": "┘",
        "h": "─", "v": "│",
        "bar_fill": "█", "bar_empty": "░",
    }
    ascii_chars = {
        "tl": "+", "tm": "+", "tr": "+",
        "ml": "+", "mm": "+", "mr": "+",
        "bl": "+", "bm": "+", "br": "+",
        "h": "-", "v": "|",
        "bar_fill": "#", "bar_empty": "-",
    }

    test_str = "".join(unicode_chars.values())
    try:
        test_str.encode(encoding)
        return unicode_chars
    except (UnicodeEncodeError, LookupError):
        return ascii_chars


def print_highlights_table(highlights: List[Highlight], unit: str = "dBFS") -> None:
    """
    Imprime una tabla elegante y estructurada en la terminal con los resultados.
    """
    if not highlights:
        print("\n-------------------------------------------------------------")
        print(" [i] No se detectaron picos de audio con el umbral especificado.")
        print("-------------------------------------------------------------\n")
        return

    b = get_box_drawing_chars(sys.stdout)

    # Definición de cabeceras y anchos de columna
    headers = ["#", "Pico (HH:MM:SS)", f"Nivel ({unit})", "Inicio Corte", "Fin Corte", "Duración"]
    widths = [4, 17, 14, 14, 14, 10]

    border_top = b["tl"] + b["tm"].join(b["h"] * (w + 2) for w in widths) + b["tr"]
    border_mid = b["ml"] + b["mm"].join(b["h"] * (w + 2) for w in widths) + b["mr"]
    border_bot = b["bl"] + b["bm"].join(b["h"] * (w + 2) for w in widths) + b["br"]

    v = b["v"]
    header_row = v + v.join(f" {h:^{widths[i]}} " for i, h in enumerate(headers)) + v

    print("\n" + border_top)
    print(header_row)
    print(border_mid)

    for hl in highlights:
        cols = [
            f"{hl.id}",
            hl.peak_time_str,
            f"{hl.peak_db:.1f}",
            hl.start_time_str,
            hl.end_time_str,
            f"{hl.duration:.1f}s",
        ]
        row_str = v + v.join(f" {cols[i]:^{widths[i]}} " for i in range(len(cols))) + v
        print(row_str)

    print(border_bot)
    print(f"Total de highlights detectados: {len(highlights)}\n")


def export_to_json(highlights: List[Highlight], output_file: Path, video_path: Path, params: Dict[str, Any]) -> None:
    """Guarda los resultados detallados en formato JSON."""
    data = {
        "source_video": str(video_path),
        "parameters": params,
        "total_highlights": len(highlights),
        "highlights": [hl.to_dict() for hl in highlights],
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[+] Reporte JSON exportado en: {output_file}")


def export_to_txt(highlights: List[Highlight], output_file: Path, video_path: Path) -> None:
    """Guarda una lista limpia de marcas de tiempo en formato texto plano para editores de video."""
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# Timestamps de Highlights - {video_path.name}\n")
        f.write(f"# Formato: [ID] Inicio -> Fin | Pico (Nivel)\n")
        f.write("# " + "=" * 60 + "\n\n")
        for hl in highlights:
            f.write(
                f"[{hl.id:02d}] {hl.start_time_str} -> {hl.end_time_str} | "
                f"Pico: {hl.peak_time_str} ({hl.peak_db:.1f} dBFS, Dur: {hl.duration:.1f}s)\n"
            )
    print(f"[+] Lista TXT exportada en: {output_file}")


def export_to_csv(highlights: List[Highlight], output_file: Path) -> None:
    """Guarda los resultados en formato CSV delimitado por comas."""
    fieldnames = [
        "id",
        "peak_time_str",
        "peak_time_sec",
        "peak_db",
        "start_time_str",
        "start_time_sec",
        "end_time_str",
        "end_time_sec",
        "duration_sec",
        "clip_path",
    ]
    with open(output_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for hl in highlights:
            writer.writerow(hl.to_dict())
    print(f"[+] Tabla CSV exportada en: {output_file}")


# =============================================================================
# CLI / Punto de Entrada
# =============================================================================

def build_argument_parser() -> argparse.ArgumentParser:
    """Define los argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Detector automático de momentos destacados (highlights) en grabaciones de videojuegos mediante picos de volumen con FFmpeg.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Argumentos obligatorios
    parser.add_argument(
        "video",
        type=str,
        help="Ruta al archivo de video (ej. MP4, MKV, AVI, TS).",
    )

    # Parámetros de detección de volumen
    parser.add_argument(
        "-t", "--threshold",
        type=float,
        default=-18.0,
        help="Umbral de volumen en decibelios (dBFS/LUFS). Valores comunes: -18.0 a -12.0.",
    )
    parser.add_argument(
        "-c", "--cooldown",
        type=float,
        default=15.0,
        help="Tiempo de cooldown en segundos para evitar registrar múltiples eventos del mismo festejo/jugada.",
    )
    parser.add_argument(
        "--pre-buffer",
        type=float,
        default=10.0,
        help="Segundos previos a restar del pico para incluir la jugada o contexto anterior.",
    )
    parser.add_argument(
        "--post-buffer",
        type=float,
        default=5.0,
        help="Segundos posteriores al pico para capturar la reacción completa.",
    )
    parser.add_argument(
        "-a", "--audio-track",
        type=int,
        default=0,
        help="Índice de la pista de audio a analizar (ej. 0 para la principal, 1 para micrófono si se grabó en pista separada en OBS).",
    )
    parser.add_argument(
        "-m", "--method",
        choices=["astats", "ebur128"],
        default="astats",
        help="Método de análisis: 'astats' (RMS en dBFS exacto) o 'ebur128' (sonoridad perceptual ITU-R BS.1770 / LUFS).",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=1.0,
        help="Paso temporal de muestreo en segundos para el filtro 'astats' (ej. 1.0 para 1 segundo, 0.5 para mayor resolución).",
    )

    # Opciones de corte y exportación
    parser.add_argument(
        "--cut-clips",
        action="store_true",
        help="Recorta y guarda automáticamente los fragmentos de video con FFmpeg (-c copy) sin recodificar.",
    )
    # Soporta variable de entorno HIGHLIGHTS_OUTPUT_DIR o archivo .env local
    env_dir = os.environ.get("HIGHLIGHTS_OUTPUT_DIR")
    if not env_dir:
        env_file = Path(__file__).parent / ".env"
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == "HIGHLIGHTS_OUTPUT_DIR":
                            env_dir = v.strip().strip('"').strip("'")
                            break
            except Exception:
                pass

    default_clips_dir = env_dir if env_dir else "./highlights"
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        default=default_clips_dir,
        help="Carpeta destino donde guardar los clips de video y reportes exportados.",
    )
    parser.add_argument(
        "--export-json",
        type=str,
        nargs="?",
        const="",
        help="Ruta opcional para exportar reporte en JSON (o deja vacío para guardarlo automáticamente en el directorio de salida).",
    )
    parser.add_argument(
        "--export-txt",
        type=str,
        nargs="?",
        const="",
        help="Ruta opcional para exportar marcas de tiempo en formato TXT legible.",
    )
    parser.add_argument(
        "--export-csv",
        type=str,
        nargs="?",
        const="",
        help="Ruta opcional para exportar tabla en formato CSV.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Desactiva la barra de progreso en vivo en terminal (útil para scripts desatendidos o logs CI).",
    )

    return parser


def main() -> None:
    # 0. Habilitar soporte UTF-8 en consola de Windows si es posible
    if sys.platform == "win32":
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # 1. Validar presencia de FFmpeg en el sistema
    has_ffmpeg, has_ffprobe = check_ffmpeg_dependencies()
    if not has_ffmpeg:
        print(
            "\n[ERROR CRÍTICO] 'ffmpeg' no está instalado o no se encuentra en el PATH del sistema.\n"
            "Por favor, descarga e instala FFmpeg:\n"
            "  - Windows: winget install Gyan.FFmpeg  o  choco install ffmpeg\n"
            "  - Linux: sudo apt update && sudo apt install ffmpeg\n"
            "  - macOS: brew install ffmpeg\n",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Parsear argumentos
    parser = build_argument_parser()
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.is_file():
        # Intentar autodetectar extensión común si el usuario la omitió en terminal
        found = False
        for ext in [".mp4", ".mkv", ".mov", ".avi", ".ts", ".webm"]:
            candidate = video_path.with_suffix(ext)
            if candidate.is_file():
                video_path = candidate
                found = True
                break
        if not found:
            print(f"\n[ERROR] El archivo de video especificado no existe: {video_path}\n", file=sys.stderr)
            sys.exit(1)

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 65)
    print(" DETECTOR DE HIGHLIGHTS DE AUDIO (FFMPEG)")
    print("=" * 65)
    print(f" Archivo a analizar : {video_path.name}")
    print(f" Pista de audio     : Pista {args.audio_track}")
    print(f" Motor de detección : {args.method.upper()} ({'dBFS' if args.method == 'astats' else 'LUFS'})")
    print(f" Umbral de volumen  : {args.threshold} dB")
    print(f" Cooldown anti-spam : {args.cooldown}s")
    print(f" Buffer de corte    : Pre {args.pre_buffer}s / Post {args.post_buffer}s")
    print("=" * 65 + "\n")

    # 3. Ejecutar análisis
    detector = HighlightDetector(
        video_path=video_path,
        threshold_db=args.threshold,
        cooldown=args.cooldown,
        pre_buffer=args.pre_buffer,
        post_buffer=args.post_buffer,
        audio_track=args.audio_track,
        method=args.method,
        step_seconds=args.step,
        show_progress=not args.no_progress,
    )

    try:
        highlights = detector.detect()
    except Exception as e:
        print(f"\n[ERROR] Falló el análisis de audio: {e}\n", file=sys.stderr)
        sys.exit(1)

    # 4. Mostrar tabla de resultados en consola
    unit = "dBFS" if args.method == "astats" else "LUFS"
    print_highlights_table(highlights, unit=unit)

    # 5. Cortar fragmentos de video si se solicitó
    if args.cut_clips and highlights:
        print(f"[*] Recortando {len(highlights)} clips con FFmpeg (-c copy) en: {out_dir}")
        cutter = ClipCutter(video_path, out_dir)
        for hl in highlights:
            clip_res = cutter.cut_highlight(hl)
            if clip_res:
                print(f"  -> Clip #{hl.id:02d} guardado: {clip_res.name}")
        print("[+] Recorte de clips completado con éxito.\n")

    # 6. Exportar reportes solicitados
    video_stem = video_path.stem
    cli_params = {
        "threshold": args.threshold,
        "cooldown": args.cooldown,
        "pre_buffer": args.pre_buffer,
        "post_buffer": args.post_buffer,
        "audio_track": args.audio_track,
        "method": args.method,
    }

    if args.export_json is not None:
        target = Path(args.export_json) if args.export_json else out_dir / f"{video_stem}_highlights.json"
        export_to_json(highlights, target, video_path, cli_params)

    if args.export_txt is not None:
        target = Path(args.export_txt) if args.export_txt else out_dir / f"{video_stem}_highlights.txt"
        export_to_txt(highlights, target, video_path)

    if args.export_csv is not None:
        target = Path(args.export_csv) if args.export_csv else out_dir / f"{video_stem}_highlights.csv"
        export_to_csv(highlights, target)


if __name__ == "__main__":
    main()
