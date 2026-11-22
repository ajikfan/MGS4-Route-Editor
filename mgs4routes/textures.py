"""Textures HD du portage PC pour la vue 3D (releve du 2026-11-21, s03a10l ;
correspondance exacte trouvee le 2026-11-22, s02a20l).

- Les pixels sont dans common/textures/PC_TXN_UP/paks/TextureData.pak (VPAK,
  chaque fichier en un seul bloc LZ4) : blocs DXT bruts, tous les niveaux.
- La correspondance est dans les .txn HD des archives txn_up.*.pak :
  common/textures/PC_TXN_UP/<stage>/<zone>/cache/<cle>.txn (aussi slot/...).
  Meme structure qu'un .txn de zone : images (16 octets : largeur, hauteur,
  format, ?, debut, fin ; dimensions et format HD reels, taille = fin - debut)
  et textures (0x30 octets : +4 identifiant, +0x14 position de son image), puis
  un chemin de 0x100 octets par texture : le .data exact, souvent range dans
  une autre zone ou un slot (textures partagees stockees une fois). Chemin
  vide = pas de version HD. Format 9 = DXT1, 11 = DXT5.
- Pieges : une meme cle de .txn n'a pas le meme contenu d'une zone a l'autre
  (0028431b_314 differe entre s01a00l et s02a10l) ; un meme identifiant peut
  changer de taille selon la zone (346 cas) : la zone passe d'abord.
"""
import json
import os
import re
import struct
import threading
import time

import numpy as np

from mgs4routes.vpak import lz4_decompress

try:  # module compile, beaucoup plus rapide, s'il est installe (facultatif)
    from lz4.block import decompress as _fast_lz4
except ImportError:
    _fast_lz4 = None

PIXEL_PACK = ("common", "textures", "PC_TXN_UP", "paks", "TextureData.pak")
HD_TABLES = (("common", "textures", "PC_TXN_UP", "txn_up.1.pak"), ("common", "textures", "PC_TXN_UP", "txn_up.2.pak"))
HD_TXN = re.compile(r"common/textures/PC_TXN_UP/(?:(?:.*/)?([^/]+)/(?:cache|resident)|(additions))/([0-9a-f]{8})\.txn$")
INDEX_MAX_SIZE = 0x2000000
DATA_ENTRY = re.compile(rb"(BolaTextures/[A-Za-z0-9_./-]+\.data)\0")
TXN_TYPE = 0x03
FORMAT_DXT1, FORMAT_DXT5 = 9, 11
MAX_SIDE = 256  # niveau de detail decode (memoire de la carte graphique)


def dxt_size(w: int, h: int, block: int) -> int:
    """Taille d'une image DXT avec tous ses niveaux (jusqu'a 1 x 1)."""
    s = 0
    while True:
        s += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block
        if w == 1 and h == 1:
            return s
        w, h = max(1, w // 2), max(1, h // 2)


def _rgb565(c: np.ndarray) -> np.ndarray:
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], -1).astype(np.int32)


