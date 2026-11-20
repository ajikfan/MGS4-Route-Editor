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

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGraphicsEllipseItem,
    QGraphicsItem, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsScene, QGraphicsSimpleTextItem,
    QGraphicsView, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from mgs4routes import geom as geomlib
from mgs4routes.game import GameLink
from mgs4routes.vpak import STAGE_ARCHIVES, StageFiles

STAGE_CODE = re.compile(r"s\d\da\d\d\w")
# donnees de l'utilisateur a cote du lanceur (ou de l'exe, une fois empaquete)
DATA_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ROUTES_DIR = os.path.join(DATA_DIR, "routes")
STEAM_GUESSES = (
    r"C:\Program Files (x86)\Steam\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"D:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
    r"F:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4\mgs4",
)
ROUTE_COLORS = ("#f0883e", "#4ea1ff", "#3fb950", "#d2a8ff", "#e5c07b", "#56d4c2", "#ff7b72")
ACTIONS = ("Marcher", "Courir", "S'arrêter et regarder", "S'accroupir")
TOOLS = ("select", "point", "insert", "move", "delete")

STYLE = """
QWidget { background: #0d1117; color: #e6e9ef; font-family: Inter, 'Segoe UI', sans-serif; font-size: 13px; }
QFrame#bar, QFrame#side { background: #151a22; }
QFrame#bar { border-bottom: 1px solid #2a313c; }
QFrame#left { background: #151a22; border-right: 1px solid #2a313c; }
QFrame#right { background: #151a22; border-left: 1px solid #2a313c; }
QLabel#section { color: #a3adbb; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel#title { font-size: 15px; font-weight: 700; }
QLabel#hint { color: #a3adbb; font-size: 12px; }
QPushButton { background: #1d232d; border: 1px solid #2f3846; border-radius: 6px; padding: 6px 10px; }
QPushButton:hover { background: #252c38; }
QPushButton:checked { background: #3a2a1c; border: 1px solid #f0883e; color: #ffd2ad; }
QPushButton:disabled { color: #5c6673; border-color: #232a33; }
QPushButton#primary { background: #f0883e; color: #1a1006; font-weight: 700; border: none; padding: 10px; }
QPushButton#primary:disabled { background: #3a2f25; color: #7a6a5c; }
QPushButton#link { border-radius: 14px; padding: 4px 12px; }
QComboBox, QDoubleSpinBox, QSpinBox { background: #0f141a; border: 1px solid #2f3846; border-radius: 6px; padding: 4px 6px; min-height: 22px; }
QListWidget { background: #151a22; border: none; outline: 0; }
QListWidget::item { padding: 6px; border-radius: 6px; }
QListWidget::item:selected { background: #251d16; border: 1px solid #5b3d22; color: #f3e6db; }
QCheckBox { background: transparent; }
QStatusBar { background: #151a22; color: #a3adbb; border-top: 1px solid #2a313c; }
QStatusBar QLabel { background: transparent; color: #a3adbb; font-family: Consolas, monospace; font-size: 12px; padding: 0 8px; }
QGraphicsView { border: none; background: #10151c; }
"""


# ---------------------------------------------------------------- reglages

def _settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
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
    RADIUS = 10

    def __init__(self, editor, route: int, index: int, color: str, selected: bool):
        r = self.RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self.editor, self.route, self.index = editor, route, index
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor("#ffffff"), 2) if selected else QPen(Qt.PenStyle.NoPen))
        self.setZValue(20)
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

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self._pan = None

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


def _swatch(color: str) -> QIcon:
    pix = QPixmap(12, 12)
    pix.fill(QColor(color))
    return QIcon(pix)


class RouteEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Éditeur de routes — MGS4")
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
        self._game_items = {"grid": [], "routes": [], "markers": [], "objects": []}
        self._route_items = []
        self._highlight = None

        self.scene = QGraphicsScene(self)
        self.view = MapView(self.scene)
        self.view.clicked.connect(self.map_clicked)
        self.view.moved.connect(self.cursor_moved)

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
        title = QLabel("Éditeur de routes")
        title.setObjectName("title")
        row.addWidget(title)
        self.zone_combo = QComboBox()
        self.zone_combo.setEditable(True)
        self.zone_combo.setMinimumWidth(150)
        self.zone_combo.setToolTip("Zone affichée (code du jeu, ex. s04a40l)")
        self.zone_combo.activated.connect(lambda _i: self.load_zone(self.zone_combo.currentText().strip()))
        self.zone_combo.lineEdit().returnPressed.connect(lambda: self.load_zone(self.zone_combo.currentText().strip()))
        row.addWidget(self.zone_combo)
        follow = QPushButton("Zone du jeu")
        follow.setToolTip("Afficher la zone où se trouve Snake")
        follow.clicked.connect(self.go_to_game_zone)
        row.addWidget(follow)
        view2d = QPushButton("2D")
        view2d.setCheckable(True)
        view2d.setChecked(True)
        view3d = QPushButton("3D")
        view3d.setEnabled(False)
        view3d.setToolTip("À venir")
        row.addWidget(view2d)
        row.addWidget(view3d)
        row.addSpacing(12)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        labels = {"select": ("Sélection", "Choisir un point"),
                  "point": ("Point", "Clic : ajouter un point à la route choisie ; Alt+clic : à la position de Snake"),
                  "insert": ("Ligne", "Clic sur un segment : insérer un point"),
                  "move": ("Déplacer", "Glisser un point"),
                  "delete": ("Supprimer", "Clic sur un point : le supprimer")}
        for key in TOOLS:
            text, tip = labels[key]
            button = QPushButton(text)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setChecked(key == self.tool)
            button.clicked.connect(lambda _c=False, k=key: self.set_tool(k))
            self.tool_group.addButton(button)
            row.addWidget(button)
        row.addStretch(1)
        self.link_label = QPushButton("Jeu non connecté")
        self.link_label.setObjectName("link")
        self.link_label.setToolTip("Le jeu est relu toutes les demi-secondes")
        row.addWidget(self.link_label)
        return bar

    def _build_left(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("left")
        panel.setFixedWidth(280)
        col = QVBoxLayout(panel)
        col.setContentsMargins(12, 16, 12, 16)
        col.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_section("Mes routes"))
        head.addStretch(1)
        new = QPushButton("+ Nouvelle")
        new.clicked.connect(self.new_route)
        head.addWidget(new)
        col.addLayout(head)
        self.route_list = QListWidget()
        self.route_list.setMaximumHeight(200)
        self.route_list.currentRowChanged.connect(self.choose_route)
        self.route_list.itemChanged.connect(self.rename_route)
        col.addWidget(self.route_list)
        buttons = QHBoxLayout()
        delete = QPushButton("Supprimer la route")
        delete.clicked.connect(self.delete_route)
        buttons.addWidget(delete)
        col.addLayout(buttons)
        col.addSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_section("Routes du jeu"))
        head.addStretch(1)
        hint = QLabel("lecture seule")
        hint.setObjectName("hint")
        head.addWidget(hint)
        col.addLayout(head)
        self.game_list = QListWidget()
        self.game_list.currentRowChanged.connect(self.highlight_game_route)
        col.addWidget(self.game_list, stretch=1)
        copy = QPushButton("Copier dans mes routes")
        copy.setToolTip("Partir d'une route du jeu pour en faire une à soi")
        copy.clicked.connect(self.copy_game_route)
        col.addWidget(copy)
        col.addSpacing(8)
        col.addWidget(_section("Calques"))
        self.layer_checks = {}
        for key, text in (("grid", "Grille de la zone"), ("routes", "Routes du jeu"),
                          ("markers", "Repères"), ("objects", "Objets de collision")):
            check = QCheckBox(text)
            check.setChecked(key != "objects")
            check.toggled.connect(lambda on, k=key: self.show_layer(k, on))
            self.layer_checks[key] = check
            col.addWidget(check)
        walls = QCheckBox("Murs et sol (à venir)")
        walls.setEnabled(False)
        col.addWidget(walls)
        return panel

    def _build_right(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("right")
        panel.setFixedWidth(320)
        col = QVBoxLayout(panel)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(10)
        col.addWidget(_section("Point sélectionné"))
        self.point_title = QLabel("Aucun point")
        self.point_title.setObjectName("title")
        col.addWidget(self.point_title)
        grid = QGridLayout()
        self.coord = {}
        for i, (key, text) in enumerate((("x", "X"), ("y", "Hauteur"), ("z", "Z"))):
            grid.addWidget(QLabel(text), 0, i)
            spin = QDoubleSpinBox()
            spin.setRange(-1e7, 1e7)
            spin.setDecimals(0)
            spin.setSingleStep(100)
            spin.valueChanged.connect(lambda v, k=key: self.edit_point(k, v))
            grid.addWidget(spin, 1, i)
            self.coord[key] = spin
        col.addLayout(grid)
        col.addWidget(QLabel("Action au point"))
        self.action_combo = QComboBox()
        self.action_combo.addItems(ACTIONS)
        self.action_combo.setToolTip("Enregistrée avec la route ; pas encore transmise au jeu")
        self.action_combo.currentTextChanged.connect(lambda t: self.edit_point("action", t))
        col.addWidget(self.action_combo)
        grid = QGridLayout()
        grid.addWidget(QLabel("Attente (s)"), 0, 0)
        grid.addWidget(QLabel("Regard (°)"), 0, 1)
        self.wait_spin = QSpinBox()
        self.wait_spin.setRange(0, 600)
        self.wait_spin.valueChanged.connect(lambda v: self.edit_point("wait", v))
        self.look_spin = QSpinBox()
        self.look_spin.setRange(0, 359)
        self.look_spin.setWrapping(True)
        self.look_spin.valueChanged.connect(lambda v: self.edit_point("look", v))
        grid.addWidget(self.wait_spin, 1, 0)
        grid.addWidget(self.look_spin, 1, 1)
        col.addLayout(grid)
        row = QHBoxLayout()
        self.tp_button = QPushButton("TP Snake ici")
        self.tp_button.clicked.connect(self.teleport_here)
        self.snake_button = QPushButton("Point = Snake")
        self.snake_button.clicked.connect(self.point_to_snake)
        row.addWidget(self.tp_button)
        row.addWidget(self.snake_button)
        col.addLayout(row)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #2a313c;")
        col.addWidget(line)
        col.addWidget(_section("Attribuer la route"))
        self.loop_check = QCheckBox("Parcours en boucle")
        self.loop_check.toggled.connect(self.edit_loop)
        col.addWidget(self.loop_check)
        npc = QComboBox()
        npc.addItem("PNJ du jeu (à venir)")
        npc.setEnabled(False)
        col.addWidget(npc)
        assign = QPushButton("Attribuer (à venir)")
        assign.setEnabled(False)
        col.addWidget(assign)
        col.addStretch(1)
        inject = QPushButton("Injecter dans le jeu")
        inject.setObjectName("primary")
        inject.setEnabled(False)
        inject.setToolTip("À venir : les routes seront ajoutées à la zone en jeu")
        col.addWidget(inject)
        export = QPushButton("Exporter (JSON)")
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
        self.zone_combo.addItems(sorted(self.geoms))
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
        self.zone_combo.setCurrentText(zone)
        self.geom = geomlib.parse(self.files.read(self.geoms[zone]))
        self.routes = self.read_routes(zone)
        self.current = 0 if self.routes else -1
        self.selected = -1
        self.scene.clear()
        self._game_items = {"grid": [], "routes": [], "markers": [], "objects": []}
        self._route_items = []
        self._highlight = None
        self._snake_item = None
        self.draw_game_layers()
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
        for route in g.routes:
            if not route.points:
                continue
            path = QPainterPath(to_scene(route.points[0].position[0], route.points[0].position[2]))
            for p in route.points[1:]:
                path.lineTo(to_scene(p.position[0], p.position[2]))
            item = self.scene.addPath(path, cosmetic("#6b7685", 1.5, Qt.PenStyle.DashLine))
            item.setToolTip(f"Route du jeu {route.ident:#08x} · {len(route.points)} points")
            item.setZValue(0)
            item.setData(0, route.ident)
            self._game_items["routes"].append(item)
            for p in route.points:
                dot = self.scene.addEllipse(-3, -3, 6, 6, QPen(Qt.PenStyle.NoPen), QBrush(QColor("#8b95a3")))
                dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                dot.setPos(to_scene(p.position[0], p.position[2]))
                dot.setZValue(1)
                self._game_items["routes"].append(dot)
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

    def show_layer(self, key: str, on: bool):
        for item in self._game_items.get(key, ()):
            item.setVisible(on)

    def fit_view(self):
        x0, z0, x1, z1 = self.geom.bounds()
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
            item = QListWidgetItem(_swatch(route["color"]), route["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.route_list.addItem(item)
        self.route_list.setCurrentRow(self.current)
        self.route_list.blockSignals(False)
        self.game_list.blockSignals(True)
        self.game_list.clear()
        for route in self.geom.routes:
            self.game_list.addItem(f"{route.ident:06X} · {len(route.points)} pts")
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

    def rename_route(self, item: QListWidgetItem):
        row = self.route_list.row(item)
        if 0 <= row < len(self.routes):
            self.routes[row]["name"] = item.text().strip() or self.routes[row]["name"]
            self.save_routes()

    def choose_route(self, row: int):
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

    def highlight_game_route(self, row: int):
        if self._highlight is not None:
            self.scene.removeItem(self._highlight)
            self._highlight = None
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
        self.view.centerOn(to_scene(route.points[0].position[0], route.points[0].position[2]))

    # -------------------------------------------------- outils
    def set_tool(self, tool: str):
        self.tool = tool
        self.redraw_routes()

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
            self.link_label.setStyleSheet("")
        else:
            self.game_location = state.get("location")
            here = self.game_location[0] if self.game_location else "?"
            self.link_label.setText(f"● Connecté au jeu · {here}")
            self.link_label.setStyleSheet("color: #8fe3ad; background: #13261b; border: 1px solid #23573a;")
            self.snake = state.get("snake") if self.game_location and self.game_location[0] == self.zone else None
        self.draw_snake()
        self.update_status()
        if self.route() and 0 <= self.selected < len(self.route()["points"]):
            self.tp_button.setEnabled(self.snake is not None)
            self.snake_button.setEnabled(self.snake is not None)

    def draw_snake(self):
        if not self.zone:
            return
        if self.snake is None:
            if getattr(self, "_snake_item", None) is not None:
                self._snake_item.setVisible(False)
            return
        if getattr(self, "_snake_item", None) is None:
            shape = QPolygonF([QPointF(0, -12), QPointF(9, 9), QPointF(0, 4), QPointF(-9, 9)])
            self._snake_item = QGraphicsPolygonItem(shape)
            self._snake_item.setBrush(QBrush(QColor("#e6e9ef")))
            self._snake_item.setPen(QPen(QColor("#10151c"), 1))
            self._snake_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self._snake_item.setZValue(30)
            label = QGraphicsSimpleTextItem("Snake", self._snake_item)
            label.setBrush(QBrush(QColor("#e6e9ef")))
            label.setPos(-16, 12)
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
