import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from typing import Callable
import urllib.request
import zipfile

_RAIW_LOCK = threading.Lock()

_BASE_DIR = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)

FFMPEG_ZIP_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)

RAIW_REPO_ZIP_URL = (
    "https://github.com/wiltodelta/remove-ai-watermarks/archive/refs/heads/main.zip"
)

ALLOWED_RAIW_EXTS = {".py", ".json"}
MIN_FFMPEG_SIZE_BYTES = 10 * 1024 * 1024

raiw_cli_main = None
HAS_RAIW = False


def get_appdata_bin_dir() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        base_dir = Path(local_appdata) / "ToolsByMesscheant"
    else:
        base_dir = Path.home() / ".ToolsByMesscheant"

    bin_dir = base_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    return bin_dir


def get_appdata_src_dir() -> Path:
    src_dir = get_appdata_bin_dir() / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    return src_dir


def get_ffmpeg_exe() -> str:
    appdata_ffmpeg = get_appdata_bin_dir() / "ffmpeg.exe"
    if appdata_ffmpeg.is_file() and appdata_ffmpeg.stat().st_size >= MIN_FFMPEG_SIZE_BYTES:
        return str(appdata_ffmpeg)
    found = shutil.which("ffmpeg")
    return found if found else str(appdata_ffmpeg)


def get_ffprobe_exe() -> str:
    appdata_ffprobe = get_appdata_bin_dir() / "ffprobe.exe"
    if appdata_ffprobe.is_file() and appdata_ffprobe.stat().st_size >= MIN_FFMPEG_SIZE_BYTES:
        return str(appdata_ffprobe)
    found = shutil.which("ffprobe")
    return found if found else str(appdata_ffprobe)


def register_ffmpeg_paths():
    appdata_bin = get_appdata_bin_dir()
    appdata_src = get_appdata_src_dir()

    candidate_src_dirs = [appdata_src, _BASE_DIR / "src"]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate_src_dirs.append(Path(sys._MEIPASS) / "src")

    for s_dir in reversed(candidate_src_dirs):
        s_str = str(s_dir)
        if s_dir.is_dir():
            if s_str in sys.path:
                sys.path.remove(s_str)
            sys.path.insert(0, s_str)

    candidate_bin_dirs = [appdata_bin, _BASE_DIR / "bin"]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate_bin_dirs.append(Path(sys._MEIPASS) / "bin")

    current_parts = os.environ.get("PATH", "").split(os.pathsep)
    for d in reversed(candidate_bin_dirs):
        d_str = str(d)
        if d.is_dir():
            if d_str in current_parts:
                current_parts.remove(d_str)
            current_parts.insert(0, d_str)
    os.environ["PATH"] = os.pathsep.join(current_parts)


def is_raiw_src_files_present() -> bool:
    pkg_dir = get_appdata_src_dir() / "remove_ai_watermarks"
    required_files = ["__init__.py", "cli.py"]
    return all((pkg_dir / f).is_file() and (pkg_dir / f).stat().st_size > 0 for f in required_files)


def _copy_selective_raiw_tree(source_pkg_dir: Path, target_pkg_dir: Path) -> int:
    copied_count = 0
    if not source_pkg_dir.is_dir():
        return 0
    for root, _, files in os.walk(source_pkg_dir):
        rel_root = Path(root).relative_to(source_pkg_dir)
        if "__pycache__" in rel_root.parts:
            continue
        for file_name in files:
            src_file = Path(root) / file_name
            if src_file.suffix.lower() in ALLOWED_RAIW_EXTS:
                dst_file = target_pkg_dir / rel_root / file_name
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                copied_count += 1
    return copied_count


