# MGS4 Route Editor — V1.0.0

*[Version française](README.md)*

Route editor for Metal Gear Solid 4 (Steam PC port): view a zone from above or
in 3D, draw your own patrol routes, edit the game's routes live and give them
to soldiers, allies or spawned NPCs. Works with MGS4 1.4.1 and 1.4.2.

Standalone application, separate from
[MGS4-Trainer](https://github.com/ajikfan/MGS4-Best-Trainer-Ever), whose update
system and languages it reuses. Interface in English and French. Single-player
use only.

## Installation

**With the exe (easiest)**

1. Download the zip of the [latest version](https://github.com/ajikfan/MGS4-Route-Editor/releases/latest).
2. Unzip it anywhere (keep the whole folder).
3. Run `MGS4RouteEditor.exe`. The game folder is found automatically (running
   game, usual Steam folders), otherwise asked once.
4. For the live features, start MGS4 with a save loaded.
5. Afterwards, the **Updates** button installs new versions without
   downloading the zip again.

**From source**

```
python route_editor.py
```

Python 3.12, PySide6 and numpy. Updates are then done with `git pull`.

## What the editor does

- **Zone map**, read from the game files without starting the game: walkable
  floor, walls and obstacles, collision scenery, game routes, markers. Choice of
  the displayed level (floor), automatic from Snake's height.
- **3D view**: collision scenery, or the game's **textured scenery** (models and
  HD textures read live while you are in the zone, loaded in about a second).
  Free camera like the
  trainer's photo mode (WASD, mouse, up/down, zoom).
- **My routes**: point-by-point creation (in 2D, or in 3D directly on a
  surface), stacked points, action on arrival, gait, wait, look-at point, flags
  and parameters of each point, loop or one way. Saved per zone in
  `routes/<zone>.json`.
- **Provided routes**: ready-made routes shipped with the editor and replaced
  on each update, which you can assign or copy to your routes to edit them.
- **Live (game running)**: Snake, soldiers, allies, animals and wolves on the
  map; editing the game's routes; **assigning** a route (game, provided or your
  own) to any NPC; teleporting Snake.
- Search in the game routes, automatic following of the game zone, crash log
  (`logs/editeur.log`).

The game does not document the action and gait codes or the point flags: the
editor shows the known ones and marks the others as hypotheses, which you can
rename as you test.

## How an NPC follows a route

- **In a straight line**: an NPC goes straight to the target point,
  without going around walls. When assigning, the editor therefore starts
  it from the nearest point it can reach in a straight line (from the
  area's walls), and the message gives that point; a route far away will
  make it cross the area straight ahead.
- **Arrival**: a point is reached at about one meter horizontally,
  whatever the height. Two points on different levels that are too close
  horizontally are validated without changing level.
- **Changing level**: slopes, rocks and steps up to about 50 cm pass on
  their own; a soldier also takes a **ladder** on its own (one point at the
  bottom, the next one at the top, no special code). Meryl does not take
  ladders (she stays at the bottom). A wall without a ladder could not be
  climbed, even with the game routes' gait 17.
- An open route (no loop) goes back from the last point to the first one.

## Updates and releases (developers)

Like the trainer: the exe (`launcher.py`, `MGS4RouteEditor.spec`) only contains
Python, PySide6 and numpy; the code ships next to it in `app/`
(`release_files.py`). Each GitHub release carries a `manifest.json`
(`make_manifest.py`) with the hash of every file: only the changed files are
downloaded, then installed on the next launch.

The texture LZ4 decoder is a small DLL shipped with the code
(`mgs4routes/lz4dec.dll`, source `native/lz4dec.c`, built with
`gcc -O2 -shared -s -static`); without it, a slower Python version takes over.

To publish a version: update the provided routes
(`python tools/export_provided_routes.py`), change `VERSION` in
`mgs4routes/__init__.py`, commit, create the tag, run
`pyinstaller MGS4RouteEditor.spec` and `python make_manifest.py`, then attach the
zip of `dist/MGS4RouteEditor` and `dist/manifest.json` to the release.
