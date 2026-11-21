"""Lecture des fichiers .geom des zones (2026-11-20, releve sur s04a40l).

Tout en gros-boutien (fichiers PS3). En-tete : signature, taille du
fichier, nombre de sections (+8), table des sections en +0x20 (entrees
de 12 octets : type << 16, taille, position).

- type 0 : grille spatiale. Origine (3 flottants + 0), taille d'une
  cellule (3 flottants + 1.0), nombre de cellules (3 entiers).
- type 1 : objets de collision, entrees de 0x70 : demi-taille (3
  flottants), forme, centre (3 flottants), matrice 4x4, puis nombre,
  indicateurs, position de leurs donnees, identifiant. Suivis de la
  geometrie elle-meme, compressee (coordonnees 16 bits, index sur un
  octet) : pas encore decodee.
- type 6 : points reperes, entrees de 0x20 : genre, ?, identifiant, ?,
  puis position (3 flottants + 1.0).
- type 7 : routes. Table d'entrees de 16 octets (identifiant, ?, nombre
  de points << 16, position des points) ; points de 0x30 octets :
  position, indicateurs, point regarde, 0x00020001, valeur 32 bits.
  Meme taille que les points de route en memoire (ALLY_ROUTE_TABLE_RVA).

Coordonnees en millimetres ; Y = hauteur, X/Z = plan horizontal."""
import struct
from dataclasses import dataclass, field


@dataclass
class RoutePoint:
    """Point de route (0x30 octets) : position +0, drapeaux +0xC (2 = poste
    de garde), position regardee +0x10, action +0x1C, allure +0x1E,
    attente +0x20, orientation +0x22 (0x10000 = un tour), 0 ensuite."""
    position: tuple[float, float, float]
    flags: int
    look: tuple[float, float, float]
    action: int = 1
    move: int = 1
    wait: int = 0
    angle: int = 0


@dataclass
class GameRoute:
    ident: int
    points: list[RoutePoint]


@dataclass
class Marker:
    ident: int
    kind: int
    position: tuple[float, float, float]


@dataclass
class CollisionObject:
    ident: int
    shape: int
    center: tuple[float, float, float]
    half_size: tuple[float, float, float]


@dataclass
class Geom:
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cell: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cells: tuple[int, int, int] = (0, 0, 0)
    routes: list[GameRoute] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    objects: list[CollisionObject] = field(default_factory=list)

    def bounds(self) -> tuple[float, float, float, float]:
        """(x min, z min, x max, z max) de la grille, sinon des routes."""
        if all(self.cells) and all(self.cell):
            x0, _y0, z0 = self.origin
            return (x0, z0, x0 + self.cell[0] * self.cells[0], z0 + self.cell[2] * self.cells[2])
        xs = [p.position[0] for r in self.routes for p in r.points] or [0.0]
        zs = [p.position[2] for r in self.routes for p in r.points] or [0.0]
        return (min(xs), min(zs), max(xs), max(zs))


def _sections(data: bytes) -> dict[int, tuple[int, int]]:
    count = struct.unpack_from(">I", data, 8)[0]
    out = {}
    for i in range(min(count, 16)):
        kind, size, offset = struct.unpack_from(">3I", data, 0x20 + 12 * i)
        if offset + size <= len(data):
            out[kind >> 16] = (offset, size)
    return out


def parse(data: bytes) -> Geom:
    geom = Geom()
    if len(data) < 0x40:
        return geom
    sections = _sections(data)
    if 0 in sections:
        at, _size = sections[0]
        geom.origin = struct.unpack_from(">3f", data, at)
        geom.cell = struct.unpack_from(">3f", data, at + 0x10)
        geom.cells = struct.unpack_from(">3I", data, at + 0x20)
    if 1 in sections:
        at, size = sections[1]
        end = at + size
        while at + 0x70 <= end:
            half = struct.unpack_from(">3f", data, at)
            shape = struct.unpack_from(">I", data, at + 12)[0]
            center = struct.unpack_from(">3f", data, at + 16)
            matrix_w = struct.unpack_from(">f", data, at + 32 + 60)[0]
            ident = struct.unpack_from(">I", data, at + 108)[0]
            if not 0 < shape < 64 or abs(matrix_w - 1.0) > 1e-3:
                break
            geom.objects.append(CollisionObject(ident, shape, center, half))
            at += 0x70
    if 6 in sections:
        at, size = sections[6]
        for entry in range(at, at + size - 0x1F, 0x20):
            x, y, z, w = struct.unpack_from(">4f", data, entry + 0x10)
            if abs(w - 1.0) < 1e-3:
                kind, _unk, ident = struct.unpack_from(">3I", data, entry)
                geom.markers.append(Marker(ident, kind, (x, y, z)))
    if 7 in sections:
        at, size = sections[7]
        start, end = at, at + size
        while at + 16 <= end:
            ident, _unk, count, offset = struct.unpack_from(">4I", data, at)
            if ident == 0 or not start <= offset < end:
                break
            points = []
            for i in range(count >> 16):
                p = offset + 0x30 * i
                if p + 0x30 > end:
                    break
                position = struct.unpack_from(">3f", data, p)
                flags = struct.unpack_from(">I", data, p + 0x0C)[0]
                look = struct.unpack_from(">3f", data, p + 0x10)
                action, move, wait, angle = struct.unpack_from(">4H", data, p + 0x1C)
                points.append(RoutePoint(position, flags, look, action, move, wait, angle))
            geom.routes.append(GameRoute(ident, points))
            at += 16
    return geom
