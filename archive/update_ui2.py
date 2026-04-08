import os
import re

path = r"d:\Ipod\antigravity.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update Colors
text = re.sub(r'BG_DARK\s*=\s*".*?"', 'BG_DARK    = "#141218"', text)
text = re.sub(r'BG_PANEL\s*=\s*".*?"', 'BG_PANEL   = "#211F26"', text)
text = re.sub(r'BG_INPUT\s*=\s*".*?"', 'BG_INPUT   = "#36343B"', text)
text = re.sub(r'BG_CARD\s*=\s*".*?"', 'BG_CARD    = "#2B2930"', text)
text = re.sub(r'FG_TEXT\s*=\s*".*?"', 'FG_TEXT    = "#E6E1E5"', text)
text = re.sub(r'FG_DIM\s*=\s*".*?"', 'FG_DIM     = "#CAC4D0"', text)
text = re.sub(r'FG_BRIGHT\s*=\s*".*?"', 'FG_BRIGHT  = "#FFFFFF"', text)
text = re.sub(r'ACCENT\s*=\s*".*?"', 'ACCENT     = "#D0BCFF"', text)
text = re.sub(r'ACCENT_HOV\s*=\s*".*?"', 'ACCENT_HOV = "#E8DEF8"', text)
text = re.sub(r'SUCCESS\s*=\s*".*?"', 'SUCCESS    = "#9BCF53"', text)
text = re.sub(r'WARNING\s*=\s*".*?"', 'WARNING    = "#F4B678"', text)
text = re.sub(r'ERROR\s*=\s*".*?"', 'ERROR      = "#F2B8B5"', text)
text = re.sub(r'BORDER\s*=\s*".*?"', 'BORDER     = "#332D41"', text)

# 2. Update Typography and Padding globally
text = text.replace('"Segoe UI", 10', '"Segoe UI Variable Display", 11')
text = text.replace('"Segoe UI", 9', '"Segoe UI Variable Display", 11')
text = text.replace('"Segoe UI", 8', '"Segoe UI Variable Display", 10')
text = text.replace('"Segoe UI", 20', '"Segoe UI Variable Display", 26')
text = text.replace('"Segoe UI", 11', '"Segoe UI Variable Display", 13')
text = text.replace('"Segoe UI", 12', '"Segoe UI Variable Display", 14')
text = text.replace('"Segoe UI", 14', '"Segoe UI Variable Display", 16')
text = text.replace('"Consolas", 9', '"Consolas", 10')

# Update accent button text color to On-Primary (#381E72) and boost padding
old_accent_btn = 'style.configure("Accent.TButton", background=self.ACCENT, foreground="#0d1117",\n                         font=("Segoe UI Variable Display", 13, "bold"), padding=(20, 10), borderwidth=0)'
new_accent_btn = 'style.configure("Accent.TButton", background=self.ACCENT, foreground="#381E72",\n                         font=("Segoe UI Variable Display", 13, "bold"), padding=(32, 16), borderwidth=0)'
text = text.replace(old_accent_btn, new_accent_btn)

# Secondary buttons padding
old_sec_btn = 'style.configure("Secondary.TButton", background=self.BG_INPUT, foreground=self.FG_TEXT,\n                         font=("Segoe UI Variable Display", 11), padding=(10, 5), borderwidth=0)'
new_sec_btn = 'style.configure("Secondary.TButton", background=self.BG_INPUT, foreground=self.FG_TEXT,\n                         font=("Segoe UI Variable Display", 11), padding=(16, 8), borderwidth=0)'
text = text.replace(old_sec_btn, new_sec_btn)

old_small_btn = 'style.configure("Small.TButton", background=self.BG_INPUT, foreground=self.FG_TEXT,\n                         font=("Segoe UI Variable Display", 10), padding=(6, 3), borderwidth=0)'
new_small_btn = 'style.configure("Small.TButton", background=self.BG_INPUT, foreground=self.FG_TEXT,\n                         font=("Segoe UI Variable Display", 10), padding=(12, 6), borderwidth=0)'
text = text.replace(old_small_btn, new_small_btn)

# Progress bars logic to be totally flat and thicker
old_green_pb = 'style.configure("green.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,\n                         background=self.ACCENT, borderwidth=0, thickness=8)'
new_green_pb = 'style.configure("green.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,\n                         background=self.ACCENT, borderwidth=0, thickness=12)'
text = text.replace(old_green_pb, new_green_pb)

old_space_pb = 'style.configure("space.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,\n                         background=self.SUCCESS, borderwidth=0, thickness=12)'
new_space_pb = 'style.configure("space.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,\n                         background=self.SUCCESS, borderwidth=0, thickness=20)'
text = text.replace(old_space_pb, new_space_pb)

old_spacewarn_pb = 'style.configure("spacewarn.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,\n                         background=self.ERROR, borderwidth=0, thickness=12)'
new_spacewarn_pb = 'style.configure("spacewarn.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,\n                         background=self.ERROR, borderwidth=0, thickness=20)'
text = text.replace(old_spacewarn_pb, new_spacewarn_pb)


# 3. Increase padding in layouts
# Header row
text = text.replace('header.pack(fill=tk.X, padx=16, pady=(10, 0))', 'header.pack(fill=tk.X, padx=24, pady=(24, 8))')

# Left panel root
text = text.replace('left.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)', 'left.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)')

# Increase right panel padding
text = text.replace('right.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)', 'right.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)')

# Increase separation inside the list view
text = text.replace('folder_frame.pack(fill=tk.X, padx=2, pady=(4, 0))', 'folder_frame.pack(fill=tk.X, padx=8, pady=(16, 4))')
text = text.replace('file_row.pack(fill=tk.X, padx=2)', 'file_row.pack(fill=tk.X, padx=8, pady=(2, 2))')

# Increase list element padding (inside Checkbutton)
text = text.replace('cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(28, 4), pady=1)', 'cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(32, 8), pady=4)')

with open(path, "w", encoding="utf-8") as f:
    f.write(text)
print("Updated successfully")
