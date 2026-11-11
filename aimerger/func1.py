from pathlib import Path
import re
import subprocess
import sys

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def extract_base_and_index(file_path: Path) -> tuple[str, int] | None:
    stem = file_path.stem.strip()

    m = re.match(
        r"^(.+?)(?:[\s_\-]+(?:scene|part|vid|s|v)?(\d+)|\s*\((\d+)\))$",
        stem,
        re.IGNORECASE,
    )
    if m:
        base = m.group(1).strip(" _-.")
        num_str = m.group(2) or m.group(3)
        if base and num_str:
            return base, int(num_str)

    m2 = re.match(r"^(.+?[^\d\s_\-])(\d+)$", stem)
    if m2:
        base = m2.group(1).strip(" _-.")
        num_str = m2.group(2)
        if base and num_str:
            return base, int(num_str)

    return None


def extract_image_base(file_path: Path) -> str:
    parsed = extract_base_and_index(file_path)
    if parsed:
        return parsed[0]
    return file_path.stem.strip(" _-.")


def parse_lufs(raw_str: str) -> float:
    try:
        val = float(raw_str.strip().replace(",", "."))
    except ValueError:
        val = -23.0
    if val > 0:
        val = -val
    return max(-70.0, min(-5.0, val))


def parse_boost_db(raw_str: str) -> float:
    try:
        val = float(raw_str.strip().replace(",", "."))
    except ValueError:
        val = 10.0
    return max(-60.0, min(60.0, val))


def parse_zoom_pct(raw_str: str) -> float:
    try:
        val = float(raw_str.strip().replace(",", "."))
    except ValueError:
        val = 2.0
    return max(0.0, min(100.0, val))


def parse_cut_sec(raw_str: str) -> float:
    try:
        val = float(str(raw_str).strip().replace(",", "."))
    except ValueError:
        val = 0.0
    return max(0.0, min(600.0, round(val, 3)))


def parse_overlay_params(raw_scale: str, raw_margin: str) -> tuple[float, int]:
    try:
        scale = float(raw_scale.strip().replace(",", "."))
    except ValueError:
        scale = 30.0
    scale = max(5.0, min(95.0, scale))

    try:
        margin = int(float(raw_margin.strip().replace(",", ".")))
    except ValueError:
        margin = 0
    margin = max(0, margin)

    return scale, margin


def ingest_paths(
    paths: list[Path],
    collected_files: set[Path],
    collected_images: set[Path],
    excluded_images: set[Path],
) -> tuple[int, int]:
    added_videos = 0
    added_images = 0

    for p in paths:
        try:
            if not p.exists():
                continue
            if p.is_dir():
                for child in p.rglob("*"):
                    if "_cleaned" in child.parts or not child.is_file():
                        continue
                    ext = child.suffix.lower()
                    res = child.resolve()
                    if ext in VIDEO_EXTENSIONS:
                        if res not in collected_files:
                            collected_files.add(res)
                            added_videos += 1
                    elif ext in IMAGE_EXTENSIONS:
                        excluded_images.discard(res)
                        if res not in collected_images:
                            collected_images.add(res)
                            added_images += 1
            elif p.is_file():
                if "_cleaned" in p.parts:
                    continue
                ext = p.suffix.lower()
                res = p.resolve()
                if ext in VIDEO_EXTENSIONS:
                    if res not in collected_files:
                        collected_files.add(res)
                        added_videos += 1
                elif ext in IMAGE_EXTENSIONS:
                    excluded_images.discard(res)
                    if res not in collected_images:
                        collected_images.add(res)
                        added_images += 1
        except OSError:
            continue

    return added_videos, added_images


def find_matching_image(
    parent_dir: Path,
    base_lower: str,
    image_by_folder_base: dict[tuple[Path, str], Path],
    image_by_base_global: dict[str, Path],
    excluded_images: set[Path],
) -> Path | None:
    if (parent_dir, base_lower) in image_by_folder_base:
        candidate = image_by_folder_base[(parent_dir, base_lower)]
        if candidate not in excluded_images and candidate.is_file():
            return candidate

    if parent_dir.is_dir():
        try:
            for child in sorted(parent_dir.iterdir()):
                if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                    res = child.resolve()
                    if res in excluded_images:
                        continue
                    if (
                        child.stem.strip(" _-.").lower() == base_lower
                        or extract_image_base(child).lower() == base_lower
                    ):
                        return res
        except OSError:
            pass

    candidate = image_by_base_global.get(base_lower)
    if candidate and candidate not in excluded_images and candidate.is_file():
        return candidate
    return None


