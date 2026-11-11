from pathlib import Path
import subprocess
import tempfile
from typing import Callable

from func1 import SUBPROCESS_FLAGS
from func2 import get_ffmpeg_exe
from func3 import (
    format_scan_log_line,
    has_audio_stream,
    probe_video_info,
    scan_media_watermarks,
)
from func4 import (
    _compute_safe_trim_window,
    _emergency_raw_stream_scrub,
    _final_bitstream_and_container_sanitize,
    clean_overlay_image,
    pre_clean_source_video,
)


def merge_videos(
    v1: Path,
    v2: Path,
    output: Path,
    reencode: bool,
    apply_loudnorm: bool = False,
    target_lufs: float = -23.0,
    apply_boost: bool = False,
    boost_db: float = 10.0,
    overlay_image: Path | None = None,
    overlay_scale: float = 30.0,
    overlay_margin: int = 0,
    apply_zoom: bool = True,
    zoom_pct: float = 2.0,
    v1_cut_start: float = 0.0,
    v1_cut_end: float = 0.0,
    v2_cut_start: float = 0.0,
    v2_cut_end: float = 0.0,
    log_callback: Callable[[str], None] | None = None,
):
    ffmpeg_bin = get_ffmpeg_exe()
    v1_abs, v2_abs, out_abs = v1.resolve(), v2.resolve(), output.resolve()
    has_overlay = overlay_image is not None and overlay_image.is_file()

    w1, h1, fps1, dur1 = probe_video_info(v1_abs)
    w2, h2, _, dur2 = probe_video_info(v2_abs)
    v1_has_aud = has_audio_stream(v1_abs)
    v2_has_aud = has_audio_stream(v2_abs)
    any_has_audio = v1_has_aud or v2_has_aud

    s1, e1, dur1_eff, trim1_active = _compute_safe_trim_window(
        dur1, v1_cut_start, v1_cut_end, "Video 1", log_callback
    )
    s2, e2, dur2_eff, trim2_active = _compute_safe_trim_window(
        dur2, v2_cut_start, v2_cut_end, "Video 2", log_callback
    )

    v1_trim_vf = f"trim=start={s1}:end={e1}," if trim1_active else ""
    v2_trim_vf = f"trim=start={s2}:end={e2}," if trim2_active else ""
    v1_trim_af = f"atrim=start={s1}:end={e1}," if trim1_active else ""
    v2_trim_af = f"atrim=start={s2}:end={e2}," if trim2_active else ""

    af_filters = [
        "aresample=48000:async=1",
        "highpass=f=25",
        "lowpass=f=18200",
        "allpass=f=1200:width_type=q:w=0.707",
    ]
    if apply_loudnorm:
        af_filters.append(f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11")
    if apply_boost and abs(boost_db) > 1e-4:
        af_filters.append(f"volume={boost_db}dB")
    af_chain_str = ",".join(af_filters)

    zoom_vf = ""
    if apply_zoom and zoom_pct > 1e-4:
        zf = round(1.0 + (zoom_pct / 100.0), 6)
        zoom_vf = (
            f"scale=w='trunc(iw*{zf}/2)*2':h='trunc(ih*{zf}/2)*2':flags=lanczos,"
            f"crop=w='trunc(iw/{zf}/2)*2':h='trunc(ih/{zf}/2)*2':"
            f"x='(iw-ow)/2':y='(ih-oh)/2',setsar=1,"
        )

    anti_ai_vf = (
        "eq=contrast=1.012:brightness=0.002:saturation=1.014:gamma=0.992,"
        "unsharp=3:3:0.18:3:3:0.0,"
        "noise=c0s=1.4:c1s=1.0:c2s=1.0:allf=t+u,"
        "format=yuv420p"
    )

    scale_ratio = round(overlay_scale / 100.0, 4)
    xy_expr = f"W-w-{overlay_margin}:H-h-{overlay_margin}"
    scale2ref_expr = (
        f"scale2ref=w='trunc(iw*{scale_ratio}/2)*2':h='trunc(ow/mdar/2)*2'"
    )

    cmd = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(v1_abs),
        "-i", str(v2_abs),
    ]
    if has_overlay:
        cmd += ["-i", str(overlay_image.resolve())]

    need_scale_pad = reencode or (w1 != w2) or (h1 != h2)
    fps_expr = f",fps={fps1}" if reencode else ""

    if need_scale_pad:
        v_prep = (
            f"[0:v:0]{v1_trim_vf}scale={w1}:{h1}:force_original_aspect_ratio=decrease:flags=bicubic,"
            f"pad={w1}:{h1}:(ow-iw)/2:(oh-ih)/2,setsar=1{fps_expr},format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v0];"
            f"[1:v:0]{v2_trim_vf}scale={w1}:{h1}:force_original_aspect_ratio=decrease:flags=bicubic,"
            f"pad={w1}:{h1}:(ow-iw)/2:(oh-ih)/2,setsar=1{fps_expr},format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v1];"
        )
    else:
        v_prep = (
            f"[0:v:0]{v1_trim_vf}setsar=1,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v0];"
            f"[1:v:0]{v2_trim_vf}setsar=1,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v1];"
        )

    a_prep = ""
    if any_has_audio:
        if v1_has_aud:
            a0_part = f"[0:a:0]{v1_trim_af}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        else:
            a0_part = f"anullsrc=r=48000:cl=stereo:d={dur1_eff:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"

        if v2_has_aud:
            a1_part = f"[1:a:0]{v2_trim_af}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
        else:
            a1_part = f"anullsrc=r=48000:cl=stereo:d={dur2_eff:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"

        a_prep = a0_part + a1_part
        concat_part = "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v_cat][a_raw];"
        audio_post = f"[a_raw]{af_chain_str}[a]"
    else:
        concat_part = "[v0][v1]concat=n=2:v=1:a=0[v_cat];"
        audio_post = ""

    if has_overlay:
        zoom_step = (
            f"[v_cat]{zoom_vf.rstrip(',')}[v_base];"
            if zoom_vf
            else "[v_cat]null[v_base];"
        )
        video_post = (
            zoom_step
            + f"[2:v][v_base]{scale2ref_expr}[ovr][base];"
            + f"[base][ovr]overlay={xy_expr}:format=auto,{anti_ai_vf}[v]"
        )
    else:
        video_post = f"[v_cat]{zoom_vf}{anti_ai_vf}[v]"

    filter_parts = [v_prep]
    if any_has_audio:
        filter_parts.append(a_prep)
    filter_parts.append(concat_part)
    filter_parts.append(video_post + (";" + audio_post if any_has_audio else ""))

    filter_complex = "".join(filter_parts)

    cmd += ["-filter_complex", filter_complex, "-map", "[v]"]
    if any_has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]

    cmd += [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-metadata", "encoder=",
        "-metadata", "creation_time=",
        "-metadata:s:v:0", "encoder=",
        "-metadata:s:v:0", "handler_name= ",
        "-metadata:s:a:0", "encoder=",
        "-metadata:s:a:0", "handler_name= ",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-g", "60",
        "-keyint_min", "30",
        "-sc_threshold", "40",
        "-bf", "2",
        "-x264-params", "no-info=1:nal-hrd=none",
        "-bsf:v", "filter_units=remove_types=6",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-flags:a", "+bitexact",
        "-brand", "mp42",
        "-movflags", "+faststart",
        str(out_abs),
    ]
    subprocess.run(cmd, check=True, creationflags=SUBPROCESS_FLAGS)


