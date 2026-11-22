"""Editeur de routes (2026-11-20, premiere version).

Carte de dessus d'une zone, lue dans son .geom (grille, routes du jeu,
reperes, objets de collision : voir geom.py), sans lancer le jeu.
On y trace ses propres routes (points numerotes), enregistrees par zone
dans routes/<zone>.json. Jeu lance (lecture seule, sauf la teleportation
de Snake) : position de Snake en direct, zone du jeu, « TP Snake ici »,
« Point = Snake ».

Pas encore fait : murs et sol (geometrie compressee du .geom), vue 3D,
injection des routes dans le jeu et attribution a un PNJ.
Coordonnees en millimetres : X et Z au sol (Z vers le haut de la carte),
Y = hauteur."""
import json
import math
from types import SimpleNamespace
import os
import re
import struct
import sys
import threading
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame,
    QLineEdit,
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QSplitter, QStyledItemDelegate, QVBoxLayout,
    QWidget,
)

from mgs4routes import geom as geomlib
from mgs4routes.game import NAV_TYPE, GameLink
from mgs4routes.collision import MeshCache, clip_below
from mgs4routes.nav import CellCache, NavIndex
from mgs4routes.scene import SceneBuilder
from mgs4routes.textures import TextureLibrary
from mgs4routes.view3d import View3D
from mgs4routes.vpak import STAGE_ARCHIVES, StageFiles

STAGE_CODE = re.compile(r"s\d\da\d\d\w")
# donnees de l'utilisateur a cote du lanceur (ou de l'exe, une fois empaquete)
DATA_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ROUTES_DIR = os.path.join(DATA_DIR, "routes")
NAV_INDEX_FILE = os.path.join(DATA_DIR, "cache", "nv2_index.json")
CELL_CACHE_DIR = os.path.join(DATA_DIR, "cache", "cells")
MESH_CACHE_DIR = os.path.join(DATA_DIR, "cache", "meshes")
TEXTURE_INDEX_FILE = os.path.join(DATA_DIR, "cache", "textures_index.json")  # + textures_256/
DECOR_CUT = 3000  # mm au-dessus du niveau affiche : le decor plus haut est coupe (toits)
DECOR_PIXELS, DECOR_MM_PER_PIXEL = 6000, 50  # image du decor en 2D : taille maximale, finesse
DECOR_IMAGE_DIR = os.path.join(DATA_DIR, "cache", "decor2d")
DECOR_IMAGE_VERSION = 2  # a changer si le rendu change (les images en cache sont alors refaites)
ACTION_NAMES_FILE = os.path.join(DATA_DIR, "action_names.json")
FLAG_NAMES_FILE = os.path.join(DATA_DIR, "flag_names.json")
# Animations par type de PNJ (classe) : {"classe": {"move": {code: true/false}, "action": {...}}} ;
# false = T-pose (animation absente de son paquet). Rempli au fil des essais.
ANIM_SUPPORT_FILE = os.path.join(DATA_DIR, "anim_support.json")
ANIM_SUPPORT_KNOWN = {"MERYL": {"move": {"27": False}}}  # essai du 2026-11-21 (T-pose)
# Hypotheses sur les bits des drapeaux (releve de toutes les routes du jeu, 2026-11-21) :
# rien n'est verifie en jeu ; les noms donnes par l'utilisateur (flag_names.json) priment.
FLAG_HINTS = {
    0: "Hypothèse : phase d'alerte où le point s'applique (bits 0x1 à 0x8 souvent ensemble)",
    1: "Hypothèse : phase d'alerte (voir 0x1) ; fréquent aux postes de garde",
    2: "Hypothèse : phase d'alerte (voir 0x1) ; postes de garde sans attente",
    3: "Hypothèse : phase d'alerte (voir 0x1)",
    4: "Inconnu : surtout des passages, souvent le dernier point",
    5: "Inconnu : postes de garde de l'acte 1",
    9: "Hypothèse : variante A (va avec 0x400 = variante B)",
    10: "Hypothèse : variante B (va avec 0x200 = variante A)",
    12: "Hypothèse : action spéciale minutée (action 47 + paramètre 1 = durée ?) ; passage de BFA5CD",
    16: "Inconnu : souvent avant un dénivelé",
    17: "Inconnu : postes de garde sans attente",
    18: "Hypothèse : le paramètre désigne un objet ou une scène (action 0, allure 0)",
    19: "Hypothèse : escalade, escaliers ou échelles (acte 4, dénivelés)",
    24: "Hypothèse : variante A (va avec 0x2000000)",
    25: "Hypothèse : variante B (va avec 0x1000000)",
    31: "Inconnu : acte 2 seulement",
}
ZONE_BLOCKS_FILE = os.path.join(DATA_DIR, "zone_blocks.json")  # blocs vus en jeu : zone -> progression -> ids
STEAM_GUESSES = (
    r"C:\Program Files (x86)\Steam\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"D:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"F:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
)
ROUTE_COLORS = ("#f0883e", "#4ea1ff", "#3fb950", "#d2a8ff", "#e5c07b", "#56d4c2", "#ff7b72")
ACTIONS = ("Marcher", "Courir", "S'arrêter et regarder", "S'accroupir")
# action de mes routes -> (action du jeu, allure) a l'injection
ACTION_CODES = {"Marcher": (2, 2), "Courir": (2, 4), "S'arrêter et regarder": (3, 2), "S'accroupir": (4, 2)}
WAIT_UNITS_PER_SECOND = 30  # a verifier en jeu (conversion des anciennes routes)


def user_point(x: float, y: float, z: float, **fields) -> dict:
    """Point de mes routes : memes champs qu'un point du jeu (action, allure,
    attente en unites du jeu, orientation sur 16 bits, regard X/Y/Z ou None)."""
    point = {"x": round(x), "y": round(y), "z": round(z), "action": 2, "move": 2, "wait": 0, "angle": 0, "look": None,
             "flags": 0, "param": 0, "param2": 0, "param3": 0}
    point.update(fields)
    return point


def upgrade_point(p: dict) -> dict:
    """Anciennes routes (action en texte, attente en secondes, regard en degres)."""
    if isinstance(p.get("action"), str):
        action, move = ACTION_CODES.get(p["action"], (2, 2))
        degrees = p.get("look", 0) or 0
        p.update(action=action, move=move, wait=min(int(p.get("wait", 0) * WAIT_UNITS_PER_SECOND), 0xFFFE),
                 angle=round(degrees * 0x10000 / 360) & 0xFFFF, look=None)
    p.setdefault("move", 2)
    p.setdefault("angle", 0)
    p.setdefault("look", None)
    for key in ("flags", "param", "param2", "param3"):
        p.setdefault(key, 0)
    return p
TOOLS = ("select", "point", "insert", "move", "delete")
ACCENT = "#f0883e"
POINT_SIZE = 0x30   # point de route en memoire (position en +0)
HEIGHT_GAP = 1000   # ecart de hauteur (mm) avec un voisin au-dela duquel on previent
# Niveau affiche sur la carte : cellules dont le sol est entre niveau - 1,5 m
# et niveau + 2 m ; en mode auto, le niveau suit Snake s'il s'en ecarte de 1 m
LEVEL_BELOW, LEVEL_ABOVE, LEVEL_FOLLOW = 1500, 2000, 1000
WALL_HEIGHT_3D = 2500
# Champs d'un point de route du jeu (voir geom.RoutePoint) : decalage, format
POINT_FIELDS = {"flags": (0x0C, "<I"), "action": (0x1C, "<H"), "move": (0x1E, "<H"),
                "wait": (0x20, "<H"), "angle": (0x22, "<H"),
                "param": (0x24, "<I"), "param2": (0x28, "<I"), "param3": (0x2C, "<I")}
# Actions verifiees en jeu (trainer, 2026-11-07) ; les autres codes sont a
# decouvrir : on peut les nommer dans l'editeur (action_names.json).
KNOWN_ACTIONS = {1: "Passage", 3: "Poste debout", 4: "Poste accroupi", 5: "Se coucher sur le ventre",
                 25: "Retour en réserve", 98: "Les mains sur la tête",
                 # hypotheses (routes du jeu qui franchissent des obstacles, 2026-11-21) :
                 # toujours proposees, meme si la zone ne s'en sert pas
                 60: "En haut d'un rebord (hypothèse)", 46: "Sauter en bas (hypothèse)",
                 30: "Réception après un saut (hypothèse)", 31: "Réception après un saut, variante (hypothèse)",
                 47: "Arrêt minuté (hypothèse)", 42: "Fin de route, message au script (hypothèse)"}
# Allures relevees en jeu (utilisateur, 2026-11-21)
KNOWN_MOVES = {0: "Marche lente", 1: "Marche lente", 2: "Marche", 3: "Marche soutenue", 4: "Course",
               5: "Course accroupie", 6: "Marche accroupie en visant une cible", 12: "Course",
               19: "Marche accroupie en visant droit devant",
               22: "Marche à reculons accroupie en visant une cible",
               23: "Marche soutenue accroupie en visant droit devant",
               24: "Marche soutenue accroupie en visant une cible",
               25: "Marche à reculons soutenue accroupie en visant une cible",
               27: "Marche les mains sur la tête",
               17: "Franchissement : escalade ou saut (hypothèse)"}
WAIT_FOREVER = 0xFFFF
# Classes (RTTI) des PNJ -> nom affiche ; camps : 1 PMC, 2 milice, 0 neutre, >= 0x80 allie
NPC_NAMES = {"ENEMY_MIDEAST_PMC": "Soldat PMC", "ENEMY_MIDEAST_MIL": "Milicien",
             "ENE_PMC_HUMV_GUNNER_MIDEAST": "Mitrailleur de Humvee",
             "ENEMY_SOUTHAMERICA_PMC": "Soldat PMC", "ENEMY_SOUTHAMERICA_MIL": "Rebelle",
             "ENEMY_SOUTHAMERICA_OPE": "PMC (autre type)", "MERYL": "Meryl"}
CAMP_NAMES = {0: "neutre", 1: "PMC", 2: "milice / rebelles"}
# couleur des PNJ sur la carte, selon le camp
CAMP_COLORS = {1: "#ff5c5c", 2: "#4fd18b", 0: "#a3adbb"}
ALLY_COLOR = "#5aa9ff"
ANIMAL_COLOR = "#f2c94c"
ANIMAL_NAMES = {"Hen": "Poule", "RatCuy": "Cochon d'Inde (cuy)", "Crow": "Corbeau", "Rat": "Rat",
                "Frog": "Grenouille", "Dog": "Chien", "Wolf": "Loup", "Bird": "Oiseau"}


def camp_color(camp: int) -> str:
    return ALLY_COLOR if camp >= 0x80 else CAMP_COLORS.get(camp, "#d9a6ff")
MONO = "Consolas"
# premiers caracteres du code de zone -> acte et region (affiche a cote du code)
ACT_NAMES = {"s00": "Prologue", "s01": "Acte 1 · Moyen-Orient", "s02": "Acte 2 · Amérique du Sud",
             "s03": "Acte 3 · Europe de l'Est", "s04": "Acte 4 · Shadow Moses", "s05": "Acte 5 · Outer Haven",
             "s10": "Hors actes"}
TOOL_HINTS = {"select": "Clic : choisir un point · clic sur un soldat : sa route",
              "point": "Clic : ajouter un point · Alt+clic : point à la position de Snake",
              "insert": "Clic près d'un segment : insérer un point",
              "move": "Glisser un point pour le déplacer",
              "delete": "Clic sur un point : le supprimer"}

# icones (traits, 24 x 24), dessinees a la couleur demandee
ICONS = {
    "logo": '<circle cx="5" cy="18" r="2.5"/><circle cx="19" cy="6" r="2.5"/>'
            '<path d="M7 16.5 C 10 12, 9 9, 12 9 S 15 8, 17 7.5"/>',
    "select": '<path d="M4 3l7 17 2-7 7-2z"/>',
    "point": '<circle cx="12" cy="12" r="4"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>',
    "insert": '<path d="M4 20L20 4"/><circle cx="4" cy="20" r="2"/><circle cx="20" cy="4" r="2"/>',
    "move": '<path d="M12 2v20M2 12h20M12 2l-3 3M12 2l3 3M12 22l-3-3M12 22l3-3M2 12l3-3M2 12l3 3M22 12l-3-3M22 12l-3 3"/>',
    "delete": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "target": '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
}


def icon(name: str, color: str = "#c9d1dc", size: int = 18) -> QIcon:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')
    pix = QPixmap()
    pix.loadFromData(svg.encode(), "SVG")
    return QIcon(pix.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation))

STYLE = """
QWidget { background: #0d1117; color: #e6e9ef; font-family: Inter, 'Segoe UI', sans-serif; font-size: 13px; }
QFrame#bar { background: #151a22; border-bottom: 1px solid #2a313c; }
QFrame#left { background: #151a22; border-right: 1px solid #2a313c; }
QFrame#right { background: #151a22; border-left: 1px solid #2a313c; }
QFrame#sep { background: #2a313c; }
QFrame#left QLabel, QFrame#right QLabel, QFrame#bar QLabel, QFrame#left QCheckBox, QFrame#right QCheckBox,
QFrame#layers, QFrame#layers QLabel, QFrame#layers QCheckBox { background: transparent; }
QLabel#section { color: #a3adbb; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel#title { font-size: 15px; font-weight: 700; }
QLabel#big { font-size: 18px; font-weight: 700; }
QLabel#hint { color: #8b95a3; font-size: 12px; }
QLabel#field { color: #a3adbb; font-size: 12px; }
QLabel#pill { color: #8fe3ad; background: #13261b; border: 1px solid #23573a; border-radius: 15px; padding: 5px 12px; font-size: 12px; font-weight: 500; }
QLabel#pill[off="true"] { color: #a3adbb; background: #1d232d; border: 1px solid #2f3846; }
QLabel#overlay { background: #151a22; color: #c9d1dc; border: 1px solid #2a313c; border-radius: 8px; padding: 7px 12px; font-size: 12px; }
QPushButton { background: #1d232d; border: 1px solid #2f3846; border-radius: 6px; padding: 7px 12px; }
QPushButton:hover { background: #252c38; }
QPushButton:disabled { color: #5c6673; border-color: #232a33; background: #171c24; }
QPushButton#tool { background: transparent; border: 1px solid transparent; color: #c9d1dc; padding: 7px 10px; }
QPushButton#tool:hover { background: #1d232d; }
QPushButton#tool:checked { background: #3a2a1c; border: 1px solid #f0883e; color: #ffd2ad; }
QPushButton#seg { border-radius: 0; min-width: 40px; color: #a3adbb; }
QPushButton#seg:checked { background: #2a3442; color: #ffffff; font-weight: 600; }
QPushButton#zoom { background: transparent; border: none; padding: 8px; }
QPushButton#zoom:hover { background: #1d232d; }
QPushButton#live { color: #ffd2ad; border: 1px solid #5b3d22; }
QPushButton#live:checked { background: #f0883e; color: #1a1006; font-weight: 700; border: none; }
QPushButton#live:disabled { color: #5c6673; border-color: #232a33; background: #171c24; }
QLabel#warn { color: #ffb86b; font-size: 12px; }
QPushButton#outline { background: transparent; border: 1px solid #f0883e; color: #ffd2ad; font-weight: 600; }
QPushButton#outline:disabled { border-color: #5b3d22; color: #8a7461; }
QPushButton#primary { background: #f0883e; color: #1a1006; font-weight: 700; border: none; padding: 11px; font-size: 14px; border-radius: 8px; }
QPushButton#primary:disabled { background: #3a2f25; color: #8a7461; }
QComboBox, QDoubleSpinBox, QSpinBox { background: #0f141a; border: 1px solid #2f3846; border-radius: 6px; padding: 5px 8px; min-height: 22px; }
QDoubleSpinBox, QSpinBox { font-family: Consolas, monospace; }
QComboBox#zone { background: #1d232d; font-family: Consolas, monospace; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: #151a22; border: 1px solid #2f3846; selection-background-color: #2a3442; }
QListWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { border-radius: 8px; margin: 1px 0; }
QListWidget::item:selected { background: #251d16; border: 1px solid #5b3d22; }
QListWidget::item:hover:!selected { background: #1a2029; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #3a4452; border-radius: 3px; background: #0f141a; }
QCheckBox::indicator:checked { background: #f0883e; border: 1px solid #f0883e; }
QCheckBox::indicator:disabled { background: #171c24; border-color: #232a33; }
QCheckBox::indicator:checked:disabled { background: #7a5236; border-color: #7a5236; }
QStatusBar { background: #151a22; color: #a3adbb; border-top: 1px solid #2a313c; }
QStatusBar QLabel { background: transparent; color: #a3adbb; font-family: Consolas, monospace; font-size: 12px; padding: 0 10px; }
QGraphicsView { border: none; background: #10151c; }
QMenu { background: #151a22; border: 1px solid #2f3846; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 4px; }
QMenu::item:selected { background: #2a3442; }
"""


# ---------------------------------------------------------------- reglages

def _settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save_setting(key: str, value) -> None:
    data = _settings()
    data[key] = value
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _valid_root(path: str | None) -> bool:
    return bool(path) and any(os.path.isfile(os.path.join(path, *parts)) for parts in STAGE_ARCHIVES)


# ---------------------------------------------------------------- carte

def to_scene(x: float, z: float) -> QPointF:
    """Jeu -> carte. Vu de dessus, le jeu a X croissant vers la GAUCHE et Z vers
    le haut (constate en jeu le 2026-11-21 : la carte etait en miroir)."""
    return QPointF(-x, -z)


def from_scene(p: QPointF) -> tuple[float, float]:
    """Carte -> jeu : (X, Z)."""
    return -p.x(), -p.y()


