# -*- coding: utf-8 -*-
"""
Test de faisabilité des sources de la veille GMR DEFCOM (actualités, textes, jurisprudence).

Lancé une seule fois, à la main, depuis l'onglet Actions de GitHub.
N'envoie aucun mail et ne touche pas à la collecte des marchés.
Écrit un rapport lisible : donnees/test-sources.md (et le détail brut dans donnees/test-sources.json).
N'utilise que la bibliothèque standard de Python.
"""
import io
import json
import os
import re
import tarfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree as ET

DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donnees")
MAINTENANT = datetime.now(timezone.utc)
J7 = MAINTENANT - timedelta(days=7)
J30 = MAINTENANT - timedelta(days=30)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml,application/atom+xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}

# Termes du directeur de thèse (volets BITD et DGA)
TERMES = [
    "BITD base industrielle technologique de défense",
    "BITDE base industrielle technologique de défense européenne",
    "industrie défense France Europe",
    "KNDS Thales Airbus Defence Safran Dassault Naval Group",
    "DGA direction générale armement",
    "DGA appel offres marché programme",
    "matériels défense programme armement France",
]
MOTS_DEFENSE = re.compile(r"défense|defence|defense|armement|armée|military|militaire|munition|DGA|BITD|EDIP|EDF\b", re.I)

rapport, brut = [], {}


def lire(url, data=None, headers=None, limite=40_000_000):
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.status, r.read(limite), r.headers.get("Content-Type", "")


def date_souple(t):
    if not t:
        return None
    t = t.strip()
    try:
        d = parsedate_to_datetime(t)
    except Exception:
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def lire_flux(octets):
    """Renvoie la liste des éléments (titre, date, lien) d'un flux RSS ou Atom."""
    racine = ET.fromstring(octets)
    items = []
    for it in racine.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        champ = {c.tag.split("}")[-1]: c for c in it}
        titre = (champ.get("title").text or "") if champ.get("title") is not None else ""
        lien = ""
        if champ.get("link") is not None:
            lien = champ["link"].text or champ["link"].get("href") or ""
        d = None
        for nom in ("pubDate", "published", "updated", "date"):
            if champ.get(nom) is not None and champ[nom].text:
                d = date_souple(champ[nom].text)
                if d:
                    break
        items.append({"titre": unescape(titre.strip()), "date": d, "lien": lien.strip()})
    return items


def resume_flux(nom, url, filtre=None):
    """Teste un flux : réponse, nombre d'éléments, éléments des 7 derniers jours, exemples."""
    try:
        st, oc, ct = lire(url)
        items = lire_flux(oc)
        recents = [i for i in items if i["date"] and i["date"] >= J7]
        retenus = [i for i in recents if (filtre is None or filtre.search(i["titre"]))]
        plus_recent = max((i["date"] for i in items if i["date"]), default=None)
        brut[nom] = [{**i, "date": i["date"].isoformat() if i["date"] else None} for i in items[:50]]
        lignes = ["- **%s** : répond (HTTP %s). %d éléments dans le flux, %d des 7 derniers jours%s. Plus récent : %s."
                  % (nom, st, len(items), len(recents),
                     (", dont %d retenus par le filtre défense" % len(retenus)) if filtre else "",
                     plus_recent.date() if plus_recent else "date inconnue")]
        for i in (retenus or recents or items)[:3]:
            lignes.append("    - %s — %s — %s" % (i["date"].date() if i["date"] else "?", i["titre"][:140], i["lien"][:160]))
        return lignes, True
    except Exception as e:
        return ["- **%s** : ÉCHEC — %s" % (nom, str(e)[:200])], False


def decouvrir_flux(nom, page, filtre=None, essais=()):
    """Cherche les flux RSS/Atom annoncés par une page, plus quelques adresses classiques, et les teste."""
    lignes, candidats = [], []
    try:
        st, oc, ct = lire(page)
        html = oc.decode("utf-8", "replace")
        lignes.append("- **%s** : la page %s répond (HTTP %s)." % (nom, page, st))
        for m in re.finditer(r"<link[^>]+>", html, re.I):
            bal = m.group(0)
            if re.search(r"rss|atom", bal, re.I):
                h = re.search(r'href=["\']([^"\']+)', bal)
                if h:
                    candidats.append(urllib.parse.urljoin(page, unescape(h.group(1))))
        for m in re.finditer(r'href=["\']([^"\']*(?:rss|feed|atom)[^"\']*)["\']', html, re.I):
            candidats.append(urllib.parse.urljoin(page, unescape(m.group(1))))
    except Exception as e:
        lignes.append("- **%s** : la page %s ne répond pas — %s" % (nom, page, str(e)[:200]))
    candidats += list(essais)
    vus, trouve = set(), False
    for c in candidats:
        if c in vus or len(vus) >= 8:
            continue
        vus.add(c)
        l, ok = resume_flux("%s — flux %s" % (nom, c), c, filtre)
        if ok:
            lignes += ["  " + x for x in l]
            trouve = True
            break
        lignes.append("  - flux essayé sans succès : %s (%s)" % (c, l[0].split("—", 1)[-1].strip()[:120]))
    if not trouve:
        lignes.append("  - **Aucun flux exploitable trouvé.**")
    return lignes