def load_raiw_engine() -> bool:
    global raiw_cli_main, HAS_RAIW
    register_ffmpeg_paths()
    try:
        if "remove_ai_watermarks.cli" in sys.modules:
            mod = importlib.reload(sys.modules["remove_ai_watermarks.cli"])
        else:
            mod = importlib.import_module("remove_ai_watermarks.cli")
        raiw_cli_main = getattr(mod, "main", None)
        HAS_RAIW = raiw_cli_main is not None
    except Exception:
        raiw_cli_main = None
        HAS_RAIW = False
    return HAS_RAIW


def is_ffmpeg_binaries_present() -> bool:
    appdata_bin = get_appdata_bin_dir()
    ffmpeg_ok = (appdata_bin / "ffmpeg.exe").is_file() and (
        appdata_bin / "ffmpeg.exe"
    ).stat().st_size >= MIN_FFMPEG_SIZE_BYTES
    ffprobe_ok = (appdata_bin / "ffprobe.exe").is_file() and (
        appdata_bin / "ffprobe.exe"
    ).stat().st_size >= MIN_FFMPEG_SIZE_BYTES
    return ffmpeg_ok and ffprobe_ok


def is_ffmpeg_installed() -> bool:
    register_ffmpeg_paths()
    if not is_ffmpeg_binaries_present():
        return False
    if not is_raiw_src_files_present():
        return False
    return load_raiw_engine()


def _download_file_with_progress(
    url: str,
    dest_path: Path,
    progress_cb: Callable[[float, float], None] | None = None,
):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req, timeout=60) as response, open(dest_path, "wb") as out_file:
        total_size = int(response.info().get("Content-Length", 0) or 0)
        downloaded = 0
        chunk_size = 256 * 1024
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if progress_cb:
                progress_cb(
                    downloaded / (1024 * 1024),
                    total_size / (1024 * 1024) if total_size > 0 else 0.0,
                )


