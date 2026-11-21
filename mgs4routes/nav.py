"""Murs des fichiers .nv2 (2026-11-20, releve sur s01a57l).

Le terrain d'une zone est fait de blocs partages, ranges dans les
« slots » (ww/stage/slot/<n>/cache/<id>.geom, .nv2, .vlm), que le jeu
charge selon la zone et la progression. Le .nv2 d'un bloc contient,
entre autres, des segments de mur de 44 octets (gros-boutien) :
centre (X, Y, Z), normale (unitaire, horizontale), demi-longueur,
hauteur, etiquette (groupe << 16 | numero), indicateurs, 0. Le groupe
des murs est propre a chaque bloc : octet 1 du champ +0x24 du deuxieme
en-tete GROP (0x20GG0000 ; 2, 3, 5... selon les blocs). Un mur va de centre - demi * tangente a
centre + demi * tangente, la tangente etant perpendiculaire a la normale.
Ce sont les contours des batiments et obstacles (la carte du menu pause).

Les enregistrements sont trouves par balayage (la structure des blocs
GROP qui les contiennent n'est pas encore decodee), d'ou les filtres."""
import json
import os
import struct

import numpy as np

RECORD = 44


def wall_group(data: bytes) -> int | None:
    """Groupe des murs du bloc, lu dans ses en-tetes GROP."""
    at = data.find(b"GROP")
    while 0 <= at <= len(data) - 0x28:
        field = struct.unpack_from(">I", data, at + 0x24)[0]
        if field >> 24 == 0x20:
            return (field >> 16) & 0xFF
        at = data.find(b"GROP", at + 4)
    return None


def walls(data: bytes) -> list[tuple[float, float, float, float, float, float]]:
    """[(x1, z1, x2, z2, hauteur du pied, hauteur du mur)]."""
    group = wall_group(data)
    if group is None:
        return []
    out = []
    o = 0
    end = len(data) - RECORD
    while o <= end:
        x, y, z, nx, ny, nz, half, height = struct.unpack_from(">8f", data, o)
        tag, _flags, zero = struct.unpack_from(">3I", data, o + 32)
        if (tag >> 16 == group and zero == 0 and 10 < half < 100000 and 0 < height < 100000
                and all(v == v and abs(v) < 1e7 for v in (x, y, z))
                and abs(nx * nx + ny * ny + nz * nz - 1) < 1e-3 and abs(ny) < 0.2):
            tx, tz = -nz, nx
            out.append((x - tx * half, z - tz * half, x + tx * half, z + tz * half, y, height))
            o += RECORD
        else:
            o += 4
    return out


def crossed_by(segments, paths, max_step: float = 20000.0) -> list[bool]:
    """Pour chaque segment de mur, vrai s'il est coupe par un pas d'une
    route (deux points consecutifs a moins de max_step mm).

    Les segments des .nv2 sont en fait les lignes de decoupe de la
    navigation : la plupart suivent un vrai mur, d'autres ne font que
    partager le sol praticable (un soldat les traverse, s01a57l
    2026-11-21). Un segment traverse par une route du jeu ne bloque donc
    rien a cet endroit."""
    steps = [(a[0], a[2], b[0], b[2]) for path in paths for a, b in zip(path, path[1:])
             if 1.0 < ((a[0] - b[0]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5 <= max_step]
    if not segments or not steps:
        return [False] * len(segments)
    walls = np.asarray([s[:4] for s in segments], dtype=np.float64)
    route = np.asarray(steps, dtype=np.float64)
    cx, cz, dx, dz = (route[:, k][None, :] for k in range(4))
    out = []
    for start in range(0, len(walls), 400):
        w = walls[start:start + 400]
        ax, az, bx, bz = (w[:, k][:, None] for k in range(4))
        o1 = (bx - ax) * (cz - az) - (bz - az) * (cx - ax)
        o2 = (bx - ax) * (dz - az) - (bz - az) * (dx - ax)
        o3 = (dx - cx) * (az - cz) - (dz - cz) * (ax - cx)
        o4 = (dx - cx) * (bz - cz) - (dz - cz) * (bx - cx)
        out += ((o1 * o2 < 0) & (o3 * o4 < 0)).any(axis=1).tolist()
    return out


def bounds(segments) -> tuple[float, float, float, float] | None:
    if not segments:
        return None
    xs = [s[0] for s in segments] + [s[2] for s in segments]
    zs = [s[1] for s in segments] + [s[3] for s in segments]
    return (min(xs), min(zs), max(xs), max(zs))


class NavIndex:
    """Index de tous les .nv2 des archives (chemin -> limites des murs),
    calcule une fois et garde dans un fichier."""

    VERSION = 2

    def __init__(self, files, cache_path: str):
        self.files = files
        self.cache_path = cache_path
        self.bounds: dict[str, tuple] = {}
        self._walls: dict[str, list] = {}

    def load_or_build(self, progress=None) -> None:
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("version") == self.VERSION:
                self.bounds = {k: tuple(v) for k, v in data["bounds"].items()}
                return
        except (OSError, ValueError, KeyError):
            pass
        paths = sorted(p for p in self.files.entries if p.endswith(".nv2"))
        for i, path in enumerate(paths):
            if progress:
                progress(i, len(paths))
            b = bounds(self.walls(path))
            if b:
                self.bounds[path] = b
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            json.dump({"version": self.VERSION, "bounds": self.bounds}, handle)

    def walls(self, path: str) -> list:
        if path not in self._walls:
            try:
                self._walls[path] = walls(self.files.read(path))
            except (OSError, KeyError, struct.error):
                self._walls[path] = []
        return self._walls[path]

    def path_for_id(self, ident: int) -> str | None:
        name = f"{ident & 0xFFFFFF:08x}.nv2"
        return next((p for p in self.bounds if p.endswith("/" + name)), None)

    def covering(self, area: tuple[float, float, float, float], minimum: float = 0.25) -> list[str]:
        """Blocs dont les murs recouvrent la zone (part de la surface du bloc
        dans la zone au moins egale a minimum), du plus couvrant au moins."""
        x0, z0, x1, z1 = area
        found = []
        for path, (bx0, bz0, bx1, bz1) in self.bounds.items():
            ix = max(0.0, min(x1, bx1) - max(x0, bx0))
            iz = max(0.0, min(z1, bz1) - max(z0, bz0))
            surface = max((bx1 - bx0) * (bz1 - bz0), 1.0)
            share = ix * iz / surface
            if share >= minimum:
                found.append((share, path))
        return [p for _s, p in sorted(found, reverse=True)]
