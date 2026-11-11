from pathlib import Path
import json
import re
import subprocess
from func1 import IMAGE_EXTENSIONS, SUBPROCESS_FLAGS
from func2 import get_ffprobe_exe

AI_SIGNATURE_PATTERNS: dict[str, bytes] = {
    "C2PA/JUMBF": b"c2pa",
    "JUMBF-Box": b"jumb",
    "ContentCredentials": b"contentcredentials",
    "ClaimGenerator": b"claim_generator",
    "Google-SynthID": b"synthid",
    "OpenAI-Sora": b"sora",
    "OpenAI": b"openai",
    "Kling-AI": b"kling",
    "Kuaishou": b"kuaishou",
    "RunwayML": b"runway",
    "Hailuo/MiniMax": b"minimax",
    "Hailuo": b"hailuo",
    "Luma-DreamMachine": b"luma",
    "Google-Veo": b"deepmind",
    "Pika-Labs": b"pika",
    "CapCut/ByteDance": b"bytedance",
    "CapCut": b"capcut",
    "HeyGen": b"heygen",
    "Midjourney": b"midjourney",
    "ElevenLabs": b"elevenlabs",
}


def _is_valid_media_file(path: Path | None, min_bytes: int = 512) -> bool:
    try:
        return path is not None and path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def _read_binary_head_tail(file_path: Path, chunk_size: int = 512 * 1024) -> bytes:
    try:
        size = file_path.stat().st_size
        with open(file_path, "rb") as f:
            if size <= chunk_size * 2:
                return f.read()
            head = f.read(chunk_size)
            f.seek(max(0, size - chunk_size))
            tail = f.read(chunk_size)
            return head + b"\x00" + tail
    except Exception:
        return b""


def _probe_bitstream_sei(video_path: Path, codec_name: str) -> list[str]:
    sei_findings: list[str] = []
    raw_bytes = _read_binary_head_tail(video_path, chunk_size=384 * 1024)
    lower_bytes = raw_bytes.lower()

    if b"x264 - core" in lower_bytes:
        sei_findings.append("Bitstream: x264 SEI UserData")
    if b"x265" in lower_bytes and b"options:" in lower_bytes:
        sei_findings.append("Bitstream: x265 SEI UserData")

    ffprobe_bin = get_ffprobe_exe()
    if not Path(ffprobe_bin).is_file():
        return sei_findings

    try:
        cmd = [
            ffprobe_bin,
            "-v", "verbose",
            "-select_streams", "v:0",
            "-read_intervals", "%+#15",
            "-bsf:v", "trace_headers",
            "-f", "null",
            "-",
            "-i", str(video_path),
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
            creationflags=SUBPROCESS_FLAGS,
        )
        trace_out = (res.stderr or "") + (res.stdout or "")
        trace_lower = trace_out.lower()

        if "sei" in trace_lower or "supplemental enhancement" in trace_lower:
            if codec_name == "hevc":
                sei_findings.append("Bitstream: HEVC SEI NAL (Type 39/40)")
            else:
                sei_findings.append("Bitstream: H.264 SEI NAL (Type 6)")
        elif re.search(r"nal_unit_type\s*\[\s*6\s*\]", trace_lower):
            sei_findings.append("Bitstream: H.264 SEI NAL (Type 6)")
        elif re.search(r"nal_unit_type\s*\[\s*(39|40)\s*\]", trace_lower):
            sei_findings.append("Bitstream: HEVC SEI NAL (Type 39/40)")
    except Exception:
        pass

    return sei_findings


