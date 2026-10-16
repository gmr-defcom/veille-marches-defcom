# -*- coding: utf-8 -*-
"""
Collecte hebdomadaire des avis de marchés de défense — GMR DEFCOM.

Sources utilisées : BOAMP et TED.
(Fonctions DECP et OCCAR conservées mais non utilisées : la base DECP ne contient pas, à ce jour,
les fiches du ministère des Armées, et l'OCCAR refuse la lecture automatique de sa page.)
Résultat : donnees/derniere-semaine.json (+ une copie datée dans donnees/archives/).

Chaque source est interrogée séparément : si l'une est en panne, les autres
sont quand même enregistrées et la panne est signalée dans le fichier.
Le programme n'utilise que la bibliothèque standard de Python (aucune installation).
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

JOURS = int(os.environ.get("JOURS", "7"))           # période couverte
DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donnees")
UA = "GMR-DEFCOM-veille/1.0 (recherche universitaire; contact via chairedefcom.fr)"

aujourdhui = datetime.now(timezone.utc).date()
debut = aujourdhui - timedelta(days=JOURS)


# ---------------------------------------------------------------- outils réseau
NAVIGATEUR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36",
              "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
              "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}


def http(url, data=None, headers=None, essais=3):
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9"}
    h.update(headers or {})
    corps = json.dumps(data).encode("utf-8") if data is not None else None
    if corps is not None:
        h["Content-Type"] = "application/json"
    derniere = None
    for i in range(essais):
        try:
            req = urllib.request.Request(url, data=corps, headers=h)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # réessai simple
            derniere = e
            time.sleep(5 * (i + 1))
    raise RuntimeError("%s : %s" % (url.split("?")[0], derniere))


def http_json(url, data=None):
    return json.loads(http(url, data))


# ---------------------------------------------------------------------- BOAMP
def boamp():
    base = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
    where = (
        "dateparution>=date'%s' AND (nomacheteur like \"MINARM*\" OR nomacheteur like \"MINDEF*\" "
        "OR nomacheteur like \"DGA*\" OR nomacheteur like \"*Direction générale de l'armement*\" "
        "OR nomacheteur like \"*commissariat*\" OR nomacheteur like \"PFC*\" OR nomacheteur like \"PFAF*\" "
        "OR nomacheteur like \"*SIMMT*\" OR nomacheteur like \"*ministère des armées*\" "
        "OR descripteur_libelle=\"Armement\")" % debut.isoformat()
    )
    champs = ("idweb,dateparution,nomacheteur,objet,nature_libelle,type_procedure,procedure_libelle,"
              "descripteur_libelle,titulaire,datelimitereponse,url_avis")
    avis, offset, total = [], 0, None
    while True:
        q = urllib.parse.urlencode({"select": champs, "where": where, "order_by": "dateparution desc",
                                    "limit": 100, "offset": offset})
        j = http_json(base + "?" + q)
        total = j.get("total_count", 0)
        lot = j.get("results", [])
        avis += lot
        offset += len(lot)
        if not lot or offset >= total or offset >= 500:
            break
    return {"total": total, "avis": avis}


# ------------------------------------------------------------------------ TED
TED_CHAMPS = ["publication-number", "publication-date", "notice-title", "buyer-name", "buyer-country",
              "notice-type", "classification-cpv", "legal-basis", "BT-105-Procedure", "BT-106-Procedure",
              "direct-award-justification-proc", "framework-agreement-lot", "result-value-notice",
              "result-value-cur-notice", "winner-partname", "winner-country", "deadline-receipt-tender-date-lot"]


def ted():
    q = ("publication-date>=%s AND (classification-cpv IN (35300000 35400000 35500000 35600000 35700000) "
         "OR (main-activity=defence AND classification-cpv IN (18000000 32000000 33000000 34000000 35000000 "
         "38000000 48000000 72000000 73000000 50100000 50200000 50600000)))" % debut.strftime("%Y%m%d"))
    avis, total = [], None
    for page in range(1, 6):
        j = http_json("https://api.ted.europa.eu/v3/notices/search",
                      {"query": q, "fields": TED_CHAMPS, "limit": 100, "page": page,
                       "paginationMode": "PAGE_NUMBER"})
        if j.get("message"):
            raise RuntimeError("TED : %s" % j["message"])
        total = j.get("totalNoticeCount")
        lot = j.get("notices") or []
        for n in lot:
            n["lien"] = "https://ted.europa.eu/fr/notice/-/detail/%s" % n.get("publication-number")
        avis += lot
        if len(lot) < 100:
            break
    return {"total": total, "avis": avis}


# ----------------------------------------------------------------------- DECP
# Les DECP ne donnent que le SIRET de l'acheteur et du titulaire : on retient les marchés
# déclarés « de défense ou de sécurité » ou portant sur du matériel militaire (CPV 353 à 357),
# puis on retrouve les noms via l'annuaire public des entreprises.
def decp():
    base = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/decp-2022-marches-valides/records"
    # 110090016 = SIREN du ministère des Armées (DGA, SCA, SIMMT, bases… sont des établissements de ce SIREN)
    filtre = ('startswith(acheteur_id, "110090016") OR nature = "Marché de défense ou de sécurité" '
              'OR startswith(codecpv, "353") OR startswith(codecpv, "354") OR startswith(codecpv, "355") '
              'OR startswith(codecpv, "356") OR startswith(codecpv, "357")')
    where = "datepublicationdonnees >= date'%s' AND (%s)" % (debut.isoformat(), filtre)
    champs = ("id,nature,objet,codecpv,procedure,techniques,modalitesexecution,acheteur_id,titulaire_id_1,"
              "montant,dureemois,datenotification,datepublicationdonnees,offresrecues,idaccordcadre,source")
    avis, offset, total = [], 0, None
    while True:
        q = urllib.parse.urlencode({"select": champs, "where": where, "order_by": "montant desc",
                                    "limit": 100, "offset": offset})
        j = http_json(base + "?" + q)
        total = j.get("total_count", 0)
        lot = j.get("results", [])
        avis += lot
        offset += len(lot)
        if not lot or offset >= total or offset >= 200:
            break
    diagnostic = None
    if not avis:  # aide au réglage : combien de fiches publiées sur la période, toutes origines confondues ?
        try:
            q = urllib.parse.urlencode({"select": "count(*) as n", "where": "datepublicationdonnees >= date'%s'"
                                        % debut.isoformat()})
            diagnostic = {"fiches_publiees_sur_la_periode": http_json(base + "?" + q).get("results")}
            q = urllib.parse.urlencode({"select": "nature, count(*) as n", "group_by": "nature",
                                        "where": "datepublicationdonnees >= date'%s'" % debut.isoformat()})
            diagnostic["par_nature"] = http_json(base + "?" + q).get("results")
        except Exception as e:
            diagnostic = {"erreur": str(e)[:200]}
    noms = {}
    for a in avis:
        for cle in ("acheteur_id", "titulaire_id_1"):
            siret = (a.get(cle) or "").strip()
            if re.fullmatch(r"\d{9,14}", siret) and siret not in noms:
                noms[siret] = nom_entreprise(siret)
        a["acheteur_nom"] = noms.get((a.get("acheteur_id") or "").strip(), "")
        a["titulaire_nom"] = noms.get((a.get("titulaire_id_1") or "").strip(), "")
        a["lien"] = ("https://data.economie.gouv.fr/explore/dataset/decp-2022-marches-valides/table/?q=%s"
                     % urllib.parse.quote(a.get("id") or ""))
    r = {"total": total, "avis": avis}
    if diagnostic:
        r["diagnostic"] = diagnostic
    return r


def nom_entreprise(siret):
    try:
        j = http_json("https://recherche-entreprises.api.gouv.fr/search?q=%s&per_page=1" % siret[:9])
        r = (j.get("results") or [{}])[0]
        time.sleep(0.2)
        return r.get("nom_complet") or r.get("nom_raison_sociale") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------- OCCAR
class _Occar(HTMLParser):
    """Relève les titres (h2 à h4) de la page et les liens qui les suivent."""

    def __init__(self):
        super().__init__()
        self.blocs, self._titre, self._dans_titre, self._lien = [], None, False, None

    def handle_starttag(self, tag, attrs):
        if tag in ("h2", "h3", "h4"):
            self._dans_titre, self._titre = True, ""
        elif tag == "a" and self.blocs:
            href = dict(attrs).get("href") or ""
            if href and not href.startswith("#"):
                self._lien = urllib.parse.urljoin("https://www.occar.int", href)

    def handle_endtag(self, tag):
        if tag in ("h2", "h3", "h4") and self._dans_titre:
            self._dans_titre = False
            t = " ".join((self._titre or "").split())
            if len(t) > 15:
                self.blocs.append({"titre": t, "liens": []})
        elif tag == "a":
            self._lien = None

    def handle_data(self, data):
        if self._dans_titre:
            self._titre += data
        elif self._lien and self.blocs and self._lien not in self.blocs[-1]["liens"]:
            if "/edit" not in self._lien and "/delete" not in self._lien:
                self.blocs[-1]["liens"].append(self._lien)


def occar():
    url = "https://www.occar.int/business-opportunities/current-advertisements"
    p = _Occar()
    p.feed(http(url, headers=NAVIGATEUR))
    blocs = [b for b in p.blocs if not re.search(r"current advertisements|menu|footer", b["titre"], re.I)]
    etat_f = os.path.join(DOSSIER, "occar-etat.json")
    deja = set()
    if os.path.exists(etat_f):
        deja = set(json.load(open(etat_f, encoding="utf-8")).get("titres", []))
    premiere_fois = not os.path.exists(etat_f)
    for b in blocs:
        b["nouveau"] = premiere_fois or b["titre"] not in deja
        b["page"] = url
    json.dump({"titres": sorted({b["titre"] for b in blocs} | deja)}, open(etat_f, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return {"total": len(blocs), "avis": blocs, "premiere_collecte": premiere_fois}


# --------------------------------------------------------------------- main
def main():
    os.makedirs(os.path.join(DOSSIER, "archives"), exist_ok=True)
    resultat = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "periode": {"du": debut.isoformat(), "au": aujourdhui.isoformat()},
        "sources": {},
    }
    for nom, fonction in (("BOAMP", boamp), ("TED", ted)):
        try:
            r = fonction()
            resultat["sources"][nom] = {"statut": "ok", **r}
            print("%-6s ok    %s avis" % (nom, len(r["avis"])))
        except Exception as e:
            resultat["sources"][nom] = {"statut": "indisponible", "erreur": str(e)[:300], "avis": []}
            print("%-6s ÉCHEC %s" % (nom, e), file=sys.stderr)
    texte = json.dumps(resultat, ensure_ascii=False, indent=1)
    open(os.path.join(DOSSIER, "derniere-semaine.json"), "w", encoding="utf-8").write(texte)
    open(os.path.join(DOSSIER, "archives", aujourdhui.isoformat() + ".json"), "w", encoding="utf-8").write(texte)
    # Échec total seulement si aucune source n'a répondu
    if all(s["statut"] != "ok" for s in resultat["sources"].values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
