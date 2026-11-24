# What's new in each version

[← Back to the README](../../README.en.md) · *[Version française](../nouveautes.md)*

The details of each version; the files are on the
[releases](https://github.com/ajikfan/MGS4-Route-Editor/releases) page.

## V1.0.1

- **Complete assignment**: the NPC receives the whole starting point
  (position, look, action, gait, wait, flags), as the game does. It no
  longer stays stuck on the action of its previous route.
- **Start from the nearest point reachable in a straight line** (from the
  zone's walls) instead of point 1; the assignment message gives that
  point, or warns when no point can be reached.
- **No more crash** on automatic zone changes ("Follow the game").
- New documentation: [editor guide](guide.md) and this page, in English
  and French.

## V1.0.0

First public version.

- Map of each zone read from the game files: floor, walls, obstacles,
  scenery, game routes and markers; floor selection.
- 3D view: collision scenery, or the game's textured scenery (HD textures,
  loads in about a second); free camera like the trainer's photo mode.
- Route creation in 2D or directly on surfaces in 3D: actions, gaits,
  waits, look-at point, flags, loop or one way.
- Provided routes shipped with the editor, updated with each version.
- Game running: live Snake, soldiers, allies, animals and wolves; editing
  the game's routes; assigning a route to any NPC; teleporting Snake.
- English and French interface, built-in updates.