def _ensure_raiw_src_in_appdata(
    update_cb: Callable[[str, float], None],
    log_cb: Callable[[str], None] | None = None,
):
    target_pkg = get_appdata_src_dir() / "remove_ai_watermarks"
    if is_raiw_src_files_present() and load_raiw_engine():
        return

    local_candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        local_candidates.append(Path(sys._MEIPASS) / "src" / "remove_ai_watermarks")
    local_candidates.append(_BASE_DIR / "src" / "remove_ai_watermarks")

    for candidate in local_candidates:
        if (candidate / "cli.py").is_file():
            update_cb("Menyalin modul inti remove_ai_watermarks ke AppData\\bin\\src...", 0.12)
            copied = _copy_selective_raiw_tree(candidate, target_pkg)
            if copied > 0 and is_raiw_src_files_present():
                load_raiw_engine()
                if log_cb:
                    log_cb(f"📦 Modul remove_ai_watermarks ({copied} file inti) disiapkan di: {target_pkg}")
                return

    if log_cb:
        log_cb("📥 Mengunduh modul inti remove-ai-watermarks dari GitHub ke AppData\\bin\\src...")

    update_cb("Mengunduh modul inti remove_ai_watermarks (GitHub)...", 0.14)
    tmp_src_zip = Path(tempfile.gettempdir()) / "messcheant_raiw_src.zip"
    try:
        _download_file_with_progress(RAIW_REPO_ZIP_URL, tmp_src_zip)
        update_cb("Mengekstrak hanya file inti src/remove_ai_watermarks...", 0.22)
        extracted_count = 0
        marker = "/src/remove_ai_watermarks/"
        with zipfile.ZipFile(tmp_src_zip, "r") as zf:
            for member in zf.namelist():
                norm_member = member.replace("\\", "/")
                if norm_member.endswith("/"):
                    continue
                idx = norm_member.find(marker)
                if idx == -1:
                    continue
                rel_inside_pkg = norm_member[idx + len(marker) :]
                if not rel_inside_pkg or "__pycache__" in rel_inside_pkg:
                    continue
                rel_path = Path(rel_inside_pkg)
                if rel_path.suffix.lower() not in ALLOWED_RAIW_EXTS:
                    continue
                dst_file = target_pkg / rel_path
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src_f, open(dst_file, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                extracted_count += 1

        load_raiw_engine()
        if log_cb:
            log_cb(
                f"✅ Berhasil mengunduh {extracted_count} file inti remove_ai_watermarks ke: {target_pkg}"
            )
    finally:
        try:
            tmp_src_zip.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_system_dependencies(
    status_callback: Callable[[str, float], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> Path:
    def _update(msg: str, progress: float):
        if status_callback:
            status_callback(msg, max(0.0, min(1.0, progress)))

    _update("Memverifikasi folder AppData\\Local\\ToolsByMesscheant\\bin...", 0.05)
    register_ffmpeg_paths()
    appdata_bin = get_appdata_bin_dir()

    _ensure_raiw_src_in_appdata(_update, log_callback)

    if is_ffmpeg_binaries_present():
        load_raiw_engine()
        _update("Seluruh komponen sistem, src & FFmpeg sudah lengkap!", 1.0)
        return appdata_bin

    if log_callback:
        log_callback(f"📥 Mengunduh FFmpeg & FFprobe ke: {appdata_bin}...")

    _update("Menghubungkan ke server rilis FFmpeg (GitHub)...", 0.25)
    tmp_zip = Path(tempfile.gettempdir()) / "messcheant_ffmpeg_setup.zip"
    try:
        def _on_ffmpeg_dl(mb_done: float, mb_total: float):
            if mb_total > 0:
                ratio = mb_done / mb_total
                _update(
                    f"Mengunduh FFmpeg & FFprobe... ({mb_done:.1f} MB / {mb_total:.1f} MB)",
                    0.25 + (ratio * 0.60),
                )
            else:
                _update(f"Mengunduh FFmpeg & FFprobe... ({mb_done:.1f} MB)", 0.55)

        _download_file_with_progress(FFMPEG_ZIP_URL, tmp_zip, _on_ffmpeg_dl)

        _update("Mengekstrak ffmpeg.exe & ffprobe.exe ke AppData\\bin...", 0.90)
        extracted_files = []
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            for member in zf.namelist():
                filename = Path(member).name.lower()
                if filename in ("ffmpeg.exe", "ffprobe.exe"):
                    target_file = appdata_bin / filename
                    with zf.open(member) as src, open(target_file, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    extracted_files.append(filename)

        register_ffmpeg_paths()
        load_raiw_engine()

        if not is_ffmpeg_binaries_present():
            raise RuntimeError(
                f"Ekstraksi selesai ({extracted_files}), namun ffmpeg.exe/ffprobe.exe belum lengkap di {appdata_bin}."
            )

        _update("Instalasi Dependencies Selesai! Membuka aplikasi...", 1.0)
        if log_callback:
            log_callback(
                f"✅ Instalasi selesai! FFmpeg, FFprobe & src siap di: {appdata_bin}"
            )
        return appdata_bin
    finally:
        try:
            tmp_zip.unlink(missing_ok=True)
        except OSError:
            pass


def setup_bundled_ffmpeg():
    if is_ffmpeg_installed():
        return
    ensure_system_dependencies()


def run_raiw_internal(args_list: list[str]):
    if not HAS_RAIW or raiw_cli_main is None:
        if not load_raiw_engine() or raiw_cli_main is None:
            raise RuntimeError("Modul remove_ai_watermarks tidak tersedia di AppData\\bin\\src.")
    with _RAIW_LOCK:
        old_argv = sys.argv[:]
        try:
            sys.argv = ["remove-ai-watermarks"] + list(args_list)
            raiw_cli_main()
        except SystemExit as e:
            if e.code not in (0, None):
                raise RuntimeError(
                    f"Eksekusi remove-ai-watermarks berhenti (kode: {e.code})"
                )
        finally:
            sys.argv = old_argv


register_ffmpeg_paths()
load_raiw_engine()