# ------------------------------------------------------------------ 1. Actualités
def actualites():
    rapport.append("## 1. Actualités\n")
    rapport.append("### Google Actualités — un flux par terme du directeur (7 derniers jours)\n")
    for t in TERMES:
        q = urllib.parse.quote(t + " when:7d")
        url = "https://news.google.com/rss/search?q=%s&hl=fr&gl=FR&ceid=FR:fr" % q
        l, _ = resume_flux("« %s »" % t, url)
        rapport.extend(l)
        time.sleep(2)
    rapport.append("\n### Ministère des Armées et DGA\n")
    rapport.extend(decouvrir_flux("defense.gouv.fr/dga", "https://www.defense.gouv.fr/dga",
                                  essais=("https://www.defense.gouv.fr/rss.xml", "https://www.defense.gouv.fr/dga/rss.xml",
                                          "https://www.defense.gouv.fr/rss")))
    rapport.append("\n### Commission européenne — industrie de défense et espace\n")
    rapport.extend(decouvrir_flux("DG DEFIS", "https://defence-industry-space.ec.europa.eu/news_en", filtre=MOTS_DEFENSE,
                                  essais=("https://defence-industry-space.ec.europa.eu/node/feed_en",
                                          "https://ec.europa.eu/commission/presscorner/api/rss?language=fr")))
    rapport.append("\n### Agence européenne de défense\n")
    rapport.extend(decouvrir_flux("AED", "https://eda.europa.eu/news-and-events/news",
                                  essais=("https://eda.europa.eu/rss", "https://eda.europa.eu/news-and-events/news/rss")))
    rapport.append("")


# ------------------------------------------------------------------ 2. Journal officiel (DILA)
def jorf():
    rapport.append("## 2. Journal officiel de la République française (données ouvertes DILA)\n")
    base = "https://echanges.dila.gouv.fr/OPENDATA/JORF/"
    try:
        st, oc, ct = lire(base)
        noms = sorted(set(re.findall(r'href="(JORF_\d{8}[^"]*\.tar\.gz)"', oc.decode("utf-8", "replace"))))
        recents = [n for n in noms if n[5:13] >= J7.strftime("%Y%m%d")]
        rapport.append("- Liste des fichiers : répond (HTTP %s). %d fichiers en ligne, %d des 7 derniers jours. Dernier : %s."
                       % (st, len(noms), len(recents), noms[-1] if noms else "aucun"))
        textes, retenus = {}, []
        for n in recents[-7:]:
            st, oc, ct = lire(base + n)
            with tarfile.open(fileobj=io.BytesIO(oc), mode="r:gz") as tar:
                for m in tar.getmembers():
                    if not m.name.endswith(".xml"):
                        continue
                    x = tar.extractfile(m).read().decode("utf-8", "replace")
                    nor = re.search(r"<NOR>([^<]+)</NOR>", x)
                    titre = re.search(r"<TITRE_TXT[^>]*>([^<]+)</TITRE_TXT>", x) or re.search(r"<TITREFULL>([^<]+)</TITREFULL>", x)
                    if nor and titre and nor.group(1) not in textes:
                        textes[nor.group(1)] = unescape(titre.group(1)).strip()
        for nor, t in textes.items():
            ministere = nor[:3]
            if ministere in ("ARM", "ECO") and re.search(
                    r"commande publique|marchés? de défense|programmation militaire|armement|industrie de défense|défense", t, re.I) \
                    and not re.search(r"nomination|portant promotion|tableau d.avancement|concours|décoration|affectation", t, re.I):
                retenus.append((nor, t))
        armees = sum(1 for n in textes if n.startswith("ARM"))
        rapport.append("- %d textes lus sur la période, dont %d du ministère des Armées (NOR ARM). %d passent le filtre de la veille."
                       % (len(textes), armees, len(retenus)))
        for nor, t in retenus[:5]:
            rapport.append("    - %s — %s" % (nor, t[:180]))
        brut["JORF"] = {"textes": len(textes), "retenus": retenus[:50]}
    except Exception as e:
        rapport.append("- ÉCHEC — %s" % str(e)[:300])
    rapport.append("")


# ------------------------------------------------------------------ 3. Union européenne (Cellar / EUR-Lex)
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
PREF = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n")


def sparql(requete):
    url = SPARQL + "?" + urllib.parse.urlencode({"query": PREF + requete, "format": "application/sparql-results+json"})
    st, oc, ct = lire(url, headers={"Accept": "application/sparql-results+json"})
    return [{k: v["value"] for k, v in b.items()} for b in json.loads(oc)["results"]["bindings"]]


