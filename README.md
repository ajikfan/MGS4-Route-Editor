# MGS4 Route Editor — V1.0.1

*[English version](README.en.md)*

Éditeur de routes pour Metal Gear Solid 4 (portage PC Steam) : voir une zone
de dessus ou en 3D, tracer ses propres routes de patrouille, modifier en direct
les routes du jeu et les donner aux soldats, aux alliés ou aux PNJ apparus.
Fonctionne avec MGS4 1.4.1 et 1.4.2.

Application autonome, séparée de
[MGS4-Trainer](https://github.com/ajikfan/MGS4-Best-Trainer-Ever), dont elle
reprend le système de mise à jour et les langues. Interface en français et en
anglais. Usage en solo uniquement.

## Installation

**Avec l'exe (le plus simple)**

1. Télécharger le zip de la [dernière version](https://github.com/ajikfan/MGS4-Route-Editor/releases/latest).
2. Le décompresser où l'on veut (garder tout le dossier).
3. Lancer `MGS4RouteEditor.exe`. Le dossier du jeu est trouvé tout seul (jeu
   lancé, dossiers Steam habituels), sinon demandé une fois.
4. Pour les fonctions en direct, lancer MGS4 avec une partie chargée.
5. Ensuite, le bouton **Mises à jour** installe les nouvelles versions sans
   retélécharger le zip.

**Depuis les sources**

```
python route_editor.py
```

Python 3.12, PySide6 et numpy. Les mises à jour se font alors par `git pull`.

## Documentation

- [Guide de l'éditeur](docs/guide.md) : la fenêtre, la 2D et la 3D, créer
  une route, la donner à un PNJ, comment il la suit, problèmes courants
- [Nouveautés par version](docs/nouveautes.md)

## Ce que fait l'éditeur

- **Carte de la zone**, lue dans les fichiers du jeu sans le lancer : sol
  praticable, murs et obstacles, décor de collision, routes du jeu, repères.
  Choix du niveau affiché (étage), automatique d'après la hauteur de Snake.
- **Vue 3D** : décor de collision, ou **décor texturé** du jeu (modèles et
  textures HD lus en direct quand on est dans la zone, chargés en une seconde
  environ). Caméra libre comme le mode photo du trainer (ZQSD, souris,
  monter/descendre, zoom).
- **Mes routes** : création point par point (en 2D, ou en 3D directement sur
  une surface), points superposés, action à l'arrivée, allure, attente, point
  regardé, drapeaux et paramètres de chaque point, boucle ou aller simple.
  Enregistrées par zone dans `routes/<zone>.json`.
- **Routes fournies** : routes toutes faites livrées avec l'éditeur et
  remplacées à chaque mise à jour, à attribuer telles quelles ou à copier dans
  ses routes pour les modifier.
- **En direct (jeu lancé)** : Snake, soldats, alliés, animaux et loups sur la
  carte ; modification des routes du jeu ; **attribution** d'une route (du jeu,
  fournie ou à soi) à n'importe quel PNJ ; téléportation de Snake.
- Recherche dans les routes du jeu, suivi automatique de la zone du jeu,
  journal des plantages (`logs/editeur.log`).

Le jeu ne documente pas les codes d'action et d'allure ni les drapeaux des
points : l'éditeur affiche ceux qui sont connus et marque les autres comme
hypothèses, à renommer au fil des essais.

## Comment un PNJ suit une route

Un PNJ va **en ligne droite** vers chaque point, sans contourner les murs,
et un point est atteint à environ un mètre à l'horizontale, quelle que soit
la hauteur. Les soldats prennent les échelles tout seuls, pas Meryl.
Détails et conseils dans le [guide](docs/guide.md#comment-un-pnj-suit-une-route).

## Mises à jour et versions (développeurs)

Comme le trainer : l'exe (`launcher.py`, `MGS4RouteEditor.spec`) ne contient
que Python, PySide6 et numpy ; le code est livré à côté, dans `app/`
(`release_files.py`). Chaque release GitHub porte un `manifest.json`
(`make_manifest.py`) avec l'empreinte de chaque fichier : seuls les fichiers
changés sont téléchargés, puis installés au lancement suivant.

Le décodeur LZ4 des textures est une petite DLL livrée avec le code
(`mgs4routes/lz4dec.dll`, source `native/lz4dec.c`, compilée par
`gcc -O2 -shared -s -static`) ; sans elle, une version Python plus lente prend
le relais.

Pour publier une version : mettre à jour les routes fournies
(`python tools/export_provided_routes.py`), changer `VERSION` dans
`mgs4routes/__init__.py`, commiter, créer le tag, lancer
`pyinstaller MGS4RouteEditor.spec` puis `python make_manifest.py`, et joindre à
la release le zip de `dist/MGS4RouteEditor` et `dist/manifest.json`.