def scan_media_watermarks(file_path: Path) -> dict:
    report = {
        "c2pa_jumbf": [],
        "ai_signatures": [],
        "bitstream_sei": [],
        "encoder_container": [],
        "image_meta": [],
        "codec": "unknown",
        "brand": "unknown",
        "all_findings": [],
        "is_clean": True,
    }

    if not file_path.is_file():
        return report

    ext = file_path.suffix.lower()
    raw_bytes = _read_binary_head_tail(file_path)
    lower_bytes = raw_bytes.lower()

    if ext in IMAGE_EXTENSIONS:
        if b"exif" in lower_bytes:
            report["image_meta"].append("EXIF")
        if b"http://ns.adobe.com/xap" in lower_bytes or b"<x:xmpmeta" in lower_bytes:
            report["image_meta"].append("XMP Packet")
        if b"icc_profile" in lower_bytes:
            report["image_meta"].append("ICC_Profile")
        if b"c2pa" in lower_bytes or b"jumb" in lower_bytes:
            report["c2pa_jumbf"].append("Image C2PA/JUMBF")
        for label, pat in AI_SIGNATURE_PATTERNS.items():
            if pat in lower_bytes and label not in ("C2PA/JUMBF", "JUMBF-Box"):
                report["ai_signatures"].append(label)

        all_img = (
            report["c2pa_jumbf"] + report["ai_signatures"] + report["image_meta"]
        )
        report["all_findings"] = all_img
        report["is_clean"] = len(all_img) == 0
        return report

    if b"uuid" in raw_bytes[:4096] or b"c2pa" in lower_bytes or b"jumb" in lower_bytes:
        if b"c2pa" in lower_bytes:
            report["c2pa_jumbf"].append("C2PA Manifest")
        if b"jumb" in lower_bytes:
            report["c2pa_jumbf"].append("JUMBF Box")
        if b"uuid" in raw_bytes[:8192] and not report["c2pa_jumbf"]:
            report["c2pa_jumbf"].append("Custom UUID Box")

    if b"<x:xmpmeta" in lower_bytes or b"http://ns.adobe.com/xap" in lower_bytes:
        report["c2pa_jumbf"].append("XMP Metadata Box")

    for label, pat in AI_SIGNATURE_PATTERNS.items():
        if label in ("C2PA/JUMBF", "JUMBF-Box"):
            continue
        if pat in lower_bytes:
            report["ai_signatures"].append(f"Signature:{label}")

    ffprobe_bin = get_ffprobe_exe()
    if Path(ffprobe_bin).is_file():
        try:
            cmd = [
                ffprobe_bin,
                "-v", "error",
                "-show_entries",
                "format=format_name:format_tags:stream=index,codec_name,codec_type:stream_tags:chapters",
                "-of", "json",
                str(file_path),
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                creationflags=SUBPROCESS_FLAGS,
            )
            data = json.loads(res.stdout or "{}")

            fmt_tags = data.get("format", {}).get("tags", {}) or {}
            major_brand = fmt_tags.get("major_brand", "").strip()
            if major_brand:
                report["brand"] = major_brand

            for k, v in fmt_tags.items():
                k_low = k.lower()
                v_str = str(v).strip()
                v_low = v_str.lower()
                if k_low in ("major_brand", "minor_version", "compatible_brands"):
                    continue
                if k_low == "encoder" and v_str:
                    report["encoder_container"].append(f"Tag:encoder={v_str}")
                elif k_low == "creation_time" and v_str:
                    report["encoder_container"].append("Tag:creation_time")
                elif k_low in ("comment", "description", "artist", "title", "copyright", "software") and v_str:
                    report["encoder_container"].append(f"Tag:{k}={v_str[:24]}")

                for label, pat in AI_SIGNATURE_PATTERNS.items():
                    if pat.decode("utf-8", errors="ignore") in v_low:
                        sig_item = f"Tag-AI:{label}"
                        if sig_item not in report["ai_signatures"]:
                            report["ai_signatures"].append(sig_item)

            chapters = data.get("chapters", []) or []
            if chapters:
                report["encoder_container"].append(f"Chapters({len(chapters)})")

            streams = data.get("streams", []) or []
            for st in streams:
                c_type = st.get("codec_type", "")
                c_name = st.get("codec_name", "")
                if c_type == "video" and c_name:
                    report["codec"] = c_name

                st_tags = st.get("tags", {}) or {}
                for sk, sv in st_tags.items():
                    sk_low = sk.lower()
                    sv_str = str(sv).strip()
                    if sk_low == "encoder" and sv_str:
                        report["encoder_container"].append(
                            f"Stream({c_type}):encoder={sv_str}"
                        )
                    elif sk_low == "creation_time" and sv_str:
                        report["encoder_container"].append(
                            f"Stream({c_type}):creation_time"
                        )
                    elif sk_low == "handler_name" and sv_str:
                        if any(
                            h in sv_str.lower()
                            for h in (
                                "videohandler",
                                "soundhandler",
                                "gpac",
                                "bento4",
                                "l-smash",
                                "lavf",
                                "ffmpeg",
                            )
                        ):
                            report["encoder_container"].append(
                                f"Handler:{sv_str}"
                            )
        except Exception:
            pass

    if b"lavf" in lower_bytes and not any("lavf" in x.lower() for x in report["encoder_container"]):
        report["encoder_container"].append("Binary:Lavf-Signature")
    if b"lavc" in lower_bytes and not any("lavc" in x.lower() for x in report["encoder_container"]):
        report["encoder_container"].append("Binary:Lavc-Signature")

    report["bitstream_sei"] = _probe_bitstream_sei(file_path, report["codec"])

    all_items = (
        report["c2pa_jumbf"]
        + report["ai_signatures"]
        + report["bitstream_sei"]
        + report["encoder_container"]
    )
    report["all_findings"] = all_items
    report["is_clean"] = len(all_items) == 0
    return report


