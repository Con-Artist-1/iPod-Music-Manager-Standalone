"""
iPod Music Manager — Application UI
Main application class with glassmorphism UI, file browser, sync controls.
"""

import os
import sys
import json
import threading
import queue
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import ctypes

from utils import (
    __version__, __title__, CONFIG_PATH,
    AUDIO_EXTENSIONS, ALL_AUDIO_EXTENSIONS, IPOD_COMPATIBLE,
    resource_path, find_ffmpeg, scan_source_folder, estimate_transcoded_size,
    get_disk_usage, format_size, extract_thumbnail_ppm,
    scan_ipod_existing, get_ipod_safe_key, detect_ipod_drives,
)
from database import build_itunes_db
from sync_engine import sync_to_ipod
from voiceover import build_voiceover
from ui_theme import COLORS, ToolTip, setup_styles


class AntigravityApp:

    BITRATES = ["64", "96", "128", "160", "192", "256", "320"]
    FORMATS  = ["MP3", "AAC"]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{__title__} v{__version__}")
        self.root.geometry("1200x700")
        self.root.minsize(1050, 680)
        self.root.configure(bg=COLORS["BG_DARK"])
        
        logo_path = resource_path("logo.ico")
        if os.path.isfile(logo_path):
            self.root.iconbitmap(logo_path)

        # Dark title bar
        try:
            self.root.update()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

        self.ffmpeg_path = find_ffmpeg()
        self._drive_paths = {}
        self._source_files = []
        self._estimated_size = 0
        self._new_file_count = 0
        self._existing_count = 0
        self.view_mode = "LIST"
        self._collapsed_folders_setting = []

        # Cache for iPod existing scan (avoid re-scanning on every checkbox)
        self._ipod_scan_cache = {}
        self._ipod_scan_cache_drive = None

        # Thumbnail system
        self._thumbnail_cache = {}  # path -> tk.PhotoImage
        self._thumbnail_queue = queue.Queue()
        self._thumbnail_thread_running = True
        self._folder_grid_containers = {}
        self._thumbnail_thread = threading.Thread(target=self._thumbnail_worker, daemon=True)
        self._thumbnail_thread.start()

        self._setup_styles()
        self._build_ui()
        self._refresh_drives()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(100, self._load_config)

    def _thumbnail_worker(self):
        """Worker thread to extract thumbnails sequentially."""
        while self._thumbnail_thread_running:
            try:
                path, cb_widget = self._thumbnail_queue.get(timeout=1)
                
                # Check cache first
                if path in self._thumbnail_cache:
                    ppm_data = self._thumbnail_cache[path]
                else:
                    ppm_data = extract_thumbnail_ppm(path, self.ffmpeg_path, size=140)
                    if ppm_data:
                        self._thumbnail_cache[path] = ppm_data
                
                # Update UI safely
                if ppm_data:
                    def update_ui(w=cb_widget, d=ppm_data):
                        try:
                            # PhotoImage must be kept alive, store on widget
                            img = tk.PhotoImage(data=d)
                            w.img_ref = img
                            
                            # Clean up placeholder text if it exists and reset dimensions to pixel count instead of character count
                            w.configure(image=img, text="", compound=tk.CENTER, width=140, height=140)
                        except Exception:
                            pass
                    self.root.after(0, update_ui)
            except queue.Empty:
                pass
            except Exception:
                pass

    def _setup_styles(self):
        style = ttk.Style()
        setup_styles(style)


    def _build_ui(self):
        _font = "Segoe UI Variable Display"

        # ═══════════════════════════════════════════════════════════════
        #  ROOT — grid layout (replaces PanedWindow for zero-lag resize)
        # ═══════════════════════════════════════════════════════════════
        root_frame = tk.Frame(self.root, bg=COLORS["BG_DARK"])
        root_frame.pack(fill=tk.BOTH, expand=True)
        root_frame.columnconfigure(0, minsize=390)
        root_frame.columnconfigure(1, weight=1)
        root_frame.rowconfigure(1, weight=1)

        # ── Header bar ───────────────────────────────────────────────
        header = tk.Frame(root_frame, bg=COLORS["BG_DARK"])
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=24, pady=(16, 0))

        logo_path = resource_path("logo.ico")
        if os.path.isfile(logo_path):
            try:
                self._header_img = tk.PhotoImage(file=logo_path)
                self._header_img_small = self._header_img.subsample(8, 8)
                tk.Label(header, image=self._header_img_small,
                         bg=COLORS["BG_DARK"]).pack(side=tk.LEFT, padx=(0, 10))
            except Exception:
                tk.Label(header, text="\u266B", font=(_font, 20),
                         bg=COLORS["BG_DARK"], fg=COLORS["ACCENT"]).pack(side=tk.LEFT, padx=(0, 10))
        else:
            tk.Label(header, text="\u266B", font=(_font, 20),
                     bg=COLORS["BG_DARK"], fg=COLORS["ACCENT"]).pack(side=tk.LEFT, padx=(0, 10))

        title_col = tk.Frame(header, bg=COLORS["BG_DARK"])
        title_col.pack(side=tk.LEFT)
        tk.Label(title_col, text="iPod Music Manager", font=(_font, 18, "bold"),
                 bg=COLORS["BG_DARK"], fg=COLORS["FG_BRIGHT"]).pack(anchor="w")
        sub_text = f"iPod Shuffle 4G Sync  \u2022  v{__version__}"
        if not self.ffmpeg_path:
            sub_text += "  \u2022  \u26A0 ffmpeg not found"
        tk.Label(title_col, text=sub_text, font=(_font, 9),
                 bg=COLORS["BG_DARK"], fg=COLORS["FG_DIM"]).pack(anchor="w")

        # Accent line under header
        tk.Frame(root_frame, bg=COLORS["ACCENT_GLOW"], height=1).grid(
            row=0, column=0, columnspan=2, sticky="sew", padx=24)

        # ═══════════════════════════════════════════════════════════════
        #  LEFT PANEL — Controls (scrollable, debounced for performance)
        # ═══════════════════════════════════════════════════════════════
        left_outer = tk.Frame(root_frame, bg=COLORS["BG_DARK"])
        left_outer.grid(row=1, column=0, sticky="nsew", padx=(16, 0), pady=(8, 16))

        self._left_canvas = tk.Canvas(left_outer, bg=COLORS["BG_DARK"],
                                      highlightthickness=0, bd=0)
        left_scroll = ttk.Scrollbar(left_outer, orient=tk.VERTICAL,
                                     command=self._left_canvas.yview,
                                     style="Custom.Vertical.TScrollbar")
        left_inner = tk.Frame(self._left_canvas, bg=COLORS["BG_DARK"])

        # Debounced scrollregion update (prevents lag)
        self._left_resize_id = None
        def _on_left_configure(e):
            if self._left_resize_id:
                self.root.after_cancel(self._left_resize_id)
            self._left_resize_id = self.root.after(
                16, lambda: self._left_canvas.configure(
                    scrollregion=self._left_canvas.bbox("all")))
        left_inner.bind("<Configure>", _on_left_configure)

        self._left_canvas_win = self._left_canvas.create_window(
            (0, 0), window=left_inner, anchor="nw")

        self._left_cw_resize_id = None
        def _on_left_canvas_resize(e):
            if self._left_cw_resize_id:
                self.root.after_cancel(self._left_cw_resize_id)
            self._left_cw_resize_id = self.root.after(
                16, lambda w=e.width: self._left_canvas.itemconfig(
                    self._left_canvas_win, width=w))
        self._left_canvas.bind("<Configure>", _on_left_canvas_resize)

        self._left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        left = tk.Frame(left_inner, bg=COLORS["BG_DARK"])
        left.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        # ── iPod Drive ────────────────────────────────────────────────
        ttk.Label(left, text="IPOD DRIVE", style="SectionTitle.TLabel").pack(anchor="w", pady=(4, 3))
        row1 = tk.Frame(left, bg=COLORS["BG_DARK"])
        row1.pack(fill=tk.X, pady=(0, 10))
        self.drive_var = tk.StringVar()
        self.drive_combo = ttk.Combobox(row1, textvariable=self.drive_var, state="readonly",
                                         style="Dark.TCombobox", font=(_font, 10))
        self.drive_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.drive_combo.bind("<<ComboboxSelected>>", lambda e: self._recalculate())
        self.btn_refresh = ttk.Button(row1, text="\u21BB", style="Small.TButton",
                                      command=self._refresh_drives)
        self.btn_refresh.pack(side=tk.LEFT, padx=(0, 2))
        ToolTip(self.btn_refresh, "Refresh external drives list")
        self.btn_browse_drive = ttk.Button(row1, text="...", style="Small.TButton",
                                           command=self._browse_drive)
        self.btn_browse_drive.pack(side=tk.LEFT)
        ToolTip(self.btn_browse_drive, "Manually browse for iPod drive path")

        # ── Music Source ──────────────────────────────────────────────
        ttk.Label(left, text="MUSIC SOURCE", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 3))
        row2 = tk.Frame(left, bg=COLORS["BG_DARK"])
        row2.pack(fill=tk.X, pady=(0, 10))
        self.music_var = tk.StringVar()
        self.music_entry = tk.Entry(row2, textvariable=self.music_var, font=(_font, 10),
                                     bg=COLORS["BG_INPUT"], fg=COLORS["FG_TEXT"], insertbackground=COLORS["FG_TEXT"],
                                     relief="flat", bd=4, state="readonly",
                                     readonlybackground=COLORS["BG_INPUT"])
        self.music_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.btn_browse_music = ttk.Button(row2, text="Browse\u2026", style="Small.TButton",
                                           command=self._browse_music)
        self.btn_browse_music.pack(side=tk.LEFT)
        ToolTip(self.btn_browse_music, "Select the parent folder of your playlists/music")

        # ── Separator ────────────────────────────────────────────────
        tk.Frame(left, bg=COLORS["BORDER"], height=1).pack(fill=tk.X, pady=(2, 10))

        # ── Transcoding Card ─────────────────────────────────────────
        tc_card = tk.Frame(left, bg=COLORS["BG_CARD"], highlightbackground=COLORS["BORDER"],
                           highlightthickness=1)
        tc_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(tc_card, text="\u2699  Transcoding", font=(_font, 10, "bold"),
                 bg=COLORS["BG_CARD"], fg=COLORS["ACCENT"]).pack(anchor="w", padx=12, pady=(10, 6))

        tc_row = tk.Frame(tc_card, bg=COLORS["BG_CARD"])
        tc_row.pack(fill=tk.X, padx=12, pady=(0, 4))
        tk.Label(tc_row, text="Format", font=(_font, 10),
                 bg=COLORS["BG_CARD"], fg=COLORS["FG_DIM"]).pack(side=tk.LEFT, padx=(0, 4))
        self.format_var = tk.StringVar(value="MP3")
        fmt_combo = ttk.Combobox(tc_row, textvariable=self.format_var, values=self.FORMATS,
                                  state="readonly" if self.ffmpeg_path else "disabled",
                                  style="Dark.TCombobox", font=(_font, 10), width=5)
        fmt_combo.pack(side=tk.LEFT, padx=(0, 12))
        fmt_combo.bind("<<ComboboxSelected>>", lambda e: self._recalculate())

        tk.Label(tc_row, text="Bitrate", font=(_font, 10),
                 bg=COLORS["BG_CARD"], fg=COLORS["FG_DIM"]).pack(side=tk.LEFT, padx=(0, 4))
        self.bitrate_var = tk.StringVar(value="128")
        br_combo = ttk.Combobox(tc_row, textvariable=self.bitrate_var, values=self.BITRATES,
                                 state="readonly" if self.ffmpeg_path else "disabled",
                                 style="Dark.TCombobox", font=(_font, 10), width=5)
        br_combo.pack(side=tk.LEFT, padx=(0, 2))
        br_combo.bind("<<ComboboxSelected>>", lambda e: self._recalculate())
        tk.Label(tc_row, text="kbps", font=(_font, 9),
                 bg=COLORS["BG_CARD"], fg=COLORS["FG_DIM"]).pack(side=tk.LEFT)

        tc_opts = tk.Frame(tc_card, bg=COLORS["BG_CARD"])
        tc_opts.pack(fill=tk.X, padx=12, pady=(0, 10))
        self.convert_all_var = tk.BooleanVar(value=False)
        self.convert_check = tk.Checkbutton(tc_opts, text="Force re-sync",
                                             variable=self.convert_all_var, font=(_font, 9),
                                             bg=COLORS["BG_CARD"], fg=COLORS["FG_TEXT"],
                                             selectcolor=COLORS["BG_INPUT"],
                                             activebackground=COLORS["BG_CARD"],
                                             command=self._recalculate)
        self.convert_check.pack(side=tk.LEFT, padx=(0, 10))
        ToolTip(self.convert_check,
                "Re-transcode files already on iPod (e.g. after changing bitrate)")

        self.voiceover_var = tk.BooleanVar(value=True)
        self.voiceover_check = tk.Checkbutton(tc_opts, text="VoiceOver",
                                               variable=self.voiceover_var, font=(_font, 9),
                                               bg=COLORS["BG_CARD"], fg=COLORS["FG_TEXT"],
                                               selectcolor=COLORS["BG_INPUT"],
                                               activebackground=COLORS["BG_CARD"],
                                               command=self._recalculate)
        self.voiceover_check.pack(side=tk.LEFT)

        if not self.ffmpeg_path:
            self.convert_check.configure(state="disabled")

        # ── Space Dashboard Card ─────────────────────────────────────
        sd_card = tk.Frame(left, bg=COLORS["BG_CARD"], highlightbackground=COLORS["BORDER"],
                           highlightthickness=1)
        sd_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(sd_card, text="\U0001F4CA  Space Dashboard", font=(_font, 10, "bold"),
                 bg=COLORS["BG_CARD"], fg=COLORS["ACCENT"]).pack(anchor="w", padx=12, pady=(10, 6))

        stats_frame = tk.Frame(sd_card, bg=COLORS["BG_CARD"])
        stats_frame.pack(fill=tk.X, padx=12, pady=(0, 4))
        stats_frame.columnconfigure(1, weight=1)

        def add_stat(row, label_text, var_name):
            tk.Label(stats_frame, text=label_text, font=(_font, 9),
                     bg=COLORS["BG_CARD"], fg=COLORS["FG_DIM"]).grid(
                row=row, column=0, sticky="w", padx=(0, 6), pady=1)
            lbl = tk.Label(stats_frame, text="--", font=(_font, 10, "bold"),
                           bg=COLORS["BG_CARD"], fg=COLORS["ACCENT"])
            lbl.grid(row=row, column=1, sticky="w", pady=1)
            setattr(self, var_name, lbl)

        add_stat(0, "Source:", "_lbl_source_size")
        add_stat(1, "Files:", "_lbl_file_count")
        add_stat(2, "Output:", "_lbl_estimated_size")
        add_stat(3, "Playlists:", "_lbl_playlist_count")
        add_stat(4, "Free:", "_lbl_free_space")
        add_stat(5, "After:", "_lbl_remaining")

        bar_frame = tk.Frame(sd_card, bg=COLORS["BG_CARD"])
        bar_frame.pack(fill=tk.X, padx=12, pady=(4, 2))
        self.space_pct_var = tk.DoubleVar(value=0)
        self.space_bar = ttk.Progressbar(bar_frame, variable=self.space_pct_var,
                                          maximum=100,
                                          style="space.Horizontal.TProgressbar")
        self.space_bar.pack(fill=tk.X)
        self.space_status_label = tk.Label(sd_card, text="", font=(_font, 9),
                                           bg=COLORS["BG_CARD"], fg=COLORS["FG_DIM"])
        self.space_status_label.pack(anchor="w", padx=12, pady=(0, 10))

        # ── Progress + Actions ────────────────────────────────────────
        tk.Frame(left, bg=COLORS["BG_DARK"], height=6).pack(fill=tk.X)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(left, variable=self.progress_var,
                                             maximum=100,
                                             style="green.Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=(0, 3))
        self.progress_label = ttk.Label(left, text="", style="Subtitle.TLabel")
        self.progress_label.pack(anchor="w", pady=(0, 6))

        btn_row = tk.Frame(left, bg=COLORS["BG_DARK"])
        btn_row.pack(fill=tk.X, pady=(0, 4))
        self.sync_btn = ttk.Button(btn_row, text="\u25B6  Sync to iPod",
                                    style="Accent.TButton", command=self._start_sync)
        self.sync_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ToolTip(self.sync_btn, "Transcode and upload checked tracks to iPod")

        self.rebuild_btn = ttk.Button(btn_row, text="\u2699 Rebuild",
                                       style="Secondary.TButton",
                                       command=self._start_rebuild_only)
        self.rebuild_btn.pack(side=tk.LEFT, padx=(0, 4))
        ToolTip(self.rebuild_btn, "Scan iPod contents and rebuild database only")

        self.wipe_btn = ttk.Button(btn_row, text="\u2717 Wipe",
                                    style="Danger.TButton", command=self._start_wipe)
        self.wipe_btn.pack(side=tk.LEFT)
        ToolTip(self.wipe_btn,
                "Delete ALL music, VoiceOver cache, and database from iPod")

        self.status_label = ttk.Label(left,
            text="Select iPod drive and music folder to begin.",
            style="Subtitle.TLabel", wraplength=350)
        self.status_label.pack(anchor="w", pady=(4, 0))

        # ═══════════════════════════════════════════════════════════════
        #  RIGHT PANEL — Music Selection + Console (expands)
        # ═══════════════════════════════════════════════════════════════
        right_outer = tk.Frame(root_frame, bg=COLORS["BG_DARK"])
        right_outer.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=(8, 16))

        right = tk.Frame(right_outer, bg=COLORS["BG_DARK"])
        right.pack(fill=tk.BOTH, expand=True, padx=4)

        # ── Selection Panel ──────────────────────────────────────────
        sel_border = tk.Frame(right, bg=COLORS["BORDER"], bd=0)
        sel_border.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        sel_outer = tk.Frame(sel_border, bg=COLORS["BG_PANEL"])
        sel_outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Search bar
        search_frame = tk.Frame(sel_outer, bg=COLORS["BG_CARD"])
        search_frame.pack(fill=tk.X)
        tk.Label(search_frame, text=" \u2315", font=(_font, 12),
                 bg=COLORS["BG_CARD"], fg=COLORS["FG_DIM"]).pack(side=tk.LEFT, padx=(8, 0))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *a: self._apply_search())
        search_entry = tk.Entry(search_frame, textvariable=self._search_var,
                                font=(_font, 10),
                                bg=COLORS["BG_INPUT"], fg=COLORS["FG_TEXT"],
                                insertbackground=COLORS["FG_TEXT"],
                                relief="flat", bd=4)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)

        self.btn_view_toggle = ttk.Button(search_frame, text="\u2630 List",
                                          style="Small.TButton",
                                          command=self._toggle_view_mode, width=8)
        self.btn_view_toggle.pack(side=tk.RIGHT, padx=4, pady=4)
        ToolTip(self.btn_view_toggle, "Toggle between List view and Grid view")

        # Header row
        self.sel_hdr = tk.Frame(sel_outer, bg=COLORS["BG_CARD"])
        self.sel_hdr.pack(fill=tk.X)
        tk.Label(self.sel_hdr, text="  Playlist / Track",
                 font=(_font, 10, "bold"),
                 bg=COLORS["BG_CARD"], fg=COLORS["FG_TEXT"], anchor="w").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=3)
        tk.Label(self.sel_hdr, text="Size", font=(_font, 10, "bold"),
                 bg=COLORS["BG_CARD"], fg=COLORS["FG_TEXT"], width=10, anchor="e").pack(
            side=tk.RIGHT, padx=8, pady=3)

        # Scrollable canvas for file list
        self._sel_canvas = tk.Canvas(sel_outer, bg=COLORS["BG_PANEL"],
                                     highlightthickness=0, bd=0)
        sel_scroll = ttk.Scrollbar(sel_outer, orient=tk.VERTICAL,
                                    command=self._sel_canvas.yview,
                                    style="Custom.Vertical.TScrollbar")
        self._sel_inner = tk.Frame(self._sel_canvas, bg=COLORS["BG_PANEL"])

        self._sel_inner_resize_id = None
        def _on_sel_inner_configure(e):
            if self._sel_inner_resize_id:
                self.root.after_cancel(self._sel_inner_resize_id)
            self._sel_inner_resize_id = self.root.after(
                16, lambda: self._sel_canvas.configure(
                    scrollregion=self._sel_canvas.bbox("all")))
        self._sel_inner.bind("<Configure>", _on_sel_inner_configure)
        self._sel_canvas_window_id = self._sel_canvas.create_window(
            (0, 0), window=self._sel_inner, anchor="nw")
        self._sel_canvas.configure(yscrollcommand=sel_scroll.set)
        # Debounced right panel canvas resize
        self._sel_resize_id = None
        def _on_sel_canvas_resize(e):
            if self._sel_resize_id:
                self.root.after_cancel(self._sel_resize_id)
            self._sel_resize_id = self.root.after(
                16, lambda w=e.width: self._sel_canvas.itemconfig(
                    self._sel_canvas_window_id, width=w))
        self._sel_canvas.bind("<Configure>", _on_sel_canvas_resize)

        sel_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._sel_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Data structures for the checkbox panel
        self._file_map = {}
        self._file_vars = {}
        self._folder_vars = {}
        self._folder_file_paths = {}
        self._folder_content_frames = {}
        self._folder_expanded = {}
        self._folder_toggle_btns = {}
        self._folder_header_frames = {}
        self._all_file_rows = {}

        # Select All New button
        tree_btn_row = tk.Frame(right, bg=COLORS["BG_DARK"])
        tree_btn_row.pack(fill=tk.X, pady=(0, 6))
        self.btn_select_new = ttk.Button(tree_btn_row,
                                         text="\u271a Select All New",
                                         style="Small.TButton",
                                         command=self._select_all_new)
        self.btn_select_new.pack(side=tk.LEFT)
        ToolTip(self.btn_select_new,
                "Read iPod and auto-check only songs you haven't synced yet")

        # ── Console Log ──────────────────────────────────────────────
        log_border = tk.Frame(right, bg=COLORS["BORDER"], bd=0)
        log_border.pack(fill=tk.X)
        log_inner = tk.Frame(log_border, bg=COLORS["BG_PANEL"])
        log_inner.pack(fill=tk.BOTH, expand=False, padx=1, pady=1)

        self.log_text = tk.Text(log_inner, bg=COLORS["BG_PANEL"], fg=COLORS["FG_DIM"],
                                font=("Consolas", 9), wrap=tk.WORD,
                                insertbackground=COLORS["FG_TEXT"], relief="flat",
                                padx=10, pady=6, state=tk.DISABLED, height=7,
                                selectbackground=COLORS["ACCENT"],
                                selectforeground=COLORS["BG_DARK"])
        scrollbar = tk.Scrollbar(log_inner, command=self.log_text.yview,
                                  bg=COLORS["BG_INPUT"], troughcolor=COLORS["BG_PANEL"],
                                  activebackground=COLORS["ACCENT"], width=8,
                                  relief="flat")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Mouse wheel scrolling ─────────────────────────────────────
        def _global_mousewheel(event):
            w_path = str(event.widget)
            if w_path.startswith(str(self._sel_canvas)):
                self._sel_canvas.yview_scroll(
                    int(-1 * (event.delta / 120)), "units")
            elif w_path.startswith(str(self._left_canvas)):
                self._left_canvas.yview_scroll(
                    int(-1 * (event.delta / 120)), "units")
            elif w_path.startswith(str(self.log_text)):
                self.log_text.yview_scroll(
                    int(-1 * (event.delta / 120)), "units")
        self.root.bind_all("<MouseWheel>", _global_mousewheel)

    def _refresh_drives(self):
        drives = detect_ipod_drives()
        self._drive_paths = {}
        display_list = []
        for path, label in drives:
            display_list.append(label)
            self._drive_paths[label] = path
        self.drive_combo["values"] = display_list
        if display_list:
            self.drive_combo.current(0)
            self._log(f"Detected {len(display_list)} iPod drive(s)")
        else:
            self.drive_var.set("")
            self._log("No iPod drives detected. Use Browse to select manually.")
        self._ipod_scan_cache = {}
        self._ipod_scan_cache_drive = None
        self._recalculate()

    def _browse_drive(self):
        path = filedialog.askdirectory(title="Select iPod Root Directory")
        if path:
            path = os.path.normpath(path)
            label = f"Custom: {path}"
            vals = [v for v in self.drive_combo["values"] if not v.startswith("Custom:")]
            vals.append(label)
            self.drive_combo["values"] = vals
            self._drive_paths[label] = path
            self.drive_var.set(label)
            self._ipod_scan_cache = {}
            self._ipod_scan_cache_drive = None
            self._recalculate()

    def _browse_music(self):
        path = filedialog.askdirectory(title="Select Music Source Folder")
        if path:
            self.music_var.set(os.path.normpath(path))
            self._log(f"Music source: {path}")
            self._populate_tree()
            self._recalculate()

    def _populate_tree(self):
        # Clear existing widgets
        for w in self._sel_inner.winfo_children():
            w.destroy()
        
        # Clear thumbnail queue to avoid backlogs on quick refreshes
        while not self._thumbnail_queue.empty():
            try:
                self._thumbnail_queue.get_nowait()
            except queue.Empty:
                break

        # Save old states before purging to preserve checks across toggles/refreshes
        old_vars = {p: v.get() for p, v in self._file_vars.items()}
        old_folders = {fn: v.get() for fn, v in self._folder_vars.items()}

        self._file_map.clear()
        self._file_vars.clear()
        self._folder_vars.clear()
        self._folder_file_paths.clear()
        self._folder_content_frames.clear()
        self._folder_grid_containers.clear()
        self._folder_expanded.clear()
        self._folder_toggle_btns.clear()
        self._folder_header_frames.clear()
        self._all_file_rows.clear()

        music_path = self.music_var.get()
        if not music_path or not os.path.isdir(music_path):
            return

        all_files = scan_source_folder(music_path)
        folders = {}
        for f in all_files:
            folder = f["folder"] if f["folder"] else "Root (All Songs)"
            if folder not in folders:
                folders[folder] = []
            folders[folder].append(f)

        if self.view_mode == "LIST":
            self._build_list_view(folders, old_vars, old_folders)
            if hasattr(self, "btn_view_toggle"):
                self.btn_view_toggle.configure(text="\u2630 List")
            if hasattr(self, "sel_hdr"):
                self.sel_hdr.pack(fill=tk.X, before=self._sel_canvas)
        else:
            self._build_grid_view(folders, old_vars, old_folders)
            if hasattr(self, "btn_view_toggle"):
                self.btn_view_toggle.configure(text="\u25A6 Grid")
            if hasattr(self, "sel_hdr"):
                self.sel_hdr.pack_forget()

        # Re-apply any active search
        self._apply_search()
        
        # Reset scroll position
        self._sel_canvas.yview_moveto(0)

    def _build_list_view(self, folders, old_vars, old_folders):
        """Ultra-fast default render with no thumbnails."""
        for folder_name in sorted(folders.keys()):
            folder_var = tk.BooleanVar(value=old_folders.get(folder_name, True))
            self._folder_vars[folder_name] = folder_var
            self._folder_file_paths[folder_name] = []
            self._folder_expanded[folder_name] = True

            folder_frame = tk.Frame(self._sel_inner, bg=COLORS["BG_CARD"])
            folder_frame.pack(fill=tk.X, padx=8, pady=(16, 4))
            self._folder_header_frames[folder_name] = folder_frame

            folder_size = sum(f["size"] for f in folders[folder_name])

            toggle_btn = tk.Label(folder_frame, text="\u25BC", font=("Segoe UI Variable Display", 11),
                                  bg=COLORS["BG_CARD"], fg=COLORS["ACCENT"], cursor="hand2", width=2)
            toggle_btn.pack(side=tk.LEFT, padx=(4, 0))
            toggle_btn.bind("<Button-1>", lambda e, fn=folder_name: self._toggle_folder(fn))
            self._folder_toggle_btns[folder_name] = toggle_btn

            cb = tk.Checkbutton(folder_frame,
                                text=folder_name + f"  ({len(folders[folder_name])} tracks)",
                                variable=folder_var, font=("Segoe UI Variable Display", 11, "bold"),
                                bg=COLORS["BG_CARD"], fg=COLORS["FG_BRIGHT"],
                                selectcolor=COLORS["BG_INPUT"], activebackground=COLORS["BG_CARD"],
                                activeforeground=COLORS["FG_BRIGHT"], anchor="w",
                                command=lambda fn=folder_name: self._on_folder_toggle(fn))
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=0, pady=2)

            tk.Label(folder_frame, text=format_size(folder_size), font=("Segoe UI Variable Display", 11),
                     bg=COLORS["BG_CARD"], fg=COLORS["FG_DIM"], width=10, anchor="e").pack(side=tk.RIGHT, padx=8)

            file_frames = []
            for f in folders[folder_name]:
                path = f["path"]
                file_var = tk.BooleanVar(value=old_vars.get(path, True))
                self._file_map[path] = f
                self._file_vars[path] = file_var
                self._folder_file_paths[folder_name].append(path)

                file_row = tk.Frame(self._sel_inner, bg=COLORS["BG_PANEL"])
                file_row.pack(fill=tk.X, padx=8, pady=(2, 2))

                basename = os.path.basename(path)

                cb = tk.Checkbutton(file_row, text=f"  {basename}",
                                    variable=file_var, font=("Segoe UI Variable Display", 11),
                                    bg=COLORS["BG_PANEL"], fg=COLORS["FG_TEXT"],
                                    selectcolor=COLORS["BG_INPUT"], activebackground=COLORS["BG_PANEL"],
                                    activeforeground=COLORS["FG_TEXT"], anchor="w",
                                    command=self._recalculate)
                cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(32, 8), pady=4)

                tk.Label(file_row, text=format_size(f["size"]), font=("Segoe UI Variable Display", 10),
                         bg=COLORS["BG_PANEL"], fg=COLORS["FG_DIM"], width=10, anchor="e").pack(side=tk.RIGHT, padx=8)

                file_frames.append(file_row)
                self._all_file_rows[path] = file_row

            self._folder_content_frames[folder_name] = file_frames
            
            if folder_name in self._collapsed_folders_setting:
                self._toggle_folder(folder_name)

    def _build_grid_view(self, folders, old_vars, old_folders):
        """Spotify-style cards with lazy loaded album art."""
        for folder_name in sorted(folders.keys()):
            folder_var = tk.BooleanVar(value=old_folders.get(folder_name, True))
            self._folder_vars[folder_name] = folder_var
            self._folder_file_paths[folder_name] = []
            self._folder_expanded[folder_name] = True

            folder_frame = tk.Frame(self._sel_inner, bg=COLORS["BG_PANEL"])
            folder_frame.pack(fill=tk.X, padx=8, pady=(16, 4))
            self._folder_header_frames[folder_name] = folder_frame

            toggle_btn = tk.Label(folder_frame, text="\u25BC", font=("Segoe UI Variable Display", 14),
                                  bg=COLORS["BG_PANEL"], fg=COLORS["ACCENT"], cursor="hand2", width=2)
            toggle_btn.pack(side=tk.LEFT, padx=(0, 4))
            toggle_btn.bind("<Button-1>", lambda e, fn=folder_name: self._toggle_folder(fn))
            self._folder_toggle_btns[folder_name] = toggle_btn

            tk.Label(folder_frame, text=folder_name, font=("Segoe UI Variable Display", 16, "bold"),
                     bg=COLORS["BG_PANEL"], fg=COLORS["FG_BRIGHT"]).pack(side=tk.LEFT)
            
            cb = tk.Checkbutton(folder_frame, text="Sync Playlist",
                                variable=folder_var, font=("Segoe UI Variable Display", 11),
                                bg=COLORS["BG_PANEL"], fg=COLORS["FG_TEXT"],
                                selectcolor=COLORS["BG_INPUT"], activebackground=COLORS["BG_PANEL"],
                                activeforeground=COLORS["FG_TEXT"],
                                command=lambda fn=folder_name: self._on_folder_toggle(fn))
            cb.pack(side=tk.LEFT, padx=16)

            grid_container = tk.Frame(self._sel_inner, bg=COLORS["BG_PANEL"])
            grid_container.pack(fill=tk.X, padx=8)
            self._folder_grid_containers[folder_name] = grid_container

            file_frames = []
            for f in folders[folder_name]:
                path = f["path"]
                file_var = tk.BooleanVar(value=old_vars.get(path, True))
                self._file_map[path] = f
                self._file_vars[path] = file_var
                self._folder_file_paths[folder_name].append(path)

                card = tk.Frame(grid_container, bg=COLORS["BG_CARD"], width=150, height=200)
                card.pack_propagate(False)
                
                img_lbl = tk.Label(card, text="♪", font=("Segoe UI", 36), bg=COLORS["BORDER"], fg=COLORS["FG_DIM"], width=15, height=5)
                img_lbl.pack(fill=tk.X, padx=10, pady=(10, 4))
                
                basename = os.path.basename(path)
                title_lbl = tk.Label(card, text=basename[:40], font=("Segoe UI Variable Display", 11), bg=COLORS["BG_CARD"], fg=COLORS["FG_TEXT"], wraplength=130, justify=tk.CENTER)
                title_lbl.pack(fill=tk.BOTH, expand=True, padx=4)

                cb = tk.Checkbutton(card, text="", variable=file_var, bg=COLORS["BG_CARD"], 
                                    selectcolor=COLORS["BG_INPUT"], activebackground=COLORS["BG_CARD"],
                                    command=self._recalculate)
                cb.place(x=4, y=4)
                
                file_frames.append(card)
                self._all_file_rows[path] = card
                
                if self.ffmpeg_path:
                    self._thumbnail_queue.put((path, img_lbl))

            self._folder_content_frames[folder_name] = file_frames
            
            if folder_name in self._collapsed_folders_setting:
                self._toggle_folder(folder_name)

    def _toggle_folder(self, folder_name):
        """Collapse or expand a folder's track rows."""
        expanded = self._folder_expanded.get(folder_name, True)
        self._folder_expanded[folder_name] = not expanded

        if expanded:
            # Collapse: hide file rows
            if self.view_mode == "LIST":
                for frame in self._folder_content_frames.get(folder_name, []):
                    frame.pack_forget()
            else:
                if folder_name in self._folder_grid_containers:
                    self._folder_grid_containers[folder_name].pack_forget()
            self._folder_toggle_btns[folder_name].configure(text="\u25B6")
        else:
            # Expand: trigger layout engine
            self._apply_search()
            self._folder_toggle_btns[folder_name].configure(text="\u25BC")

    def _on_folder_toggle(self, folder_name):
        """When a folder checkbox is toggled, cascade to all its children."""
        val = self._folder_vars[folder_name].get()
        for path in self._folder_file_paths.get(folder_name, []):
            self._file_vars[path].set(val)
        self._recalculate()

    def _apply_search(self):
        """Filter the visible folders and tracks based on search text."""
        query = self._search_var.get().strip().lower()
        view_mode = self.view_mode

        # Step 1: Hide EVERYTHING first to reset layout order
        for folder_name in self._folder_file_paths:
            header = self._folder_header_frames.get(folder_name)
            if header:
                header.pack_forget()
            if view_mode == "LIST":
                for frame in self._folder_content_frames.get(folder_name, []):
                    frame.pack_forget()
            else:
                if folder_name in self._folder_grid_containers:
                    self._folder_grid_containers[folder_name].pack_forget()
                for frame in self._folder_content_frames.get(folder_name, []):
                    frame.grid_forget()

        # Step 2: Re-pack in correct order, applying filter
        for folder_name in sorted(self._folder_file_paths.keys()):
            header = self._folder_header_frames.get(folder_name)
            file_frames = self._folder_content_frames.get(folder_name, [])
            paths = self._folder_file_paths.get(folder_name, [])

            # Check if folder name matches
            folder_match = query in folder_name.lower() if query else False

            # Check which individual files match
            matching_indices = []
            if query:
                for i, path in enumerate(paths):
                    basename = os.path.basename(path).lower()
                    if folder_match or query in basename:
                        matching_indices.append(i)
            else:
                matching_indices = list(range(len(paths)))

            if not query and not self._folder_expanded.get(folder_name, True):
                # Folder is collapsed and no search query: show only header
                if header:
                    header.pack(fill=tk.X, padx=2, pady=(4, 0))
                continue

            # Need to display items
            if folder_match or matching_indices:
                if header:
                    header.pack(fill=tk.X, padx=2, pady=(4, 0))
                
                # Show matching files (override collapse during search)
                visible_frames = [f for i, f in enumerate(file_frames) if (not query) or folder_match or (i in matching_indices)]
                
                if view_mode == "LIST":
                    for frame in visible_frames:
                        frame.pack(fill=tk.X, padx=2)
                else: # GRID
                    grid_container = self._folder_grid_containers.get(folder_name)
                    if grid_container:
                        grid_container.pack(fill=tk.X, padx=8)
                        for idx, frame in enumerate(visible_frames):
                            frame.grid(row=idx//5, column=idx%5, padx=6, pady=8)
            # else: folder stays hidden (already pack_forget'd)

    def _toggle_view_mode(self):
        """Switch between List View and Grid View."""
        if self.view_mode == "LIST":
            self.view_mode = "GRID"
        else:
            self.view_mode = "LIST"
        # Layout depends on full recreation
        self._populate_tree()

    def _select_all_new(self):
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")
        if not ipod_path or not os.path.isdir(ipod_path):
            messagebox.showinfo("Select Drive", "Please select a valid iPod drive first to scan for existing files.")
            return

        existing = scan_ipod_existing(ipod_path)
        new_count = 0

        for folder_name, paths in self._folder_file_paths.items():
            folder_has_new = False
            for path in paths:
                finfo = self._file_map.get(path)
                if not finfo:
                    continue
                folder = finfo["folder"]
                basename = os.path.splitext(os.path.basename(path))[0]
                key = get_ipod_safe_key(folder, basename)

                if key in existing:
                    self._file_vars[path].set(False)
                else:
                    self._file_vars[path].set(True)
                    folder_has_new = True
                    new_count += 1

            self._folder_vars[folder_name].set(folder_has_new)

        self._log(f"Select All New: {new_count} new track(s) selected")
        self._recalculate()

    # ── State Persistence ────────────────────────────────────────────────
        
    def _save_config(self):
        unchecked = []
        try:
            for path, var in self._file_vars.items():
                if not var.get():
                    unchecked.append(path)
        except Exception:
            pass
            
        collapsed_folders = []
        try:
            for folder, is_expanded in self._folder_expanded.items():
                if not is_expanded:
                    collapsed_folders.append(folder)
        except Exception:
            pass

        config = {
            "music_folder": self.music_var.get(),
            "ipod_drive_label": self.drive_var.get(),
            "format": self.format_var.get(),
            "bitrate": self.bitrate_var.get(),
            "convert_all": self.convert_all_var.get(),
            "voiceover": self.voiceover_var.get(),
            "unchecked": unchecked,
            "collapsed_folders": collapsed_folders
        }
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def _load_config(self):
        if not os.path.isfile(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
            
            if "format" in config and config["format"] in self.FORMATS:
                self.format_var.set(config["format"])
            if "bitrate" in config and config["bitrate"] in self.BITRATES:
                self.bitrate_var.set(config["bitrate"])
            if "convert_all" in config:
                self.convert_all_var.set(config["convert_all"])
            if "voiceover" in config:
                self.voiceover_var.set(config["voiceover"])
            
            if "collapsed_folders" in config:
                self._collapsed_folders_setting = config["collapsed_folders"]
            
            # Select drive
            label = config.get("ipod_drive_label", "")
            if label and label in self.drive_combo["values"]:
                self.drive_var.set(label)

            # Load music folder
            mfolder = config.get("music_folder", "")
            if mfolder and os.path.isdir(mfolder):
                self.music_var.set(mfolder)
                self._populate_tree()

                # Restore unchecked items
                unchecked_set = set(config.get("unchecked", []))
                if unchecked_set:
                    for path in unchecked_set:
                        if path in self._file_vars:
                            self._file_vars[path].set(False)
                    # Update folder checkboxes
                    for folder_name, paths in self._folder_file_paths.items():
                        all_unchecked = all(not self._file_vars[p].get() for p in paths)
                        if all_unchecked:
                            self._folder_vars[folder_name].set(False)

            self._recalculate()
        except Exception as e:
            print(f"Failed to load config: {e}")

    def _on_closing(self):
        """Handle window close event."""
        self._thumbnail_thread_running = False
        self._save_config()
        self.root.destroy()

    # ── Space Calculation ────────────────────────────────────────────────

    def _recalculate(self):
        """Recalculate space estimates whenever inputs change."""
        music_path = self.music_var.get()
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")

        self._source_files = []
        try:
            for path, var in self._file_vars.items():
                if var.get() and path in self._file_map:
                    self._source_files.append(self._file_map[path])
        except Exception:
            pass

        num_files = len(self._source_files)
        source_total = sum(f["size"] for f in self._source_files)

        # Count playlists (unique folder names)
        folders = set(f["folder"] for f in self._source_files if f["folder"])
        num_playlists = 1 + len(folders)  # master + folders

        # Estimate output size — only for NEW files not already on iPod
        bitrate = int(self.bitrate_var.get())
        fmt = self.format_var.get()
        convert_all = self.convert_all_var.get()

        # Use cached iPod scan to avoid disk I/O on every checkbox toggle
        if ipod_path and os.path.isdir(ipod_path) and not convert_all:
            if self._ipod_scan_cache_drive != ipod_path:
                self._ipod_scan_cache = scan_ipod_existing(ipod_path)
                self._ipod_scan_cache_drive = ipod_path
            existing = self._ipod_scan_cache
        else:
            existing = {}

        new_files = []
        existing_count = 0
        for f in self._source_files:
            folder = f["folder"]
            basename = os.path.splitext(os.path.basename(f["path"]))[0]
            key = get_ipod_safe_key(folder, basename)
            if key in existing and not convert_all:
                existing_count += 1
            else:
                new_files.append(f)

        self._new_file_count = len(new_files)
        self._existing_count = existing_count

        if self.ffmpeg_path:
            self._estimated_size = sum(
                estimate_transcoded_size(f, bitrate, fmt) for f in new_files
            )
        else:
            self._estimated_size = sum(f["size"] for f in new_files)

        if self.voiceover_var.get() and self._new_file_count > 0:
            vo_count = self._new_file_count + num_playlists
            self._estimated_size += vo_count * 15360

        # iPod free space
        if ipod_path and os.path.isdir(ipod_path):
            total_disk, used_disk, free_disk = get_disk_usage(ipod_path)
        else:
            total_disk, used_disk, free_disk = 0, 0, 0

        remaining = free_disk - self._estimated_size
        fits = remaining >= 0

        # Update labels
        self._lbl_source_size.configure(text=format_size(source_total) if num_files else "--")
        new_ct = self._new_file_count
        exist_ct = self._existing_count
        self._lbl_file_count.configure(text=f"{num_files} ({new_ct} new, {exist_ct} on iPod)" if num_files else "--")
        self._lbl_estimated_size.configure(text=f"~{format_size(self._estimated_size)} to transfer" if new_ct else ("0 B (all synced)" if num_files else "--"))
        self._lbl_playlist_count.configure(text=str(num_playlists) if num_files else "--")
        self._lbl_free_space.configure(text=format_size(free_disk) if total_disk else "--")
        self._lbl_remaining.configure(
            text=f"~{format_size(remaining)}" if (total_disk and num_files) else "--"
        )
        if total_disk and num_files:
            self._lbl_remaining.configure(fg=COLORS["SUCCESS"] if fits else COLORS["ERROR"])

        # Space bar
        if total_disk > 0 and num_files > 0:
            used_pct = ((used_disk + self._estimated_size) / total_disk) * 100
            self.space_pct_var.set(min(used_pct, 100))
            self.space_bar.configure(style="space.Horizontal.TProgressbar" if fits else "spacewarn.Horizontal.TProgressbar")
            self.space_status_label.configure(
                text=f"{used_pct:.0f}% of iPod used after sync" if fits else "NOT ENOUGH SPACE!",
                fg=COLORS["FG_DIM"] if fits else COLORS["ERROR"]
            )
        else:
            self.space_pct_var.set(0)
            self.space_status_label.configure(text="", fg=COLORS["FG_DIM"])

        # Enable/disable sync button
        can_sync = num_files > 0 and ipod_path and fits
        self.sync_btn.configure(state=tk.NORMAL if can_sync else tk.DISABLED)

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, msg):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_progress(self, current, total, phase=""):
        if total > 0:
            pct = (current / total) * 100
            self.progress_var.set(pct)
            self.progress_label.configure(text=f"{phase}: {current}/{total}")
        self.root.update_idletasks()

    # ── Sync Action ──────────────────────────────────────────────────────

    def _start_sync(self):
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")

        if not ipod_path or not os.path.isdir(ipod_path):
            messagebox.showwarning("No Drive", "Please select a valid iPod drive.")
            return
        if not self._source_files:
            messagebox.showwarning("No Music", "Please select a music folder with audio files.")
            return

        new_ct = self._new_file_count
        convert_all = self.convert_all_var.get()

        if convert_all:
            msg = (f"Re-encode all: {len(self._source_files)} tracks will be transcoded to "
                   f"{self.format_var.get()} {self.bitrate_var.get()}kbps "
                   f"(existing files on iPod will be overwritten).\n\nContinue?")
        elif new_ct == 0:
            msg = "All files are already on the iPod. Only the database will be rebuilt.\n\nContinue?"
        else:
            msg = (f"{new_ct} new file(s) will be copied to the iPod "
                   f"({format_size(self._estimated_size)}).\n"
                   f"{self._existing_count} file(s) already on iPod will be kept.\n\nContinue?")

        if not messagebox.askyesno("Confirm Sync", msg):
            return

        self._clear_log()
        self.progress_var.set(0)
        self.sync_btn.configure(state=tk.DISABLED)
        self.rebuild_btn.configure(state=tk.DISABLED)
        self.wipe_btn.configure(state=tk.DISABLED)
        self.status_label.configure(text="Syncing...", style="Subtitle.TLabel")

        target_format = self.format_var.get()
        target_bitrate = int(self.bitrate_var.get())
        convert_all = self.convert_all_var.get()
        voiceover_enabled = self.voiceover_var.get()

        def run():
            def log_cb(msg):
                self.root.after(0, self._log, msg)

            def progress_cb(cur, tot, phase):
                self.root.after(0, self._set_progress, cur, tot, phase)

            success, summary = sync_to_ipod(
                ipod_path, self._source_files,
                target_format, target_bitrate, convert_all,
                self.ffmpeg_path, voiceover_enabled, log_cb, progress_cb
            )

            def finish():
                self.sync_btn.configure(state=tk.NORMAL)
                self.rebuild_btn.configure(state=tk.NORMAL)
                self.wipe_btn.configure(state=tk.NORMAL)
                if success:
                    self.progress_var.set(100)
                    self._log("")
                    self._log("=" * 50)
                    self._log(f"  Sync complete!")
                    self._log(f"  Tracks: {summary['tracks']}")
                    self._log(f"  Playlists: {summary['playlists']}")
                    for name in summary.get("playlist_names", []):
                        self._log(f"    - {name}")
                    if summary["errors"]:
                        self._log(f"  Errors: {len(summary['errors'])}")
                        for e in summary["errors"][:5]:
                            self._log(f"    ! {e}")
                    self._log("=" * 50)
                    self.status_label.configure(
                        text="\u2713  READY TO EJECT \u2014 Safe to disconnect your iPod.",
                        style="Status.TLabel")
                    self.progress_label.configure(text="Complete!")
                    self._recalculate()
                else:
                    self.status_label.configure(text="\u2717  Sync failed. Check log for details.",
                                                style="Subtitle.TLabel")
                    self.progress_label.configure(text="Failed")

            self.root.after(0, finish)

        threading.Thread(target=run, daemon=True).start()

    # ── Rebuild DB Only ──────────────────────────────────────────────────

    def _start_rebuild_only(self):
        """Rebuild database from files already on the iPod (Part 2 behavior)."""
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")

        if not ipod_path or not os.path.isdir(ipod_path):
            messagebox.showwarning("No Drive", "Please select a valid iPod drive.")
            return

        self._clear_log()
        self.progress_var.set(0)
        self.sync_btn.configure(state=tk.DISABLED)
        self.rebuild_btn.configure(state=tk.DISABLED)
        self.wipe_btn.configure(state=tk.DISABLED)
        self.status_label.configure(text="Rebuilding database...", style="Subtitle.TLabel")

        def run():
            def log_cb(msg):
                self.root.after(0, self._log, msg)
            def progress_cb(cur, tot, phase=""):
                self.root.after(0, self._set_progress, cur, tot, phase)

            # Scan files already on iPod
            log_cb("Scanning iPod for existing audio files...")
            all_tracks = []
            playlists = {}
            scan_dirs = [
                os.path.join(ipod_path, "Music"),
                os.path.join(ipod_path, "iPod_Control", "Music"),
            ]
            for sd in scan_dirs:
                if not os.path.isdir(sd):
                    continue
                for root, dirs, files in os.walk(sd):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for fname in sorted(files, key=lambda x: x.lower()):
                        if fname.startswith('.'):
                            continue
                        ext = os.path.splitext(fname)[1].lower()
                        full = os.path.join(root, fname)
                        if ext in AUDIO_EXTENSIONS:
                            rel = os.path.relpath(full, ipod_path)
                            ipod_rel = "/" + rel.replace("\\", "/")
                            all_tracks.append(ipod_rel)
                            rel_to_sd = os.path.relpath(root, sd)
                            if rel_to_sd != ".":
                                folder = rel_to_sd.split(os.sep)[0]
                                if folder not in playlists:
                                    playlists[folder] = []
                                playlists[folder].append(ipod_rel)

            all_tracks.sort(key=lambda x: os.path.basename(x).lower())

            if not all_tracks:
                log_cb("ERROR: No audio files found on iPod.")
                self.root.after(0, lambda: self.sync_btn.configure(state=tk.NORMAL))
                self.root.after(0, lambda: self.rebuild_btn.configure(state=tk.NORMAL))
                self.root.after(0, lambda: self.wipe_btn.configure(state=tk.NORMAL))
                return

            # Clean legacy files
            itunes_dir = os.path.join(ipod_path, "iPod_Control", "iTunes")
            os.makedirs(itunes_dir, exist_ok=True)
            for f in ["iTunesDB", "iTunesPrefs", "iTunesPrefs.plist", "iTunesControl", "iTunesStats", "iTunesPState"]:
                fp = os.path.join(itunes_dir, f)
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass

            # Build database
            log_cb(f"Found {len(all_tracks)} tracks, {len(playlists)} folder playlist(s)")

            final_db, num_tracks, actual_pl, _, _ = build_itunes_db(
                all_tracks, playlists, voiceover_enabled=False, log_cb=log_cb, progress_cb=progress_cb
            )

            try:
                with open(os.path.join(itunes_dir, "iTunesSD"), "wb") as f:
                    f.write(final_db)
                log_cb(f"Database written: {format_size(len(final_db))}")
            except Exception as e:
                log_cb(f"ERROR: {e}")
                self.root.after(0, lambda: self.sync_btn.configure(state=tk.NORMAL))
                self.root.after(0, lambda: self.rebuild_btn.configure(state=tk.NORMAL))
                self.root.after(0, lambda: self.wipe_btn.configure(state=tk.NORMAL))
                return

            def finish():
                self.sync_btn.configure(state=tk.NORMAL)
                self.rebuild_btn.configure(state=tk.NORMAL)
                self.wipe_btn.configure(state=tk.NORMAL)
                self.progress_var.set(100)
                self._log(f"\nDatabase rebuilt: {num_tracks} tracks, {actual_pl} playlists")
                self.status_label.configure(
                    text="\u2713  READY TO EJECT \u2014 Safe to disconnect your iPod.",
                    style="Status.TLabel")
                self.progress_label.configure(text="Complete!")

            self.root.after(0, finish)

        threading.Thread(target=run, daemon=True).start()

    # ── Wipe iPod ─────────────────────────────────────────────────────────

    def _start_wipe(self):
        """Delete all music, VoiceOver cache, and database from the iPod."""
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")

        if not ipod_path or not os.path.isdir(ipod_path):
            messagebox.showwarning("No Drive", "Please select a valid iPod drive.")
            return

        # First confirmation
        if not messagebox.askyesno(
            "Wipe iPod",
            "This will permanently delete ALL music, VoiceOver audio, "
            "and database files from the iPod.\n\n"
            "This action cannot be undone.\n\nAre you sure?",
            icon="warning"
        ):
            return

        # Second confirmation — destructive action
        if not messagebox.askyesno(
            "Final Confirmation",
            f"LAST CHANCE — All content on {drive_sel} will be erased.\n\n"
            "Do you really want to wipe everything?",
            icon="warning"
        ):
            return

        self._clear_log()
        self.progress_var.set(0)
        self.sync_btn.configure(state=tk.DISABLED)
        self.rebuild_btn.configure(state=tk.DISABLED)
        self.wipe_btn.configure(state=tk.DISABLED)
        self.status_label.configure(text="Wiping iPod...", style="Subtitle.TLabel")

        def run():
            def log_cb(msg):
                self.root.after(0, self._log, msg)

            log_cb("Starting full iPod wipe...")
            total_freed = 0
            phases_done = 0
            total_phases = 3

            # Phase 1: Wipe Music
            music_dir = os.path.join(ipod_path, "iPod_Control", "Music")
            if os.path.isdir(music_dir):
                log_cb("Phase 1/3: Deleting all music files...")
                try:
                    for entry in os.scandir(music_dir):
                        try:
                            if entry.is_dir():
                                dir_size = sum(
                                    f.stat().st_size for f in os.scandir(entry.path) if f.is_file()
                                )
                                shutil.rmtree(entry.path)
                                total_freed += dir_size
                            elif entry.is_file():
                                total_freed += entry.stat().st_size
                                os.remove(entry.path)
                        except Exception as e:
                            log_cb(f"  Warning: Could not remove {entry.name}: {e}")
                    log_cb(f"  Music folder cleared ({format_size(total_freed)} freed)")
                except Exception as e:
                    log_cb(f"  Error accessing Music folder: {e}")
            else:
                log_cb("Phase 1/3: No Music folder found (skipped)")
            phases_done += 1
            self.root.after(0, self._set_progress, phases_done, total_phases, "Wiping")

            # Phase 2: Wipe VoiceOver / Speakable
            speak_dir = os.path.join(ipod_path, "iPod_Control", "Speakable")
            if os.path.isdir(speak_dir):
                log_cb("Phase 2/3: Deleting VoiceOver cache...")
                vo_size = 0
                try:
                    for root_dir, dirs, files in os.walk(speak_dir):
                        for fname in files:
                            fp = os.path.join(root_dir, fname)
                            try:
                                vo_size += os.path.getsize(fp)
                                os.remove(fp)
                            except Exception:
                                pass
                    # Remove empty directories
                    shutil.rmtree(speak_dir, ignore_errors=True)
                    total_freed += vo_size
                    log_cb(f"  VoiceOver cache cleared ({format_size(vo_size)} freed)")
                except Exception as e:
                    log_cb(f"  Error clearing VoiceOver: {e}")
            else:
                log_cb("Phase 2/3: No VoiceOver folder found (skipped)")
            phases_done += 1
            self.root.after(0, self._set_progress, phases_done, total_phases, "Wiping")

            # Phase 3: Wipe iTunes database files
            itunes_dir = os.path.join(ipod_path, "iPod_Control", "iTunes")
            if os.path.isdir(itunes_dir):
                log_cb("Phase 3/3: Deleting database files...")
                db_size = 0
                try:
                    for fname in os.listdir(itunes_dir):
                        fp = os.path.join(itunes_dir, fname)
                        if os.path.isfile(fp):
                            try:
                                db_size += os.path.getsize(fp)
                                os.remove(fp)
                            except Exception:
                                pass
                    total_freed += db_size
                    log_cb(f"  Database files cleared ({format_size(db_size)} freed)")
                except Exception as e:
                    log_cb(f"  Error clearing database: {e}")
            else:
                log_cb("Phase 3/3: No iTunes folder found (skipped)")
            phases_done += 1
            self.root.after(0, self._set_progress, phases_done, total_phases, "Wiping")

            log_cb("")
            log_cb("=" * 50)
            log_cb(f"  Wipe complete! {format_size(total_freed)} freed.")
            log_cb("=" * 50)

            def finish():
                self.sync_btn.configure(state=tk.NORMAL)
                self.rebuild_btn.configure(state=tk.NORMAL)
                self.wipe_btn.configure(state=tk.NORMAL)
                self.progress_var.set(100)
                self.progress_label.configure(text="Wipe complete!")
                self.status_label.configure(
                    text="\u2713  iPod wiped \u2014 Ready for fresh sync.",
                    style="Status.TLabel")
                # Invalidate iPod scan cache
                self._ipod_scan_cache = {}
                self._ipod_scan_cache_drive = None
                self._recalculate()

            self.root.after(0, finish)

        threading.Thread(target=run, daemon=True).start()

    def run(self):
        self.root.mainloop()
