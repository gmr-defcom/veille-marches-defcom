# -*- coding: utf-8 -*-
"""
Test de faisabilité des sources de la veille GMR DEFCOM — second passage (points restés ouverts).

Lancé à la main depuis l'onglet Actions. N'envoie aucun mail, ne touche pas à la collecte des marchés.
Écrit donnees/test-sources.md (rapport lisible) et donnees/test-sources.json (détail brut).
Bibliothèque standard de Python uniquement.
"""
import io
import json
import os
import re
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree as ET

DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donnees")
MAINTENANT = datetime.now(timezone.utc)
J7 = MAINTENANT - timedelta(days=7)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}
rapport, brut = [], {}


def lire(url, headers=None, limite=60_000_000):
    h = dict(UA)
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
        return r.status, r.read(limite), r.headers


def date_souple(t):
    if not t:
        return None
    t = t.strip()
    for f in (lambda x: parsedate_to_datetime(x), lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            d = f(t)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
    return None


def items_flux(octets):
    racine = ET.fromstring(octets)
    out = []
    for it in racine.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        ch = {}
        for c in it:
            ch.setdefault(c.tag.split("}")[-1], c)
        titre = unescape((ch["title"].text or "").strip()) if "title" in ch else ""
        lien = (ch["link"].text or ch["link"].get("href") or "").strip() if "link" in ch else ""
        source = ch["source"].get("url", "") if "source" in ch else ""
        d = None
        for nom in ("pubDate", "published", "updated", "date", "issued"):
            if nom in ch and ch[nom].text:
                d = date_souple(ch[nom].text)
                if d:
                    break
        out.append({"titre": titre, "lien": lien, "source": source, "date": d, "balises": sorted(ch)})
    return out


# ------------------------------------------------------------------ 1. Google Actualités limité à un site
def actualites_sites():
    rapport.append("## 1. Google Actualités limité aux sites officiels (7 derniers jours)\n")
    requetes = [
        ("Ministère des Armées / DGA", "site:defense.gouv.fr DGA"),
        ("Ministère des Armées (tout le site)", "site:defense.gouv.fr"),
        ("Agence européenne de défense", "site:eda.europa.eu"),
        ("Commission — industrie de défense et espace", "site:defence-industry-space.ec.europa.eu"),
        ("Commission — communiqués sur la défense", "site:ec.europa.eu defence industry"),
        ("Commission — communiqués (commission.europa.eu)", "site:commission.europa.eu defence"),
    ]
    for nom, q in requetes:
        for langue in (("fr", "FR", "FR:fr"), ("en", "BE", "BE:en")):
            url = "https://news.google.com/rss/search?q=%s&hl=%s&gl=%s&ceid=%s" % (
                urllib.parse.quote(q + " when:7d"), *langue)
            try:
                st, oc, h = lire(url)
                it = items_flux(oc)
                rec = [i for i in it if i["date"] and i["date"] >= J7]
                rapport.append("- **%s** (« %s », édition %s) : %d éléments, %d des 7 derniers jours."
                               % (nom, q, langue[0], len(it), len(rec)))
                for i in rec[:4]:
                    rapport.append("    - %s — %s — source : %s" % (i["date"].date(), i["titre"][:140], i["source"]))
                brut["GN " + q + " " + langue[0]] = [{**i, "date": str(i["date"])} for i in it[:30]]
            except Exception as e:
                rapport.append("- **%s** (%s) : ÉCHEC — %s" % (nom, langue[0], str(e)[:200]))
            time.sleep(2)
    rapport.append("")


# ------------------------------------------------------------------ 2. Journal officiel : filtre par date de publication
RE_VEILLE = re.compile(r"commande publique|march[ée]s? (?:publics? )?de d[ée]fense|programmation militaire|"
                       r"loi de finances|armement|industrie de d[ée]fense|code de la d[ée]fense|mat[ée]riels? de guerre|"
                       r"secret de la d[ée]fense|seuils", re.I)
RE_ECARTE = re.compile(r"nomination|portant promotion|tableau d.avancement|concours|examen professionnel|d[ée]coration|"
                       r"affectation|cessation de fonctions|admission|radiation|r[ée]int[ée]gration|d[ée]l[ée]gation de signature|"
                       r"citation|m[ée]daille|attribution de la|liste d.aptitude|portant inscription", re.I)


def champ(x, nom):
    m = re.search(r"<%s[^>]*>(.*?)</%s>" % (nom, nom), x, re.S)
    return unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


def jorf():
    rapport.append("## 2. Journal officiel — tri par date de publication\n")
    base = "https://echanges.dila.gouv.fr/OPENDATA/JORF/"
    try:
        st, oc, h = lire(base)
        noms = sorted(set(re.findall(r'href="(JORF_\d{8}[^"]*\.tar\.gz)"', oc.decode("utf-8", "replace"))))
        recents = [n for n in noms if n[5:13] >= J7.strftime("%Y%m%d")]
        textes, chemins = {}, {}
        for n in recents:
            st, oc, h = lire(base + n)
            with tarfile.open(fileobj=io.BytesIO(oc), mode="r:gz") as tar:
                for m in tar.getmembers():
                    if not m.name.endswith(".xml"):
                        continue
                    x = tar.extractfile(m).read().decode("utf-8", "replace")
                    nor = champ(x, "NOR")
                    if not nor:
                        continue
                    sorte = m.name.split("/")[-3] if m.name.count("/") > 3 else "?"
                    chemins[sorte] = chemins.get(sorte, 0) + 1
                    t = textes.setdefault(nor, {})
                    for c in ("DATE_PUBLI", "DATE_TEXTE", "NATURE", "MINISTERE", "TITREFULL", "TITRE_TXT"):
                        v = champ(x, c)
                        if v and not t.get(c):
                            t[c] = v
        jour7 = J7.date().isoformat()
        publies = {n: t for n, t in textes.items() if t.get("DATE_PUBLI", "") >= jour7}
        rapport.append("- %d fichiers quotidiens lus, %d textes distincts, dont **%d publiés ces 7 derniers jours**."
                       % (len(recents), len(textes), len(publies)))
        rapport.append("- Types de fichiers rencontrés : %s" % dict(sorted(chemins.items(), key=lambda x: -x[1])[:8]))
        natures = {}
        for t in publies.values():
            natures[t.get("NATURE", "?")] = natures.get(t.get("NATURE", "?"), 0) + 1
        rapport.append("- Nature des textes publiés : %s" % natures)
        armees = {n: t for n, t in publies.items() if n.startswith("ARM")}
        rapport.append("- Textes du ministère des Armées (NOR ARM) publiés : %d. Les voici, pour juger le filtre :" % len(armees))
        for n, t in list(armees.items())[:30]:
            titre = t.get("TITREFULL") or t.get("TITRE_TXT", "")
            etat = "écarté" if RE_ECARTE.search(titre) else ("RETENU" if RE_VEILLE.search(titre) else "hors sujet")
            rapport.append("    - [%s] %s — %s — %s" % (etat, t.get("DATE_PUBLI"), n, titre[:160]))
        retenus = [(n, t) for n, t in publies.items()
                   if RE_VEILLE.search(t.get("TITREFULL") or t.get("TITRE_TXT", ""))
                   and not RE_ECARTE.search(t.get("TITREFULL") or t.get("TITRE_TXT", ""))]
        rapport.append("- **Textes retenus par le filtre, tous ministères confondus : %d**" % len(retenus))
        for n, t in retenus[:20]:
            rapport.append("    - %s — %s — %s" % (t.get("DATE_PUBLI"), n, (t.get("TITREFULL") or t.get("TITRE_TXT", ""))[:180]))
        brut["JORF"] = publies
    except Exception as e:
        rapport.append("- ÉCHEC — %s" % str(e)[:300])
    rapport.append("")


# ------------------------------------------------------------------ 3. Union européenne : actes et jurisprudence par citation
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
PREF = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n")


def sparql(q):
    url = SPARQL + "?" + urllib.parse.urlencode({"query": PREF + q, "format": "application/sparql-results+json"})
    st, oc, h = lire(url, headers={"Accept": "application/sparql-results+json"})
    return [{k: v["value"] for k, v in b.items()} for b in json.loads(oc)["results"]["bindings"]]


def union():
    rapport.append("## 3. Union européenne\n")
    d30 = (MAINTENANT - timedelta(days=30)).date().isoformat()
    d365 = (MAINTENANT - timedelta(days=365)).date().isoformat()
    essais = [
        ("Règlements et directives (hors PESC) des 30 derniers jours touchant à la défense", """
SELECT DISTINCT ?celex ?date ?titre WHERE {
  ?w cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date .
  FILTER(?date >= "%s"^^xsd:date) FILTER(REGEX(STR(?celex), "^3[0-9]{4}[RL]"))
  ?e cdm:expression_belongs_to_work ?w ; cdm:expression_title ?titre ;
     cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/FRA> .
  FILTER(REGEX(?titre, "défense|armement|munitions|2009/81", "i")) FILTER(!REGEX(?titre, "PESC"))
} ORDER BY DESC(?date) LIMIT 30""" % d30),
        ("Même recherche sur 365 jours (pour juger le volume habituel)", """
SELECT DISTINCT ?celex ?date ?titre WHERE {
  ?w cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date .
  FILTER(?date >= "%s"^^xsd:date) FILTER(REGEX(STR(?celex), "^3[0-9]{4}[RL]"))
  ?e cdm:expression_belongs_to_work ?w ; cdm:expression_title ?titre ;
     cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/FRA> .
  FILTER(REGEX(?titre, "défense|armement|munitions|2009/81", "i")) FILTER(!REGEX(?titre, "PESC"))
} ORDER BY DESC(?date) LIMIT 40""" % d365),
        ("Jurisprudence de l'Union des 365 derniers jours citant 2009/81, l'article 346 TFUE, 2014/23 ou 2014/24", """
SELECT DISTINCT ?celex ?date ?cite WHERE {
  VALUES ?cite { "32009L0081" "12008E346" "12012E346" "12016E346" "32014L0023" "32014L0024" }
  ?d cdm:resource_legal_id_celex ?c . FILTER(STR(?c) = ?cite)
  ?w cdm:work_cites_work ?d ; cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date .
  FILTER(STRSTARTS(STR(?celex), "6")) FILTER(?date >= "%s"^^xsd:date)
} ORDER BY DESC(?date) LIMIT 100""" % d365),
    ]
    for titre, q in essais:
        try:
            res = sparql(q)
            brut["UE " + titre] = res
            rapport.append("- **%s** : %d résultats." % (titre, len(res)))
            if res and "cite" in res[0]:
                par = {}
                for r in res:
                    par[r["cite"]] = par.get(r["cite"], 0) + 1
                rapport.append("    - Répartition par texte cité : %s" % par)
            for r in res[:8]:
                rapport.append("    - %s — %s — %s" % (r.get("date", "")[:10], r.get("celex", ""), r.get("titre", r.get("cite", ""))[:150]))
        except Exception as e:
            rapport.append("- **%s** : ÉCHEC — %s" % (titre, str(e)[:250]))
        time.sleep(2)
    rapport.append("")


# ------------------------------------------------------------------ 4. Justice administrative : contenu d'un fichier mensuel
RE_JA = re.compile(r"[LR]\.?\s?23\d\d-\d+|march[ée]s? (?:publics? )?de d[ée]fense|d[ée]fense ou de s[ée]curit[ée]|"
                   r"concession de d[ée]fense|march[ée]s? classifi[ée]s?|2009/81|346 du trait[ée]|"
                   r"L\.?\s?3(?:114-3|123-1[234]|123-21|124-6|133-4|212-3|215-1)|R\.?\s?3(?:122-11|123-[368]|221-[1-5])", re.I)


def justice_administrative():
    rapport.append("## 4. Juridictions administratives — contenu des fichiers mensuels\n")
    for code, dossier in (("CE", "DCE"), ("CAA", "DCA"), ("TA", "DTA")):
        try:
            st, oc, h = lire("https://opendata.justice-administrative.fr/%s/" % dossier, limite=3_000_000)
            fichiers = sorted(set(re.findall(r'href=["\'](/%s/\d{4}/\d{2}/[^"\']+\.zip)' % dossier, oc.decode("utf-8", "replace"))))
            dernier = fichiers[-1]
            url = "https://opendata.justice-administrative.fr" + dernier
            req = urllib.request.Request(url, headers=UA, method="HEAD")
            with urllib.request.urlopen(req, timeout=60) as r:
                taille = int(r.headers.get("Content-Length", "0"))
                modif = r.headers.get("Last-Modified", "?")
            rapport.append("- **%s** : dernier fichier %s — %.1f Mo — modifié le %s." % (code, dernier, taille / 1e6, modif))
            if taille > 400_000_000:
                rapport.append("    - Fichier trop gros pour ce test, non téléchargé.")
                continue
            with tempfile.TemporaryFile() as tmp:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
                    while True:
                        bloc = r.read(1 << 20)
                        if not bloc:
                            break
                        tmp.write(bloc)
                tmp.seek(0)
                with zipfile.ZipFile(tmp) as z:
                    noms = [n for n in z.namelist() if n.endswith(".xml")]
                    rapport.append("    - %d décisions (fichiers XML). Exemple de nom : %s" % (len(noms), noms[0] if noms else "—"))
                    if noms:
                        x = z.read(noms[0]).decode("utf-8", "replace")
                        balises = sorted(set(re.findall(r"<([A-Za-z_]+)[ >]", x)))
                        rapport.append("    - Balises d'une décision : %s" % ", ".join(balises)[:600])
                        brut["JA exemple " + code] = x[:3000]
                    trouves = []
                    for n in noms:
                        x = z.read(n).decode("utf-8", "replace")
                        m = RE_JA.search(x)
                        if m:
                            trouves.append((champ(x, "Numero_Dossier") or n, champ(x, "Date_Lecture"),
                                            champ(x, "Nom_Juridiction"), m.group(0)))
                    rapport.append("    - **Décisions qui contiennent un de nos critères : %d**" % len(trouves))
                    for t in trouves[:12]:
                        rapport.append("        - %s — %s — %s — terme trouvé : « %s »" % t)
                    brut["JA trouvés " + code] = trouves
        except Exception as e:
            rapport.append("- **%s** : ÉCHEC — %s" % (code, str(e)[:250]))
    rapport.append("")


# ------------------------------------------------------------------ 5. Conseil constitutionnel : dates du flux
def conseil_constitutionnel():
    rapport.append("## 5. Conseil constitutionnel — lecture des dates du flux\n")
    try:
        st, oc, h = lire("https://www.conseil-constitutionnel.fr/flux/rss.xml")
        it = items_flux(oc)
        racine = ET.fromstring(oc)
        premier = next((e for e in racine.iter() if e.tag.split("}")[-1] in ("item", "entry")), None)
        if premier is not None:
            rapport.append("- Balises d'un élément du flux : %s" % [c.tag for c in premier])
            rapport.append("- Contenu brut du premier élément : `%s`" % ET.tostring(premier, encoding="unicode")[:700].replace("\n", " "))
        datees = [i for i in it if i["date"]]
        rapport.append("- %d éléments, dont %d avec une date lue. Plus récent : %s."
                       % (len(it), len(datees), max((i["date"] for i in datees), default="—")))
        for i in it[:5]:
            rapport.append("    - %s — %s" % (i["date"].date() if i["date"] else "?", i["titre"][:140]))
    except Exception as e:
        rapport.append("- ÉCHEC — %s" % str(e)[:250])
    rapport.append("")


def main():
    os.makedirs(DOSSIER, exist_ok=True)
    rapport.append("# Test des sources de la veille GMR DEFCOM — second passage\n")
    rapport.append("Lancé le %s (UTC). Période « 7 jours » : depuis le %s.\n" % (MAINTENANT.strftime("%d/%m/%Y à %H:%M"), J7.date()))
    for etape in (actualites_sites, jorf, union, conseil_constitutionnel, justice_administrative):
        try:
            etape()
        except Exception as e:
            rapport.append("- Étape %s interrompue : %s\n" % (etape.__name__, str(e)[:300]))
    open(os.path.join(DOSSIER, "test-sources.md"), "w", encoding="utf-8").write("\n".join(rapport) + "\n")
    json.dump(brut, open(os.path.join(DOSSIER, "test-sources.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("\n".join(rapport))


if __name__ == "__main__":
    main()