def evaluate_row(row: dict, require_overlay: bool):
    v1: Path | None = row.get("v1")
    v2: Path | None = row.get("v2")
    idx1: int | None = row.get("idx1")
    idx2: int | None = row.get("idx2")
    ovr: Path | None = row.get("overlay")
    base: str = row.get("base", "")
    parent_dir: Path = row.get("parent_dir", Path("."))
    unnumbered: bool = row.get("unnumbered", False)

    if v1 is not None and v2 is not None:
        s1 = str(idx1) if idx1 is not None else "1"
        s2 = str(idx2) if idx2 is not None else "2"
        row["out"] = parent_dir / "_cleaned" / f"{base}{s1}{s2}.mp4"
    else:
        row["out"] = None

    if unnumbered:
        row["valid"] = False
        row["status_reason"] = "TIDAK VALID (Tanpa Angka)"
        return

    missing_parts = []
    if v1 is None or not v1.is_file():
        missing_parts.append("Kurang Video 1")
    if v2 is None or not v2.is_file():
        missing_parts.append("Kurang Video 2")
    if require_overlay and (ovr is None or not ovr.is_file()):
        missing_parts.append("Tanpa Gambar")

    if missing_parts:
        row["valid"] = False
        row["status_reason"] = f"TIDAK VALID ({', '.join(missing_parts)})"
    else:
        row["valid"] = True
        row["status_reason"] = "VALID"


def recompute_all_rows_validity(
    pairs_queue: list[dict], require_overlay: bool
) -> list[dict]:
    cleaned_queue = []
    for row in pairs_queue:
        if row.get("v1") is None and row.get("v2") is None:
            continue
        evaluate_row(row, require_overlay)
        cleaned_queue.append(row)
    return cleaned_queue


def regroup_pairs(
    collected_files: set[Path],
    collected_images: set[Path],
    excluded_images: set[Path],
    require_overlay: bool,
    existing_cuts: dict[Path, tuple[float, float]] | None = None,
) -> list[dict]:
    pairs_queue: list[dict] = []
    cut_map = existing_cuts or {}

    image_by_folder_base: dict[tuple[Path, str], Path] = {}
    image_by_base_global: dict[str, Path] = {}

    for img_path in sorted(collected_images):
        if img_path in excluded_images or not img_path.is_file():
            continue
        exact_stem = img_path.stem.strip(" _-.").lower()
        parsed_base = extract_image_base(img_path).lower()

        image_by_folder_base[(img_path.parent, exact_stem)] = img_path
        image_by_base_global[exact_stem] = img_path

        if (img_path.parent, parsed_base) not in image_by_folder_base:
            image_by_folder_base[(img_path.parent, parsed_base)] = img_path
        if parsed_base not in image_by_base_global:
            image_by_base_global[parsed_base] = img_path

    groups: dict[tuple[Path, str], list[tuple[int, Path, str]]] = {}
    unnumbered_files: list[Path] = []

    for fpath in sorted(collected_files):
        if not fpath.is_file():
            continue
        parsed = extract_base_and_index(fpath)
        if not parsed:
            unnumbered_files.append(fpath)
            continue
        base_name, idx_num = parsed
        key = (fpath.parent, base_name.lower())
        groups.setdefault(key, []).append((idx_num, fpath, base_name))

    for (parent_dir, base_lower), items in sorted(
        groups.items(), key=lambda x: (str(x[0][0]), x[0][1])
    ):
        items.sort(key=lambda x: x[0])
        clean_base = items[0][2]

        matched_img = find_matching_image(
            parent_dir,
            base_lower,
            image_by_folder_base,
            image_by_base_global,
            excluded_images,
        )

        for i in range(0, len(items), 2):
            idx1, v1, _ = items[i]
            if i + 1 < len(items):
                idx2, v2, _ = items[i + 1]
            else:
                idx2, v2 = None, None

            v1_cs, v1_ce = cut_map.get(v1, (0.0, 0.0)) if v1 else (0.0, 0.0)
            v2_cs, v2_ce = cut_map.get(v2, (0.0, 0.0)) if v2 else (0.0, 0.0)

            row = {
                "v1": v1,
                "idx1": idx1,
                "v1_cut_start": v1_cs,
                "v1_cut_end": v1_ce,
                "v2": v2,
                "idx2": idx2,
                "v2_cut_start": v2_cs,
                "v2_cut_end": v2_ce,
                "overlay": matched_img,
                "parent_dir": parent_dir,
                "base": clean_base,
                "out": None,
                "valid": False,
                "status_reason": "",
                "unnumbered": False,
            }
            evaluate_row(row, require_overlay)
            pairs_queue.append(row)

    for fpath in unnumbered_files:
        base_stem = fpath.stem.strip(" _-.")
        matched_img = find_matching_image(
            fpath.parent,
            base_stem.lower(),
            image_by_folder_base,
            image_by_base_global,
            excluded_images,
        )
        v1_cs, v1_ce = cut_map.get(fpath, (0.0, 0.0))
        row = {
            "v1": fpath,
            "idx1": None,
            "v1_cut_start": v1_cs,
            "v1_cut_end": v1_ce,
            "v2": None,
            "idx2": None,
            "v2_cut_start": 0.0,
            "v2_cut_end": 0.0,
            "overlay": matched_img,
            "parent_dir": fpath.parent,
            "base": base_stem,
            "out": None,
            "valid": False,
            "status_reason": "",
            "unnumbered": True,
        }
        evaluate_row(row, require_overlay)
        pairs_queue.append(row)

    return pairs_queue