def cosmetic(color: str, width: float = 1.0, style=Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(QColor(color), width, style)
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class PointItem(QGraphicsEllipseItem):
    """Point d'une route de l'utilisateur : taille fixe a l'ecran, numerote."""
    RADIUS = 11

    def __init__(self, editor, route: int, index: int, color: str, selected: bool, text: str | None = None):
        r = self.RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self.editor, self.route, self.index = editor, route, index
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(20)
        if selected:
            ring = QGraphicsEllipseItem(-r - 6, -r - 6, 2 * r + 12, 2 * r + 12, self)
            ring.setPen(QPen(QColor("#ffffff"), 2))
            ring.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        label = QGraphicsSimpleTextItem(text or str(index + 1), self)
        font = QFont("Inter", 8)
        font.setBold(True)
        label.setFont(font)
        label.setBrush(QBrush(QColor("#10151c")))
        rect = label.boundingRect()
        label.setPos(-rect.width() / 2, -rect.height() / 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.editor.point_dragged(self.route, self.index, self.pos())
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.editor.point_released()


class GameHandle(QGraphicsEllipseItem):
    """Poignee d'un point de route du jeu en edition en direct."""

    def __init__(self, editor):
        super().__init__(-9, -9, 18, 18)
        self.editor = editor
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(QColor(ACCENT)))
        self.setPen(QPen(QColor("#ffffff"), 2))
        self.setZValue(26)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Glisser pour déplacer ce point dans le jeu")

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.editor.game_handle_moved(self.pos())
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self.editor.dragging_game_point = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.editor.dragging_game_point = False
        QTimer.singleShot(0, self.editor.game_handle_released)


class MapView(QGraphicsView):
    clicked = Signal(QPointF, object, object)  # position (scene), modificateurs, element sous la souris
    moved = Signal(QPointF)
    center_requested = Signal()

    def __init__(self, scene):
        super().__init__(scene)
        self.hint = QLabel("", self)
        self.hint.setObjectName("overlay")
        self.zoom_box = QFrame(self)
        self.zoom_box.setStyleSheet("QFrame { background: #151a22; border: 1px solid #2a313c; border-radius: 8px; }")
        box = QVBoxLayout(self.zoom_box)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(2)
        for name, tip, action in (("plus", "Zoom avant", lambda: self.scale(1.25, 1.25)),
                                  ("minus", "Zoom arrière", lambda: self.scale(0.8, 0.8)),
                                  ("target", "Centrer sur Snake", self.center_requested.emit)):
            button = QPushButton()
            button.setObjectName("zoom")
            button.setIcon(icon(name, "#e6e9ef"))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tip)
            button.clicked.connect(action)
            box.addWidget(button)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self._pan = None

    def set_hint(self, text: str):
        self.hint.setText(text)
        self.hint.adjustSize()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.hint.move(16, 16)
        self.zoom_box.adjustSize()
        self.zoom_box.move(self.width() - self.zoom_box.width() - 16, 16)

    def drawForeground(self, painter, rect):
        """Echelle en bas a gauche, en pixels d'ecran."""
        per_mm = abs(self.transform().m11()) or 1.0
        length = 1000
        for candidate in (1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000):
            length = candidate
            if candidate * per_mm >= 90:
                break
        pixels = length * per_mm
        painter.save()
        painter.resetTransform()
        y = self.viewport().height() - 26
        painter.setPen(QPen(QColor("#c9d1dc"), 2))
        painter.drawLine(QPointF(36, y), QPointF(36 + pixels, y))
        font = QFont(MONO, 9)
        painter.setFont(font)
        painter.drawText(QPointF(22, y + 4), "0")
        painter.drawText(QPointF(42 + pixels, y + 4), f"{length // 1000} m")
        painter.restore()

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            while item is not None and not isinstance(item, PointItem) and item.parentItem() is not None:
                item = item.parentItem()
            self.clicked.emit(self.mapToScene(event.position().toPoint()), event.modifiers(), item)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan is not None:
            delta = event.position() - self._pan
            self._pan = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            return
        self.moved.emit(self.mapToScene(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan is not None and event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------- fenetre

def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("section")
    return label


def _separator(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setObjectName("sep")
    if vertical:
        line.setFixedSize(1, 28)
    else:
        line.setFixedHeight(1)
    return line


def _field(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("field")
    return label


class RouteCard(QWidget):
    """Ligne de « Mes routes » : pastille, nom, resume."""

    def __init__(self, route: dict, on_toggle):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 9, 10, 9)
        row.setSpacing(10)
        self.check = QCheckBox()
        self.check.setChecked(not route.get("hidden"))
        self.check.setToolTip("Afficher sur la carte")
        self.check.toggled.connect(on_toggle)
        row.addWidget(self.check)
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background: {route['color']}; border-radius: 3px;")
        row.addWidget(dot)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(route["name"])
        name.setStyleSheet("font-weight: 600; font-size: 13px;")
        n = len(route["points"])
        summary = QLabel(f"{n} point{'s' if n > 1 else ''} · {'boucle' if route.get('loop') else 'aller simple'} · aucun PNJ")
        summary.setStyleSheet("color: #a3adbb; font-size: 12px;")
        text.addWidget(name)
        text.addWidget(summary)
        row.addLayout(text, stretch=1)


ROLE_POINTS, ROLE_ASSIGNABLE, ROLE_MODIFIED = (Qt.ItemDataRole.UserRole + k for k in range(3))


class DeselectableList(QListWidget):
    """Liste ou l'on peut tout deselectionner : clic sur l'element deja choisi,
    clic dans le vide ou Echap. La case a cocher garde son role."""
    CHECK_ZONE = 26  # pixels a gauche : case a cocher

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        on_check = item is not None and bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable) \
            and event.position().x() < self.CHECK_ZONE
        if event.button() == Qt.MouseButton.LeftButton and not on_check and (
                item is None or (item is self.currentItem() and item.isSelected())):
            self.clearSelection()
            self.setCurrentRow(-1)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.currentRow() >= 0:
            self.clearSelection()
            self.setCurrentRow(-1)
            event.accept()
            return
        super().keyPressEvent(event)


class GameRouteDelegate(QStyledItemDelegate):
    """Ligne de « Routes du jeu » dessinee directement : case a cocher et
    identifiant (dessin standard), pastilles PNJ / modif. et nombre de points
    a droite. Bien plus rapide qu'un widget par ligne (815 routes en s01a57l)."""
    HEIGHT = 34
    BADGES = (("PNJ", "#8fe3ad", "#13261b", "#23573a", ROLE_ASSIGNABLE),
              ("modif.", "#ffd2ad", "#3a2a1c", "#5b3d22", ROLE_MODIFIED))

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), self.HEIGHT)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(painter.font())
        font.setFamily("Segoe UI")
        font.setPixelSize(12)
        painter.setFont(font)
        right = rect.right() - 8
        painter.setPen(QColor("#8b95a3"))
        painter.drawText(QRectF(right - 44, rect.top(), 44, rect.height()),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), f"{index.data(ROLE_POINTS)} pts")
        x = right - 50
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        for text, color, background, border, role in reversed(self.BADGES):
            if not index.data(role):
                continue
            width = painter.fontMetrics().horizontalAdvance(text) + 10
            box = QRectF(x - width, rect.center().y() - 8, width, 16)
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(QBrush(QColor(background)))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor(color))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)
            x -= width + 4
        painter.restore()


