"""Langue de l'editeur (francais / anglais), comme le trainer : chaque texte de
l'interface est une cle de locales/<langue>/strings.json, lue par tr().

- Les deux langues viennent de fichiers JSON (pas de francais en dur dans le
  code, l'anglais n'est pas une surcouche) ; une cle absente de l'anglais
  retombe sur le francais, une cle absente partout s'affiche telle quelle
  (on repere ainsi un oubli sans planter).
- Choix garde dans settings.json (cle "language"). Changer de langue relance
  l'editeur : pas de retraduction a chaud des elements deja construits.

Sans Qt : importable partout (editeur, vue 3D)."""
import json
import os
import sys

DEFAULT_LANGUAGE = "fr"
SUPPORTED_LANGUAGES = {"fr": "Français", "en": "English"}

# meme regle que l'editeur : donnees a cote du lanceur (ou de l'exe empaquete)
DATA_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# fichiers livres avec le code (locales, routes fournies) : a cote de lui, dans le depot
# ou dans app/ a cote de l'exe (voir release_files.py), jamais dans l'exe lui-meme
BUNDLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
LOCALES_DIR = os.path.join(BUNDLE_DIR, "locales")


def _settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def current_language() -> str:
    lang = _settings().get("language", DEFAULT_LANGUAGE)
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def set_language(lang: str) -> None:
    data = _settings()
    data["language"] = lang
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def update_check_enabled() -> bool:
    """Recherche d'une mise a jour au lancement (active par defaut)."""
    return bool(_settings().get("check_updates", True))


def set_update_check(enabled: bool) -> None:
    data = _settings()
    data["check_updates"] = bool(enabled)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def restart() -> None:
    """Relance l'editeur a l'identique (meme exe ou script, memes arguments)."""
    args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv  # exe : pas de script a repasser
    os.execv(sys.executable, [sys.executable] + args)


def _load(lang: str, category: str = "strings") -> dict:
    try:
        with open(os.path.join(LOCALES_DIR, lang, f"{category}.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


LANGUAGE = current_language()
_STRINGS = _load(DEFAULT_LANGUAGE)
if LANGUAGE != DEFAULT_LANGUAGE:
    _STRINGS.update(_load(LANGUAGE))


def tr(key: str, **values) -> str:
    """Texte de la cle dans la langue active ; values remplissent ses {noms}."""
    text = _STRINGS.get(key, key)
    if values:
        try:
            return text.format(**values)
        except (KeyError, IndexError, ValueError):
            return text
    return text
