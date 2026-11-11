from pathlib import Path
import shutil
import subprocess
from typing import Callable

from func1 import SUBPROCESS_FLAGS
from func2 import get_ffmpeg_exe, load_raiw_engine, run_raiw_internal
from func3 import (
    _is_valid_media_file,
    has_audio_stream,
    probe_video_info,
)


def _compute_safe_trim_window(
    total_dur: float,
    cut_start: float,
    cut_end: float,
    video_label: str = "Video",
    log_callback: Callable[[str], None] | None = None,
) -> tuple[float, float, float, bool]:
    cs = max(0.0, float(cut_start))
    ce = max(0.0, float(cut_end))
    if cs <= 1e-4 and ce <= 1e-4:
        return 0.0, total_dur, total_dur, False

    min_keep = 0.20
    max_total_cut = max(0.0, total_dur - min_keep)

    if (cs + ce) > max_total_cut:
        if log_callback:
            log_callback(
                f"   ⚠️ [PERINGATAN CUT] Total potong {video_label} ({cs:g}s + {ce:g}s) "
                f"melebihi durasi video ({total_dur:.2f}s). Disesuaikan otomatis agar sisa 0.20s."
            )
        if cs >= max_total_cut:
            cs = max_total_cut
            ce = 0.0
        else:
            ce = max(0.0, max_total_cut - cs)

    start_sec = round(cs, 4)
    end_sec = round(max(start_sec + min_keep, total_dur - ce), 4)
    eff_dur = round(end_sec - start_sec, 4)
    is_trimmed = (start_sec > 1e-4) or (ce > 1e-4)
    return start_sec, end_sec, eff_dur, is_trimmed


def clean_overlay_image(image_path: Path, output_path: Path) -> Path:
    ffmpeg_bin = get_ffmpeg_exe()
    try:
        cmd = [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(image_path),
            "-map", "0:v:0",
            "-map_metadata", "-1",
            "-fflags", "+bitexact",
            "-flags:v", "+bitexact",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, creationflags=SUBPROCESS_FLAGS)
        if _is_valid_media_file(output_path, min_bytes=64):
            return output_path
    except Exception:
        pass
    return image_path


def _ffmpeg_fallback_pre_clean(src_video: Path, out_video: Path) -> Path:
    ffmpeg_bin = get_ffmpeg_exe()
    try:
        cmd = [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src_video),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-metadata", "encoder=",
            "-metadata", "creation_time=",
            "-metadata:s:v:0", "encoder=",
            "-metadata:s:v:0", "handler_name= ",
            "-metadata:s:a:0", "encoder=",
            "-metadata:s:a:0", "handler_name= ",
            "-fflags", "+bitexact",
            "-flags:v", "+bitexact",
            "-flags:a", "+bitexact",
            "-brand", "mp42",
            "-movflags", "+faststart",
            "-c", "copy",
            str(out_video),
        ]
        subprocess.run(cmd, check=True, creationflags=SUBPROCESS_FLAGS)
        if _is_valid_media_file(out_video):
            return out_video
    except Exception:
        pass
    return src_video


def pre_clean_source_video(
    src_video: Path,
    temp_dir: Path,
    slot_tag: str,
    log_callback: Callable[[str], None] | None = None,
) -> Path:
    step1_out = temp_dir / f"pre_raiw_{slot_tag}.mp4"
    if load_raiw_engine():
        try:
            run_raiw_internal([
                "video",
                "metadata",
                str(src_video),
                "--remove",
                "-o",
                str(step1_out),
            ])
            if _is_valid_media_file(step1_out):
                return step1_out
        except Exception as err:
            if log_callback:
                log_callback(
                    f"   ℹ️ [PRE-CLEAN] Fallback FFmpeg Box-Stripper untuk {src_video.name} ({err})"
                )

    fallback_out = temp_dir / f"pre_ffmpeg_{slot_tag}.mp4"
    return _ffmpeg_fallback_pre_clean(src_video, fallback_out)


