#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script de pruebas automatizadas para el Detector de Highlights.
Genera un video sintético con picos de audio controlados y valida:
- Detección precisa de picos
- Lógica de cooldown anti-spam
- Cálculo de buffers (pre y post)
- Exportación a JSON, TXT y CSV
- Extracción de clips con stream copy
- Modos 'astats' y 'ebur128'
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def create_synthetic_test_video(output_path: Path) -> None:
    """
    Crea un video de prueba de 40 segundos con audio sintetizado:
    - 0s a 10s: Audio tenue (~ -35 dBFS)
    - 10s a 14s: PICO FUERTE 1 (~ -8 dBFS) -> Primer highlight (esperado en ~10-12s)
    - 15s a 18s: Ruido dentro de la ventana de cooldown de 15s -> Debe ser IGNORADO por anti-spam
    - 19s a 29s: Audio tenue
    - 30s a 33s: PICO FUERTE 2 (~ -6 dBFS) -> Segundo highlight (esperado en ~30-31s)
    - 34s a 40s: Silencio/tenue
    """
    print(f"[*] Generando video sintético de prueba en: {output_path}")

    # Generar señal de audio con picos claros:
    # 0-10s: -43 dBFS (ruido de fondo)
    # 10-14s: -4.9 dBFS (PICO 1 - Highlight 1)
    # 15-18s: -6.0 dBFS (Dentro de cooldown de 15s de Pico 1 -> Debe ignorarse)
    # 19-29s: -43 dBFS (ruido de fondo)
    # 30-33s: -3.9 dBFS (PICO 2 - Highlight 2)
    # 34-40s: -43 dBFS (ruido de fondo)
    audio_expr = (
        r"if(between(t\, 10\, 14)\, 0.8*sin(2*PI*440*t)\, "
        r"if(between(t\, 15\, 18)\, 0.7*sin(2*PI*440*t)\, "
        r"if(between(t\, 30\, 33)\, 0.9*sin(2*PI*440*t)\, "
        r"0.01*sin(2*PI*440*t))))"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostdin",
        "-f", "lavfi",
        "-i", "testsrc=duration=40:size=320x240:rate=25",
        "-f", "lavfi",
        "-i", f"aevalsrc={audio_expr}:duration=40",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-b:a", "128k",
        str(output_path),
    ]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    print("[+] Video sintético generado exitosamente.")


