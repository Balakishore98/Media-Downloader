"""MediaForge mobile engine.

Runs yt-dlp inside the app via Chaquopy.  There is no ffmpeg on Android, so
this module never asks yt-dlp to merge: it either takes a stream that already
carries audio, or fetches video and audio separately and hands both paths back
for Android's own MediaMuxer to combine.

Kotlin calls into here; progress travels back through a listener object.
"""

from __future__ import annotations

import json
import os
import traceback

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled

# MediaMuxer can write an mp4 holding H.264 video and AAC audio without
# re-encoding anything, which is why the selectors below insist on those.
VIDEO_ONLY = 'bv*[vcodec^=avc1]{cap}'
AUDIO_ONLY = 'ba[ext=m4a]{lang}/ba[acodec^=mp4a]{lang}/ba[ext=m4a]/ba'
# The last resort is the *smallest* stream, not the best: asking for 480p on
# mobile data and silently getting 4K is worse than getting something small.
PROGRESSIVE = ('b[vcodec^=avc1][acodec!=none]{cap}/b[acodec!=none]{cap}/'
               'b[acodec!=none]/b/w')

QUALITY_CAPS = {
    'MAX': None,
    '1080p': 1080,
    '720p': 720,
    '480p': 480,
    '360p': 360,
    'AUDIO': None,
}


def _cap(quality: str, height_override: int = 0) -> str:
    height = int(height_override) or QUALITY_CAPS.get(quality)
    return f'[height<={height}]' if height else ''


def _base_opts(cookies: str | None = None) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'no_color': True,
        'nocheckcertificate': False,
        # every merge path needs ffmpeg, so make sure none is ever chosen
        'prefer_free_formats': False,
        'retries': 5,
        'fragment_retries': 5,
        'concurrent_fragment_downloads': 4,
    }
    if cookies and os.path.exists(cookies):
        opts['cookiefile'] = cookies
    return opts


def _entry_url(entry: dict) -> str:
    url = entry.get('webpage_url') or entry.get('url') or entry.get('original_url')
    if url:
        return url
    return f'https://www.youtube.com/watch?v={entry["id"]}' if entry.get('id') else ''


def probe(url: str, cookies: str | None = None) -> str:
    """Resolve a link into a list of media. Playlists expand. Returns JSON."""
    try:
        opts = _base_opts(cookies)
        opts.update({'extract_flat': 'in_playlist', 'skip_download': True,
                     'ignoreerrors': True})
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return json.dumps({'ok': False, 'error': 'Nothing could be extracted'})

        items = []
        if info.get('_type') in ('playlist', 'multi_video'):
            entries = [e for e in (info.get('entries') or []) if e]
            for index, entry in enumerate(entries, start=1):
                eurl = _entry_url(entry)
                if not eurl:
                    continue
                items.append({
                    'url': eurl,
                    'title': entry.get('title') or eurl,
                    'duration': entry.get('duration') or 0,
                    'uploader': entry.get('uploader') or entry.get('channel') or '',
                    'collection': info.get('title') or '',
                    'index': index,
                    'total': len(entries),
                })
        else:
            items.append({
                'url': info.get('webpage_url') or url,
                'title': info.get('title') or url,
                'duration': info.get('duration') or 0,
                'uploader': info.get('uploader') or info.get('channel') or '',
                'collection': '',
                'index': 1,
                'total': 1,
            })
        return json.dumps({'ok': True, 'title': info.get('title') or url, 'items': items})
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        return json.dumps({'ok': False, 'error': str(exc)})


def audio_languages(url: str, cookies: str | None = None) -> str:
    """Dubbed audio tracks a single media offers. Returns JSON."""
    try:
        opts = _base_opts(cookies)
        opts.update({'skip_download': True, 'noplaylist': True})
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        langs = {}
        for fmt in (info.get('formats') or []):
            if fmt.get('acodec') in (None, 'none'):
                continue
            code = fmt.get('language')
            if code and code not in langs:
                langs[code] = (fmt.get('format_note') or '').split(',')[0]
        return json.dumps({'ok': True, 'default': info.get('language') or '',
                           'languages': langs})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({'ok': False, 'error': str(exc)})


