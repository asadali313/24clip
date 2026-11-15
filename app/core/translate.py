"""Offline translation via Argos Translate (open source, local, on D:).

Language packs are downloaded on demand (~80-100 MB each) into
D:\\AI_Clip_Generator\\models\\xdg\\data\\argos-translate. Translation is
optional: features degrade gracefully when a pack is missing.
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import config

# XDG dirs must point at D: BEFORE argostranslate imports.
_ARGOS_ENV_READY = False

POPULAR_TARGETS = ["es", "fr", "de", "pt", "it", "hi", "ar", "ru", "ja",
                   "zh", "ko", "tr"]


def _prepare_env() -> None:
    global _ARGOS_ENV_READY
    if _ARGOS_ENV_READY:
        return
    xdg = config.MODELS_DIR / "xdg"
    os.environ.setdefault("XDG_DATA_HOME", str(xdg / "data"))
    os.environ.setdefault("XDG_CACHE_HOME", str(xdg / "cache"))
    os.environ.setdefault("XDG_CONFIG_HOME", str(xdg / "config"))
    for d in (xdg / "data", xdg / "cache", xdg / "config"):
        Path(d).mkdir(parents=True, exist_ok=True)
    _ARGOS_ENV_READY = True


def _argos():
    _prepare_env()
    import argostranslate.package
    import argostranslate.settings
    return argostranslate.package, argostranslate.settings


def installed_pairs() -> list[tuple[str, str]]:
    pkg, _s = _argos()
    return [(p.from_code, p.to_code)
            for p in pkg.get_installed_packages()]


def available_pairs() -> list[tuple[str, str]]:
    pkg, _s = _argos()
    try:
        return [(p.from_code, p.to_code) for p in
                pkg.get_available_packages()]
    except Exception:
        return []


def ensure_pair(from_code: str, to_code: str,
                progress_cb=None) -> bool:
    """Install the translation pack if missing (requires internet once)."""
    if (from_code, to_code) in installed_pairs():
        return True
    pkg, settings = _argos()
    available = pkg.get_available_packages()
    match = next((p for p in available
                  if p.from_code == from_code and p.to_code == to_code), None)
    if match is None:
        return False
    match.download()
    model_file = Path(settings.downloads_dir) / (
        f"translate-{from_code}_{to_code}.argosmodel")
    if model_file.exists():
        pkg.install_from_path(model_file)
    if progress_cb:
        progress_cb("installed")
    return (from_code, to_code) in installed_pairs()


def translate_text(text: str, from_code: str, to_code: str) -> str:
    if from_code == to_code:
        return text
    if (from_code, to_code) not in installed_pairs():
        raise RuntimeError(
            f"The {from_code}->{to_code} language pack is not installed.")
    import argostranslate.translate
    return argostranslate.translate.translate(text, from_code, to_code)


def translate_sentences(sentences: list[dict], from_code: str, to_code: str,
                        progress_cb=None) -> list[str]:
    """Translate a list of {text: ...} dicts, returning translated strings
    in order. Batched per call for speed."""
    if from_code == to_code:
        return [s["text"] for s in sentences]
    if (from_code, to_code) not in installed_pairs():
        raise RuntimeError(
            f"The {from_code}->{to_code} language pack is not installed.")
    import argostranslate.translate
    texts = [s["text"] for s in sentences]
    out = []
    n = len(texts)
    for i in range(0, n, 25):
        out.extend(argostranslate.translate.translate_batch(
            texts[i:i + 25], from_code, to_code))
        if progress_cb:
            progress_cb(min(1.0, (i + 25) / max(1, n)))
    return out
