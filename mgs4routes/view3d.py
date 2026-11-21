"""Vue 3D de la zone (OpenGL par Qt, sans dependance de plus).

Murs des .nv2 extrudes (pied + hauteur), routes et points, soldats et
Snake. Coordonnees du jeu en millimetres (Y vers le haut) ; la vue
travaille en metres, avec Z inverse pour que, vu du dessus, le nord soit
en haut comme sur la carte 2D.

Souris : glisser (gauche) = tourner, glisser (droit ou milieu) = deplacer,
molette = zoom, double-clic = recentrer sur le point vise."""
import math

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMatrix4x4, QOpenGLFunctions, QSurfaceFormat, QVector3D
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

GL_LINES, GL_TRIANGLES = 0x0001, 0x0004
GL_FLOAT = 0x1406
GL_DEPTH_TEST, GL_BLEND, GL_MULTISAMPLE = 0x0B71, 0x0BE2, 0x809D
GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0302, 0x0303
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_POLYGON_OFFSET_FILL = 0x8037

VERTEX = """
attribute vec3 position;
attribute vec4 color;
uniform mat4 mvp;
varying vec4 v_color;
void main() {
    v_color = color;
    gl_Position = mvp * vec4(position, 1.0);
}
"""
FRAGMENT = """
varying vec4 v_color;
void main() {
    gl_FragColor = v_color;
}
"""