LANGUAGE_NAMES = {
    'en': 'English', 'hi': 'Hindi', 'ta': 'Tamil', 'te': 'Telugu', 'ml': 'Malayalam',
    'kn': 'Kannada', 'bn': 'Bengali', 'mr': 'Marathi', 'pa': 'Punjabi', 'ur': 'Urdu',
    'es': 'Spanish', 'pt': 'Portuguese', 'fr': 'French', 'de': 'German', 'it': 'Italian',
    'pl': 'Polish', 'ru': 'Russian', 'uk': 'Ukrainian', 'tr': 'Turkish', 'ar': 'Arabic',
    'he': 'Hebrew', 'ja': 'Japanese', 'ko': 'Korean', 'id': 'Indonesian', 'th': 'Thai',
    'vi': 'Vietnamese', 'fil': 'Filipino', 'nl': 'Dutch', 'sv': 'Swedish', 'ro': 'Romanian',
    'el': 'Greek', 'fa': 'Persian', 'sw': 'Swahili',
    'zh-Hans': 'Chinese (Simplified)', 'zh-Hant': 'Chinese (Traditional)',
}


def probe_formats(url: str, cookies: str | None = None) -> str:
    """What a single media really offers, for the chooser. Returns JSON.

    Only heights this app can actually deliver are listed: either a ready-made
    stream, or an H.264 video track that MediaMuxer can pair with AAC audio.
    Offering 4K that would fail at mux time helps nobody.
    """
    try:
        opts = _base_opts(cookies)
        opts.update({'skip_download': True, 'noplaylist': True})
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return json.dumps({'ok': False, 'error': 'Nothing could be extracted'})

        formats = info.get('formats') or []

        languages, best_audio = {}, 0
        for fmt in formats:
            if fmt.get('acodec') in (None, 'none'):
                continue
            best_audio = max(best_audio, fmt.get('filesize') or fmt.get('filesize_approx') or 0)
            code = fmt.get('language')
            if code and code not in languages:
                languages[code] = LANGUAGE_NAMES.get(code, code)

        heights = {}
        for fmt in formats:
            if fmt.get('vcodec') == 'none':
                continue        # audio-only
            # 'none' means the stream really lacks it; None only means the
            # extractor could not tell. Instagram's ready-made mp4s report
            # unknown codecs and are perfectly downloadable, so treating
            # unknown as unusable hid the only video this app could fetch.
            progressive = fmt.get('acodec') != 'none'
            muxable = str(fmt.get('vcodec') or '').startswith('avc1')
            if not (progressive or muxable):
                continue        # nothing this app could finish
            size = fmt.get('filesize') or fmt.get('filesize_approx') or 0
            height = fmt.get('height') or 0
            row = heights.setdefault(height, {
                'height': height, 'width': fmt.get('width'),
                'size': 0, 'progressive': False,
            })
            if size > row['size']:
                row['size'] = size
                row['width'] = fmt.get('width') or row['width']
            row['progressive'] = row['progressive'] or progressive

        rows = []
        for row in sorted(heights.values(), key=lambda r: r['height'], reverse=True):
            row['total'] = row['size'] + (0 if row['progressive'] else best_audio)
            rows.append(row)

        return json.dumps({
            'ok': True,
            'title': info.get('title') or url,
            'duration': info.get('duration') or 0,
            'default_language': info.get('language') or '',
            'heights': rows,
            'languages': languages,
            'best_audio_size': best_audio,
        })
    except Exception as exc:  # noqa: BLE001 - shown in the app
        return json.dumps({'ok': False, 'error': str(exc)})


class _Cancelled(Exception):
    pass


def _run(url, selector, outtmpl, cookies, listener, stage):
    """Download one stream, reporting progress. Returns the finished path."""
    produced = {}

    def hook(d):
        if listener is not None and listener.isCancelled():
            raise DownloadCancelled('stopped')
        if d.get('status') == 'downloading' and listener is not None:
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            listener.onProgress(json.dumps({
                'stage': stage,
                'percent': (d.get('downloaded_bytes', 0) / total * 100) if total else 0.0,
                'speed': d.get('speed') or 0,
                'eta': d.get('eta') or 0,
                'bytes': d.get('downloaded_bytes', 0),
                'total': total,
            }))

    opts = _base_opts(cookies)
    opts.update({'format': selector, 'outtmpl': {'default': outtmpl},
                 'progress_hooks': [hook], 'noplaylist': True,
                 'post_hooks': [lambda fn: produced.setdefault('path', fn)],
                 'overwrites': True})
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    path = produced.get('path') or (info or {}).get('filepath', '')
    if not path:
        downloads = (info or {}).get('requested_downloads') or []
        path = downloads[0].get('filepath', '') if downloads else ''
    return path, info or {}


