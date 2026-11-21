"""Navigation des blocs .nv2 (releve sur s01a57l, 2026-11-20/21).

Le terrain d'une zone est fait de blocs partages, ranges dans les
« slots » (ww/stage/slot/<n>/cache/<id>.geom, .nv2, .vlm), que le jeu
charge selon la zone et la progression.

Section BSPN d'un .nv2 (gros-boutien ; base = debut de BSPN + 0x10) :
+0x14 nombre de noeuds, +0x18 table des noeuds, +0x1C table des feuilles,
+0x20 fin (decalages depuis la base). Noeud de 24 octets : enfant avant
(u16), enfant arriere (u16), plan (normale X, Y, Z, distance : n . p = d,
l'avant est du cote positif), numero de feuille (u32, -1 sauf pour les
feuilles, dont les deux enfants valent 0xFFFF). Table des feuilles : un
decalage par feuille vers un bloc : 0x120, nombre de segments, 0, puis les
segments de 44 octets : etiquette (groupe << 16 | numero), indicateurs, 0,
centre (X, Y, Z), normale, demi-longueur, hauteur.

Une feuille est une cellule convexe d'espace praticable (3D : certains
plans sont horizontaux). Ses segments sont les PASSAGES vers les cellules
voisines (chacun est liste dans les deux cellules) ; les feuilles sans
segment sont pleines. Les murs sont donc les bords d'une cellule que ne
couvre aucun passage, et le sol praticable est la reunion des cellules,
chacune a la hauteur de son sol (pied le plus bas de ses passages).

walls() ci-dessous (balayage des segments) ne sert plus qu'a l'index des
blocs (etendue de chaque bloc)."""
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


# ------------------------------------------------------------ cellules (BSP)
CELL_CACHE_VERSION = 2
# Nature d'une cellule (2026-11-21, s01a57l : mur derriere une caisse, caisse) :
# chaque passage a une plage de hauteur (pied, hauteur libre). Un mur bas ou
# une caisse est une cellule fine (ou petite) aux passages limites en
# hauteur ; une grande cellule a faible hauteur libre est un abri (auvent,
# porche, dessous de passerelle).
OBSTACLE_WIDTH = 900      # mm : plus fine que ca = obstacle (mur, muret, poteau)
OBSTACLE_LOW = 1500       # mm de hauteur libre : en dessous, petite cellule = obstacle
OBSTACLE_AREA = 4.0e6     # mm2 (4 m2)
SHELTER_LOW = 2500        # mm de hauteur libre : en dessous = abri


def _min_width(poly) -> float:
    best = None
    for i in range(len(poly)):
        p, q = poly[i], poly[(i + 1) % len(poly)]
        length = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
        if length < 1:
            continue
        far = max(abs((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])) / length for r in poly)
        best = far if best is None else min(best, far)
    return best or 0.0


def _area(poly) -> float:
    return abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(poly, poly[1:] + poly[:1]))) / 2


def cell_kind(poly, clearance: float) -> str:
    """"obstacle", "shelter" (abri) ou "floor"."""
    if _min_width(poly) < OBSTACLE_WIDTH or (clearance < OBSTACLE_LOW and _area(poly) < OBSTACLE_AREA):
        return "obstacle"
    return "shelter" if clearance < SHELTER_LOW else "floor"