def run_tests() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_highlights_"))
    try:
        test_video = temp_dir / "gameplay_test.mp4"
        out_highlights_dir = temp_dir / "output_clips"
        out_highlights_dir.mkdir(parents=True, exist_ok=True)

        create_synthetic_test_video(test_video)

        script_path = Path(__file__).parent / "highlight_detector.py"

        # TEST 1: Detección con astats + corte de clips + exportadores
        print("\n--- TEST 1: Motor 'astats' (RMS en dBFS) + Exportación + Clips ---")
        json_path = temp_dir / "report.json"
        txt_path = temp_dir / "report.txt"
        csv_path = temp_dir / "report.csv"

        cmd1 = [
            sys.executable,
            str(script_path),
            str(test_video),
            "--threshold", "-18.0",
            "--cooldown", "15.0",
            "--pre-buffer", "5.0",
            "--post-buffer", "5.0",
            "--method", "astats",
            "--cut-clips",
            "--output-dir", str(out_highlights_dir),
            "--export-json", str(json_path),
            "--export-txt", str(txt_path),
            "--export-csv", str(csv_path),
            "--no-progress",
        ]

        res1 = subprocess.run(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        print(res1.stdout)
        if res1.returncode != 0:
            print(res1.stderr, file=sys.stderr)
            raise AssertionError(f"El test 1 falló con código {res1.returncode}")

        # Validar JSON
        assert json_path.exists(), "El archivo JSON no fue creado."
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        highlights = data.get("highlights", [])
        print(f"[TEST 1] Highlights detectados: {len(highlights)}")
        assert len(highlights) == 2, f"Se esperaban exactamente 2 highlights (cooldown debió filtrar el pico intermedio), pero se obtuvieron {len(highlights)}"

        # Verificar que el pico 1 esté cerca de 10-12s y el pico 2 cerca de 30-31s
        hl1, hl2 = highlights[0], highlights[1]
        print(f"Highlight 1: Pico={hl1['peak_time_sec']}s ({hl1['peak_db']} dBFS), Inicio={hl1['start_time_sec']}s, Fin={hl1['end_time_sec']}s")
        print(f"Highlight 2: Pico={hl2['peak_time_sec']}s ({hl2['peak_db']} dBFS), Inicio={hl2['start_time_sec']}s, Fin={hl2['end_time_sec']}s")

        assert 9.0 <= hl1["peak_time_sec"] <= 14.0, f"Pico 1 fuera de rango: {hl1['peak_time_sec']}"
        assert 29.0 <= hl2["peak_time_sec"] <= 34.0, f"Pico 2 fuera de rango: {hl2['peak_time_sec']}"

        # Verificar buffer pre (5s) y post (5s)
        assert abs((hl1["peak_time_sec"] - 5.0) - hl1["start_time_sec"]) < 0.1, "Error en cálculo de start_time para hl1"
        assert abs((hl1["peak_time_sec"] + 5.0) - hl1["end_time_sec"]) < 0.1, "Error en cálculo de end_time para hl1"

        # Verificar TXT y CSV
        assert txt_path.exists() and txt_path.stat().st_size > 0, "Archivo TXT vacío o inexistente"
        assert csv_path.exists() and csv_path.stat().st_size > 0, "Archivo CSV vacío o inexistente"

        # Verificar clips generados
        generated_clips = list(out_highlights_dir.glob("*.mp4"))
        print(f"[TEST 1] Clips generados en disco: {[c.name for c in generated_clips]}")
        assert len(generated_clips) == 2, f"Se esperaban 2 clips MP4 recortados, se encontraron {len(generated_clips)}"

        # TEST 2: Motor 'ebur128' (LUFS)
        print("\n--- TEST 2: Motor 'ebur128' (Sonoridad ITU-R BS.1770) ---")
        cmd2 = [
            sys.executable,
            str(script_path),
            str(test_video),
            "--threshold", "-18.0",
            "--cooldown", "15.0",
            "--method", "ebur128",
            "--no-progress",
        ]
        res2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        print(res2.stdout)
        if res2.returncode != 0:
            print(res2.stderr, file=sys.stderr)
            raise AssertionError(f"El test 2 falló con código {res2.returncode}")

        assert "Total de highlights detectados: 2" in res2.stdout, "ebur128 no detectó los 2 highlights esperados"

        # TEST 3: Pistas de audio múltiples (--audio-track)
        print("\n--- TEST 3: Pistas de audio múltiples (OBS / Multi-track) ---")
        multitrack_video = temp_dir / "multitrack_gameplay.mkv"
        cmd_mt = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-nostdin",
            "-f", "lavfi", "-i", "testsrc=duration=15:size=320x240:rate=25",
            "-f", "lavfi", "-i", r"aevalsrc=0.01*sin(2*PI*440*t):duration=15",
            "-f", "lavfi", "-i", r"aevalsrc=if(between(t\, 5\, 8)\, 0.8*sin(2*PI*440*t)\, 0.01*sin(2*PI*440*t)):duration=15",
            "-map", "0:v",
            "-map", "1:a",  # Track 0: Solo juego (silencioso)
            "-map", "2:a",  # Track 1: Micrófono (grito en t=5s)
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(multitrack_video),
        ]
        subprocess.run(cmd_mt, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)

        # Analizar Pista 0 (audio del juego) -> Debe dar 0 highlights
        cmd_track0 = [
            sys.executable, str(script_path), str(multitrack_video),
            "--audio-track", "0", "--threshold", "-18.0", "--no-progress"
        ]
        res_t0 = subprocess.run(cmd_track0, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        assert "Total de highlights detectados: 0" in res_t0.stdout or "No se detectaron picos" in res_t0.stdout, "La pista 0 debió tener 0 picos"

        # Analizar Pista 1 (micrófono) -> Debe detectar el pico en t=5s
        cmd_track1 = [
            sys.executable, str(script_path), str(multitrack_video),
            "--audio-track", "1", "--threshold", "-18.0", "--no-progress"
        ]
        res_t1 = subprocess.run(cmd_track1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        assert "Total de highlights detectados: 1" in res_t1.stdout, "La pista 1 debió detectar 1 highlight del micrófono"
        print("[+] Prueba de pistas de audio múltiples superada con éxito!")

        print("\n=============================================")
        print("  TODAS LAS PRUEBAS PASARON EXITOSAMENTE! (100%)")
        print("=============================================\n")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_tests()
