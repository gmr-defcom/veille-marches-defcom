# Veille des marchés de défense — GMR DEFCOM

Collecte automatique, chaque lundi matin, des avis de marchés publics de défense publiés la semaine précédente :

- **BOAMP** : avis des acheteurs français de la défense (ministère des Armées, DGA, Commissariat…) ;
- **TED** : avis européens (toute l'Union) portant sur du matériel militaire ou passés par des acheteurs de défense ;
- (DECP et OCCAR ont été testés puis écartés : données des Armées absentes de la base DECP, page OCCAR fermée à la lecture automatique.)

Le résultat est enregistré dans [`donnees/derniere-semaine.json`](donnees/derniere-semaine.json) ; une copie datée est conservée dans `donnees/archives/`.
Ce fichier est ensuite lu par Claude, qui sélectionne les avis pertinents, rédige un éclairage juridique et prépare le mail de veille du GMR.

Toutes les données proviennent de sources publiques officielles.
