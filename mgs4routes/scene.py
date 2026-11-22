"""Decor texture de la vue 3D : modeles affiches par le jeu (2026-11-21).

Le jeu tient la liste des modeles affiches avec leur matrice monde
(GameLink.model_instances). On garde ceux sans os (terrain, batiments,
accessoires ; les personnages ont des os), on lit leurs triangles (mdn_mesh)
et on les regroupe par image de couleur : 2e numero de texture du materiau
(1er = relief de detail, 3e = normales, 4e = eclairage ; releve s03a10l)."""
import os
import pickle
import struct

import numpy as np

from mgs4routes import mdn_mesh
from mgs4routes.textures import FORMAT_DXT5

# eclairage precalcule du jeu (couleur des sommets, ~60/255 en moyenne) : mis
# a l'echelle pour une luminosite proche du jeu, plafonne
VERTEX_LIGHT_SCALE, VERTEX_LIGHT_MAX = 3.2, 1.5
BLEND_ALPHA = 0  # transparence par l alpha des sommets DESACTIVEE : cet alpha ne sert pas qu au dosage (murs entiers translucides, essai s01a57l 2026-11-21)
# decor : 0 os (terrain de s03a10l, s01a57l) ou quelques os (accessoires et
# terrain de s01a20l : 1 os pour 176 instances, 5 ou 7 parfois) ; les
# personnages en ont 49 a 115 (releve s01a20l, 2026-11-22)
MAX_DECOR_BONES = 8
MODEL_CACHE_VERSION = 1  # a changer si mdn_mesh.parse change de sortie
ZONE_CACHE_VERSION = 3  # 3 : textures des .txn HD (absentes ou fausses avant) ; idem, ou si la forme des textures compressees change
LIGHT = np.array((0.35, 0.85, 0.40))
LIGHT = LIGHT / np.linalg.norm(LIGHT)