def format_scan_log_line(file_name: str, scan_report: dict, is_input: bool = True) -> str:
    prefix = "🔍 [SCAN INPUT]" if is_input else "🔎 [SCAN OUTPUT]"
    findings = scan_report.get("all_findings", [])
    if not findings:
        return f"   {prefix} {file_name}: Bersih (0 watermark/metadata terdeteksi)"
    joined = ", ".join(findings)
    return f"   {prefix} {file_name}: Terdeteksi {len(findings)} jejak -> [{joined}]"


def has_audio_stream(video_path: Path) -> bool:
    ffprobe_bin = get_ffprobe_exe()
    if not Path(ffprobe_bin).is_file():
        return False
    try:
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(video_path),
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            creationflags=SUBPROCESS_FLAGS,
        )
        return "audio" in res.stdout.lower()
    except Exception:
        return False


def probe_video_info(video_path: Path) -> tuple[int, int, float, float]:
    w, h, fps, duration = 1080, 1920, 30.0, 5.0
    ffprobe_bin = get_ffprobe_exe()
    if not Path(ffprobe_bin).is_file():
        return w, h, fps, duration
    try:
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,duration:format=duration",
            "-of", "json",
            str(video_path),
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            creationflags=SUBPROCESS_FLAGS,
        )
        data = json.loads(res.stdout or "{}")
        streams = data.get("streams", [])
        if streams:
            st = streams[0]
            raw_w = int(st.get("width", 1080)) // 2 * 2
            raw_h = int(st.get("height", 1920)) // 2 * 2
            if raw_w > 0 and raw_h > 0:
                w, h = raw_w, raw_h

            r_fps = st.get("r_frame_rate", "30/1")
            if "/" in r_fps:
                num, den = r_fps.split("/", 1)
                if float(den) > 0:
                    calc_fps = float(num) / float(den)
                    if 5.0 <= calc_fps <= 120.0:
                        fps = round(calc_fps, 3)
            else:
                calc_fps = float(r_fps)
                if 5.0 <= calc_fps <= 120.0:
                    fps = round(calc_fps, 3)

            st_dur = st.get("duration")
            if st_dur is not None:
                duration = max(0.5, float(st_dur))

        fmt_dur = data.get("format", {}).get("duration")
        if fmt_dur is not None:
            duration = max(0.5, float(fmt_dur))
    except Exception:
        pass
    return w, h, fps, duration


def probe_video_resolution(video_path: Path) -> tuple[int, int]:
    w, h, _, _ = probe_video_info(video_path)
    return w, h