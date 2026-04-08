import os

path = r"d:\Ipod\antigravity.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

# Fix window sizes to accommodate the new padding and fonts
text = text.replace('self.root.minsize(1000, 550)', 'self.root.minsize(1050, 680)')

# Give the left pane more horizontal breathing room for the sync/rebuild buttons
text = text.replace('body.add(left_border, minsize=300, width=340)', 'body.add(left_border, minsize=360, width=380)')

# Pull back button padding just slightly so they don't visually crash into each other
text = text.replace('padding=(32, 16)', 'padding=(24, 10)')
text = text.replace('padding=(16, 8)', 'padding=(12, 6)')

# Prevent the status label text from being clipped by adding wraplength
old_lbl = 'self.status_label = ttk.Label(left, text="Select iPod drive and music folder to begin.",\n                                       style="Subtitle.TLabel")'
new_lbl = 'self.status_label = ttk.Label(left, text="Select iPod drive and music folder to begin.",\n                                       style="Subtitle.TLabel", wraplength=360)'
text = text.replace(old_lbl, new_lbl)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

print("Clipping bugs fixed.")
