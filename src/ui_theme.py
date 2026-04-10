"""
iPod Music Manager — UI Theme
Glassmorphism neon-purple color palette, ttk styles, and ToolTip widget.
"""

import tkinter as tk
from tkinter import ttk


# ── Color Palette (Glassmorphism Neon Purple) ────────────────────────────────

COLORS = {
    "BG_DARK":     "#0D0B14",
    "BG_PANEL":    "#14111D",
    "BG_INPUT":    "#221E2E",
    "BG_CARD":     "#1A1625",
    "FG_TEXT":     "#E0DCE8",
    "FG_DIM":      "#847E96",
    "FG_BRIGHT":   "#F4F0FF",
    "ACCENT":      "#B388FF",
    "ACCENT_HOV":  "#D4BBFF",
    "ACCENT_GLOW": "#7C4DFF",
    "SUCCESS":     "#69F0AE",
    "WARNING":     "#FFD54F",
    "ERROR":       "#FF5252",
    "BORDER":      "#2A2438",
}


class ToolTip:
    """Lightweight hover tooltip for any tkinter widget."""
    def __init__(self, widget, text, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._tip_window = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._cancel)
        widget.bind("<ButtonPress>", self._cancel)

    def _schedule(self, event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self, event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self):
        if self._tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        c = COLORS
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         bg=c["BG_CARD"], fg=c["FG_TEXT"], relief="solid", borderwidth=1,
                         font=("Segoe UI Variable Display", 10), padx=8, pady=4)
        label.pack()

    def _hide(self):
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None


def setup_styles(style):
    """Configure all ttk styles using the neon-purple glassmorphism palette."""
    c = COLORS
    _font = "Segoe UI Variable Display"
    style.theme_use("clam")

    # Frames
    style.configure("Dark.TFrame", background=c["BG_DARK"])
    style.configure("Card.TFrame", background=c["BG_CARD"])
    style.configure("Panel.TFrame", background=c["BG_PANEL"])

    # Labels
    style.configure("Dark.TLabel",       background=c["BG_DARK"], foreground=c["FG_TEXT"],   font=(_font, 11))
    style.configure("Title.TLabel",      background=c["BG_DARK"], foreground=c["ACCENT"],    font=(_font, 22, "bold"))
    style.configure("Subtitle.TLabel",   background=c["BG_DARK"], foreground=c["FG_DIM"],    font=(_font, 10))
    style.configure("Card.TLabel",       background=c["BG_CARD"], foreground=c["FG_TEXT"],    font=(_font, 11))
    style.configure("CardDim.TLabel",    background=c["BG_CARD"], foreground=c["FG_DIM"],    font=(_font, 11))
    style.configure("CardBright.TLabel", background=c["BG_CARD"], foreground=c["FG_BRIGHT"], font=(_font, 13, "bold"))
    style.configure("CardValue.TLabel",  background=c["BG_CARD"], foreground=c["ACCENT"],    font=(_font, 13, "bold"))
    style.configure("CardWarn.TLabel",   background=c["BG_CARD"], foreground=c["ERROR"],     font=(_font, 11, "bold"))
    style.configure("CardOk.TLabel",     background=c["BG_CARD"], foreground=c["SUCCESS"],   font=(_font, 11, "bold"))
    style.configure("Status.TLabel",     background=c["BG_DARK"], foreground=c["SUCCESS"],   font=(_font, 12, "bold"))
    style.configure("SectionTitle.TLabel", background=c["BG_DARK"], foreground=c["FG_DIM"],  font=(_font, 9, "bold"))

    # Buttons
    style.configure("Accent.TButton", background=c["ACCENT_GLOW"], foreground=c["FG_BRIGHT"],
                     font=(_font, 12, "bold"), padding=(20, 9), borderwidth=0)
    style.map("Accent.TButton", background=[("active", c["ACCENT"]), ("disabled", c["BG_INPUT"])],
              foreground=[("disabled", c["FG_DIM"])])
    style.configure("Secondary.TButton", background=c["BG_INPUT"], foreground=c["FG_TEXT"],
                     font=(_font, 10), padding=(12, 6), borderwidth=0)
    style.map("Secondary.TButton", background=[("active", c["BORDER"])])
    style.configure("Small.TButton", background=c["BG_INPUT"], foreground=c["FG_TEXT"],
                     font=(_font, 10), padding=(10, 5), borderwidth=0)
    style.map("Small.TButton", background=[("active", c["BORDER"])])
    style.configure("Danger.TButton", background="#3D1418", foreground=c["ERROR"],
                     font=(_font, 10), padding=(12, 6), borderwidth=0)
    style.map("Danger.TButton", background=[("active", "#5C1D22"), ("disabled", c["BG_INPUT"])],
              foreground=[("disabled", c["FG_DIM"])])

    # Combobox
    style.configure("Dark.TCombobox", fieldbackground=c["BG_INPUT"], background=c["BG_INPUT"],
                     foreground=c["FG_TEXT"], arrowcolor=c["ACCENT"], borderwidth=0, relief="flat")
    style.map("Dark.TCombobox", fieldbackground=[("readonly", c["BG_INPUT"])],
              selectbackground=[("readonly", c["BG_INPUT"])], selectforeground=[("readonly", c["FG_TEXT"])])

    # Progress bars
    style.configure("green.Horizontal.TProgressbar", troughcolor=c["BG_INPUT"],
                     background=c["ACCENT_GLOW"], borderwidth=0, thickness=10)
    style.configure("space.Horizontal.TProgressbar", troughcolor=c["BG_INPUT"],
                     background=c["ACCENT"], borderwidth=0, thickness=16)
    style.configure("spacewarn.Horizontal.TProgressbar", troughcolor=c["BG_INPUT"],
                     background=c["ERROR"], borderwidth=0, thickness=16)

    # Checkbutton
    style.configure("Dark.TCheckbutton", background=c["BG_CARD"], foreground=c["FG_TEXT"], font=(_font, 10))
    style.map("Dark.TCheckbutton", background=[("active", c["BG_CARD"])])

    # Scrollbar
    style.configure("Custom.Vertical.TScrollbar", background=c["BG_INPUT"],
                     troughcolor=c["BG_PANEL"], arrowcolor=c["ACCENT"], borderwidth=0)
    style.map("Custom.Vertical.TScrollbar", background=[("active", c["BORDER"])])
