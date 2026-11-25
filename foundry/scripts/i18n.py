"""UI message catalogs: prefs `ui.language` (ko | ja | en) picks the language of the board, the pane, board.html and
CLI text. Outward text (Jira, Linear, Obsidian) follows `sync.languages` instead, and text already written to the
journal stays in the language it was written in.

Catalogs live in defaults/i18n/<lang>/<namespace>.json, one file per namespace so parallel work never collides.
A key missing in the chosen language falls back to en, and a key missing everywhere renders as itself.
Placeholders are named ({n}, {unit}): word order differs between languages, so never join translated fragments.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LANGS = ("ko", "ja", "en")
FALLBACK = "en"
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "defaults", "i18n")
_CAT = {}
_LANG = {}
_MISSING = set()


def namespaces():
    return sorted(f[:-5] for f in os.listdir(os.path.join(DIR, FALLBACK)) if f.endswith(".json"))


def _file(lang, ns):
    p = os.path.join(DIR, lang, f"{ns}.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def lang():
    """prefs ui.language, read once per prefs file version; anything unknown is en."""
    import foundrylib as cl
    p = cl.path("prefs.json")
    try:
        stamp = (p, os.stat(p).st_mtime_ns)
    except OSError:
        stamp = (p, None)
    if stamp not in _LANG:
        import prefs
        v = ((prefs.load().get("ui") or {}).get("language")) or FALLBACK
        _LANG.clear()
        _LANG[stamp] = v if v in LANGS else FALLBACK
    return _LANG[stamp]


def catalog(ns, language=None):
    """The namespace's strings in language (default ui.language), en underneath for missing keys."""
    language = language or lang()
    key = (ns, language)
    if key not in _CAT:
        _CAT[key] = _file(FALLBACK, ns) | (_file(language, ns) if language != FALLBACK else {})
    return _CAT[key]


def t(key, language=None, **kw):
    """`namespace.key` in the UI language, with named placeholders filled."""
    ns, _, k = key.partition(".")
    s = catalog(ns, language).get(k)
    if s is None:
        s = key
        if key not in _MISSING:  # a dev signal, once per key and process, in <state>/i18n-missing.jsonl
            _MISSING.add(key)
            try:
                import foundrylib as cl
                cl.append_log("i18n-missing.jsonl", {"key": key, "language": language or lang()})
            except Exception:
                pass
    return s.format(**kw) if kw else s
