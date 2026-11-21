"""Lien avec le jeu en cours (facultatif) : zone, position de Snake,
soldats et routes suivies, teleportation de Snake, edition en direct
des points des routes chargees. Lecture seule, sauf la position de Snake
et les points de route modifies a la demande.

Adresses relevees avec MGS4-Trainer, par version de l'exe (horodatage de
l'en-tete PE). Version inconnue : rien n'est lu."""
import re
import struct

from mgs4routes.winproc import ProcessHandle, find_module_base, find_pid

PROCESS_NAME = "mgs4.exe"
# horodatage PE -> (pointeur vers linkvarbuf, index du joueur, table des
# joueurs, dictionnaire des ressources : 16 arbres, noeud = gauche +0,
# droite +8, cle +0x10 (type << 24 | identifiant, bit 31 = resident),
# gestionnaire d'alerte (liste des soldats de la zone : nombre +0x30,
# table +0x38 d'entrees de 0x10, acteur en +8), table des routes chargees
# (nombre +0, entrees de 0x10 en +8 : identifiant, ?, nombre de points,
# decalage depuis la base suivante ; points de 0x30 comme dans le .geom,
# petit-boutiens), base des decalages des routes)
ADDRESSES = {
    0x6AE07392: (0x157A8E8, 0x238012D0, 0x238012E0, 0x2367B9D0, 0x23857630, 0x1C9DB88, 0x1BD54B0),  # 1.4.2
}
REFERENCE = (0x1C28B28, 0x23EB5150, 0x23EB5160, 0x23D2F6D0, 0x23F0B5B0, 0x234F488, 0x22864B0)  # 1.4.1
NAV_TYPE = 0x15  # type de ressource des .nv2
STAGE_CODE_OFFSET, PROGRESS_OFFSET = 0x34, 0x54
PLAYER_ENTRY_SIZE, TRANSFORM_POSITION = 0x80, 0x10
# soldat : position +0x2D0 ; suivi de route +0x42D8 -> entree de la route
# suivie (+0x80, identifiant en +0), point en cours (+0x60, mot)
SOLDIER_POSITION, SOLDIER_FOLLOWER = 0x2D0, 0x42D8
FOLLOWER_ROUTE, FOLLOWER_POINT = 0x80, 0x60
ROUTE_POINT_SIZE = 0x30  # position (3 flottants) en +0
STAGE_CODE = re.compile(r"s\d\da\d\d\w")


def _timestamp(proc: ProcessHandle, base: int) -> int:
    header = struct.unpack("<I", proc.read_bytes(base + 0x3C, 4))[0]
    return struct.unpack("<I", proc.read_bytes(base + header + 8, 4))[0]


