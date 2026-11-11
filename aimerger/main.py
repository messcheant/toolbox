"""
main.py — GUI Batch Video Merger (CustomTkinter)
"""

from pathlib import Path
import threading
from tkinter import filedialog, messagebox
import customtkinter as ctk

from func1 import (
    DND_FILES,
    HAS_DND,
    TkinterDnD,
    evaluate_row,
    ingest_paths,
    parse_boost_db,
    parse_cut_sec,
    parse_lufs,
    parse_overlay_params,
    parse_zoom_pct,
    recompute_all_rows_validity,
    regroup_pairs,
)
from func2 import (
    HAS_RAIW,
    ensure_system_dependencies,
    get_appdata_bin_dir,
    is_ffmpeg_installed,
    setup_bundled_ffmpeg,
)
from func5 import process_and_clean_pair


class CTkDnDWindow(ctk.CTk, TkinterDnD.DnDWrapper if HAS_DND else object):
    """Menggabungkan CustomTkinter dengan fitur Drag & Drop TkinterDnD."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if HAS_DND:
            self.TkdndVersion = TkinterDnD._require(self)


class DependencySetupPopup(ctk.CTkToplevel):
    """Pop-up Modal untuk mengunduh & menyiapkan seluruh dependencies di PC baru."""

    def __init__(self, parent: "BatchVideoMergerApp"):
        super().__init__(parent)
        self.parent_app = parent
        self.title("First-Time System Setup — Tools by messcheant")
        self.geometry("500x250")
        self.resizable(False, False)
        self.configure(fg_color="#0f172a")

        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._disable_close)

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 500) // 2
        y = (sh - 250) // 2
        self.geometry(f"500x250+{x}+{y}")

        card = ctk.CTkFrame(
            self,
            fg_color="#1e293b",
            corner_radius=10,
            border_width=1,
            border_color="#38bdf8",
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            card,
            text="⚙️ Menyiapkan Dependencies Sistem & Engine",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#f8fafc",
        ).pack(pady=(16, 4))

        ctk.CTkLabel(
            card,
            text=(
                "PC ini belum memiliki komponen lengkap. Mengunduh & menginstal otomatis\n"
                "ke AppData\\Local (Hanya dilakukan 1x saat pertama kali dibuka)."
            ),
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        ).pack(pady=(0, 14))

        self.status_label = ctk.CTkLabel(
            card,
            text="Memeriksa Python Runtime, Modul Internal & FFmpeg...",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
        )
        self.status_label.pack(pady=(0, 8))

        self.pbar = ctk.CTkProgressBar(
            card,
            width=420,
            height=12,
            progress_color="#10b981",
            fg_color="#0f172a",
        )
        self.pbar.pack(pady=(0, 8))
        self.pbar.set(0.02)

        self.pct_label = ctk.CTkLabel(
            card,
            text="2%  |  Target: " + str(get_appdata_bin_dir()),
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color="#cbd5e1",
        )
        self.pct_label.pack(pady=(0, 8))

        self.retry_btn = ctk.CTkButton(
            card,
            text="🔄 Coba Unduh Lagi",
            width=150,
            height=30,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._start_download_thread,
        )

        self._start_download_thread()

    def _disable_close(self):
        pass

    def _start_download_thread(self):
        self.retry_btn.pack_forget()
        self.pbar.configure(progress_color="#10b981")
        threading.Thread(target=self._worker, daemon=True).start()

    def _update_ui(self, msg: str, progress: float):
        if not self.winfo_exists():
            return
        self.status_label.configure(text=msg, text_color="#38bdf8")
        self.pbar.set(progress)
        pct = int(progress * 100)
        self.pct_label.configure(
            text=f"{pct}%  |  Lokasi: {get_appdata_bin_dir()}"
        )

    def _worker(self):
        try:
            ensure_system_dependencies(
                status_callback=lambda m, p: self.after(0, self._update_ui, m, p),
                log_callback=lambda log_msg: self.parent_app.after(
                    0, self.parent_app.log, log_msg
                ),
            )
            self.after(400, self._on_success)
        except Exception as err:
            self.after(0, self._on_error, str(err))

    def _on_success(self):
        if self.winfo_exists():
            self.grab_release()
            self.destroy()

    def _on_error(self, err_msg: str):
        if not self.winfo_exists():
            return
        self.status_label.configure(
            text="✕ Gagal mengunduh: Pastikan koneksi internet aktif!",
            text_color="#f87171",
        )
        self.pbar.configure(progress_color="#ef4444")
        self.pct_label.configure(text=f"Detail Error: {err_msg[:65]}")
        self.retry_btn.pack(pady=(4, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)


class BatchVideoMergerApp(CTkDnDWindow):

    def __init__(self):
        super().__init__()
        self.title("Tools by messcheant")
        self.geometry("1040x780")
        self.minsize(900, 580)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#0f172a")

        # Koleksi data antrean & memori cut per path video
        self.collected_files: set[Path] = set()
        self.collected_images: set[Path] = set()
        self.excluded_images: set[Path] = set()
        self.pairs_queue: list[dict] = []
        self.video_cut_memory: dict[Path, tuple[float, float]] = {}

        # Variabel Opsi Video & Overlay
        self.reencode_var = ctk.BooleanVar(value=False)
        self.use_zoom_var = ctk.BooleanVar(value=True)
        self.zoom_pct_var = ctk.StringVar(value="2.0")

        self.use_overlay_var = ctk.BooleanVar(value=True)
        self.overlay_scale_var = ctk.StringVar(value="30.0")
        self.overlay_margin_var = ctk.StringVar(value="0")

        # Variabel Opsi Audio
        self.norm_loudness_var = ctk.BooleanVar(value=False)
        self.lufs_target_var = ctk.StringVar(value="-23.0")
        self.boost_vol_var = ctk.BooleanVar(value=False)
        self.boost_db_var = ctk.StringVar(value="10.0")

        # Variabel Preset Quick Cut (Untuk isi cepat ke semua antrean jika diperlukan)
        self.quick_cut_start_var = ctk.StringVar(value="0.0")
        self.quick_cut_end_var = ctk.StringVar(value="0.0")

        self.is_processing = False
        self._build_ui()

        self.after(250, self._check_initial_dependencies)

    def _check_initial_dependencies(self):
        if is_ffmpeg_installed():
            self.log(
                f"✅ Dependencies Sistem Lengkap (FFmpeg & FFprobe siap di: {get_appdata_bin_dir()})"
            )
        else:
            DependencySetupPopup(self)

    def _build_ui(self):
        self.main_scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="#0f172a",
            corner_radius=0,
            scrollbar_button_color="#334155",
            scrollbar_button_hover_color="#475569",
        )
        self.main_scroll.pack(fill="both", expand=True)

        # 1. HEADER BAR & TOMBOL AKSI CEPAT
        top_bar = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        top_bar.pack(fill="x", padx=16, pady=(10, 6))

        title_box = ctk.CTkFrame(top_bar, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box,
            text="Pler kuda",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#f8fafc",
        ).pack(side="left")

        ctk.CTkLabel(
            title_box,
            text=" Forensic Scan • Trim Per-Video • Stego-Breaker • Output Audit ",
            fg_color="#064e3b",
            text_color="#6ee7b7",
            corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            top_bar,
            text="Bersihkan Daftar",
            width=120,
            height=30,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.clear_all,
        ).pack(side="right")

        ctk.CTkButton(
            top_bar,
            text="+ Folder",
            width=95,
            height=30,
            fg_color="#1e293b",
            hover_color="#334155",
            border_width=1,
            border_color="#475569",
            font=ctk.CTkFont(size=12),
            command=self.browse_folder,
        ).pack(side="right", padx=6)

        ctk.CTkButton(
            top_bar,
            text="+ File Video / Gambar",
            width=155,
            height=30,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.browse_files,
        ).pack(side="right")

        # 2. ZONA DRAG & DROP CLEAN
        self.drop_frame = ctk.CTkFrame(
            self.main_scroll,
            height=60,
            fg_color="#1e293b",
            border_width=1,
            border_color="#38bdf8",
            corner_radius=8,
        )
        self.drop_frame.pack(fill="x", padx=16, pady=(2, 8))
        self.drop_frame.pack_propagate(False)

        dnd_status = (
            "📥  Tarik & Lepas (Drag & Drop) File Video + Gambar atau Folder ke Sini\n"
            "Atur potong durasi (Awal/Akhir) tepat di bawah nama masing-masing video pada antrean"
            if HAS_DND
            else "Klik tombol '+ File Video / Gambar' atau '+ Folder' di kanan atas untuk memulai"
        )
        self.drop_label = ctk.CTkLabel(
            self.drop_frame,
            text=dnd_status,
            font=ctk.CTkFont(size=12),
            text_color="#bae6fd",
        )
        self.drop_label.pack(expand=True, fill="both", padx=10, pady=4)

        if HAS_DND:
            for widget in (self, self.main_scroll, self.drop_frame, self.drop_label):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self.on_drop_files)
                except Exception:
                    pass

        # 3. DAFTAR ANTREAN VIDEO
        self.queue_frame = ctk.CTkScrollableFrame(
            self.main_scroll,
            height=255,
            fg_color="#1e293b",
            corner_radius=8,
            label_text="Daftar Antrean Video (0 Valid | 0 Tidak Valid)",
            label_fg_color="#0f172a",
            label_text_color="#e2e8f0",
        )
        self.queue_frame.pack(fill="x", padx=16, pady=(0, 8))

        # 4. PANEL PENGATURAN 3 KOLOM (RAPI & MUDAH DIGUNAKAN)
        opt_container = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        opt_container.pack(fill="x", padx=16, pady=(0, 8))
        opt_container.grid_columnconfigure((0, 1, 2), weight=1, uniform="opt_col")

        # --- KARTU 1: VIDEO & CENTER ZOOM ---
        card_vid = ctk.CTkFrame(
            opt_container,
            fg_color="#1e293b",
            corner_radius=8,
            border_width=1,
            border_color="#334155",
        )
        card_vid.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=0)

        ctk.CTkLabel(
            card_vid,
            text="🎞️  1. VIDEO & CENTER ZOOM",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
        ).pack(anchor="w", padx=12, pady=(8, 4))

        ctk.CTkFrame(card_vid, height=1, fg_color="#334155").pack(
            fill="x", padx=12, pady=(0, 8)
        )

        ctk.CTkCheckBox(
            card_vid,
            text="Samakan Resolusi & FPS",
            variable=self.reencode_var,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(0, 8))

        ctk.CTkCheckBox(
            card_vid,
            text="Aktifkan Zoom Tengah (Anti-Stego)",
            variable=self.use_zoom_var,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._toggle_zoom_controls,
        ).pack(anchor="w", padx=12, pady=(0, 6))

        zoom_input_row = ctk.CTkFrame(card_vid, fg_color="transparent")
        zoom_input_row.pack(fill="x", padx=32, pady=(0, 10))

        ctk.CTkLabel(
            zoom_input_row,
            text="Persentase Zoom:",
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
        ).pack(side="left", padx=(0, 8))

        self.zoom_pct_entry = ctk.CTkEntry(
            zoom_input_row,
            textvariable=self.zoom_pct_var,
            width=58,
            height=26,
            justify="center",
        )
        self.zoom_pct_entry.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(
            zoom_input_row,
            text="%  (Aman: 2% - 5%)",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        ).pack(side="left")

        # --- KARTU 2: WATERMARK / GAMBAR OVERLAY ---
        card_ovr = ctk.CTkFrame(
            opt_container,
            fg_color="#1e293b",
            corner_radius=8,
            border_width=1,
            border_color="#334155",
        )
        card_ovr.grid(row=0, column=1, sticky="nsew", padx=5, pady=0)

        ctk.CTkLabel(
            card_ovr,
            text="🖼️  2. WATERMARK / OVERLAY",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
        ).pack(anchor="w", padx=12, pady=(8, 4))

        ctk.CTkFrame(card_ovr, height=1, fg_color="#334155").pack(
            fill="x", padx=12, pady=(0, 8)
        )

        ctk.CTkCheckBox(
            card_ovr,
            text="Gunakan Gambar Overlay (Kanan Bawah)",
            variable=self.use_overlay_var,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_overlay_toggle,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        ovr_scale_row = ctk.CTkFrame(card_ovr, fg_color="transparent")
        ovr_scale_row.pack(fill="x", padx=32, pady=(0, 4))

        ctk.CTkLabel(
            ovr_scale_row,
            text="Ukuran Scale :",
            width=90,
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
        ).pack(side="left")

        self.overlay_scale_entry = ctk.CTkEntry(
            ovr_scale_row,
            textvariable=self.overlay_scale_var,
            width=58,
            height=26,
            justify="center",
        )
        self.overlay_scale_entry.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(
            ovr_scale_row,
            text="% dari lebar video",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        ).pack(side="left")

        ovr_margin_row = ctk.CTkFrame(card_ovr, fg_color="transparent")
        ovr_margin_row.pack(fill="x", padx=32, pady=(0, 10))

        ctk.CTkLabel(
            ovr_margin_row,
            text="Jarak Margin :",
            width=90,
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
        ).pack(side="left")

        self.overlay_margin_entry = ctk.CTkEntry(
            ovr_margin_row,
            textvariable=self.overlay_margin_var,
            width=58,
            height=26,
            justify="center",
        )
        self.overlay_margin_entry.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(
            ovr_margin_row,
            text="px dari tepi sudut",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        ).pack(side="left")

        # --- KARTU 3: PENGATURAN AUDIO & QUICK CUT ---
        card_aud = ctk.CTkFrame(
            opt_container,
            fg_color="#1e293b",
            corner_radius=8,
            border_width=1,
            border_color="#334155",
        )
        card_aud.grid(row=0, column=2, sticky="nsew", padx=(5, 0), pady=0)

        ctk.CTkLabel(
            card_aud,
            text="🔊  3. AUDIO & PRESET CUT MASSAL",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
        ).pack(anchor="w", padx=12, pady=(8, 4))

        ctk.CTkFrame(card_aud, height=1, fg_color="#334155").pack(
            fill="x", padx=12, pady=(0, 8)
        )

        aud_norm_row = ctk.CTkFrame(card_aud, fg_color="transparent")
        aud_norm_row.pack(fill="x", padx=12, pady=(0, 5))

        ctk.CTkCheckBox(
            aud_norm_row,
            text="Normalize Audio:",
            variable=self.norm_loudness_var,
            width=135,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._toggle_audio_controls,
        ).pack(side="left")

        self.lufs_entry = ctk.CTkEntry(
            aud_norm_row,
            textvariable=self.lufs_target_var,
            width=55,
            height=25,
            justify="center",
        )
        self.lufs_entry.pack(side="left", padx=(4, 4))
        ctk.CTkLabel(
            aud_norm_row, text="LUFS", font=ctk.CTkFont(size=11), text_color="#94a3b8"
        ).pack(side="left")

        aud_boost_row = ctk.CTkFrame(card_aud, fg_color="transparent")
        aud_boost_row.pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkCheckBox(
            aud_boost_row,
            text="Boost Volume:",
            variable=self.boost_vol_var,
            width=135,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._toggle_audio_controls,
        ).pack(side="left")

        self.boost_db_entry = ctk.CTkEntry(
            aud_boost_row,
            textvariable=self.boost_db_var,
            width=55,
            height=25,
            justify="center",
        )
        self.boost_db_entry.pack(side="left", padx=(4, 4))
        ctk.CTkLabel(
            aud_boost_row, text="dB", font=ctk.CTkFont(size=11), text_color="#94a3b8"
        ).pack(side="left")

        ctk.CTkFrame(card_aud, height=1, fg_color="#334155").pack(
            fill="x", padx=12, pady=(2, 5)
        )

        quick_cut_row = ctk.CTkFrame(card_aud, fg_color="transparent")
        quick_cut_row.pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(
            quick_cut_row,
            text="✂️ Isi Semua:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#fbbf24",
        ).pack(side="left", padx=(0, 4))

        ctk.CTkLabel(quick_cut_row, text="Awal", font=ctk.CTkFont(size=11)).pack(
            side="left"
        )
        ctk.CTkEntry(
            quick_cut_row,
            textvariable=self.quick_cut_start_var,
            width=38,
            height=24,
            justify="center",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=2)

        ctk.CTkLabel(quick_cut_row, text="Akhir", font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(2, 0)
        )
        ctk.CTkEntry(
            quick_cut_row,
            textvariable=self.quick_cut_end_var,
            width=38,
            height=24,
            justify="center",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            quick_cut_row,
            text="Set",
            width=38,
            height=24,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.apply_quick_cut_to_all,
        ).pack(side="left", padx=(4, 0))

        self._toggle_zoom_controls()
        self._toggle_overlay_controls()
        self._toggle_audio_controls()

        # 5. TOMBOL START EKSEKUSI BATCH
        self.run_btn = ctk.CTkButton(
            self.main_scroll,
            text="▶  MULAI PROSES PASANGAN VALID (0 PASANG)",
            height=42,
            corner_radius=8,
            fg_color="#059669",
            hover_color="#047857",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.start_batch,
        )
        self.run_btn.pack(fill="x", padx=16, pady=(0, 6))

        # 6. PROGRESS BAR & LOGS DI BAWAH TOMBOL START
        self.progress = ctk.CTkProgressBar(
            self.main_scroll, height=8, progress_color="#10b981", fg_color="#1e293b"
        )
        self.progress.pack(fill="x", padx=16, pady=(0, 6))
        self.progress.set(0)

        log_container = ctk.CTkFrame(
            self.main_scroll,
            fg_color="#1e293b",
            corner_radius=8,
            border_width=1,
            border_color="#334155",
        )
        log_container.pack(fill="x", padx=16, pady=(0, 16))

        log_header = ctk.CTkFrame(log_container, fg_color="transparent")
        log_header.pack(fill="x", padx=10, pady=(6, 2))

        ctk.CTkLabel(
            log_header,
            text="📋 Activity & Forensic Watermark Logs (Deteksi Input ➔ Trim & Stego Clean ➔ Audit Output)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e2e8f0",
        ).pack(side="left")

        ctk.CTkButton(
            log_header,
            text="Bersihkan Log",
            width=85,
            height=22,
            fg_color="#334155",
            hover_color="#475569",
            font=ctk.CTkFont(size=11),
            command=self.clear_logs,
        ).pack(side="right")

        self.log_box = ctk.CTkTextbox(
            log_container,
            height=180,
            fg_color="#090d16",
            text_color="#cbd5e1",
            corner_radius=6,
            state="disabled",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_box.pack(fill="x", padx=8, pady=(4, 8))

        engine_status = (
            "Repo Engine (remove_ai_watermarks) + FFmpeg Stego-Breaker AKTIF"
            if HAS_RAIW
            else "FFmpeg Forensic Stego-Breaker & SEI Scrub AKTIF"
        )
        self.log(f"Aplikasi siap [{engine_status}]. Masukkan file video & gambar overlay.")

    def _toggle_zoom_controls(self):
        state = "normal" if self.use_zoom_var.get() else "disabled"
        self.zoom_pct_entry.configure(state=state)

    def _on_overlay_toggle(self):
        self._toggle_overlay_controls()
        self.pairs_queue = recompute_all_rows_validity(
            self.pairs_queue, self.use_overlay_var.get()
        )
        self._refresh_queue_ui()

    def _toggle_overlay_controls(self):
        state = "normal" if self.use_overlay_var.get() else "disabled"
        self.overlay_scale_entry.configure(state=state)
        self.overlay_margin_entry.configure(state=state)

    def _toggle_audio_controls(self):
        self.lufs_entry.configure(
            state="normal" if self.norm_loudness_var.get() else "disabled"
        )
        self.boost_db_entry.configure(
            state="normal" if self.boost_vol_var.get() else "disabled"
        )

    def apply_quick_cut_to_all(self):
        """Menerapkan nilai Awal & Akhir dari Preset Massal ke seluruh video di antrean."""
        if self.is_processing or not self.pairs_queue:
            return
        cs = parse_cut_sec(self.quick_cut_start_var.get())
        ce = parse_cut_sec(self.quick_cut_end_var.get())

        for row in self.pairs_queue:
            if row.get("v1") is not None:
                row["v1_cut_start"] = cs
                row["v1_cut_end"] = ce
                self.video_cut_memory[row["v1"]] = (cs, ce)
            if row.get("v2") is not None:
                row["v2_cut_start"] = cs
                row["v2_cut_end"] = ce
                self.video_cut_memory[row["v2"]] = (cs, ce)

        self._refresh_queue_ui()
        self.log(
            f"✂️ Preset Cut diterapkan ke semua video di antrean: Awal={cs:g}s, Akhir={ce:g}s."
        )

    def log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"> {text}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def clear_logs(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def on_drop_files(self, event):
        if self.is_processing:
            return
        raw_paths = self.tk.splitlist(event.data)
        self._handle_new_paths([Path(p) for p in raw_paths])

    def browse_files(self):
        if self.is_processing:
            return
        files = filedialog.askopenfilenames(
            title="Pilih File Video & Gambar Overlay",
            filetypes=[
                (
                    "Video & Image Files",
                    "*.mp4 *.mov *.mkv *.webm *.m4v *.avi *.png *.jpg *.jpeg *.webp *.bmp",
                ),
                ("Video Files", "*.mp4 *.mov *.mkv *.webm *.m4v *.avi"),
                ("Image Files", "*.png *.jpg *.jpeg *.webp *.bmp"),
            ],
        )
        if files:
            self._handle_new_paths([Path(f) for f in files])

    def browse_folder(self):
        if self.is_processing:
            return
        folder = filedialog.askdirectory(title="Pilih Folder Video & Gambar")
        if folder:
            self._handle_new_paths([Path(folder)])

    def clear_all(self):
        if self.is_processing:
            return
        self.collected_files.clear()
        self.collected_images.clear()
        self.excluded_images.clear()
        self.pairs_queue.clear()
        self.video_cut_memory.clear()
        self._refresh_queue_ui()
        self.log("Daftar antrean dibersihkan.")

    def _sync_cut_memory_from_rows(self):
        """Menyimpan pengaturan cut dari baris yang sudah ada ke dictionary memori."""
        for row in self.pairs_queue:
            v1 = row.get("v1")
            if v1 is not None:
                self.video_cut_memory[v1] = (
                    float(row.get("v1_cut_start", 0.0)),
                    float(row.get("v1_cut_end", 0.0)),
                )
            v2 = row.get("v2")
            if v2 is not None:
                self.video_cut_memory[v2] = (
                    float(row.get("v2_cut_start", 0.0)),
                    float(row.get("v2_cut_end", 0.0)),
                )

    def _handle_new_paths(self, paths: list[Path]):
        self._sync_cut_memory_from_rows()
        added_v, added_i = ingest_paths(
            paths, self.collected_files, self.collected_images, self.excluded_images
        )
        self.pairs_queue = regroup_pairs(
            self.collected_files,
            self.collected_images,
            self.excluded_images,
            self.use_overlay_var.get(),
            existing_cuts=self.video_cut_memory,
        )
        self._refresh_queue_ui()

        valid_cnt = sum(1 for item in self.pairs_queue if item["valid"])
        invalid_cnt = len(self.pairs_queue) - valid_cnt
        if added_v > 0 or added_i > 0:
            self.log(
                f"Input ditambahkan: +{added_v} video, +{added_i} gambar | Antrean: {valid_cnt} Valid, {invalid_cnt} Tidak Valid."
            )

    def _on_video_cut_changed(
        self,
        row_index: int,
        slot_key: str,
        start_var: ctk.StringVar,
        end_var: ctk.StringVar,
    ):
        """Dipanggil secara real-time saat user mengetik detik potong Awal/Akhir di bawah chip video."""
        if not (0 <= row_index < len(self.pairs_queue)):
            return
        row = self.pairs_queue[row_index]
        cs = parse_cut_sec(start_var.get())
        ce = parse_cut_sec(end_var.get())

        row[f"{slot_key}_cut_start"] = cs
        row[f"{slot_key}_cut_end"] = ce

        vpath = row.get(slot_key)
        if vpath is not None:
            self.video_cut_memory[vpath] = (cs, ce)

    def _create_item_chip(
        self,
        parent,
        icon: str,
        file_path: Path | None,
        empty_text: str,
        on_remove_cmd,
        row_index: int | None = None,
        video_slot: str | None = None,
    ):
        """Membuat chip item. Khusus untuk video (v1/v2), menampilkan kontrol Cut
        tepat di BAWAH ikon & nama video di dalam chip yang sama.
        """
        if file_path is not None:
            chip = ctk.CTkFrame(
                parent,
                fg_color="#0f172a",
                corner_radius=6,
                border_width=1,
                border_color="#334155",
            )
            chip.pack(side="left", padx=3, pady=4)

            # Baris 1 (Atas): Ikon Video/Gambar + Nama File + Tombol Hapus (✕)
            top_line = ctk.CTkFrame(chip, fg_color="transparent")
            top_line.pack(fill="x", padx=6, pady=(3, 2))

            ctk.CTkLabel(
                top_line,
                text=f"{icon} {file_path.name}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#f1f5f9",
            ).pack(side="left", padx=(2, 6))

            ctk.CTkButton(
                top_line,
                text="✕",
                width=20,
                height=20,
                corner_radius=4,
                fg_color="#7f1d1d",
                hover_color="#991b1b",
                font=ctk.CTkFont(size=10, weight="bold"),
                command=on_remove_cmd,
            ).pack(side="right", padx=(2, 0))

            # Baris 2 (Bawah): Kontrol Cut Awal & Akhir (Khusus untuk Video 1 & Video 2)
            if video_slot in ("v1", "v2") and row_index is not None:
                ctk.CTkFrame(chip, height=1, fg_color="#1e293b").pack(
                    fill="x", padx=6, pady=(1, 2)
                )

                cut_line = ctk.CTkFrame(chip, fg_color="transparent")
                cut_line.pack(fill="x", padx=6, pady=(0, 4))

                row_data = self.pairs_queue[row_index]
                init_cs = float(row_data.get(f"{video_slot}_cut_start", 0.0))
                init_ce = float(row_data.get(f"{video_slot}_cut_end", 0.0))

                start_var = ctk.StringVar(value=f"{init_cs:g}")
                end_var = ctk.StringVar(value=f"{init_ce:g}")

                start_var.trace_add(
                    "write",
                    lambda *_, r=row_index, s=video_slot, sv=start_var, ev=end_var: self._on_video_cut_changed(
                        r, s, sv, ev
                    ),
                )
                end_var.trace_add(
                    "write",
                    lambda *_, r=row_index, s=video_slot, sv=start_var, ev=end_var: self._on_video_cut_changed(
                        r, s, sv, ev
                    ),
                )

                ctk.CTkLabel(
                    cut_line,
                    text="✂️ Awal:",
                    font=ctk.CTkFont(size=11),
                    text_color="#94a3b8",
                ).pack(side="left", padx=(0, 2))

                ctk.CTkEntry(
                    cut_line,
                    textvariable=start_var,
                    width=38,
                    height=20,
                    justify="center",
                    fg_color="#1e293b",
                    border_color="#475569",
                    font=ctk.CTkFont(size=11),
                ).pack(side="left")

                ctk.CTkLabel(
                    cut_line,
                    text="s  Akhir:",
                    font=ctk.CTkFont(size=11),
                    text_color="#94a3b8",
                ).pack(side="left", padx=(2, 2))

                ctk.CTkEntry(
                    cut_line,
                    textvariable=end_var,
                    width=38,
                    height=20,
                    justify="center",
                    fg_color="#1e293b",
                    border_color="#475569",
                    font=ctk.CTkFont(size=11),
                ).pack(side="left")

                ctk.CTkLabel(
                    cut_line,
                    text="s",
                    font=ctk.CTkFont(size=11),
                    text_color="#94a3b8",
                ).pack(side="left", padx=(2, 0))
        else:
            chip = ctk.CTkFrame(
                parent,
                fg_color="#2a161b",
                corner_radius=6,
                border_width=1,
                border_color="#7f1d1d",
            )
            chip.pack(side="left", padx=3, pady=4)

            ctk.CTkLabel(
                chip,
                text=f"{icon} [{empty_text}]",
                font=ctk.CTkFont(size=11, slant="italic"),
                text_color="#fca5a5",
            ).pack(side="left", padx=8, pady=6)

    def _refresh_queue_ui(self):
        for widget in self.queue_frame.winfo_children():
            widget.destroy()

        valid_pairs = sum(1 for item in self.pairs_queue if item["valid"])
        invalid_pairs = len(self.pairs_queue) - valid_pairs

        self.queue_frame.configure(
            label_text=f"Daftar Antrean Video ({valid_pairs} Valid Siap Diproses | {invalid_pairs} Tidak Valid)"
        )
        if not self.is_processing:
            self.run_btn.configure(
                text=f"▶  MULAI PROSES PASANGAN VALID ({valid_pairs} PASANG)"
            )

        use_ovr = self.use_overlay_var.get()

        for idx, item in enumerate(self.pairs_queue):
            is_valid = item["valid"]
            row_bg = "#162032" if is_valid else "#261922"
            row = ctk.CTkFrame(self.queue_frame, fg_color=row_bg, corner_radius=6)
            row.pack(fill="x", padx=4, pady=4)

            ctk.CTkLabel(
                row,
                text=f"#{idx + 1}",
                width=30,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#64748b",
            ).pack(side="left", padx=(8, 4), pady=6)

            self._create_item_chip(
                row,
                "🎬",
                item.get("v1"),
                "Video 1 Kosong",
                lambda i=idx: self.remove_single_item(i, "v1"),
                row_index=idx,
                video_slot="v1",
            )
            ctk.CTkLabel(
                row, text="+", font=ctk.CTkFont(size=13, weight="bold"), text_color="#64748b"
            ).pack(side="left", padx=2)
            self._create_item_chip(
                row,
                "🎬",
                item.get("v2"),
                "Video 2 Kosong",
                lambda i=idx: self.remove_single_item(i, "v2"),
                row_index=idx,
                video_slot="v2",
            )

            if use_ovr:
                ctk.CTkLabel(
                    row, text="+", font=ctk.CTkFont(size=13, weight="bold"), text_color="#64748b"
                ).pack(side="left", padx=2)
                self._create_item_chip(
                    row,
                    "🖼️",
                    item.get("overlay"),
                    "Gambar Kosong",
                    lambda i=idx: self.remove_single_item(i, "overlay"),
                )

            out_path: Path | None = item.get("out")
            out_text = f"➔  _cleaned/{out_path.name}" if out_path else "➔  (Belum Lengkap)"
            out_color = "#38bdf8" if (out_path and is_valid) else "#94a3b8"

            ctk.CTkLabel(
                row,
                text=out_text,
                anchor="w",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=out_color,
            ).pack(side="left", padx=(8, 6), pady=6)

            status_badge = ctk.CTkLabel(
                row,
                text=f" {'✓ ' if is_valid else '✕ '}{item['status_reason']} ",
                fg_color="#065f46" if is_valid else "#7f1d1d",
                text_color="#a7f3d0" if is_valid else "#fecaca",
                corner_radius=6,
                height=24,
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            status_badge.pack(side="right", padx=(4, 10), pady=4)

            can_swap = item.get("v1") is not None and item.get("v2") is not None
            ctk.CTkButton(
                row,
                text="⇅ Tukar",
                width=60,
                height=24,
                fg_color="#334155" if can_swap else "#1e293b",
                hover_color="#475569",
                state="normal" if can_swap else "disabled",
                font=ctk.CTkFont(size=11),
                command=lambda i=idx: self.swap_pair(i),
            ).pack(side="right", padx=4, pady=4)

    def swap_pair(self, index: int):
        """Menukar posisi Video 1 dan Video 2 beserta pengaturan detik Cut masing-masing."""
        if self.is_processing:
            return
        pair = self.pairs_queue[index]
        pair["v1"], pair["v2"] = pair["v2"], pair["v1"]
        pair["idx1"], pair["idx2"] = pair["idx2"], pair["idx1"]
        pair["v1_cut_start"], pair["v2_cut_start"] = (
            pair.get("v2_cut_start", 0.0),
            pair.get("v1_cut_start", 0.0),
        )
        pair["v1_cut_end"], pair["v2_cut_end"] = (
            pair.get("v2_cut_end", 0.0),
            pair.get("v1_cut_end", 0.0),
        )
        evaluate_row(pair, self.use_overlay_var.get())
        self._refresh_queue_ui()

    def remove_single_item(self, row_index: int, slot_key: str):
        if self.is_processing or not (0 <= row_index < len(self.pairs_queue)):
            return

        self._sync_cut_memory_from_rows()
        row = self.pairs_queue[row_index]
        target_path: Path | None = row.get(slot_key)
        if target_path is None:
            return

        if slot_key in ("v1", "v2"):
            self.collected_files.discard(target_path)
            self.video_cut_memory.pop(target_path, None)
            row[slot_key] = None
            row["idx1" if slot_key == "v1" else "idx2"] = None
            row[f"{slot_key}_cut_start"] = 0.0
            row[f"{slot_key}_cut_end"] = 0.0
            self.log(f"Video dihapus dari Baris #{row_index + 1}: {target_path.name}")
        elif slot_key == "overlay":
            row["overlay"] = None
            if not any(r.get("overlay") == target_path for r in self.pairs_queue):
                self.collected_images.discard(target_path)
                self.excluded_images.add(target_path)
            self.log(f"Overlay dihapus dari Baris #{row_index + 1}: {target_path.name}")

        self.pairs_queue = recompute_all_rows_validity(
            self.pairs_queue, self.use_overlay_var.get()
        )
        self._refresh_queue_ui()

    def start_batch(self):
        if self.is_processing:
            return

        if not is_ffmpeg_installed():
            DependencySetupPopup(self)
            return

        self._sync_cut_memory_from_rows()
        self.pairs_queue = recompute_all_rows_validity(
            self.pairs_queue, self.use_overlay_var.get()
        )
        self._refresh_queue_ui()

        valid_queue = [item for item in self.pairs_queue if item["valid"]]
        invalid_count = len(self.pairs_queue) - len(valid_queue)

        if not valid_queue:
            warn_detail = (
                "\n\nPastikan setiap baris memiliki 2 video (misal ayam1.mp4 + ayam2.mp4) dan 1 gambar overlay (ayam.jpg)."
                if self.use_overlay_var.get()
                else "\n\nPastikan setiap baris memiliki 2 video berpasangan."
            )
            messagebox.showwarning(
                "Tidak Ada Pasangan Valid",
                "Belum ada pasangan video berstatus VALID yang siap diproses!" + warn_detail,
            )
            return

        if invalid_count > 0:
            self.log(f"INFO: Melewati {invalid_count} baris yang berstatus TIDAK VALID.")

        self.is_processing = True
        self.run_btn.configure(
            state="disabled",
            text="⏳  SEDANG MEMPROSES (SCAN INPUT ➔ TRIM & STEGO CLEAN ➔ AUDIT OUTPUT)...",
        )
        self.progress.set(0)

        threading.Thread(
            target=self._batch_worker,
            args=(valid_queue, invalid_count),
            daemon=True,
        ).start()

    def _batch_worker(self, valid_queue: list[dict], invalid_count: int):
        total = len(valid_queue)
        success_count = 0

        try:
            setup_bundled_ffmpeg()
            reencode = self.reencode_var.get()
            use_zoom = self.use_zoom_var.get()
            zoom_pct = parse_zoom_pct(self.zoom_pct_var.get())
            norm_loudness = self.norm_loudness_var.get()
            target_lufs = parse_lufs(self.lufs_target_var.get())
            boost_vol = self.boost_vol_var.get()
            boost_db = parse_boost_db(self.boost_db_var.get())
            use_overlay = self.use_overlay_var.get()
            scale_pct, margin_px = parse_overlay_params(
                self.overlay_scale_var.get(), self.overlay_margin_var.get()
            )

            for idx, pair in enumerate(valid_queue, start=1):
                v1: Path = pair["v1"]
                v2: Path = pair["v2"]
                v1_cs = float(pair.get("v1_cut_start", 0.0))
                v1_ce = float(pair.get("v1_cut_end", 0.0))
                v2_cs = float(pair.get("v2_cut_start", 0.0))
                v2_ce = float(pair.get("v2_cut_end", 0.0))

                overlay_img_path: Path | None = (
                    pair.get("overlay") if use_overlay else None
                )
                out_path: Path = pair["out"]

                if use_overlay and (
                    overlay_img_path is None or not overlay_img_path.is_file()
                ):
                    self.after(
                        0,
                        self.log,
                        f"[{idx}/{total}] ⚠️ DILEWATI: Gambar overlay '{pair['base']}' tidak ditemukan!",
                    )
                    self.after(0, self.progress.set, idx / total)
                    continue

                try:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    log_features = []
                    if v1_cs > 0 or v1_ce > 0:
                        log_features.append(f"Cut V1(-{v1_cs:g}s/-{v1_ce:g}s)")
                    if v2_cs > 0 or v2_ce > 0:
                        log_features.append(f"Cut V2(-{v2_cs:g}s/-{v2_ce:g}s)")
                    if use_zoom and zoom_pct > 0:
                        log_features.append(f"Center Zoom {zoom_pct:g}%")
                    if use_overlay and overlay_img_path:
                        log_features.append(
                            f"Overlay {overlay_img_path.name} ({scale_pct:g}%, {margin_px}px)"
                        )
                    if norm_loudness:
                        log_features.append(f"LUFS {target_lufs}")
                    if boost_vol:
                        log_features.append(f"Boost {boost_db:+.1f}dB")

                    feat_str = f" [{' | '.join(log_features)}]" if log_features else ""
                    self.after(
                        0,
                        self.log,
                        f"━━━ [{idx}/{total}] Memproses {v1.name} + {v2.name}{feat_str} ━━━",
                    )

                    verify_status = process_and_clean_pair(
                        v1=v1,
                        v2=v2,
                        output=out_path,
                        reencode=reencode,
                        apply_loudnorm=norm_loudness,
                        target_lufs=target_lufs,
                        apply_boost=boost_vol,
                        boost_db=boost_db,
                        overlay_image=overlay_img_path,
                        overlay_scale=scale_pct,
                        overlay_margin=margin_px,
                        apply_zoom=use_zoom,
                        zoom_pct=zoom_pct,
                        v1_cut_start=v1_cs,
                        v1_cut_end=v1_ce,
                        v2_cut_start=v2_cs,
                        v2_cut_end=v2_ce,
                        log_callback=lambda msg: self.after(0, self.log, msg),
                    )

                    success_count += 1
                    self.after(
                        0,
                        self.log,
                        f"[{idx}/{total}] ✓ SELESAI ({verify_status}): {out_path}",
                    )
                except Exception as e:
                    self.after(
                        0, self.log, f"[{idx}/{total}] ✕ GAGAL pada {out_path.name}: {e}"
                    )

                self.after(0, self.progress.set, idx / total)
        except Exception as fatal_err:
            self.after(0, self.log, f"✕ ERROR SISTEM: {fatal_err}")
        finally:
            self.after(
                0, self._finish_batch, success_count, total, invalid_count
            )

    def _finish_batch(self, success_count: int, total: int, invalid_count: int):
        self.is_processing = False
        valid_pairs = sum(1 for item in self.pairs_queue if item["valid"])
        self.run_btn.configure(
            state="normal",
            text=f"▶  MULAI PROSES PASANGAN VALID ({valid_pairs} PASANG)",
        )
        skipped_info = (
            f"\nBaris Tidak Valid (Dilewati): {invalid_count} baris."
            if invalid_count > 0
            else ""
        )
        messagebox.showinfo(
            "Batch Selesai",
            f"Proses Batch & Forensic Cleaning Selesai!\n\n"
            f"Berhasil & Terverifikasi: {success_count} dari {total} pasang video valid.{skipped_info}\n"
            f"Semua file tersimpan di sub-folder '_cleaned' masing-masing.",
        )


if __name__ == "__main__":
    app = BatchVideoMergerApp()
    app.mainloop()