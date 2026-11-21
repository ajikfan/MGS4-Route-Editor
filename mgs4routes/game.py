"""Lien avec le jeu en cours (facultatif) : zone, position de Snake,
soldats et routes suivies, teleportation de Snake, edition en direct
des points des routes chargees. Lecture seule, sauf la position de Snake
et les points de route modifies a la demande.

Adresses relevees avec MGS4-Trainer, par version de l'exe (horodatage de
l'en-tete PE). Version inconnue : rien n'est lu."""
import math
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
    0x6AE07392: (0x157A8E8, 0x238012D0, 0x238012E0, 0x2367B9D0, 0x23857630, 0x1C9DB88, 0x1BD54B0,
                 0x23857640),  # 1.4.2
}
REFERENCE = (0x1C28B28, 0x23EB5150, 0x23EB5160, 0x23D2F6D0, 0x23F0B5B0, 0x234F488, 0x22864B0,
             0x23F0B5C0)  # 1.4.1
# dernier : liste des groupes de routes (noeud +8 premier, +0x10 suivant, +0x18
# famille, +0x20 nombre de groupes, +0x28 groupes ; groupe : +8 numero,
# +0xF04 nombre de routes, +0xF28 routes de 0x10 octets, identifiant en +8)
NAV_TYPE = 0x15  # type de ressource des .nv2
STAGE_CODE_OFFSET, PROGRESS_OFFSET = 0x34, 0x54
PLAYER_ENTRY_SIZE, TRANSFORM_POSITION = 0x80, 0x10
# matrice de rotation de Snake (lignes de 16 octets) : m20 en +0x260, m22 en
# +0x268 ; angle = atan2(m20, m22), 0 = Z croissant (releve 2026-11-21 : un
# quart de tour a gauche fait passer l'angle de 2 a 90 degres)
TRANSFORM_YAW_SIN, TRANSFORM_YAW_COS = 0x260, 0x268
# soldat : position +0x2D0 ; suivi de route +0x42D8 -> entree de la route
# suivie (+0x80, identifiant en +0), point en cours (+0x60, mot)
SOLDIER_POSITION, SOLDIER_FOLLOWER = 0x2D0, 0x42D8
FOLLOWER_ROUTE, FOLLOWER_POINT, FOLLOWER_TARGET = 0x80, 0x60, 0x30
ROUTE_POINT_SIZE = 0x30  # position (3 flottants) en +0
# Fiche de route d'un soldat (+0x4640) : 3 cases de 0x14 octets (0, famille,
# groupe, index de la route dans le groupe, point) ; case 1 = route suivie,
# case 2 = route de rechange. +0x46EC : famille et groupe de l'unite. Ecrire
# la fiche suffit : le soldat change de route en moins d'une seconde (rebelle
# apparu par le trainer, s01a57l, 2026-11-21).
RECORD, RECORD_SIZE, RECORD_SLOT = 0x4640, 0x3C, 0x14
UNIT_GROUP = 0x46EC
ROUTE_KIND = 0xEA2C5E
SOLDIER_CAMP, SOLDIER_LIFE = 0x43D0, 0x42B0
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
        """{"location": (code de zone, progression), "snake": (x, y, z) ou None,
        "yaw": angle de Snake en degres ou None}, None sans jeu."""
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
                yaw = None
                if transform:
                    s = struct.unpack("<f", self.proc.read_bytes(transform + TRANSFORM_YAW_SIN, 4))[0]
                    c = struct.unpack("<f", self.proc.read_bytes(transform + TRANSFORM_YAW_COS, 4))[0]
                    if 0.9 < s * s + c * c < 1.1:
                        yaw = math.degrees(math.atan2(s, c))
                return {"location": (code, progress), "snake": snake, "yaw": yaw}
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
                record = self.proc.read_bytes(actor + RECORD, RECORD_SIZE)
                slot_kind, slot_group, slot_index = struct.unpack_from("<IIi", record, 4)
                life_object = self._q(actor + SOLDIER_LIFE)
                life = struct.unpack("<i", self.proc.read_bytes(life_object + 0x314, 4))[0] if life_object else None
                slots = []
                for k in range(2):
                    kind_k, group_k, index_k = struct.unpack_from("<IIi", record, RECORD_SLOT * k + 4)
                    if kind_k == ROUTE_KIND:
                        slots.append((group_k, index_k))
                out.append({"actor": actor, "position": position, "route": route, "point": point,
                            "class": self.class_name(actor), "camp": self._q(actor + SOLDIER_CAMP) >> 56,
                            "life": life, "slots": slots,
                            "record": (slot_group, slot_index) if slot_kind == ROUTE_KIND else None})
            return out
        except OSError:
            return []

    def class_name(self, obj: int) -> str:
        """Nom de classe (RTTI) : .?AVNOM@@ -> NOM."""
        try:
            col = self._q(self._q(obj) - 8)
            descriptor = self.base + struct.unpack("<I", self.proc.read_bytes(col + 0x0C, 4))[0]
            raw = self.proc.read_bytes(descriptor + 0x10, 80).split(b"\0")[0].decode("ascii", "replace")
            return raw[4:-2] if raw.startswith(".?AV") and raw.endswith("@@") else raw
        except (OSError, struct.error):
            return "?"

    def route_groups(self) -> dict[int, tuple[int, int]]:
        """Routes attribuables : identifiant -> (groupe, index dans le groupe)."""
        out = {}
        if not self.proc:
            return out
        try:
            node = self._q(self._q(self.base + self.addresses[7]) + 8)
            seen = set()
            while node and node not in seen and len(seen) < 64:
                seen.add(node)
                kind, _unused, groups = struct.unpack("<iii", self.proc.read_bytes(node + 0x18, 12))
                if kind == ROUTE_KIND:
                    table = self._q(node + 0x28)
                    for g in range(min(max(groups, 0), 64)):
                        group = self._q(table + 8 * g)
                        number = struct.unpack("<i", self.proc.read_bytes(group + 8, 4))[0]
                        count = struct.unpack("<i", self.proc.read_bytes(group + 0xF04, 4))[0]
                        entries = self.proc.read_bytes(self._q(group + 0xF28), 0x10 * min(max(count, 0), 0x400))
                        for r in range(len(entries) // 0x10):
                            out.setdefault(struct.unpack_from("<I", entries, 0x10 * r + 8)[0], (number, r))
                node = self._q(node + 0x10)
        except OSError:
            pass
        return out

    def read_record(self, actor: int) -> tuple[bytes, bytes] | None:
        try:
            return (self.proc.read_bytes(actor + RECORD, RECORD_SIZE),
                    self.proc.read_bytes(actor + UNIT_GROUP, 8))
        except (OSError, AttributeError):
            return None

    def assign_route(self, actor: int, group: int, index: int, ident: int | None = None) -> bool:
        """Route suivie et route de rechange = (groupe, index) ; groupe de l'unite
        aligne. Si `ident` est donne, le suivi de route de l'acteur (+0x42D8) passe
        aussitot sur cette route, au point 1 (sinon le PNJ peut finir sa route en
        cours avant de changer). A n'appeler que pour un acteur verifie (classe)."""
        slot = struct.pack("<IIIiI", 0, ROUTE_KIND, group, index, 0)
        try:
            self.proc.write_bytes(actor + RECORD, slot)
            self.proc.write_bytes(actor + RECORD + RECORD_SLOT, slot)
            self.proc.write_bytes(actor + UNIT_GROUP, struct.pack("<II", ROUTE_KIND, group))
            if self.proc.read_bytes(actor + RECORD, 2 * RECORD_SLOT) != slot * 2:
                return False
            follower = self._q(actor + SOLDIER_FOLLOWER)
            entry = self.route_entry(ident) if ident is not None else None
            location = self.route_location(ident) if ident is not None else None
            if follower and entry and location:
                first = self.proc.read_bytes(location[0], 12)
                self.proc.write_bytes(follower + FOLLOWER_TARGET, first)
                self.proc.write_bytes(follower + FOLLOWER_POINT, struct.pack("<h", 0))
                self.proc.write_bytes(follower + FOLLOWER_ROUTE, struct.pack("<Q", entry[0]))
            return True
        except OSError:
            return False

    def restore_record(self, actor: int, saved: tuple[bytes, bytes]) -> bool:
        try:
            self.proc.write_bytes(actor + RECORD, saved[0])
            self.proc.write_bytes(actor + UNIT_GROUP, saved[1])
            return True
        except OSError:
            return False

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

    def route_entry(self, ident: int) -> tuple[int, bytes] | None:
        """(adresse, 16 octets) de l'entree de la route dans la table des routes chargees."""
        if not self.proc:
            return None
        try:
            count = struct.unpack("<i", self.proc.read_bytes(self.base + self.addresses[5], 4))[0]
            table = self._q(self.base + self.addresses[5] + 8)
            raw = self.proc.read_bytes(table, 0x10 * count) if table and 0 < count < 0x4000 else b""
            for i in range(len(raw) // 0x10):
                if struct.unpack_from("<I", raw, 0x10 * i)[0] == ident:
                    return table + 0x10 * i, raw[0x10 * i:0x10 * i + 0x10]
        except OSError:
            pass
        return None

    def inject_route(self, ident: int, points: bytes) -> bool:
        """Donne a la route `ident` les points fournis (0x30 octets chacun), places
        dans une memoire allouee dans le jeu ; l'entree de la table pointe dessus
        (nombre + decalage sur 32 bits depuis la base des decalages)."""
        entry = self.route_entry(ident)
        if entry is None or not points or len(points) % ROUTE_POINT_SIZE:
            return False
        address, raw = entry
        try:
            base = self._q(self.base + self.addresses[6])
            memory = self.proc.alloc_near(base, max(len(points), 0x1000))
            if not memory or not 0 < memory - base < 0xFFFFFFFF:
                return False  # decalage non representable : rien n'est ecrit dans la table
            self.proc.write_bytes(memory, points)
            if self.proc.read_bytes(memory, len(points)) != points:
                return False
            name, unknown = struct.unpack_from("<II", raw, 0)
            self.proc.write_bytes(address, struct.pack("<IIII", name, unknown, len(points) // ROUTE_POINT_SIZE,
                                                       memory - base))
            return True
        except OSError:
            return False

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
