from pathlib import Path
from PIL import Image

# folder
FOLDER_INPUT = "input"
FOLDER_OUTPUT = "output"

# mode: "folder" atau "file"
MODE = "folder"

# kalau mode = "file"
FILE_TUNGGAL = "SKy.png"

# pengaturan potongan
JUMLAH_BAGIAN = 3
OVERLAP = 0
FORMAT_OUTPUT = "png"
KUALITAS_JPG = 90
EKSTENSI = (".png", ".jpg", ".jpeg", ".webp")


def get_target_files():
    input_dir = Path(FOLDER_INPUT)

    if MODE == "file":
        file_path = input_dir / FILE_TUNGGAL
        if not file_path.exists():
            raise FileNotFoundError(f"File tidak ditemukan: {file_path}")
        return [file_path]

    if MODE == "folder":
        if not input_dir.exists():
            raise FileNotFoundError(f"Folder input tidak ditemukan: {input_dir}")
        files = [
            f for f in sorted(input_dir.iterdir())
            if f.is_file() and f.suffix.lower() in EKSTENSI
        ]
        if not files:
            raise FileNotFoundError(f"Tidak ada gambar di folder: {input_dir}")
        return files

    raise ValueError("MODE harus 'file' atau 'folder'")


def split_image(image_path, jumlah_bagian, overlap):
    img = Image.open(image_path)
    width, height = img.size

    if jumlah_bagian < 1:
        raise ValueError("JUMLAH_BAGIAN harus >= 1")

    base_width = width // jumlah_bagian
    parts = []

    for i in range(jumlah_bagian):
        left = i * base_width
        right = width if i == jumlah_bagian - 1 else (i + 1) * base_width

        if overlap > 0:
            left = max(0, left - overlap)
            right = min(width, right + overlap)

        part = img.crop((left, 0, right, height))
        parts.append(part)

    return parts


def save_parts(parts, original_name, output_dir):
    stem = Path(original_name).stem
    target_folder = output_dir / stem
    target_folder.mkdir(parents=True, exist_ok=True)

    for idx, part in enumerate(parts, start=1):
        filename = f"{stem}_{idx}.{FORMAT_OUTPUT}"
        save_path = target_folder / filename

        if FORMAT_OUTPUT.lower() in ("jpg", "jpeg"):
            if part.mode in ("RGBA", "P"):
                part = part.convert("RGB")
            part.save(save_path, quality=KUALITAS_JPG, optimize=True)
        else:
            part.save(save_path)

        print(f"    -> {save_path}  ({part.size[0]}x{part.size[1]})")


def main():
    print("=" * 50)
    print("  Image Splitter")
    print("=" * 50)
    print(f"Mode          : {MODE}")
    print(f"Jumlah bagian : {JUMLAH_BAGIAN}")
    print(f"Overlap       : {OVERLAP} px")
    print(f"Format output : {FORMAT_OUTPUT}")
    print("-" * 50)

    try:
        files = get_target_files()
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] {e}")
        return

    output_dir = Path(FOLDER_OUTPUT)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Ditemukan {len(files)} file:\n")

    for file_path in files:
        print(f"Memproses: {file_path.name}")
        try:
            parts = split_image(file_path, JUMLAH_BAGIAN, OVERLAP)
            save_parts(parts, file_path.name, output_dir)
            print(f"  Selesai -> {len(parts)} bagian\n")
        except Exception as e:
            print(f"  [GAGAL] {e}\n")

    print("=" * 50)
    print("Selesai!")
    print(f"Hasil di: {FOLDER_OUTPUT}/")
    print("=" * 50)


if __name__ == "__main__":
    main()