_RELIEF_ONLY = object()  # materiau sans couleur (voir SceneBuilder._color_texture)


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

    def __init__(self, files, library, cache_dir: str | None = None):
        self.files, self.library = files, library
        self.cache_dir = cache_dir  # modeles deja decoupes en triangles (voir _cached_model)
        self._models: dict[int, dict | None] = {}
        self._paths: dict[int, str] = {}
        self._cutouts: dict[tuple, bool] = {}  # voir _bare_cutout
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
                model = self._cached_model(ident, path)
                if model is None:
                    try:
                        data = self.files.read(path)
                        bones = struct.unpack_from(">I", data, 8)[0]
                        model = mdn_mesh.parse(data) if bones <= MAX_DECOR_BONES else None  # personnages ecartes
                        if model is not None:
                            model["bones"] = bones
                    except (OSError, KeyError, ValueError, struct.error):
                        model = None
                    self._store_model(ident, path, model if model is not None else {"skip": True})
                elif model.get("skip"):
                    model = None
            self._models[ident] = model
        return self._models[ident]

    # Cache disque (2026-11-22) : relire et decompresser un .mdn (LZ4 en Python)
    # coutait ~1,2 s par zone ; le modele decoupe est garde, avec la taille du
    # fichier source pour reconnaitre un autre fichier sous le meme nom.
    def _cache_file(self, ident: int, path: str) -> str | None:
        if not self.cache_dir:
            return None
        size = self.files.entries[path][3]
        return os.path.join(self.cache_dir, f"{ident & 0xFFFFFF:06x}_{size}.v{MODEL_CACHE_VERSION}.pkl")

    def _cached_model(self, ident: int, path: str) -> dict | None:
        name = self._cache_file(ident, path)
        try:
            with open(name, "rb") as handle:
                return pickle.load(handle)
        except (OSError, TypeError, ValueError, EOFError, pickle.UnpicklingError, AttributeError):
            return None

    def _store_model(self, ident: int, path: str, model: dict) -> None:
        name = self._cache_file(ident, path)
        if not name:
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(name, "wb") as handle:
                pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)
        except OSError:
            pass

    # Cache d'une zone (2026-11-22) : tous ses modeles et toutes ses textures
    # (compressees) dans UN fichier. Ouvrir un petit fichier coute ~3 ms sur la
    # machine de l'utilisateur (antivirus) : 579 fichiers = 1,6 s, un seul = rien.
    def _zone_file(self, zone: str) -> str | None:
        if not (self.cache_dir and zone):
            return None
        return os.path.join(os.path.dirname(self.cache_dir), "zones", f"{zone}.v{ZONE_CACHE_VERSION}.pkl")

    def _load_zone(self, zone: str) -> dict:
        try:
            with open(self._zone_file(zone), "rb") as handle:
                pack = pickle.load(handle)
            return pack if isinstance(pack, dict) else {}
        except (OSError, TypeError, ValueError, EOFError, pickle.UnpicklingError, AttributeError):
            return {}

    def _save_zone(self, zone: str, pack: dict) -> None:
        name = self._zone_file(zone)
        if not name:
            return
        try:
            os.makedirs(os.path.dirname(name), exist_ok=True)
            with open(name + ".tmp", "wb") as handle:
                pickle.dump(pack, handle, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(name + ".tmp", name)
        except OSError:
            pass

    def _color_texture(self, model: dict, material: int):
        """Texture de couleur d'un materiau : l'emplacement habituel
        (diffuse_texture), sinon le premier autre qui n'est pas une carte de
        relief ; _RELIEF_ONLY si le materiau n'a que du relief (couche de detail,
        rien a dessiner)."""
        texture = diffuse_texture(model, material)
        if texture is None or not self.library or not self.library.is_normal_map(texture):
            return texture
        for slot in model["materials"][material]:
            if 0 <= slot < len(model["textures"]):
                other = model["textures"][slot]
                if self.library.compressed(other) is not None and not self.library.is_normal_map(other):
                    return other
        return _RELIEF_ONLY

    def _bare_cutout(self, ident: int, model: dict, material: int, texture: int | None) -> bool:
        """Materiau detoure (une texture DXT5 annoncee) dont aucune texture n'a de
        pixel lisible : sans elles, un feuillage n'est qu'une croix de panneaux
        opaques (plantes 001fb189 et 007b694d des slots ww, s02a20l,
        2026-11-22) ; mieux vaut ne rien dessiner."""
        if texture is None or not self.library or self.library.compressed(texture) is not None:
            return False
        key = (ident, material)
        if key not in self._cutouts:
            slots = model["materials"][material] if 0 <= material < len(model["materials"]) else []
            used = {model["textures"][s] for s in slots if 0 <= s < len(model["textures"])}
            bare = all(self.library.compressed(t) is None for t in used)
            if bare:
                path = self._paths.get(ident & 0xFFFFFF, "")
                formats = self.library.declared_formats(path[:-4] + ".txn") if path.endswith(".mdn") else {}
                formats = {k & 0xFFFFFF: v for k, v in formats.items()}
                bare = any(formats.get(t & 0xFFFFFF) == FORMAT_DXT5 for t in used)
            self._cutouts[key] = bare
        return self._cutouts[key]

    def build(self, instances, cut: float | None = None, progress=None, zone: str | None = None) -> list[tuple]:
        """[(tableau (N, 9) x, y, z, u, v, r, g, b, a en coordonnees de la vue, texture, melange)]
        par texture et par sorte (opaque / couche de melange). texture : DXT
        compresse (TextureLibrary.compressed), None = gris.
        cut : hauteur (mm) au-dessus de laquelle les triangles sont retires ;
        zone : cache disque de la zone (modeles et textures en un fichier)."""
        pack = self._load_zone(zone) if zone else {}
        for ident, model in pack.get("models", {}).items():
            self._models.setdefault(ident, model)
        if self.library:
            self.library.preload(pack.get("textures", {}))
        used_models: dict = {}
        groups: dict[tuple, list[np.ndarray]] = {}
        for k, (ident, matrix, *rest) in enumerate(instances):
            if progress:
                progress(k, len(instances))
            model = self.model(ident)
            used_models[ident] = model
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
                texture = self._color_texture(model, material)
                if texture is _RELIEF_ONLY or self._bare_cutout(ident, model, material, texture):
                    continue
                groups.setdefault((texture, blended), []).append(rows.reshape(-1, 9))
        out, used_textures = [], {}
        for k, ((texture, blended), parts) in enumerate(groups.items()):
            if progress:
                progress(k, len(groups))
            tex = self.library.compressed(texture) if texture is not None and self.library else None
            if texture is not None:
                used_textures[texture] = tex
            out.append((np.concatenate(parts), tex, blended))
        out.sort(key=lambda b: b[2])  # couches de melange en dernier
        if zone and (set(used_models) - set(pack.get("models", {})) or set(used_textures) - set(pack.get("textures", {}))):
            models = dict(pack.get("models", {}))
            models.update(used_models)
            textures = dict(pack.get("textures", {}))
            textures.update(used_textures)
            self._save_zone(zone, {"models": models, "textures": textures})
        return out
