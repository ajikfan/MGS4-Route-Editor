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
import os
import re
import struct
import sys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from mgs4routes import geom as geomlib
from mgs4routes.game import NAV_TYPE, GameLink
from mgs4routes.collision import MeshCache, clip_below
from mgs4routes.nav import CellCache, NavIndex
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
DECOR_CUT = 3000  # mm au-dessus du niveau affiche : le decor plus haut est coupe (toits)
DECOR_PIXELS, DECOR_MM_PER_PIXEL = 6000, 50  # image du decor en 2D : taille maximale, finesse
ACTION_NAMES_FILE = os.path.join(DATA_DIR, "action_names.json")
ZONE_BLOCKS_FILE = os.path.join(DATA_DIR, "zone_blocks.json")  # blocs vus en jeu : zone -> progression -> ids
STEAM_GUESSES = (
    r"C:\Program Files (x86)\Steam\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"D:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"F:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
)
ROUTE_COLORS = ("#f0883e", "#4ea1ff", "#3fb950", "#d2a8ff", "#e5c07b", "#56d4c2", "#ff7b72")
ACTIONS = ("Marcher", "Courir", "S'arrêter et regarder", "S'accroupir")
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
                "wait": (0x20, "<H"), "angle": (0x22, "<H")}
# Actions verifiees en jeu (trainer, 2026-11-07) ; les autres codes sont a
# decouvrir : on peut les nommer dans l'editeur (action_names.json).
KNOWN_ACTIONS = {1: "Passage", 3: "Poste debout", 4: "Poste accroupi", 25: "Retour en réserve"}
# Allures relevees en jeu (utilisateur, 2026-11-21)
KNOWN_MOVES = {0: "Marche lente", 1: "Marche lente", 2: "Marche", 3: "Marche soutenue", 4: "Course",
               5: "Course accroupie", 6: "Marche accroupie en visant une cible", 12: "Course",
               19: "Marche accroupie en visant droit devant",
               22: "Marche à reculons accroupie en visant une cible",
               23: "Marche soutenue accroupie en visant droit devant",
               24: "Marche soutenue accroupie en visant une cible",
               25: "Marche à reculons soutenue accroupie en visant une cible"}
WAIT_FOREVER = 0xFFFF
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
    return QPointF(x, -z)


def cosmetic(color: str, width: float = 1.0, style=Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(QColor(color), width, style)
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class PointItem(QGraphicsEllipseItem):
    """Point d'une route de l'utilisateur : taille fixe a l'ecran, numerote."""
    RADIUS = 11

    def __init__(self, editor, route: int, index: int, color: str, selected: bool):
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
        label = QGraphicsSimpleTextItem(str(index + 1), self)
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


class GameRouteRow(QWidget):
    """Ligne de « Routes du jeu » : pointille, identifiant, nombre de points."""

    def __init__(self, ident: int, count: int, visible: bool, on_toggle):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)
        self.check = QCheckBox()
        self.check.setChecked(visible)
        self.check.setToolTip("Afficher sur la carte")
        self.check.toggled.connect(on_toggle)
        row.addWidget(self.check)
        dash = QLabel()
        dash.setFixedSize(12, 2)
        dash.setStyleSheet("border-top: 2px dashed #8b95a3;")
        row.addWidget(dash)
        label = QLabel(f"{ident:06X}")
        label.setStyleSheet(f"font-family: {MONO}; font-size: 12px; color: #c9d1dc;")
        row.addWidget(label, stretch=1)
        self.mark = QLabel("modifiée")
        self.mark.setStyleSheet("color: #f0883e; font-size: 11px; font-weight: 600;")
        self.mark.hide()
        row.addWidget(self.mark)
        pts = QLabel(f"{count} pts")
        pts.setStyleSheet("color: #8b95a3; font-size: 12px;")
        row.addWidget(pts)

    def set_modified(self, on: bool):
        self.mark.setVisible(on)


