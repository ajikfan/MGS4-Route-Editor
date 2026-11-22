"""Vue 3D de la zone (OpenGL par Qt, sans dependance de plus).

Murs des .nv2 extrudes (pied + hauteur), routes et points, soldats et
Snake. Coordonnees du jeu en millimetres (Y vers le haut) ; la vue
travaille en metres, avec X et Z inverses pour que, vu du dessus, l'image
soit celle du jeu (comme la carte 2D).

Souris : glisser (gauche) = tourner, glisser (droit ou milieu) = deplacer,
molette = zoom, double-clic = recentrer sur le point vise."""
from mgs4routes.i18n import tr
from mgs4routes.textures import FORMAT_DXT1, FORMAT_DXT5, decode_dxt
import math

import numpy as np
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QMatrix4x4, QOpenGLFunctions, QSurfaceFormat, QVector3D, QVector4D
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication, QLabel

GL_LINES, GL_TRIANGLES = 0x0001, 0x0004
GL_FLOAT = 0x1406
GL_DEPTH_TEST, GL_BLEND, GL_MULTISAMPLE = 0x0B71, 0x0BE2, 0x809D
GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0302, 0x0303
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_POLYGON_OFFSET_FILL = 0x8037
GL_LESS, GL_GREATER = 0x0201, 0x0204

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

# Decor texture (modeles affiches du jeu) : position, coordonnees de texture, eclairage
TEX_VERTEX = """
attribute vec3 position;
attribute vec2 uv;
attribute vec4 light;
uniform mat4 mvp;
varying vec2 v_uv;
varying vec4 v_light;
varying float v_y;
void main() {
    v_uv = uv;
    v_light = light;
    v_y = position.y;
    gl_Position = mvp * vec4(position, 1.0);
}
"""
TEX_FRAGMENT = """
uniform sampler2D tex;
uniform float blend;
uniform float cut;
varying vec2 v_uv;
varying vec4 v_light;
varying float v_y;
void main() {
    if (v_y > cut) discard;  // coupe au-dessus du niveau affiche (toits)
    vec4 c = texture2D(tex, v_uv);
    if (blend < 0.5) {
        if (c.a < 0.35) discard;
        gl_FragColor = vec4(c.rgb * v_light.rgb, 1.0);
    } else {
        float a = c.a * v_light.a;
        if (a < 0.02) discard;
        gl_FragColor = vec4(c.rgb * v_light.rgb, a);
    }
}
"""

