"""MediaForge — media acquisition suite.

Paste any mix of video, playlist, profile or channel links. Collections are
expanded into their individual media, downloaded in parallel, and filed into
folders named after the collection.

Run with:  python app.py
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor, wait

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import theme
from theme import C, Card, LED, Sparkline, Tip
from core import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    AUDIO_BITRATES,
    AUDIO_LANGUAGES,
    AUTO_AUDIO,
    BROWSERS,
    CONTAINERS,
    ENGINE_VERSION,
    PLATFORMS,
    QUALITY_PRESETS,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_DOWNLOADING,
    STATUS_ERROR,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    STATUS_SKIPPED,
    Cancelled,
    DownloadItem,
    download_item,
    expand_url,
    ffmpeg_available,
    probe_tracks,
    human_bytes,
    human_speed,
    human_time,
    platform_of,
)

CONFIG_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.config'),
    APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, 'settings.json')

DEFAULT_OUTDIR = os.path.join(os.path.expanduser('~'), 'Downloads', APP_NAME)

# Ask before flooding the queue with an entire channel or profile
LARGE_COLLECTION = 150

DEFAULTS = {
    'outdir': DEFAULT_OUTDIR,
    'quality': next(iter(QUALITY_PRESETS)),
    'container': 'AUTO',
    'audio_bitrate': '192',
    'audio_lang': AUTO_AUDIO,
    'audio_extra': '',
    'subtitles': False,
    'auto_subs': True,
    'embed_subs': True,
    'sub_langs': 'en',
    'thumbnail': False,
    'metadata': True,
    'sponsorblock': False,
    'use_archive': False,
    'overwrite': False,
    'include_id': False,
    'quality_in_name': True,
    'platform_folder': False,
    'playlist_folder': True,
    'number_playlist_items': True,
    'playlist_items': '',
    'playlist_reverse': False,
    'no_playlist': False,
    'workers': 3,
    'fragments': 4,
    'retries': 10,
    'limit_rate': '',
    'cookies_browser': 'None',
    'proxy': '',
}

STATE_STYLE = {
    STATUS_QUEUED: ('queued', C['text_dim']),
    STATUS_DOWNLOADING: ('fetching', C['accent']),
    STATUS_PROCESSING: ('encoding', C['blue']),
    STATUS_DONE: ('done', C['green']),
    STATUS_SKIPPED: ('skipped', C['text_dim']),
    STATUS_ERROR: ('failed', C['red']),
    STATUS_CANCELLED: ('aborted', C['amber']),
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as fp:
            stored = json.load(fp)
        data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return data


def save_settings(data: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as fp:
            json.dump({k: v for k, v in data.items() if k in DEFAULTS}, fp, indent=2)
    except OSError:
        pass


def open_in_explorer(path: str) -> None:
    """Reveal a file, or open a folder, in the system file manager."""
    if not path:
        return
    try:
        if os.name == 'nt':
            if os.path.isfile(path):
                subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
            else:
                os.startfile(path)  # noqa: S606
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path if os.path.isdir(path)
                              else os.path.dirname(path)])
    except OSError:
        pass


def resource_path(name: str) -> str:
    """Path to a bundled resource, in a source tree or inside a frozen build."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def bar_glyphs(percent: float, width: int = 14) -> str:
    filled = int(percent / 100 * width)
    remainder = (percent / 100 * width) - filled
    edge = '▌' if remainder >= 0.5 and filled < width else ''
    pad = width - filled - len(edge)
    return f"{'█' * filled}{edge}{'░' * max(0, pad)} {percent:5.1f}%"