def _clip(poly, nx: float, nz: float, d: float, positive: bool):
    """Polygone convexe (X, Z) coupe par le demi-plan n . p >= d (ou <= d)."""
    out = []
    sign = 1.0 if positive else -1.0
    for i in range(len(poly)):
        p, q = poly[i], poly[(i + 1) % len(poly)]
        fp = sign * (nx * p[0] + nz * p[1] - d)
        fq = sign * (nx * q[0] + nz * q[1] - d)
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def cells(data: bytes) -> list[dict]:
    """Cellules praticables d'un bloc : {"polygon": [(x, z)], "floor": mm,
    "walls": [(x1, z1, x2, z2)], "portals": [(x1, z1, x2, z2, pied, hauteur)]}.
    Contour vu de dessus (les plans presque horizontaux ne decoupent pas)."""
    at = data.find(b"BSPN")
    if at < 0:
        return []
    base = at + 0x10
    count, node_table, leaf_table, _end = struct.unpack_from(">4I", data, at + 0x14)
    nodes = [struct.unpack_from(">HH4fI", data, base + node_table + 24 * k) for k in range(count)]
    leaves = sum(1 for n in nodes if n[0] == 0xFFFF)
    offsets = struct.unpack_from(f">{leaves}I", data, base + leaf_table)
    portals = []
    for offset in offsets:
        block = base + offset
        number = struct.unpack_from(">I", data, block + 4)[0]
        segs = []
        for i in range(number):
            _tag, _flags, _zero, x, y, z, nx, _ny, nz, half, height = struct.unpack_from(
                ">3I8f", data, block + 12 + 44 * i)
            tx, tz = -nz, nx
            segs.append((x - tx * half, z - tz * half, x + tx * half, z + tz * half, y, height, x, z, nx, nz, half))
        portals.append(segs)
    points = [(s[0], s[1]) for segs in portals for s in segs] + [(s[2], s[3]) for segs in portals for s in segs]
    if not points:
        return []
    margin = 1.0
    x0, x1 = min(p[0] for p in points) - margin, max(p[0] for p in points) + margin
    z0, z1 = min(p[1] for p in points) - margin, max(p[1] for p in points) + margin
    box = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    polygons = {}
    stack = [(0, box)]
    while stack:
        k, poly = stack.pop()
        front, back, nx, ny, nz, d, leaf = nodes[k]
        if front == 0xFFFF:
            polygons[leaf] = poly
            continue
        if abs(ny) > 0.5:  # plan presque horizontal : meme contour des deux cotes
            stack += [(front, poly), (back, poly)]
            continue
        for child, positive in ((front, True), (back, False)):
            clipped = _clip(poly, nx, nz, d, positive)
            if len(clipped) >= 3:
                stack.append((child, clipped))
    out = []
    for leaf, segs in enumerate(portals):
        poly = polygons.get(leaf)
        if not segs or not poly or len(poly) < 3:
            continue
        walls = []
        for i in range(len(poly)):
            p, q = poly[i], poly[(i + 1) % len(poly)]
            # bord du cadre du bloc : la suite est dans le bloc voisin
            on_x = abs(p[0] - q[0]) < 1 and min(abs(p[0] - x0), abs(p[0] - x1)) < 2
            on_z = abs(p[1] - q[1]) < 1 and min(abs(p[1] - z0), abs(p[1] - z1)) < 2
            if on_x or on_z:
                continue
            walls += _uncovered(p, q, segs)
        clearance = min(s[5] for s in segs)
        out.append({"polygon": [(round(x), round(z)) for x, z in poly],
                    "floor": min(s[4] for s in segs),
                    "clearance": round(clearance),
                    "kind": cell_kind(poly, clearance),
                    "walls": [tuple(round(v) for v in w) for w in walls],
                    "portals": [tuple(round(v) for v in s[:6]) for s in segs]})
    return out


def _uncovered(p, q, segs, tolerance: float = 60.0, minimum: float = 100.0) -> list[tuple]:
    """Morceaux du bord p-q qu'aucun passage de la cellule ne couvre."""
    length = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
    if length < 1:
        return []
    ux, uz = (q[0] - p[0]) / length, (q[1] - p[1]) / length
    spans = []  # passages portes par ce bord : intervalles le long du bord
    for s in segs:
        cx, cz, nx, nz, half = s[6], s[7], s[8], s[9], s[10]
        if abs((cx - p[0]) * uz - (cz - p[1]) * ux) > tolerance or abs(nx * uz - nz * ux) < 0.99:
            continue  # pas sur la meme droite
        t = (cx - p[0]) * ux + (cz - p[1]) * uz
        spans.append((t - half - tolerance, t + half + tolerance))
    spans.sort()
    out, cursor = [], 0.0
    for a, b in spans:
        if a > cursor:
            out.append((cursor, min(a, length)))
        cursor = max(cursor, b)
        if cursor >= length:
            break
    if cursor < length:
        out.append((cursor, length))
    return [(p[0] + ux * a, p[1] + uz * a, p[0] + ux * b, p[1] + uz * b) for a, b in out if b - a > minimum]


class CellCache:
    """Cellules par bloc, calculees une fois et gardees dans un fichier par bloc."""

    def __init__(self, files, cache_dir: str):
        self.files, self.cache_dir = files, cache_dir
        self._memory: dict[str, list] = {}

    def get(self, path: str) -> list[dict]:
        if path in self._memory:
            return self._memory[path]
        name = os.path.join(self.cache_dir, path.split("/")[-1] + ".json")
        result = None
        try:
            with open(name, encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("version") == CELL_CACHE_VERSION:
                result = data["cells"]
        except (OSError, ValueError, KeyError):
            pass
        if result is None:
            try:
                result = cells(self.files.read(path))
            except (OSError, KeyError, struct.error):
                result = []
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(name, "w", encoding="utf-8") as handle:
                    json.dump({"version": CELL_CACHE_VERSION, "cells": result}, handle)
            except OSError:
                pass
        self._memory[path] = result
        return result


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
