# Guide de l'éditeur

[← Retour au README](../README.md) · *[English version](en/guide.md)*

- [La fenêtre](#la-fenêtre)
- [La carte 2D et la vue 3D](#la-carte-2d-et-la-vue-3d)
- [Créer une route](#créer-une-route)
- [Les routes du jeu et les routes fournies](#les-routes-du-jeu-et-les-routes-fournies)
- [Donner une route à un PNJ](#donner-une-route-à-un-pnj)
- [Comment un PNJ suit une route](#comment-un-pnj-suit-une-route)
- [Problèmes courants](#problèmes-courants)

## La fenêtre

- **Barre du haut** :
  - **Zone affichée** : la zone dont la carte est montrée ; **Zone du jeu**
    affiche celle où se trouve Snake ;
  - **Suivre le jeu** : cochée, la carte change toute seule quand le jeu
    change de zone ;
  - les **outils** : **Point**, **Ligne** (insérer un point dans un
    tronçon), **Déplacer**, **Sélection**, **Supprimer** ;
  - le passage **2D / 3D**, la **langue** (l'éditeur se relance) et les
    **mises à jour**.
- **Panneau de gauche** :
  - **Calques** : sol praticable, murs et obstacles, passages entre
    cellules, décor de collision, décor texturé (jeu lancé), routes du
    jeu, repères, PNJ et animaux en direct, grille ;
  - **Niveau** : l'étage affiché (les cellules de -1,5 m à +2 m autour).
    **Auto** suit la hauteur de Snake ;
  - **Mes routes**, les **routes fournies** et les **routes du jeu**
    (recherche par identifiant, ou « PNJ » pour les routes attribuables).
- **Panneau de droite** : le **point sélectionné** et ses champs, puis
  l'**attribution** d'une route à un PNJ.
- La barre d'état indique la connexion au jeu (relu toutes les
  demi-secondes).

## La carte 2D et la vue 3D

- **Carte 2D** : molette pour zoomer, clic droit ou clic molette maintenu
  pour se déplacer.
- **Vue 3D** : le décor de collision, ou le **décor texturé** du jeu
  quand il est lancé (modèles et textures HD lus en direct, chargés en une
  seconde environ ; la première ouverture d'une zone est un peu plus
  longue). Commandes, comme le mode photo du trainer :
  - **ZQSD** pour se déplacer ; **A** ou **Maj** pour aller vite, **Ctrl**
    pour aller lentement ;
  - **clic molette** ou **clic gauche maintenu** + souris pour regarder ;
  - **clic droit** maintenu pour monter, **clic gauche maintenu** sans
    bouger pour descendre (aussi **Espace** et **C**) ;
  - **molette** pour zoomer ; **clic** pour choisir un point ;
    **double-clic** pour revenir à la vue de dessus.
- En 3D, l'outil **Point** pose le point **sur la surface** cliquée (sol,
  toit, rocher).

## Créer une route

1. **+ Nouvelle** dans « Mes routes » (double-clic pour la renommer).
2. Outil **Point** : chaque clic ajoute un point. **Point = Snake** place
   le point sur Snake (jeu lancé). **Maj + clic** sur un point ajoute un
   point au même endroit, comme les routes du jeu qui enchaînent plusieurs
   actions sur place.
3. Pour chaque point, à droite :
   - **Action à l'arrivée** et **Allure pour y aller** : codes connus
     nommés, les autres marqués comme hypothèses (**Nommer…** pour leur
     donner un nom, gardé pour toutes les zones) ;
   - **Attente** (65535 = sans fin), **Regard** (point visé ; **Regard =
     Snake**), **Orientation**, **Drapeaux** (**Bits…** pour les voir un
     par un) et paramètres ;
   - **Hauteur** : celle de Snake avec « Point = Snake » (environ 10 cm
     au-dessus des pieds d'un soldat, comme les routes du jeu).
4. **Parcours en boucle** ou non.

Les routes sont enregistrées par zone dans `routes/<zone>.json`.
**Exporter (JSON)** en fait une copie.

## Les routes du jeu et les routes fournies

- **Routes du jeu** : masquées par défaut ; cochez celles à afficher.
  Jeu lancé, leurs points se modifient **en direct** (déplacer un point,
  changer son action…) ; l'éditeur garde l'original et propose de le
  rétablir. Clic droit : **copier dans mes routes**.
- **Routes fournies** : routes toutes faites livrées avec l'éditeur et
  remplacées à chaque mise à jour, à attribuer telles quelles ou à copier
  dans vos routes.

## Donner une route à un PNJ

1. Jeu lancé, dans la zone de la route.
2. Choisissez le **PNJ** dans la liste, ou cliquez sur lui sur la carte.
3. Choisissez la route (du jeu, fournie ou à vous), puis **Attribuer la
   route**. Une route à vous est d'abord écrite dans le jeu, à la place
   d'une route de PNJ inutilisée (**Injecter dans le jeu** le fait à la
   main).
4. Le PNJ reçoit tout le point de départ (position, regard, action,
   allure, attente) et part tout de suite. Le message indique **le point
   de départ choisi** : le plus proche qu'il peut rejoindre en ligne
   droite.

Le trainer (MGS4-Trainer) peut faire apparaître des PNJ **sans route**, en
garde là où ils apparaissent, prêts à recevoir une route de l'éditeur
(case « Sans route (en faction) »).

**Animations de ce type…** note, pour un type de PNJ, les allures et
actions qui fonctionnent ou donnent une T-pose ; l'éditeur prévient avant
d'attribuer une route qui en contient.

## Comment un PNJ suit une route

- **En ligne droite** : un PNJ va droit vers le point visé, sans
  contourner les murs. Une route très éloignée lui fera traverser la zone
  tout droit (et il peut buter contre un obstacle).
- **Arrivée** : un point est atteint à environ **un mètre à
  l'horizontale**, quelle que soit la hauteur. Deux points de niveaux
  différents trop proches à l'horizontale sont validés sans changer de
  niveau : espacez-les d'environ 2 m.
- **Changer de niveau** :
  - pentes, rochers et marches jusqu'à environ 50 cm passent tout seuls ;
  - un **soldat** prend une **échelle** tout seul : un point en bas, le
    suivant en haut, sans code spécial ;
  - **Meryl** ne prend pas les échelles (elle reste en bas) ;
  - une paroi sans échelle n'a pas pu être franchie, même avec l'allure
    17 que le jeu met sur ses propres sauts.
- Une route **ouverte** (sans boucle) repart du dernier point vers le
  premier.
- En **alerte** ou en **combat**, un PNJ ignore sa route.
- Les animations disponibles dépendent du type de PNJ et de la zone :
  Meryl n'a pas « mains sur la tête », et certaines zones retirent des
  animations aux soldats (le trainer les rend aux PNJ qu'il ajoute).

## Problèmes courants

- **Le PNJ ne bouge pas** : vérifiez qu'il n'est pas en alerte, et que
  le point de départ annoncé est joignable. Un FROG placé par le script de
  la zone garde la route imposée par le script.
- **Le PNJ court contre un mur** : un obstacle est sur la ligne droite
  vers son point ; ajoutez un point intermédiaire.
- **Carte vide ou murs absents** : la zone n'a pas encore été vue en jeu ;
  passez-y une fois avec l'éditeur ouvert.
- **Plantage de l'éditeur** : le détail est dans `logs/editeur.log`.
