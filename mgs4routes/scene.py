"""Decor texture de la vue 3D : modeles affiches par le jeu (2026-11-21).

Le jeu tient la liste des modeles affiches avec leur matrice monde
(GameLink.model_instances). On garde ceux sans os (terrain, batiments,
accessoires ; les personnages ont des os), on lit leurs triangles (mdn_mesh)
et on les regroupe par image de couleur : 2e numero de texture du materiau
(1er = relief de detail, 3e = normales, 4e = eclairage ; releve s03a10l)."""
import struct

import numpy as np

from mgs4routes import mdn_mesh

# eclairage precalcule du jeu (couleur des sommets, ~60/255 en moyenne) : mis
# a l'echelle pour une luminosite proche du jeu, plafonne
VERTEX_LIGHT_SCALE, VERTEX_LIGHT_MAX = 3.2, 1.5
BLEND_ALPHA = 0  # transparence par l alpha des sommets DESACTIVEE : cet alpha ne sert pas qu au dosage (murs entiers translucides, essai s01a57l 2026-11-21)
# decor : 0 os (terrain de s03a10l, s01a57l) ou quelques os (accessoires et
# terrain de s01a20l : 1 os pour 176 instances, 5 ou 7 parfois) ; les
# personnages en ont 49 a 115 (releve s01a20l, 2026-11-22)
MAX_DECOR_BONES = 8
LIGHT = np.array((0.35, 0.85, 0.40))
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def diffuse_texture(model: dict, material: int) -> int | None:
    """Identifiant de l'image de couleur d'un materiau."""
    if not 0 <= material < len(model["materials"]):
        return None
    slots = model["materials"][material]
    if not slots:
        return None
    slot = slots[1] if len(slots) >= 2 else slots[0]
    return model["textures"][slot] if 0 <= slot < len(model["textures"]) else None


class SceneBuilder:
    """Modeles lus une fois par fichier ; scene assemblee a la demande."""

    def __init__(self, files, library):
        self.files, self.library = files, library
        self._models: dict[int, dict | None] = {}
        self._paths: dict[int, str] = {}
        for path in files.entries:
            if path.endswith(".mdn"):
                try:
                    self._paths.setdefault(int(path.rsplit("/", 1)[-1][:8], 16), path)
                except ValueError:
                    pass

    def model(self, ident: int) -> dict | None:
        if ident not in self._models:
            path = self._paths.get(ident & 0xFFFFFF)
            model = None
            if path:
                try:
                    data = self.files.read(path)
                    bones = struct.unpack_from(">I", data, 8)[0]
                    model = mdn_mesh.parse(data) if bones <= MAX_DECOR_BONES else None  # personnages ecartes
                    if model is not None:
                        model["bones"] = bones
                except (OSError, KeyError, ValueError, struct.error):
                    model = None
            self._models[ident] = model
        return self._models[ident]

    def build(self, instances, cut: float | None = None, progress=None) -> list[tuple]:
        """[(tableau (N, 9) x, y, z, u, v, r, g, b, a en coordonnees de la vue, image RGBA, melange)]
        par texture et par sorte (opaque / couche de melange).
        cut : hauteur (mm) au-dessus de laquelle les triangles sont retires."""
        groups: dict[tuple, list[np.ndarray]] = {}
        for k, (ident, matrix, *rest) in enumerate(instances):
            if progress:
                progress(k, len(instances))
            model = self.model(ident)
            if model is None:
                continue
            if model.get("bones") and rest and all(v == v and abs(v) < 1e8 for v in rest[0]):
                matrix = rest[0]  # modele a os : place par la matrice de son os
            m = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
            for positions, uv, idx, material, colors in model["parts"]:
                world = positions.astype(np.float64) @ m[:3, :3] + m[3, :3]
                tris = world[idx]  # (n, 3, 3)
                if cut is not None:
                    keep = tris[:, :, 1].min(axis=1) <= cut
                    if not keep.any():
                        continue
                    tris, tidx = tris[keep], idx[keep]
                else:
                    tidx = idx
                n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
                n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
                shade = 0.5 + 0.5 * np.abs(n @ LIGHT)
                rows = np.zeros((len(tris), 3, 9), np.float32)
                rows[:, :, 0] = -tris[:, :, 0] / 1000.0
                rows[:, :, 1] = tris[:, :, 1] / 1000.0
                rows[:, :, 2] = -tris[:, :, 2] / 1000.0
                if uv is not None:
                    rows[:, :, 3:5] = uv[tidx]
                if colors is not None:  # eclairage du jeu, adouci par la face
                    c = colors[tidx].astype(np.float32) / 255.0
                    light = np.minimum(c[:, :, :3] * VERTEX_LIGHT_SCALE, VERTEX_LIGHT_MAX)
                    rows[:, :, 5:8] = light * (0.85 + 0.15 * shade)[:, None, None]
                    rows[:, :, 8] = c[:, :, 3]
                    blended = bool((colors[:, 3] < BLEND_ALPHA).any())
                else:
                    rows[:, :, 5:8] = shade[:, None, None]
                    rows[:, :, 8] = 1.0
                    blended = False
                key = (diffuse_texture(model, material), blended)
                groups.setdefault(key, []).append(rows.reshape(-1, 9))
        out = []
        for k, ((texture, blended), parts) in enumerate(groups.items()):
            if progress:
                progress(k, len(groups))
            image = self.library.image(texture) if texture is not None and self.library else None
            out.append((np.concatenate(parts), image, blended))
        out.sort(key=lambda b: b[2])  # couches de melange en dernier
        return out
