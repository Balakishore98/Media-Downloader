"""The 'what do you actually want' dialog shown after ANALYZE.

Rather than guessing settings up front and discovering afterwards that a video
had no Tamil track, this asks using the resolutions, languages and subtitles the
media genuinely offers.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from theme import C
from core import (
    AUTO_AUDIO,
    CONTAINERS,
    QUALITY_PRESETS,
    human_bytes,
    human_time,
    language_name,
    probe_formats,
)

AUDIO_ONLY_ROW = 'AUDIO ONLY'


class FormatChooser(tk.Toplevel):
    """Modal chooser. `result` is None if cancelled, else a settings dict."""

    def __init__(self, master, sample_url: str, settings: dict, fonts,
                 batch_size: int = 1):
        super().__init__(master)
        self.result: dict | None = None
        self.fonts = fonts
        self.settings = settings
        self.batch_size = batch_size
        self.info: dict | None = None

        self.title('Select format')
        self.configure(background=C['bg'])
        self.transient(master)
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self._cancel)

        self._build()
        self._centre(master)
        self.grab_set()

        # Tk is not thread-safe: the probe hands its result over a queue and the
        # main thread picks it up on a timer. Calling after() from the worker
        # raises "main thread is not in main loop".
        self._inbox: queue.Queue = queue.Queue()
        threading.Thread(target=self._probe, args=(sample_url, settings),
                         daemon=True).start()
        self.after(80, self._poll)

    # ------------------------------------------------------------------ ui --
    def _build(self):
        pad = ttk.Frame(self, style='TFrame', padding=14)
        pad.pack(fill='both', expand=True)

        self.heading = tk.Label(pad, text='reading the media…', background=C['bg'],
                                foreground=C['text_hi'], font=self.fonts.ui_bold,
                                anchor='w', wraplength=560, justify='left')
        self.heading.pack(fill='x')
        self.subheading = ttk.Label(pad, text='', style='DimBg.TLabel')
        self.subheading.pack(fill='x', pady=(2, 10))

        # ---- quality
        ttk.Label(pad, text='QUALITY', style='HeadBg.TLabel').pack(anchor='w')
        wrap = tk.Frame(pad, background=C['border'])
        wrap.pack(fill='x', pady=(3, 10))
        self.qlist = ttk.Treeview(wrap, columns=('res', 'codec', 'size', 'kind'),
                                  show='headings', height=7, selectmode='browse')
        for key, text, width, anchor in (('res', 'RESOLUTION', 150, 'w'),
                                         ('codec', 'CODEC', 90, 'w'),
                                         ('size', 'APPROX SIZE', 120, 'e'),
                                         ('kind', '', 130, 'w')):
            self.qlist.heading(key, text=text)
            self.qlist.column(key, width=width, anchor=anchor, stretch=False)
        self.qlist.pack(fill='x', padx=1, pady=1)

        # ---- audio language
        row = ttk.Frame(pad, style='TFrame')
        row.pack(fill='x')
        row.columnconfigure(1, weight=1)
        row.columnconfigure(3, weight=1)
        ttk.Label(row, text='AUDIO', style='HeadBg.TLabel').grid(row=0, column=0, sticky='w')
        self.audio_var = tk.StringVar(value=AUTO_AUDIO)
        self.audio_box = ttk.Combobox(row, textvariable=self.audio_var, state='readonly',
                                      values=[AUTO_AUDIO], font=self.fonts.mono_sm)
        self.audio_box.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(3, 10),
                            padx=(0, 10))

        ttk.Label(row, text='CONTAINER', style='HeadBg.TLabel').grid(row=0, column=2, sticky='w')
        self.container_var = tk.StringVar(value=self.settings.get('container', 'AUTO'))
        ttk.Combobox(row, textvariable=self.container_var, state='readonly',
                     values=CONTAINERS, font=self.fonts.mono_sm).grid(
            row=1, column=2, columnspan=2, sticky='ew', pady=(3, 10))

        # ---- subtitles
        subs = ttk.Frame(pad, style='TFrame')
        subs.pack(fill='x')
        subs.columnconfigure(2, weight=1)
        self.subs_var = tk.BooleanVar(value=bool(self.settings.get('subtitles')))
        ttk.Checkbutton(subs, text='Subtitles', variable=self.subs_var,
                        style='Bg.TCheckbutton').grid(row=0, column=0, sticky='w')
        ttk.Label(subs, text='LANGS', style='HeadBg.TLabel').grid(row=0, column=1,
                                                                  sticky='e', padx=(12, 6))
        self.sublang_var = tk.StringVar(value=self.settings.get('sub_langs', 'en'))
        ttk.Entry(subs, textvariable=self.sublang_var, font=self.fonts.mono_sm).grid(
            row=0, column=2, sticky='ew')
        self.subs_hint = ttk.Label(pad, text='', style='DimBg.TLabel')
        self.subs_hint.pack(fill='x', pady=(4, 0))

        # ---- footer
        foot = ttk.Frame(pad, style='TFrame')
        foot.pack(fill='x', pady=(14, 0))
        self.batch_note = ttk.Label(foot, text='', style='DimBg.TLabel')
        self.batch_note.pack(side='left')
        self.ok = ttk.Button(foot, text='DOWNLOAD', style='Accent.TButton',
                             command=self._accept, state='disabled')
        self.ok.pack(side='right')
        ttk.Button(foot, text='CANCEL', command=self._cancel).pack(side='right', padx=(0, 8))

        if self.batch_size > 1:
            self.batch_note.configure(text=f'applies to all {self.batch_size} queued items')

    def _centre(self, master):
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f'+{max(0, x)}+{max(0, y)}')

    # --------------------------------------------------------------- probe --
    def _probe(self, url, settings):
        try:
            self._inbox.put(('ok', probe_formats(url, settings)))
        except Exception as exc:  # noqa: BLE001 - shown in the dialog
            self._inbox.put(('error', str(exc)))

    def _poll(self):
        try:
            kind, payload = self._inbox.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self.after(80, self._poll)
            return
        if kind == 'ok':
            self._populate(payload)
        else:
            self._probe_failed(payload)

    def _probe_failed(self, message):
        self.heading.configure(text='Could not read the media')
        self.subheading.configure(text=message[:180])
        # let the batch go ahead on the sidebar settings rather than trapping it
        self.ok.configure(state='normal', text='USE CURRENT SETTINGS')

    def _populate(self, info):
        self.info = info
        length = human_time(info.get('duration'))
        self.heading.configure(text=info['title'])
        self.subheading.configure(text=f'{length}   ·   what this media actually offers')

        wanted = self.settings.get('height_override') or 0
        chosen = None
        for row in info['heights']:
            size = human_bytes(row.get('total')) or '—'
            label = f"{row.get('width') or '?'}x{row['height']}"
            kind = 'ready-made' if row['progressive'] else 'video+audio'
            iid = str(row['height'])
            self.qlist.insert('', 'end', iid=iid,
                              values=(label, row['vcodec'] or '', size, kind))
            if wanted and row['height'] == wanted:
                chosen = iid
        self.qlist.insert('', 'end', iid=AUDIO_ONLY_ROW,
                          values=('audio only', 'm4a/mp3',
                                  human_bytes(info.get('best_audio_size')) or '—', ''))

        preset = str(self.settings.get('quality', ''))
        if preset.startswith('AUDIO'):
            chosen = AUDIO_ONLY_ROW
        if not chosen:
            children = self.qlist.get_children()
            chosen = children[0] if children else None
        if chosen:
            self.qlist.selection_set(chosen)
            self.qlist.see(chosen)

        languages = info.get('languages') or {}
        values = [AUTO_AUDIO]
        self._lang_codes = {AUTO_AUDIO: ''}
        for code in sorted(languages):
            label = f'{language_name(code)}  ·  {code}'
            if code == info.get('default_language'):
                label += '   (original)'
            values.append(label)
            self._lang_codes[label] = code
        self.audio_box.configure(values=values)
        if len(values) == 1:
            self.audio_box.configure(state='disabled')
            self.subheading.configure(
                text=f'{length}   ·   single audio track, no dubs offered')
        else:
            current = str(self.settings.get('audio_lang', ''))
            for label, code in self._lang_codes.items():
                if code and (current.endswith(code) or current == code):
                    self.audio_var.set(label)
                    break

        subs = info.get('subtitles') or []
        auto = info.get('automatic_captions') or []
        if subs:
            self.subs_hint.configure(text=f'available: {", ".join(subs[:14])}'
                                          + (' …' if len(subs) > 14 else '')
                                          + (f'   (+{len(auto)} auto-generated)' if auto else ''))
        elif auto:
            self.subs_hint.configure(text=f'{len(auto)} auto-generated caption languages only')
        else:
            self.subs_hint.configure(text='this media offers no subtitles')

        self.ok.configure(state='normal')

    # -------------------------------------------------------------- result --
    def _accept(self):
        chosen = dict(self.settings)
        selection = self.qlist.selection()
        pick = selection[0] if selection else None

        if pick == AUDIO_ONLY_ROW:
            chosen['quality'] = 'AUDIO  ·  MP3'
            chosen['height_override'] = None
        elif pick:
            chosen['height_override'] = int(pick)
            if str(chosen.get('quality', '')).startswith('AUDIO'):
                chosen['quality'] = next(iter(QUALITY_PRESETS))

        label = self.audio_var.get()
        chosen['audio_lang'] = getattr(self, '_lang_codes', {}).get(label, '') or AUTO_AUDIO
        chosen['container'] = self.container_var.get()
        chosen['subtitles'] = bool(self.subs_var.get())
        chosen['sub_langs'] = self.sublang_var.get().strip() or 'en'

        self.result = chosen
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()
