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

# Codec families each container can actually carry.  Picking a container has to
# steer format *selection*, not just the merge target: YouTube's best audio is
# Opus, which mp4 cannot hold, so asking for mp4 without steering the choice
# ends with the merge silently falling back to mkv.
CONTAINER_PREFS: dict[str, tuple[str, str]] = {
    'mp4': ('[ext=mp4]', '[ext=m4a]'),
    'webm': ('[ext=webm]', '[ext=webm]'),
    'mkv': ('', ''),
    'AUTO': ('', ''),
}


def parse_langs(text) -> list[str]:
    """'ta, en' -> ['ta', 'en'].  Accepts a list too."""
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        parts = list(text)
    else:
        parts = str(text).replace(';', ',').split(',')
    return [x.strip() for x in parts if x.strip()]


def _audio_expr(main: str, extras, pref: str) -> str:
    def one(code):
        return f'ba{f"[language^={code}]" if code else ""}{pref}'
    return '+'.join([one(main)] + [one(c) for c in extras])


def build_format(preset: str, audio_lang: str = '', extra_langs=(),
                 container: str = 'AUTO', has_ffmpeg: bool = True,
                 height_override=None) -> str:
    """Compose a format selector.

    Preference order: honour container + language, then drop the container
    constraint, then the extra languages, then the language, and only then the
    height cap - so the most specific request that the media can satisfy wins.

    Without ffmpeg nothing can be merged, so only streams that already carry
    audio are eligible.  That caps YouTube at 720p, but it downloads instead of
    failing with "you have requested merging of multiple formats".
    """
    spec, kind = QUALITY_PRESETS.get(preset, next(iter(QUALITY_PRESETS.values())))
    # an exact height picked from what the media really offers beats the preset
    if height_override and kind == 'video':
        spec = int(height_override)
    main = audio_code(audio_lang)
    extras = [c for c in parse_langs(extra_langs) if c and c != main]
    vpref, apref = CONTAINER_PREFS.get(container, ('', ''))

    if not has_ffmpeg:
        if kind in ('mp3', 'm4a', 'audio'):
            # no converting either; take the best ready-made audio stream
            lang = f'[language^={main}]' if main else ''
            chain = ([f'ba{lang}[ext=m4a]', f'ba{lang}'] if lang else []) + \
                ['ba[ext=m4a]', 'ba', 'b']
            return '/'.join(dict.fromkeys(chain))
        cap_only = f'[height<={spec}]' if isinstance(spec, int) else ''
        chain = [f'b{cap_only}[ext=mp4]', f'b{cap_only}', 'b']
        return '/'.join(dict.fromkeys(c for c in chain if c))

    if kind in ('mp3', 'm4a', 'audio'):
        # a single audio file cannot carry several tracks
        ext = '[ext=m4a]' if kind == 'm4a' else ''
        lang = f'[language^={main}]' if main else ''
        chain = ([f'ba{lang}{ext}', f'ba{lang}'] if lang else []) + [f'ba{ext}', 'ba', 'b']
        return '/'.join(dict.fromkeys(c for c in chain if c))

    if spec == 'worst':
        chain = [f'wv*+{_audio_expr(main, extras, apref)}'] if (main or extras or apref) else []
        return '/'.join(dict.fromkeys(chain + ['wv*+wa', 'w']))

    cap = f'[height<={spec}]' if isinstance(spec, int) else ''
    chain = []
    if vpref or apref:
        chain.append(f'bv*{cap}{vpref}+{_audio_expr(main, extras, apref)}')
        if extras:
            chain.append(f'bv*{cap}{vpref}+{_audio_expr(main, [], apref)}')
        chain.append(f'bv*{cap}+{_audio_expr(main, extras, apref)}')
    if main or extras:
        chain.append(f'bv*{cap}+{_audio_expr(main, extras, "")}')
        if extras:
            chain.append(f'bv*{cap}+{_audio_expr(main, [], "")}')
    chain += [f'bv*{cap}+ba', f'b{cap}']
    if cap:
        # nothing at or below the cap - take the smallest thing above it rather
        # than the best, so asking for 1080p never lands a surprise 4K file
        chain += ['wv*+wa', 'w']
    return '/'.join(dict.fromkeys(c for c in chain if c))


def height_cap(preset: str):
    """Requested height cap for a profile, or None when uncapped."""
    spec, _kind = QUALITY_PRESETS.get(preset, (None, 'video'))
    return spec if isinstance(spec, int) else None


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
    if settings.get('height_override'):
        parts.append(f"{settings['height_override']}p")
    lang = audio_code(settings.get('audio_lang', ''))
    if lang:
        parts.append(lang)
    if parse_langs(settings.get('audio_extra', '')):
        parts.append('multi')

    safe = re.sub(r'[^A-Za-z0-9._-]+', '-', '-'.join(parts)).strip('-')
    return f'{ARCHIVE_PREFIX}-{safe}.txt'.lower()


# ffmpeg installed by the app itself lands beside the executable, which is not
# on PATH, so make sure that directory is searched too.
def app_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _HERE


