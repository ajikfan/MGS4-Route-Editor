# MGS4 Route Editor

Éditeur de routes pour Metal Gear Solid 4.

Application à part (indépendante de MGS4-Trainer) pour voir une zone de
Metal Gear Solid 4 de dessus et y tracer des routes.

```
python route_editor.py
```

Python 3.12 et PySide6. Le dossier du jeu est trouvé tout seul (jeu lancé,
dossiers Steam habituels), sinon demandé, puis mémorisé dans
`settings.json`.

## Ce qui marche

- Carte de dessus d'une zone, lue dans son fichier `.geom`, sans lancer le
  jeu : grille de la zone, routes du jeu (pointillés), repères, objets de
  collision.
- Mes routes : création, renommage, copie d'une route du jeu ; outils
  Point (Alt+clic : position de Snake), Ligne (insérer), Déplacer,
  Sélection, Supprimer ; coordonnées, action, attente, regard, boucle.
  Enregistrées par zone dans `routes/<zone>.json` ; export JSON.
- Jeu lancé (1.4.1 ou 1.4.2) : Snake en direct sur la carte, zone du jeu,
  « TP Snake ici », « Point = Snake ».

## À venir

- Murs et sol : la géométrie de collision du `.geom` est compressée, à
  décoder.
- Vue 3D.
- Injection des routes dans le jeu et attribution à un PNJ.

## Format `.geom` (relevé sur s04a40l)

Gros-boutien. Table de sections en `+0x20` (type << 16, taille, position) :
0 grille (origine, taille de cellule, nombre de cellules), 1 objets de
collision (entrées de 0x70) puis géométrie compressée, 6 repères (entrées
de 0x20 : genre, identifiant, position), 7 routes (identifiant, nombre de
points, points de 0x30 octets : position, indicateurs, point regardé…).
Coordonnées en millimètres, Y = hauteur.