def _final_bitstream_and_container_sanitize(
    input_path: Path,
    output_path: Path,
):
    ffmpeg_bin = get_ffmpeg_exe()
    output_path.unlink(missing_ok=True)

    meta_strip_flags = [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-metadata", "encoder=",
        "-metadata", "creation_time=",
        "-metadata", "comment=",
        "-metadata", "description=",
        "-metadata:s:v:0", "encoder=",
        "-metadata:s:v:0", "handler_name= ",
        "-metadata:s:v:0", "vendor_id=",
        "-metadata:s:a:0", "encoder=",
        "-metadata:s:a:0", "handler_name= ",
        "-metadata:s:a:0", "vendor_id=",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-flags:a", "+bitexact",
        "-brand", "mp42",
        "-movflags", "+faststart",
    ]

    cmd_primary = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(input_path),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        *meta_strip_flags,
        "-bsf:v", "filter_units=remove_types=6|12",
        "-c", "copy",
        str(output_path),
    ]
    res = subprocess.run(
        cmd_primary,
        capture_output=True,
        text=True,
        check=False,
        creationflags=SUBPROCESS_FLAGS,
    )
    if res.returncode == 0 and _is_valid_media_file(output_path):
        return

    cmd_secondary = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(input_path),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        *meta_strip_flags,
        "-bsf:v", "filter_units=remove_types=6",
        "-c", "copy",
        str(output_path),
    ]
    subprocess.run(cmd_secondary, check=True, creationflags=SUBPROCESS_FLAGS)


def _emergency_raw_stream_scrub(
    infected_mp4: Path,
    temp_dir: Path,
    log_callback: Callable[[str], None] | None = None,
):
    ffmpeg_bin = get_ffmpeg_exe()
    if log_callback:
        log_callback(
            "   🛡️ [EMERGENCY SCRUB] Mengekstrak Raw Elementary Stream (.h264) untuk memutus sisa kontainer..."
        )
    raw_h264 = temp_dir / "raw_stream.h264"
    raw_aac = temp_dir / "raw_stream.aac"
    rebuilt_mp4 = temp_dir / "rebuilt_clean.mp4"

    has_aud = has_audio_stream(infected_mp4)

    cmd_ext_v = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(infected_mp4),
        "-map", "0:v:0",
        "-c:v", "copy",
        "-bsf:v", "h264_mp4toannexb,filter_units=remove_types=6",
        "-f", "h264",
        str(raw_h264),
    ]
    subprocess.run(cmd_ext_v, check=True, creationflags=SUBPROCESS_FLAGS)

    if has_aud:
        cmd_ext_a = [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(infected_mp4),
            "-map", "0:a:0",
            "-c:a", "copy",
            "-f", "adts",
            str(raw_aac),
        ]
        subprocess.run(cmd_ext_a, check=True, creationflags=SUBPROCESS_FLAGS)

    _, _, fps, _ = probe_video_info(infected_mp4)
    cmd_mux = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
        "-r", str(fps),
        "-i", str(raw_h264),
    ]
    if has_aud and _is_valid_media_file(raw_aac, min_bytes=64):
        cmd_mux += ["-i", str(raw_aac), "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd_mux += ["-map", "0:v:0"]

    cmd_mux += [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-metadata", "encoder=",
        "-metadata", "creation_time=",
        "-metadata:s:v:0", "encoder=",
        "-metadata:s:v:0", "handler_name= ",
        "-metadata:s:a:0", "encoder=",
        "-metadata:s:a:0", "handler_name= ",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-flags:a", "+bitexact",
        "-brand", "mp42",
        "-movflags", "+faststart",
        "-c", "copy",
        str(rebuilt_mp4),
    ]
    subprocess.run(cmd_mux, check=True, creationflags=SUBPROCESS_FLAGS)

    if _is_valid_media_file(rebuilt_mp4):
        shutil.move(str(rebuilt_mp4), str(infected_mp4))


def clean_video_twice(
    input_path: Path,
    output_path: Path,
    log_callback: Callable[[str], None] | None = None,
):
    if log_callback:
        log_callback(
            f"   ↳ Final Bitstream Sanitizer: Menghapus SEI NAL (Type 6/12) & Reset MP4 Brand -> _cleaned/{output_path.name}..."
        )
    _final_bitstream_and_container_sanitize(input_path, output_path)