"""Point d'entree de l'exe (MGS4RouteEditor.spec) : installe une mise a jour en
attente, puis lance l'editeur depuis le dossier app/ a cote de l'exe (voir
release_files.py). En developpement, lancer route_editor.py.

Mise a jour en attente : dossier update/ a cote de l'exe, prepare par l'editeur
(fichiers verifies) et valide par update/ready.json (liste des fichiers) ; sans
ce fichier, le dossier est un telechargement inacheve et n'est pas applique.
Reprise du lanceur du trainer."""
import ctypes
import json
import os
import runpy
import shutil
import sys

# A augmenter quand l'exe change (nouveau module embarque, nouvelle version de
# Python, PySide6 ou numpy) : le manifeste d'une version qui en a besoin l'exige
# ("min_launcher") et la mise a jour renvoie alors au zip complet.
LAUNCHER_VERSION = 1

ROOT = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
APP_DIR = os.path.join(ROOT, "app")
UPDATE_DIR = os.path.join(ROOT, "update")
READY_FILE = os.path.join(UPDATE_DIR, "ready.json")
LOG_FILE = os.path.join(ROOT, "update.log")


def _log(message):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def _apply_pending_update():
    if not os.path.isfile(READY_FILE):
        return
    try:
        with open(READY_FILE, encoding="utf-8-sig") as handle:
            ready = json.load(handle)
        for path in ready["files"]:
            source = os.path.join(UPDATE_DIR, *path.split("/"))
            target = os.path.join(APP_DIR, *path.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(source, target)
        for path in ready.get("removed", []):
            target = os.path.join(APP_DIR, *path.split("/"))
            if os.path.isfile(target):
                os.remove(target)
        _log(f"mise a jour {ready.get('version', '?')} appliquee ({len(ready['files'])} fichiers)")
    except (OSError, ValueError, KeyError) as exc:
        _log(f"mise a jour non appliquee : {exc!r}")
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)


def main():
    _apply_pending_update()
    entry = os.path.join(APP_DIR, "route_editor.py")
    if not os.path.isfile(entry):
        ctypes.windll.user32.MessageBoxW(
            None, f"Dossier de l'editeur incomplet : {entry} introuvable.\n"
                  "Retelecharge l'editeur complet.", "MGS4 Route Editor", 0x10)
        return
    sys.path.insert(0, APP_DIR)
    sys.mgs4_route_editor_launcher = LAUNCHER_VERSION
    runpy.run_path(entry, run_name="__main__")


if __name__ == "__main__":
    main()
