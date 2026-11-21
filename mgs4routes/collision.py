"""Maillages de collision des .geom (releve sur s01a57l, 2026-11-21).

Un .geom contient des descripteurs de 32 octets (gros-boutiens) :
0x11, type, ..., 0, table des sommets (+0xC), premier bloc de faces (+0x10),
materiaux, nombre, indicateurs ; adresses depuis le debut du fichier. Ceux
de la section 0 d'un .geom de bloc forment le terrain (sol, batiments,
caisses, escaliers), ceux de la section 1 les objets.

Table des sommets : total, nombre N d'entrees d'en-tete, puis des entrees
de 16 octets : origine (X, Y, Z ; coin de la case de la grille pour le
terrain), N - 1 normales des faces, puis les sommets, relatifs a l'origine.

Faces : un arbre de boites 01042004 (boite en +0x20 / +0x30, frere suivant
a +4 x 16 octets, enfant en +0x40) dont les feuilles NN002012 portent NN
quadrilateres de 8 octets en +0x20 : 4 indices sur un octet, puis un octet
de poids forts (2 bits par indice, bits 0-1 pour le premier). Un
descripteur peut aussi pointer directement sur une feuille. Un quadrilatere
dont les deux derniers indices sont egaux est un triangle.

Ce sont les formes de collision (pas les modeles affiches) : volumes justes,
sans textures."""
import os
import struct

import numpy as np

BIG = b"\x01\x04\x20\x04"
MESH_CACHE_VERSION = 3


def _vertices(g: bytes, vt: int) -> np.ndarray:
    total, transforms = struct.unpack_from(">II", g, vt)
    entries = np.frombuffer(g, dtype=">f4", count=4 * total, offset=vt + 0x10).reshape(total, 4).astype(np.float64)
    origin = entries[0, :3] if transforms >= 1 else np.zeros(3)
    return entries[transforms:, :3] + origin


def _leaves(g: bytes, c: int, out: list, depth: int = 0) -> None:
    """Feuilles NN002012 d'un arbre de boites 01042004 (chaque noeud : enfant en
    +0x40, frere suivant a +4 x 16 octets, 0 = dernier)."""
    while depth < 64:
        sig = g[c:c + 4]
        if sig == BIG:
            step = struct.unpack_from(">I", g, c + 4)[0]
            _leaves(g, c + 0x40, out, depth + 1)
            if not step:
                return
            c += 16 * step
        elif sig[1:4] == b"\x00\x20\x12":
            out.append(c)
            return
        else:
            return


def _quads(g: bytes, ch: int) -> list[tuple]:
    """Quadrilateres : 4 indices sur un octet, plus 2 bits de poids fort par
    indice dans le premier octet suivant (bits 0-1 pour le premier...)."""
    leaves = []
    _leaves(g, ch, leaves)
    quads = []
    for leaf in leaves:
        for k in range(g[leaf]):
            p = leaf + 0x20 + 8 * k
            high = g[p + 4]
            quads.append(tuple(g[p + i] + 256 * ((high >> (2 * i)) & 3) for i in range(4)))
    return quads


def _grid_bounds(g: bytes):
    """Etendue de la grille de la section 0 (origine, taille des cases, nombre)."""
    try:
        count = struct.unpack_from(">I", g, 0x08)[0]
        for k in range(count):
            kind, _size, offset = struct.unpack_from(">III", g, 0x20 + 12 * k)
            if kind >> 16 == 0:
                ox, oy, oz, _w, cx, cy, cz = struct.unpack_from(">7f", g, offset)
                nx, ny, nz = struct.unpack_from(">3I", g, offset + 0x20)
                return ox, oz, ox + cx * nx, oz + cz * nz
    except struct.error:
        pass
    return None


def triangles(g: bytes) -> np.ndarray:
    """Triangles (N, 3, 3) en mm, coordonnees de la zone."""
    out = []
    a, end = 0x80, len(g) - 0x20
    while a <= end:
        if g[a] == 0x11 and g[a + 8:a + 12] == b"\0\0\0\0":
            vt, ch = struct.unpack_from(">II", g, a + 0xC)
            if 0 < vt < len(g) and 0 < ch < len(g):
                sig = g[ch:ch + 4]
                if sig == BIG or sig[1:4] == b"\x00\x20\x12":
                    try:
                        verts = _vertices(g, vt)
                        quads = _quads(g, ch)
                    except (struct.error, ValueError):
                        a += 0x20
                        continue
                    n = len(verts)
                    for i0, i1, i2, i3 in quads:
                        if max(i0, i1, i2, i3) >= n:
                            continue
                        out.append((verts[i0], verts[i1], verts[i2]))
                        if i3 != i2:
                            out.append((verts[i0], verts[i2], verts[i3]))
                    a += 0x20
                    continue
        a += 4
    if not out:
        return np.zeros((0, 3, 3), np.float32)
    tris = np.asarray(out, dtype=np.float32)
    bounds = _grid_bounds(g)
    if bounds:  # une piece sans transformation tombe pres de l'origine : hors de la grille
        x0, z0, x1, z1 = bounds
        margin = 20000
        cx, cz = tris[:, :, 0].mean(axis=1), tris[:, :, 2].mean(axis=1)
        tris = tris[(cx > x0 - margin) & (cx < x1 + margin) & (cz > z0 - margin) & (cz < z1 + margin)]
    return tris


def clip_below(tris: np.ndarray, top: float) -> np.ndarray:
    """Garde ce qui est sous Y = top (triangles coupes au besoin)."""
    if not len(tris):
        return tris
    y = tris[:, :, 1]
    keep = tris[(y <= top).all(axis=1)]
    cross = tris[(y <= top).any(axis=1) & (y > top).any(axis=1)]
    pieces = []
    for tri in cross:
        poly = []
        for i in range(3):
            p, q = tri[i], tri[(i + 1) % 3]
            if p[1] <= top:
                poly.append(p)
            if (p[1] <= top) != (q[1] <= top):
                t = (top - p[1]) / (q[1] - p[1])
                poly.append(p + t * (q - p))
        for k in range(1, len(poly) - 1):
            pieces.append((poly[0], poly[k], poly[k + 1]))
    if pieces:
        keep = np.concatenate([keep, np.asarray(pieces, dtype=np.float32)])
    return keep


class MeshCache:
    """Triangles par .geom, calcules une fois et gardes en .npy."""

    def __init__(self, files, cache_dir: str):
        self.files, self.cache_dir = files, cache_dir
        self._memory: dict[str, np.ndarray] = {}

    def get(self, path: str) -> np.ndarray:
        if path in self._memory:
            return self._memory[path]
        name = os.path.join(self.cache_dir, f"{path.split('/')[-1]}.v{MESH_CACHE_VERSION}.npy")
        try:
            tris = np.load(name)
        except (OSError, ValueError):
            try:
                tris = triangles(self.files.read(path))
            except (OSError, KeyError, struct.error):
                tris = np.zeros((0, 3, 3), np.float32)
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                np.save(name, tris)
            except OSError:
                pass
        self._memory[path] = tris
        return tris
