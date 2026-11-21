"""Lien avec le jeu en cours (facultatif) : zone, position de Snake,
teleportation de Snake. Lecture seule, sauf la position de Snake.

Adresses relevees avec MGS4-Trainer, par version de l'exe (horodatage de
l'en-tete PE). Version inconnue : rien n'est lu."""
import re
import struct

from mgs4routes.winproc import ProcessHandle, find_module_base, find_pid

PROCESS_NAME = "mgs4.exe"
# horodatage PE -> (pointeur vers linkvarbuf, index du joueur, table des
# joueurs, dictionnaire des ressources : 16 arbres, noeud = gauche +0,
# droite +8, cle +0x10 (type << 24 | identifiant, bit 31 = resident))
ADDRESSES = {
    0x6AE07392: (0x157A8E8, 0x238012D0, 0x238012E0, 0x2367B9D0),  # 1.4.2
}
REFERENCE = (0x1C28B28, 0x23EB5150, 0x23EB5160, 0x23D2F6D0)  # 1.4.1 (version de reference du trainer)
NAV_TYPE = 0x15  # type de ressource des .nv2
STAGE_CODE_OFFSET, PROGRESS_OFFSET = 0x34, 0x54
PLAYER_ENTRY_SIZE, TRANSFORM_POSITION = 0x80, 0x10
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
        _link, index_rva, table_rva, _dictionary = self.addresses
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