def process_and_clean_pair(
    v1: Path,
    v2: Path,
    output: Path,
    reencode: bool,
    apply_loudnorm: bool = False,
    target_lufs: float = -23.0,
    apply_boost: bool = False,
    boost_db: float = 10.0,
    overlay_image: Path | None = None,
    overlay_scale: float = 30.0,
    overlay_margin: int = 0,
    apply_zoom: bool = True,
    zoom_pct: float = 2.0,
    v1_cut_start: float = 0.0,
    v1_cut_end: float = 0.0,
    v2_cut_start: float = 0.0,
    v2_cut_end: float = 0.0,
    log_callback: Callable[[str], None] | None = None,
) -> str:
    scan_v1 = scan_media_watermarks(v1)
    scan_v2 = scan_media_watermarks(v2)
    if log_callback:
        log_callback(format_scan_log_line(v1.name, scan_v1, is_input=True))
        log_callback(format_scan_log_line(v2.name, scan_v2, is_input=True))

    if overlay_image is not None and overlay_image.is_file():
        scan_ovr = scan_media_watermarks(overlay_image)
        if log_callback:
            log_callback(format_scan_log_line(overlay_image.name, scan_ovr, is_input=True))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        if log_callback:
            log_callback(
                "   ↳ [STEP 1/3] Pre-Clean Source: Menghancurkan C2PA/JUMBF & EXIF pada file sumber..."
            )
        clean_v1 = pre_clean_source_video(v1, tmp_path, "v1", log_callback=log_callback)
        clean_v2 = pre_clean_source_video(v2, tmp_path, "v2", log_callback=log_callback)

        clean_ovr: Path | None = None
        if overlay_image is not None and overlay_image.is_file():
            ovr_temp = tmp_path / f"clean_ovr{overlay_image.suffix.lower()}"
            clean_ovr = clean_overlay_image(overlay_image, ovr_temp)

        if log_callback:
            log_callback(
                "   ↳ [STEP 2/3] Trim, Merge & Stego-Breaker: Re-encode H.264 + Temporal Micro-Noise + Audio Phase Scrub..."
            )
        merged_temp = tmp_path / "merged_stego_clean.mp4"
        merge_videos(
            v1=clean_v1,
            v2=clean_v2,
            output=merged_temp,
            reencode=reencode,
            apply_loudnorm=apply_loudnorm,
            target_lufs=target_lufs,
            apply_boost=apply_boost,
            boost_db=boost_db,
            overlay_image=clean_ovr,
            overlay_scale=overlay_scale,
            overlay_margin=overlay_margin,
            apply_zoom=apply_zoom,
            zoom_pct=zoom_pct,
            v1_cut_start=v1_cut_start,
            v1_cut_end=v1_cut_end,
            v2_cut_start=v2_cut_start,
            v2_cut_end=v2_cut_end,
            log_callback=log_callback,
        )

        if log_callback:
            log_callback(
                f"   ↳ [STEP 3/3] Final Bitstream Sanitizer: Stripping SEI NAL (Type 6/12) & Reset MP4 Brand -> _cleaned/{output.name}..."
            )
        _final_bitstream_and_container_sanitize(merged_temp, output)

        out_scan = scan_media_watermarks(output)
        if not out_scan["is_clean"]:
            _emergency_raw_stream_scrub(output, tmp_path, log_callback=log_callback)
            out_scan = scan_media_watermarks(output)

    if log_callback:
        c2pa_stat = (
            "BERSIH (0 ditemukan)"
            if not out_scan["c2pa_jumbf"]
            else f"TERDETEKSI ({', '.join(out_scan['c2pa_jumbf'])})"
        )
        ai_stat = (
            "BERSIH (0 ditemukan)"
            if not out_scan["ai_signatures"]
            else f"TERDETEKSI ({', '.join(out_scan['ai_signatures'])})"
        )
        sei_stat = (
            "BERSIH (0 SEI NAL unit)"
            if not out_scan["bitstream_sei"]
            else f"TERDETEKSI ({', '.join(out_scan['bitstream_sei'])})"
        )
        enc_stat = (
            f"BERSIH (Bitexact, Brand: {out_scan['brand']})"
            if not out_scan["encoder_container"]
            else f"TERDETEKSI ({', '.join(out_scan['encoder_container'])})"
        )

        badge = "✅" if out_scan["is_clean"] else "⚠️"
        log_callback(f"   {badge} [CEK OUTPUT] _cleaned/{output.name}:")
        log_callback(f"      • C2PA / JUMBF Box : {c2pa_stat}")
        log_callback(f"      • AI Signatures    : {ai_stat}")
        log_callback(f"      • Bitstream SEI    : {sei_stat}")
        log_callback(f"      • Encoder / Lavf   : {enc_stat}")

    if not out_scan["is_clean"]:
        raise RuntimeError(
            f"Masih tersisa watermark pada output: {', '.join(out_scan['all_findings'])}"
        )

    return f"100% Bersih | Brand:{out_scan['brand']}"