class App(tk.Tk):
    COLUMNS = (
        ('num', '#', 34, 'center', False),
        ('site', 'SOURCE', 84, 'w', False),
        ('title', 'MEDIA', 246, 'w', True),
        ('collection', 'COLLECTION', 150, 'w', True),
        ('duration', 'LEN', 52, 'center', False),
        ('status', 'STATE', 78, 'w', False),
        ('progress', 'TRANSFER', 158, 'w', False),
        ('speed', 'RATE', 78, 'e', False),
        ('eta', 'ETA', 52, 'e', False),
        ('size', 'SIZE', 72, 'e', False),
    )

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.fonts = theme.apply(self)

        self.title(f'{APP_NAME} · {APP_TAGLINE}')
        icon = resource_path('icon.ico')
        if os.path.exists(icon):
            try:
                self.iconbitmap(default=icon)
            except tk.TclError:
                pass
        self.geometry('1420x950')
        self.minsize(1180, 700)

        self.items: dict[int, DownloadItem] = {}
        self.order: list[int] = []
        self.pos: dict[int, int] = {}
        self.events: queue.Queue = queue.Queue()
        self.dirty: set[int] = set()
        self.dirty_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.executor: ThreadPoolExecutor | None = None
        self.futures: list = []
        self.futures_lock = threading.Lock()
        self.running = False
        self.expanding = 0
        self.run_settings: dict = {}
        self.session_bytes = 0
        self.session_start = 0.0
        self._spark_tick = 0

        self._build_ui()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.after(120, self._pump)

        self.console(f'{APP_NAME} {APP_VERSION} online · extraction core {ENGINE_VERSION}', 'ok')
        self.console(f'{len(PLATFORMS)} platforms tagged · 1800+ sites supported by the core')
        if ffmpeg_available():
            self.console('ffmpeg detected · merging, remux, audio extraction enabled', 'ok')
        else:
            self.console('ffmpeg NOT found on PATH — no stream merging, MP3 conversion, '
                         'thumbnail or subtitle embedding. Install it: winget install '
                         'Gyan.FFmpeg', 'warn')

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        self._build_header()
        # packed before the body so the bottom bar always keeps its space
        self._build_telemetry()

        body = ttk.Frame(self, style='TFrame')
        body.pack(fill='both', expand=True, padx=12, pady=(10, 0))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # the config rail scrolls, so it survives short screens
        rail = ttk.Frame(body, style='TFrame', width=352)
        rail.grid(row=0, column=0, sticky='ns')
        rail.grid_propagate(False)
        rail.rowconfigure(0, weight=1)
        rail.columnconfigure(0, weight=1)

        canvas = tk.Canvas(rail, background=C['bg'], highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky='nsew')
        rail_sb = ttk.Scrollbar(rail, orient='vertical', command=canvas.yview)
        rail_sb.grid(row=0, column=1, sticky='ns')
        canvas.configure(yscrollcommand=rail_sb.set)

        sidebar = ttk.Frame(canvas, style='TFrame')
        window = canvas.create_window((0, 0), window=sidebar, anchor='nw')
        sidebar.bind('<Configure>',
                     lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))

        right = ttk.Frame(body, style='TFrame')
        right.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_sidebar(sidebar)
        self._build_intake(right)
        self._build_queue(right)
        self._bind_wheel(sidebar, canvas)

    @staticmethod
    def _bind_wheel(root_widget, canvas):
        def scroll(event):
            canvas.yview_scroll(-3 if event.delta > 0 else 3, 'units')
            return 'break'

        stack = [root_widget]
        while stack:
            widget = stack.pop()
            widget.bind('<MouseWheel>', scroll)
            stack.extend(widget.winfo_children())
        canvas.bind('<MouseWheel>', scroll)

    def _build_header(self):
        head = ttk.Frame(self, style='Panel.TFrame')
        head.pack(fill='x')
        inner = ttk.Frame(head, style='Panel.TFrame')
        inner.pack(fill='x', padx=16, pady=11)

        mark = tk.Canvas(inner, width=30, height=30, background=C['panel'],
                         highlightthickness=0, bd=0)
        mark.pack(side='left', padx=(0, 11))
        mark.create_polygon(3, 4, 15, 4, 21, 15, 15, 26, 3, 26, 9, 15,
                            fill='', outline=C['accent'], width=2)
        mark.create_polygon(12, 10, 12, 21, 21, 15.5, fill=C['accent'], outline=C['accent'])
        mark.create_line(24, 7, 27, 7, fill=C['accent_dim'], width=2)
        mark.create_line(24, 15, 29, 15, fill=C['accent_dim'], width=2)
        mark.create_line(24, 23, 27, 23, fill=C['accent_dim'], width=2)

        title_box = ttk.Frame(inner, style='Panel.TFrame')
        title_box.pack(side='left')
        name = tk.Label(title_box, text=APP_NAME.upper(), background=C['panel'],
                        foreground=C['text_hi'], font=self.fonts.brand)
        name.pack(side='top', anchor='w')
        ttk.Label(title_box, text=APP_TAGLINE, style='Head.TLabel').pack(side='top', anchor='w')

        right = ttk.Frame(inner, style='Panel.TFrame')
        right.pack(side='right')

        self.led_engine = LED(right, f'CORE {ENGINE_VERSION}', C['accent'], self.fonts,
                              bg=C['panel'])
        self.led_engine.pack(side='right', padx=(14, 0))
        ok = ffmpeg_available()
        self.led_ffmpeg = LED(right, 'FFMPEG' if ok else 'NO FFMPEG',
                              C['green'] if ok else C['amber'], self.fonts, bg=C['panel'])
        self.led_ffmpeg.pack(side='right', padx=(14, 0))
        self.led_state = LED(right, 'IDLE', C['text_dim'], self.fonts, bg=C['panel'])
        self.led_state.pack(side='right', padx=(14, 0))
        ttk.Label(right, text=f'v{APP_VERSION}', style='Head.TLabel').pack(side='right')

        ttk.Frame(self, style='Accent.TFrame', height=2).pack(fill='x')

    # ---------------------------------------------------------- sidebar ----
    def _build_sidebar(self, parent):
        s = self.settings
        self.var: dict[str, tk.Variable] = {}

        def mkvar(key, kind=tk.StringVar):
            v = kind(value=s.get(key, DEFAULTS.get(key)))
            self.var[key] = v
            return v

        def checks(parent_frame, specs, columns=1):
            for i, (key, label, tip) in enumerate(specs):
                cb = ttk.Checkbutton(parent_frame, text=label, variable=mkvar(key, tk.BooleanVar))
                cb.grid(row=i // columns, column=i % columns, sticky='w', pady=1)
                if tip:
                    Tip(cb, tip)

        # ---- OUTPUT
        out = Card(parent, 'output', self.fonts)
        out.pack(fill='x')
        b = out.body
        b.columnconfigure(0, weight=1)

        path_row = ttk.Frame(b, style='Panel.TFrame')
        path_row.grid(row=0, column=0, sticky='ew')
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=mkvar('outdir'), font=self.fonts.mono_sm).grid(
            row=0, column=0, sticky='ew')
        btn_row = ttk.Frame(b, style='Panel.TFrame')
        btn_row.grid(row=1, column=0, sticky='ew', pady=(6, 8))
        ttk.Button(btn_row, text='BROWSE', command=self.pick_folder).pack(side='left')
        ttk.Button(btn_row, text='OPEN',
                   command=lambda: open_in_explorer(self.var['outdir'].get())).pack(
            side='left', padx=(6, 0))
        ttk.Button(btn_row, text='RESET',
                   command=lambda: self.var['outdir'].set(DEFAULT_OUTDIR)).pack(
            side='left', padx=(6, 0))

        opt = ttk.Frame(b, style='Panel.TFrame')
        opt.grid(row=2, column=0, sticky='ew')
        checks(opt, [
            ('platform_folder', 'Sort into per-source folders',
             'YOUTUBE\\... , INSTAGRAM\\... etc.'),
            ('quality_in_name', 'Append quality to filename',
             'Title [1080p].mkv\n\nKeeps renditions apart, so the same video can be\n'
             'kept at several qualities in one folder. Without it the\n'
             'second download collides with the first and is skipped.'),
            ('include_id', 'Append media id to filename', 'Title [dQw4w9WgXcQ].mp4'),
            ('use_archive', 'Skip media already downloaded',
             'Keeps an archive file in the output folder.\nRe-running a playlist then '
             'fetches only what is new.'),
            ('overwrite', 'Overwrite existing files', None),
        ])

        # ---- FORMAT
        fmt = Card(parent, 'format', self.fonts, accent=C['blue'])
        fmt.pack(fill='x', pady=(10, 0))
        b = fmt.body
        b.columnconfigure(1, weight=1)

        ttk.Label(b, text='PROFILE', style='Head.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Combobox(b, textvariable=mkvar('quality'), state='readonly',
                     values=list(QUALITY_PRESETS), font=self.fonts.mono_sm).grid(
            row=1, column=0, columnspan=2, sticky='ew', pady=(2, 7))

        ttk.Label(b, text='AUDIO TRACK', style='Head.TLabel').grid(row=2, column=0,
                                                                    sticky='w', pady=(0, 4))
        track = ttk.Combobox(b, textvariable=mkvar('audio_lang'), state='readonly',
                             values=list(AUDIO_LANGUAGES), font=self.fonts.mono_sm)
        track.grid(row=2, column=1, sticky='ew', padx=(8, 0), pady=(0, 4))
        Tip(track, 'Which dubbed audio track to take when a video carries\n'
                   'more than one. Falls back to the original track if that\n'
                   'language is not offered. Right-click a queued row and\n'
                   'pick "Inspect tracks" to list what a video actually has.')

        extra = ttk.Frame(b, style='Panel.TFrame')
        extra.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(0, 7))
        extra.columnconfigure(1, weight=1)
        ttk.Label(extra, text='ALSO EMBED', style='Head.TLabel').grid(row=0, column=0,
                                                                     sticky='w')
        extra_entry = ttk.Entry(extra, textvariable=mkvar('audio_extra'),
                                font=self.fonts.mono_sm)
        extra_entry.grid(row=0, column=1, sticky='ew', padx=(8, 0))
        Tip(extra_entry, 'Extra audio tracks to mux into the same file:\n'
                         '   ta,en      those languages alongside the main one\n'
                         '   all        every dubbed track the media carries\n'
                         'Empty keeps a single audio track. Video profiles only.')

        ttk.Label(b, text='CONTAINER', style='Head.TLabel').grid(row=4, column=0, sticky='w')
        ttk.Label(b, text='AUDIO kbps', style='Head.TLabel').grid(row=4, column=1, sticky='w',
                                                                  padx=(8, 0))
        cont = ttk.Combobox(b, textvariable=mkvar('container'), state='readonly', width=9,
                            values=CONTAINERS, font=self.fonts.mono_sm)
        cont.grid(row=5, column=0, sticky='ew', pady=(2, 8))
        Tip(cont, 'AUTO  best available streams; usually lands as .mkv\n'
                  '      because the best audio is Opus, which mp4 cannot hold\n'
                  'mp4   forces a real .mp4 - picks AAC audio and an\n'
                  '      mp4-compatible video stream, no re-encoding\n'
                  'mkv   holds anything; best quality with any codec\n'
                  'webm  VP9/AV1 + Opus')
        ttk.Combobox(b, textvariable=mkvar('audio_bitrate'), state='readonly', width=9,
                     values=AUDIO_BITRATES, font=self.fonts.mono_sm).grid(
            row=5, column=1, sticky='ew', padx=(8, 0), pady=(2, 8))

        opt = ttk.Frame(b, style='Panel.TFrame')
        opt.grid(row=6, column=0, columnspan=2, sticky='ew')
        checks(opt, [('subtitles', 'Subtitles', 'Download subtitle tracks.')])

        subs = ttk.Frame(b, style='Panel.TFrame')
        subs.grid(row=7, column=0, columnspan=2, sticky='ew', padx=(19, 0), pady=(2, 0))
        subs.columnconfigure(1, weight=1)
        ttk.Label(subs, text='LANGS', style='Head.TLabel').grid(row=0, column=0, sticky='w')
        sub_entry = ttk.Entry(subs, textvariable=mkvar('sub_langs'), font=self.fonts.mono_sm)
        sub_entry.grid(row=0, column=1, sticky='ew', padx=(8, 0))
        Tip(sub_entry, 'Subtitle languages:   en    en,ta,hi    all\n'
                       '"all" takes every language the video offers.')

        subopt = ttk.Frame(b, style='Panel.TFrame')
        subopt.grid(row=8, column=0, columnspan=2, sticky='ew', padx=(19, 0))
        checks(subopt, [
            ('auto_subs', 'Include auto-generated',
             'Machine captions. A video can carry 150+ of these,\nso avoid pairing this '
             'with "all".'),
            ('embed_subs', 'Embed into the video file',
             'Off leaves them as separate subtitle files\nnext to the video.'),
        ])

        opt2 = ttk.Frame(b, style='Panel.TFrame')
        opt2.grid(row=9, column=0, columnspan=2, sticky='ew', pady=(5, 0))
        checks(opt2, [
            ('thumbnail', 'Embed cover thumbnail', None),
            ('metadata', 'Write metadata + chapters', None),
            ('sponsorblock', 'Cut sponsor segments',
             'Removes SponsorBlock-flagged sponsor reads.'),
        ])

        # ---- COLLECTIONS
        col = Card(parent, 'collections  ·  playlists & profiles', self.fonts,
                   accent=C['purple'])
        col.pack(fill='x', pady=(10, 0))
        b = col.body
        b.columnconfigure(0, weight=1)

        opt = ttk.Frame(b, style='Panel.TFrame')
        opt.grid(row=0, column=0, sticky='ew')
        checks(opt, [
            ('playlist_folder', 'Folder per collection',
             'Files land in  <output>\\<playlist name>\\'),
            ('number_playlist_items', 'Number files by position', '007 - Title.mp4'),
            ('playlist_reverse', 'Reverse order (oldest first)', None),
            ('no_playlist', 'Single media only — ignore collection',
             'A link that points at one video inside a playlist\n'
             'downloads just that video.'),
        ])

        rng = ttk.Frame(b, style='Panel.TFrame')
        rng.grid(row=1, column=0, sticky='ew', pady=(7, 0))
        rng.columnconfigure(1, weight=1)
        ttk.Label(rng, text='RANGE', style='Head.TLabel').grid(row=0, column=0, sticky='w')
        entry = ttk.Entry(rng, textvariable=mkvar('playlist_items'), font=self.fonts.mono_sm)
        entry.grid(row=0, column=1, sticky='ew', padx=(8, 0))
        Tip(entry, 'Which entries to take:   1-25    3,7,12-    -10\n'
                   'Empty takes the whole collection.')

        # ---- NETWORK & AUTH
        net = Card(parent, 'network  ·  auth', self.fonts, accent=C['amber'])
        net.pack(fill='x', pady=(10, 0))
        b = net.body
        b.columnconfigure(1, weight=1)

        ttk.Label(b, text='THREADS', style='Head.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(b, text='RATE CAP', style='Head.TLabel').grid(row=0, column=1, sticky='w',
                                                                padx=(8, 0))
        ttk.Spinbox(b, from_=1, to=8, textvariable=mkvar('workers', tk.IntVar),
                    font=self.fonts.mono_sm, width=6).grid(row=1, column=0, sticky='ew',
                                                           pady=(2, 8))
        rate = ttk.Entry(b, textvariable=mkvar('limit_rate'), font=self.fonts.mono_sm)
        rate.grid(row=1, column=1, sticky='ew', padx=(8, 0), pady=(2, 8))
        Tip(rate, 'Per-download cap:  2M   500K\nEmpty = unlimited')

        ttk.Label(b, text='SESSION COOKIES', style='Head.TLabel').grid(
            row=2, column=0, columnspan=2, sticky='w')
        cookie = ttk.Combobox(b, textvariable=mkvar('cookies_browser'), state='readonly',
                              values=BROWSERS, font=self.fonts.mono_sm)
        cookie.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(2, 3))
        Tip(cookie, 'Reuse a browser login for private, age-restricted\n'
                    'or members-only media. Instagram almost always\n'
                    'needs this. Close the browser first — Chromium\n'
                    'browsers lock their cookie database while open.')
        ttk.Label(b, text='required for Instagram · private media',
                  style='Dim.TLabel', font=self.fonts.micro).grid(
            row=4, column=0, columnspan=2, sticky='w', pady=(0, 8))

        ttk.Label(b, text='PROXY', style='Head.TLabel').grid(row=5, column=0, columnspan=2,
                                                             sticky='w')
        proxy = ttk.Entry(b, textvariable=mkvar('proxy'), font=self.fonts.mono_sm)
        proxy.grid(row=6, column=0, columnspan=2, sticky='ew', pady=(2, 0))
        Tip(proxy, 'socks5://127.0.0.1:1080   http://host:port')

    # ----------------------------------------------------------- intake ----
    def _build_intake(self, parent):
        card = Card(parent, 'intake  ·  target links', self.fonts)
        card.grid(row=0, column=0, sticky='ew')
        b = card.body
        b.columnconfigure(0, weight=1)

        wrap = tk.Frame(b, background=C['border'])
        wrap.grid(row=0, column=0, sticky='ew')
        self.url_text = tk.Text(wrap, height=4, wrap='none', font=self.fonts.mono,
                                background=C['inset'], foreground=C['text'],
                                insertbackground=C['accent'], relief='flat',
                                highlightthickness=0, bd=0, padx=9, pady=7,
                                selectbackground=C['sel'], selectforeground=C['text_hi'],
                                undo=True)
        self.url_text.pack(fill='both', expand=True, padx=1, pady=1)
        self.url_text.bind('<Control-Return>', lambda e: (self.add_urls(), 'break')[1])

        side = ttk.Frame(b, style='Panel.TFrame')
        side.grid(row=0, column=1, sticky='n', padx=(10, 0))
        ttk.Button(side, text='▸  ANALYZE', style='Accent.TButton',
                   command=self.add_urls).pack(fill='x')
        ttk.Button(side, text='PASTE', command=self.paste_clipboard).pack(fill='x', pady=(6, 0))
        ttk.Button(side, text='CLEAR',
                   command=lambda: self.url_text.delete('1.0', 'end')).pack(fill='x',
                                                                            pady=(6, 0))

        chips = ttk.Frame(b, style='Panel.TFrame')
        chips.grid(row=1, column=0, columnspan=2, sticky='w', pady=(9, 0))
        ttk.Label(chips, text='ACCEPTS', style='Head.TLabel').pack(side='left', padx=(0, 9))
        for tag, _pattern, colour, _login in PLATFORMS[:7]:
            chip = tk.Label(chips, text=tag, background=C['panel_alt'], foreground=colour,
                            font=self.fonts.micro_bold, padx=7, pady=2)
            chip.pack(side='left', padx=(0, 5))
        ttk.Label(chips, text='+ 1800 MORE  ·  playlists, profiles and channels expand '
                              'automatically', style='Dim.TLabel',
                  font=self.fonts.micro).pack(side='left', padx=(4, 0))

    # ------------------------------------------------------------ queue ----
    def _build_queue(self, parent):
        nb = ttk.Notebook(parent)
        nb.grid(row=1, column=0, sticky='nsew', pady=(10, 0))
        self.notebook = nb

        qwrap = tk.Frame(nb, background=C['border'])
        nb.add(qwrap, text='QUEUE')
        qframe = ttk.Frame(qwrap, style='Panel.TFrame')
        qframe.pack(fill='both', expand=True, padx=1, pady=1)
        qframe.columnconfigure(0, weight=1)
        qframe.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(qframe, columns=[c[0] for c in self.COLUMNS],
                                 show='headings', selectmode='extended')
        for key, heading, width, anchor, stretch in self.COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch,
                             minwidth=40)
        self.tree.grid(row=0, column=0, sticky='nsew')
        sb = ttk.Scrollbar(qframe, orient='vertical', command=self.tree.yview)
        sb.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=sb.set)

        for name, colour in STATE_STYLE.values():
            self.tree.tag_configure(name, foreground=colour)
        self.tree.tag_configure('odd', background=C['row_alt'])
        self.tree.tag_configure('even', background=C['panel'])

        self.tree.bind('<Double-1>', self.on_row_activate)
        self.tree.bind('<Button-3>', self.on_right_click)
        self.tree.bind('<Delete>', lambda e: self.remove_selected())

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label='Open file', command=lambda: self.open_selected(False))
        self.menu.add_command(label='Show in folder', command=lambda: self.open_selected(True))
        self.menu.add_separator()
        self.menu.add_command(label='Inspect tracks', command=self.inspect_tracks)
        self.menu.add_separator()
        self.menu.add_command(label='Open source page', command=self.open_link)
        self.menu.add_command(label='Copy link', command=self.copy_link)
        self.menu.add_separator()
        self.menu.add_command(label='Requeue', command=self.retry_selected)
        self.menu.add_command(label='Force re-download', command=self.force_selected)
        self.menu.add_command(label='Remove', command=self.remove_selected)

        cwrap = tk.Frame(nb, background=C['border'])
        nb.add(cwrap, text='CONSOLE')
        cframe = ttk.Frame(cwrap, style='Panel.TFrame')
        cframe.pack(fill='both', expand=True, padx=1, pady=1)
        self.log_text = tk.Text(cframe, wrap='word', font=self.fonts.mono_sm,
                                background=C['inset'], foreground=C['text'],
                                relief='flat', highlightthickness=0, bd=0,
                                padx=10, pady=8, state='disabled',
                                selectbackground=C['sel'])
        self.log_text.pack(side='left', fill='both', expand=True)
        lsb = ttk.Scrollbar(cframe, orient='vertical', command=self.log_text.yview)
        lsb.pack(side='right', fill='y')
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.tag_configure('ts', foreground=C['text_dim'])
        self.log_text.tag_configure('info', foreground=C['text'])
        self.log_text.tag_configure('ok', foreground=C['green'])
        self.log_text.tag_configure('warn', foreground=C['amber'])
        self.log_text.tag_configure('err', foreground=C['red'])
        self.log_text.tag_configure('dim', foreground=C['text_dim'])

    # -------------------------------------------------------- telemetry ----
    def _build_telemetry(self):
        outer = ttk.Frame(self, style='TFrame')
        outer.pack(side='bottom', fill='x', padx=12, pady=(10, 12))
        wrap = tk.Frame(outer, background=C['border'])
        wrap.pack(fill='x')
        bar = ttk.Frame(wrap, style='Panel.TFrame')
        bar.pack(fill='x', padx=1, pady=1)

        row1 = ttk.Frame(bar, style='Panel.TFrame')
        row1.pack(fill='x', padx=14, pady=(10, 0))
        row2 = ttk.Frame(bar, style='Panel.TFrame')
        row2.pack(fill='x', padx=14, pady=(9, 11))

        stats = ttk.Frame(row1, style='Panel.TFrame')
        stats.pack(side='left')
        self.stat_labels = {}
        for key, caption, colour in (('total', 'QUEUE', C['text_hi']),
                                     ('active', 'ACTIVE', C['accent']),
                                     ('done', 'DONE', C['green']),
                                     ('failed', 'FAILED', C['red'])):
            block = ttk.Frame(stats, style='Panel.TFrame')
            block.pack(side='left', padx=(0, 22))
            value = tk.Label(block, text='0', background=C['panel'], foreground=colour,
                             font=self.fonts.mono_big)
            value.pack(anchor='w')
            ttk.Label(block, text=caption, style='Head.TLabel').pack(anchor='w')
            self.stat_labels[key] = value

        graph = ttk.Frame(row1, style='Panel.TFrame')
        graph.pack(side='left', padx=(6, 0))
        self.spark = Sparkline(graph, width=180, height=38)
        self.spark.pack(side='left')
        readout = ttk.Frame(graph, style='Panel.TFrame')
        readout.pack(side='left', padx=(10, 0))
        self.rate_label = tk.Label(readout, text='0 B/s', background=C['panel'],
                                   foreground=C['accent'], font=self.fonts.mono_big)
        self.rate_label.pack(anchor='w')
        self.volume_label = ttk.Label(readout, text='0 B TRANSFERRED', style='Head.TLabel')
        self.volume_label.pack(anchor='w')

        actions = ttk.Frame(row1, style='Panel.TFrame')
        actions.pack(side='right')
        self.btn_start = ttk.Button(actions, text='▶  EXECUTE', style='Accent.TButton',
                                    command=self.start)
        self.btn_start.pack(side='right')
        self.btn_stop = ttk.Button(actions, text='■ ABORT', style='Danger.TButton',
                                   command=self.stop, state='disabled')
        self.btn_stop.pack(side='right', padx=(0, 8))
        ttk.Button(actions, text='REQUEUE FAILED', command=self.retry_failed).pack(
            side='right', padx=(0, 8))
        ttk.Button(actions, text='PURGE DONE', command=self.clear_finished).pack(
            side='right', padx=(0, 8))
        ttk.Button(actions, text='REMOVE', command=self.remove_selected).pack(
            side='right', padx=(0, 8))

        row2.columnconfigure(0, weight=1)
        self.overall = ttk.Progressbar(row2, style='Thin.Horizontal.TProgressbar',
                                       mode='determinate', maximum=100)
        self.overall.grid(row=0, column=0, sticky='ew', pady=(4, 0))
        self.status_lbl = tk.Label(row2, text='IDLE', background=C['panel'],
                                   foreground=C['text_dim'], font=self.fonts.micro_bold,
                                   anchor='e')
        self.status_lbl.grid(row=0, column=1, sticky='e', padx=(14, 0))

    # ------------------------------------------------------------- utils ---
    def collect_settings(self) -> dict:
        data = dict(self.settings)
        for key, var in self.var.items():
            try:
                data[key] = var.get()
            except tk.TclError:
                pass
        return data

    def console(self, message: str, level: str = 'info') -> None:
        self.log_text.configure(state='normal')
        self.log_text.insert('end', time.strftime('%H:%M:%S  '), 'ts')
        self.log_text.insert('end', message.rstrip() + '\n', level)
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def console_threadsafe(self, message: str, level: str = 'info') -> None:
        self.events.put(('log', message, level))

    def mark_dirty(self, item: DownloadItem) -> None:
        with self.dirty_lock:
            self.dirty.add(item.uid)

    def selected_items(self) -> list[DownloadItem]:
        return [self.items[int(i)] for i in self.tree.selection() if int(i) in self.items]

    # -------------------------------------------------------- URL intake ---
    def paste_clipboard(self):
        try:
            data = self.clipboard_get()
        except tk.TclError:
            return
        if data.strip():
            current = self.url_text.get('1.0', 'end').strip()
            self.url_text.insert('end', ('\n' if current else '') + data.strip())

    def pick_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.var['outdir'].get() or DEFAULT_OUTDIR,
                                         title=f'{APP_NAME} — output folder')
        if chosen:
            self.var['outdir'].set(os.path.normpath(chosen))

    def add_urls(self):
        raw = self.url_text.get('1.0', 'end')
        urls = [line.strip() for line in raw.splitlines() if line.strip()]
        urls = [u for u in urls if not u.startswith('#')]
        if not urls:
            messagebox.showinfo(APP_NAME, 'Paste at least one link first.')
            return

        settings = self.collect_settings()
        if settings.get('cookies_browser', 'None') == 'None':
            gated = {platform_of(u)[0] for u in urls if platform_of(u)[2]}
            if gated:
                self.console(f'{", ".join(sorted(gated))} usually needs a login — set '
                             f'SESSION COOKIES in the sidebar if extraction fails.', 'warn')

        self.url_text.delete('1.0', 'end')
        for url in urls:
            self.expanding += 1
            threading.Thread(target=self._expand_worker, args=(url, settings),
                             daemon=True).start()
        self.set_status(f'ANALYZING {len(urls)} LINK(S)')

    def _expand_worker(self, url: str, settings: dict):
        try:
            self.events.put(('log', f'resolving  {url}', 'dim'))
            items, label = expand_url(url, settings, self.console_threadsafe)
            self.events.put(('items', items, label, url))
        except Exception as exc:  # noqa: BLE001 - surfaced in the console
            self.events.put(('expand_error', url, str(exc)))
        finally:
            self.events.put(('expand_done',))

    def add_items(self, items: list[DownloadItem]) -> list[DownloadItem]:
        existing = {self.items[u].url for u in self.order}
        added = []
        for item in items:
            if item.url in existing:
                continue
            existing.add(item.url)
            self.items[item.uid] = item
            self.order.append(item.uid)
            self.pos[item.uid] = len(self.order)
            self.tree.insert('', 'end', iid=str(item.uid), values=self.row_values(item),
                             tags=self.row_tags(item))
            added.append(item)
        return added

    def row_values(self, item: DownloadItem):
        return (
            self.pos.get(item.uid, 0),
            item.platform,
            item.display_title,
            item.source,
            human_time(item.duration),
            item.status,
            self.transfer_cell(item),
            item.speed,
            item.eta,
            item.size,
        )

    def row_tags(self, item: DownloadItem):
        name = STATE_STYLE.get(item.status, ('queued', C['text_dim']))[0]
        stripe = 'odd' if self.pos.get(item.uid, 0) % 2 else 'even'
        return (name, stripe)

    def transfer_cell(self, item: DownloadItem) -> str:
        if item.status == STATUS_ERROR:
            return (item.error or 'extraction failed')[:58]
        if item.status == STATUS_SKIPPED:
            return 'already on disk'
        if item.status == STATUS_QUEUED and not item.percent:
            return ''
        return bar_glyphs(item.percent)

    def refresh_row(self, item: DownloadItem):
        iid = str(item.uid)
        if self.tree.exists(iid):
            self.tree.item(iid, values=self.row_values(item), tags=self.row_tags(item))

    # --------------------------------------------------------- execution ---
    def start(self):
        pending = [self.items[u] for u in self.order if self.items[u].status == STATUS_QUEUED]
        if not pending:
            messagebox.showinfo(APP_NAME, 'Nothing queued. Analyze some links first.')
            return
        if self.running:
            return

        self.run_settings = self.collect_settings()
        outdir = self.run_settings.get('outdir') or DEFAULT_OUTDIR
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f'Cannot create output folder:\n{exc}')
            return
        self.settings = self.run_settings
        save_settings(self.settings)

        self.cancel_event = threading.Event()
        with self.futures_lock:
            self.futures = []
        self.running = True
        self.session_bytes = 0
        self.session_start = time.time()
        self.spark.clear()
        self.btn_start.configure(state='disabled')
        self.btn_stop.configure(state='normal')
        self.led_state.set('RUNNING', C['accent'])

        if self.run_settings.get('subtitles'):
            langs = str(self.run_settings.get('sub_langs') or 'en').lower()
            if 'all' in langs.split(',') and self.run_settings.get('auto_subs'):
                self.console('subtitles set to "all" with auto-generated on — a single '
                             'video can carry 150+ caption files', 'warn')

        workers = max(1, min(8, int(self.run_settings.get('workers', 3))))
        self.console(f'execute · {len(pending)} item(s) · {workers} thread(s) · '
                     f'{self.run_settings.get("quality")}', 'ok')
        self.console(f'output → {outdir}', 'dim')

        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='forge')
        started = self.submit(pending)
        threading.Thread(target=self._await_batch, args=(started,), daemon=True).start()

    def submit(self, items):
        """Queue *items* on the running executor and track their futures."""
        if self.executor is None:
            return []
        futures = [self.executor.submit(self._download_worker, it) for it in items]
        with self.futures_lock:
            self.futures.extend(futures)
        return futures

    def _download_worker(self, item: DownloadItem):
        if self.cancel_event.is_set():
            item.status = STATUS_CANCELLED
            self.mark_dirty(item)
            return
        settings = self.run_settings
        if getattr(item, 'force', False):
            settings = dict(settings, use_archive=False, overwrite=True)
        try:
            download_item(item, settings, self.mark_dirty,
                          self.console_threadsafe, self.cancel_event)
            self.events.put(('done', item))
        except Cancelled:
            item.status = STATUS_CANCELLED
        except Exception as exc:  # noqa: BLE001 - reported per item
            item.status = STATUS_ERROR
            item.error = str(exc).replace('ERROR: ', '').strip()
            item.speed = item.eta = ''
            item.speed_raw = 0.0
            self.events.put(('log', f'✗ {item.display_title or item.url} — {item.error}',
                             'err'))
        finally:
            self.mark_dirty(item)

    def _await_batch(self, futures):
        pending = list(futures)
        while pending:
            wait(pending)
            with self.futures_lock:
                self.futures = [f for f in self.futures if not f.done()]
                pending = list(self.futures)
        self.events.put(('batch_done',))

    def stop(self):
        if not self.running:
            return
        self.cancel_event.set()
        self.btn_stop.configure(state='disabled')
        self.led_state.set('ABORTING', C['amber'])
        self.set_status('ABORTING')
        self.console('abort requested — closing active transfers', 'warn')
        with self.futures_lock:
            for fut in self.futures:
                fut.cancel()
        if self.executor:
            threading.Thread(target=self.executor.shutdown,
                             kwargs={'wait': False, 'cancel_futures': True},
                             daemon=True).start()

    def _batch_finished(self):
        self.running = False
        self.executor = None
        with self.futures_lock:
            self.futures = []
        self.btn_start.configure(state='normal')
        self.btn_stop.configure(state='disabled')
        done = sum(1 for u in self.order
                   if self.items[u].status in (STATUS_DONE, STATUS_SKIPPED))
        failed = sum(1 for u in self.order if self.items[u].status == STATUS_ERROR)
        elapsed = time.time() - self.session_start if self.session_start else 0
        self.console(f'batch complete · {done} ok · {failed} failed · '
                     f'{human_bytes(self.session_bytes) or "0 B"} in {human_time(elapsed)}',
                     'ok' if not failed else 'warn')
        self.led_state.set('IDLE', C['text_dim'])
        self.set_status(f'{done} OK · {failed} FAILED')
        self.rate_label.configure(text='0 B/s')

    # ---------------------------------------------------------- UI pump ----
    def _pump(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == 'log':
                    self.console(event[1], event[2] if len(event) > 2 else 'info')
                elif kind == 'done':
                    item = event[1]
                    if item.status == STATUS_DONE:
                        tag = f'  [audio: {item.audio_lang}]' if item.audio_lang else ''
                        self.console(f'✓ {item.display_title}{tag}', 'ok')
                    elif item.status == STATUS_SKIPPED:
                        self.console(f'• {item.display_title} — already recorded for '
                                     f'this profile. Right-click ▸ Force re-download to '
                                     f'fetch it anyway.', 'warn')
                elif kind == 'items':
                    items, label, _url = event[1], event[2], event[3]
                    if len(items) > LARGE_COLLECTION and not messagebox.askyesno(
                            APP_NAME,
                            f'"{label}" holds {len(items)} media items.\n\n'
                            f'Add all of them to the queue?\n\n'
                            f'Cancel and use COLLECTIONS ▸ RANGE (e.g. 1-25) '
                            f'to take only part of it.'):
                        self.console(f'skipped "{label}" ({len(items)} items)', 'warn')
                        continue
                    added = self.add_items(items)
                    if len(items) > 1:
                        self.console(f'collection "{label}" → {len(items)} media, '
                                     f'{len(added)} queued', 'ok')
                    else:
                        self.console(f'queued  {label}', 'ok')
                    self.renumber()
                    if self.running and added:
                        self.submit(added)
                        self.console(f'{len(added)} item(s) joined the running batch', 'dim')
                elif kind == 'expand_error':
                    self.console(f'✗ {event[1]} — {event[2]}', 'err')
                elif kind == 'expand_done':
                    self.expanding = max(0, self.expanding - 1)
                elif kind == 'batch_done':
                    self._batch_finished()
        except queue.Empty:
            pass

        with self.dirty_lock:
            dirty, self.dirty = self.dirty, set()
        for uid in dirty:
            item = self.items.get(uid)
            if item is not None:
                self.refresh_row(item)

        self.update_telemetry()
        self.after(150, self._pump)

    def update_telemetry(self):
        total = len(self.order)
        active = sum(1 for u in self.order if self.items[u].active)
        done = sum(1 for u in self.order
                   if self.items[u].status in (STATUS_DONE, STATUS_SKIPPED))
        failed = sum(1 for u in self.order if self.items[u].status == STATUS_ERROR)
        self.stat_labels['total'].configure(text=str(total))
        self.stat_labels['active'].configure(text=str(active))
        self.stat_labels['done'].configure(text=str(done))
        self.stat_labels['failed'].configure(text=str(failed))

        rate = sum(self.items[u].speed_raw for u in self.order if self.items[u].active)
        self.session_bytes = sum(self.items[u].bytes_done for u in self.order)
        self._spark_tick += 1
        if self._spark_tick % 3 == 0 and (self.running or rate):
            self.spark.push(rate)
        self.rate_label.configure(text=human_speed(rate) or '0 B/s')
        self.volume_label.configure(text=f'{human_bytes(self.session_bytes) or "0 B"} '
                                         f'TRANSFERRED')

        if not total:
            self.overall['value'] = 0
            if not self.expanding:
                self.set_status('IDLE')
            return

        percent = sum(100.0 if self.items[u].status in (STATUS_DONE, STATUS_SKIPPED)
                      else self.items[u].percent for u in self.order) / total
        self.overall['value'] = percent
        if self.running:
            finished = sum(1 for u in self.order if self.items[u].finished)
            self.set_status(f'{finished}/{total} DONE · {active} ACTIVE · {percent:.1f}%')
        elif self.expanding:
            self.set_status(f'ANALYZING {self.expanding} LINK(S)')

    def set_status(self, text: str):
        self.status_lbl.configure(text=text)

    def renumber(self):
        self.pos = {uid: n for n, uid in enumerate(self.order, start=1)}
        for uid, n in self.pos.items():
            iid = str(uid)
            if self.tree.exists(iid):
                self.tree.set(iid, 'num', n)
                self.tree.item(iid, tags=self.row_tags(self.items[uid]))

    # ------------------------------------------------------ row actions ----
    def on_row_activate(self, _event):
        for item in self.selected_items():
            if item.filepath and os.path.exists(item.filepath):
                try:
                    os.startfile(item.filepath)  # noqa: S606
                except (OSError, AttributeError):
                    open_in_explorer(item.filepath)
            else:
                webbrowser.open(item.url)
            break

    def on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def open_selected(self, reveal: bool):
        for item in self.selected_items():
            target = item.filepath or self.var['outdir'].get()
            if reveal or not item.filepath:
                open_in_explorer(target)
            else:
                try:
                    os.startfile(target)  # noqa: S606
                except (OSError, AttributeError):
                    open_in_explorer(target)
            break

    def inspect_tracks(self):
        chosen = self.selected_items()
        if not chosen:
            return
        item = chosen[0]
        self.notebook.select(1)
        self.console(f'probing tracks for {item.display_title} ...', 'dim')
        threading.Thread(target=self._inspect_worker,
                         args=(item, self.collect_settings()), daemon=True).start()

    def _inspect_worker(self, item: DownloadItem, settings: dict):
        try:
            found = probe_tracks(item.url, settings)
        except Exception as exc:  # noqa: BLE001 - surfaced in the console
            self.events.put(('log', f'\u2717 track probe failed \u2014 {exc}', 'err'))
            return
        put = self.events.put
        put(('log', f'tracks \u00b7 {found["title"]}', 'ok'))
        audio = found['audio']
        if audio:
            put(('log', f'   {len(audio)} audio track(s):', 'info'))
            for code, note in sorted(audio.items()):
                marker = '   <- original' if code == found['default'] else ''
                put(('log', f'      {code:9} {note}{marker}', 'dim'))
        else:
            put(('log', '   audio: single track, no language variants', 'dim'))
        if found['subs']:
            put(('log', f'   {len(found["subs"])} subtitle language(s): '
                        f'{", ".join(found["subs"])}', 'info'))
        else:
            put(('log', '   no hand-written subtitles', 'dim'))
        if found['auto']:
            put(('log', f'   {len(found["auto"])} auto-generated caption language(s)', 'dim'))
        put(('log', '   pick them in FORMAT \u25b8 AUDIO TRACK / LANGS', 'dim'))

    def open_link(self):
        for item in self.selected_items():
            webbrowser.open(item.url)
            break

    def copy_link(self):
        urls = [i.url for i in self.selected_items()]
        if urls:
            self.clipboard_clear()
            self.clipboard_append('\n'.join(urls))

    def remove_selected(self):
        for item in self.selected_items():
            if item.active:
                continue
            self.tree.delete(str(item.uid))
            self.items.pop(item.uid, None)
            self.pos.pop(item.uid, None)
            if item.uid in self.order:
                self.order.remove(item.uid)
        self.renumber()

    def clear_finished(self):
        for uid in list(self.order):
            if self.items[uid].finished:
                if self.tree.exists(str(uid)):
                    self.tree.delete(str(uid))
                self.items.pop(uid, None)
                self.pos.pop(uid, None)
                self.order.remove(uid)
        self.renumber()

    def retry_selected(self):
        for item in self.selected_items():
            self._reset(item)

    def force_selected(self):
        """Requeue ignoring the archive and overwriting whatever is on disk."""
        count = 0
        for item in self.selected_items():
            if item.active:
                continue
            item.force = True
            item.status = STATUS_QUEUED
            item.percent = 0.0
            item.speed = item.eta = item.size = item.error = ''
            item.speed_raw = 0.0
            item.bytes_done = 0
            item.filepath = ''
            self.refresh_row(item)
            count += 1
        if count:
            self.console(f'{count} item(s) forced — archive bypassed, files overwritten',
                         'ok')
            if self.running:
                self.submit([i for i in self.selected_items() if getattr(i, 'force', False)])

    def retry_failed(self):
        count = 0
        for uid in self.order:
            item = self.items[uid]
            if item.status in (STATUS_ERROR, STATUS_CANCELLED):
                self._reset(item)
                count += 1
        if count:
            self.console(f'{count} item(s) requeued', 'ok')

    def _reset(self, item: DownloadItem):
        if not item.finished:
            return
        item.force = False
        item.status = STATUS_QUEUED
        item.percent = 0.0
        item.speed = item.eta = item.size = item.error = ''
        item.speed_raw = 0.0
        item.bytes_done = 0
        item.filepath = ''
        self.refresh_row(item)

    # ---------------------------------------------------------- shutdown ---
    def on_close(self):
        if self.running and not messagebox.askyesno(
                APP_NAME, 'Transfers are still running. Quit anyway?'):
            return
        self.cancel_event.set()
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
        save_settings(self.collect_settings())
        self.destroy()


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - non-Windows or older Windows
        pass
    App().mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        try:
            messagebox.showerror(APP_NAME, traceback.format_exc())
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