def _ffmpeg_search_dirs() -> list[str]:
    """Where a bundled or self-installed ffmpeg might be.

    A one-file build unpacks its bundled binaries into sys._MEIPASS, which is a
    temp directory - not the folder the .exe sits in - so both have to be
    searched or a bundled ffmpeg is invisible to the app that ships it.
    """
    dirs = []
    meipass = getattr(sys, '_MEIPASS', '')
    if meipass:
        dirs += [meipass, os.path.join(meipass, 'ffmpeg')]
    dirs += [app_dir(), os.path.join(app_dir(), 'ffmpeg')]
    return dirs


def _register_local_ffmpeg() -> None:
    for candidate in _ffmpeg_search_dirs():
        if candidate and os.path.isfile(os.path.join(candidate, 'ffmpeg.exe')):
            current = os.environ.get('PATH', '')
            if candidate not in current.split(os.pathsep):
                os.environ['PATH'] = candidate + os.pathsep + current
            return


_register_local_ffmpeg()

FFMPEG_ZIP = ('https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/'
              'ffmpeg-master-latest-win64-gpl.zip')


def ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


def install_ffmpeg(on_log) -> bool:
    """Fetch ffmpeg for this machine. Tries winget, then a direct download."""
    import subprocess
    import zipfile

    if os.name == 'nt':
        on_log('trying winget...')
        try:
            proc = subprocess.run(
                ['winget', 'install', '--id', 'Gyan.FFmpeg', '-e', '--silent',
                 '--accept-source-agreements', '--accept-package-agreements'],
                capture_output=True, text=True, timeout=900,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if proc.returncode == 0:
                on_log('winget finished - restart the app to pick it up')
                return True
            on_log(f'winget could not do it (exit {proc.returncode}), downloading instead')
        except (OSError, subprocess.SubprocessError) as exc:
            on_log(f'winget unavailable ({exc.__class__.__name__}), downloading instead')

    target = os.path.join(app_dir(), 'ffmpeg')
    try:
        os.makedirs(target, exist_ok=True)
        archive = os.path.join(target, 'ffmpeg.zip')
        on_log('downloading ffmpeg (about 160 MB, one time)...')

        import urllib.request
        with urllib.request.urlopen(FFMPEG_ZIP, timeout=120) as response, \
                open(archive, 'wb') as fh:
            total = int(response.headers.get('Content-Length') or 0)
            done = 0
            step = max(1, total // 20) if total else 8 << 20
            nxt = step
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if done >= nxt:
                    nxt += step
                    if total:
                        on_log(f'  {done * 100 // total}%  ({done >> 20} MB)')

        on_log('extracting...')
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                name = os.path.basename(member)
                if name in ('ffmpeg.exe', 'ffprobe.exe'):
                    with zf.open(member) as src, open(os.path.join(target, name), 'wb') as dst:
                        shutil.copyfileobj(src, dst)
        os.remove(archive)

        _register_local_ffmpeg()
        if ffmpeg_available():
            on_log(f'ffmpeg ready in {target}')
            return True
        on_log('ffmpeg still not visible after install')
        return False
    except Exception as exc:  # noqa: BLE001 - reported in the console
        on_log(f'ffmpeg install failed: {exc}')
        return False


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
    height: int | None = None
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


def _extraction_failure(url: str, settings: dict, engine_errors: list) -> str:
    """A message that says what went wrong and what to do about it."""
    # An extractor the engine has disabled will not start working because you
    # signed in, so this case must be reported before the login advice.
    if any('marked as broken' in str(e) for e in engine_errors):
        tag = platform_of(url)[0]
        return (f'Support for this kind of {tag.title()} link is currently broken in the '
                f'extraction engine - the site changed and the engine has disabled it. '
                f'Signing in will not help. Individual posts and reels still work; '
                f'paste those links instead.')

    reason = ''
    if engine_errors:
        reason = engine_errors[-1]
        for prefix in ('ERROR: ', 'ERROR:'):
            while reason.startswith(prefix):
                reason = reason[len(prefix):].lstrip()
        reason = reason.split('; please report')[0].split('. See  http')[0].strip()

    tag, _colour, needs_login = platform_of(url)
    signed_in = (settings.get('cookies_browser') or 'None') != 'None' or settings.get('cookie_file')
    lowered = reason.lower()
    gated = 'log in' in lowered or 'login' in lowered or 'private' in lowered

    if (needs_login or gated) and not signed_in:
        hint = (f'{tag} needs you to be signed in for this. Set NETWORK - AUTH > '
                f'SESSION COOKIES to the browser you use for {tag.title()}, and close '
                f'that browser first.')
        return f'{reason}  |  {hint}' if reason else hint
    return reason or f'Nothing could be extracted from {url}'


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

    # ignoreerrors keeps a part-broken playlist usable, but it also swallows the
    # reason a single link failed. Keep the engine's own messages so the failure
    # can say something better than "nothing could be extracted".
    engine_errors: list[str] = []
    original_log = log

    def capture(message):
        text = str(message)
        if 'ERROR' in text or 'marked as broken' in text:
            engine_errors.append(text)
        if original_log is not None:
            original_log(text)

    opts['logger'] = _Logger(capture)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        raise DownloadError(_extraction_failure(url, settings, engine_errors))

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


LANGUAGE_NAMES = {v: k.split('\u00b7')[0].strip() for k, v in AUDIO_LANGUAGES.items() if v}


def language_name(code: str) -> str:
    """'ta' -> 'Tamil', falling back to the bare code."""
    return LANGUAGE_NAMES.get(code, code)


def probe_formats(url: str, settings: dict, log=None) -> dict:
    """What a single media actually offers.

    Returns the resolutions really on the server with their sizes, the dubbed
    audio languages that exist, and the subtitle languages - so the UI can ask
    about real choices instead of guessing and silently falling back.
    """
    opts = _base_opts(settings, log)
    opts.update({'skip_download': True, 'noplaylist': True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise DownloadError(f'Nothing could be extracted from {url}')

    formats = info.get('formats') or []

    # best audio size per language, used to estimate a merged file
    audio_langs: dict[str, str] = {}
    audio_sizes: dict[str, int] = {}
    best_audio = 0
    for fmt in formats:
        if fmt.get('acodec') in (None, 'none'):
            continue
        size = fmt.get('filesize') or fmt.get('filesize_approx') or 0
        best_audio = max(best_audio, size)
        code = fmt.get('language')
        if code:
            audio_langs.setdefault(code, (fmt.get('format_note') or '').split(',')[0])
            audio_sizes[code] = max(audio_sizes.get(code, 0), size)

    # one row per height, keeping the largest (best) variant of each
    heights: dict[int, dict] = {}
    for fmt in formats:
        if fmt.get('vcodec') in (None, 'none'):
            continue
        height = fmt.get('height')
        if not height:
            continue
        size = fmt.get('filesize') or fmt.get('filesize_approx') or 0
        progressive = fmt.get('acodec') not in (None, 'none')
        row = heights.setdefault(height, {
            'height': height, 'width': fmt.get('width'), 'size': 0,
            'ext': fmt.get('ext'), 'vcodec': '', 'note': fmt.get('format_note') or '',
            'progressive': False,
        })
        if size > row['size']:
            row.update({'size': size, 'ext': fmt.get('ext'),
                        'vcodec': (fmt.get('vcodec') or '').split('.')[0],
                        'width': fmt.get('width') or row['width'],
                        'note': fmt.get('format_note') or row['note']})
        row['progressive'] = row['progressive'] or progressive

    for row in heights.values():
        # a merged file is the video stream plus one audio stream
        row['total'] = row['size'] + (0 if row['progressive'] else best_audio)

    return {
        'title': info.get('title') or url,
        'duration': info.get('duration'),
        'default_language': info.get('language') or '',
        'heights': sorted(heights.values(), key=lambda r: r['height'], reverse=True),
        'languages': audio_langs,
        'language_sizes': audio_sizes,
        'subtitles': sorted(info.get('subtitles') or {}),
        'automatic_captions': sorted(info.get('automatic_captions') or {}),
        'best_audio_size': best_audio,
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
    if preset not in QUALITY_PRESETS and hooks.get('log'):
        hooks['log'](f'WARNING: unknown quality profile {preset!r} - falling back to '
                     f'{next(iter(QUALITY_PRESETS))}')
    _spec, kind = QUALITY_PRESETS.get(preset, next(iter(QUALITY_PRESETS.values())))
    extras = parse_langs(settings.get('audio_extra', '')) if kind == 'video' else []
    container = settings.get('container', 'AUTO')
    has_ffmpeg = ffmpeg_available()
    selector = build_format(preset, settings.get('audio_lang', ''), extras, container,
                            has_ffmpeg, settings.get('height_override'))

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
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(item.url, download=True)
        except DownloadError as err:
            # Most sites stopped serving pre-merged streams, so without ffmpeg
            # there is simply nothing downloadable. Say that plainly instead of
            # letting "requested format is not available" reach the user.
            text = str(err)
            if not ffmpeg_available() and ('not available' in text or 'merging' in text):
                raise DownloadError(
                    'ffmpeg is required for this site. It serves video and audio as '
                    'separate streams and they have to be merged. Install it with:  '
                    'winget install Gyan.FFmpeg  (then restart the app)') from None
            raise
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

            streams = info.get('requested_formats') or [info]
            heights = [f.get('height') for f in streams if f.get('height')]
            item.height = max(heights) if heights else None
            cap = settings.get('height_override') or height_cap(settings.get('quality', ''))
            if cap and item.height and item.height > cap:
                on_log(f'WARNING: {item.display_title}: nothing at or below {cap}p was '
                       f'available - took {item.height}p instead')
            want = settings.get('container', 'AUTO')
            if want != 'AUTO' and item.filepath:
                actual = os.path.splitext(item.filepath)[1].lstrip('.').lower()
                if actual and actual != want.lower():
                    on_log(f'WARNING: {item.display_title}: asked for .{want} but the '
                           f'streams could not be held by it - wrote .{actual}')
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