BACKGROUND = (0x10 / 255, 0x15 / 255, 0x1C / 255)
WALL = np.array((0.80, 0.84, 0.90))
LIGHT = np.array((0.45, 0.0, 0.89))  # lumiere rasante (plan horizontal), normalisee plus bas
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def gl_point(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Jeu (mm) -> vue (m)."""
    return -x / 1000.0, y / 1000.0, -z / 1000.0


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
    x1, z1, x2, z2 = -w[:, 0] / 1000, -w[:, 1] / 1000, -w[:, 2] / 1000, -w[:, 3] / 1000
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


def decor_mesh(tris) -> np.ndarray:
    """Triangles du decor eclaires (normale de chaque face), un peu plus clairs en hauteur."""
    if tris is None or not len(tris):
        return np.zeros((0, 7), np.float32)
    t = np.asarray(tris, dtype=np.float64) / 1000.0
    t[:, :, 0] *= -1.0  # X et Z inverses : vu de dessus, comme dans le jeu
    t[:, :, 2] *= -1.0
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    light = np.array((0.35, 0.85, 0.40))
    light /= np.linalg.norm(light)
    lit = 0.45 + 0.55 * np.abs(n @ light)
    y = t[:, :, 1].mean(axis=1)
    h = np.clip((y - np.percentile(y, 5)) / 15.0, 0.0, 1.0)
    up = np.abs(n[:, 1]) > 0.7  # sols, dessus : teinte a part des parois
    ground = np.array((0.33, 0.39, 0.47))
    wall = np.array((0.66, 0.69, 0.74))
    base = np.where(up[:, None], ground[None, :], wall[None, :])
    col = (lit * (0.85 + 0.3 * h))[:, None] * base
    out = np.zeros((len(t), 3, 7), np.float32)
    out[:, :, 0:3] = t
    out[:, :, 3:6] = np.clip(col, 0, 1)[:, None, :]
    out[:, :, 6] = 1.0
    return out.reshape(-1, 7)


def floor_mesh(floors) -> np.ndarray:
    """Triangles des sols (polygones convexes en eventail), teintes selon la hauteur."""
    if not floors:
        return np.zeros((0, 7), np.float32)
    heights = [y for _poly, y in floors]
    low, high = min(heights), max(heights)
    span = max(high - low, 1.0)
    rows = []
    for poly, y in floors:
        t = (y - low) / span
        color = (0.13 + 0.16 * t, 0.19 + 0.17 * t, 0.26 + 0.16 * t, 1.0)  # plus clair en hauteur
        pts = [(-x / 1000.0, y / 1000.0, -z / 1000.0) for x, z in poly]
        for a, b in zip(pts[1:], pts[2:]):
            rows += [(*pts[0], *color), (*a, *color), (*b, *color)]
    return np.array(rows, np.float32)


def _octagon(n: int = 8):
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.cos(a), np.sin(a)


def actor_meshes(actors) -> tuple[np.ndarray, np.ndarray]:
    """Silhouettes pleines (corps, tete, disque et cone d'orientation au sol) et
    leur fantome translucide (dessine seulement la ou un decor les cache).
    actors : [{"position" (mm), "color", "height" (m), "yaw" (radians, 0 = +Z du jeu) ou None, "selected"}]."""
    solid, ghost = [], []
    light = np.array((0.35, 0.85, 0.40))
    light /= np.linalg.norm(light)

    def tri(out, a, b, c, rgb, alpha, lit=True):
        k = 1.0
        if lit:
            n = np.cross(np.subtract(b, a), np.subtract(c, a))
            n = n / (np.linalg.norm(n) or 1.0)
            k = 0.55 + 0.45 * abs(float(n @ light))
        col = (*(np.clip(np.asarray(rgb) * k, 0, 1)), alpha)
        out += [(*a, *col), (*b, *col), (*c, *col)]

    cx, sx = _octagon(10)
    n = len(cx)
    for actor in actors:
        x, y, z = gl_point(*actor["position"])
        rgb = np.array(_rgba(actor["color"])[:3])
        h = actor.get("height", 1.8)
        r = 0.16 * h
        body_top, head_c, head_r = y + h * 0.74, y + h * 0.87, 0.1 * h
        for out, alpha in ((solid, 1.0), (ghost, 0.35)):
            for i in range(n):
                j = (i + 1) % n
                ax, az, bx, bz = x + r * cx[i], z + r * sx[i], x + r * cx[j], z + r * sx[j]
                tri(out, (ax, y + 0.03, az), (bx, y + 0.03, bz), (bx, body_top, bz), rgb, alpha)
                tri(out, (ax, y + 0.03, az), (bx, body_top, bz), (ax, body_top, az), rgb, alpha)
                tri(out, (x, body_top, z), (ax, body_top, az), (bx, body_top, bz), rgb, alpha)
                hx0, hz0 = x + head_r * cx[i], z + head_r * sx[i]
                hx1, hz1 = x + head_r * cx[j], z + head_r * sx[j]
                tri(out, (hx0, head_c, hz0), (hx1, head_c, hz1), (x, head_c + head_r * 1.2, z), rgb * 1.1, alpha)
                tri(out, (hx0, head_c, hz0), (hx1, head_c, hz1), (x, head_c - head_r * 1.2, z), rgb * 0.9, alpha)
            ring = 0.55 * h / 1.8 * (1.35 if actor.get("selected") else 1.0)
            for i in range(n):  # disque au sol
                j = (i + 1) % n
                tri(out, (x, y + 0.02, z), (x + ring * cx[i], y + 0.02, z + ring * sx[i]),
                    (x + ring * cx[j], y + 0.02, z + ring * sx[j]), rgb, 0.35 if out is solid else 0.2, lit=False)
            yaw = actor.get("yaw")
            if yaw is not None:  # cone d'orientation (avant du jeu = (sin, cos) ; vue : X et Z inverses)
                fx, fz = -math.sin(yaw), -math.cos(yaw)
                px, pz = -fz, fx
                length, width = 1.3 * h / 1.8, 0.45 * h / 1.8
                tip = (x + fx * length, y + 0.04, z + fz * length)
                left = (x + px * width, y + 0.04, z + pz * width)
                right = (x - px * width, y + 0.04, z - pz * width)
                tri(out, left, right, tip, np.minimum(rgb * 1.25, 1.0), 0.85 * alpha, lit=False)
    return (np.array(solid, np.float32) if solid else np.zeros((0, 7), np.float32),
            np.array(ghost, np.float32) if ghost else np.zeros((0, 7), np.float32))


class TexturedBatch:
    """Triangles (x, y, z, u, v, r, g, b, a) d'une meme texture ; blended = couche de melange."""

    def __init__(self, array: np.ndarray, image, blended: bool = False):
        self.blended = blended
        # donnees gardees : un contexte OpenGL recree (fenetre deplacee sur un
        # autre ecran...) oblige a tout renvoyer a la carte graphique
        self.array = np.ascontiguousarray(array, dtype=np.float32).reshape(-1, 9)
        # DXT compresse (dict de TextureLibrary.compressed), RGBA (h, w, 4), ou None = gris
        self.image = image
        self.buffer = self.texture = None
        self.count = 0

    def upload(self):
        if self.buffer is not None:
            return
        self.buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.buffer.create()
        self.buffer.bind()
        data = self.array.tobytes()
        self.buffer.allocate(data, len(data))
        self.buffer.release()
        self.count = len(self.array)
        image = self.image
        if isinstance(image, dict):
            self.texture = self._compressed_texture(image)
            if self.texture is not None:
                return
            try:  # carte graphique sans DXT : decodage par le processeur
                image = decode_dxt(image["levels"][0], image["w"], image["h"], image["format"])
            except (ValueError, KeyError, IndexError):
                image = None
        image = image if image is not None else np.full((4, 4, 4), 150, np.uint8)
        image = np.ascontiguousarray(image)
        qimage = QImage(image.tobytes(), image.shape[1], image.shape[0], QImage.Format.Format_RGBA8888).copy()
        self.texture = QOpenGLTexture(qimage, QOpenGLTexture.MipMapGeneration.GenerateMipMaps)
        self.texture.setWrapMode(QOpenGLTexture.WrapMode.Repeat)
        self.texture.setMinMagFilters(QOpenGLTexture.Filter.LinearMipMapLinear, QOpenGLTexture.Filter.Linear)

    @staticmethod
    def _compressed_texture(tex: dict):
        """Texture DXT envoyee telle quelle avec ses niveaux ; None si impossible."""
        formats = {FORMAT_DXT1: QOpenGLTexture.TextureFormat.RGBA_DXT1,
                   FORMAT_DXT5: QOpenGLTexture.TextureFormat.RGBA_DXT5}
        fmt = formats.get(tex.get("format"))
        levels = tex.get("levels") or []
        if fmt is None or not levels:
            return None
        block = 8 if tex["format"] == FORMAT_DXT1 else 16
        w, h = tex["w"], tex["h"]
        for k, level in enumerate(levels):  # niveaux complets obligatoires
            a, b = max(1, w >> k), max(1, h >> k)
            if len(level) != max(1, (a + 3) // 4) * max(1, (b + 3) // 4) * block:
                return None
        texture = None
        try:
            texture = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
            texture.setFormat(fmt)
            texture.setSize(w, h)
            texture.setMipLevels(len(levels))
            texture.allocateStorage()
            if not texture.isStorageAllocated():
                raise RuntimeError("stockage DXT refuse")
            for k, level in enumerate(levels):
                texture.setCompressedData(k, len(level), level)
            texture.setWrapMode(QOpenGLTexture.WrapMode.Repeat)
            texture.setMinMagFilters(QOpenGLTexture.Filter.LinearMipMapLinear, QOpenGLTexture.Filter.Linear)
            return texture
        except Exception:  # noqa: BLE001 - pilote ou liaison Qt sans DXT
            if texture is not None and texture.isCreated():
                texture.destroy()
            return None

    def draw(self, gl: QOpenGLFunctions, program: QOpenGLShaderProgram):
        self.upload()
        if not self.count:
            return
        self.texture.bind(0)
        self.buffer.bind()
        for k in range(3):
            program.enableAttributeArray(k)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 36)
        program.setAttributeBuffer(1, GL_FLOAT, 12, 2, 36)
        program.setAttributeBuffer(2, GL_FLOAT, 20, 4, 36)
        gl.glDrawArrays(GL_TRIANGLES, 0, self.count)
        self.buffer.release()
        self.texture.release()

    def destroy(self):
        """Libere la memoire de la carte graphique (contexte OpenGL actif obligatoire)."""
        if self.buffer is not None:
            self.buffer.destroy()
        if self.texture is not None:
            self.texture.destroy()
        self.buffer = self.texture = None
        self.count = 0


class View3D(QOpenGLWidget):
    """Vue 3D orbitale."""
    status = Signal(str)
    picked = Signal(object)  # cle du point clique (voir set_pick_points)
    placed = Signal(float, float, float)  # outil Point : surface cliquee (mm du jeu)

    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setMinimumSize(200, 200)
        self.gl = None
        self.program = None
        self.decor = Mesh(GL_TRIANGLES)
        self.floors = Mesh(GL_TRIANGLES)
        self.walls = Mesh(GL_TRIANGLES)
        self.wall_edges = Mesh(GL_LINES)
        self.grid = Mesh(GL_LINES)
        self.lines = Mesh(GL_LINES)
        self.actors = Mesh(GL_TRIANGLES)
        self.actor_ghosts = Mesh(GL_TRIANGLES)
        self.textured: list[TexturedBatch] = []
        self._old_batches: list[TexturedBatch] = []
        self.tex_program = None
        self.cut = 1.0e6  # hauteur (m, vue) au-dessus de laquelle le decor texture est cache
        # camera libre, regles du mode photo du trainer : position de l'oeil,
        # cap (yaw) et inclinaison du regard (pitch, negatif = vers le bas)
        self.eye = np.array((0.0, 60.0, 60.0))
        self.yaw, self.pitch = 0.0, math.radians(-35)
        self.distance = 25.0  # recul utilise par look_at
        self.zoom = 1.0       # 0,5 a 20, comme le mode photo
        self._zoom_frames = 0
        self._drag = None
        self._button = None
        self._press = None
        self._press_time = 0.0
        self._looked = False
        self._pick_keys, self._pick_xyz = [], np.zeros((0, 3))
        self._last_pick = None
        self.place_mode = False  # outil Point : un clic sur le decor pose un point
        self._surface = np.zeros((0, 3, 3))  # triangles cliquables (coordonnees de la vue)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._keys: set = set()       # codes de position des touches (scancodes)
        self._buttons: set = set()    # boutons de souris maintenus
        self._tick = QTimer(self)
        self._tick.setInterval(16)
        self._tick.timeout.connect(self._step)
        self._tick_time = 0.0
        self.hint = QLabel(tr("view3d.init.zqsd_se_deplacer_clic_molette"), self)
        self.hint.setObjectName("overlay")

    # -------------------------------------------------- donnees
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.hint.setWordWrap(True)
        self.hint.setMaximumWidth(max(200, self.width() - 32))
        self.hint.adjustSize()
        self.hint.move(16, 16)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:  # vue de dessus, a la meme place
            self.pitch = math.radians(-89)
            self.update()

    def set_decor(self, tris):
        """tris : (N, 3, 3) en mm (formes de collision)."""
        self.decor.set(decor_mesh(tris))
        self.update()

    def set_cut(self, y_mm: float | None):
        """Coupe du decor texture au-dessus de cette hauteur (mm), None = rien."""
        cut = 1.0e6 if y_mm is None else y_mm / 1000.0
        if cut != self.cut:
            self.cut = cut
            self.update()

    def set_actors(self, actors):
        """Personnages (voir actor_meshes)."""
        solid, ghost = actor_meshes(actors)
        self.actors.set(solid)
        self.actor_ghosts.set(ghost)
        self.update()

    def set_textured(self, batches):
        """batches : [(tableau (N, 9) x, y, z, u, v, r, g, b, a en metres de la vue, image RGBA ou None,
        couche de melange)]. Remplace le decor de collision tant qu'il y en a."""
        self._old_batches += self.textured
        self.textured = [TexturedBatch(b[0], b[1], b[2] if len(b) > 2 else False) for b in batches if len(b[0])]
        self.update()

    def set_world(self, floors, walls, ground: float):
        """floors : [(polygone [(x, z)], hauteur du sol)] ; walls : [(x1, z1, x2, z2, pied, hauteur)] (mm)."""
        tri, edges = wall_meshes(walls)
        self.walls.set(tri)
        self.wall_edges.set(edges)
        self.floors.set(floor_mesh(floors))
        self.set_grid(walls, ground)
        self.update()

    def set_grid(self, walls, ground: float):
        """Quadrillage de 10 m au niveau du sol, sur l'etendue des murs."""
        if not walls:
            self.grid.set(np.zeros((0, 7), np.float32))
            return
        w = np.asarray(walls, dtype=np.float64)
        x0, x1 = -w[:, [0, 2]].max() / 1000, -w[:, [0, 2]].min() / 1000
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

    def set_pick_points(self, points):
        """points : [(cle, (x, y, z) en mm)] que l'on peut choisir d'un clic."""
        self._pick_keys = [key for key, _ in points]
        self._pick_xyz = np.array([gl_point(*xyz) for _, xyz in points], dtype=np.float64).reshape(-1, 3)

    def pick(self, pos) -> object | None:
        """Point le plus proche du clic (12 px) ; clic repete : point superpose suivant."""
        if not len(self._pick_keys):
            return None
        m = np.array(self.matrix().data(), dtype=np.float64).reshape(4, 4)  # colonne par colonne
        clip = np.c_[self._pick_xyz, np.ones(len(self._pick_xyz))] @ m
        front = clip[:, 3] > 1e-6
        w = np.where(front, clip[:, 3], 1.0)
        sx = (clip[:, 0] / w + 1) * 0.5 * self.width()
        sy = (1 - clip[:, 1] / w) * 0.5 * self.height()
        d = np.hypot(sx - pos.x(), sy - pos.y())
        near = [i for i in np.nonzero(front & (d <= 12))[0]]
        if not near:
            return None
        hits = [self._pick_keys[i] for i in near]
        if self._last_pick in hits and len(hits) > 1:
            return hits[(hits.index(self._last_pick) + 1) % len(hits)]
        return self._pick_keys[min(near, key=lambda i: (d[i], clip[i, 3]))]

    def set_pick_surface(self, tris_mm):
        """Triangles (N, 3, 3) en mm du jeu sur lesquels on peut poser un point."""
        if tris_mm is None or not len(tris_mm):
            self._surface = np.zeros((0, 3, 3))
            return
        t = np.asarray(tris_mm, dtype=np.float64) / 1000.0
        t[:, :, 0] *= -1.0
        t[:, :, 2] *= -1.0
        self._surface = t

    def surface_hit(self, pos):
        """Point du decor sous la souris (mm du jeu) ou None : rayon de l'oeil
        a travers le pixel, premier triangle touche."""
        if not len(self._surface):
            return None
        inverse, ok = self.matrix().inverted()
        if not ok:
            return None
        nx = 2.0 * pos.x() / max(self.width(), 1) - 1.0
        ny = 1.0 - 2.0 * pos.y() / max(self.height(), 1)
        near = inverse.map(QVector4D(nx, ny, -1.0, 1.0))
        far = inverse.map(QVector4D(nx, ny, 1.0, 1.0))
        a = np.array((near.x(), near.y(), near.z())) / near.w()
        b = np.array((far.x(), far.y(), far.z())) / far.w()
        d = b - a
        d /= np.linalg.norm(d) or 1.0
        v0, v1, v2 = self._surface[:, 0], self._surface[:, 1], self._surface[:, 2]
        e1, e2 = v1 - v0, v2 - v0
        h = np.cross(d, e2)
        det = (e1 * h).sum(axis=1)
        good = np.abs(det) > 1e-9
        inv = np.where(good, 1.0 / np.where(good, det, 1.0), 0.0)
        s = a - v0
        u = (s * h).sum(axis=1) * inv
        q = np.cross(s, e1)
        v = (q * d).sum(axis=1) * inv
        t = (q * e2).sum(axis=1) * inv
        hit = good & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0.05)
        if not hit.any():
            return None
        p = a + d * t[hit].min()
        return -p[0] * 1000.0, p[1] * 1000.0, -p[2] * 1000.0

    def look_direction(self) -> np.ndarray:
        cp = math.cos(self.pitch)
        return np.array((-math.sin(self.yaw) * cp, math.sin(self.pitch), -math.cos(self.yaw) * cp))

    def look_at(self, x: float, y: float, z: float, distance: float | None = None):
        """Place la camera en recul sur ce point (meme cap, regard plongeant au besoin)."""
        if distance:
            self.distance = distance
        if self.pitch > math.radians(-15):
            self.pitch = math.radians(-35)
        target = np.array(gl_point(x, y, z), dtype=np.float64)
        self.eye = target - self.look_direction() * self.distance
        self.update()

    # -------------------------------------------------- OpenGL
    def initializeGL(self):
        # avant la destruction du contexte (fermeture, changement d'ecran) :
        # liberer les textures tant qu'il est encore actif, sinon Qt previent
        # « QOpenGLTexturePrivate::destroy() called without a current context »
        # (PAS de branchement sur aboutToBeDestroyed : emis aussi quand Qt recree
        # la fenetre au premier affichage de la 3D, il figeait l'editeur, 2026-11-22)
        self.gl = QOpenGLFunctions(self.context())
        self.gl.initializeOpenGLFunctions()
        self.program = QOpenGLShaderProgram(self)
        self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERTEX)
        self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT)
        self.program.bindAttributeLocation("position", 0)
        self.program.bindAttributeLocation("color", 1)
        if not self.program.link():
            self.status.emit(tr("view3d.initializegl.vue_3d_shaders_refuses_par") + self.program.log() + ")")
        self.tex_program = QOpenGLShaderProgram(self)
        self.tex_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, TEX_VERTEX)
        self.tex_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, TEX_FRAGMENT)
        for k, name in enumerate(("position", "uv", "light")):
            self.tex_program.bindAttributeLocation(name, k)
        if not self.tex_program.link():
            self.status.emit(tr("view3d.initializegl.vue_3d_textures_refusees_par") + self.tex_program.log() + ")")
            self.tex_program = None

    def _release_gl(self):
        self.release_gl()

    def release_gl(self, final: bool = False):
        """Libere les textures dans le contexte OpenGL de la vue. final : a la
        fermeture, plus aucune reference Python (sinon l'arret de Python les
        detruit plus tard, contexte disparu : « Texture has not been destroyed »)."""
        if self.context() is None or not self.context().isValid():
            return
        self.makeCurrent()
        for batch in self.textured + self._old_batches:
            batch.destroy()  # sinon : renvoyes au prochain dessin
        self._old_batches = []
        if final:
            self.textured = []
        self.doneCurrent()

    def resizeGL(self, w: int, h: int):
        ratio = self.devicePixelRatioF()
        self.gl.glViewport(0, 0, int(w * ratio), int(h * ratio))

    def matrix(self) -> QMatrix4x4:
        proj = QMatrix4x4()
        fov = math.degrees(2 * math.atan(math.tan(math.radians(25)) / self.zoom))
        proj.perspective(fov, max(self.width(), 1) / max(self.height(), 1), 0.1, 5000.0)
        view = QMatrix4x4()
        view.lookAt(QVector3D(*self.eye), QVector3D(*(self.eye + self.look_direction())), QVector3D(0, 1, 0))
        return proj * view

    def paintGL(self):
        gl = self.gl
        gl.glClearColor(*BACKGROUND, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_BLEND)
        gl.glEnable(GL_MULTISAMPLE)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        for batch in self._old_batches:  # liberes dans le contexte OpenGL
            batch.destroy()
        self._old_batches = []
        textured = bool(self.textured and self.tex_program is not None)
        if textured and self.tex_program.bind():
            gl.glEnable(GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(2.0, 2.0)
            self.tex_program.setUniformValue("mvp", self.matrix())
            self.tex_program.setUniformValue("tex", 0)
            self.tex_program.setUniformValue1f("cut", float(self.cut))
            self.tex_program.setUniformValue1f("blend", 0.0)
            for batch in self.textured:
                if not batch.blended:
                    batch.draw(gl, self.tex_program)
            # couches de melange : par-dessus, sans ecrire la profondeur
            gl.glPolygonOffset(0.0, 0.0)
            gl.glDepthMask(False)
            self.tex_program.setUniformValue1f("blend", 1.0)
            for batch in self.textured:
                if batch.blended:
                    batch.draw(gl, self.tex_program)
            gl.glDepthMask(True)
            self.tex_program.disableAttributeArray(2)
            gl.glDisable(GL_POLYGON_OFFSET_FILL)
            self.tex_program.release()
        if not self.program.bind():
            return
        self.program.setUniformValue("mvp", self.matrix())
        self.grid.draw(gl, self.program)
        gl.glEnable(GL_POLYGON_OFFSET_FILL)
        gl.glPolygonOffset(2.0, 2.0)  # sols sous les lignes des routes
        if not textured:
            self.decor.draw(gl, self.program)
        self.floors.draw(gl, self.program)
        gl.glPolygonOffset(1.0, 1.0)  # aretes au-dessus des faces
        self.walls.draw(gl, self.program)
        gl.glDisable(GL_POLYGON_OFFSET_FILL)
        self.wall_edges.draw(gl, self.program)
        gl.glLineWidth(2.0)
        self.lines.draw(gl, self.program)
        gl.glLineWidth(1.0)
        self.actors.draw(gl, self.program)
        # fantomes : seulement la ou le decor cache le personnage
        gl.glDepthFunc(GL_GREATER)
        gl.glDepthMask(False)
        self.actor_ghosts.draw(gl, self.program)
        gl.glDepthMask(True)
        gl.glDepthFunc(GL_LESS)
        self.program.release()

    # -------------------------------------------------- clavier et souris (mode photo du trainer)
    # Touches par POSITION (scancodes), quelle que soit la disposition :
    # Z/W 0x11 avancer, S 0x1F reculer, Q/A 0x1E gauche, D 0x20 droite,
    # A/Q 0x10 rapide ; en plus : fleches, Espace monter, C 0x2E descendre.
    SC_FORWARD, SC_BACK, SC_LEFT, SC_RIGHT, SC_FAST, SC_DOWN = 0x11, 0x1F, 0x1E, 0x20, 0x10, 0x2E
    ARROWS = {Qt.Key.Key_Up: SC_FORWARD, Qt.Key.Key_Down: SC_BACK, Qt.Key.Key_Left: SC_LEFT,
              Qt.Key.Key_Right: SC_RIGHT, Qt.Key.Key_Space: "up"}
    STEP = 3.0            # m/s (mode photo : 50 unites du jeu par image)
    FAST, SLOW = 5.0, 0.5  # A ou Maj ; Ctrl
    LOOK = 0.003          # radians par pixel
    ZOOM_RATE, ZOOM_FRAMES = 1.02, 8  # par image ; images par cran de molette
    HOLD_DOWN = 0.25      # s : clic gauche immobile au-dela = descendre

    def _code(self, event):
        if event.key() in self.ARROWS:
            return self.ARROWS[event.key()]
        code = event.nativeScanCode()
        if code in (self.SC_FORWARD, self.SC_BACK, self.SC_LEFT, self.SC_RIGHT, self.SC_FAST, self.SC_DOWN):
            return code
        return None

    def _start(self):
        if not self._tick.isActive():
            self._tick_time = time.perf_counter()
            self._tick.start()

    def keyPressEvent(self, event):
        code = self._code(event)
        if code is None:
            return super().keyPressEvent(event)
        if not event.isAutoRepeat():
            self._keys.add(code)
            self._start()
        event.accept()

    def keyReleaseEvent(self, event):
        code = self._code(event)
        if code is None:
            return super().keyReleaseEvent(event)
        if not event.isAutoRepeat():
            self._keys.discard(code)
        event.accept()

    def focusOutEvent(self, event):
        self._keys.clear()
        self._buttons.clear()
        super().focusOutEvent(event)

    def _step(self):
        now = time.perf_counter()
        dt, self._tick_time = min(now - self._tick_time, 0.1), now
        keys = self._keys
        left_hold = (Qt.MouseButton.LeftButton in self._buttons and not self._looked
                     and now - self._press_time > self.HOLD_DOWN)
        moving = keys - {self.SC_FAST} or Qt.MouseButton.RightButton in self._buttons or left_hold
        if not moving and not self._zoom_frames:
            if not self._buttons:
                self._tick.stop()
            return
        if self._zoom_frames:
            frames = max(1, round(dt * 60))
            k = self.ZOOM_RATE ** (frames if self._zoom_frames > 0 else -frames)
            self.zoom = min(max(self.zoom * k, 0.5), 20.0)
            self._zoom_frames -= int(math.copysign(min(frames, abs(self._zoom_frames)), self._zoom_frames))
        if moving:
            look = self.look_direction()
            right = np.array((math.cos(self.yaw), 0.0, -math.sin(self.yaw)))
            move = np.zeros(3)
            move += look * ((self.SC_FORWARD in keys) - (self.SC_BACK in keys))
            move += right * ((self.SC_RIGHT in keys) - (self.SC_LEFT in keys))
            up = (Qt.MouseButton.RightButton in self._buttons or "up" in keys) - (left_hold or self.SC_DOWN in keys)
            move[1] += up
            mods = QApplication.keyboardModifiers()
            speed = self.STEP
            if self.SC_FAST in keys or mods & Qt.KeyboardModifier.ShiftModifier:
                speed *= self.FAST
            if mods & Qt.KeyboardModifier.ControlModifier:
                speed *= self.SLOW
            self.eye += move * speed * dt
        self.update()

    def mousePressEvent(self, event):
        self.setFocus()
        self._drag = event.position()
        self._press = event.position()
        self._press_time = time.perf_counter()
        self._looked = False
        self._button = event.button()
        self._buttons.add(event.button())
        self._start()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        delta = event.position() - self._drag
        self._drag = event.position()
        buttons = event.buttons()
        if buttons & (Qt.MouseButton.MiddleButton | Qt.MouseButton.LeftButton):
            if self._press is not None and (event.position() - self._press).manhattanLength() >= 4:
                self._looked = True  # le clic gauche devient un regard, plus une descente ni un choix
            if self._looked or buttons & Qt.MouseButton.MiddleButton:
                self.yaw -= delta.x() * self.LOOK
                self.pitch = min(max(self.pitch - delta.y() * self.LOOK, math.radians(-89)), math.radians(89))
                self.update()

    def mouseReleaseEvent(self, event):
        self._buttons.discard(event.button())
        if not event.buttons():
            self._drag = None
        quick = time.perf_counter() - self._press_time <= self.HOLD_DOWN
        if (event.button() == Qt.MouseButton.LeftButton and self._press is not None and not self._looked and quick
                and (event.position() - self._press).manhattanLength() < 4):
            key = self.pick(event.position())
            if key is not None and not self.place_mode:
                self._last_pick = key
                self.picked.emit(key)
            elif self.place_mode:
                hit = self.surface_hit(event.position())
                if hit is not None:
                    self.placed.emit(*hit)
                else:
                    self.status.emit(tr("view3d.mousereleaseevent.aucune_surface_sous_la_souris"))
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = None

    def wheelEvent(self, event):
        notches = event.angleDelta().y() / 120
        if notches:
            self._zoom_frames += int(round(notches * self.ZOOM_FRAMES)) or (self.ZOOM_FRAMES if notches > 0 else -self.ZOOM_FRAMES)
            self._start()

    def reset_orientation(self):
        self.yaw, self.pitch, self.zoom = 0.0, math.radians(-35), 1.0
        self.update()
