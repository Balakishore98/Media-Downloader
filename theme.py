"""Dark technical theme + custom widgets for MediaForge."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

PALETTE = {
    'bg': '#070b10',
    'panel': '#0e141b',
    'panel_alt': '#131c25',
    'inset': '#04070a',
    'border': '#1d2836',
    'border_hi': '#2c3d4e',
    'text': '#c3d1e0',
    'text_dim': '#63768a',
    'text_hi': '#eef4fa',
    'accent': '#00e5c0',
    'accent_dim': '#0a7d6c',
    'blue': '#3ea6ff',
    'green': '#3ddc84',
    'amber': '#ffb020',
    'red': '#ff5c5c',
    'purple': '#b47cff',
    'row_alt': '#0b1119',
    'sel': '#12303a',
}

C = PALETTE


def pick_mono() -> str:
    families = set(tkfont.families())
    for name in ('Cascadia Mono', 'Cascadia Code', 'JetBrains Mono', 'Consolas',
                 'DejaVu Sans Mono', 'Courier New'):
        if name in families:
            return name
    return 'TkFixedFont'


def pick_ui() -> str:
    families = set(tkfont.families())
    for name in ('Segoe UI Variable Text', 'Segoe UI', 'Inter', 'Helvetica'):
        if name in families:
            return name
    return 'TkDefaultFont'


class Fonts:
    def __init__(self):
        mono, ui = pick_mono(), pick_ui()
        self.mono = (mono, 9)
        self.mono_sm = (mono, 8)
        self.mono_bold = (mono, 9, 'bold')
        self.mono_big = (mono, 12, 'bold')
        self.brand = (mono, 17, 'bold')
        self.ui = (ui, 9)
        self.ui_bold = (ui, 9, 'bold')
        self.micro = (ui, 8)
        self.micro_bold = (ui, 8, 'bold')


def apply(root: tk.Misc) -> Fonts:
    """Install the dark theme on *root* and return the font set."""
    fonts = Fonts()
    style = ttk.Style(root)
    style.theme_use('clam')

    root.configure(background=C['bg'])
    root.option_add('*TCombobox*Listbox.background', C['panel_alt'])
    root.option_add('*TCombobox*Listbox.foreground', C['text'])
    root.option_add('*TCombobox*Listbox.selectBackground', C['accent_dim'])
    root.option_add('*TCombobox*Listbox.selectForeground', C['text_hi'])
    root.option_add('*TCombobox*Listbox.font', fonts.mono)
    root.option_add('*Menu.background', C['panel_alt'])
    root.option_add('*Menu.foreground', C['text'])
    root.option_add('*Menu.activeBackground', C['accent_dim'])
    root.option_add('*Menu.activeForeground', C['text_hi'])
    root.option_add('*Menu.font', fonts.ui)
    root.option_add('*Menu.borderWidth', 0)

    # ---- frames -----------------------------------------------------------
    style.configure('TFrame', background=C['bg'])
    style.configure('Panel.TFrame', background=C['panel'])
    style.configure('Alt.TFrame', background=C['panel_alt'])
    style.configure('Rule.TFrame', background=C['border'])
    style.configure('Accent.TFrame', background=C['accent'])

    # ---- labels -----------------------------------------------------------
    for name, bg in (('TLabel', C['bg']), ('Panel.TLabel', C['panel']),
                     ('Alt.TLabel', C['panel_alt'])):
        style.configure(name, background=bg, foreground=C['text'], font=fonts.ui)
    style.configure('Head.TLabel', background=C['panel'], foreground=C['text_dim'],
                    font=fonts.micro_bold)
    style.configure('HeadBg.TLabel', background=C['bg'], foreground=C['text_dim'],
                    font=fonts.micro_bold)
    style.configure('Dim.TLabel', background=C['panel'], foreground=C['text_dim'],
                    font=fonts.ui)
    style.configure('DimBg.TLabel', background=C['bg'], foreground=C['text_dim'],
                    font=fonts.micro)
    style.configure('Mono.TLabel', background=C['panel'], foreground=C['text'],
                    font=fonts.mono)
    style.configure('Value.TLabel', background=C['panel'], foreground=C['accent'],
                    font=fonts.mono_big)

    # ---- entries ----------------------------------------------------------
    style.configure('TEntry', fieldbackground=C['inset'], background=C['inset'],
                    foreground=C['text'], insertcolor=C['accent'],
                    bordercolor=C['border'], lightcolor=C['border'],
                    darkcolor=C['border'], borderwidth=1, padding=4)
    style.map('TEntry',
              bordercolor=[('focus', C['accent'])],
              lightcolor=[('focus', C['accent'])],
              darkcolor=[('focus', C['accent'])])

    style.configure('TSpinbox', fieldbackground=C['inset'], background=C['panel_alt'],
                    foreground=C['text'], bordercolor=C['border'],
                    lightcolor=C['border'], darkcolor=C['border'],
                    arrowcolor=C['text_dim'], insertcolor=C['accent'], padding=3)
    style.map('TSpinbox', bordercolor=[('focus', C['accent'])],
              arrowcolor=[('active', C['accent'])])

    style.configure('TCombobox', fieldbackground=C['inset'], background=C['panel_alt'],
                    foreground=C['text'], bordercolor=C['border'],
                    lightcolor=C['border'], darkcolor=C['border'],
                    arrowcolor=C['text_dim'], selectbackground=C['inset'],
                    selectforeground=C['text'], padding=4)
    style.map('TCombobox',
              bordercolor=[('focus', C['accent']), ('hover', C['border_hi'])],
              arrowcolor=[('active', C['accent'])],
              fieldbackground=[('readonly', C['inset'])],
              foreground=[('readonly', C['text'])])

    # ---- checkbuttons -----------------------------------------------------
    style.configure('TCheckbutton', background=C['panel'], foreground=C['text'],
                    font=fonts.ui, indicatorbackground=C['inset'],
                    indicatorforeground=C['accent'], indicatorsize=11,
                    indicatormargin=(1, 1, 6, 1), upperbordercolor=C['border_hi'],
                    lowerbordercolor=C['border_hi'], focuscolor=C['panel'], padding=2)
    style.map('TCheckbutton',
              indicatorbackground=[('selected', C['inset']), ('active', C['panel_alt']),
                                   ('disabled', C['panel'])],
              indicatorforeground=[('selected', C['accent']), ('disabled', C['text_dim'])],
              upperbordercolor=[('selected', C['accent']), ('active', C['border_hi'])],
              lowerbordercolor=[('selected', C['accent']), ('active', C['border_hi'])],
              foreground=[('selected', C['text_hi']), ('active', C['text_hi'])],
              background=[('active', C['panel'])])

    # ---- buttons ----------------------------------------------------------
    style.configure('TButton', background=C['panel_alt'], foreground=C['text'],
                    bordercolor=C['border_hi'], lightcolor=C['panel_alt'],
                    darkcolor=C['panel_alt'], focuscolor=C['panel_alt'],
                    font=fonts.micro_bold, padding=(10, 5), borderwidth=1)
    style.map('TButton',
              background=[('active', C['border']), ('disabled', C['panel'])],
              foreground=[('active', C['text_hi']), ('disabled', C['text_dim'])],
              bordercolor=[('active', C['accent'])])

    style.configure('Accent.TButton', background=C['accent'], foreground='#04120f',
                    bordercolor=C['accent'], lightcolor=C['accent'],
                    darkcolor=C['accent'], font=fonts.micro_bold, padding=(16, 6))
    style.map('Accent.TButton',
              background=[('active', '#4ff2d8'), ('disabled', C['panel_alt'])],
              foreground=[('disabled', C['text_dim'])],
              bordercolor=[('disabled', C['border'])])

    style.configure('Danger.TButton', background=C['panel_alt'], foreground=C['red'],
                    bordercolor=C['border_hi'], lightcolor=C['panel_alt'],
                    darkcolor=C['panel_alt'], font=fonts.micro_bold, padding=(14, 6))
    style.map('Danger.TButton',
              background=[('active', '#2a1418'), ('disabled', C['panel'])],
              foreground=[('disabled', C['text_dim'])],
              bordercolor=[('active', C['red'])])

    # ---- treeview ---------------------------------------------------------
    style.configure('Treeview', background=C['panel'], fieldbackground=C['panel'],
                    foreground=C['text'], bordercolor=C['border'],
                    lightcolor=C['panel'], darkcolor=C['panel'], borderwidth=0,
                    font=fonts.mono, rowheight=26)
    style.map('Treeview',
              background=[('selected', C['sel'])],
              foreground=[('selected', C['text_hi'])])
    style.configure('Treeview.Heading', background=C['panel_alt'],
                    foreground=C['text_dim'], font=fonts.micro_bold,
                    relief='flat', borderwidth=0, padding=(6, 6))
    style.map('Treeview.Heading',
              background=[('active', C['border'])],
              foreground=[('active', C['accent'])])
    style.layout('Treeview.Item', [
        ('Treeitem.padding', {'sticky': 'nswe', 'children': [
            ('Treeitem.text', {'side': 'left', 'sticky': ''}),
        ]}),
    ])

    # ---- notebook ---------------------------------------------------------
    style.configure('TNotebook', background=C['bg'], bordercolor=C['border'],
                    borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure('TNotebook.Tab', background=C['bg'], foreground=C['text_dim'],
                    font=fonts.micro_bold, padding=(18, 7), borderwidth=0,
                    bordercolor=C['border'])
    style.map('TNotebook.Tab',
              background=[('selected', C['panel'])],
              foreground=[('selected', C['accent']), ('active', C['text'])])

    # ---- scrollbars -------------------------------------------------------
    style.configure('Vertical.TScrollbar', background=C['panel_alt'],
                    troughcolor=C['bg'], bordercolor=C['bg'],
                    arrowcolor=C['text_dim'], lightcolor=C['panel_alt'],
                    darkcolor=C['panel_alt'], borderwidth=0, arrowsize=12)
    style.map('Vertical.TScrollbar', background=[('active', C['border_hi'])])
    style.configure('Horizontal.TScrollbar', background=C['panel_alt'],
                    troughcolor=C['bg'], bordercolor=C['bg'],
                    arrowcolor=C['text_dim'], lightcolor=C['panel_alt'],
                    darkcolor=C['panel_alt'], borderwidth=0, arrowsize=12)

    # ---- progressbar ------------------------------------------------------
    style.configure('Thin.Horizontal.TProgressbar', troughcolor=C['inset'],
                    background=C['accent'], bordercolor=C['inset'],
                    lightcolor=C['accent'], darkcolor=C['accent'],
                    borderwidth=0, thickness=6)

    style.configure('TSeparator', background=C['border'])
    return fonts


# --------------------------------------------------------------------------- #
# Custom widgets
# --------------------------------------------------------------------------- #
class Card(ttk.Frame):
    """A panel with a 1px border and an uppercase caption."""

    def __init__(self, master, title: str, fonts: Fonts, accent: str | None = None, **kw):
        outer = ttk.Frame(master, style='Rule.TFrame')
        self.outer = outer
        super().__init__(outer, style='Panel.TFrame', **kw)
        super().pack(fill='both', expand=True, padx=1, pady=1)

        header = ttk.Frame(self, style='Panel.TFrame')
        header.pack(fill='x', padx=12, pady=(9, 0))
        bar = tk.Frame(header, background=accent or C['accent'], width=3, height=11)
        bar.pack(side='left', padx=(0, 7))
        ttk.Label(header, text=title.upper(), style='Head.TLabel').pack(side='left')
        self.header = header

        self.body = ttk.Frame(self, style='Panel.TFrame')
        self.body.pack(fill='both', expand=True, padx=12, pady=(7, 11))

    def grid(self, **kw):
        self.outer.grid(**kw)
        return self

    def pack(self, **kw):
        self.outer.pack(**kw)
        return self


class LED(tk.Canvas):
    """A small status dot with a caption."""

    def __init__(self, master, text: str, colour: str, fonts: Fonts, bg=None):
        bg = bg or C['bg']
        self.font = fonts.micro_bold
        width = 22 + len(text) * 7
        super().__init__(master, width=width, height=16, background=bg,
                         highlightthickness=0, bd=0)
        self._dot = self.create_oval(3, 6, 9, 12, fill=colour, outline=colour)
        self._halo = self.create_oval(1, 4, 11, 14, outline=colour, width=1)
        self._text = self.create_text(15, 8, text=text, anchor='w', fill=C['text_dim'],
                                      font=self.font)

    def set(self, text: str, colour: str):
        self.itemconfigure(self._dot, fill=colour, outline=colour)
        self.itemconfigure(self._halo, outline=colour)
        self.itemconfigure(self._text, text=text)
        self.configure(width=22 + len(text) * 7)


class Sparkline(tk.Canvas):
    """Rolling throughput graph."""

    def __init__(self, master, width=190, height=34, samples=64, colour=None):
        super().__init__(master, width=width, height=height, background=C['inset'],
                         highlightthickness=1, highlightbackground=C['border'], bd=0)
        self.w, self.h, self.n = width, height, samples
        self.colour = colour or C['accent']
        self.data: list[float] = []
        self._draw()

    def push(self, value: float):
        self.data.append(max(0.0, float(value or 0)))
        if len(self.data) > self.n:
            self.data = self.data[-self.n:]
        self._draw()

    def clear(self):
        self.data = []
        self._draw()

    def _draw(self):
        self.delete('all')
        for i in range(1, 4):
            y = self.h * i / 4
            self.create_line(0, y, self.w, y, fill='#0c141c')
        if not self.data:
            self.create_text(self.w / 2, self.h / 2, text='no traffic',
                             fill=C['text_dim'], font=('Segoe UI', 7))
            return
        peak = max(self.data) or 1.0
        step = self.w / max(1, self.n - 1)
        pts = []
        offset = self.n - len(self.data)
        for i, value in enumerate(self.data):
            x = (offset + i) * step
            y = self.h - 2 - (value / peak) * (self.h - 6)
            pts.append((x, y))
        if len(pts) > 1:
            poly = [(pts[0][0], self.h)] + pts + [(pts[-1][0], self.h)]
            self.create_polygon([c for p in poly for c in p], fill='#062a26', outline='')
            self.create_line([c for p in pts for c in p], fill=self.colour, width=1)
        self.create_oval(pts[-1][0] - 2, pts[-1][1] - 2, pts[-1][0] + 2, pts[-1][1] + 2,
                         fill=self.colour, outline=self.colour)


class Tip:
    """Minimal dark hover tooltip."""

    def __init__(self, widget, text: str):
        self.widget, self.text, self.win = widget, text, None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')

    def show(self, _event=None):
        if self.win or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.wm_geometry(f'+{x}+{y}')
        self.win.configure(background=C['border_hi'])
        tk.Label(self.win, text=self.text, justify='left', background=C['panel_alt'],
                 foreground=C['text'], font=('Segoe UI', 8), padx=8, pady=5,
                 ).pack(padx=1, pady=1)

    def hide(self, _event=None):
        if self.win:
            self.win.destroy()
            self.win = None
