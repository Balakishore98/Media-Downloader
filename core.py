"""MediaForge download engine.

UI-agnostic layer over the extraction core.  It knows how to
  * probe a URL and expand playlists / profiles / channels into single media,
  * translate the app's settings into extractor options,
  * run a download while reporting progress and honouring cancellation.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass, field

APP_NAME = 'MediaForge'
APP_TAGLINE = 'MEDIA ACQUISITION SUITE'
APP_VERSION = '1.0.0'

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)


def _load_engine():
    """Import the extraction engine, from the installed package or a checkout."""
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        pass

    candidates = [
        os.environ.get('MEDIAFORGE_ENGINE_PATH', ''),
        os.path.join(_HERE, 'vendor'),
        os.path.join(_PARENT, 'yt-dlp'),
        r'D:\Softwares\yt-dlp',
        r'C:\Softwares\yt-dlp',
    ]
    for base in candidates:
        if base and os.path.isdir(os.path.join(base, 'yt_dlp')):
            sys.path.insert(0, base)
            import yt_dlp
            return yt_dlp

    raise ImportError(
        'The extraction engine is missing.\n\n'
        'Install it with:  python -m pip install yt-dlp\n'
        'or point MEDIAFORGE_ENGINE_PATH at a source checkout.')


yt_dlp = _load_engine()
from yt_dlp.utils import DownloadCancelled, DownloadError, sanitize_filename  # noqa: E402

ENGINE_VERSION = yt_dlp.version.__version__

# --------------------------------------------------------------------------- #
# Platforms
# --------------------------------------------------------------------------- #
# tag, url pattern, accent colour, usually-needs-login
PLATFORMS: list[tuple[str, str, str, bool]] = [
    ('YOUTUBE', r'(?:youtube\.com|youtu\.be)', '#ff4d4f', False),
    ('INSTAGRAM', r'instagram\.com', '#e1306c', True),
    ('TIKTOK', r'tiktok\.com', '#22d3ee', False),
    ('FACEBOOK', r'(?:facebook\.com|fb\.watch)', '#3b82f6', True),
    ('X', r'(?:twitter\.com|x\.com)', '#c9d4e0', False),
    ('REDDIT', r'reddit\.com', '#fb923c', False),
    ('TWITCH', r'twitch\.tv', '#a78bfa', False),
    ('VIMEO', r'vimeo\.com', '#38bdf8', False),
    ('DAILYMOTION', r'dailymotion\.com', '#60a5fa', False),
    ('SOUNDCLOUD', r'soundcloud\.com', '#f97316', False),
    ('PINTEREST', r'pinterest\.', '#ef4444', False),
    ('SNAPCHAT', r'snapchat\.com', '#facc15', False),
    ('LINKEDIN', r'linkedin\.com', '#38bdf8', True),
    ('BILIBILI', r'bilibili\.com', '#22d3ee', False),
]

DEFAULT_PLATFORM = ('WEB', '#7d8b9a', False)


def platform_of(url: str) -> tuple[str, str, bool]:
    """(tag, colour, needs_login) for a URL."""
    for tag, pattern, colour, login in PLATFORMS:
        if re.search(pattern, url, re.I):
            return tag, colour, login
    return DEFAULT_PLATFORM


def platform_colour(tag: str) -> str:
    for name, _pattern, colour, _login in PLATFORMS:
        if name == tag:
            return colour
    return DEFAULT_PLATFORM[1]


# --------------------------------------------------------------------------- #
# Quality presets: label -> (format selector, kind)
# --------------------------------------------------------------------------- #
# label -> (height cap | 'worst' | None, kind)
QUALITY_PRESETS: dict[str, tuple[object, str]] = {
    'MAX  ·  best video + audio': (None, 'video'),
    '2160p  ·  4K UHD': (2160, 'video'),
    '1440p  ·  2K QHD': (1440, 'video'),
    '1080p  ·  Full HD': (1080, 'video'),
    '720p  ·  HD': (720, 'video'),
    '480p  ·  SD': (480, 'video'),
    'MIN  ·  smallest file': ('worst', 'video'),
    'AUDIO  ·  MP3': (None, 'mp3'),
    'AUDIO  ·  M4A': (None, 'm4a'),
    'AUDIO  ·  source stream': (None, 'audio'),
}

# Dubbed-audio track selection.  label -> language code ('' = leave it to the site)
AUTO_AUDIO = 'AUTO  ·  original track'
AUDIO_LANGUAGES: dict[str, str] = {
    AUTO_AUDIO: '',
    'English  ·  en': 'en',
    'Hindi  ·  hi': 'hi',
    'Tamil  ·  ta': 'ta',
    'Telugu  ·  te': 'te',
    'Malayalam  ·  ml': 'ml',
    'Kannada  ·  kn': 'kn',
    'Bengali  ·  bn': 'bn',
    'Marathi  ·  mr': 'mr',
    'Punjabi  ·  pa': 'pa',
    'Urdu  ·  ur': 'ur',
    'Spanish  ·  es': 'es',
    'Portuguese  ·  pt': 'pt',
    'French  ·  fr': 'fr',
    'German  ·  de': 'de',
    'Italian  ·  it': 'it',
    'Polish  ·  pl': 'pl',
    'Russian  ·  ru': 'ru',
    'Ukrainian  ·  uk': 'uk',
    'Turkish  ·  tr': 'tr',
    'Arabic  ·  ar': 'ar',
    'Hebrew  ·  he': 'he',
    'Japanese  ·  ja': 'ja',
    'Korean  ·  ko': 'ko',
    'Chinese Simplified  ·  zh-Hans': 'zh-Hans',
    'Chinese Traditional  ·  zh-Hant': 'zh-Hant',
    'Indonesian  ·  id': 'id',
    'Thai  ·  th': 'th',
    'Vietnamese  ·  vi': 'vi',
    'Filipino  ·  fil': 'fil',
    'Dutch  ·  nl': 'nl',
    'Swedish  ·  sv': 'sv',
    'Romanian  ·  ro': 'ro',
    'Greek  ·  el': 'el',
    'Persian  ·  fa': 'fa',
    'Swahili  ·  sw': 'sw',
}


def audio_code(label: str) -> str:
    """Language code for a picker label (accepts a bare code too)."""
    if not label:
        return ''
    if label in AUDIO_LANGUAGES:
        return AUDIO_LANGUAGES[label]
    return label.strip()


ALL_TRACKS = 'all'


def parse_langs(text) -> list[str]:
    """'ta, en' -> ['ta', 'en'].  Accepts a list too."""
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        parts = list(text)
    else:
        parts = str(text).replace(';', ',').split(',')
    return [x.strip() for x in parts if x.strip()]


def build_format(preset: str, audio_lang: str = '', extra_langs=()) -> str:
    """Compose a format selector, preferring dubbed audio track(s) when asked.

    *extra_langs* adds further audio streams alongside the main one, which
    needs `allow_multiple_audio_streams`.  Every language-specific branch is
    followed by a plain fallback, so a video carrying a single audio track
    still downloads.
    """
    spec, kind = QUALITY_PRESETS.get(preset, next(iter(QUALITY_PRESETS.values())))
    main = audio_code(audio_lang)
    # ^= matches 'zh' against 'zh-Hans' as well as an exact code
    extras = [c for c in parse_langs(extra_langs) if c and c != main]

    def alt(code):
        return f'ba[language^={code}]' if code else 'ba'

    if kind in ('mp3', 'm4a', 'audio'):
        # a single audio file cannot carry several tracks
        ext = '[ext=m4a]' if kind == 'm4a' else ''
        pref = f'[language^={main}]' if main else ''
        chain = ([f'ba{pref}{ext}', f'ba{pref}'] if pref else []) + [f'ba{ext}', 'ba', 'b']
        return '/'.join(dict.fromkeys(c for c in chain if c))

    audio = '+'.join([alt(main)] + [alt(c) for c in extras]) if (main or extras) else ''

    if spec == 'worst':
        chain = ([f'wv*+{audio}'] if audio else []) + ['wv*+wa', 'w']
        return '/'.join(dict.fromkeys(chain))

    cap = f'[height<={spec}]' if isinstance(spec, int) else ''
    chain = []
    if audio:
        chain.append(f'bv*{cap}+{audio}')
        if extras:
            # every extra language present is a bonus, not a requirement
            chain.append(f'bv*{cap}+{alt(main)}')
    chain += [f'bv*{cap}+ba', f'b{cap}']
    if cap:
        chain += ['bv*+ba', 'b']
    return '/'.join(dict.fromkeys(chain))


CONTAINERS = ['AUTO', 'mp4', 'mkv', 'webm']
AUDIO_BITRATES = ['320', '256', '192', '128', '96']
BROWSERS = ['None', 'chrome', 'edge', 'firefox', 'brave', 'opera', 'vivaldi', 'chromium']

STATUS_QUEUED = 'QUEUED'
STATUS_DOWNLOADING = 'FETCHING'
STATUS_PROCESSING = 'ENCODING'
STATUS_DONE = 'COMPLETE'
STATUS_ERROR = 'FAILED'
STATUS_CANCELLED = 'ABORTED'
STATUS_SKIPPED = 'ON DISK'

FINISHED_STATES = {STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED, STATUS_SKIPPED}
ARCHIVE_PREFIX = '.mediaforge-archive'


def archive_name(settings: dict) -> str:
    """Archive file for this output profile.

    The archive records media ids, not qualities, so a single shared file would
    make "already downloaded" mean "downloaded at *some* quality" - fetching the
    same video again at a different resolution or in another language would be
    skipped.  Keying the file by profile keeps playlist resume working while
    letting a different profile fetch the same media again.
    """
    preset = str(settings.get('quality') or 'default')
    head, _, tail = preset.partition('\u00b7')
    slug = head.strip() or 'default'
    if slug.upper() == 'AUDIO':
        slug = f'AUDIO-{tail.strip()}'

    parts = [slug]
    lang = audio_code(settings.get('audio_lang', ''))
    if lang:
        parts.append(lang)
    if parse_langs(settings.get('audio_extra', '')):
        parts.append('multi')

    safe = re.sub(r'[^A-Za-z0-9._-]+', '-', '-'.join(parts)).strip('-')
    return f'{ARCHIVE_PREFIX}-{safe}.txt'.lower()


def ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


# --------------------------------------------------------------------------- #
# Queue items
# --------------------------------------------------------------------------- #
_id_lock = threading.Lock()
_id_counter = [0]


def _next_id() -> int:
    with _id_lock:
        _id_counter[0] += 1
        return _id_counter[0]


@dataclass
class DownloadItem:
    """One media file in the queue.  A playlist expands into many of these."""

    url: str
    title: str = ''
    video_id: str = ''
    uploader: str = ''
    duration: float | None = None
    playlist_title: str = ''
    playlist_index: int | None = None
    playlist_count: int | None = None
    playlist_url: str = ''

    uid: int = field(default_factory=_next_id)
    status: str = STATUS_QUEUED
    percent: float = 0.0
    speed: str = ''
    speed_raw: float = 0.0
    eta: str = ''
    size: str = ''
    bytes_done: int = 0
    filepath: str = ''
    audio_lang: str = ''
    force: bool = False
    error: str = ''

    @property
    def platform(self) -> str:
        return platform_of(self.url)[0]

    @property
    def source(self) -> str:
        if self.playlist_title:
            if self.playlist_index and self.playlist_count:
                return f'{self.playlist_title}  [{self.playlist_index}/{self.playlist_count}]'
            return self.playlist_title
        return self.uploader or '—'

    @property
    def display_title(self) -> str:
        return self.title or self.url

    @property
    def finished(self) -> bool:
        return self.status in FINISHED_STATES

    @property
    def active(self) -> bool:
        return self.status in (STATUS_DOWNLOADING, STATUS_PROCESSING)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def human_bytes(n) -> str:
    if not n:
        return ''
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{int(n)} B' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return ''


def human_speed(n) -> str:
    s = human_bytes(n)
    return f'{s}/s' if s else ''


def human_time(seconds) -> str:
    if seconds in (None, ''):
        return ''
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ''
    if seconds < 0:
        return ''
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


# --------------------------------------------------------------------------- #
# Logging bridge
# --------------------------------------------------------------------------- #
class _Logger:
    """Routes engine messages into the app console."""

    def __init__(self, log):
        self._log = log

    def debug(self, msg):
        if not msg.startswith('[debug] ') and msg.strip():
            self._log(msg)

    def info(self, msg):
        if msg.strip():
            self._log(msg)

    def warning(self, msg):
        self._log(f'WARNING: {msg}')

    def error(self, msg):
        self._log(f'ERROR: {msg}')


# --------------------------------------------------------------------------- #
# Probing / playlist expansion
# --------------------------------------------------------------------------- #
def _base_opts(settings: dict, log=None) -> dict:
    opts: dict = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'no_color': True,
        'consoletitle': False,
    }
    if log is not None:
        opts['logger'] = _Logger(log)
    browser = settings.get('cookies_browser') or 'None'
    if browser != 'None':
        opts['cookiesfrombrowser'] = (browser, None, None, None)
    if settings.get('cookie_file'):
        opts['cookiefile'] = settings['cookie_file']
    if settings.get('proxy'):
        opts['proxy'] = settings['proxy']
    return opts


def expand_url(url: str, settings: dict, log=None) -> tuple[list[DownloadItem], str]:
    """Resolve *url* into concrete media items.

    A playlist, channel, Instagram profile or mix URL is flattened into one
    DownloadItem per entry; a single post/video yields one item.
    """
    opts = _base_opts(settings, log)
    opts.update({
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'ignoreerrors': True,
        'playlistreverse': bool(settings.get('playlist_reverse')),
    })
    if settings.get('playlist_items'):
        opts['playlist_items'] = settings['playlist_items']
    if settings.get('no_playlist'):
        opts['noplaylist'] = True

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        raise DownloadError(f'Nothing could be extracted from {url}')

    items: list[DownloadItem] = []
    _collect(info, items, url, depth=0)
    if not items:
        raise DownloadError(f'No downloadable media found at {url}')
    return items, (info.get('title') or url)


def _entry_url(entry: dict) -> str:
    url = entry.get('webpage_url') or entry.get('url') or entry.get('original_url')
    if url:
        return url
    return f'https://www.youtube.com/watch?v={entry["id"]}' if entry.get('id') else ''


def _collect(info, items: list[DownloadItem], origin: str, depth: int,
             playlist_title: str = '', playlist_url: str = '') -> None:
    if not info or depth > 3:
        return

    if info.get('_type') in ('playlist', 'multi_video'):
        title = info.get('title') or playlist_title or 'Collection'
        purl = info.get('webpage_url') or playlist_url or origin
        entries = [e for e in list(info.get('entries') or []) if e]
        videos = [e for e in entries if e.get('_type') not in ('playlist', 'multi_video')]
        # Flat entries carry no playlist_index; `requested_entries` holds the real
        # positions when only a slice of the playlist was asked for.
        requested = info.get('requested_entries') or []
        total = info.get('playlist_count') or len(videos)
        seen = 0
        for entry in entries:
            if entry.get('_type') in ('playlist', 'multi_video'):
                _collect(entry, items, origin, depth + 1, title, purl)
                continue
            eurl = _entry_url(entry)
            if not eurl:
                continue
            position = (entry.get('playlist_index')
                        or (requested[seen] if seen < len(requested) else seen + 1))
            seen += 1
            items.append(DownloadItem(
                url=eurl,
                title=entry.get('title') or '',
                video_id=entry.get('id') or '',
                uploader=entry.get('uploader') or entry.get('channel') or '',
                duration=entry.get('duration'),
                playlist_title=title,
                playlist_index=position,
                playlist_count=total,
                playlist_url=purl,
            ))
        return

    items.append(DownloadItem(
        url=info.get('webpage_url') or origin,
        title=info.get('title') or '',
        video_id=info.get('id') or '',
        uploader=info.get('uploader') or info.get('channel') or '',
        duration=info.get('duration'),
        playlist_title=playlist_title,
        playlist_url=playlist_url,
    ))


def probe_tracks(url: str, settings: dict, log=None) -> dict:
    """Audio tracks and subtitle languages a single media offers."""
    opts = _base_opts(settings, log)
    opts.update({'skip_download': True, 'noplaylist': True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise DownloadError(f'Nothing could be extracted from {url}')

    audio: dict[str, str] = {}
    for fmt in info.get('formats') or []:
        if fmt.get('acodec') in (None, 'none'):
            continue
        code = fmt.get('language')
        if code and code not in audio:
            audio[code] = (fmt.get('format_note') or '').split(',')[0]
    return {
        'title': info.get('title') or url,
        'default': info.get('language') or '',
        'audio': audio,
        'subs': sorted(info.get('subtitles') or {}),
        'auto': sorted(info.get('automatic_captions') or {}),
    }


# --------------------------------------------------------------------------- #
# Building engine options for one item
# --------------------------------------------------------------------------- #
def _escape_tmpl(text: str) -> str:
    """Make a literal string safe to embed in an output template."""
    return text.replace('%', '%%')


def build_outtmpl(item: DownloadItem, settings: dict) -> str:
    outdir = settings.get('outdir') or os.getcwd()
    parts = [outdir]

    if settings.get('platform_folder'):
        parts.append(item.platform)

    if item.playlist_title and settings.get('playlist_folder', True):
        folder = sanitize_filename(item.playlist_title, restricted=False) or 'Collection'
        parts.append(_escape_tmpl(folder))

    name = '%(title).150B'
    if item.playlist_title and settings.get('number_playlist_items', True) and item.playlist_index:
        name = f'{item.playlist_index:03d} - {name}'

    # Without this, the same video fetched at two qualities lands on one
    # filename: the second download finds the first file already there and is
    # reported as finished without anything being fetched.
    if settings.get('quality_in_name', True):
        _spec, kind = QUALITY_PRESETS.get(settings.get('quality', ''), (None, 'video'))
        if kind == 'video':
            # height of what was actually taken, not the cap that was asked for
            name += ' [%(height&{}p|na)s]'
        elif kind in ('mp3', 'm4a'):
            name += f' [{settings.get("audio_bitrate", "192")}k]'

    if settings.get('include_id', False):
        name += ' [%(id)s]'
    parts.append(name + '.%(ext)s')
    return os.path.join(*parts)


def build_opts(item: DownloadItem, settings: dict, hooks: dict) -> dict:
    preset = settings.get('quality') or next(iter(QUALITY_PRESETS))
    _spec, kind = QUALITY_PRESETS.get(preset, next(iter(QUALITY_PRESETS.values())))
    extras = parse_langs(settings.get('audio_extra', '')) if kind == 'video' else []
    selector = build_format(preset, settings.get('audio_lang', ''), extras)
    has_ffmpeg = ffmpeg_available()

    opts = _base_opts(settings, hooks.get('log'))
    opts.update({
        'format': selector,
        'outtmpl': {'default': build_outtmpl(item, settings)},
        'windowsfilenames': os.name == 'nt',
        'noplaylist': True,          # items are pre-expanded, one video each
        'ignoreerrors': False,
        'retries': int(settings.get('retries', 10)),
        'fragment_retries': int(settings.get('retries', 10)),
        'concurrent_fragment_downloads': int(settings.get('fragments', 4)),
        'overwrites': bool(settings.get('overwrite', False)),
        'continuedl': True,
        'trim_file_name': 200,
    })

    if extras:
        # several audio streams muxed into one file
        opts['allow_multiple_audio_streams'] = True

    if hooks.get('progress'):
        opts['progress_hooks'] = [hooks['progress']]
    if hooks.get('postprocessor'):
        opts['postprocessor_hooks'] = [hooks['postprocessor']]
    if hooks.get('post'):
        opts['post_hooks'] = [hooks['post']]

    if settings.get('limit_rate'):
        rate = parse_rate(settings['limit_rate'])
        if rate:
            opts['ratelimit'] = rate

    if settings.get('use_archive') and settings.get('outdir'):
        opts['download_archive'] = os.path.join(settings['outdir'], archive_name(settings))

    postprocessors: list[dict] = []

    if kind in ('mp3', 'm4a'):
        if has_ffmpeg:
            postprocessors.append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': kind,
                'preferredquality': str(settings.get('audio_bitrate', '192')),
            })
    elif kind == 'video':
        container = settings.get('container', 'AUTO')
        if has_ffmpeg and container != 'AUTO':
            opts['merge_output_format'] = container
            postprocessors.append({'key': 'FFmpegVideoRemuxer', 'preferedformat': container})
        elif has_ffmpeg:
            # mkv tags per-track languages far more reliably than mp4
            opts['merge_output_format'] = 'mkv' if extras else 'mp4/mkv'

    if settings.get('subtitles'):
        opts['writesubtitles'] = True
        opts['writeautomaticsub'] = bool(settings.get('auto_subs', True))
        langs = (settings.get('sub_langs') or 'en').replace(' ', '')
        opts['subtitleslangs'] = [x for x in langs.split(',') if x] or ['en']
        opts['subtitlesformat'] = 'srt/vtt/best'
        if has_ffmpeg and settings.get('embed_subs', True) and kind == 'video':
            postprocessors.append({'key': 'FFmpegEmbedSubtitle', 'already_have_subtitle': False})

    if settings.get('thumbnail'):
        opts['writethumbnail'] = True
        if has_ffmpeg:
            postprocessors.append({'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg',
                                   'when': 'before_dl'})
            postprocessors.append({'key': 'EmbedThumbnail', 'already_have_thumbnail': False})

    if settings.get('metadata') and has_ffmpeg:
        postprocessors.append({'key': 'FFmpegMetadata', 'add_metadata': True,
                               'add_chapters': True})

    if settings.get('sponsorblock') and has_ffmpeg and kind == 'video':
        postprocessors.append({'key': 'SponsorBlock', 'categories': {'sponsor'},
                               'when': 'after_filter'})
        postprocessors.append({
            'key': 'ModifyChapters',
            'remove_sponsor_segments': {'sponsor'},
            'sponsorblock_chapter_title': '[SponsorBlock]: %(category_names)l',
        })

    if postprocessors:
        opts['postprocessors'] = postprocessors
    return opts


def parse_rate(text: str):
    """'1.5M' / '500K' / '2000' -> bytes per second."""
    text = str(text).strip().upper().rstrip('B')
    if not text:
        return None
    mult = 1
    if text.endswith('K'):
        mult, text = 1024, text[:-1]
    elif text.endswith('M'):
        mult, text = 1024 * 1024, text[:-1]
    elif text.endswith('G'):
        mult, text = 1024 * 1024 * 1024, text[:-1]
    try:
        return int(float(text) * mult)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Running one download
# --------------------------------------------------------------------------- #
class Cancelled(Exception):
    pass


def download_item(item: DownloadItem, settings: dict, on_progress, on_log,
                  cancel_event: threading.Event) -> None:
    """Download a single item.  Raises Cancelled if the user stopped it."""

    def progress_hook(d):
        if cancel_event.is_set():
            raise DownloadCancelled('Stopped by user')
        status = d.get('status')
        if status == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            done = d.get('downloaded_bytes') or 0
            item.status = STATUS_DOWNLOADING
            item.percent = (done / total * 100) if total else 0.0
            item.speed_raw = d.get('speed') or 0.0
            item.speed = human_speed(d.get('speed'))
            item.eta = human_time(d.get('eta'))
            item.size = human_bytes(total)
            item.bytes_done = done
            on_progress(item)
        elif status == 'finished':
            item.percent = 100.0
            item.speed = ''
            item.speed_raw = 0.0
            item.eta = ''
            item.bytes_done = d.get('downloaded_bytes') or item.bytes_done
            item.status = STATUS_PROCESSING
            on_progress(item)
        elif status == 'error':
            item.status = STATUS_ERROR
            item.speed_raw = 0.0
            on_progress(item)

    def pp_hook(d):
        if cancel_event.is_set():
            raise DownloadCancelled('Stopped by user')
        if d.get('status') == 'started':
            item.status = STATUS_PROCESSING
            item.speed = (d.get('postprocessor') or '').replace('FFmpeg', '')
            on_progress(item)

    def post_hook(filename):
        item.filepath = filename

    hooks = {'progress': progress_hook, 'postprocessor': pp_hook, 'post': post_hook,
             'log': on_log}

    if ALL_TRACKS in [x.lower() for x in parse_langs(settings.get('audio_extra', ''))]:
        # 'all' needs to know what this particular media actually offers
        try:
            available = sorted(probe_tracks(item.url, settings)['audio'])
        except Exception:  # noqa: BLE001 - fall back to a single track
            available = []
        if available:
            on_log(f'{item.display_title}: embedding {len(available)} audio track(s) '
                   f'({", ".join(available)})')
        settings = dict(settings, audio_extra=available)

    opts = build_opts(item, settings, hooks)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(item.url, download=True)
        if info:
            item.title = info.get('title') or item.title
            item.video_id = info.get('id') or item.video_id
            item.uploader = info.get('uploader') or info.get('channel') or item.uploader
            item.duration = info.get('duration') or item.duration
            if not item.filepath:
                downloads = info.get('requested_downloads') or []
                item.filepath = info.get('filepath') or (
                    downloads[0].get('filepath', '') if downloads else '')
            picked = [f.get('language') for f in (info.get('requested_formats') or [info])
                      if f.get('language')]
            item.audio_lang = '+'.join(dict.fromkeys(picked)) or (info.get('language') or '')
    except DownloadCancelled:
        item.status = STATUS_CANCELLED
        item.speed_raw = 0.0
        raise Cancelled from None

    if cancel_event.is_set():
        item.status = STATUS_CANCELLED
        item.speed_raw = 0.0
        raise Cancelled

    # Nothing fetched and no file produced -> the archive skipped it
    item.status = STATUS_SKIPPED if (item.percent == 0 and not item.filepath) else STATUS_DONE
    if item.status == STATUS_DONE:
        item.percent = 100.0
    item.speed = ''
    item.speed_raw = 0.0
    item.eta = ''