class RouteEditor(QMainWindow):
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
        self.tool = "point"
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
        self.selected_soldier = None  # acteur suivi sur la carte
        self.dragging_game_point = False
        # edition en direct : gardee quand on change de zone affichee (le jeu,
        # lui, garde les routes modifiees tant qu'il reste dans sa zone)
        self.live = False
        self.live_zone = None         # zone du jeu des sauvegardes ci-dessous
        self.live_backup = {}         # identifiant -> (adresse, octets d'origine)
        self.live_state = {}          # identifiant -> octets ecrits (attendus en memoire)
        self._geom_zone_of_backup = None
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
        self.map_stack = QStackedWidget()
        self.map_stack.addWidget(self.view)
        self.map_stack.addWidget(self.view3d)
        body.addWidget(self.map_stack, stretch=1)
        body.addWidget(self._build_right())
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
        self.live_button = QPushButton("Édition en direct")
        self.live_button.setObjectName("live")
        self.live_button.setCheckable(True)
        self.live_button.setEnabled(False)
        self.live_button.setToolTip("Déplacer les points des routes du jeu pendant la partie.\n"
                                    "Possible quand le jeu est dans la zone affichée.")
        self.live_button.toggled.connect(self.set_live)
        row.addWidget(self.live_button)
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
        col = QVBoxLayout(panel)
        col.setContentsMargins(8, 16, 8, 0)
        col.setSpacing(6)
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
        self.route_list.setMaximumHeight(220)
        self.route_list.currentRowChanged.connect(self.choose_route)
        self.route_list.itemDoubleClicked.connect(lambda _item: self.rename_route())
        self.route_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.route_list.customContextMenuRequested.connect(self.route_menu)
        self.route_list.setToolTip("Double-clic : renommer · clic droit : renommer, supprimer")
        col.addWidget(self.route_list)
        head = QHBoxLayout()
        head.setContentsMargins(8, 12, 8, 0)
        head.addWidget(_section("Routes du jeu"))
        head.addStretch(1)
        for text, on in (("Tout", True), ("Aucune", False)):
            button = QPushButton(text)
            button.setStyleSheet("padding: 3px 8px; font-size: 12px;")
            button.setToolTip("Afficher toutes les routes du jeu" if on else "Masquer toutes les routes du jeu")
            button.clicked.connect(lambda _c=False, v=on: self.show_all_game_routes(v))
            head.addWidget(button)
        col.addLayout(head)
        self.game_list = QListWidget()
        self.game_list.currentRowChanged.connect(self.highlight_game_route)
        self.game_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.game_list.customContextMenuRequested.connect(self.game_route_menu)
        self.game_list.setToolTip("Clic : la montrer · clic droit : copier dans mes routes")
        col.addWidget(self.game_list, stretch=1)
        layers = QFrame()
        layers.setObjectName("layers")
        layers.setStyleSheet("QFrame#layers { border-top: 1px solid #2a313c; }")
        lay = QVBoxLayout(layers)
        lay.setContentsMargins(8, 14, 8, 14)
        lay.setSpacing(8)
        lay.addWidget(_section("Calques"))
        self.layer_checks = {}
        for key, text in (("soldiers", "Soldats (en direct)"), ("decor", "Décor (formes de collision)"),
                          ("floor", "Sol praticable"),
                          ("walls", "Murs et obstacles"), ("passages", "Passages entre cellules"),
                          ("grid", "Grille de la zone"), ("routes", "Routes du jeu"),
                          ("markers", "Repères"), ("objects", "Objets de collision")):
            check = QCheckBox(text)
            check.setChecked(key not in ("objects", "passages", "markers", "grid"))
            check.toggled.connect(lambda on, k=key: self.show_layer(k, on))
            self.layer_checks[key] = check
            lay.addWidget(check)
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
        col.addWidget(layers)
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
        col.addWidget(_field("PNJ"))
        npc = QComboBox()
        npc.addItem("PNJ du jeu (à venir)")
        npc.setEnabled(False)
        col.addWidget(npc)
        self.loop_check = QCheckBox("Parcours en boucle")
        self.loop_check.toggled.connect(self.edit_loop)
        col.addWidget(self.loop_check)
        assign = QPushButton("Attribuer (à venir)")
        assign.setObjectName("outline")
        assign.setEnabled(False)
        col.addWidget(assign)
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
        inject = QPushButton("Injecter dans le jeu")
        inject.setObjectName("primary")
        inject.setEnabled(False)
        inject.setToolTip("À venir : les routes seront ajoutées à la zone en jeu")
        col.addWidget(inject)
        export = QPushButton("Exporter (JSON)")
        export.setStyleSheet("background: transparent;")
        export.clicked.connect(self.export_routes)
        col.addWidget(export)
        self.enable_point_panel(False)
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
        self.game_flags = QLabel("")
        self.game_flags.setObjectName("hint")
        self.game_flags.setToolTip("Drapeaux du point (+0xC) : 2 = poste de garde ; les autres bits sont inconnus")
        col.addWidget(self.game_flags)
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

    def name_action(self):
        if not self.game_pick:
            return
        r, i = self.game_pick
        code = self.geom.routes[r].points[i].action
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
        """Ecrit le point vise (+0x10) du point choisi d'une route du jeu."""
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
            path.addRect(QRectF(to_scene(x0, z1), to_scene(x1, z0)))
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
            rect = self.scene.addRect(QRectF(to_scene(cx - hx, cz + hz), to_scene(cx + hx, cz - hz)),
                                      cosmetic("#3c4a5b", 1.5), QBrush(QColor(60, 74, 91, 60)))
            rect.setToolTip(f"Objet de collision {obj.ident:#08x} (forme {obj.shape})")
            rect.setZValue(-5)
            self._game_items["objects"].append(rect)
        self._game_route_items = [self.draw_game_route(route) for route in g.routes]
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

    def draw_game_route(self, route) -> list:
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
        shown = (route.ident in self.visible_game_routes() or modified) and self.layer_checks["routes"].isChecked()
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
            center = self.view.mapToScene(self.view.viewport().rect().center())
            self.view3d.look_at(center.x(), self.level, -center.y())
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
            decor = self.layer_checks["decor"].isChecked()
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
            self.walls_3d_dirty = False
        polylines, markers = [], []
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
        for r_index, route in enumerate(self.routes):
            if route.get("hidden") and r_index != self.current:
                continue
            pts = [(p["x"], p["y"], p["z"]) for p in route["points"]]
            if route.get("loop") and len(pts) > 2:
                pts.append(pts[0])
            polylines.append((pts, route["color"], True))
        if self.game_pick is not None:
            r, i = self.game_pick
            markers.append((self.geom.routes[r].points[i].position, "#ffffff", 0.9))
        if self.layer_checks["soldiers"].isChecked():
            for soldier in self.soldiers:
                chosen = soldier["actor"] == self.selected_soldier
                color = ACCENT if chosen else ("#ff5c5c" if soldier["route"] else "#8b95a3")
                markers.append((soldier["position"], color, 1.8))
        if self.snake:
            markers.append((self.snake, "#e6e9ef", 1.8))
        self.view3d.set_overlay(polylines, markers)

    # -------------------------------------------------- edition en direct des routes du jeu
    def live_allowed(self) -> bool:
        return bool(self.game_location and self.game_location[0] == self.zone)

    def set_live(self, on: bool):
        self.live = on and self.live_allowed()
        if self.live_button.isChecked() != self.live:
            self.live_button.blockSignals(True)
            self.live_button.setChecked(self.live)
            self.live_button.blockSignals(False)
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
        allowed = self.live_allowed()
        self.live_button.setEnabled(allowed)
        if self.live and not allowed:
            self.set_live(False)

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
        for key, value in (("x", pos.x()), ("z", -pos.y())):
            self.coord[key].blockSignals(True)
            self.coord[key].setValue(value)
            self.coord[key].blockSignals(False)

    def game_handle_released(self):
        if self.game_pick is None or self._handle is None:
            return
        r, i = self.game_pick
        pos = self._handle.pos()
        y = self.geom.routes[r].points[i].position[1]
        self.write_game_point(r, i, (round(pos.x()), y, round(-pos.y())))
        self.redraw_game_route(r)
        self.show_point()

    def update_live_box(self):
        modified = sorted(self.live_backup) if self.live_zone == self.zone else []
        self.live_box.setVisible(bool(modified))
        self.live_count.setText(", ".join(f"{ident:06X}" for ident in modified))
        current = self.geom.routes[self.game_pick[0]].ident if self.game_pick else None
        self.restore_button.setEnabled(current in self.live_backup)
        for index, route in enumerate(self.geom.routes if self.geom else []):
            item = self.game_list.item(index)
            row = self.game_list.itemWidget(item) if item else None
            if row is not None:
                row.set_modified(route.ident in modified)

    def restore_game_routes(self, current_only: bool):
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

    def show_all_game_routes(self, on: bool):
        self.save_visible_game_routes({r.ident for r in self.geom.routes} if on else set())
        for i in range(self.game_list.count()):
            row = self.game_list.itemWidget(self.game_list.item(i))
            row.check.blockSignals(True)
            row.check.setChecked(on)
            row.check.blockSignals(False)
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
        """Decor vu de dessus : image ombree (eclairage + hauteur), coupee au niveau."""
        import numpy as np
        for item in self._game_items["decor"]:
            self.scene.removeItem(item)
        self._game_items["decor"] = []
        tris = self.decor_triangles()
        if tris is None or not len(tris):
            return
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
            painter.drawPolygon(QPolygonF([QPointF((p[0] - x0) * scale, (z1 - p[2]) * scale) for p in t]))
        painter.end()
        item = self.scene.addPixmap(QPixmap.fromImage(image))
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setScale(1 / scale)
        item.setPos(to_scene(x0, z1))
        item.setZValue(-11.5)
        item.setOpacity(0.7)
        item.setVisible(self.layer_checks["decor"].isChecked())
        self._game_items["decor"].append(item)

    def decor_triangles(self):
        """Formes de collision des blocs de la zone, coupees au-dessus du niveau."""
        import numpy as np
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
        if key in ("walls", "floor", "passages", "decor"):
            self.walls_3d_dirty = True
        self.schedule_3d()
        if key == "soldiers":
            for item in getattr(self, "_soldier_items", ()):
                item.setVisible(on)
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
        rect = QRectF(to_scene(x0 - margin, z1 + margin), to_scene(x1 + margin, z0 - margin))
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
        for index, route in enumerate(self.geom.routes):
            row = GameRouteRow(route.ident, len(route.points), route.ident in shown,
                               lambda on, i=index: self.toggle_game_route(i, on))
            item = QListWidgetItem()
            item.setSizeHint(row.sizeHint())
            self.game_list.addItem(item)
            self.game_list.setItemWidget(item, row)
        self.game_list.blockSignals(False)
        self.update_live_box()

    # -------------------------------------------------- routes de l'utilisateur
    def read_routes(self, zone: str) -> list[dict]:
        try:
            with open(os.path.join(ROUTES_DIR, f"{zone}.json"), encoding="utf-8") as handle:
                return json.load(handle).get("routes", [])
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

    def choose_route(self, row: int):
        self.game_pick = None
        self.draw_pick()
        self.current = row
        self.selected = -1
        self.redraw_routes()
        self.show_point()

    def copy_game_route(self):
        row = self.game_list.currentRow()
        if not 0 <= row < len(self.geom.routes):
            return
        route = self.geom.routes[row]
        points = [{"x": p.position[0], "y": p.position[1], "z": p.position[2], "action": ACTIONS[0], "wait": 0, "look": 0}
                  for p in route.points]
        self.new_route(points, f"Copie de {route.ident:06X}")

    def changed(self):
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
                item = PointItem(self, r_index, i, route["color"], mine and i == self.selected)
                item.setPos(to_scene(p["x"], p["z"]))
                item.setOpacity(1.0 if mine else 0.55)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, mine and self.tool in ("move", "select"))
                self.scene.addItem(item)
                self._route_items.append(item)
        self._building = False

    def highlight_game_route(self, row: int, center: bool = True):
        self.schedule_3d()
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
                d = math.hypot(pt.position[0] - scene_pos.x(), pt.position[2] + scene_pos.y())
                if d <= tolerance:
                    hits.append((r, i))
        if not hits:
            return None
        hits.sort()
        # clic repete au meme endroit : point superpose suivant
        if self.game_pick in hits:
            return hits[(hits.index(self.game_pick) + 1) % len(hits)]
        return min(hits, key=lambda h: math.hypot(self.geom.routes[h[0]].points[h[1]].position[0] - scene_pos.x(),
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
            x, y, z, flags, lx, ly, lz, action, move, wait, angle = struct.unpack_from("<3fI3f4H", data, POINT_SIZE * i)
            fresh = dict(position=(x, y, z), flags=flags, look=(lx, ly, lz), action=action, move=move, wait=wait, angle=angle)
            for key, value in fresh.items():
                if getattr(pt, key) != value:
                    setattr(pt, key, value)
                    changed = True
        if changed and route.ident in self.live_state and self.live_backup[route.ident][0] == location[0]:
            self.live_state[route.ident] = data
        return changed

    def select_game_point(self, r: int, i: int):
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
        self.assign_box.hide()
        self.game_fields.show()
        self.prev_button.setEnabled(len(route.points) > 1)
        self.next_button.setEnabled(len(route.points) > 1)
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
            widget.setEnabled(self.live)
        self.look_snake_button.setEnabled(self.live and self.snake is not None)
        for key, value in zip("xyz", pt.look):
            self.game_look[key].blockSignals(True)
            self.game_look[key].setValue(value)
            self.game_look[key].blockSignals(False)
        self.name_action_button.setEnabled(True)
        self.game_flags.setText(f"Drapeaux 0x{pt.flags:X}" + (" · poste de garde" if pt.flags & 2 else ""))
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
        x, z = scene_pos.x(), -scene_pos.y()
        if isinstance(item, GameHandle):
            return
        point = item if isinstance(item, PointItem) else None
        if point is None and self.tool in ("select", "move", "delete"):
            soldier = self.pick_soldier(scene_pos)
            index = next((i for i, r in enumerate(self.geom.routes) if soldier and r.ident == soldier["route"]), None)
            if index is not None and self.geom.routes[index].points:
                self.selected_soldier = soldier["actor"]
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
        if self.tool in ("select", "move"):
            if point is not None:
                self.selected = point.index
                self.redraw_routes()
                self.show_point()
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
        new = {"x": round(x), "y": round(y), "z": round(z), "action": ACTIONS[0], "wait": 0, "look": 0}
        if self.tool == "insert" and len(pts) >= 2:
            index = self.nearest_segment(x, z) + 1
            pts.insert(index, new)
            self.selected = index
        else:
            pts.append(new)
            self.selected = len(pts) - 1
        self.changed()

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
        p = self.routes[route]["points"][index]
        p["x"], p["z"] = round(pos.x()), round(-pos.y())
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
        self.game_fields.hide()
        self.stack_label.hide()
        self.user_fields.show()
        self.assign_box.show()
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
        widgets = (*self.coord.values(), self.action_combo, self.wait_spin, self.look_spin)
        for widget in widgets:
            widget.blockSignals(True)
        for key, spin in self.coord.items():
            spin.setValue(point[key])
        self.action_combo.setCurrentText(point.get("action", ACTIONS[0]))
        self.wait_spin.setValue(int(point.get("wait", 0)))
        self.look_spin.setValue(int(point.get("look", 0)))
        for widget in widgets:
            widget.blockSignals(False)
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
            here = self.game_location[0] if self.game_location else "?"
            self.link_label.setText(f"●  Connecté au jeu · {here}")
            self._set_pill(True)
            self.snake = state.get("snake") if self.game_location and self.game_location[0] == self.zone else None
        self.draw_snake()
        here = bool(state and self.game_location and self.game_location[0] == self.zone)
        self.soldiers = self.link.soldiers() if here and self.layer_checks["soldiers"].isChecked() else []
        self.live_check_zone()
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
        """Un repere par soldat (rouge : suit une route, gris : sans route)."""
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
            item.setBrush(QBrush(QColor("#ff5c5c" if route else "#8b95a3")))
            text = f"Soldat · hauteur {y:.0f}"
            if route:
                text += f"\nSuit la route {route:06X}, vers le point {soldier['point'] + 1}"
            item.setToolTip(text)
            item.setVisible(True)
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
            self.select_game_point(index, target)
            self.point_title.setText(self.point_title.text().replace("Point ", "Soldat → point ", 1))

    def pick_soldier(self, scene_pos: QPointF):
        tolerance = 12 / (abs(self.view.transform().m11()) or 1.0)
        best = None
        for soldier in self.soldiers:
            x, _y, z = soldier["position"]
            d = math.hypot(x - scene_pos.x(), z + scene_pos.y())
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

    def cursor_moved(self, pos: QPointF):
        self.status_cursor.setText(f"Curseur ({pos.x():.0f}, {-pos.y():.0f})")

    def update_status(self):
        if self.zone:
            progress = f" · progression {self.game_location[1]}" if self.game_location and self.game_location[0] == self.zone else ""
            self.status_zone.setText(f"{self.zone}{progress}")
        if self.snake:
            self.status_snake.setText("Snake ({:.0f}, {:.0f}, {:.0f})".format(*self.snake))
            if self.soldiers:
                self.status_snake.setText(self.status_snake.text() + f" · {len(self.soldiers)} soldats")
        elif self.game_location:
            self.status_snake.setText(f"Snake en {self.game_location[0]}")
        else:
            self.status_snake.setText("Snake : jeu non lancé")
        points = sum(len(r["points"]) for r in self.routes)
        game = len(self.geom.routes) if self.geom else 0
        self.status_count.setText(f"{len(self.routes)} route(s) · {points} point(s) · {game} routes du jeu · non injectées")

    def closeEvent(self, event):
        self.save_routes()
        if self.live_backup and self.game_location and self.game_location[0] == self.live_zone:
            answer = QMessageBox.question(self, "Routes du jeu modifiées",
                                          "Rétablir les routes du jeu modifiées avant de quitter ?")
            if answer == QMessageBox.StandardButton.Yes:
                self.restore_game_routes(current_only=False)
        super().closeEvent(event)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    window = RouteEditor()
    window.show()
    return app.exec()
