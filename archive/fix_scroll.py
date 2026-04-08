import os

path = r"d:\Ipod\antigravity.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

old_left = """        left_border = tk.Frame(body, bg=self.BORDER)
        left_inner = ttk.Frame(left_border, style="Dark.TFrame")
        left_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        body.add(left_border, minsize=360, width=380)

        left = ttk.Frame(left_inner, style="Dark.TFrame")"""

new_left = """        left_border = tk.Frame(body, bg=self.BORDER)
        body.add(left_border, minsize=360, width=380)

        self._left_canvas = tk.Canvas(left_border, bg=self.BG_DARK, highlightthickness=0, bd=0)
        left_scroll = tk.Scrollbar(left_border, orient=tk.VERTICAL, command=self._left_canvas.yview)
        
        left_inner = ttk.Frame(self._left_canvas, style="Dark.TFrame")
        
        def _on_left_inner_configure(e):
            self._left_canvas.configure(scrollregion=self._left_canvas.bbox("all"))
        left_inner.bind("<Configure>", _on_left_inner_configure)
        
        self._left_canvas_window = self._left_canvas.create_window((0, 0), window=left_inner, anchor="nw")
        
        def _on_left_canvas_configure(e):
            self._left_canvas.itemconfig(self._left_canvas_window, width=e.width)
        self._left_canvas.bind("<Configure>", _on_left_canvas_configure)
        
        self._left_canvas.configure(yscrollcommand=left_scroll.set)
        
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)

        left = ttk.Frame(left_inner, style="Dark.TFrame")"""
        
text = text.replace(old_left, new_left)

old_mousewheel = """        # Smart global mouse wheel scrolling
        def _global_mousewheel(event):
            w_path = str(event.widget)
            if w_path.startswith(str(self._sel_canvas)):
                self._sel_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif w_path.startswith(str(self.log_text)):
                self.log_text.yview_scroll(int(-1 * (event.delta / 120)), "units")"""

new_mousewheel = """        # Smart global mouse wheel scrolling
        def _global_mousewheel(event):
            w_path = str(event.widget)
            if w_path.startswith(str(self._sel_canvas)):
                self._sel_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif hasattr(self, '_left_canvas') and w_path.startswith(str(self._left_canvas)):
                self._left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif w_path.startswith(str(self.log_text)):
                self.log_text.yview_scroll(int(-1 * (event.delta / 120)), "units")"""

text = text.replace(old_mousewheel, new_mousewheel)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

print("Dynamic viewport logic applied.")
