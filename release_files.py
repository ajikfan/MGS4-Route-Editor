"""Fichiers du code de l'editeur, livres a part de l'exe (dossier app/) et
remplacables par la mise a jour : une seule liste pour le build
(MGS4RouteEditor.spec) et pour le manifeste des releases.

L'exe (launcher.py, empaquete par PyInstaller) ne contient que Python,
PySide6, numpy et les modules standard de RUNTIME_MODULES : il ne change que si
le code a besoin d'un module qui n'y est pas (LAUNCHER_VERSION de launcher.py, a
augmenter alors, et "min_launcher" du manifeste).

Les routes fournies (provided_routes/, remplies par
tools/export_provided_routes.py) font partie du code : chaque mise a jour les
remplace."""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# Fichiers et dossiers du code, relatifs a la racine du depot.
APP_ENTRIES = (
    "route_editor.py",
    "mgs4routes",
    "locales",
    "provided_routes",
    "assets",
)
_SKIPPED_DIRS = {"__pycache__"}
_SKIPPED_SUFFIXES = (".pyc",)

# Modules embarques dans l'exe : ceux qu'importe le code, plus ceux de la mise
# a jour, plus quelques modules standard courants pour que les prochaines
# versions du code n'exigent pas un nouvel exe.
RUNTIME_MODULES = (
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "numpy",
    "ctypes", "ctypes.wintypes", "json", "os", "re", "struct", "sys", "math", "threading", "time",
    "faulthandler", "traceback", "datetime", "types", "glob", "copy", "unicodedata",
    # mise a jour
    "hashlib", "ssl", "http.client", "urllib.request", "urllib.error", "urllib.parse", "shutil", "webbrowser",
    # reserve
    "base64", "bisect", "collections", "contextlib", "csv", "dataclasses", "enum", "functools", "heapq",
    "html", "io", "itertools", "logging", "pathlib", "queue", "random", "string", "subprocess", "tempfile",
    "textwrap", "typing", "uuid", "zipfile",
)


def app_files():
    """Chemins relatifs (separateur "/") des fichiers du code."""
    out = []
    for entry in APP_ENTRIES:
        path = os.path.join(ROOT, entry)
        if os.path.isfile(path):
            out.append(entry)
            continue
        for folder, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in _SKIPPED_DIRS)
            for name in sorted(files):
                if not name.endswith(_SKIPPED_SUFFIXES):
                    out.append(os.path.relpath(os.path.join(folder, name), ROOT).replace(os.sep, "/"))
    return out