def download(url: str, outdir: str, quality: str, audio_lang: str,
             cookies: str | None, listener, height_override: int = 0) -> str:
    """Fetch one media.

    Returns JSON describing what landed on disk.  When two files come back the
    caller is expected to mux them; `needs_mux` says so explicitly.
    """
    try:
        os.makedirs(outdir, exist_ok=True)
        lang = f'[language^={audio_lang}]' if audio_lang else ''
        cap = _cap(quality, height_override)
        stem = os.path.join(outdir, '%(title).120B')

        if quality == 'AUDIO':
            path, info = _run(url, AUDIO_ONLY.format(lang=lang), stem + '.%(ext)s',
                              cookies, listener, 'audio')
            return json.dumps({'ok': True, 'needs_mux': False, 'path': path,
                               'title': info.get('title', ''),
                               'height': 0, 'audio_lang': audio_lang})

        # Prefer a stream that already carries audio - nothing to mux.
        opts = _base_opts(cookies)
        opts.update({'skip_download': True, 'noplaylist': True})
        with YoutubeDL(opts) as ydl:
            probe_info = ydl.extract_info(url, download=False)

        formats = probe_info.get('formats') or []
        cap_height = int(height_override) or QUALITY_CAPS.get(quality)

        def usable_progressive(f):
            if f.get('acodec') == 'none' or f.get('vcodec') == 'none':
                return False
            if cap_height and (f.get('height') or 0) > cap_height:
                return False
            codec = str(f.get('vcodec') or '')
            # unknown codec = a ready-made file the extractor could not label
            return codec.startswith('avc1') or codec == ''

        progressive = [f for f in formats if usable_progressive(f)]
        best_prog = max(progressive, key=lambda f: f.get('height') or 0, default=None)

        split_candidates = [
            f for f in formats
            if f.get('acodec') in (None, 'none')
            and str(f.get('vcodec', '')).startswith('avc1')
            and (not cap_height or (f.get('height') or 0) <= cap_height)
        ]
        best_split = max(split_candidates, key=lambda f: f.get('height') or 0, default=None)

        # Split streams are worth the mux only when they actually beat the
        # ready-made one; on most sites the progressive file is all there is.
        if best_split and (not best_prog or
                           (best_split.get('height') or 0) > (best_prog.get('height') or 0)):
            video_path, info = _run(url, VIDEO_ONLY.format(cap=cap), stem + '.video.%(ext)s',
                                    cookies, listener, 'video')
            audio_path, _ = _run(url, AUDIO_ONLY.format(lang=lang), stem + '.audio.%(ext)s',
                                 cookies, listener, 'audio')
            return json.dumps({
                'ok': True, 'needs_mux': True,
                'video': video_path, 'audio': audio_path,
                'out': os.path.join(outdir, _safe(info.get('title', 'media')) +
                                    f'_{best_split.get("height") or 0}p.mp4'),
                'title': info.get('title', ''),
                'height': best_split.get('height') or 0,
                'audio_lang': audio_lang,
            })

        path, info = _run(url, PROGRESSIVE.format(cap=cap), stem + '.%(ext)s',
                          cookies, listener, 'video')
        return json.dumps({'ok': True, 'needs_mux': False, 'path': path,
                           'title': info.get('title', ''),
                           'height': (best_prog or {}).get('height') or 0,
                           'audio_lang': audio_lang})

    except DownloadCancelled:
        return json.dumps({'ok': False, 'cancelled': True, 'error': 'stopped'})
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI
        return json.dumps({'ok': False, 'error': str(exc),
                           'trace': traceback.format_exc()[-600:]})


def _safe(name: str) -> str:
    keep = ' ._-()[]'
    cleaned = ''.join(c for c in name if c.isalnum() or c in keep).strip()
    return (cleaned or 'media')[:110]


def engine_version() -> str:
    from yt_dlp.version import __version__
    return __version__