def decode_dxt(data: bytes, w: int, h: int, fmt: int) -> np.ndarray:
    """Premier niveau d'une image DXT1 / DXT5 -> RGBA (h, w, 4) uint8."""
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    size = 8 if fmt == FORMAT_DXT1 else 16
    blocks = np.frombuffer(data, dtype=np.uint8, count=bw * bh * size).reshape(bh * bw, size)
    col = blocks[:, -8:]
    c0 = col[:, 0].astype(np.int32) | (col[:, 1].astype(np.int32) << 8)
    c1 = col[:, 2].astype(np.int32) | (col[:, 3].astype(np.int32) << 8)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    four = ((c0 > c1) | (fmt != FORMAT_DXT1))[:, None]
    p2 = np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four, (p0 + 2 * p1) // 3, 0)
    palette = np.stack([p0, p1, p2, p3], 1)
    bits = col[:, 4:8].astype(np.uint32)
    index = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    select = ((index[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3).astype(np.int64)
    rgb = np.take_along_axis(palette, select[:, :, None].repeat(3, 2), 1)
    if fmt == FORMAT_DXT1:
        alpha = np.where(~four & (select == 3), 0, 255)
    else:
        a = blocks[:, :8]
        a0, a1 = a[:, 0].astype(np.int32), a[:, 1].astype(np.int32)
        packed = np.zeros(len(a), np.uint64)
        for k in range(6):
            packed |= a[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
        aselect = ((packed[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
        levels = np.zeros((len(a), 8), np.int32)
        levels[:, 0], levels[:, 1] = a0, a1
        for k in range(2, 8):
            smooth = ((8 - k) * a0 + (k - 1) * a1) // 7
            short = ((6 - k) * a0 + (k - 1) * a1) // 5 if k < 6 else np.full_like(a0, 0 if k == 6 else 255)
            levels[:, k] = np.where(a0 > a1, smooth, short)
        alpha = np.take_along_axis(levels, aselect, 1)
    px = np.concatenate([rgb, alpha[:, :, None]], 2).reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4)
    return px.reshape(bh * 4, bw * 4, 4)[:h, :w].astype(np.uint8)


class PixelPack:
    """Index des fichiers de pixels HD : chemin complet -> entree."""

    def __init__(self, root: str):
        self.path = os.path.join(root, *PIXEL_PACK)
        self._handle = None
        self._lock = threading.Lock()
        self.entries: dict[str, tuple] = {}
        try:
            with open(self.path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - INDEX_MAX_SIZE))
                tail = handle.read()
        except OSError:
            return
        for match in DATA_ENTRY.finditer(tail):
            start = match.end() - 1 + 8  # le nom finit par un zero, puis 8 octets
            try:
                length, stored, position, block, count = struct.unpack_from("<QQQII", tail, start)
            except struct.error:
                continue
            if not length or position + (stored or length) > size:
                continue
            self.entries.setdefault(match.group(1).decode(), (position, stored, length, block))

    def read(self, entry) -> bytes:
        position, stored, length, block = entry
        with self._lock:  # archive gardee ouverte (voir StageFiles.read)
            if self._handle is None:
                self._handle = open(self.path, "rb")
            self._handle.seek(position)
            raw = self._handle.read(stored or length)
        if not stored:
            return raw
        if block == 0:
            if _fast_lz4 is not None:
                return _fast_lz4(raw, uncompressed_size=length)
            return lz4_decompress(raw, length)
        raise ValueError("fichier de pixels en plusieurs blocs : non prevu")


GLOBAL_INDEX_VERSION = 4  # 4 : correspondance des .txn HD (txn_up), 2026-11-22


class TextureLibrary:
    """Textures d'une zone, par identifiant (celui des materiaux des .mdn) :
    d'abord les .txn HD de la zone et des .txn qu'elle charge, puis n'importe
    quel .txn HD du jeu."""

    def __init__(self, files, root: str, cache_path: str | None = None):
        self.files = files
        self.pixels = PixelPack(root)
        self.tables = type(files)(root, HD_TABLES)  # .txn HD (meme lecteur VPAK)
        self.hd_txn: dict[tuple[str, int], str] = {}  # (dossier, cle) -> chemin
        for path in self.tables.entries:
            match = HD_TXN.match(path)
            if match:
                folder = match.group(1) or match.group(2)
                self.hd_txn.setdefault((folder, int(match.group(3), 16)), path)
        self.cache_path = cache_path
        self._zone = None
        self._where: dict[int, tuple] = {}
        self._global: dict[int, tuple] | None = None
        self._images: dict[int, np.ndarray | None] = {}
        self._compressed: dict[int, dict | None] = {}
        self._normal: dict[int, bool] = {}  # voir is_normal_map

    def _txn_entries(self, path: str):
        """(identifiant, entree de pixels, largeur, hauteur, format) d'un .txn HD."""
        data = self.tables.read(path)
        _z, _u, images, image_table, textures, texture_table = struct.unpack_from(">6I", data, 0)
        names = texture_table + 0x30 * textures
        for n in range(textures):
            name = data[names + 0x100 * n:names + 0x100 * (n + 1)].split(b"\0", 1)[0]
            pixels = self.pixels.entries.get(name.decode("ascii", "replace")) if name else None
            if pixels is None:
                continue
            ident = struct.unpack_from(">I", data, texture_table + 0x30 * n + 4)[0]
            image = (struct.unpack_from(">I", data, texture_table + 0x30 * n + 0x14)[0] - image_table) // 16
            if not 0 <= image < images:
                continue
            w, h, fmt, _levels, start, end = struct.unpack_from(">HHHHII", data, image_table + 16 * image)
            if fmt in (FORMAT_DXT1, FORMAT_DXT5) and end - start == pixels[2]:
                yield ident, pixels, w, h, fmt

    def global_index(self, progress=None) -> dict[int, tuple]:
        """Identifiant -> (pixels, w, h, format) sur tous les .txn HD du jeu,
        calcule une fois (quelques secondes) et garde dans un fichier."""
        if self._global is not None:
            return self._global
        self._global = {}
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("version") == GLOBAL_INDEX_VERSION:
                self._global = {int(k): (tuple(v[0]), *v[1:]) for k, v in data["textures"].items()}
                return self._global
        except (OSError, ValueError, KeyError, TypeError):
            pass
        paths = sorted(self.hd_txn.values())
        for i, path in enumerate(paths):
            if progress:
                progress(i, len(paths))
            if i % 50 == 0:
                time.sleep(0.001)  # calcul dans un fil : laisser la main a l'interface
            try:
                for ident, pixels, w, h, fmt in self._txn_entries(path):
                    self._global.setdefault(ident, (pixels, w, h, fmt))
            except (struct.error, OSError, KeyError, ValueError):
                continue
        if self.cache_path:
            try:
                os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                with open(self.cache_path, "w", encoding="utf-8") as handle:
                    json.dump({"version": GLOBAL_INDEX_VERSION,
                               "textures": {str(k): [list(v[0]), *v[1:]] for k, v in self._global.items()}}, handle)
            except OSError:
                pass
        return self._global

    def set_zone(self, zone: str) -> None:
        if zone == self._zone:
            return
        self._zone, self._where, self._images, self._compressed, self._normal = zone, {}, {}, {}, {}
        # .txn HD de la zone d'abord, puis ceux des .txn qu'elle charge
        paths = [path for (folder, _key), path in sorted(self.hd_txn.items()) if folder == zone]
        try:
            resources = self.files.zone_resources(zone)
        except StopIteration:
            resources = []
        for key, path in resources:
            if key >> 24 != TXN_TYPE:
                continue
            key &= 0xFFFFFF
            hd = self.hd_txn.get((zone, key)) or self.hd_txn.get((path.split("/")[-3], key))
            if hd and hd not in paths:
                paths.append(hd)
        for path in paths:
            try:
                for ident, pixels, w, h, fmt in self._txn_entries(path):
                    self._where.setdefault(ident, (pixels, w, h, fmt))
            except (struct.error, OSError, KeyError, ValueError):
                continue

    def known(self) -> int:
        return len(self._where)

    def is_normal_map(self, ident: int) -> bool:
        """Carte de relief en DXT5 (rouge et bleu constants vers 131, relief dans
        le vert et l'alpha) : prise pour une couleur, elle peignait le terrain en
        blanc avec des bords en pointilles (couches de detail, s02a20l,
        2026-11-22). Jugee sur un petit niveau de detail."""
        if ident in self._normal:
            return self._normal[ident]
        tex = self.compressed(ident)
        out = False
        if tex is not None and tex["format"] == FORMAT_DXT5:
            w, h, level = tex["w"], tex["h"], 0
            while max(w, h) > 32 and level + 1 < len(tex["levels"]):
                w, h, level = max(1, w // 2), max(1, h // 2), level + 1
            try:
                rgb = decode_dxt(tex["levels"][level], w, h, FORMAT_DXT5)[..., :3].reshape(-1, 3).astype(np.float32)
                r, b = rgb[:, 0], rgb[:, 2]
                out = bool(r.std() < 4 and b.std() < 4 and abs(r.mean() - b.mean()) < 4 and 110 < r.mean() < 150)
            except ValueError:
                out = False
        self._normal[ident] = out
        return out

    def declared_formats(self, path: str) -> dict[int, int]:
        """Identifiant -> format annonce par un .txn des archives de zone (en-tete
        seul, sans pixels) : sert a reconnaitre une texture detouree (DXT5) dont
        le jeu ne fournit aucun pixel lisible (plantes des slots ww, s02a20l)."""
        out = {}
        try:
            data = self.files.read(path)
            _z, _u, images, image_table, textures, texture_table = struct.unpack_from(">6I", data, 0)
            for n in range(textures):
                ident = struct.unpack_from(">I", data, texture_table + 0x30 * n + 4)[0]
                image = (struct.unpack_from(">I", data, texture_table + 0x30 * n + 0x14)[0] - image_table) // 16
                if 0 <= image < images:
                    out[ident] = struct.unpack_from(">H", data, image_table + 16 * image + 4)[0]
        except (struct.error, OSError, KeyError, ValueError):
            pass
        return out

    # Texture compressee : {"w", "h", "format", "levels": [octets du niveau <= MAX_SIDE,
    # puis chaque niveau plus petit jusqu'a 1 x 1]}. La carte graphique lit le DXT
    # elle-meme (View3D) : plus de decodage par le processeur (1,2 s par zone).
    def compressed(self, ident: int) -> dict | None:
        if ident in self._compressed:
            return self._compressed[ident]
        tex = self._cached(ident)
        if tex is None:
            found = self._where.get(ident) or self.global_index().get(ident)
            if found:
                pixels, w, h, fmt = found
                try:
                    tex = self._levels(self.pixels.read(pixels), w, h, fmt)
                except (OSError, ValueError):
                    tex = None
                if tex is not None:
                    self._store(ident, tex)
        self._compressed[ident] = tex
        return tex

    @staticmethod
    def _levels(data: bytes, w: int, h: int, fmt: int) -> dict | None:
        """Niveaux du fichier HD a partir du premier de cote <= MAX_SIDE."""
        block = 8 if fmt == FORMAT_DXT1 else 16
        scale = next((k for k in (1, 2, 4, 8) if dxt_size(w * k, h * k, block) == len(data)), None)
        if not scale:
            return None
        w, h, offset = w * scale, h * scale, 0
        size = lambda a, b: max(1, (a + 3) // 4) * max(1, (b + 3) // 4) * block
        while max(w, h) > MAX_SIDE:
            offset += size(w, h)
            w, h = max(1, w // 2), max(1, h // 2)
        tex = {"w": w, "h": h, "format": fmt, "levels": []}
        while True:
            tex["levels"].append(bytes(data[offset:offset + size(w, h)]))
            offset += size(w, h)
            if w == 1 and h == 1:
                return tex
            w, h = max(1, w // 2), max(1, h // 2)

    def preload(self, textures: dict) -> None:
        """Textures deja connues (cache d'une zone) : aucune lecture de fichier."""
        for ident, tex in textures.items():
            self._compressed.setdefault(ident, tex)

    def image(self, ident: int) -> np.ndarray | None:
        """RGBA (h, w, 4) du premier niveau, ou None (repli si la carte graphique
        refuse le DXT)."""
        if ident in self._images:
            return self._images[ident]
        tex = self.compressed(ident)
        out = None
        if tex is not None:
            try:
                out = decode_dxt(tex["levels"][0], tex["w"], tex["h"], tex["format"])
            except ValueError:
                out = None
        self._images[ident] = out
        return out

    # niveaux gardes sur disque, encore en DXT : la decompression LZ4 en Python
    # est l'etape lente (environ 20 s a la premiere ouverture d'une zone)
    def _cache_file(self, ident: int) -> str | None:
        if not self.cache_path:
            return None
        return os.path.join(os.path.dirname(self.cache_path), f"textures_{MAX_SIDE}_v3", f"{ident & 0xFFFFFF:06x}.dxt")

    def _cached(self, ident: int) -> dict | None:
        path = self._cache_file(ident)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            w, h, fmt, count = struct.unpack_from("<HHHH", data, 0)
            block = 8 if fmt == FORMAT_DXT1 else 16
            tex, offset, a, b = {"w": w, "h": h, "format": fmt, "levels": []}, 8, w, h
            for _ in range(count):
                n = max(1, (a + 3) // 4) * max(1, (b + 3) // 4) * block
                tex["levels"].append(data[offset:offset + n])
                offset += n
                a, b = max(1, a // 2), max(1, b // 2)
            return tex if offset == len(data) else None
        except (OSError, TypeError, ValueError, struct.error):
            return None

    def _store(self, ident: int, tex: dict) -> None:
        path = self._cache_file(ident)
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(struct.pack("<HHHH", tex["w"], tex["h"], tex["format"], len(tex["levels"]))
                             + b"".join(tex["levels"]))
        except OSError:
            pass
