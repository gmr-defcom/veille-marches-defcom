# Veille des marchés de défense — GMR DEFCOM

Collecte automatique, chaque lundi matin, des avis de marchés publics de défense publiés la semaine précédente :

- **BOAMP** : avis des acheteurs français de la défense (ministère des Armées, DGA, Commissariat…) ;
- **TED** : avis européens portant sur du matériel militaire ou passés par des acheteurs de défense ;
- **DECP** : données essentielles des marchés de défense ou de sécurité et des marchés de matériel militaire ;
- **OCCAR** : nouveaux avis de l'Organisation conjointe de coopération en matière d'armement.

Le résultat est enregistré dans [`donnees/derniere-semaine.json`](donnees/derniere-semaine.json) ; une copie datée est conservée dans `donnees/archives/`.
Ce fichier est ensuite lu par Claude, qui sélectionne les avis pertinents, rédige un éclairage juridique et prépare le mail de veille du GMR.

Toutes les données proviennent de sources publiques officielles.