class RouteEditor(QMainWindow):
    textured_ready = Signal(object, object)  # (cle, lots) du decor texture, depuis le fil de calcul

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MGS4 Route Editor")
        self.resize(1440, 900)
        self.link = GameLink()
        self.files = None
        self.zone = None
        self.geom = None
        self.routes = []          # routes de l'utilisateur de la zone
        self.current = -1         # route choisie
        self.selected = -1        # point choisi
        self.tool = "select"  # outil au lancement
        self.snake = None
        self.game_location = None
        self._building = False
        self._game_items = {"grid": [], "decor": [], "floor": [], "walls": [], "passages": [], "routes": [], "markers": [], "objects": []}
        self._route_items = []
        self._highlight = None
        self._pick_item = None
        self._handle = None
        self._soldier_items = []
        self._soldier_ring = None
        self._snake_item = None
        self.game_pick = None
        self.soldiers = []
        self.animals, self._animal_managers, self._animal_items = [], [], []
        self.selected_soldier = None  # acteur suivi sur la carte
        self.dragging_game_point = False
        # edition en direct : gardee quand on change de zone affichee (le jeu,
        # lui, garde les routes modifiees tant qu'il reste dans sa zone)
        self.live = False
        self.live_zone = None         # zone du jeu des sauvegardes ci-dessous
        self.live_backup = {}         # identifiant -> (adresse, octets d'origine)
        self.live_state = {}          # identifiant -> octets ecrits (attendus en memoire)
        self._geom_zone_of_backup = None
        self.npc_backup = {}          # acteur -> (classe, fiche d'origine) des PNJ a qui on a donne une route
        self.injections = {}          # nom de ma route -> route porteuse du jeu (identifiant)
        self.target_kind = "game"     # route a attribuer : "game" (route du jeu) ou "mine" (ma route)
        # route du jeu a attribuer : choisie par l'utilisateur seulement (le suivi
        # d'un PNJ selectionne sa route sur la carte sans la changer, 2026-11-21)
        self.assign_game_row = None
        self.inject_backup = {}       # route porteuse -> (adresse de l'entree, 16 octets d'origine)
        self.assignable = {}          # identifiant de route -> (groupe, index), lu dans le jeu
        self._assignable_zone = None
        self.cells = []               # cellules praticables des blocs de la zone
        self.block_paths = []         # .nv2 des blocs de la zone (meme numero que leur .geom)
        self.level = 0.0              # hauteur du sol affiche (mm)
        self.walls_3d_dirty = True
        self._3d_pending = False

        self.scene = QGraphicsScene(self)
        self.view = MapView(self.scene)
        self.view.clicked.connect(self.map_clicked)
        self.view.moved.connect(self.cursor_moved)
        self.view.center_requested.connect(self.center_on_snake)
        self.view.set_hint(TOOL_HINTS[self.tool])

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_bar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_left())
        self.view3d = View3D()
        self.view3d.status.connect(self.live_message)
        self.view3d.picked.connect(self.pick_3d)
        self.view3d.placed.connect(self.place_point_3d)
        self.map_stack = QStackedWidget()
        self.map_stack.addWidget(self.view)
        self.map_stack.addWidget(self.view3d)
        body.addWidget(self.map_stack, stretch=1)
        right = QScrollArea()
        right.setWidget(self._build_right())
        right.setWidgetResizable(True)
        right.setFrameShape(QFrame.Shape.NoFrame)
        right.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.setFixedWidth(336)
        right.setStyleSheet("QScrollArea { background: #151a22; border-left: 1px solid #2a313c; }")
        body.addWidget(right)
        outer.addLayout(body, stretch=1)
        self.setCentralWidget(root)

        self.status_zone = QLabel("-")
        self.status_snake = QLabel("Snake : jeu non lancé")
        self.status_cursor = QLabel("Curseur -")
        self.status_count = QLabel("")
        for widget in (self.status_zone, self.status_snake, self.status_cursor):
            self.statusBar().addWidget(widget)
        self.statusBar().addPermanentWidget(self.status_count)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_game)
        self.timer.start(500)
        QTimer.singleShot(0, self.start)

    # -------------------------------------------------- construction
    def _build_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("bar")
        bar.setFixedHeight(56)
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icon("logo", ACCENT, 22).pixmap(22, 22))
        row.addWidget(logo)
        title = QLabel("MGS4 Route Editor")
        title.setObjectName("title")
        row.addWidget(title)
        row.addWidget(_separator(True))
        self.zone_combo = QComboBox()
        self.zone_combo.setObjectName("zone")
        self.zone_combo.setMinimumWidth(280)
        self.zone_combo.setMaxVisibleItems(25)
        self.zone_combo.setToolTip("Zone affichée")
        self.zone_combo.activated.connect(lambda i: self.load_zone(self.zone_combo.itemData(i)))
        row.addWidget(self.zone_combo)
        follow = QPushButton("Zone du jeu")
        follow.setIcon(icon("target"))
        follow.setToolTip("Afficher la zone où se trouve Snake")
        follow.clicked.connect(self.go_to_game_zone)
        row.addWidget(follow)
        self.follow_game_zone = QCheckBox("Suivre le jeu")
        self.follow_game_zone.setToolTip("Charger automatiquement la carte quand le jeu change de zone")
        self.follow_game_zone.setChecked(bool(_settings().get("route_editor_follow_zone", True)))
        self.follow_game_zone.toggled.connect(lambda on: _save_setting("route_editor_follow_zone", on))
        self.follow_game_zone.setStyleSheet("background: transparent;")
        row.addWidget(self.follow_game_zone)
        seg = QFrame()
        seg.setStyleSheet("QFrame { border: 1px solid #2f3846; border-radius: 6px; }")
        seg.setFixedHeight(34)
        seg_row = QHBoxLayout(seg)
        seg_row.setContentsMargins(0, 0, 0, 0)
        seg_row.setSpacing(0)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for text, tip in (("2D", "Carte de dessus : édition des routes"),
                          ("3D", "Vue 3D : murs, routes, soldats (glisser = tourner, clic droit = déplacer, molette = zoom)")):
            button = QPushButton(text)
            button.setObjectName("seg")
            button.setCheckable(True)
            button.setChecked(text == "2D")
            button.setToolTip(tip)
            button.setStyleSheet("border: none;")
            button.clicked.connect(lambda _c=False, t=text: self.set_mode(t))
            self.mode_group.addButton(button)
            seg_row.addWidget(button)
        row.addWidget(seg)
        row.addWidget(_separator(True))
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        labels = {"select": "Sélection", "point": "Point", "insert": "Ligne", "move": "Déplacer", "delete": "Supprimer"}
        self.tool_buttons = {}
        for key in TOOLS:
            button = QPushButton(labels[key])
            button.setObjectName("tool")
            button.setIcon(icon(key))
            button.setIconSize(QSize(15, 15))
            button.setCheckable(True)
            button.setToolTip(TOOL_HINTS[key])
            button.setChecked(key == self.tool)
            button.clicked.connect(lambda _c=False, k=key: self.set_tool(k))
            self.tool_group.addButton(button)
            self.tool_buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)

        self.link_label = QLabel("Jeu non connecté")
        self.link_label.setObjectName("pill")
        self.link_label.setProperty("off", True)
        self.link_label.setToolTip("Le jeu est relu toutes les demi-secondes")
        self.link_label.setFixedHeight(30)
        row.addWidget(self.link_label)
        return bar

    def _build_left(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("left")
        panel.setFixedWidth(280)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(8, 16, 8, 0)
        outer.setSpacing(0)
        # trois blocs (mes routes, routes du jeu, calques) dans un separateur
        # reglable a la souris ; tailles gardees dans les reglages
        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.setChildrenCollapsible(False)
        self.left_split.setHandleWidth(7)
        self.left_split.setStyleSheet("QSplitter::handle { background: transparent; border-top: 1px solid #2a313c; margin: 2px 24px; }"
                                      "QSplitter::handle:hover { border-top: 2px solid #f0883e; }")
        outer.addWidget(self.left_split)
        top = QWidget()
        col = QVBoxLayout(top)
        col.setContentsMargins(0, 0, 0, 4)
        col.setSpacing(6)
        self.left_split.addWidget(top)
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 0, 0)
        head.addWidget(_section("Mes routes"))
        head.addStretch(1)
        new = QPushButton("+ Nouvelle")
        new.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        new.clicked.connect(lambda: self.new_route())
        head.addWidget(new)
        col.addLayout(head)
        self.route_list = QListWidget()
        self.route_list.setMinimumHeight(56)
        self.route_list.currentRowChanged.connect(self.choose_route)
        self.route_list.itemDoubleClicked.connect(lambda _item: self.rename_route())
        self.route_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.route_list.customContextMenuRequested.connect(self.route_menu)
        self.route_list.setToolTip("Double-clic : renommer · clic droit : renommer, supprimer")
        col.addWidget(self.route_list)
        middle = QWidget()
        col = QVBoxLayout(middle)
        col.setContentsMargins(0, 6, 0, 4)
        col.setSpacing(6)
        self.left_split.addWidget(middle)
        head = QHBoxLayout()
        head.setContentsMargins(8, 4, 8, 0)
        head.addWidget(_section("Routes du jeu"))
        head.addStretch(1)
        for text, on in (("Tout", True), ("Aucune", False)):
            button = QPushButton(text)
            button.setStyleSheet("padding: 3px 8px; font-size: 12px;")
            button.setToolTip("Afficher toutes les routes du jeu" if on else "Masquer toutes les routes du jeu")
            button.clicked.connect(lambda _c=False, v=on: self.show_all_game_routes(v))
            head.addWidget(button)
        col.addLayout(head)
        self.game_search = QLineEdit()
        self.game_search.setPlaceholderText("Rechercher une route (ex. fa83, PNJ)")
        self.game_search.setClearButtonEnabled(True)
        self.game_search.setToolTip("Filtre par identifiant (morceau en hexadécimal) ; « PNJ » : routes attribuables. "
                                    "Entrée : sélectionner la première trouvée")
        self.game_search.textChanged.connect(self.filter_game_routes)
        self.game_search.returnPressed.connect(self.select_first_game_route)
        col.addWidget(self.game_search)
        self.game_list = DeselectableList()
        self.game_list.setItemDelegate(GameRouteDelegate(self.game_list))
        self.game_list.setUniformItemSizes(True)
        self.game_list.setStyleSheet(f"QListWidget {{ font-family: {MONO}; font-size: 12px; }}"
                                     "QListWidget::item { padding-left: 6px; }"
                                     "QListWidget::indicator { width: 14px; height: 14px; border: 1px solid #3a4452;"
                                     " border-radius: 3px; background: #0f141a; }"
                                     "QListWidget::indicator:checked { background: #f0883e; border-color: #f0883e; }")
        self.game_list.itemChanged.connect(self.game_item_changed)
        self.game_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.game_list.currentRowChanged.connect(self.highlight_game_route)
        self.game_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.game_list.customContextMenuRequested.connect(self.game_route_menu)
        self.game_list.setToolTip("Clic : la montrer · re-clic, clic dans le vide ou Échap : la désélectionner · "
                                  "clic droit : copier dans mes routes")
        self.game_list.setMinimumHeight(120)
        col.addWidget(self.game_list, stretch=1)
        layers = QFrame()
        layers.setObjectName("layers")
        layers.setStyleSheet("QFrame#layers { border-top: 1px solid #2a313c; }")
        lay = QVBoxLayout(layers)
        lay.setContentsMargins(8, 14, 8, 14)
        lay.setSpacing(8)
        lay.addWidget(_section("Calques"))
        self.layer_checks = {}
        for key, text in (("soldiers", "PNJ (en direct)"), ("animals", "Animaux (en direct)"),
                          ("textured", "Décor texturé (jeu en direct)"),
                          ("decor", "Décor (formes de collision)"),
                          ("floor", "Sol praticable"),
                          ("walls", "Murs et obstacles"), ("passages", "Passages entre cellules"),
                          ("grid", "Grille de la zone"), ("routes", "Routes du jeu"),
                          ("markers", "Repères"), ("objects", "Objets de collision")):
            check = QCheckBox(text)
            check.setChecked(key not in ("objects", "passages", "markers", "grid"))
            check.toggled.connect(lambda on, k=key: self.show_layer(k, on))
            self.layer_checks[key] = check
            lay.addWidget(check)
            if key == "soldiers":  # legende des couleurs
                dot = '<span style="color:{0}; font-size:14px;">●</span>&nbsp;{1}'
                legend = QLabel("&nbsp;&nbsp;".join((dot.format(CAMP_COLORS[1], "PMC"), dot.format(CAMP_COLORS[2], "rebelles"),
                                                     dot.format(ALLY_COLOR, "alliés"),
                                                     '<span style="font-size:14px;">○</span>&nbsp;sans route',
                                                     f'<span style="color:{ANIMAL_COLOR}; font-size:13px;">◆</span>&nbsp;animaux')))
                legend.setWordWrap(True)
                legend.setTextFormat(Qt.TextFormat.RichText)
                legend.setObjectName("hint")
                legend.setContentsMargins(24, 0, 0, 2)
                lay.addWidget(legend)
        level = QHBoxLayout()
        level.setSpacing(6)
        level.addWidget(_field("Niveau"))
        self.level_spin = QDoubleSpinBox()
        self.level_spin.setRange(-100.0, 300.0)
        self.level_spin.setDecimals(1)
        self.level_spin.setSingleStep(0.5)
        self.level_spin.setSuffix(" m")
        self.level_spin.setToolTip("Hauteur du sol affiché : cellules de -1,5 m à +2 m autour")
        self.level_spin.valueChanged.connect(self.level_changed)
        level.addWidget(self.level_spin, stretch=1)
        self.level_auto = QCheckBox("Auto")
        self.level_auto.setChecked(True)
        self.level_auto.setToolTip("Suivre la hauteur de Snake (sinon celle des routes de la zone)")
        self.level_auto.toggled.connect(lambda on: self.update_level(force=True) if on else None)
        level.addWidget(self.level_auto)
        lay.addLayout(level)
        self.blocks_label = QLabel("")
        self.blocks_label.setObjectName("hint")
        self.blocks_label.setWordWrap(True)
        lay.addWidget(self.blocks_label)
        scroll = QScrollArea()  # les calques defilent s'ils manquent de place
        scroll.setWidget(layers)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(90)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.left_split.addWidget(scroll)
        self.left_split.setStretchFactor(0, 0)
        self.left_split.setStretchFactor(1, 1)
        self.left_split.setStretchFactor(2, 0)
        saved = _settings().get("route_editor_left_split")
        self.left_split.setSizes(saved if isinstance(saved, list) and len(saved) == 3 else [130, 420, 380])
        self.left_split.splitterMoved.connect(lambda *_a: _save_setting("route_editor_left_split", self.left_split.sizes()))
        return panel

    def _build_right(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("right")
        panel.setFixedWidth(320)
        col = QVBoxLayout(panel)
        col.setContentsMargins(16, 18, 16, 16)
        col.setSpacing(8)
        self._right_col = col
        col.addWidget(_section("Point sélectionné"))
        self.point_title = QLabel("Aucun point")
        self.point_title.setObjectName("big")
        self.point_title.setWordWrap(True)
        col.addWidget(self.point_title)
        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.prev_button = QPushButton("◀  Précédent")
        self.prev_button.setToolTip("Point précédent de la route (Page précédente)")
        self.prev_button.setShortcut("PgUp")
        self.prev_button.clicked.connect(lambda: self.step_point(-1))
        self.next_button = QPushButton("Suivant  ▶")
        self.next_button.setToolTip("Point suivant de la route (Page suivante)")
        self.next_button.setShortcut("PgDown")
        self.next_button.clicked.connect(lambda: self.step_point(1))
        nav.addWidget(self.prev_button)
        nav.addWidget(self.next_button)
        col.addLayout(nav)
        self.stack_label = QLabel("")
        self.stack_label.setObjectName("warn")
        self.stack_label.setWordWrap(True)
        self.stack_label.hide()
        col.addWidget(self.stack_label)
        col.addSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.coord = {}
        for i, (key, text) in enumerate((("x", "X"), ("y", "Hauteur"), ("z", "Z"))):
            grid.addWidget(_field(text), 0, i)
            spin = QDoubleSpinBox()
            spin.setRange(-1e7, 1e7)
            spin.setDecimals(0)
            spin.setSingleStep(100)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.valueChanged.connect(lambda v, k=key: self.edit_point(k, v))
            grid.addWidget(spin, 1, i)
            self.coord[key] = spin
        col.addLayout(grid)
        col.addSpacing(6)
        col.addWidget(self._build_game_fields())
        self.user_fields = QFrame()
        self.user_fields.setStyleSheet("QFrame { background: transparent; }")
        user_col = QVBoxLayout(self.user_fields)
        user_col.setContentsMargins(0, 0, 0, 0)
        user_col.setSpacing(8)
        user_col.addWidget(_field("Action au point"))
        self.action_combo = QComboBox()
        self.action_combo.addItems(ACTIONS)
        self.action_combo.setToolTip("Enregistrée avec la route ; pas encore transmise au jeu")
        self.action_combo.currentTextChanged.connect(lambda t: self.edit_point("action", t))
        user_col.addWidget(self.action_combo)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.addWidget(_field("Attente (s)"), 0, 0)
        grid.addWidget(_field("Regard (°)"), 0, 1)
        self.wait_spin = QSpinBox()
        self.wait_spin.setRange(0, 600)
        self.wait_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.wait_spin.valueChanged.connect(lambda v: self.edit_point("wait", v))
        self.look_spin = QSpinBox()
        self.look_spin.setRange(0, 359)
        self.look_spin.setWrapping(True)
        self.look_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.look_spin.valueChanged.connect(lambda v: self.edit_point("look", v))
        grid.addWidget(self.wait_spin, 1, 0)
        grid.addWidget(self.look_spin, 1, 1)
        user_col.addLayout(grid)
        col.addWidget(self.user_fields)
        col.addSpacing(6)
        row = QHBoxLayout()
        self.tp_button = QPushButton("TP Snake ici")
        self.tp_button.clicked.connect(self.teleport_here)
        self.snake_button = QPushButton("Point = Snake")
        self.snake_button.clicked.connect(self.point_to_snake)
        row.addWidget(self.tp_button)
        row.addWidget(self.snake_button)
        col.addLayout(row)
        self.stack_button = QPushButton("+ Point au même endroit")
        self.stack_button.setToolTip("Ajoute, juste après ce point, un point au même endroit (copie à modifier),\n"
                                     "comme les routes du jeu qui enchaînent plusieurs actions sur place.\n"
                                     "Raccourci : Maj + clic sur le point avec l'outil Point.")
        self.stack_button.clicked.connect(lambda: self.add_stacked_point())
        col.addWidget(self.stack_button)
        self.assign_box = QFrame()
        self.assign_box.setStyleSheet("QFrame { background: transparent; }")
        assign_col = QVBoxLayout(self.assign_box)
        assign_col.setContentsMargins(0, 10, 0, 0)
        assign_col.setSpacing(8)
        col.addWidget(self.assign_box)
        col = assign_col  # la section Attribuer se range dans son cadre
        col.addWidget(_separator())
        col.addSpacing(4)
        col.addWidget(_section("Attribuer la route"))
        col.addWidget(_field("PNJ (ou clic sur un soldat de la carte)"))
        self.npc_combo = QComboBox()
        self.npc_combo.setMaxVisibleItems(25)
        self.npc_combo.addItem("Jeu non connecté ou autre zone", None)
        self.npc_combo.currentIndexChanged.connect(lambda _i: self.update_assign())
        self.npc_combo.activated.connect(self.npc_activated)  # choix de l'utilisateur seulement
        col.addWidget(self.npc_combo)
        self.loop_check = QCheckBox("Parcours en boucle")
        self.loop_check.toggled.connect(self.edit_loop)
        col.addWidget(self.loop_check)
        self.assign_info = QLabel("")
        self.assign_info.setObjectName("hint")
        self.assign_info.setWordWrap(True)
        self.assign_info.setMinimumHeight(38)  # deux lignes (sinon chevauchement)
        col.addWidget(self.assign_info)
        buttons = QHBoxLayout()
        self.assign_button = QPushButton("Attribuer")
        self.assign_button.setObjectName("outline")
        self.assign_button.setToolTip("Donne au PNJ choisi la route choisie (du jeu ou une des tiennes)")
        self.assign_button.clicked.connect(self.assign_route)
        self.unassign_button = QPushButton("Rendre sa route")
        self.unassign_button.setToolTip("Remet la fiche de route d'origine du PNJ")
        self.unassign_button.clicked.connect(self.restore_npc)
        buttons.addWidget(self.assign_button)
        buttons.addWidget(self.unassign_button)
        col.addLayout(buttons)
        self.anim_button = QPushButton("Animations de ce type…")
        self.anim_button.setToolTip("Noter, pour ce type de PNJ, les allures et actions qui fonctionnent ou donnent une T-pose")
        self.anim_button.clicked.connect(self.edit_anim_support)
        col.addWidget(self.anim_button)
        col = self._right_col
        self.live_warning = QLabel("")
        self.live_warning.setObjectName("warn")
        self.live_warning.setWordWrap(True)
        self.live_warning.hide()
        col.addWidget(self.live_warning)
        self.live_box = QFrame()
        self.live_box.setStyleSheet("QFrame { background: transparent; }")
        box = QVBoxLayout(self.live_box)
        box.setContentsMargins(0, 8, 0, 0)
        box.setSpacing(6)
        box.addWidget(_section("Routes du jeu modifiées"))
        self.live_count = QLabel("")
        self.live_count.setObjectName("hint")
        self.live_count.setWordWrap(True)
        box.addWidget(self.live_count)
        buttons = QHBoxLayout()
        self.restore_button = QPushButton("Rétablir la route")
        self.restore_button.clicked.connect(lambda: self.restore_game_routes(current_only=True))
        restore_all = QPushButton("Tout rétablir")
        restore_all.clicked.connect(lambda: self.restore_game_routes(current_only=False))
        buttons.addWidget(self.restore_button)
        buttons.addWidget(restore_all)
        box.addLayout(buttons)
        self.live_box.hide()
        col.addWidget(self.live_box)
        col.addStretch(1)
        self.inject_button = QPushButton("Injecter dans le jeu")
        self.inject_button.setObjectName("primary")
        self.inject_button.setEnabled(False)
        self.inject_button.setToolTip("Écrit la route choisie de « Mes routes » dans le jeu, à la place d'une "
                                      "route de PNJ inutilisée ; elle peut ensuite être attribuée")
        self.inject_button.clicked.connect(self.inject_route)
        col.addWidget(self.inject_button)
        export = QPushButton("Exporter (JSON)")
        export.setStyleSheet("background: transparent;")
        export.clicked.connect(self.export_routes)
        col.addWidget(export)
        self.enable_point_panel(False)
        self.user_fields.hide()  # ancien panneau des points : remplace par les champs du jeu
        return panel

    def _build_game_fields(self) -> QFrame:
        """Champs d'un point de route du jeu (modifiables en edition en direct)."""
        self.game_fields = QFrame()
        self.game_fields.setStyleSheet("QFrame { background: transparent; }")
        col = QVBoxLayout(self.game_fields)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(_field("Action à l'arrivée"))
        head.addStretch(1)
        self.name_action_button = QPushButton("Nommer…")
        self.name_action_button.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.name_action_button.setToolTip("Donner un nom à ce code d'action (gardé pour toutes les zones)")
        self.name_action_button.clicked.connect(self.name_action)
        head.addWidget(self.name_action_button)
        col.addLayout(head)
        self.game_action = QComboBox()
        self.game_action.setMaxVisibleItems(25)
        self.game_action.activated.connect(lambda _i: self.edit_game_field("action", self.game_action.currentData()))
        col.addWidget(self.game_action)
        col.addSpacing(4)
        col.addWidget(_field("Allure pour y aller"))
        self.game_move = QComboBox()
        self.game_move.setMaxVisibleItems(25)
        self.game_move.activated.connect(lambda _i: self.edit_game_field("move", self.game_move.currentData()))
        col.addWidget(self.game_move)
        col.addSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.addWidget(_field("Attente"), 0, 0)
        grid.addWidget(_field("Orientation (°)"), 0, 1)
        self.game_wait = QSpinBox()
        self.game_wait.setRange(0, WAIT_FOREVER)
        self.game_wait.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.game_wait.setSpecialValueText("0")
        self.game_wait.setToolTip("Unité à confirmer en jeu (valeurs courantes : 60, 90, 120, 180).\n"
                                  "65535 = sans fin ; 65534 = valeur spéciale encore inconnue.")
        self.game_wait.editingFinished.connect(lambda: self.edit_game_field("wait", self.game_wait.value()))
        self.game_angle = QSpinBox()
        self.game_angle.setRange(0, 359)
        self.game_angle.setWrapping(True)
        self.game_angle.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.game_angle.editingFinished.connect(
            lambda: self.edit_game_field("angle", round(self.game_angle.value() * 0x10000 / 360) & 0xFFFF))
        grid.addWidget(self.game_wait, 1, 0)
        grid.addWidget(self.game_angle, 1, 1)
        col.addLayout(grid)
        col.addSpacing(4)
        head = QHBoxLayout()
        head.addWidget(_field("Regard (point visé)"))
        head.addStretch(1)
        self.look_snake_button = QPushButton("Regard = Snake")
        self.look_snake_button.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.look_snake_button.setToolTip("Place le point visé sur la position actuelle de Snake")
        self.look_snake_button.clicked.connect(self.look_at_snake)
        head.addWidget(self.look_snake_button)
        col.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        self.game_look = {}
        for i, key in enumerate("xyz"):
            spin = QDoubleSpinBox()
            spin.setRange(-1e7, 1e7)
            spin.setDecimals(0)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setToolTip(("X", "Hauteur", "Z")[i] + " du point visé (mm)")
            spin.editingFinished.connect(self.edit_game_look)
            grid.addWidget(spin, 0, i)
            self.game_look[key] = spin
        col.addLayout(grid)
        self.game_forever = QCheckBox("Attente sans fin")
        self.game_forever.clicked.connect(
            lambda on: self.edit_game_field("wait", WAIT_FOREVER if on else 0))
        col.addWidget(self.game_forever)
        col.addSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.game_extra = {}
        for i, (key, text, tip) in enumerate((
                ("flags", "Drapeaux", "Drapeaux du point (+0xC), en hexadécimal : 0x2 = poste de garde, "
                                      "0x1000 accompagne l'action 47 ; les autres bits sont inconnus"),
                ("param", "Param. 1", "Paramètre +0x24 (action 47 : 1000, 3000, 10000… peut-être une durée en ms)"),
                ("param2", "Param. 2", "Paramètre +0x28 (rarement utilisé)"),
                ("param3", "Param. 3", "Paramètre +0x2C (rarement utilisé)"))):
            if key == "flags":
                head = QHBoxLayout()
                head.setSpacing(4)
                head.addWidget(_field(text))
                bits = QPushButton("Bits…")
                bits.setStyleSheet("padding: 0px 6px; font-size: 11px;")
                bits.setToolTip("Voir et cocher les bits un par un, avec leur rôle supposé")
                bits.clicked.connect(self.edit_flag_bits)
                head.addWidget(bits)
                grid.addLayout(head, 0, i)
            else:
                grid.addWidget(_field(text), 0, i)
            edit = QLineEdit()
            edit.setToolTip(tip + "\nDécimal ou hexadécimal (0x…).")
            edit.setStyleSheet(f"font-family: {MONO};")
            edit.editingFinished.connect(lambda k=key: self.edit_extra_field(k))
            grid.addWidget(edit, 1, i)
            self.game_extra[key] = edit
        col.addLayout(grid)
        self.game_fields.hide()
        return self.game_fields

    def action_names(self) -> dict[int, str]:
        names = dict(KNOWN_ACTIONS)
        names.update({int(k): v for k, v in _read_json(ACTION_NAMES_FILE).items() if str(k).isdigit()})
        return names

    def action_label(self, code: int, names: dict | None = None) -> str:
        name = (names if names is not None else self.action_names()).get(code)
        return f"{code} · {name}" if name else f"{code} (0x{code:X}) · inconnue"

    def fill_game_combos(self, point):
        """Listes Action et Allure : codes de la zone (les plus frequents d'abord) + valeur du point."""
        names = self.action_names()
        counts = {}
        for route in self.geom.routes:
            for pt in route.points:
                counts[pt.action] = counts.get(pt.action, 0) + 1
        codes = sorted(set(counts) | set(names) | {point.action}, key=lambda c: (c not in names, -counts.get(c, 0), c))
        self.game_action.clear()
        for code in codes:
            self.game_action.addItem(self.action_label(code, names), code)
        self.game_action.setCurrentIndex(self.game_action.findData(point.action))
        moves = sorted(set(KNOWN_MOVES) | {pt.move for r in self.geom.routes for pt in r.points} | {point.move})
        self.game_move.clear()
        for code in moves:
            self.game_move.addItem(f"{code} · {KNOWN_MOVES[code]}" if code in KNOWN_MOVES else f"{code} · inconnue", code)
        self.game_move.setCurrentIndex(self.game_move.findData(point.move))

    def fill_point_fields(self, pt, editable: bool):
        """Champs action / allure / attente / orientation / regard d'un point."""
        self.fill_game_combos(pt)
        for widget in (self.game_wait, self.game_angle, self.game_forever):
            widget.blockSignals(True)
        self.game_forever.setChecked(pt.wait == WAIT_FOREVER)
        self.game_wait.setValue(pt.wait)
        self.game_angle.setValue(round(pt.angle * 360 / 0x10000) % 360)
        for widget in (self.game_wait, self.game_angle, self.game_forever):
            widget.blockSignals(False)
        for widget in (self.game_action, self.game_move, self.game_wait, self.game_angle, self.game_forever,
                       *self.game_look.values()):
            widget.setEnabled(editable)
        self.look_snake_button.setEnabled(editable and self.snake is not None)
        self.name_action_button.setEnabled(True)
        names = self.flag_names()
        for key, edit in self.game_extra.items():
            value = getattr(pt, key, 0)
            edit.blockSignals(True)
            edit.setText(f"0x{value:X}" if key == "flags" else str(value))
            edit.blockSignals(False)
            edit.setEnabled(editable)
            if key == "flags":
                lines = [f"0x{1 << b:X} : {names.get(b, 'rôle inconnu')}" for b in range(32) if value >> b & 1]
                edit.setToolTip("Drapeaux du point (+0xC), décimal ou hexadécimal (0x…).\n"
                                + ("\n".join(lines) if lines else "Aucun bit.") + "\nBouton « Bits… » pour le détail.")
        for key, value in zip("xyz", pt.look):
            self.game_look[key].blockSignals(True)
            self.game_look[key].setValue(value)
            self.game_look[key].blockSignals(False)

    def flag_names(self) -> dict[int, str]:
        names = dict(FLAG_HINTS)
        names.update({int(k): v for k, v in _read_json(FLAG_NAMES_FILE).items() if str(k).isdigit()})
        return names

    def current_flags(self) -> int | None:
        mine = self.user_point_selected()
        if mine is not None:
            return mine.get("flags", 0)
        if self.game_pick is not None:
            r, i = self.game_pick
            return self.geom.routes[r].points[i].flags
        return None

    def edit_flag_bits(self):
        """Fenetre des 32 bits : cases a cocher et noms modifiables."""
        flags = self.current_flags()
        if flags is None:
            return
        editable = self.user_point_selected() is not None or self.live
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Drapeaux du point : 0x{flags:X}")
        dialog.setStyleSheet(STYLE)
        outer = QVBoxLayout(dialog)
        note = QLabel("Cocher un bit change le point" + ("" if editable else " (le jeu doit être dans la zone affichée pour un point du jeu)")
                      + ". Les noms sont des hypothèses à vérifier en jeu : renomme-les au fil de tes essais.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        outer.addWidget(note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        names = self.flag_names()
        checks, edits = {}, {}
        for bit in range(32):
            row, col = bit % 16, (bit // 16) * 3
            check = QCheckBox(f"0x{1 << bit:X}")
            check.setChecked(bool(flags >> bit & 1))
            check.setEnabled(editable)
            check.setStyleSheet(f"font-family: {MONO};")
            edit = QLineEdit(names.get(bit, ""))
            edit.setPlaceholderText("rôle inconnu")
            edit.setMinimumWidth(420)
            edit.setCursorPosition(0)  # debut du texte visible
            edit.setToolTip(names.get(bit, ""))
            grid.addWidget(check, row, col)
            grid.addWidget(edit, row, col + 1)
            checks[bit], edits[bit] = check, edit
        outer.addLayout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(dialog.reject)
        ok = QPushButton("Appliquer")
        ok.setObjectName("outline")
        ok.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        outer.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # noms : on ne garde dans le fichier que ce qui differe des hypotheses
        saved = {str(bit): edit.text().strip() for bit, edit in edits.items()
                 if edit.text().strip() and edit.text().strip() != FLAG_HINTS.get(bit, "")}
        try:
            os.makedirs(os.path.dirname(FLAG_NAMES_FILE), exist_ok=True)
            with open(FLAG_NAMES_FILE, "w", encoding="utf-8") as handle:
                json.dump(saved, handle, ensure_ascii=False, indent=1, sort_keys=True)
        except OSError:
            pass
        value = sum(1 << bit for bit, check in checks.items() if check.isChecked())
        if editable and value != flags:
            self.edit_game_field("flags", value)
        self.show_point()

    def edit_extra_field(self, key: str):
        """Drapeaux ou parametre saisi (decimal ou 0x...)."""
        text = self.game_extra[key].text().strip()
        try:
            value = int(text, 0) & 0xFFFFFFFF
        except ValueError:
            self.show_point()  # saisie invalide : on remet la valeur actuelle
            return
        self.edit_game_field(key, value)

    def user_point_selected(self) -> dict | None:
        route = self.route()
        if self.game_pick is None and route and 0 <= self.selected < len(route["points"]):
            return route["points"][self.selected]
        return None

    def name_action(self):
        mine = self.user_point_selected()
        if mine is not None:
            code = mine["action"]
        elif self.game_pick:
            r, i = self.game_pick
            code = self.geom.routes[r].points[i].action
        else:
            return
        current = self.action_names().get(code, "")
        name, ok = QInputDialog.getText(self, "Nommer l'action", f"Nom de l'action {code} (0x{code:X}) :", text=current)
        if not ok:
            return
        data = _read_json(ACTION_NAMES_FILE)
        if name.strip():
            data[str(code)] = name.strip()
        else:
            data.pop(str(code), None)
        try:
            os.makedirs(os.path.dirname(ACTION_NAMES_FILE), exist_ok=True)
            with open(ACTION_NAMES_FILE, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
        except OSError:
            return
        self.show_point()

    def edit_game_field(self, key: str, value):
        mine = self.user_point_selected()
        if mine is not None and value is not None:  # point de mes routes : enregistre avec la route
            if mine.get(key) != value:
                mine[key] = value
                self.save_routes()
                self.reinject_if_needed()
                self.show_point()
            return
        if not (self.live and self.game_pick is not None) or value is None:
            return
        r, i = self.game_pick
        point = self.geom.routes[r].points[i]
        if getattr(point, key) == value:
            return
        offset, fmt = POINT_FIELDS[key]
        if self.write_game_bytes(r, i, offset, struct.pack(fmt, value)):
            setattr(point, key, value)
        self.show_point()

    def edit_game_look(self, position=None):
        """Ecrit le point vise (+0x10) du point choisi d'une route du jeu (ou de mes routes)."""
        mine = self.user_point_selected()
        if mine is not None:
            look = [round(float(v)) for v in position] if position else [round(self.game_look[k].value()) for k in "xyz"]
            if mine.get("look") != look:
                mine["look"] = look
                self.save_routes()
                self.reinject_if_needed()
                self.show_point()
            return
        if not (self.live and self.game_pick is not None):
            return
        r, i = self.game_pick
        point = self.geom.routes[r].points[i]
        look = tuple(float(v) for v in position) if position else tuple(self.game_look[k].value() for k in "xyz")
        if max(abs(a - b) for a, b in zip(look, point.look)) < 1:
            return
        if self.write_game_bytes(r, i, 0x10, struct.pack("<3f", *look)):
            point.look = look
            self.redraw_game_route(r)
        self.show_point()

    def look_at_snake(self):
        if self.snake:
            self.edit_game_look(self.snake)

    # -------------------------------------------------- passer d'un point a l'autre
    def step_point(self, delta: int):
        if self.game_pick is not None and self.selected == -1:
            r, i = self.game_pick
            count = len(self.geom.routes[r].points)
            if count:
                self.selected_soldier = None
                self.select_game_point(r, (i + delta) % count)
                x, _y, z = self.geom.routes[r].points[self.game_pick[1]].position
                self.view.ensureVisible(QRectF(to_scene(x, z), to_scene(x, z)).adjusted(-1, -1, 1, 1), 80, 80)
            return
        route = self.route()
        if route and route["points"]:
            self.selected = (self.selected + delta) % len(route["points"]) if self.selected >= 0 else 0
            self.redraw_routes()
            self.show_point()

    def stacked_points(self, x: float, z: float, radius: float = 300.0) -> list[tuple[int, int]]:
        """Points des routes du jeu affichees a moins de radius mm de (x, z)."""
        shown = self.visible_game_routes() | ({self.geom.routes[self.game_pick[0]].ident} if self.game_pick else set())
        return [(r, i) for r, route in enumerate(self.geom.routes) if route.ident in shown
                for i, pt in enumerate(route.points) if math.hypot(pt.position[0] - x, pt.position[2] - z) <= radius]

    # -------------------------------------------------- demarrage
    def start(self):
        state = self.link.poll()
        root = self.link.game_root()
        for candidate in (root, _settings().get("game_root"), *STEAM_GUESSES):
            if _valid_root(candidate):
                root = candidate
                break
        else:
            root = QFileDialog.getExistingDirectory(self, "Dossier du jeu (celui de mgs4.exe)")
        if not _valid_root(root):
            QMessageBox.critical(self, "Éditeur de routes", "Archives du jeu introuvables dans ce dossier.")
            return
        _save_setting("game_root", root)
        self.statusBar().showMessage("Lecture des archives du jeu…")
        QApplication.processEvents()
        self.files = StageFiles(root)
        self.geoms = {}
        for path in self.files.entries:
            parts = path.split("/")
            if path.endswith(".geom") and len(parts) >= 3 and parts[-2] == "cache" and STAGE_CODE.fullmatch(parts[-3]):
                self.geoms.setdefault(parts[-3], path)
        for code in sorted(self.geoms):
            self.zone_combo.addItem(f"{code}   {ACT_NAMES.get(code[:3], '')}", code)
        self.nav = NavIndex(self.files, NAV_INDEX_FILE)
        self.cell_cache = CellCache(self.files, CELL_CACHE_DIR)
        self.mesh_cache = MeshCache(self.files, MESH_CACHE_DIR)
        self.textures = TextureLibrary(self.files, root, TEXTURE_INDEX_FILE)
        self.scene_builder = SceneBuilder(self.files, self.textures)
        self._textured_key, self._textured_busy = None, False
        self.textured_ready.connect(self.textured_done)

        def progress(i, n):
            if i % 10 == 0:
                self.statusBar().showMessage(f"Première ouverture : index des blocs de terrain ({i}/{n})…")
                QApplication.processEvents()
        self.nav.load_or_build(progress)
        self._poll_count = 0
        self.statusBar().clearMessage()
        here = state["location"][0] if state and state.get("location") else None
        self.load_zone(here if here in self.geoms else _settings().get("route_editor_zone", "s04a40l"))

    def go_to_game_zone(self):
        if self.game_location and self.game_location[0] in getattr(self, "geoms", {}):
            self.load_zone(self.game_location[0])

    def follow_zone_change(self):
        """Le jeu vient de changer de zone : la carte suit (case « Suivre le jeu »).
        Seulement au changement, pour laisser consulter une autre zone a la main."""
        game_zone = self.game_location[0] if self.game_location else None
        previous = getattr(self, "_followed_game_zone", None)
        if not game_zone or game_zone == previous:
            return
        if self.dragging_game_point:
            return  # on reessaie au releve suivant
        self._followed_game_zone = game_zone
        if (self.follow_game_zone.isChecked() and game_zone != self.zone
                and game_zone in getattr(self, "geoms", {})):
            self.load_zone(game_zone)

    # -------------------------------------------------- zone
    def load_zone(self, zone: str):
        if not self.files or zone not in self.geoms:
            return
        if self.zone:
            self.save_routes()
        self.zone = zone
        _save_setting("route_editor_zone", zone)
        self.zone_combo.setCurrentIndex(max(self.zone_combo.findData(zone), 0))
        self.geom = geomlib.parse(self.files.read(self.geoms[zone]))
        self.routes = self.read_routes(zone)
        self.current = 0 if self.routes else -1
        self.selected = -1
        self.scene.clear()
        self._soldier_items = []
        self._animal_items = []
        self._soldier_ring = None
        self.selected_soldier = None
        self._handle = None
        self._game_items = {"grid": [], "decor": [], "floor": [], "walls": [], "passages": [], "routes": [], "markers": [], "objects": []}
        self._route_items = []
        self._highlight = None
        self._pick_item = None
        self.dragging_game_point = False
        self.walls_3d_dirty = True
        self.game_pick = None
        self.assign_game_row = None
        self._snake_item = None
        self.draw_game_layers()
        self.draw_walls()
        self.fill_lists()
        self.redraw_routes()
        self.fit_view()
        self.update_status()

    def draw_game_layers(self):
        g = self.geom
        x0, z0, x1, z1 = g.bounds()
        if all(g.cells) and all(g.cell):
            area = QGraphicsPathItem()
            path = QPainterPath()
            path.addRect(QRectF(to_scene(x0, z1), to_scene(x1, z0)).normalized())
            area.setPath(path)
            area.setBrush(QBrush(QColor("#151c25")))
            area.setPen(cosmetic("#2c3b4b", 1.5))
            area.setZValue(-10)
            self.scene.addItem(area)
            lines = QPainterPath()
            for i in range(g.cells[0] + 1):
                x = x0 + i * g.cell[0]
                lines.moveTo(to_scene(x, z0))
                lines.lineTo(to_scene(x, z1))
            for k in range(g.cells[2] + 1):
                z = z0 + k * g.cell[2]
                lines.moveTo(to_scene(x0, z))
                lines.lineTo(to_scene(x1, z))
            grid = self.scene.addPath(lines, cosmetic("#1f2833", 1))
            grid.setZValue(-9)
            self._game_items["grid"] += [area, grid]
        for obj in g.objects:
            cx, _cy, cz = obj.center
            hx, _hy, hz = obj.half_size
            rect = self.scene.addRect(QRectF(to_scene(cx - hx, cz + hz), to_scene(cx + hx, cz - hz)).normalized(),
                                      cosmetic("#3c4a5b", 1.5), QBrush(QColor(60, 74, 91, 60)))
            rect.setToolTip(f"Objet de collision {obj.ident:#08x} (forme {obj.shape})")
            rect.setZValue(-5)
            self._game_items["objects"].append(rect)
        shown = self.visible_game_routes()
        self._game_route_items = [self.draw_game_route(route, shown) for route in g.routes]
        for m in g.markers:
            cross = QPainterPath()
            cross.moveTo(-4, -4)
            cross.lineTo(4, 4)
            cross.moveTo(-4, 4)
            cross.lineTo(4, -4)
            item = self.scene.addPath(cross, cosmetic("#c9d1dc", 1.2))
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            item.setPos(to_scene(m.position[0], m.position[2]))
            item.setToolTip(f"Repère {m.ident:#08x} (genre {m.kind:#x}) · hauteur {m.position[1]:.0f}")
            item.setZValue(2)
            self._game_items["markers"].append(item)
        for key, check in self.layer_checks.items():
            self.show_layer(key, check.isChecked())

    def draw_game_route(self, route, shown_routes=None) -> list:
        """Trace en pointille et points d'une route du jeu (orange si modifiee en direct)."""
        group = []
        if not route.points:
            return group
        modified = self.live_zone == self.zone and route.ident in self.live_backup
        color = ACCENT if modified else "#6b7685"
        path = QPainterPath(to_scene(route.points[0].position[0], route.points[0].position[2]))
        for p in route.points[1:]:
            path.lineTo(to_scene(p.position[0], p.position[2]))
        item = self.scene.addPath(path, cosmetic(color, 2 if modified else 1.5, Qt.PenStyle.DashLine))
        item.setToolTip(f"Route du jeu {route.ident:06X} · {len(route.points)} points" + (" · modifiée" if modified else ""))
        item.setZValue(0)
        item.setData(0, route.ident)
        group.append(item)
        for p in route.points:
            dot = self.scene.addEllipse(-3, -3, 6, 6, QPen(Qt.PenStyle.NoPen), QBrush(QColor(color if modified else "#8b95a3")))
            dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            dot.setPos(to_scene(p.position[0], p.position[2]))
            dot.setZValue(1)
            group.append(dot)
        if shown_routes is None:
            shown_routes = self.visible_game_routes()
        shown = (route.ident in shown_routes or modified) and self.layer_checks["routes"].isChecked()
        for part in group:
            part.setVisible(shown)
        return group

    def redraw_game_route(self, index: int):
        self.schedule_3d()
        for part in self._game_route_items[index]:
            self.scene.removeItem(part)
        self._game_route_items[index] = self.draw_game_route(self.geom.routes[index])
        if self.game_list.currentRow() == index:
            self.highlight_game_route(index, center=False)
            self.draw_pick()

    # -------------------------------------------------- vue 3D
    def set_mode(self, mode: str):
        for button in self.mode_group.buttons():
            button.setChecked(button.text() == mode)
        if mode == "3D":
            self.map_stack.setCurrentWidget(self.view3d)
            if self.snake:  # Snake dans la zone affichee : la vue s'ouvre sur lui
                self.view3d.look_at(*self.snake, distance=35.0)
            else:
                center = self.view.mapToScene(self.view.viewport().rect().center())
                cx, cz = from_scene(center)
                self.view3d.look_at(cx, self.level, cz, distance=35.0)
            self.walls_3d_dirty = True
            self.refresh_3d()
            self.view.hint.hide()
        else:
            self.map_stack.setCurrentWidget(self.view)
            self.view.hint.show()

    def ground_height(self) -> float:
        """Hauteur la plus frequente des points de route (le sol de la zone)."""
        heights = [round(p.position[1], -2) for r in (self.geom.routes if self.geom else []) for p in r.points]
        return max(set(heights), key=heights.count) if heights else 0.0

    # -------------------------------------------------- decor texture (modeles affiches par le jeu)
    def textured_wanted_key(self):
        """Cle du decor texture a afficher, None si rien (calque decoche, jeu ailleurs)."""
        if (not hasattr(self, "scene_builder") or not self.layer_checks["textured"].isChecked()
                or not (self.game_location and self.game_location[0] == self.zone)):
            return None
        # une seule fois par zone : la coupe au-dessus du niveau se fait dans la
        # carte graphique (View3D.set_cut). Avec le niveau dans la cle, le niveau
        # Auto (hauteur de Snake) relancait l'assemblage sans fin et figeait
        # l'editeur (2026-11-21).
        return (self.zone,)

    def update_textured(self):
        """Lance l'assemblage en arriere-plan si la zone ou le niveau a change."""
        key = self.textured_wanted_key()
        if key is None:
            if self._textured_key is not None:
                self._textured_key = None
                self.view3d.set_textured([])
                self.walls_3d_dirty = True
                self.schedule_3d()
            return
        if key == self._textured_key or self._textured_busy:
            return
        instances = self.link.model_instances()
        if not instances:
            return
        self._textured_busy = True
        first = self.textures._global is None and not os.path.exists(TEXTURE_INDEX_FILE)
        self.statusBar().showMessage("Décor texturé : première fois, index des textures du jeu (environ 20 s)…" if first
                                     else "Décor texturé : lecture des modèles et des textures…")

        def work():
            batches = None
            try:
                self.textures.set_zone(key[0])
                # le fil laisse souvent la main : l'interface reste fluide (verrou de Python)
                batches = self.scene_builder.build(instances, progress=lambda _k, _n: time.sleep(0.002))
            except Exception as error:  # le fil ne doit jamais tomber sans prevenir
                batches = error
            self.textured_ready.emit(key, batches)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def textured_covers(batches, snake, radius: float = 40.0, minimum: int = 300) -> bool:
        """Au moins `minimum` sommets du decor texture a moins de `radius` m de Snake."""
        import numpy as np
        sx, _sy, sz = snake
        near = 0
        for array, *_rest in batches:
            d = np.hypot(array[:, 0] + sx / 1000.0, array[:, 2] + sz / 1000.0)
            near += int((d < radius).sum())
            if near >= minimum:
                return True
        return False

    def textured_done(self, key, batches):
        self._textured_busy = False
        if isinstance(batches, Exception) or batches is None:
            self.statusBar().showMessage(f"Décor texturé impossible : {batches}", 8000)
            return
        if key != self.textured_wanted_key():  # zone ou niveau change entre-temps
            self.schedule_3d()
            return
        # repli : decor texture qui ne couvre pas les environs de Snake (zone dont
        # le terrain n'est pas dans le tableau des instances, s01a20l) -> on garde
        # le decor de collision plutot qu'une vue vide
        if self.snake and not self.textured_covers(batches, self.snake):
            self._textured_key = key  # pas de nouvel essai en boucle dans cette zone
            self._textured_useless = True
            self.view3d.set_textured([])
            self.statusBar().showMessage("Décor texturé indisponible dans cette zone : décor de collision affiché", 8000)
            self.walls_3d_dirty = True
            self.schedule_3d()
            return
        self._textured_useless = False
        self._textured_key = key
        self.view3d.set_textured(batches)
        triangles = sum(len(b[0]) for b in batches) // 3
        self.statusBar().showMessage(f"Décor texturé : {triangles:,} triangles, {len(batches)} textures".replace(",", " "), 6000)
        self.walls_3d_dirty = True
        self.schedule_3d()

    def schedule_3d(self):
        """Mise a jour de la 3D apres l'evenement en cours (regroupe les appels)."""
        if self._3d_pending or self.map_stack.currentWidget() is not self.view3d:
            return
        self._3d_pending = True
        QTimer.singleShot(0, self.refresh_3d)

    def refresh_3d(self):
        self._3d_pending = False
        if self.map_stack.currentWidget() is not self.view3d or self.geom is None:
            return
        if self.walls_3d_dirty:
            # coupe comme un plan d'architecte : rien au-dessus du niveau affiche + 2 m
            cut = [c for c in self.cells if c["floor"] <= self.level + LEVEL_ABOVE]
            floors, walls = [], []
            decor = self.layer_checks["decor"].isChecked() or (
                getattr(self, "_textured_key", None) is not None and not getattr(self, "_textured_useless", False))
            for c in (cut if not decor else []):  # avec le decor, sols et murs de navigation font doublon
                if c.get("kind") == "obstacle":  # bloc : dessus a la hauteur libre, cotes en murs
                    top = min(c.get("clearance", WALL_HEIGHT_3D), WALL_HEIGHT_3D)
                    if self.layer_checks["floor"].isChecked():
                        floors.append((c["polygon"], c["floor"] + top))
                    if self.layer_checks["walls"].isChecked():
                        poly = c["polygon"]
                        walls += [(*p, *q, c["floor"], top) for p, q in zip(poly, poly[1:] + poly[:1])]
                    continue
                if self.layer_checks["floor"].isChecked():
                    floors.append((c["polygon"], c["floor"]))
                if self.layer_checks["walls"].isChecked():
                    walls += [(*w, c["floor"], WALL_HEIGHT_3D) for w in c["walls"]]
            self.view3d.set_world(floors, walls, self.level)
            self.view3d.set_decor(self.decor_triangles() if self.layer_checks["decor"].isChecked() else None)
            self.view3d.set_pick_surface(self.decor_triangles())  # outil Point en 3D : surfaces de collision
            self.walls_3d_dirty = False
        self.view3d.set_cut(self.level + DECOR_CUT)
        self.update_textured()
        polylines, markers, picks = [], [], []
        if self.layer_checks["routes"].isChecked():
            shown = self.visible_game_routes()
            modified = set(self.live_backup) if self.live_zone == self.zone else set()
            current = self.game_list.currentRow()
            for index, route in enumerate(self.geom.routes):
                if not route.points:
                    continue
                if index == current:
                    color = "#ffffff"
                elif route.ident in modified:
                    color = ACCENT
                elif route.ident in shown:
                    color = "#8b95a3"
                else:
                    continue
                polylines.append(([p.position for p in route.points], color, True))
                picks += [(("game", index, i), p.position) for i, p in enumerate(route.points)]
        for r_index, route in enumerate(self.routes):
            if route.get("hidden") and r_index != self.current:
                continue
            pts = [(p["x"], p["y"], p["z"]) for p in route["points"]]
            picks += [(("mine", r_index, i), xyz) for i, xyz in enumerate(pts)]
            if route.get("loop") and len(pts) > 2:
                pts.append(pts[0])
            polylines.append((pts, route["color"], True))
        if self.game_pick is not None:
            r, i = self.game_pick
            markers.append((self.geom.routes[r].points[i].position, "#ffffff", 0.9))
        elif self.route() is not None and 0 <= self.selected < len(self.route()["points"]):
            q = self.route()["points"][self.selected]
            markers.append(((q["x"], q["y"], q["z"]), "#ffffff", 0.9))
        actors = []
        if self.layer_checks["soldiers"].isChecked():
            for soldier in self.soldiers:
                chosen = soldier["actor"] == self.selected_soldier
                color = ACCENT if chosen else camp_color(soldier.get("camp", 1))
                actors.append({"position": soldier["position"], "color": color, "height": 1.8,
                               "yaw": self.npc_heading(soldier), "selected": chosen})
        if self.layer_checks["animals"].isChecked():
            actors += [{"position": a["position"], "color": ANIMAL_COLOR,
                        "height": 0.8 if a.get("species") == "Wolf" else 0.5, "yaw": a.get("yaw")}
                       for a in self.animals]
        if self.snake:
            yaw = getattr(self, "snake_yaw", None)
            actors.append({"position": self.snake, "color": "#f4f6fa", "height": 1.8,
                           "yaw": math.radians(yaw) if yaw is not None else None})
        self.view3d.set_overlay(polylines, markers)
        self.view3d.set_actors(actors)
        self.view3d.set_pick_points(picks)

    def npc_heading(self, npc: dict) -> float | None:
        """Orientation d'un PNJ (radians, 0 = +Z) deduite de son deplacement ;
        garde la derniere connue quand il s'arrete."""
        headings = self.__dict__.setdefault("_npc_headings", {})
        last = self.__dict__.setdefault("_npc_last_pos", {})
        x, _y, z = npc["position"]
        previous = last.get(npc["actor"])
        last[npc["actor"]] = (x, z)
        if previous and math.hypot(x - previous[0], z - previous[1]) > 30:
            headings[npc["actor"]] = math.atan2(x - previous[0], z - previous[1])
        return headings.get(npc["actor"])

    def pick_3d(self, key):
        """Clic sur un point dans la vue 3D : meme selection qu'en 2D."""
        kind, r, i = key
        self.selected_soldier = None
        if kind == "game":
            if 0 <= r < len(self.geom.routes) and i < len(self.geom.routes[r].points):
                self.select_game_point(r, i)
        elif 0 <= r < len(self.routes) and i < len(self.routes[r]["points"]):
            self.target_kind = "mine"
            if self.game_pick is not None:
                self.game_pick = None
                self.draw_pick()
            self.current, self.selected = r, i
            self.route_list.blockSignals(True)
            self.route_list.setCurrentRow(r)
            self.route_list.blockSignals(False)
            self.redraw_routes()
            self.show_point()
            self.update_assign()
        self.schedule_3d()

    # -------------------------------------------------- edition en direct des routes du jeu
    def live_allowed(self) -> bool:
        return bool(self.game_location and self.game_location[0] == self.zone)

    def update_game_route_marks(self):
        self.game_list.blockSignals(True)
        for index, route in enumerate(self.geom.routes if self.geom else []):
            item = self.game_list.item(index)
            if item is not None and bool(item.data(ROLE_ASSIGNABLE)) != (route.ident in self.assignable):
                item.setData(ROLE_ASSIGNABLE, route.ident in self.assignable)
        self.game_list.blockSignals(False)

    def set_live(self, on: bool):
        """Edition des routes du jeu : active des que le jeu est dans la zone affichee."""
        if on != self.live:
            self.live = on
            self.draw_pick()
            self.show_point()

    def live_check_zone(self):
        """Appele a chaque releve : le jeu a quitte la zone des sauvegardes ->
        ses routes ont ete rechargees depuis les fichiers, plus rien a retablir."""
        game_zone = self.game_location[0] if self.game_location else None
        if self.live_backup and game_zone != self.live_zone:
            self.live_backup, self.live_state, self.live_zone = {}, {}, None
            if self.geom is not None and self.zone == self._geom_zone_of_backup:
                self.reload_geom_routes()
            self.update_live_box()
        self.set_live(self.live_allowed())

    def reload_geom_routes(self):
        """Positions des routes du jeu relues depuis le fichier de la zone."""
        fresh = geomlib.parse(self.files.read(self.geoms[self.zone]))
        self.geom.routes = fresh.routes
        for index in range(len(self.geom.routes)):
            self.redraw_game_route(index)

    def write_game_point(self, r: int, i: int, position) -> bool:
        """Ecrit la position d'un point d'une route du jeu."""
        if not self.write_game_bytes(r, i, 0, struct.pack("<3f", *position)):
            return False
        self.geom.routes[r].points[i].position = tuple(float(v) for v in position)
        self.height_warning(r, i)
        return True

    def write_game_bytes(self, r: int, i: int, offset: int, data: bytes) -> bool:
        """Ecrit dans un point d'une route du jeu, apres verification : la
        route est a la meme adresse et la memoire contient ce qu'on attend."""
        route = self.geom.routes[r]
        location = self.link.route_location(route.ident)
        if location is None or location[1] != len(route.points):
            self.live_message(f"Route {route.ident:06X} introuvable en mémoire : rien n'est écrit.")
            return False
        address, total = location
        current = self.link.read_points(address, total)
        if current is None:
            return False
        if route.ident not in self.live_backup:
            if not self.live_backup:
                self.live_zone = self.zone
                self._geom_zone_of_backup = self.zone
            self.live_backup[route.ident] = (address, current)
            self.live_state[route.ident] = current
        if self.live_backup[route.ident][0] != address or current != self.live_state[route.ident]:
            self.live_message(f"La route {route.ident:06X} a changé en mémoire (rechargée par le jeu ?) : rien n'est écrit.")
            return False
        start = POINT_SIZE * i + offset
        if not self.link.write_bytes(address + start, data):
            self.live_message("Écriture refusée par le jeu.")
            return False
        state = bytearray(current)
        state[start:start + len(data)] = data
        self.live_state[route.ident] = bytes(state)
        self.live_message("")
        self.update_live_box()
        return True

    def height_warning(self, r: int, i: int):
        pts = self.geom.routes[r].points
        y = pts[i].position[1]
        gaps = [abs(y - pts[k].position[1]) for k in (i - 1, i + 1) if 0 <= k < len(pts)]
        if gaps and max(gaps) > HEIGHT_GAP:
            self.live_message(f"Attention : {max(gaps) / 1000:.1f} m de différence de hauteur avec un point voisin. "
                              "Le soldat risque de ne pas trouver de chemin.")
        else:
            self.live_message("")

    def live_message(self, text: str):
        self.live_warning.setText(text)
        self.live_warning.setVisible(bool(text))

    def game_handle_moved(self, pos: QPointF):
        if self._pick_item is not None:
            self._pick_item.setPos(pos)
        for key, value in zip(("x", "z"), from_scene(pos)):
            self.coord[key].blockSignals(True)
            self.coord[key].setValue(value)
            self.coord[key].blockSignals(False)

    def game_handle_released(self):
        if self.game_pick is None or self._handle is None:
            return
        r, i = self.game_pick
        pos = self._handle.pos()
        y = self.geom.routes[r].points[i].position[1]
        gx, gz = from_scene(pos)
        self.write_game_point(r, i, (round(gx), y, round(gz)))
        self.redraw_game_route(r)
        self.show_point()

    def update_live_box(self):
        modified = sorted(self.live_backup) if self.live_zone == self.zone else []
        self.live_box.setVisible(bool(modified))
        self.live_count.setText(", ".join(f"{ident:06X}" for ident in modified))
        current = self.geom.routes[self.game_pick[0]].ident if self.game_pick else None
        self.restore_button.setEnabled(current in self.live_backup)
        self.game_list.blockSignals(True)
        for index, route in enumerate(self.geom.routes if self.geom else []):
            item = self.game_list.item(index)
            if item is not None and bool(item.data(ROLE_MODIFIED)) != (route.ident in modified):
                item.setData(ROLE_MODIFIED, route.ident in modified)
        self.game_list.blockSignals(False)

    def restore_game_routes(self, current_only: bool):
        if not current_only:
            self.restore_injections()
        if current_only:
            if not self.game_pick:
                return
            idents = [self.geom.routes[self.game_pick[0]].ident]
        else:
            idents = list(self.live_backup)
        failed = []
        for ident in idents:
            if ident not in self.live_backup:
                continue
            address, original = self.live_backup[ident]
            location = self.link.route_location(ident)
            current = self.link.read_points(address, len(original) // POINT_SIZE) if location else None
            # seule la route telle qu'on l'a laissee est remise ; sinon le jeu l'a rechargee
            if location and location[0] == address and current == self.live_state[ident]:
                if not self.link.write_bytes(address, original):
                    failed.append(ident)
                    continue
            del self.live_backup[ident]
            del self.live_state[ident]
        if not self.live_backup:
            self.live_zone = None
        # positions d'origine dans la carte
        fresh = {r.ident: r for r in geomlib.parse(self.files.read(self.geoms[self.zone])).routes}
        for index, route in enumerate(self.geom.routes):
            if route.ident in idents and route.ident in fresh and route.ident not in failed:
                self.geom.routes[index] = fresh[route.ident]
                self.redraw_game_route(index)
        self.live_message("Échec pour " + ", ".join(f"{i:06X}" for i in failed) if failed else "")
        self.update_live_box()
        self.show_point()

    # -------------------------------------------------- visibilite des routes du jeu (par zone, gardee)
    def visible_game_routes(self) -> set[int]:
        """Routes du jeu cochees dans cette zone (aucune par defaut)."""
        return set(_settings().get("route_editor_visible", {}).get(self.zone, []))

    def save_visible_game_routes(self, idents: set[int]):
        data = _settings().get("route_editor_visible", {})
        data[self.zone] = sorted(idents)
        _save_setting("route_editor_visible", data)

    def apply_game_route_visibility(self):
        self.schedule_3d()
        shown = self.visible_game_routes()
        layer = self.layer_checks["routes"].isChecked()
        modified = set(self.live_backup) if self.live_zone == self.zone else set()
        for route, group in zip(self.geom.routes, self._game_route_items):
            for part in group:
                part.setVisible(layer and (route.ident in shown or route.ident in modified))

    def toggle_game_route(self, index: int, on: bool):
        shown = self.visible_game_routes()
        ident = self.geom.routes[index].ident
        if on:
            shown.add(ident)
        else:
            shown.discard(ident)
        self.save_visible_game_routes(shown)
        self.apply_game_route_visibility()

    def game_item_changed(self, item):
        """Case cochee ou decochee dans la liste des routes du jeu."""
        row = self.game_list.row(item)
        if 0 <= row < len(self.geom.routes):
            self.toggle_game_route(row, item.checkState() == Qt.CheckState.Checked)

    def show_all_game_routes(self, on: bool):
        self.save_visible_game_routes({r.ident for r in self.geom.routes} if on else set())
        self.game_list.blockSignals(True)
        for i in range(self.game_list.count()):
            self.game_list.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.game_list.blockSignals(False)
        self.apply_game_route_visibility()

    def toggle_route(self, index: int, on: bool):
        if 0 <= index < len(self.routes):
            if on:
                self.routes[index].pop("hidden", None)
            else:
                self.routes[index]["hidden"] = True
            self.save_routes()
            self.redraw_routes()

    # -------------------------------------------------- murs (carte)
    def zone_blocks(self) -> tuple[list[str], str]:
        """Blocs de terrain de la zone : ceux vus charges par le jeu (toutes
        progressions confondues), sinon ceux qui recouvrent ses routes."""
        learned = _read_json(ZONE_BLOCKS_FILE).get(self.zone, {})
        ids = sorted({i for ids in learned.values() for i in ids})
        paths = [p for p in (self.nav.path_for_id(i) for i in ids) if p]
        if paths:
            return paths, f"vus en jeu ({len(learned)} progression(s))"
        pts = [p.position for r in self.geom.routes for p in r.points]
        if not pts:
            return [], "aucun"
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        z0, z1 = min(p[2] for p in pts), max(p[2] for p in pts)
        mx, mz = (x1 - x0) * 0.1 + 2000, (z1 - z0) * 0.1 + 2000
        return self.nav.covering((x0 - mx, z0 - mz, x1 + mx, z1 + mz)), "automatique"

    def draw_walls(self):
        """Sol et murs a partir des cellules de navigation (voir nav.cells)."""
        paths, source = self.zone_blocks()
        self.block_paths = paths
        self.cells = [cell for path in paths for cell in self.cell_cache.get(path)]
        self._blocks_text = (f"Carte : {len(paths)} bloc(s), {len(self.cells)} cellules · {source}"
                             + ("\n" + ", ".join(p.split("/")[-1][:8] for p in paths[:6]) + ("…" if len(paths) > 6 else "")
                                if paths else ""))
        self.update_level(force=True, redraw=False)
        self.draw_level()

    def draw_level(self):
        for key in ("floor", "walls", "passages"):
            for item in self._game_items[key]:
                self.scene.removeItem(item)
            self._game_items[key] = []
        low, high = self.level - LEVEL_BELOW, self.level + LEVEL_ABOVE
        here = [c for c in self.cells if low <= c["floor"] <= high]
        others = [c for c in self.cells if not low <= c["floor"] <= high]

        def area(cells):
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.WindingFill)
            for cell in cells:
                path.addPolygon(QPolygonF([to_scene(x, z) for x, z in cell["polygon"]]))
                path.closeSubpath()
            return path

        # relief : 5 teintes selon la hauteur du sol dans le niveau
        ground = [c for c in here if c.get("kind", "floor") != "obstacle"]
        relief = []
        for step in range(5):
            lo = low + (high - low) * step / 5
            hi = low + (high - low) * (step + 1) / 5
            cells = [c for c in ground if lo <= c["floor"] < hi or (step == 4 and c["floor"] == high)]
            if cells:
                shade = QColor.fromHsv(212, 90, 52 + step * 9)
                relief.append((area(cells), shade))
        shelters = area([c for c in here if c.get("kind") == "shelter"])
        obstacles = area([c for c in here if c.get("kind") == "obstacle"])
        walls, passages = QPainterPath(), QPainterPath()
        count = 0
        for cell in here:
            for x1, z1, x2, z2 in cell["walls"]:
                walls.moveTo(to_scene(x1, z1))
                walls.lineTo(to_scene(x2, z2))
                count += 1
            for x1, z1, x2, z2, _y, _h in cell["portals"]:
                passages.moveTo(to_scene(x1, z1))
                passages.lineTo(to_scene(x2, z2))
        hatch = QBrush(QColor(255, 255, 255, 28), Qt.BrushStyle.BDiagPattern)
        items = [("floor", self.scene.addPath(area(others), QPen(Qt.PenStyle.NoPen), QBrush(QColor("#141a22"))), -12)]
        items += [("floor", self.scene.addPath(path, QPen(Qt.PenStyle.NoPen), QBrush(shade)), -11) for path, shade in relief]
        items += [("floor", self.scene.addPath(shelters, QPen(Qt.PenStyle.NoPen), hatch), -10.5),
                  ("floor", self.scene.addPath(obstacles, cosmetic("#8a97a8", 1), QBrush(QColor("#5b6878"))), -10),
                  ("floor", self.scene.addPath(obstacles, QPen(Qt.PenStyle.NoPen), QBrush(QColor(16, 21, 28, 90),
                                                                                       Qt.BrushStyle.FDiagPattern)), -9.5)]
        items += [("passages", self.scene.addPath(passages, cosmetic("#5f8f6b", 1, Qt.PenStyle.DotLine)), -7),
                  ("walls", self.scene.addPath(walls, cosmetic("#e6e9ef", 1.4)), -6)]
        for key, item, z in items:
            item.setZValue(z)
            item.setVisible(self.layer_checks[key].isChecked())
            self._game_items[key].append(item)
        self.draw_decor_2d()
        self.blocks_label.setText(f"{self._blocks_text}\nNiveau {self.level / 1000:.1f} m : "
                                  f"{len(here)} cellules, {count} murs")
        self.walls_3d_dirty = True
        self.schedule_3d()

    def update_level(self, force: bool = False, redraw: bool = True):
        """Niveau auto : hauteur de Snake (s'il est dans la zone), sinon le sol des routes."""
        if not self.level_auto.isChecked():
            return
        target = self.floor_under(*self.snake) if self.snake else self.ground_height()
        if force or abs(target - self.level) > LEVEL_FOLLOW:
            self.level = target
            self.level_spin.blockSignals(True)
            self.level_spin.setValue(target / 1000)
            self.level_spin.blockSignals(False)
            if redraw:
                self.draw_level()

    def draw_decor_2d(self):
        """Decor vu de dessus : image ombree (eclairage + hauteur), coupee au niveau.
        L'image est gardee sur le disque (par zone, blocs et niveau)."""
        import hashlib
        import numpy as np
        for item in self._game_items["decor"]:
            self.scene.removeItem(item)
        self._game_items["decor"] = []
        geoms = sorted(p[:-4] + ".geom" for p in self.block_paths)
        key = hashlib.sha1(f"{DECOR_IMAGE_VERSION}|{self.zone}|{round(self.level)}|{'|'.join(geoms)}".encode()).hexdigest()[:16]
        name = os.path.join(DECOR_IMAGE_DIR, f"{self.zone}_{key}")
        image, place = QImage(name + ".png"), _read_json(name + ".json")
        if image.isNull() or not place:
            image, place = self.render_decor_2d(np)
            if image is None:
                return
            try:
                os.makedirs(DECOR_IMAGE_DIR, exist_ok=True)
                image.save(name + ".png")
                with open(name + ".json", "w", encoding="utf-8") as handle:
                    json.dump(place, handle)
            except OSError:
                pass
        item = self.scene.addPixmap(QPixmap.fromImage(image))
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setScale(1 / place["scale"])
        item.setPos(to_scene(place["x1"], place["z1"]))
        item.setZValue(-11.5)
        item.setOpacity(0.7)
        item.setVisible(self.layer_checks["decor"].isChecked())
        self._game_items["decor"].append(item)

    def render_decor_2d(self, np):
        tris = self.decor_triangles()
        if tris is None or not len(tris):
            return None, None
        x0, x1 = float(tris[:, :, 0].min()), float(tris[:, :, 0].max())
        z0, z1 = float(tris[:, :, 2].min()), float(tris[:, :, 2].max())
        scale = min(DECOR_PIXELS / max(x1 - x0, z1 - z0), 1 / DECOR_MM_PER_PIXEL)
        width, height = int((x1 - x0) * scale) + 2, int((z1 - z0) * scale) + 2
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
        light = np.array((0.35, 0.85, -0.40))
        light /= np.linalg.norm(light)
        lit = 0.4 + 0.6 * np.abs(normal @ light)
        top = tris[:, :, 1].max(axis=1)
        rel = np.clip((top - (self.level - LEVEL_BELOW)) / (LEVEL_ABOVE + DECOR_CUT + LEVEL_BELOW), 0, 1)
        order = np.argsort(top)  # du plus bas au plus haut : le dessus recouvre
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        for k in order:
            if abs(normal[k, 1]) < 0.05:
                continue  # paroi verticale : invisible de dessus
            v = int((30 + 175 * rel[k]) * (0.78 + 0.22 * lit[k]))  # surtout la hauteur
            painter.setBrush(QColor(max(v - 6, 0), v, min(v + 16, 255)))
            t = tris[k]
            painter.drawPolygon(QPolygonF([QPointF((x1 - p[0]) * scale, (z1 - p[2]) * scale) for p in t]))
        painter.end()
        return image, {"x1": x1, "z1": z1, "scale": scale}

    def decor_triangles(self):
        """Formes de collision des blocs de la zone, coupees au-dessus du niveau
        (calcul garde tant que la zone, les blocs et le niveau ne changent pas)."""
        import numpy as np
        key = (self.zone, tuple(self.block_paths), round(self.level))
        if getattr(self, "_decor_key", None) == key:
            return self._decor_tris
        self._decor_key = key
        self._decor_tris = self._decor_triangles(np)
        return self._decor_tris

    def _decor_triangles(self, np):
        parts = []
        for path in self.block_paths:
            geom = path[:-4] + ".geom"
            if geom in self.files.entries:
                parts.append(self.mesh_cache.get(geom))
        if not parts:
            return None
        return clip_below(np.concatenate(parts), self.level + DECOR_CUT)

    def floor_under(self, x: float, y: float, z: float) -> float:
        """Sol de la cellule sous ce point (la plus haute sous les pieds). Sur une
        caisse ou un muret (cellule obstacle), c'est le sol a leur pied."""
        best = None
        for cell in self.cells:
            if cell["floor"] > y + 300:
                continue
            poly = cell["polygon"]
            sign = None
            for p, q in zip(poly, poly[1:] + poly[:1]):
                c = (q[0] - p[0]) * (z - p[1]) - (q[1] - p[1]) * (x - p[0])
                if abs(c) < 1:
                    continue
                if sign is None:
                    sign = c > 0
                elif sign != (c > 0):
                    break
            else:
                if best is None or cell["floor"] > best:
                    best = cell["floor"]
        return y if best is None else best

    def level_changed(self, value: float):
        self.level_auto.blockSignals(True)
        self.level_auto.setChecked(False)
        self.level_auto.blockSignals(False)
        self.level = value * 1000
        self.draw_level()

    def learn_blocks(self):
        """Jeu dans la zone affichee : retient les blocs qu'il a charges."""
        if not (self.game_location and self.game_location[0] == self.zone):
            return
        ids = sorted(self.link.loaded_ids(NAV_TYPE))
        ids = [i for i in ids if self.nav.path_for_id(i)]
        if not ids:
            return
        data = _read_json(ZONE_BLOCKS_FILE)
        progress = str(self.game_location[1])
        # le jeu charge les blocs selon la position de Snake : on les cumule
        ids = sorted(set(ids) | set(data.get(self.zone, {}).get(progress, [])))
        if data.get(self.zone, {}).get(progress) == ids:
            return
        data.setdefault(self.zone, {})[progress] = ids
        try:
            with open(ZONE_BLOCKS_FILE, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=1)
        except OSError:
            return
        self.draw_walls()

    def show_layer(self, key: str, on: bool):
        if key in ("walls", "floor", "passages", "decor", "textured"):
            self.walls_3d_dirty = True
        self.schedule_3d()
        if key == "soldiers":
            for item in getattr(self, "_soldier_items", ()):
                item.setVisible(on)
            self.schedule_3d()
            return
        if key == "animals":
            for item in getattr(self, "_animal_items", ()):
                item.setVisible(on)
            self.schedule_3d()
            return
        if key == "routes":
            if getattr(self, "geom", None) is not None and hasattr(self, "_game_route_items"):
                self.apply_game_route_visibility()
            return
        for item in self._game_items.get(key, ()):
            item.setVisible(on)

    def fit_view(self):
        x0, z0, x1, z1 = self.geom.bounds()
        shown = self.visible_game_routes()
        pts = [p.position for r in self.geom.routes if r.ident in shown for p in r.points]
        pts += [(p["x"], p["y"], p["z"]) for r in self.routes for p in r["points"]]
        walls = self._game_items["walls"][0].boundingRect() if self._game_items["walls"] else QRectF()
        if not walls.isEmpty():  # la carte des murs donne le cadre
            pts += [(walls.left(), 0, -walls.top()), (walls.right(), 0, -walls.bottom())]
        if not pts:
            pts = [p.position for r in self.geom.routes for p in r.points]
        if pts:  # cadre sur ce qui est trace (la grille deborde souvent)
            x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
            z0, z1 = min(p[2] for p in pts), max(p[2] for p in pts)
        margin = max(x1 - x0, z1 - z0, 1000) * 0.05
        rect = QRectF(to_scene(x0 - margin, z1 + margin), to_scene(x1 + margin, z0 - margin)).normalized()
        self.scene.setSceneRect(rect.adjusted(-rect.width(), -rect.height(), rect.width(), rect.height()))
        self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def fill_lists(self):
        self.route_list.blockSignals(True)
        self.route_list.clear()
        for route in self.routes:
            card = RouteCard(route, lambda on, i=self.route_list.count(): self.toggle_route(i, on))
            item = QListWidgetItem()
            item.setSizeHint(card.sizeHint())
            self.route_list.addItem(item)
            self.route_list.setItemWidget(item, card)
        self.route_list.setCurrentRow(self.current)
        self.route_list.blockSignals(False)
        self.game_list.blockSignals(True)
        self.game_list.clear()
        shown = self.visible_game_routes()
        self.game_list.setUpdatesEnabled(False)
        for route in self.geom.routes:
            item = QListWidgetItem(f"{route.ident:06X}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if route.ident in shown else Qt.CheckState.Unchecked)
            item.setData(ROLE_POINTS, len(route.points))
            item.setToolTip("Cocher : afficher sur la carte")
            self.game_list.addItem(item)
        self.game_list.setUpdatesEnabled(True)
        self.game_list.blockSignals(False)
        self.filter_game_routes()
        self.update_live_box()
        self.update_game_route_marks()

    # -------------------------------------------------- routes de l'utilisateur
    def read_routes(self, zone: str) -> list[dict]:
        try:
            with open(os.path.join(ROUTES_DIR, f"{zone}.json"), encoding="utf-8") as handle:
                routes = json.load(handle).get("routes", [])
            for route in routes:
                route["points"] = [upgrade_point(p) for p in route.get("points", [])]
            return routes
        except (OSError, ValueError):
            return []

    def save_routes(self):
        if not self.zone:
            return
        path = os.path.join(ROUTES_DIR, f"{self.zone}.json")
        if not self.routes and not os.path.exists(path):
            return
        os.makedirs(ROUTES_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"zone": self.zone, "routes": self.routes}, handle, ensure_ascii=False, indent=1)

    def new_route(self, points=None, name=None):
        color = ROUTE_COLORS[len(self.routes) % len(ROUTE_COLORS)]
        self.routes.append({"name": name or f"Route {len(self.routes) + 1}", "color": color, "loop": False,
                            "points": points or []})
        self.current = len(self.routes) - 1
        self.selected = -1
        self.changed()

    def delete_route(self):
        if not 0 <= self.current < len(self.routes):
            return
        name = self.routes[self.current]["name"]
        if QMessageBox.question(self, "Supprimer la route", f"Supprimer « {name} » ?") != QMessageBox.StandardButton.Yes:
            return
        del self.routes[self.current]
        self.current = min(self.current, len(self.routes) - 1)
        self.selected = -1
        self.changed()

    def rename_route(self):
        route = self.route()
        if not route:
            return
        name, ok = QInputDialog.getText(self, "Renommer la route", "Nom :", text=route["name"])
        if ok and name.strip():
            route["name"] = name.strip()
            self.changed()

    def route_menu(self, pos):
        if self.route_list.itemAt(pos) is None:
            return
        menu = QMenu(self)
        menu.addAction("Renommer…", self.rename_route)
        menu.addAction("Supprimer", self.delete_route)
        menu.exec(self.route_list.mapToGlobal(pos))

    def game_route_menu(self, pos):
        if self.game_list.itemAt(pos) is None:
            return
        self.game_list.setCurrentItem(self.game_list.itemAt(pos))
        menu = QMenu(self)
        menu.addAction("Copier dans mes routes", self.copy_game_route)
        menu.exec(self.game_list.mapToGlobal(pos))

    def filter_game_routes(self, _text: str = ""):
        """Liste des routes du jeu filtree par le champ de recherche : morceaux
        d'identifiant (hexadecimal, sans tenir compte de la casse) ; le mot
        « pnj » garde les routes attribuables."""
        if not hasattr(self, "game_search") or self.geom is None:
            return
        words = self.game_search.text().strip().upper().replace("0X", "").split()
        want_npc = "PNJ" in words
        parts = [w for w in words if w != "PNJ"]
        shown = 0
        for row, route in enumerate(self.geom.routes):
            ident = f"{route.ident:06X}"
            ok = all(w in ident for w in parts) and (not want_npc or route.ident in self.assignable)
            self.game_list.setRowHidden(row, not ok)
            shown += ok
        if words:
            self.game_search.setToolTip(f"{shown} route(s) trouvée(s) · Entrée : sélectionner la première")

    def select_first_game_route(self):
        for row in range(self.game_list.count()):
            if not self.game_list.isRowHidden(row):
                self.game_list.setCurrentRow(row)  # surlignage, centrage et cible de l'attribution
                self.game_list.scrollToItem(self.game_list.item(row))
                return

    def choose_route(self, row: int):
        self.target_kind = "mine"
        self.selected_soldier = None  # choix de l'utilisateur : ne plus suivre le PNJ
        self.game_pick = None
        self.draw_pick()
        self.current = row
        self.selected = -1
        self.redraw_routes()
        self.show_point()
        self.update_assign()

    def copy_game_route(self):
        row = self.game_list.currentRow()
        if not 0 <= row < len(self.geom.routes):
            return
        route = self.geom.routes[row]
        points = [user_point(*p.position, action=p.action, move=p.move, wait=p.wait, angle=p.angle, look=list(p.look),
                             flags=p.flags, param=p.param, param2=p.param2, param3=p.param3)
                  for p in route.points]
        self.new_route(points, f"Copie de {route.ident:06X}")

    def changed(self):
        self.target_kind = "mine"  # on vient de modifier une de mes routes
        self.save_routes()
        self.fill_lists()
        self.redraw_routes()
        self.show_point()
        self.update_status()

    def route(self) -> dict | None:
        return self.routes[self.current] if 0 <= self.current < len(self.routes) else None

    def redraw_routes(self):
        self.schedule_3d()
        self._building = True
        for item in self._route_items:
            self.scene.removeItem(item)
        self._route_items = []
        for r_index, route in enumerate(self.routes):
            pts = route["points"]
            mine = r_index == self.current
            if route.get("hidden") and not mine:
                continue
            if len(pts) >= 2:
                path = QPainterPath(to_scene(pts[0]["x"], pts[0]["z"]))
                for p in pts[1:]:
                    path.lineTo(to_scene(p["x"], p["z"]))
                if route.get("loop"):
                    path.lineTo(to_scene(pts[0]["x"], pts[0]["z"]))
                line = self.scene.addPath(path, cosmetic(route["color"], 3 if mine else 2))
                line.setOpacity(1.0 if mine else 0.55)
                line.setZValue(10)
                self._route_items.append(line)
            for i, p in enumerate(pts):
                first, last = self.stack_range(pts, i)
                if i != first and not (mine and i == self.selected):
                    continue  # point superpose : un seul repere par pile (sauf le point choisi)
                text = f"{first + 1}–{last + 1}" if last > first else None
                shown = self.selected if mine and first <= self.selected <= last else first
                item = PointItem(self, r_index, shown, route["color"], mine and shown == self.selected, text)
                item.setPos(to_scene(p["x"], p["z"]))
                item.setOpacity(1.0 if mine else 0.55)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, mine and self.tool in ("move", "select"))
                self.scene.addItem(item)
                self._route_items.append(item)
        self._building = False

    @staticmethod
    def stack_range(pts: list, i: int) -> tuple[int, int]:
        """Premier et dernier point de la pile (points consecutifs au meme endroit) qui contient i."""
        same = lambda a, b: a["x"] == b["x"] and a["z"] == b["z"] and a["y"] == b["y"]
        first = i
        while first > 0 and same(pts[first - 1], pts[i]):
            first -= 1
        last = i
        while last + 1 < len(pts) and same(pts[last + 1], pts[i]):
            last += 1
        return first, last

    def add_stacked_point(self, index: int | None = None):
        """Ajoute, juste apres le point (choisi), un point au meme endroit : copie a modifier."""
        route = self.route()
        index = self.selected if index is None else index
        if not route or not 0 <= index < len(route["points"]):
            return
        copy = dict(route["points"][index])
        route["points"].insert(index + 1, copy)
        self.selected = index + 1
        self.changed()
        self.reinject_if_needed()

    def highlight_game_route(self, row: int, center: bool = True):
        self.schedule_3d()
        if center:  # choix de l'utilisateur dans la liste : ne plus suivre le soldat
            self.selected_soldier = None
            self.target_kind = "game"
            self.assign_game_row = row
        if hasattr(self, "assign_button"):
            self.update_assign()
        if self._highlight is not None:
            self.scene.removeItem(self._highlight)
            self._highlight = None
        if center and (self.game_pick is None or self.game_pick[0] != row):
            self.game_pick = None
            self.draw_pick()
        if not 0 <= row < len(self.geom.routes):
            return
        route = self.geom.routes[row]
        if not route.points:
            return
        path = QPainterPath(to_scene(route.points[0].position[0], route.points[0].position[2]))
        for p in route.points[1:]:
            path.lineTo(to_scene(p.position[0], p.position[2]))
        self._highlight = self.scene.addPath(path, cosmetic("#e6e9ef", 3))
        self._highlight.setZValue(5)
        for p in route.points:  # ses points aussi, meme si la route est decochee
            dot = QGraphicsEllipseItem(-4, -4, 8, 8, self._highlight)
            dot.setPen(QPen(Qt.PenStyle.NoPen))
            dot.setBrush(QBrush(QColor("#e6e9ef")))
            dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            dot.setPos(to_scene(p.position[0], p.position[2]))
        if center:
            self.view.centerOn(to_scene(route.points[0].position[0], route.points[0].position[2]))

    # -------------------------------------------------- points des routes du jeu (lecture seule)
    def pick_game_point(self, scene_pos: QPointF):
        """Point de route du jeu le plus proche du clic (routes affichees ou surlignee)."""
        tolerance = 12 / (abs(self.view.transform().m11()) or 1.0)
        shown = self.visible_game_routes() if self.layer_checks["routes"].isChecked() else set()
        current = self.game_list.currentRow()
        hits = []
        for r, route in enumerate(self.geom.routes):
            if route.ident not in shown and r != current:
                continue
            for i, pt in enumerate(route.points):
                d = math.hypot(pt.position[0] + scene_pos.x(), pt.position[2] + scene_pos.y())
                if d <= tolerance:
                    hits.append((r, i))
        if not hits:
            return None
        hits.sort()
        # clic repete au meme endroit : point superpose suivant
        if self.game_pick in hits:
            return hits[(hits.index(self.game_pick) + 1) % len(hits)]
        return min(hits, key=lambda h: math.hypot(self.geom.routes[h[0]].points[h[1]].position[0] + scene_pos.x(),
                                                 self.geom.routes[h[0]].points[h[1]].position[2] + scene_pos.y()))

    def sync_route_from_game(self, r: int) -> bool:
        """Edition en direct : relit la route dans le jeu (elle a pu etre modifiee
        avant le lancement de l'editeur). Vrai si quelque chose a change."""
        route = self.geom.routes[r]
        location = self.link.route_location(route.ident)
        if location is None or location[1] != len(route.points):
            return False
        data = self.link.read_points(*location)
        if data is None:
            return False
        changed = False
        for i, pt in enumerate(route.points):
            x, y, z, flags, lx, ly, lz, action, move, wait, angle, param, param2, param3 = struct.unpack_from(
                "<3fI3f4H3I", data, POINT_SIZE * i)
            fresh = dict(position=(x, y, z), flags=flags, look=(lx, ly, lz), action=action, move=move, wait=wait, angle=angle,
                         param=param, param2=param2, param3=param3)
            for key, value in fresh.items():
                if getattr(pt, key) != value:
                    setattr(pt, key, value)
                    changed = True
        if changed and route.ident in self.live_state and self.live_backup[route.ident][0] == location[0]:
            self.live_state[route.ident] = data
        return changed

    def select_game_point(self, r: int, i: int, user: bool = True):
        if user:  # choix de l'utilisateur : devient la route a attribuer
            self.target_kind = "game"
            self.assign_game_row = r
        if self.live and self.sync_route_from_game(r):
            self.redraw_game_route(r)
        self.game_pick = (r, i)
        if self.selected != -1:
            self.selected = -1
            self.redraw_routes()
        self.game_list.blockSignals(True)
        self.game_list.setCurrentRow(r)
        self.game_list.blockSignals(False)
        self.highlight_game_route(r, center=False)
        self.draw_pick()
        self.show_point()

    def draw_pick(self):
        if self.dragging_game_point:
            return
        if self._pick_item is not None:
            self.scene.removeItem(self._pick_item)
            self._pick_item = None
        if self._handle is not None:
            self.scene.removeItem(self._handle)
            self._handle = None
        if self.game_pick is None:
            return
        r, i = self.game_pick
        pos = self.geom.routes[r].points[i].position
        self._pick_item = QGraphicsEllipseItem(-9, -9, 18, 18)
        self._pick_item.setPen(QPen(QColor("#ffffff"), 2))
        self._pick_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._pick_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._pick_item.setPos(to_scene(pos[0], pos[2]))
        self._pick_item.setZValue(25)
        self.scene.addItem(self._pick_item)
        if self.live and self.tool in ("select", "move"):
            self._handle = GameHandle(self)
            self._handle.setPos(to_scene(pos[0], pos[2]))
            self.scene.addItem(self._handle)

    def show_game_point(self):
        r, i = self.game_pick
        route = self.geom.routes[r]
        pt = route.points[i]
        self.enable_point_panel(False)
        self.loop_check.setEnabled(False)
        state = "modifiable en direct" if self.live else "lecture seule"
        self.point_title.setText(f"Point {i + 1} / {len(route.points)} · route du jeu {route.ident:06X} ({state})")
        for spin in self.coord.values():
            spin.setEnabled(self.live)
        self.user_fields.hide()
        self.stack_button.hide()
        self.assign_box.show()
        self.loop_check.hide()
        self.game_fields.show()
        self.prev_button.setEnabled(len(route.points) > 1)
        self.next_button.setEnabled(len(route.points) > 1)
        self.fill_point_fields(pt, editable=self.live)
        self.name_action_button.setEnabled(True)
        stacked = [h for h in self.stacked_points(pt.position[0], pt.position[2]) if h != (r, i)]
        if stacked:
            names = ", ".join(f"{self.geom.routes[a].ident:06X} #{b + 1}" for a, b in stacked[:4])
            self.stack_label.setText(f"{len(stacked)} autre(s) point(s) au même endroit : {names}"
                                     + ("…" if len(stacked) > 4 else "") + ". Recliquer dessus pour passer de l'un à l'autre.")
        self.stack_label.setVisible(bool(stacked))
        self.update_live_box()
        widgets = (*self.coord.values(), self.look_spin)
        for widget in widgets:
            widget.blockSignals(True)
        for key, value in zip(("x", "y", "z"), pt.position):
            self.coord[key].setValue(value)
        dx, dz = pt.look[0] - pt.position[0], pt.look[2] - pt.position[2]
        self.look_spin.setValue(round(math.degrees(math.atan2(dx, dz))) % 360 if dx or dz else 0)
        for widget in widgets:
            widget.blockSignals(False)
        self.tp_button.setEnabled(self.snake is not None)

    # -------------------------------------------------- outils
    def set_tool(self, tool: str):
        self.tool = tool
        self.view3d.place_mode = tool in ("point", "insert")
        self.tool_buttons[tool].setChecked(True)
        self.view.set_hint(TOOL_HINTS[tool])
        self.draw_pick()
        self.redraw_routes()

    def center_on_snake(self):
        if self.snake:
            self.view.centerOn(to_scene(self.snake[0], self.snake[2]))

    def default_height(self, x: float, z: float) -> float:
        if self.snake:
            return self.snake[1]
        route = self.route()
        if route and route["points"]:
            return route["points"][-1]["y"]
        nearest = min(((p.position[0] - x) ** 2 + (p.position[2] - z) ** 2, p.position[1])
                      for r in self.geom.routes for p in r.points) if self.geom.routes else (0, 0.0)
        return nearest[1]

    def map_clicked(self, scene_pos: QPointF, modifiers, item):
        if isinstance(item, PointItem):
            self.target_kind = "mine"
        x, z = from_scene(scene_pos)
        if isinstance(item, GameHandle):
            return
        point = item if isinstance(item, PointItem) else None
        if point is None and self.tool in ("select", "move", "delete"):
            soldier = self.pick_soldier(scene_pos)
            index = next((i for i, r in enumerate(self.geom.routes) if soldier and r.ident == soldier["route"]), None)
            if index is not None and self.geom.routes[index].points:
                self.selected_soldier = soldier["actor"]
                self.choose_npc(soldier["actor"])
                self.follow_soldier()
                self.draw_soldiers()
                return
            hit = self.pick_game_point(scene_pos)
            if hit:
                self.selected_soldier = None
                self.select_game_point(*hit)
                return
        self.selected_soldier = None
        if self.game_pick is not None:
            self.game_pick = None
            self.draw_pick()
            self.show_point()
        if point is not None and point.route != self.current:
            self.current, self.selected = point.route, point.index
            self.changed()
            return
        if point is not None and self.tool == "point" and modifiers & Qt.KeyboardModifier.ShiftModifier:
            self.add_stacked_point(point.index)
            return
        if point is not None and self.tool in ("select", "move", "point"):
            pts = self.routes[self.current]["points"]
            first, last = self.stack_range(pts, point.index)
            if first <= self.selected <= last and last > first:  # clic repete sur une pile : point suivant
                self.selected = first + (self.selected - first + 1) % (last - first + 1)
            else:
                self.selected = point.index
            self.redraw_routes()
            self.show_point()
            return
        if self.tool in ("select", "move"):
            return
        if self.tool == "delete":
            if point is not None:
                del self.routes[self.current]["points"][point.index]
                self.selected = -1
                self.changed()
            return
        if point is not None:
            self.selected = point.index
            self.redraw_routes()
            self.show_point()
            return
        if self.route() is None:
            self.new_route()
        pts = self.route()["points"]
        if modifiers & Qt.KeyboardModifier.AltModifier and self.snake:
            x, y, z = self.snake
        else:
            y = self.default_height(x, z)
        new = user_point(x, y, z)
        if self.tool == "insert" and len(pts) >= 2:
            index = self.nearest_segment(x, z) + 1
            pts.insert(index, new)
            self.selected = index
        else:
            pts.append(new)
            self.selected = len(pts) - 1
        self.changed()

    def place_point_3d(self, x: float, y: float, z: float):
        """Outil Point en 3D : point pose sur la surface cliquee (dessus d'un
        muret, marche...), a sa hauteur exacte."""
        if self.route() is None:
            self.new_route()
        pts = self.route()["points"]
        new = user_point(x, y, z)
        if self.tool == "insert" and len(pts) >= 2:
            index = self.nearest_segment(x, z) + 1
            pts.insert(index, new)
            self.selected = index
        else:
            pts.append(new)
            self.selected = len(pts) - 1
        self.target_kind = "mine"
        self.changed()
        self.reinject_if_needed()
        self.live_message(f"Point posé en 3D à ({x:.0f}, {y:.0f}, {z:.0f}).")

    def nearest_segment(self, x: float, z: float) -> int:
        pts = self.route()["points"]
        best, best_index = None, len(pts) - 1
        for i in range(len(pts) - 1):
            ax, az, bx, bz = pts[i]["x"], pts[i]["z"], pts[i + 1]["x"], pts[i + 1]["z"]
            dx, dz = bx - ax, bz - az
            t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz or 1)))
            d = math.hypot(ax + t * dx - x, az + t * dz - z)
            if best is None or d < best:
                best, best_index = d, i
        return best_index

    def point_dragged(self, route: int, index: int, pos: QPointF):
        if self._building or not 0 <= route < len(self.routes):
            return
        pts = self.routes[route]["points"]
        first, last = self.stack_range(pts, index)  # une pile de points se deplace d'un bloc
        for p in pts[first:last + 1]:
            gx, gz = from_scene(pos)
            p["x"], p["z"] = round(gx), round(gz)
        self.selected = index
        self.show_point()

    def point_released(self):
        if self.tool in ("move", "select"):
            self.save_routes()
            # apres l'evenement : le point qui le traite serait detruit
            QTimer.singleShot(0, self.redraw_routes)

    # -------------------------------------------------- panneau du point
    def enable_point_panel(self, on: bool):
        for widget in (*self.coord.values(), self.action_combo, self.wait_spin, self.look_spin,
                       self.tp_button, self.snake_button):
            widget.setEnabled(on)

    def show_point(self):
        route = self.route()
        point = route["points"][self.selected] if route and 0 <= self.selected < len(route["points"]) else None
        if point is None and self.game_pick is not None:
            self.show_game_point()
            return
        self.game_fields.setVisible(point is not None)
        self.stack_button.setVisible(point is not None)
        self.stack_label.hide()
        self.user_fields.hide()
        if point is not None:
            first, last = self.stack_range(route["points"], self.selected)
            if last > first:
                self.stack_label.setText(f"Points {first + 1} à {last + 1} au même endroit (point {self.selected + 1} "
                                         "affiché). Recliquer dessus ou Précédent / Suivant pour passer de l'un à l'autre.")
                self.stack_label.show()
        self.assign_box.show()
        self.loop_check.show()
        self.prev_button.setEnabled(bool(route and len(route["points"]) > 1))
        self.next_button.setEnabled(bool(route and len(route["points"]) > 1))
        self.enable_point_panel(point is not None)
        self.loop_check.blockSignals(True)
        self.loop_check.setChecked(bool(route and route.get("loop")))
        self.loop_check.setEnabled(route is not None)
        self.loop_check.blockSignals(False)
        if point is None:
            self.point_title.setText(f"{route['name']} · aucun point choisi" if route else "Aucune route")
            return
        self.point_title.setText(f"Point {self.selected + 1} · {route['name']}")
        for spin in self.coord.values():
            spin.blockSignals(True)
        for key, spin in self.coord.items():
            spin.setValue(point[key])
        for spin in self.coord.values():
            spin.blockSignals(False)
        self.fill_point_fields(SimpleNamespace(action=point["action"], move=point["move"], wait=point["wait"],
                                               angle=point["angle"], look=point["look"] or (point["x"], point["y"], point["z"]),
                                               flags=point["flags"], param=point["param"], param2=point["param2"],
                                               param3=point["param3"]), editable=True)
        self.tp_button.setEnabled(self.snake is not None)
        self.snake_button.setEnabled(self.snake is not None)

    def edit_point(self, key: str, value):
        if self.live and self.game_pick is not None and self.selected == -1 and key in ("x", "y", "z"):
            r, i = self.game_pick
            position = list(self.geom.routes[r].points[i].position)
            position["xyz".index(key)] = round(value)
            if self.write_game_point(r, i, position):
                self.redraw_game_route(r)
            return
        route = self.route()
        if not route or not 0 <= self.selected < len(route["points"]):
            return
        route["points"][self.selected][key] = round(value) if key in ("x", "y", "z") else value
        self.save_routes()
        if key in ("x", "z"):
            self.redraw_routes()

    def edit_loop(self, on: bool):
        route = self.route()
        if route:
            route["loop"] = on
            self.save_routes()
            self.redraw_routes()

    def teleport_here(self):
        route = self.route()
        if route and 0 <= self.selected < len(route["points"]):
            p = route["points"][self.selected]
        elif self.game_pick is not None:  # point d'une route du jeu
            r, i = self.game_pick
            x, y, z = self.geom.routes[r].points[i].position
            p = {"x": x, "y": y, "z": z}
        else:
            return
        if self.game_location and self.game_location[0] != self.zone:
            QMessageBox.information(self, "TP Snake ici", f"Snake est en {self.game_location[0]}, pas en {self.zone}.")
            return
        self.link.teleport(p["x"], p["y"] + 100, p["z"])

    def point_to_snake(self):
        route = self.route()
        if route and self.snake and 0 <= self.selected < len(route["points"]):
            p = route["points"][self.selected]
            p["x"], p["y"], p["z"] = (round(v) for v in self.snake)
            self.changed()

    def export_routes(self):
        if not self.zone:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporter les routes", f"routes_{self.zone}.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"zone": self.zone, "routes": self.routes}, handle, ensure_ascii=False, indent=1)

    # -------------------------------------------------- jeu en direct
    def poll_game(self):
        state = self.link.poll()
        if not state:
            self.snake = None
            self.game_location = None
            self.link_label.setText("Jeu non connecté")
            self._set_pill(False)
        else:
            self.game_location = state.get("location")
            self.follow_zone_change()
            here = self.game_location[0] if self.game_location else "?"
            self.link_label.setText(f"●  Connecté au jeu · {here}")
            self._set_pill(True)
            self.snake = state.get("snake") if self.game_location and self.game_location[0] == self.zone else None
            self.snake_yaw = state.get("yaw")
        self.draw_snake()
        here = bool(state and self.game_location and self.game_location[0] == self.zone)
        self.soldiers = self.link.soldiers() if here else []
        if here and (getattr(self, "_poll_count", 0) % 10 == 0 or not self._animal_managers):  # troupeaux relus toutes les 5 s
            self._animal_managers = self.link.animal_list(self.link.task_actors("AnimalManager"))
        self.animals = self.link.animal_positions(self._animal_managers) + self.link.wolves() if here else []
        self.draw_animals()
        self.live_check_zone()
        if here and (not self.assignable or self._assignable_zone != self.game_location[0]):
            self.assignable = self.link.route_groups()
            self._assignable_zone = self.game_location[0]
            self.update_game_route_marks()
            self.filter_game_routes()
        game_zone = self.game_location[0] if self.game_location else None
        if game_zone != getattr(self, "_npc_zone", None):
            self.npc_backup = {}  # le jeu a change de zone : ce ne sont plus les memes PNJ
            self.injections, self.inject_backup = {}, {}
            self._npc_zone = game_zone
        self.refresh_npcs()
        if self.selected_soldier is not None and not self.dragging_game_point:
            self.follow_soldier()
        self.draw_soldiers()
        if self.snake and self.cells:
            self.update_level()
        self.schedule_3d()
        self.update_status()
        self._poll_count = getattr(self, "_poll_count", 0) + 1
        if state and self.zone and self._poll_count % 10 == 1:  # toutes les 5 s
            self.learn_blocks()
        if self.route() and 0 <= self.selected < len(self.route()["points"]):
            self.tp_button.setEnabled(self.snake is not None)
            self.snake_button.setEnabled(self.snake is not None)

    def _set_pill(self, on: bool):
        if self.link_label.property("off") != (not on):
            self.link_label.setProperty("off", not on)
            self.link_label.style().unpolish(self.link_label)
            self.link_label.style().polish(self.link_label)

    def draw_soldiers(self):
        """Un repere par PNJ, couleur du camp (PMC rouge, rebelles vert, allies bleu) ;
        creux s'il ne suit pas de route."""
        if not self.zone:
            return
        while len(self._soldier_items) < len(self.soldiers):
            item = QGraphicsEllipseItem(-6, -6, 12, 12)
            item.setPen(QPen(QColor("#10151c"), 2))
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            item.setZValue(28)
            self.scene.addItem(item)
            self._soldier_items.append(item)
        for item, soldier in zip(self._soldier_items, self.soldiers):
            x, y, z = soldier["position"]
            item.setPos(to_scene(x, z))
            route = soldier["route"]
            color = QColor(camp_color(soldier.get("camp", 1)))
            if route:
                item.setBrush(QBrush(color))
                item.setPen(QPen(QColor("#10151c"), 2))
            else:
                item.setBrush(QBrush(QColor("#10151c")))
                item.setPen(QPen(color, 3))
            text = f"{self.npc_label(soldier)} · hauteur {y:.0f}" if soldier.get("class") else f"PNJ · hauteur {y:.0f}"
            if route:
                text += f"\nSuit la route {route:06X}, vers le point {soldier['point'] + 1}"
            item.setToolTip(text)
            item.setVisible(self.layer_checks["soldiers"].isChecked())
        for item in self._soldier_items[len(self.soldiers):]:
            item.setVisible(False)
        chosen = next((x for x in self.soldiers if x["actor"] == self.selected_soldier), None)
        if chosen is None:
            if self._soldier_ring is not None:
                self._soldier_ring.setVisible(False)
            return
        if self._soldier_ring is None:
            self._soldier_ring = QGraphicsEllipseItem(-13, -13, 26, 26)
            self._soldier_ring.setPen(QPen(QColor(ACCENT), 3))
            self._soldier_ring.setBrush(QBrush(QColor(240, 136, 62, 60)))
            self._soldier_ring.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self._soldier_ring.setZValue(27)
            self.scene.addItem(self._soldier_ring)
        x, _y, z = chosen["position"]
        self._soldier_ring.setPos(to_scene(x, z))
        self._soldier_ring.setVisible(self.layer_checks["soldiers"].isChecked())

    def follow_soldier(self):
        """Route et point vise du soldat choisi, a jour a chaque releve."""
        soldier = next((x for x in self.soldiers if x["actor"] == self.selected_soldier), None)
        if soldier is None or not soldier["route"]:
            return
        index = next((i for i, r in enumerate(self.geom.routes) if r.ident == soldier["route"]), None)
        if index is None or not self.geom.routes[index].points:
            return
        target = min(soldier["point"], len(self.geom.routes[index].points) - 1)
        if self.game_pick != (index, target):
            self.select_game_point(index, target, user=False)
            self.point_title.setText(self.point_title.text().replace("Point ", "Soldat → point ", 1))

    # -------------------------------------------------- PNJ : attribution d'une route
    def npc_label(self, npc: dict) -> str:
        name = NPC_NAMES.get(npc["class"], npc["class"])
        camp = CAMP_NAMES.get(npc["camp"], "allié" if npc["camp"] >= 0x80 else f"camp {npc['camp']}")
        route = f"{npc['route']:06X}" if npc["route"] else "sans route"
        far = ""
        if self.snake:
            d = math.hypot(npc["position"][0] - self.snake[0], npc["position"][2] - self.snake[2]) / 1000
            far = f" · {d:.0f} m"
        mark = " · route donnée" if npc["actor"] in self.npc_backup else ""
        dead = " · hors d'état" if npc.get("life") is not None and npc["life"] <= 0 else ""
        return f"{name} ({camp}) · {route}{far}{mark}{dead}"

    def refresh_npcs(self):
        """Liste des PNJ, mise a jour a chaque releve sans changer l'ordre : ordre
        du jeu, nouveaux PNJ a la fin, disparus retires ; seuls les textes changent."""
        npcs = {n["actor"]: n for n in self.soldiers if n.get("class")}
        combo = self.npc_combo
        combo.blockSignals(True)
        if not npcs:
            if combo.count() != 1 or combo.itemData(0) is not None:
                combo.clear()
                combo.addItem("Aucun PNJ (jeu non connecté ou autre zone)", None)
        else:
            if combo.count() and combo.itemData(0) is None:
                combo.clear()
            for i in reversed(range(combo.count())):  # disparus
                if combo.itemData(i) not in npcs:
                    combo.removeItem(i)
            present = {combo.itemData(i) for i in range(combo.count())}
            for actor, npc in npcs.items():  # nouveaux, a la fin, dans l'ordre du jeu
                if actor not in present:
                    combo.addItem(self.npc_label(npc), actor)
            for i in range(combo.count()):  # textes a jour, sans toucher a l'ordre
                text = self.npc_label(npcs[combo.itemData(i)])
                if combo.itemText(i) != text:
                    combo.setItemText(i, text)
        combo.blockSignals(False)
        self.update_assign()

    def choose_npc(self, actor: int):
        """PNJ choisi depuis la carte : la liste suit (sans recentrer la vue)."""
        index = self.npc_combo.findData(actor)
        if index >= 0:
            self.npc_combo.setCurrentIndex(index)

    def npc_activated(self, _index: int):
        """PNJ choisi dans la liste : selectionne sur la carte, vue centree dessus."""
        actor = self.npc_combo.currentData()
        npc = next((n for n in self.soldiers if n["actor"] == actor), None)
        if npc is None:
            return
        self.selected_soldier = actor
        if npc["route"]:
            self.follow_soldier()
        self.draw_soldiers()
        x, y, z = npc["position"]
        self.view.centerOn(to_scene(x, z))
        if self.map_stack.currentWidget() is self.view3d:
            self.view3d.look_at(x, y, z)
        self.update_assign()

    def selected_game_route(self):
        row = self.game_list.currentRow()
        return self.geom.routes[row] if self.geom and 0 <= row < len(self.geom.routes) else None

    def assign_target(self):
        """(route du jeu ou None, ma route ou None) a attribuer, selon le dernier choix."""
        mine = self.route()
        if self.target_kind == "mine" and mine is not None:
            return None, mine
        row = self.assign_game_row
        return (self.geom.routes[row] if self.geom and row is not None and 0 <= row < len(self.geom.routes) else None), None

    def update_assign(self):
        npc = next((n for n in self.soldiers if n["actor"] == self.npc_combo.currentData()), None)
        route, mine = self.assign_target()
        self.update_inject_button()
        ok, why = False, ""
        if not self.live_allowed():
            why = "Le jeu doit être dans la zone affichée."
        elif npc is None:
            why = "Choisis un PNJ."
        elif mine is not None and len(mine["points"]) < 2:
            why = f"« {mine['name']} » doit avoir au moins 2 points."
        elif mine is None and route is None:
            why = "Choisis une route (du jeu ou une des tiennes)."
        else:
            ok = True
            name = NPC_NAMES.get(npc["class"], npc["class"])
            if mine is not None:
                carrier = self.injections.get(mine["name"])
                where = f" (dans {carrier:06X})" if carrier else " (copiée dans une route libre)"
                why = f"{name} → « {mine['name']} »{where}."
            elif route.ident in self.assignable:
                why = f"{name} → route {route.ident:06X} ({len(route.points)} points)."
            else:
                why = f"{name} → route {route.ident:06X}, copiée dans une route libre (elle n'est rangée dans aucun groupe)."
            if npc["record"] is None:
                why += " Attention : ce PNJ n'a pas de fiche de route, essai non vérifié."
            missing = self.missing_anims(npc["class"], route, mine)
            if missing:
                why += f" ⚠ T-pose probable : {', '.join(missing)} absente(s) pour ce type."
        self.assign_button.setEnabled(ok)
        self.unassign_button.setEnabled(bool(self.live_allowed() and npc and npc["actor"] in self.npc_backup))
        self.assign_info.setText(why)

    # -------------------------------------------------- animations par type de PNJ
    def anim_support(self) -> dict:
        data = {k: {kind: dict(v) for kind, v in kinds.items()} for k, kinds in ANIM_SUPPORT_KNOWN.items()}
        for klass, kinds in _read_json(ANIM_SUPPORT_FILE).items():
            for kind, codes in kinds.items():
                data.setdefault(klass, {}).setdefault(kind, {}).update(codes)
        return data

    def target_codes(self, route, mine) -> tuple[set[int], set[int]]:
        """(allures, actions) utilisees par la route a attribuer."""
        if mine is not None:
            pts = [upgrade_point(dict(p)) for p in mine["points"]]
            return {p["move"] for p in pts}, {p["action"] for p in pts}
        if route is not None:
            return {p.move for p in route.points}, {p.action for p in route.points}
        return set(), set()

    def missing_anims(self, klass: str, route, mine) -> list[str]:
        """Allures / actions de la route marquees « T-pose » pour cette classe."""
        support = self.anim_support().get(klass, {})
        moves, actions = self.target_codes(route, mine)
        out = [f"allure {c}" for c in sorted(moves) if support.get("move", {}).get(str(c)) is False]
        out += [f"action {c}" for c in sorted(actions) if support.get("action", {}).get(str(c)) is False]
        return out

    def edit_anim_support(self):
        """Fenetre : pour la classe du PNJ choisi, ce qui marche ou non dans la route visee."""
        npc = next((n for n in self.soldiers if n["actor"] == self.npc_combo.currentData()), None)
        route, mine = self.assign_target()
        if npc is None:
            return
        klass = npc["class"]
        moves, actions = self.target_codes(route, mine)
        support = self.anim_support().get(klass, {})
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Animations : {NPC_NAMES.get(klass, klass)}")
        dialog.setStyleSheet(STYLE)
        outer = QVBoxLayout(dialog)
        note = QLabel(f"Pour les {NPC_NAMES.get(klass, klass)} ({klass}) : note ce que tu as constaté en jeu. "
                      "« T-pose » = l'animation manque, l'éditeur préviendra avant d'attribuer une route qui l'utilise.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        outer.addWidget(note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        names = self.action_names()
        combos = {}
        rows = [("move", c, f"Allure {c} · {KNOWN_MOVES.get(c, 'inconnue')}") for c in sorted(moves)]
        rows += [("action", c, f"Action {self.action_label(c, names)}") for c in sorted(actions)]
        if not rows:
            rows = [("move", c, f"Allure {c} · {n}") for c, n in sorted(KNOWN_MOVES.items())]
        for k, (kind, code, text) in enumerate(rows):
            grid.addWidget(QLabel(text), k, 0)
            combo = QComboBox()
            for label, value in (("inconnu", None), ("fonctionne", True), ("T-pose", False)):
                combo.addItem(label, value)
            combo.setCurrentIndex(combo.findData(support.get(kind, {}).get(str(code))))
            grid.addWidget(combo, k, 1)
            combos[(kind, code)] = combo
        outer.addLayout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(dialog.reject)
        ok = QPushButton("Enregistrer")
        ok.setObjectName("outline")
        ok.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        outer.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = _read_json(ANIM_SUPPORT_FILE)
        entry = data.setdefault(klass, {})
        for (kind, code), combo in combos.items():
            value = combo.currentData()
            codes = entry.setdefault(kind, {})
            if value is None:
                codes.pop(str(code), None)
            else:
                codes[str(code)] = value
        try:
            os.makedirs(os.path.dirname(ANIM_SUPPORT_FILE), exist_ok=True)
            with open(ANIM_SUPPORT_FILE, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
        except OSError:
            pass
        self.update_assign()

    def carry(self, key: str, data: bytes) -> int | None:
        """Met ces points dans une route libre attribuable (la meme pour la meme cle)
        et renvoie son identifiant ; None si aucune route libre ou si le jeu refuse."""
        carrier = self.injections.get(key) or self.free_carrier()
        if carrier is None:
            self.live_message("Aucune route de PNJ libre dans cette zone pour porter cette route.")
            return None
        if carrier not in self.inject_backup:
            entry = self.link.route_entry(carrier)
            if entry is None:
                self.live_message(f"Route porteuse {carrier:06X} introuvable : rien n'est écrit.")
                return None
            self.inject_backup[carrier] = entry
        if not self.link.inject_route(carrier, data):
            self.live_message("Injection refusée (mémoire du jeu) : rien n'est écrit dans la table des routes.")
            return None
        self.injections[key] = carrier
        return carrier

    def assign_route(self):
        npc = next((n for n in self.soldiers if n["actor"] == self.npc_combo.currentData()), None)
        route, mine = self.assign_target()
        if not (self.live_allowed() and npc and (route or mine)):
            return
        # verification : l'acteur est toujours le meme (classe)
        if self.link.class_name(npc["actor"]) != npc["class"]:
            self.live_message("Ce PNJ n'existe plus : rien n'est écrit.")
            return
        if npc["record"] is None and QMessageBox.question(
                self, "PNJ sans fiche de route",
                "Ce PNJ n'a pas de fiche de route (véhicule, poste fixe…). Lui en écrire une n'a jamais été "
                "essayé : le jeu pourrait l'ignorer ou planter. Continuer ?") != QMessageBox.StandardButton.Yes:
            return
        missing = self.missing_anims(npc["class"], route, mine)
        if missing and QMessageBox.question(
                self, "Animations manquantes",
                f"Pour ce type de PNJ, {', '.join(missing)} donne(nt) une T-pose. Attribuer quand même ?"
        ) != QMessageBox.StandardButton.Yes:
            return
        # route a donner : attribuable telle quelle, sinon copiee dans une route libre
        if mine is not None:
            if len(mine["points"]) < 2:
                return
            ident = self.carry(mine["name"], self.route_bytes(mine))
            label = f"« {mine['name']} »"
        elif route.ident in self.assignable:
            ident, label = route.ident, f"La route {route.ident:06X}"
        else:
            location = self.link.route_location(route.ident)
            data = self.link.read_points(*location) if location else None
            if not data:
                self.live_message(f"Route {route.ident:06X} introuvable en mémoire : rien n'est écrit.")
                return
            ident = self.carry(f"jeu:{route.ident:06X}", data)
            label = f"La route {route.ident:06X}"
        if ident is None or ident not in self.assignable:
            return
        if npc["actor"] not in self.npc_backup:
            saved = self.link.read_record(npc["actor"])
            if saved is None:
                return
            self.npc_backup[npc["actor"]] = (npc["class"], saved)
        group, index = self.assignable[ident]
        if self.link.assign_route(npc["actor"], group, index, ident):
            self.live_message("")
            carried = f" (portée par {ident:06X})" if mine is not None or (route and route.ident != ident) else ""
            self.assign_info.setText(f"{label}{carried} est donnée : le PNJ la prend tout de suite, au point 1.")
            self.update_inject_button()
        else:
            self.live_message("Écriture refusée par le jeu.")

    # -------------------------------------------------- injection de mes routes
    def update_inject_button(self):
        mine = self.route()
        ok = bool(self.live_allowed() and self.target_kind == "mine" and mine and len(mine["points"]) >= 2)
        self.inject_button.setEnabled(ok)
        self.inject_button.setText("Mettre à jour dans le jeu" if mine and mine["name"] in self.injections
                                   else "Injecter dans le jeu")

    def route_bytes(self, route: dict) -> bytes:
        """Points de ma route au format du jeu (0x30 octets chacun)."""
        pts = [upgrade_point(dict(p)) for p in route["points"]]
        if route.get("loop") and len(pts) > 2:
            pts.append(pts[0])
        out = bytearray()
        for p in pts:
            look = p["look"]
            if not look:  # sans regard : 10 m devant, selon l'orientation
                a = p["angle"] * 2 * math.pi / 0x10000
                look = (p["x"] + 10000 * math.sin(a), p["y"], p["z"] + 10000 * math.cos(a))
            out += struct.pack("<3fI3f4H3I", p["x"], p["y"], p["z"], p["flags"] & 0xFFFFFFFF, *look, p["action"],
                               p["move"], min(int(p["wait"]), 0xFFFF), p["angle"] & 0xFFFF,
                               p["param"] & 0xFFFFFFFF, p["param2"] & 0xFFFFFFFF, p["param3"] & 0xFFFFFFFF)
        return bytes(out)

    def free_carrier(self) -> int | None:
        """Route de PNJ attribuable que personne n'utilise (ni suivie, ni dans une fiche)."""
        used = {n["route"] for n in self.soldiers if n["route"]}
        slots = {s for n in self.soldiers for s in n.get("slots", [])}
        taken = set(self.injections.values())
        for ident, pair in sorted(self.assignable.items(), key=lambda kv: kv[1]):
            if ident not in used and pair not in slots and ident not in taken:
                return ident
        return None

    def inject_route(self):
        mine = self.route()
        if not (self.live_allowed() and mine and len(mine["points"]) >= 2):
            return
        carrier = self.carry(mine["name"], self.route_bytes(mine))
        if carrier is None:
            return
        self.live_message("")
        self.assign_info.setText(f"« {mine['name']} » est dans le jeu (route {carrier:06X}).")
        self.update_inject_button()

    def reinject_if_needed(self):
        """Ma route deja dans le jeu : la modification y est renvoyee aussitot."""
        mine = self.route()
        if self.live_allowed() and mine and mine["name"] in self.injections:
            self.inject_route()

    def restore_injections(self):
        """Remet les entrees d'origine des routes porteuses (tant que le jeu est dans la meme zone)."""
        for carrier, (address, raw) in list(self.inject_backup.items()):
            entry = self.link.route_entry(carrier)
            if entry and entry[0] == address:
                self.link.write_bytes(address, raw)
        self.injections, self.inject_backup = {}, {}
        self.update_inject_button()

    def restore_npc(self):
        actor = self.npc_combo.currentData()
        if actor not in self.npc_backup:
            return
        klass, saved = self.npc_backup[actor]
        if self.link.class_name(actor) == klass:
            self.link.restore_record(actor, saved)
        del self.npc_backup[actor]
        self.update_assign()

    def draw_animals(self):
        """Un losange jaune par animal."""
        if not self.zone:
            return
        while len(self._animal_items) < len(self.animals):
            item = QGraphicsPolygonItem(QPolygonF([QPointF(0, -7), QPointF(6, 0), QPointF(0, 7), QPointF(-6, 0)]))
            item.setBrush(QBrush(QColor(ANIMAL_COLOR)))
            item.setPen(QPen(QColor("#10151c"), 1.5))
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            item.setZValue(27)
            self.scene.addItem(item)
            self._animal_items.append(item)
        for item, animal in zip(self._animal_items, self.animals):
            x, y, z = animal["position"]
            item.setPos(to_scene(x, z))
            species = ANIMAL_NAMES.get(animal.get("species"), animal.get("species") or "Animal")
            item.setToolTip(f"{species} · hauteur {y:.0f}")
            item.setVisible(self.layer_checks["animals"].isChecked())
        for item in self._animal_items[len(self.animals):]:
            item.setVisible(False)

    def pick_soldier(self, scene_pos: QPointF):
        tolerance = 12 / (abs(self.view.transform().m11()) or 1.0)
        best = None
        for soldier in self.soldiers:
            x, _y, z = soldier["position"]
            d = math.hypot(x + scene_pos.x(), z + scene_pos.y())
            if d <= tolerance and soldier["route"] and (best is None or d < best[0]):
                best = (d, soldier)
        return best[1] if best else None

    def draw_snake(self):
        if not self.zone:
            return
        if self.snake is None:
            if getattr(self, "_snake_item", None) is not None:
                self._snake_item.setVisible(False)
            return
        if getattr(self, "_snake_item", None) is None:
            self._snake_item = QGraphicsEllipseItem(-22, -22, 44, 44)
            self._snake_item.setBrush(QBrush(QColor(230, 233, 239, 40)))
            self._snake_item.setPen(QPen(Qt.PenStyle.NoPen))
            self._snake_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self._snake_item.setZValue(30)
            arrow = QGraphicsPolygonItem(QPolygonF([QPointF(0, -12), QPointF(9, 9), QPointF(0, 4), QPointF(-9, 9)]),
                                         self._snake_item)
            self._snake_arrow = arrow
            arrow.setBrush(QBrush(QColor("#e6e9ef")))
            arrow.setPen(QPen(Qt.PenStyle.NoPen))
            label = QGraphicsSimpleTextItem("Snake", self._snake_item)
            font = QFont("Inter", 9)
            font.setBold(True)
            label.setFont(font)
            label.setBrush(QBrush(QColor("#e6e9ef")))
            label.setPos(-label.boundingRect().width() / 2, 26)
            self.scene.addItem(self._snake_item)
        self._snake_item.setVisible(True)
        self._snake_item.setPos(to_scene(self.snake[0], self.snake[2]))
        # angle 0 = Z croissant = haut de la carte ; Qt tourne dans le sens horaire
        yaw = getattr(self, "snake_yaw", None)
        self._snake_arrow.setRotation(-yaw if yaw is not None else 0.0)

    def cursor_moved(self, pos: QPointF):
        self.status_cursor.setText("Curseur ({:.0f}, {:.0f})".format(*from_scene(pos)))

    def update_status(self):
        if self.zone:
            progress = f" · progression {self.game_location[1]}" if self.game_location and self.game_location[0] == self.zone else ""
            self.status_zone.setText(f"{self.zone}{progress}")
        if self.snake:
            self.status_snake.setText("Snake ({:.0f}, {:.0f}, {:.0f})".format(*self.snake))
            if self.soldiers:
                self.status_snake.setText(self.status_snake.text() + f" · {len(self.soldiers)} PNJ")
        elif self.game_location:
            self.status_snake.setText(f"Snake en {self.game_location[0]}")
        else:
            self.status_snake.setText("Snake : jeu non lancé")
        points = sum(len(r["points"]) for r in self.routes)
        game = len(self.geom.routes) if self.geom else 0
        self.status_count.setText(f"{len(self.routes)} route(s) · {points} point(s) · {game} routes du jeu · non injectées")

    def closeEvent(self, event):
        self.save_routes()
        if (self.live_backup or self.inject_backup) and self.game_location and (
                self.game_location[0] == self.live_zone or self.inject_backup):
            answer = QMessageBox.question(self, "Routes du jeu modifiées",
                                          "Rétablir les routes du jeu modifiées avant de quitter ?")
            if answer == QMessageBox.StandardButton.Yes:
                self.restore_game_routes(current_only=False)
        self.view3d.release_gl(final=True)  # textures liberees tant que la vue existe
        super().closeEvent(event)


CRASH_LOG = os.path.join(DATA_DIR, "logs", "editeur.log")


def install_crash_log():
    """Journal qui survit a un plantage : piles de tous les fils en cas de
    plantage natif (Qt, OpenGL...), erreurs Python non rattrapees,
    avertissements de Qt. Garde les 2 derniers lancements."""
    import datetime
    import faulthandler
    import traceback
    from PySide6.QtCore import qInstallMessageHandler
    os.makedirs(os.path.dirname(CRASH_LOG), exist_ok=True)
    try:
        if os.path.exists(CRASH_LOG):
            os.replace(CRASH_LOG, CRASH_LOG + ".precedent")
    except OSError:
        pass
    log = open(CRASH_LOG, "a", encoding="utf-8", buffering=1)
    log.write(f"=== lancement {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
    faulthandler.enable(file=log, all_threads=True)

    def excepthook(kind, value, tb):
        log.write(f"--- erreur Python {datetime.datetime.now():%H:%M:%S}\n" + "".join(traceback.format_exception(kind, value, tb)))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = excepthook
    threading.excepthook = lambda args: excepthook(args.exc_type, args.exc_value, args.exc_traceback)

    def qt_message(mode, context, message):
        log.write(f"--- Qt {datetime.datetime.now():%H:%M:%S} {message}\n")
    qInstallMessageHandler(qt_message)
    return log


def main():
    install_crash_log()
    app = QApplication.instance() or QApplication(sys.argv)
    # boutons standard de Qt en francais (Oui / Non, OK / Annuler...)
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    translator = QTranslator(app)
    if translator.load(QLocale(QLocale.Language.French), "qtbase", "_",
                       QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    app.setStyleSheet(STYLE)
    window = RouteEditor()
    window.show()
    code = app.exec()
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as log:
            log.write("=== fermeture normale\n")
    except OSError:
        pass
    return code
