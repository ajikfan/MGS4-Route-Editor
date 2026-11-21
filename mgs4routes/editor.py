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
import sys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from mgs4routes import geom as geomlib
from mgs4routes.game import NAV_TYPE, GameLink
from mgs4routes.nav import NavIndex
from mgs4routes.vpak import STAGE_ARCHIVES, StageFiles

STAGE_CODE = re.compile(r"s\d\da\d\d\w")
# donnees de l'utilisateur a cote du lanceur (ou de l'exe, une fois empaquete)
DATA_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ROUTES_DIR = os.path.join(DATA_DIR, "routes")
NAV_INDEX_FILE = os.path.join(DATA_DIR, "cache", "nv2_index.json")
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
MONO = "Consolas"
# premiers caracteres du code de zone -> acte et region (affiche a cote du code)
ACT_NAMES = {"s00": "Prologue", "s01": "Acte 1 · Moyen-Orient", "s02": "Acte 2 · Amérique du Sud",
             "s03": "Acte 3 · Europe de l'Est", "s04": "Acte 4 · Shadow Moses", "s05": "Acte 5 · Outer Haven",
             "s10": "Hors actes"}
TOOL_HINTS = {"select": "Clic : choisir un point",
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
        pts = QLabel(f"{count} pts")
        pts.setStyleSheet("color: #8b95a3; font-size: 12px;")
        row.addWidget(pts)


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
        self._game_items = {"grid": [], "walls": [], "routes": [], "markers": [], "objects": []}
        self._route_items = []
        self._highlight = None

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
        body.addWidget(self.view, stretch=1)
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
        for text, on in (("2D", True), ("3D", False)):
            button = QPushButton(text)
            button.setObjectName("seg")
            button.setCheckable(True)
            button.setChecked(on)
            button.setEnabled(on)
            button.setToolTip("" if on else "À venir")
            button.setStyleSheet("border: none;")
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
        for key, text in (("walls", "Murs et obstacles"), ("grid", "Grille de la zone"), ("routes", "Routes du jeu"),
                          ("markers", "Repères"), ("objects", "Objets de collision")):
            check = QCheckBox(text)
            check.setChecked(key != "objects")
            check.toggled.connect(lambda on, k=key: self.show_layer(k, on))
            self.layer_checks[key] = check
            lay.addWidget(check)
        for text in ("Sol praticable (à venir)", "Décor 3D (à venir)"):
            check = QCheckBox(text)
            check.setEnabled(False)
            lay.addWidget(check)
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
        col.addWidget(_section("Point sélectionné"))
        self.point_title = QLabel("Aucun point")
        self.point_title.setObjectName("big")
        self.point_title.setWordWrap(True)
        col.addWidget(self.point_title)
        col.addSpacing(6)
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
        col.addWidget(_field("Action au point"))
        self.action_combo = QComboBox()
        self.action_combo.addItems(ACTIONS)
        self.action_combo.setToolTip("Enregistrée avec la route ; pas encore transmise au jeu")
        self.action_combo.currentTextChanged.connect(lambda t: self.edit_point("action", t))
        col.addWidget(self.action_combo)
        col.addSpacing(6)
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
        col.addLayout(grid)
        col.addSpacing(6)
        row = QHBoxLayout()
        self.tp_button = QPushButton("TP Snake ici")
        self.tp_button.clicked.connect(self.teleport_here)
        self.snake_button = QPushButton("Point = Snake")
        self.snake_button.clicked.connect(self.point_to_snake)
        row.addWidget(self.tp_button)
        row.addWidget(self.snake_button)
        col.addLayout(row)
        col.addSpacing(10)
        col.addWidget(_separator())
        col.addSpacing(10)
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
        self._game_items = {"grid": [], "walls": [], "routes": [], "markers": [], "objects": []}
        self._route_items = []
        self._highlight = None
        self._pick_item = None
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
        self._game_route_items = []
        shown = self.visible_game_routes()
        for route in g.routes:
            group = []
            self._game_route_items.append(group)
            if not route.points:
                continue
            path = QPainterPath(to_scene(route.points[0].position[0], route.points[0].position[2]))
            for p in route.points[1:]:
                path.lineTo(to_scene(p.position[0], p.position[2]))
            item = self.scene.addPath(path, cosmetic("#6b7685", 1.5, Qt.PenStyle.DashLine))
            item.setToolTip(f"Route du jeu {route.ident:#08x} · {len(route.points)} points")
            item.setZValue(0)
            item.setData(0, route.ident)
            group.append(item)
            for p in route.points:
                dot = self.scene.addEllipse(-3, -3, 6, 6, QPen(Qt.PenStyle.NoPen), QBrush(QColor("#8b95a3")))
                dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                dot.setPos(to_scene(p.position[0], p.position[2]))
                dot.setZValue(1)
                group.append(dot)
            for part in group:
                part.setVisible(route.ident in shown)
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

    # -------------------------------------------------- visibilite des routes du jeu (par zone, gardee)
    def visible_game_routes(self) -> set[int]:
        """Routes du jeu cochees dans cette zone (aucune par defaut)."""
        return set(_settings().get("route_editor_visible", {}).get(self.zone, []))

    def save_visible_game_routes(self, idents: set[int]):
        data = _settings().get("route_editor_visible", {})
        data[self.zone] = sorted(idents)
        _save_setting("route_editor_visible", data)

    def apply_game_route_visibility(self):
        shown = self.visible_game_routes()
        layer = self.layer_checks["routes"].isChecked()
        for route, group in zip(self.geom.routes, self._game_route_items):
            for part in group:
                part.setVisible(layer and route.ident in shown)

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
        for item in self._game_items["walls"]:
            self.scene.removeItem(item)
        self._game_items["walls"] = []
        paths, source = self.zone_blocks()
        lines = QPainterPath()
        count = 0
        for path in paths:
            for x1, z1, x2, z2, _y, _h in self.nav.walls(path):
                lines.moveTo(to_scene(x1, z1))
                lines.lineTo(to_scene(x2, z2))
                count += 1
        item = self.scene.addPath(lines, cosmetic("#d7dde6", 1.2))
        item.setZValue(-6)
        item.setVisible(self.layer_checks["walls"].isChecked())
        self._game_items["walls"].append(item)
        names = ", ".join(p.split("/")[-1][:8] for p in paths[:6]) + ("…" if len(paths) > 6 else "")
        text = f"Carte : {len(paths)} bloc(s), {count} murs · {source}"
        self.blocks_label.setText(text + ("\n" + names if paths else ""))

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
        best = None
        for r, route in enumerate(self.geom.routes):
            if route.ident not in shown and r != current:
                continue
            for i, pt in enumerate(route.points):
                d = math.hypot(pt.position[0] - scene_pos.x(), pt.position[2] + scene_pos.y())
                if d <= tolerance and (best is None or d < best[0]):
                    best = (d, r, i)
        return best[1:] if best else None

    def select_game_point(self, r: int, i: int):
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
        if self._pick_item is not None:
            self.scene.removeItem(self._pick_item)
            self._pick_item = None
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

    def show_game_point(self):
        r, i = self.game_pick
        route = self.geom.routes[r]
        pt = route.points[i]
        self.enable_point_panel(False)
        self.loop_check.setEnabled(False)
        self.point_title.setText(f"Point {i + 1} / {len(route.points)} · route du jeu {route.ident:06X} (lecture seule)")
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
        point = item if isinstance(item, PointItem) else None
        if point is None and self.tool in ("select", "move", "delete"):
            hit = self.pick_game_point(scene_pos)
            if hit:
                self.select_game_point(*hit)
                return
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
        elif self.game_location:
            self.status_snake.setText(f"Snake en {self.game_location[0]}")
        else:
            self.status_snake.setText("Snake : jeu non lancé")
        points = sum(len(r["points"]) for r in self.routes)
        game = len(self.geom.routes) if self.geom else 0
        self.status_count.setText(f"{len(self.routes)} route(s) · {points} point(s) · {game} routes du jeu · non injectées")

    def closeEvent(self, event):
        self.save_routes()
        super().closeEvent(event)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    window = RouteEditor()
    window.show()
    return app.exec()