def union():
    rapport.append("## 3. Union européenne (base de l'Office des publications, qui alimente EUR-Lex)\n")
    d30 = J30.date().isoformat()
    essais = [
        ("Actes publiés au JOUE ces 30 derniers jours dont le titre touche à la défense", """
SELECT DISTINCT ?celex ?date ?titre WHERE {
  ?w cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date .
  FILTER(?date >= "%s"^^xsd:date) FILTER(STRSTARTS(STR(?celex), "3"))
  ?e cdm:expression_belongs_to_work ?w ; cdm:expression_title ?titre ;
     cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/FRA> .
  FILTER(REGEX(?titre, "défense|armement|munitions|militaire", "i"))
} ORDER BY DESC(?date) LIMIT 30""" % d30),
        ("Décisions de la Cour et du Tribunal (arrêts, ordonnances, conclusions) de ces 30 derniers jours", """
SELECT DISTINCT ?celex ?date WHERE {
  ?w cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date .
  FILTER(?date >= "%s"^^xsd:date) FILTER(STRSTARTS(STR(?celex), "6"))
} ORDER BY DESC(?date) LIMIT 300""" % d30),
        ("Décisions de la Cour et du Tribunal citant la directive 2009/81/CE (toutes dates, pour vérifier le lien de citation)", """
SELECT DISTINCT ?celex ?date WHERE {
  ?d cdm:resource_legal_id_celex ?c . FILTER(STR(?c) = "32009L0081")
  ?w cdm:work_cites_work ?d ; cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date .
  FILTER(STRSTARTS(STR(?celex), "6"))
} ORDER BY DESC(?date) LIMIT 20"""),
    ]
    for titre, q in essais:
        try:
            res = sparql(q)
            brut["UE — " + titre] = res
            ligne = "- **%s** : répond. %d résultats." % (titre, len(res))
            if res and "celex" in res[0] and res[0]["celex"].startswith("6"):
                types = {}
                for r in res:
                    k = r["celex"][5:7]
                    types[k] = types.get(k, 0) + 1
                ligne += " Répartition par type (CJ = arrêt Cour, CO = ordonnance Cour, CC = conclusions, TJ/TO = Tribunal) : %s." % types
            rapport.append(ligne)
            for r in res[:4]:
                rapport.append("    - %s — %s — %s" % (r.get("date", "?")[:10], r.get("celex", ""), r.get("titre", "")[:150]))
        except Exception as e:
            rapport.append("- **%s** : ÉCHEC — %s" % (titre, str(e)[:250]))
        time.sleep(2)
    rapport.append("")


# ------------------------------------------------------------------ 4. Justice administrative
def justice_administrative():
    rapport.append("## 4. Juridictions administratives (données ouvertes)\n")
    for page in ("https://opendata.justice-administrative.fr/", "https://opendata.justice-administrative.fr/DCE/",
                 "https://opendata.justice-administrative.fr/DCA/", "https://opendata.justice-administrative.fr/DTA/"):
        try:
            st, oc, ct = lire(page, limite=3_000_000)
            html = oc.decode("utf-8", "replace")
            liens = re.findall(r'href=["\']([^"\'#?]+)["\']', html)
            fichiers = [l for l in liens if re.search(r"\.(zip|tar|gz|xml|json)$", l, re.I)]
            rapport.append("- %s : répond (HTTP %s, %s). %d liens, dont %d fichiers. Exemples : %s"
                           % (page, st, ct.split(";")[0], len(liens), len(fichiers),
                              ", ".join((fichiers or liens)[-6:])[:400]))
            brut["JA " + page] = (fichiers or liens)[-200:]
        except Exception as e:
            rapport.append("- %s : ÉCHEC — %s" % (page, str(e)[:200]))
    rapport.append("")


# ------------------------------------------------------------------ 5. Conseil constitutionnel
def conseil_constitutionnel():
    rapport.append("## 5. Conseil constitutionnel\n")
    rapport.extend(decouvrir_flux("Conseil constitutionnel", "https://www.conseil-constitutionnel.fr/decisions",
                                  essais=("https://www.conseil-constitutionnel.fr/rss.xml",
                                          "https://www.conseil-constitutionnel.fr/rss/decisions.xml",
                                          "https://www.conseil-constitutionnel.fr/feed")))
    rapport.append("")


def main():
    os.makedirs(DOSSIER, exist_ok=True)
    rapport.append("# Test des sources de la veille GMR DEFCOM\n")
    rapport.append("Lancé le %s (UTC). Période « 7 jours » : depuis le %s.\n"
                   % (MAINTENANT.strftime("%d/%m/%Y à %H:%M"), J7.date().isoformat()))
    for etape in (actualites, jorf, union, justice_administrative, conseil_constitutionnel):
        try:
            etape()
        except Exception as e:
            rapport.append("- Étape %s interrompue : %s\n" % (etape.__name__, str(e)[:300]))
    open(os.path.join(DOSSIER, "test-sources.md"), "w", encoding="utf-8").write("\n".join(rapport) + "\n")
    json.dump(brut, open(os.path.join(DOSSIER, "test-sources.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\n".join(rapport))


if __name__ == "__main__":
    main()