BACKGROUND = (0x10 / 255, 0x15 / 255, 0x1C / 255)
WALL = np.array((0.80, 0.84, 0.90))
LIGHT = np.array((0.45, 0.0, 0.89))  # lumiere rasante (plan horizontal), normalisee plus bas
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def gl_point(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Jeu (mm) -> vue (m)."""
    return x / 1000.0, y / 1000.0, -z / 1000.0


def _rgba(color, alpha: float = 1.0) -> tuple[float, float, float, float]:
    if isinstance(color, str):
        color = color.lstrip("#")
        return int(color[0:2], 16) / 255, int(color[2:4], 16) / 255, int(color[4:6], 16) / 255, alpha
    return (*color[:3], color[3] if len(color) > 3 else alpha)


class Mesh:
    """Tampon de sommets (x, y, z, r, g, b, a) dessine en lignes ou triangles."""

    def __init__(self, mode: int):
        self.mode = mode
        self.buffer = None
        self.count = 0
        self.pending = None  # tableau a envoyer au prochain dessin

    def set(self, array: np.ndarray):
        self.pending = np.ascontiguousarray(array, dtype=np.float32).reshape(-1, 7)

    def upload(self):
        if self.pending is None:
            return
        if self.buffer is None:
            self.buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            self.buffer.create()
        self.buffer.bind()
        data = self.pending.tobytes()
        self.buffer.allocate(data, len(data))
        self.buffer.release()
        self.count = len(self.pending)
        self.pending = None

    def draw(self, gl: QOpenGLFunctions, program: QOpenGLShaderProgram):
        self.upload()
        if not self.count:
            return
        self.buffer.bind()
        program.enableAttributeArray(0)
        program.enableAttributeArray(1)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 28)
        program.setAttributeBuffer(1, GL_FLOAT, 12, 4, 28)
        gl.glDrawArrays(self.mode, 0, self.count)
        self.buffer.release()


def wall_meshes(walls, ground: float | None = None, cut: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Triangles (faces eclairees, plus claires en haut) et aretes du haut.
    walls : [(x1, z1, x2, z2, pied, hauteur)] en mm ; ground (mm) et cut (m) :
    murs coupes a cut metres au-dessus du sol (ou de leur pied s'il est plus
    haut), et pas plus bas que le sol, comme un plan d'architecte."""
    if not walls:
        return np.zeros((0, 7), np.float32), np.zeros((0, 7), np.float32)
    w = np.asarray(walls, dtype=np.float64)
    x1, z1, x2, z2 = w[:, 0] / 1000, -w[:, 1] / 1000, w[:, 2] / 1000, -w[:, 3] / 1000
    y0 = w[:, 4] / 1000
    y1 = y0 + w[:, 5] / 1000
    if cut is not None and ground is not None:
        g = ground / 1000
        y0 = np.maximum(y0, g - 0.1)
        y1 = np.minimum(y1, np.maximum(y0, g) + cut)
        keep = y1 > y0 + 0.05
        x1, z1, x2, z2, y0, y1 = x1[keep], z1[keep], x2[keep], z2[keep], y0[keep], y1[keep]
        if not len(x1):
            return np.zeros((0, 7), np.float32), np.zeros((0, 7), np.float32)
    dx, dz = x2 - x1, z2 - z1
    length = np.maximum(np.hypot(dx, dz), 1e-6)
    nx, nz = -dz / length, dx / length
    shade = 0.55 + 0.45 * np.abs(nx * LIGHT[0] + nz * LIGHT[2])
    n = len(x1)
    bottom = (shade[:, None] * WALL[None, :] * 0.72)
    top = (shade[:, None] * WALL[None, :])
    tri = np.zeros((n, 6, 7), np.float32)
    corners = (
        (x1, y0, z1, bottom), (x2, y0, z2, bottom), (x2, y1, z2, top),
        (x1, y0, z1, bottom), (x2, y1, z2, top), (x1, y1, z1, top),
    )
    for k, (cx, cy, cz, col) in enumerate(corners):
        tri[:, k, 0], tri[:, k, 1], tri[:, k, 2] = cx, cy, cz
        tri[:, k, 3:6] = col
        tri[:, k, 6] = 0.92
    edges = np.zeros((n, 2, 7), np.float32)
    for k, (cx, cz) in enumerate(((x1, z1), (x2, z2))):
        edges[:, k, 0], edges[:, k, 1], edges[:, k, 2] = cx, y1, cz
        edges[:, k, 3:7] = (0.95, 0.97, 1.0, 0.9)
    return tri.reshape(-1, 7), edges.reshape(-1, 7)


class View3D(QOpenGLWidget):
    """Vue 3D orbitale."""
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setMinimumSize(200, 200)
        self.gl = None
        self.program = None
        self.walls = Mesh(GL_TRIANGLES)
        self.wall_edges = Mesh(GL_LINES)
        self.grid = Mesh(GL_LINES)
        self.lines = Mesh(GL_LINES)
        self.target = np.array((0.0, 0.0, 0.0))
        self.yaw, self.pitch, self.distance = 0.0, math.radians(55), 80.0
        self._drag = None
        self._button = None
        self._walls, self._ground = [], 0.0
        self.cut = 4.0  # metres au-dessus du sol ; None = hauteur reelle
        self.hint = QLabel("Glisser : tourner · clic droit : déplacer · molette : zoom · double-clic : vue de dessus", self)
        self.hint.setObjectName("overlay")
        self.tools = QWidget(self)
        row = QHBoxLayout(self.tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.cut_button = QPushButton("Murs coupés à 4 m")
        self.cut_button.setCheckable(True)
        self.cut_button.setChecked(True)
        self.cut_button.setToolTip("Coupés : on voit les routes entre les murs.\nDécoché : hauteur réelle des murs.")
        self.cut_button.toggled.connect(self.set_cut)
        row.addWidget(self.cut_button)

    # -------------------------------------------------- donnees
    def set_cut(self, on: bool):
        self.cut = 4.0 if on else None
        self.cut_button.setText("Murs coupés à 4 m" if on else "Murs : hauteur réelle")
        self.set_walls(self._walls, self._ground)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.hint.adjustSize()
        self.hint.move(16, 16)
        self.tools.adjustSize()
        self.tools.move(self.width() - self.tools.width() - 16, 16)

    def mouseDoubleClickEvent(self, event):
        self.pitch = math.radians(89)
        self.yaw = 0.0
        self.update()

    def set_walls(self, walls, ground: float):
        self._walls, self._ground = walls, ground
        tri, edges = wall_meshes(walls, ground, self.cut)
        self.walls.set(tri)
        self.wall_edges.set(edges)
        self.set_grid(walls, ground)
        self.update()

    def set_grid(self, walls, ground: float):
        """Quadrillage de 10 m au niveau du sol, sur l'etendue des murs."""
        if not walls:
            self.grid.set(np.zeros((0, 7), np.float32))
            return
        w = np.asarray(walls, dtype=np.float64)
        x0, x1 = w[:, [0, 2]].min() / 1000, w[:, [0, 2]].max() / 1000
        z0, z1 = -w[:, [1, 3]].max() / 1000, -w[:, [1, 3]].min() / 1000
        step = 10.0
        y = ground / 1000 - 0.02
        color = (0.17, 0.22, 0.28, 1.0)
        rows = []
        for x in np.arange(math.floor(x0 / step) * step, x1 + step, step):
            rows += [(x, y, z0, *color), (x, y, z1, *color)]
        for z in np.arange(math.floor(z0 / step) * step, z1 + step, step):
            rows += [(x0, y, z, *color), (x1, y, z, *color)]
        self.grid.set(np.array(rows, np.float32))

    def set_overlay(self, polylines, markers):
        """polylines : [(points en mm, couleur, ticks)] ; markers : [((x, y, z) en mm, couleur, taille m)]."""
        rows = []
        for points, color, ticks in polylines:
            rgba = _rgba(color)
            pts = [gl_point(*p) for p in points]
            for a, b in zip(pts, pts[1:]):
                rows += [(*a, *rgba), (*b, *rgba)]
            if ticks:  # petit poteau sur chaque point : on voit sa hauteur
                for p in pts:
                    rows += [(p[0], p[1], p[2], *rgba), (p[0], p[1] + 0.6, p[2], *rgba)]
        for (x, y, z), color, size in markers:
            rgba = _rgba(color)
            px, py, pz = gl_point(x, y, z)
            h = size
            rows += [(px, py, pz, *rgba), (px, py + h, pz, *rgba)]  # corps
            r = size * 0.35
            for ax, az in ((r, 0), (0, r)):  # croix en haut et en bas
                for yy in (py + h, py):
                    rows += [(px - ax, yy, pz - az, *rgba), (px + ax, yy, pz + az, *rgba)]
        self.lines.set(np.array(rows, np.float32) if rows else np.zeros((0, 7), np.float32))
        self.update()

    def look_at(self, x: float, y: float, z: float, distance: float | None = None):
        self.target = np.array(gl_point(x, y, z), dtype=np.float64)
        if distance:
            self.distance = distance
        self.update()

    # -------------------------------------------------- OpenGL
    def initializeGL(self):
        self.gl = QOpenGLFunctions(self.context())
        self.gl.initializeOpenGLFunctions()
        self.program = QOpenGLShaderProgram(self)
        self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERTEX)
        self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT)
        self.program.bindAttributeLocation("position", 0)
        self.program.bindAttributeLocation("color", 1)
        if not self.program.link():
            self.status.emit("Vue 3D : shaders refusés par la carte graphique (" + self.program.log() + ")")

    def resizeGL(self, w: int, h: int):
        ratio = self.devicePixelRatioF()
        self.gl.glViewport(0, 0, int(w * ratio), int(h * ratio))

    def matrix(self) -> QMatrix4x4:
        proj = QMatrix4x4()
        proj.perspective(50.0, max(self.width(), 1) / max(self.height(), 1), 0.2, 5000.0)
        eye = self.target + self.distance * np.array((
            math.cos(self.pitch) * math.sin(self.yaw), math.sin(self.pitch), math.cos(self.pitch) * math.cos(self.yaw)))
        view = QMatrix4x4()
        view.lookAt(QVector3D(*eye), QVector3D(*self.target), QVector3D(0, 1, 0))
        return proj * view

    def paintGL(self):
        gl = self.gl
        gl.glClearColor(*BACKGROUND, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_BLEND)
        gl.glEnable(GL_MULTISAMPLE)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        if not self.program.bind():
            return
        self.program.setUniformValue("mvp", self.matrix())
        self.grid.draw(gl, self.program)
        gl.glEnable(GL_POLYGON_OFFSET_FILL)
        gl.glPolygonOffset(1.0, 1.0)  # aretes au-dessus des faces
        self.walls.draw(gl, self.program)
        gl.glDisable(GL_POLYGON_OFFSET_FILL)
        self.wall_edges.draw(gl, self.program)
        gl.glLineWidth(2.0)
        self.lines.draw(gl, self.program)
        gl.glLineWidth(1.0)
        self.program.release()

    # -------------------------------------------------- souris
    def mousePressEvent(self, event):
        self._drag = event.position()
        self._button = event.button()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        delta = event.position() - self._drag
        self._drag = event.position()
        if self._button == Qt.MouseButton.LeftButton:
            self.yaw -= delta.x() * 0.008
            self.pitch = min(max(self.pitch + delta.y() * 0.008, math.radians(5)), math.radians(89))
        else:  # deplacement dans le plan du sol
            scale = self.distance * 0.0015
            right = np.array((math.cos(self.yaw), 0.0, -math.sin(self.yaw)))
            forward = np.array((-math.sin(self.yaw), 0.0, -math.cos(self.yaw)))
            self.target += (-delta.x() * right + delta.y() * forward) * scale
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None

    def wheelEvent(self, event):
        factor = 0.85 if event.angleDelta().y() > 0 else 1 / 0.85
        self.distance = min(max(self.distance * factor, 3.0), 3000.0)
        self.update()

    def reset_orientation(self):
        self.yaw, self.pitch = 0.0, math.radians(55)
        self.update()
