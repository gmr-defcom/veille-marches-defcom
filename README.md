# Veille de la commande publique de défense — GMR DEFCOM

Collecte automatique, chaque lundi matin, des éléments publiés la semaine précédente, en trois rubriques :

- **Marchés** (`collecte.py`, 4 h UTC) : avis du **BOAMP** (acheteurs français de la défense) et de **TED** (toute l'Union : matériel militaire ou acheteurs de défense) → [`donnees/derniere-semaine.json`](donnees/derniere-semaine.json) ;
- **Actualités, textes et jurisprudence** (`collecte_veille.py`, 4 h 30 UTC) → [`donnees/veille-semaine.json`](donnees/veille-semaine.json) :
  - actualités : Google Actualités (termes de recherche du GMR, sites du ministère des Armées, de l'Agence européenne de défense et de la DG Industrie de défense et espace) ;
  - textes : Journal officiel (données ouvertes DILA) et Journal officiel de l'Union (Office des publications) ;
  - jurisprudence : décisions des juridictions administratives mises en ligne dans la semaine (données ouvertes), Conseil constitutionnel, Cour de justice et Tribunal de l'Union (par les textes cités).

Une copie datée de chaque collecte est conservée dans `donnees/archives/` ; `donnees/etat-veille.json` mémorise les éléments déjà transmis pour ne jamais les proposer deux fois.
Ces fichiers sont lus par la tâche planifiée Claude de chaque professeur (`veille.py`), qui sélectionne les éléments pertinents, rédige les fiches et prépare le mail de veille du GMR, avec un bouton « Publier » vers le site.
Le programme `maintien.yml` note une date le 15 de chaque mois pour que GitHub ne mette pas les collectes en pause.

Toutes les données proviennent de sources publiques officielles.