class GameLink:
    def __init__(self):
        self.proc = None
        self.base = None
        self.addresses = None

    def _attach(self) -> bool:
        self.close()
        pid = find_pid(PROCESS_NAME)
        base = find_module_base(pid, PROCESS_NAME) if pid else None
        if not base:
            return False
        try:
            self.proc = ProcessHandle(pid)
            self.base = base
            self.addresses = ADDRESSES.get(_timestamp(self.proc, base), REFERENCE)
        except OSError:
            self.close()
            return False
        return True

    def close(self):
        if self.proc:
            self.proc.close()
        self.proc = self.base = self.addresses = None

    def _q(self, address: int) -> int:
        return struct.unpack("<Q", self.proc.read_bytes(address, 8))[0]

    def _transform(self) -> int:
        index_rva, table_rva = self.addresses[1], self.addresses[2]
        index = struct.unpack("<i", self.proc.read_bytes(self.base + index_rva, 4))[0]
        return self._q(self.base + table_rva + (index + 1) * PLAYER_ENTRY_SIZE + 8)

    def poll(self) -> dict | None:
        """{"location": (code de zone, progression), "snake": (x, y, z) ou None}, None sans jeu."""
        for attempt in range(2):
            if self.proc is None and not self._attach():
                return None
            try:
                link = self._q(self.base + self.addresses[0])
                raw = self.proc.read_bytes(link + STAGE_CODE_OFFSET, 8)
                code = raw.split(b"\0")[0].decode("ascii", errors="replace")
                if not STAGE_CODE.fullmatch(code):
                    return {"location": None, "snake": None}
                progress = struct.unpack("<I", self.proc.read_bytes(link + PROGRESS_OFFSET, 4))[0]
                transform = self._transform()
                snake = struct.unpack("<3f", self.proc.read_bytes(transform + TRANSFORM_POSITION, 12)) if transform else None
                return {"location": (code, progress), "snake": snake}
            except OSError:
                self.close()  # jeu ferme ou relance : nouvel accrochage
        return None

    def loaded_ids(self, kind: int) -> set[int]:
        """Identifiants des ressources de ce type chargees par le jeu."""
        if not self.proc:
            return set()
        try:
            tree = self.base + self.addresses[3]
            stack = [self._q(tree + i * 8) for i in range(16)]
            seen, found = set(), set()
            while stack and len(seen) < 200000:
                node = stack.pop()
                if not node or node in seen:
                    continue
                seen.add(node)
                key = struct.unpack("<I", self.proc.read_bytes(node + 0x10, 4))[0]
                if (key >> 24) & 0x7F == kind:
                    found.add(key & 0xFFFFFF)
                stack += [self._q(node), self._q(node + 8)]
            return found
        except OSError:
            return set()

    def soldiers(self) -> list[dict]:
        """Soldats de la zone : {"actor", "position", "route" (identifiant
        suivi ou None), "point" (index du point vise)}. Lecture seule."""
        if not self.proc:
            return []
        try:
            manager = self._q(self.base + self.addresses[4])
            if not manager:
                return []
            count = struct.unpack("<i", self.proc.read_bytes(manager + 0x30, 4))[0]
            table = self._q(manager + 0x38)
            if not table or not 0 < count < 512:
                return []
            block = self.proc.read_bytes(table, count * 0x10)
            out = []
            for i in range(count):
                actor = struct.unpack_from("<Q", block, i * 0x10 + 8)[0]
                if not actor:
                    continue
                position = struct.unpack("<3f", self.proc.read_bytes(actor + SOLDIER_POSITION, 12))
                if not all(v == v and abs(v) < 1e7 for v in position):
                    continue
                route = point = None
                follower = self._q(actor + SOLDIER_FOLLOWER)
                if follower:
                    entry = self._q(follower + FOLLOWER_ROUTE)
                    if entry:
                        route = struct.unpack("<I", self.proc.read_bytes(entry, 4))[0]
                        point = struct.unpack("<H", self.proc.read_bytes(follower + FOLLOWER_POINT, 2))[0]
                out.append({"actor": actor, "position": position, "route": route, "point": point})
            return out
        except OSError:
            return []

    # -------------------------------------------------- routes chargees (edition en direct)
    def route_location(self, ident: int) -> tuple[int, int] | None:
        """(adresse des points, nombre de points) d'une route chargee, sinon None."""
        if not self.proc:
            return None
        try:
            count = struct.unpack("<i", self.proc.read_bytes(self.base + self.addresses[5], 4))[0]
            table = self._q(self.base + self.addresses[5] + 8)
            offsets = self._q(self.base + self.addresses[6])
            if not table or not offsets or not 0 < count < 0x4000:
                return None
            raw = self.proc.read_bytes(table, 0x10 * count)
            for i in range(count):
                name, _unused, total, offset = struct.unpack_from("<IIII", raw, 0x10 * i)
                if name == ident and offset and 0 < total < 0x400:
                    return offsets + offset, total
        except OSError:
            pass
        return None

    def read_points(self, address: int, total: int) -> bytes | None:
        try:
            return self.proc.read_bytes(address, ROUTE_POINT_SIZE * total) if self.proc else None
        except OSError:
            return None

    def write_bytes(self, address: int, data: bytes) -> bool:
        """Ecriture brute (appelee seulement apres verification par l'editeur)."""
        try:
            if not self.proc:
                return False
            self.proc.write_bytes(address, data)
            return self.proc.read_bytes(address, len(data)) == data
        except OSError:
            return False

    def teleport(self, x: float, y: float, z: float) -> bool:
        try:
            transform = self._transform() if self.proc else 0
            if not transform:
                return False
            self.proc.write_bytes(transform + TRANSFORM_POSITION, struct.pack("<3f", x, y, z))
            return True
        except OSError:
            return False

    def game_root(self) -> str | None:
        """Dossier de mgs4.exe d'apres le processus."""
        if not self.proc:
            return None
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        size = wintypes.DWORD(1024)
        name = ctypes.create_unicode_buffer(size.value)
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        if not kernel32.QueryFullProcessImageNameW(self.proc.handle, 0, name, ctypes.byref(size)):
            return None
        import os
        return os.path.dirname(name.value)
