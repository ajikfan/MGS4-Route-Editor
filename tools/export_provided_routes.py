"""Copie mes routes (routes/<zone>.json) dans provided_routes/, les routes
fournies avec l'editeur et mises a jour a chaque version.

python tools/export_provided_routes.py

Seules les routes d'au moins 2 points sont gardees ; l'etat d'affichage
(route masquee) n'est pas copie. Les fichiers de provided_routes/ sont
remplaces : c'est le contenu de routes/ qui fait foi."""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "routes")
TARGET = os.path.join(ROOT, "provided_routes")
KEEP = ("name", "color", "loop", "points")


def main() -> int:
    os.makedirs(TARGET, exist_ok=True)
    for old in glob.glob(os.path.join(TARGET, "*.json")):
        os.remove(old)
    total = 0
    for path in sorted(glob.glob(os.path.join(SOURCE, "*.json"))):
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        routes = [{k: r[k] for k in KEEP if k in r} for r in data.get("routes", []) if len(r.get("points", [])) >= 2]
        if not routes:
            continue
        zone = data.get("zone") or os.path.splitext(os.path.basename(path))[0]
        with open(os.path.join(TARGET, f"{zone}.json"), "w", encoding="utf-8") as handle:
            json.dump({"zone": zone, "routes": routes}, handle, ensure_ascii=False, indent=1)
        total += len(routes)
        print(f"{zone} : {len(routes)} route(s)")
    print(f"{total} route(s) fournie(s) dans {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
