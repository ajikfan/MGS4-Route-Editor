# Editor guide

[← Back to the README](../../README.en.md) · *[Version française](../guide.md)*

- [The window](#the-window)
- [The 2D map and the 3D view](#the-2d-map-and-the-3d-view)
- [Creating a route](#creating-a-route)
- [Game routes and provided routes](#game-routes-and-provided-routes)
- [Giving a route to an NPC](#giving-a-route-to-an-npc)
- [How an NPC follows a route](#how-an-npc-follows-a-route)
- [Common problems](#common-problems)

## The window

- **Top bar**:
  - **Displayed zone**: the zone whose map is shown; **Game zone** shows the
    one Snake is in;
  - **Follow the game**: when checked, the map changes on its own when the
    game changes zone;
  - the **tools**: **Point**, **Line** (insert a point in a segment),
    **Move**, **Select**, **Delete**;
  - the **2D / 3D** switch, the **language** (the editor restarts) and
    **updates**.
- **Left panel**:
  - **Layers**: walkable floor, walls and obstacles, passages between
    cells, collision scenery, textured scenery (game running), game
    routes, markers, live NPCs and animals, grid;
  - **Level**: the floor shown (cells from -1.5 m to +2 m around it).
    **Auto** follows Snake's height;
  - **My routes**, the **provided routes** and the **game routes**
    (search by identifier, or "NPC" for assignable routes).
- **Right panel**: the **selected point** and its fields, then the
  **assignment** of a route to an NPC.
- The status bar shows the connection to the game (read every half
  second).

## The 2D map and the 3D view

- **2D map**: wheel to zoom, right or middle button held to pan.
- **3D view**: the collision scenery, or the game's **textured scenery**
  when it is running (models and HD textures read live, loaded in about a
  second; the first opening of a zone takes a bit longer). Controls, like
  the trainer's photo mode:
  - **WASD** (ZQSD on an AZERTY keyboard) to move; **Q** (A on AZERTY) or
    **Shift** to go fast, **Ctrl** to go slowly;
  - **middle button** or **left button held** + mouse to look around;
  - **right button** held to go up, **left button held** without moving
    to go down (also **Space** and **C**);
  - **wheel** to zoom; **click** to pick a point; **double-click** to go
    back to the top view.
- In 3D, the **Point** tool places the point **on the clicked surface**
  (ground, roof, rock).

## Creating a route

1. **+ New** in "My routes" (double-click to rename it).
2. **Point** tool: each click adds a point. **Point = Snake** puts the
   point on Snake (game running). **Shift + click** on a point adds a
   point at the same place, like the game routes that chain several
   actions on the spot.
3. For each point, on the right:
   - **Action on arrival** and **Gait to get there**: known codes are
     named, the others marked as hypotheses (**Name…** to give them a
     name, kept for every zone);
   - **Wait** (65535 = endless), **Look** (target point; **Look at = Snake**),
     **Facing**, **Flags** (**Bits…** to see them one by one) and
     parameters;
   - **Height**: Snake's with "Point = Snake" (about 10 cm above a
     soldier's feet, like the game routes).
4. **Loop** or not.

Routes are saved per zone in `routes/<zone>.json`. **Export (JSON)** makes
a copy.

## Game routes and provided routes

- **Game routes**: hidden by default; check the ones to show. With the
  game running, their points are edited **live** (move a point, change its
  action…); the editor keeps the original and offers to restore it.
  Right-click: **copy to my routes**.
- **Provided routes**: ready-made routes shipped with the editor and
  replaced on each update, to assign as they are or to copy into your
  routes.

## Giving a route to an NPC

1. Game running, in the route's zone.
2. Pick the **NPC** in the list, or click on it on the map.
3. Pick the route (game, provided or yours), then **Assign the route**.
   One of your routes is first written into the game, in place of an
   unused NPC route (**Inject into the game** does it by hand).
4. The NPC receives the whole starting point (position, look, action,
   gait, wait) and leaves right away. The message gives **the chosen
   starting point**: the nearest one it can reach in a straight line.

The trainer (MGS4-Trainer) can spawn NPCs **with no route**, standing
guard where they appear, ready to receive a route from the editor ("No
route (on guard)" box).

**Animations of this type…** records, for an NPC type, the gaits and
actions that work or give a T-pose; the editor warns before assigning a
route that uses them.

## How an NPC follows a route

- **In a straight line**: an NPC goes straight to the target point,
  without going around walls. A route far away will make it cross the
  zone straight ahead (and it can get stuck against an obstacle).
- **Arrival**: a point is reached at about **one meter horizontally**,
  whatever the height. Two points on different levels that are too close
  horizontally are validated without changing level: space them about
  2 m apart.
- **Changing level**:
  - slopes, rocks and steps up to about 50 cm pass on their own;
  - a **soldier** takes a **ladder** on its own: one point at the bottom,
    the next one at the top, no special code;
  - **Meryl** does not take ladders (she stays at the bottom);
  - a wall without a ladder could not be climbed, even with gait 17,
    which the game puts on its own jumps.
- An **open** route (no loop) goes back from the last point to the first
  one.
- During an **alert** or a **fight**, an NPC ignores its route.
- The available animations depend on the NPC type and the zone: Meryl
  has no "hands on head", and some zones remove animations from the
  soldiers (the trainer gives them back to the NPCs it adds).

## Common problems

- **The NPC does not move**: check that it is not on alert, and that the
  announced starting point can be reached. A FROG placed by the zone's
  script keeps the route the script imposes.
- **The NPC runs against a wall**: an obstacle is on the straight line to
  its point; add an intermediate point.
- **Empty map or missing walls**: the zone has not been seen in game yet;
  go there once with the editor open.
- **Editor crash**: the details are in `logs/editeur.log`.
