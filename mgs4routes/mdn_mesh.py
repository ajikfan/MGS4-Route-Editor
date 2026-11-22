"""Triangles des modeles MDN (affichage) pour la vue 3D (2026-11-21).

Format grand-boutiste, d'apres le lecteur du trainer (core/mdn.py, lui-meme
d'apres Codec zoft123/codec, licence MIT) et un releve sur s03a10l :

- maillage (0x50) : +0x8 nombre d'entrees de faces, +0xC premiere entree,
  +0x10 definition de sommets, +0x18 nombre de sommets, +0x20 / +0x30 boite
  (max / min), +0x40 position ;
- entree de faces (0x10) : type (u16), nombre d'indices (u16), position en
  octets dans le tampon de faces (u32), materiau (u32), puis un intervalle
  de sommets (u16 debut, u16 nombre) ; indices sur 16 bits, liste de
  triangles ;
- definition de sommets (0x30) : ?, nombre de champs, pas, debut dans le
  tampon de sommets, 16 octets de types (4 bits bas = sens : 0 position,
  8 coordonnees de texture ; 4 bits hauts = format), 16 octets de positions ;
- materiau (0x70) : ?, empreinte du nom, nombre, puis des numeros de texture ;
- texture (0x20) : identifiant, ?, echelle U, echelle V.

Le terrain et les batiments d'une zone sont des MDN en coordonnees de la zone
(une seule instance a matrice identite dans le jeu)."""
import struct

import numpy as np

HEADER = struct.Struct(">4s21I")
MESH_SIZE, FACE_SIZE, VDEF_SIZE, MATERIAL_SIZE, TEXTURE_SIZE = 0x50, 0x10, 0x30, 0x70, 0x20
FIELD_POSITION, FIELD_COLOR, FIELD_UV = 0, 3, 8  # couleur : 4 octets RGBA (eclairage precalcule, alpha = dosage)


def _half(raw: np.ndarray) -> np.ndarray:
    return raw.view(">f2").astype(np.float32)


def parse(data: bytes) -> dict:
    """{"parts": [(positions (N,3), uv (N,2) ou None, indices (M,3), materiau, couleurs (N,4) uint8 ou None)],
    "materials": [[numeros de texture]], "textures": [identifiants]}."""
    values = HEADER.unpack_from(data, 0)
    if values[0] != b"MDN ":
        raise ValueError("pas un modele MDN")
    (_id, _bones, _groups, meshes, _faces, _vdefs, materials, textures, _skins, _bo, _go, mesh_off, face_off,
     vdef_off, mat_off, tex_off, _so, vbuf, _vsize, fbuf, _fsize) = values[1:]
    texture_ids = [struct.unpack_from(">I", data, tex_off + i * TEXTURE_SIZE)[0] for i in range(textures)]
    material_textures = []
    for i in range(materials):
        base = mat_off + i * MATERIAL_SIZE
        count = struct.unpack_from(">I", data, base + 8)[0]
        material_textures.append(list(struct.unpack_from(f">{min(count, 8)}I", data, base + 12)))
    parts = []
    for m in range(meshes):
        base = mesh_off + m * MESH_SIZE
        face_count, first_face, vdef, vertex_count = struct.unpack_from(">IIII", data, base + 8)[0:2] + \
            struct.unpack_from(">I", data, base + 0x10) + struct.unpack_from(">I", data, base + 0x18)
        d = vdef_off + vdef * VDEF_SIZE
        _pad, fields, stride, start = struct.unpack_from(">4I", data, d)
        kinds = data[d + 0x10:d + 0x10 + fields]
        offsets = data[d + 0x20:d + 0x20 + fields]
        at = {}
        for kind, offset in zip(kinds, offsets):
            at.setdefault(kind & 0x0F, (kind >> 4, offset))
        if FIELD_POSITION not in at or not vertex_count:
            continue
        raw = np.frombuffer(data, dtype=np.uint8, count=stride * vertex_count, offset=vbuf + start)
        raw = raw.reshape(vertex_count, stride)
        p_off = at[FIELD_POSITION][1]
        positions = raw[:, p_off:p_off + 12].copy().view(">f4").astype(np.float32).reshape(vertex_count, 3)
        uv = None
        colors = None
        if FIELD_COLOR in at:
            c_off = at[FIELD_COLOR][1]
            colors = raw[:, c_off:c_off + 4].copy()
        if FIELD_UV in at:
            u_off = at[FIELD_UV][1]
            uv = _half(raw[:, u_off:u_off + 4].copy().reshape(-1)).reshape(vertex_count, 2)
        for k in range(face_count):
            f = face_off + (first_face + k) * FACE_SIZE
            _kind, count, offset, material = struct.unpack_from(">HHII", data, f)
            idx = np.frombuffer(data, dtype=">u2", count=count, offset=fbuf + offset).astype(np.int64)
            idx = idx[: len(idx) // 3 * 3].reshape(-1, 3)
            idx = idx[(idx < vertex_count).all(axis=1)]
            if len(idx):
                parts.append((positions, uv, idx, material, colors))
    return {"parts": parts, "materials": material_textures, "textures": texture_ids}


def triangles(model: dict) -> np.ndarray:
    """Tous les triangles (N, 3, 3) du modele."""
    tris = [part[0][part[2]] for part in model["parts"]]
    return np.concatenate(tris) if tris else np.zeros((0, 3, 3), np.float32)
