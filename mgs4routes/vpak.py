"""Lecture des archives du jeu (VPAK v3) sans passer par le jeu.

Index en fin de fichier : chemin en clair, 8 octets nuls, puis taille, taille
compressee (0 = stocke tel quel), position (u64), taille de bloc et nombre de
blocs (u32), 8 octets, fin compressee de chaque bloc sauf le dernier
(u64). Donnees en blocs LZ4 bruts de 1 Mo. Chaque zone liste
ses ressources dans data.cnf, y compris celles d'autres zones
(/../s01a00l/cache/00123456.mdn, /../00000001/cache/...).

Copie du lecteur de MGS4-Trainer (mgs4trainer/core/vpak.py), sans Qt."""
import os
import re
import struct
import threading

try:  # module compile, beaucoup plus rapide, s'il est installe (facultatif)
    from lz4.block import decompress as _fast_lz4
except ImportError:
    _fast_lz4 = None

STAGE_ARCHIVES = (
    ("common", "stage", "stage_data_compressed.1.pak"),
    ("common", "stage", "stage_data_compressed.2.pak"),
    ("ww", "stage", "stage_data_compressed.1.pak"),
    ("ww", "stage", "stage_data_compressed.2.pak"),
)
INDEX_MAX_SIZE = 0x1000000  # l'index tient dans les derniers 16 Mo de chaque archive
INDEX_ENTRY = re.compile(rb"((?:ww|common)/[a-zA-Z0-9_./-]+\.[a-z0-9]{2,5})\0")
RESOURCE_ENTRY = re.compile(r"(?:/\.\./)*((?:[a-z0-9_]+/)?cache/([0-9a-f]{8})\.([a-z0-9]+))$")

# Extension de fichier -> type de ressource (octet haut de la cle).
RESOURCE_TYPES = {
    "mdn": 0x0D, "txn": 0x03, "cnp": 0x11, "rdv": 0x22, "cpef": 0x2B,
    "la2": 0x5F, "mtar": 0x08, "mtsq": 0x09, "eqpp": 0x19,
}


def _load_native():
    """Decodeur C livre avec le code (lz4dec.dll, source native/lz4dec.c)."""
    try:
        import ctypes
        dll = ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lz4dec.dll"))
        function = dll.lz4_block_decode
        function.argtypes = (ctypes.c_char_p, ctypes.c_longlong, ctypes.c_void_p, ctypes.c_longlong)
        function.restype = ctypes.c_longlong
        return function, ctypes
    except (OSError, AttributeError, ImportError):
        return None


_NATIVE = _load_native()


def lz4_decompress(source: bytes, size: int) -> bytes:
    if _NATIVE is not None:
        function, ctypes = _NATIVE
        source = bytes(source)
        out = ctypes.create_string_buffer(size)
        if function(source, len(source), out, size) != size:
            raise ValueError("bloc LZ4 invalide")
        return out.raw
    # Version Python (2026-11-22) : longueur tenue a part (len() coutait 3,6 s
    # par zone) et recopie des motifs qui se chevauchent par blocs entiers.
    out = bytearray()
    done = 0
    position = 0
    end = len(source)
    while position < end:
        token = source[position]
        position += 1
        literals = token >> 4
        if literals:
            if literals == 15:
                while True:
                    extra = source[position]
                    position += 1
                    literals += extra
                    if extra != 255:
                        break
            out += source[position:position + literals]
            position += literals
            done += literals
        if position >= end or done >= size:
            break
        distance = source[position] | source[position + 1] << 8
        position += 2
        length = (token & 15) + 4
        if length == 19:
            while True:
                extra = source[position]
                position += 1
                length += extra
                if extra != 255:
                    break
        if not distance or distance > done:
            raise ValueError("bloc LZ4 invalide")
        start = done - distance
        if distance >= length:
            out += out[start:start + length]
        else:
            pattern = out[start:done]
            out += (pattern * (length // distance + 1))[:length]
        done += length
    if done != size:
        raise ValueError("taille LZ4 inattendue")
    return bytes(out)


class StageFiles:
    """Fichiers de zone des archives. root : dossier de mgs4.exe."""

    def __init__(self, root: str, archives=STAGE_ARCHIVES):
        self.root = root
        # archives gardees ouvertes : les rouvrir a chaque lecture coutait 3 ms
        # (2,2 s pour une zone en 3D texturee, 2026-11-22) ; verrou : deux fils y lisent
        self._handles: dict[str, object] = {}
        self._lock = threading.Lock()
        self.entries = {}  # chemin complet -> (archive, position, compressee, taille)
        self.suffixes = {}  # zone/cache/fichier -> chemin complet
        for parts in archives:
            try:
                self._read_index(os.path.join(root, *parts))
            except OSError:
                if archives is STAGE_ARCHIVES:
                    raise

    def _read_index(self, archive: str) -> None:
        with open(archive, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - INDEX_MAX_SIZE))
            tail = handle.read()
        for match in INDEX_ENTRY.finditer(tail):
            name = match.group(1)
            start = match.start() + len(name) + 8
            try:
                length, stored, position, block, count = struct.unpack_from("<QQQII", tail, start)
                sizes = struct.unpack_from(f"<{max(count - 1, 0)}Q", tail, start + 0x28) if count < 0x1000 else ()
            except struct.error:
                continue
            if not length or position + (stored or length) > size or not 0 < count < 0x1000:
                continue
            path = name.decode()
            if path not in self.entries:
                self.entries[path] = (archive, position, stored, length, block, sizes)
                self.suffixes.setdefault("/".join(path.split("/")[-3:]), path)

    def read(self, path: str) -> bytes:
        archive, position, stored, length, block, sizes = self.entries[path]
        with self._lock:
            handle = self._handles.get(archive)
            if handle is None:
                handle = self._handles[archive] = open(archive, "rb")
            handle.seek(position)
            data = handle.read(stored or length)
        if not stored:
            return data
        out = []
        offset = 0
        remaining = length
        for end in (*sizes, stored):
            expected = min(block, remaining) if block else remaining  # 0 = un seul bloc
            chunk = data[offset:end]
            if end - offset == expected:
                out.append(chunk)
            elif _fast_lz4 is not None:
                out.append(_fast_lz4(chunk, uncompressed_size=expected))
            else:
                out.append(lz4_decompress(chunk, expected))
            offset = end
            remaining -= expected
        return b"".join(out)

    def zone_resources(self, zone: str) -> list[tuple[int, str]]:
        """(cle, chemin) des ressources listees par le data.cnf de la zone,
        dans leur ordre, pour les types connus."""
        suffix = f"/{zone}/data.cnf"
        config = next(path for path in self.entries if path.endswith(suffix))
        resources = []
        section = ""
        for line in self.read(config).decode("latin-1").splitlines():
            line = line.strip()
            if line.startswith("."):
                section = line
                continue
            match = RESOURCE_ENTRY.search(line) if section == ".cache" else None
            if not match or match.group(3) not in RESOURCE_TYPES:
                continue
            key = RESOURCE_TYPES[match.group(3)] << 24 | int(match.group(2), 16)
            name = match.group(1)
            if name.startswith("cache/"):
                name = f"{zone}/{name}"
            path = self.suffixes.get(name)
            if path:
                resources.append((key, path))
        return resources
