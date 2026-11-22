"""Textures HD du portage PC pour la vue 3D (releve du 2026-11-21, s03a10l).

- Un .txn de zone (archives de zone) liste des images (table de 16 octets :
  largeur, hauteur, format, ?, debut, fin) et des textures (0x30 octets :
  +4 identifiant, +8 cle du .txn, +0x14 position de son image dans la table).
  Format 9 = DXT1, 11 = DXT5.
- Les pixels sont dans common/textures/PC_TXN_UP/paks/TextureData.pak (VPAK,
  chaque fichier en un seul bloc LZ4) :
  .../PC_TXN_UP_Cache/Common/master/<stage>/<zone>/cache/<cle>_<n>.data, n =
  numero de l'entree de texture dans le .txn de cette zone. Blocs DXT bruts
  (meme format), image agrandie 2 fois (parfois 1), tous les niveaux.
- Une zone charge aussi des .txn d'autres zones (data.cnf) : une texture se
  cherche dans ces .txn, chacun avec les pixels du dossier de sa zone.
  Le .txn du dossier PC_TXN_UP (txn_up.*.pak) n'a PAS la meme numerotation.
"""
import json
import os
import re
import struct
import time

import numpy as np

from mgs4routes.vpak import lz4_decompress

try:  # module compile, beaucoup plus rapide, s'il est installe (facultatif)
    from lz4.block import decompress as _fast_lz4
except ImportError:
    _fast_lz4 = None

PIXEL_PACK = ("common", "textures", "PC_TXN_UP", "paks", "TextureData.pak")
INDEX_MAX_SIZE = 0x2000000
DATA_ENTRY = re.compile(rb"BolaTextures/textures-compression-output/PC_TXN_UP_Cache/Common/(master|lossless)/"
                        rb"([A-Za-z0-9_./-]+?)/cache/([0-9a-f]{8})_(\d+)\.data\0")
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
    """Index des fichiers de pixels HD : (dossier de zone, cle, numero) -> entree."""

    def __init__(self, root: str):
        self.path = os.path.join(root, *PIXEL_PACK)
        self.entries: dict[tuple[str, int, int], tuple] = {}
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
            folder = match.group(2).decode().split("/")[-1]
            key = (folder, int(match.group(3), 16), int(match.group(4)))
            if match.group(1) == b"master" or key not in self.entries:  # master d'abord
                self.entries[key] = (position, stored, length, block)

    def read(self, entry) -> bytes:
        position, stored, length, block = entry
        with open(self.path, "rb") as handle:
            handle.seek(position)
            raw = handle.read(stored or length)
        if not stored:
            return raw
        if block == 0:
            if _fast_lz4 is not None:
                return _fast_lz4(raw, uncompressed_size=length)
            return lz4_decompress(raw, length)
        raise ValueError("fichier de pixels en plusieurs blocs : non prevu")


GLOBAL_INDEX_VERSION = 1


class TextureLibrary:
    """Textures d'une zone, par identifiant (celui des materiaux des .mdn) :
    d'abord les .txn charges par la zone, puis n'importe quel .txn du jeu dont
    les pixels HD existent (textures partagees rangees une seule fois)."""

    def __init__(self, files, root: str, cache_path: str | None = None):
        self.files = files
        self.pixels = PixelPack(root)
        self.cache_path = cache_path
        self._zone = None
        self._where: dict[int, tuple] = {}
        self._global: dict[int, tuple] | None = None
        self._images: dict[int, np.ndarray | None] = {}

    def _txn_entries(self, path: str, key: int):
        """(identifiant, entree de pixels, largeur, hauteur, format) d'un .txn."""
        folder = path.split("/")[-3]
        data = self.files.read(path)
        _z, _u, images, image_table, textures, texture_table = struct.unpack_from(">6I", data, 0)
        for n in range(textures):
            pixels = self.pixels.entries.get((folder, key, n))
            if pixels is None:
                continue
            ident = struct.unpack_from(">I", data, texture_table + 0x30 * n + 4)[0]
            image = (struct.unpack_from(">I", data, texture_table + 0x30 * n + 0x14)[0] - image_table) // 16
            if not 0 <= image < images:
                continue
            w, h, fmt = struct.unpack_from(">HHH", data, image_table + 16 * image)
            if fmt in (FORMAT_DXT1, FORMAT_DXT5):
                yield ident, pixels, w, h, fmt

    def global_index(self, progress=None) -> dict[int, tuple]:
        """Identifiant -> (pixels, w, h, format) sur tous les .txn du jeu,
        calcule une fois (une minute environ) et garde dans un fichier."""
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
        paths = sorted(p for p in self.files.entries if p.endswith(".txn"))
        for i, path in enumerate(paths):
            if progress:
                progress(i, len(paths))
            time.sleep(0.001)  # calcul dans un fil : laisser la main a l'interface
            try:
                key = int(path.split("/")[-1][:8], 16)
                for ident, pixels, w, h, fmt in self._txn_entries(path, key):
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
        self._zone, self._where, self._images = zone, {}, {}
        try:
            resources = self.files.zone_resources(zone)
        except StopIteration:
            return
        for key, path in resources:
            if key >> 24 != TXN_TYPE:
                continue
            try:
                for ident, pixels, w, h, fmt in self._txn_entries(path, key & 0xFFFFFF):
                    self._where.setdefault(ident, (pixels, w, h, fmt))
            except (struct.error, OSError, KeyError, ValueError):
                continue

    def known(self) -> int:
        return len(self._where)

    def image(self, ident: int) -> np.ndarray | None:
        """RGBA (h, w, 4) ou None si la texture n'est pas trouvee."""
        if ident in self._images:
            return self._images[ident]
        out = self._cached(ident)
        if out is not None:
            self._images[ident] = out
            return out
        found = self._where.get(ident) or self.global_index().get(ident)
        if found:
            pixels, w, h, fmt = found
            try:
                data = self.pixels.read(pixels)
                block = 8 if fmt == FORMAT_DXT1 else 16
                scale = next((k for k in (1, 2, 4, 8) if dxt_size(w * k, h * k, block) == len(data)), None)
                if scale:
                    # niveau de detail suffisant : on saute les niveaux plus grands
                    w, h, offset = w * scale, h * scale, 0
                    while max(w, h) > MAX_SIDE:
                        offset += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block
                        w, h = max(1, w // 2), max(1, h // 2)
                    level = data[offset:offset + max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block]
                    out = decode_dxt(level, w, h, fmt)
                    self._store(ident, w, h, fmt, level)
            except (OSError, ValueError):
                out = None
        self._images[ident] = out
        return out

    # niveau de detail garde sur disque, encore en DXT (32 a 64 Ko par texture) :
    # la decompression LZ4 en Python est l'etape lente (environ 20 s par zone)
    def _cache_file(self, ident: int) -> str | None:
        if not self.cache_path:
            return None
        return os.path.join(os.path.dirname(self.cache_path), f"textures_{MAX_SIDE}", f"{ident & 0xFFFFFF:06x}.dxt")

    def _cached(self, ident: int) -> np.ndarray | None:
        path = self._cache_file(ident)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            w, h, fmt = struct.unpack_from("<HHH", data, 0)
            return decode_dxt(data[6:], w, h, fmt)
        except (OSError, TypeError, ValueError, struct.error):
            return None

    def _store(self, ident: int, w: int, h: int, fmt: int, level: bytes) -> None:
        path = self._cache_file(ident)
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(struct.pack("<HHH", w, h, fmt) + level)
        except OSError:
            pass
