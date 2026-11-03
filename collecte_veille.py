# -*- coding: utf-8 -*-
"""
Collecte hebdomadaire de la veille d'actualité, des textes et de la jurisprudence — GMR DEFCOM.

Complète collecte.py (marchés BOAMP et TED), qui reste inchangé.
Sources (toutes publiques, sans inscription) :
  - Actualités : Google Actualités (termes du directeur de thèse + sites du ministère des Armées,
    de l'Agence européenne de défense et de la DG Industrie de défense et espace) ;
  - Textes : Journal officiel (données ouvertes DILA) et Journal officiel de l'Union (base de l'Office des publications) ;
  - Jurisprudence : justice administrative (données ouvertes), Conseil constitutionnel (flux RSS),
    Cour de justice et Tribunal (base de l'Office des publications, par les textes cités).
Résultat : donnees/veille-semaine.json (+ copie datée dans donnees/archives/).
Mémoire : donnees/etat-veille.json (éléments déjà transmis, pour ne jamais les proposer deux fois).
Chaque source est traitée séparément : une source en panne n'empêche pas les autres.
Bibliothèque standard de Python uniquement.
"""
import hashlib
import io
import json
import os
import re
import sys
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

JOURS = int(os.environ.get("JOURS", "7"))
DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donnees")
MAINTENANT = datetime.now(timezone.utc)
DEBUT = MAINTENANT - timedelta(days=JOURS)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}


# ------------------------------------------------------------------ outils
def lire(url, headers=None, limite=80_000_000, essais=3):
    h = dict(UA)
    h.update(headers or {})
    derniere = None
    for i in range(essais):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=180) as r:
                return r.read(limite)
        except Exception as e:
            derniere = e
            time.sleep(5 * (i + 1))
    raise RuntimeError("%s : %s" % (url.split("?")[0], derniere))


def telecharger(url, fichier):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=900) as r:
        while True:
            bloc = r.read(1 << 20)
            if not bloc:
                break
            fichier.write(bloc)
    fichier.seek(0)


def date_souple(t):
    if not t:
        return None
    t = t.strip()
    for f in (parsedate_to_datetime, lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            d = f(t)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def texte_brut(html):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


def champ(x, nom):
    m = re.search(r"<%s[^>]*>(.*?)</%s>" % (nom, nom), x, re.S)
    return texte_brut(m.group(1)) if m else ""


def items_flux(octets):
    """Éléments d'un flux RSS ou Atom : titre, lien, source, date, description."""
    out = []
    for it in ET.fromstring(octets).iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        ch = {}
        for c in it:
            ch.setdefault(c.tag.split("}")[-1], c)
        d = None
        for nom in ("pubDate", "published", "updated", "date"):
            if nom in ch:
                e = ch[nom]
                candidats = [e.text] + [s.get("datetime") for s in e.iter() if s.get("datetime")] + ["".join(e.itertext())]
                for c in candidats:
                    d = date_souple(c) if c and c.strip() else None
                    if d:
                        break
            if d:
                break
        out.append({
            "titre": texte_brut(ch["title"].text if "title" in ch else ""),
            "lien": ((ch["link"].text or ch["link"].get("href") or "") if "link" in ch else "").strip(),
            "source": texte_brut(ch["source"].text) if "source" in ch else "",
            "site": ch["source"].get("url", "") if "source" in ch else "",
            "description": texte_brut(ch["description"].text if "description" in ch else "")[:400],
            "date": d,
        })
    return out


def jour(d):
    return d.date().isoformat() if d else ""


def iso(t):
    """Date ISO (AAAA-MM-JJ) à partir des formats rencontrés : ISO, JJ/MM/AAAA, date RSS."""
    t = (t or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", t):
        return t[:10]
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))
    d = date_souple(t)
    return d.date().isoformat() if d else ""


def ident(*parties):
    return hashlib.sha1("|".join(parties).encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ I. Actualités
TERMES_DIRECTEUR = [
    "BITD base industrielle technologique de défense",
    "BITDE base industrielle technologique de défense européenne",
    "industrie défense France Europe",
    "KNDS Thales Airbus Defence Safran Dassault Naval Group",
    "DGA direction générale armement",
    "DGA appel offres marché programme",
    "matériels défense programme armement France",
]
SITES_OFFICIELS = [
    ("Ministère des Armées / DGA", "site:defense.gouv.fr DGA", "fr"),
    ("Agence européenne de défense", "site:eda.europa.eu", "en"),
    ("Commission européenne — DG Industrie de défense et espace", "site:defence-industry-space.ec.europa.eu", "en"),
]
EDITIONS = {"fr": ("fr", "FR", "FR:fr"), "en": ("en", "BE", "BE:en")}


def titre_utile(t):
    lettres = sum(ch.isalpha() for ch in t)
    return len(t) >= 25 and lettres >= 0.6 * len(t) and len(t.split()) >= 5


def vrai_lien(lien):
    """Remplace un lien Google Actualités (news.google.com/rss/articles/…) par l'adresse de l'article ; garde le lien d'origine en cas d'échec."""
    m = re.match(r"https://news\.google\.com/(?:rss/)?articles/([^?]+)", lien or "")
    if not m:
        return lien
    ident_g = m.group(1)
    try:
        ua = {"User-Agent": "Mozilla/5.0 (veille GMR DEFCOM)"}
        page = urllib.request.urlopen(urllib.request.Request(
            "https://news.google.com/rss/articles/%s" % ident_g, headers=ua), timeout=30).read().decode("utf-8", "replace")
        sg = re.search(r'data-n-a-sg="([^"]+)"', page).group(1)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page).group(1)
        req = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
               '"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]' % (ident_g, ts, sg))
        corps = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", req, None, "generic"]]])}).encode()
        rep = urllib.request.urlopen(urllib.request.Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute", data=corps,
            headers=dict(ua, **{"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})), timeout=30).read().decode("utf-8", "replace")
        donnees = json.loads(rep.split("\n\n", 1)[1])[:-2]
        url = json.loads(donnees[0][2])[1]
        return url if url.startswith("http") else lien
    except Exception:
        return lien


def actualites():
    vus, resultats = {}, []
    requetes = [("Terme du directeur", t, "fr") for t in TERMES_DIRECTEUR] + list(SITES_OFFICIELS)
    for volet, q, langue in requetes:
        url = "https://news.google.com/rss/search?q=%s&hl=%s&gl=%s&ceid=%s" % (
            (urllib.parse.quote(q + " when:%dd" % JOURS),) + EDITIONS[langue])
        for i in items_flux(lire(url)):
            if not i["date"] or i["date"] < DEBUT or not titre_utile(i["titre"]):
                continue
            titre = re.sub(r"\s+-\s+[^-]{2,60}$", "", i["titre"]).strip()   # retire « - Nom du journal »
            cle = re.sub(r"\W+", " ", titre.lower()).strip()[:90]
            if cle in vus:
                if q not in vus[cle]["requetes"]:
                    vus[cle]["requetes"].append(q)
                continue
            el = {"id": "ACT-" + ident(cle), "titre": titre, "source": i["source"] or volet, "site": i["site"],
                  "date": jour(i["date"]), "lien": i["lien"], "chapeau": i["description"],
                  "volet": volet if volet != "Terme du directeur" else "Presse", "requetes": [q]}
            vus[cle] = el
            resultats.append(el)
        time.sleep(2)
    resultats.sort(key=lambda e: e["date"], reverse=True)
    for el in resultats:   # liens directs vers les articles (et non vers Google Actualités)
        el["lien"] = vrai_lien(el["lien"])
        time.sleep(0.5)
    return resultats


# ------------------------------------------------------------------ II. Textes — Journal officiel (DILA)
RE_JORF_SUJET = re.compile(
    r"commande publique|march[ée]s? (?:publics? )?de d[ée]fense|d[ée]fense ou de s[ée]curit[ée]|programmation militaire|"
    r"loi de finances|armement|industrie de d[ée]fense|mat[ée]riels? de guerre|exportations? (?:d.armes|de mat[ée]riels)|"
    r"secret de la d[ée]fense|stocks? strat[ée]giques?|stockage strat[ée]gique|produits p[ée]troliers|"
    r"concessions? de d[ée]fense|direction g[ée]n[ée]rale de l.armement", re.I)
RE_JORF_ECARTE = re.compile(
    r"nomination|portant promotion|tableau d.avancement|tableau de classement|concours|examen professionnel|d[ée]coration|"
    r"affectation|cessation de fonctions|admission [àa] la retraite|radiation|r[ée]int[ée]gration|d[ée]l[ée]gation de signature|"
    r"citation|m[ée]daille|liste d.aptitude|portant inscription|recrutement|r[ée]mun[ée]ration|indemnit|prime |"
    r"bonification|r[ée]serve op[ée]rationnelle|statut particulier|dialogue social|vacance d.emploi", re.I)


def textes_jorf():
    base = "https://echanges.dila.gouv.fr/OPENDATA/JORF/"
    noms = sorted(set(re.findall(r'href="(JORF_\d{8}[^"]*\.tar\.gz)"', lire(base).decode("utf-8", "replace"))))
    a_lire = [n for n in noms if n[5:13] >= (DEBUT - timedelta(days=1)).strftime("%Y%m%d")]
    textes = {}
    for n in a_lire:
        with tarfile.open(fileobj=io.BytesIO(lire(base + n)), mode="r:gz") as tar:
            for m in tar.getmembers():
                if not m.name.endswith(".xml"):
                    continue
                x = tar.extractfile(m).read().decode("utf-8", "replace")
                nor = champ(x, "NOR")
                if not nor:
                    continue
                t = textes.setdefault(nor, {})
                for c in ("DATE_PUBLI", "DATE_TEXTE", "NATURE", "MINISTERE", "TITREFULL", "TITRE_TXT", "NUM"):
                    v = champ(x, c)
                    if v and not t.get(c):
                        t[c] = v
                j = re.search(r"JORFTEXT\d{12}", x)
                if j and not t.get("ID"):
                    t["ID"] = j.group(0)
    out = []
    for nor, t in textes.items():
        titre = t.get("TITREFULL") or t.get("TITRE_TXT", "")
        if t.get("DATE_PUBLI", "") < DEBUT.date().isoformat():
            continue
        if not RE_JORF_SUJET.search(titre) or RE_JORF_ECARTE.search(titre):
            continue
        out.append({"id": "JORF-" + nor, "origine": "JORF", "nature": t.get("NATURE", ""), "titre": titre,
                    "date_publication": t.get("DATE_PUBLI", ""), "date_signature": t.get("DATE_TEXTE", ""),
                    "ministere": t.get("MINISTERE", ""), "nor": nor,
                    "lien": ("https://www.legifrance.gouv.fr/jorf/id/%s" % t["ID"]) if t.get("ID")
                    else "https://www.legifrance.gouv.fr/search/all?query=%s" % nor})
    out.sort(key=lambda e: e["date_publication"], reverse=True)
    return out, len(textes)


# ------------------------------------------------------------------ II bis / III bis. Union européenne (Cellar)
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
PREF = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n")
FRA = "<http://publications.europa.eu/resource/authority/language/FRA>"


def sparql(q):
    url = SPARQL + "?" + urllib.parse.urlencode({"query": PREF + q, "format": "application/sparql-results+json"})
    return [{k: v["value"] for k, v in b.items()}
            for b in json.loads(lire(url, headers={"Accept": "application/sparql-results+json"}))["results"]["bindings"]]


def eurlex(celex):
    return "https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:%s" % urllib.parse.quote(celex)


def textes_union():
    d = (MAINTENANT - timedelta(days=45)).date().isoformat()
    res = sparql("""
SELECT DISTINCT ?celex ?date ?titre WHERE {
  ?w cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date .
  FILTER(?date >= "%s"^^xsd:date) FILTER(REGEX(STR(?celex), "^3[0-9]{4}[RL]"))
  ?e cdm:expression_belongs_to_work ?w ; cdm:expression_title ?titre ; cdm:expression_uses_language %s .
  FILTER(REGEX(?titre, "défense|armement|munitions|2009/81|industrie de défense|stocks pétroliers|produits pétroliers", "i"))
  FILTER(!REGEX(?titre, "PESC"))
} ORDER BY DESC(?date) LIMIT 100""" % (d, FRA))
    out = []
    for r in res:
        nature = "Directive UE" if r["celex"][5:6] == "L" else "Règlement UE"
        out.append({"id": "UE-" + r["celex"], "origine": "JOUE", "nature": nature, "titre": r["titre"],
                    "date_signature": r["date"][:10], "celex": r["celex"], "lien": eurlex(r["celex"])})
    return out


TYPES_UE = {"CJ": "Arrêt (Cour)", "CO": "Ordonnance (Cour)", "CC": "Conclusions de l'avocat général",
            "TJ": "Arrêt (Tribunal)", "TO": "Ordonnance (Tribunal)"}
CITES_UE = {"32009L0081": "directive 2009/81/CE", "12008E346": "article 346 TFUE", "12012E346": "article 346 TFUE",
            "12016E346": "article 346 TFUE", "32014L0023": "directive 2014/23/UE", "32014L0024": "directive 2014/24/UE"}


def numero_affaire(celex):
    m = re.match(r"6(\d{4})([CT])[A-Z]{1,2}(\d{4})", celex)
    return "%s-%d/%s" % (m.group(2), int(m.group(3)), m.group(1)[2:]) if m else ""


def jurisprudence_union():
    d = (MAINTENANT - timedelta(days=60)).date().isoformat()
    valeurs = " ".join('"%s"' % c for c in CITES_UE)
    res = sparql("""
SELECT DISTINCT ?celex ?date ?cite ?titre WHERE {
  VALUES ?cite { %s }
  ?d cdm:resource_legal_id_celex ?c . FILTER(STR(?c) = ?cite)
  ?w cdm:work_cites_work ?d ; cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date .
  FILTER(REGEX(STR(?celex), "^6[0-9]{4}(CJ|CO|CC|TJ|TO)[0-9]{4}$")) FILTER(?date >= "%s"^^xsd:date)
  OPTIONAL { ?e cdm:expression_belongs_to_work ?w ; cdm:expression_title ?titre ; cdm:expression_uses_language %s . }
} ORDER BY DESC(?date) LIMIT 200""" % (valeurs, d, FRA))
    par = {}
    for r in res:
        e = par.setdefault(r["celex"], {
            "id": "UE-" + r["celex"], "juridiction": "Cour de justice" if r["celex"][5] == "C" else "Tribunal",
            "type": TYPES_UE.get(r["celex"][5:7], r["celex"][5:7]), "date_decision": r["date"][:10],
            "affaire": numero_affaire(r["celex"]), "intitule": r.get("titre", ""), "celex": r["celex"],
            "textes_cites": [], "lien": eurlex(r["celex"])})
        lib = CITES_UE.get(r["cite"], r["cite"])
        if lib not in e["textes_cites"]:
            e["textes_cites"].append(lib)
    return sorted(par.values(), key=lambda e: e["date_decision"], reverse=True)


# ------------------------------------------------------------------ III. Juridictions administratives
ART_CONCESSIONS = {"L1120-1", "L1122-1", "L1323-1", "L1330-1", "L3114-3", "L3123-12", "L3123-13", "L3123-14", "L3123-21",
                   "L3124-6", "L3133-4", "L3212-3", "L3215-1", "R3123-3", "R3123-6", "R3123-7", "R3123-8"}
ART_CONCESSIONS_SI_DEFENSE = {"R3122-11", "R3221-1", "R3221-2", "R3221-3", "R3221-4", "R3221-5"}
RE_ARTICLE_CCP = re.compile(r"\b([LRD])\.?\s?(\d{4}-\d+)((?:(?!code\b)[^;]){0,200}?)code de la commande publique", re.I)
RE_CCP_NOTAMMENT = re.compile(r"code de la commande publique,?\s+notamment\s+(?:ses|son|l.)\s*articles?((?:(?!code\b)[^;]){0,250})", re.I)
RE_EXPRESSIONS = re.compile(r"march[ée]s? (?:publics? )?de d[ée]fense(?: ou de s[ée]curit[ée])?|concessions? de d[ée]fense"
                            r"|march[ée]s? classifi[ée]s?|directive 2009/81|article 346 du trait[ée]", re.I)
RE_DEFENSE = re.compile(r"d[ée]fense|s[ée]curit[ée] nationale|arm[ée]es|militaire|armement", re.I)
# Plus strict, pour les décisions : « mémoire en défense » ou « droits de la défense » ne doivent pas suffire.
RE_DEFENSE_STRICTE = re.compile(r"concessions? de d[ée]fense|d[ée]fense nationale|minist(?:re|ère) des arm[ée]es|ministre des arm[ée]es|"
                                r"militaire|armement|s[ée]curit[ée] nationale|direction g[ée]n[ée]rale de l.armement|march[ée]s? de d[ée]fense", re.I)
RE_2014_23 = re.compile(r"directive (?:n[°o]\s?)?2014/23", re.I)


def criteres_ja(texte):
    """Renvoie la liste des critères de la veille présents dans une décision (vide = décision écartée)."""
    trouves = []
    defense = bool(RE_DEFENSE_STRICTE.search(texte))
    for m in RE_ARTICLE_CCP.finditer(texte):
        art = m.group(1).upper() + m.group(2)
        # « articles L. 2331-2 et L. 2331-3 du code… » : on reprend aussi les autres numéros de l'énumération
        numeros = [art] + [x[0].upper() + x[1] for x in re.findall(r"\b([LRD])\.?\s?(\d{4}-\d+)", m.group(3))]
        for a in numeros:
            if re.match(r"[LRD]23\d\d-", a):
                trouves.append(a + " CCP")
            elif (a in ART_CONCESSIONS or a in ART_CONCESSIONS_SI_DEFENSE) and defense:
                trouves.append(a + " CCP")
    for m in RE_CCP_NOTAMMENT.finditer(texte):
        for x in re.findall(r"\b([LRD])\.?\s?(\d{4}-\d+)", m.group(1)):
            a = x[0].upper() + x[1]
            if re.match(r"[LRD]23\d\d-", a) or (a in ART_CONCESSIONS and defense):
                trouves.append(a + " CCP")
    trouves += [m.group(0).lower() for m in RE_EXPRESSIONS.finditer(texte)]
    if RE_2014_23.search(texte) and defense:
        trouves.append("directive 2014/23/UE (défense)")
    return sorted(set(trouves))


def extrait_autour(texte, motif, n=700):
    m = re.search(motif, texte, re.I)
    if not m:
        return ""
    return texte[max(0, m.start() - n // 2): m.start() + n // 2]


def mois_precedents(k):
    a, m = MAINTENANT.year, MAINTENANT.month
    out = []
    for _ in range(k):
        out.append((a, m))
        m -= 1
        if m == 0:
            a, m = a - 1, 12
    return out


def jurisprudence_administrative(deja):
    out, lus = [], 0
    for code, dossier, prefixe in (("CE", "DCE", "CE"), ("CAA", "DCA", "CAA"), ("TA", "DTA", "TA")):
        for a, m in mois_precedents(3):
            url = "https://opendata.justice-administrative.fr/%s/%d/%02d/%s_%d%02d.zip" % (dossier, a, m, prefixe, a, m)
            try:
                with tempfile.TemporaryFile() as tmp:
                    telecharger(url, tmp)
                    with zipfile.ZipFile(tmp) as z:
                        for nom in z.namelist():
                            if not nom.endswith(".xml"):
                                continue
                            x = z.read(nom).decode("utf-8", "replace")
                            lus += 1
                            mise_en_ligne = iso(champ(x, "Date_Mise_Jour"))
                            numero = champ(x, "Numero_Dossier")
                            cle = "JA-%s-%s-%s" % (code, numero, champ(x, "Date_Lecture"))
                            if mise_en_ligne < DEBUT.date().isoformat() or cle in deja:
                                continue
                            texte = champ(x, "Texte_Integral")
                            criteres = criteres_ja(texte)
                            if not criteres:
                                continue
                            dispositif = re.split(r"D\s?[EÉ]\s?C\s?I\s?D\s?E\s?:?", texte, maxsplit=1)
                            date_lecture = iso(champ(x, "Date_Lecture"))
                            out.append({
                                "id": cle, "juridiction": champ(x, "Nom_Juridiction"), "niveau": code,
                                "numero": numero, "date_decision": date_lecture, "date_mise_en_ligne": mise_en_ligne,
                                "type": champ(x, "Type_Decision"), "formation": champ(x, "Formation_Jugement"),
                                "publication_recueil": champ(x, "Code_Publication"), "ecli": champ(x, "Numero_ECLI"),
                                "criteres": criteres, "debut": texte[:1500],
                                "passage": extrait_autour(texte, r"code de la commande publique|d[ée]fense ou de s[ée]curit[ée]|concession de d[ée]fense"),
                                "dispositif": dispositif[1][:1200] if len(dispositif) > 1 else texte[-1200:],
                                "lien": ("https://www.conseil-etat.fr/fr/arianeweb/CE/decision/%s/%s" % (date_lecture, numero))
                                if code == "CE" else "https://opendata.justice-administrative.fr/",
                            })
            except Exception as e:
                if "404" not in str(e):
                    raise
    out.sort(key=lambda e: e["date_mise_en_ligne"], reverse=True)
    return out, lus


# ------------------------------------------------------------------ III ter. Conseil constitutionnel
RE_CC = re.compile(r"commande publique|march[ée]s? publics?|programmation militaire", re.I)


def conseil_constitutionnel():
    out = []
    for i in items_flux(lire("https://www.conseil-constitutionnel.fr/flux/rss.xml")):
        if not i["date"] or i["date"] < DEBUT or not re.match(r"D[ée]cision n[°o]", i["titre"]) or "communiqu" in i["titre"].lower():
            continue
        try:
            page = texte_brut(lire(i["lien"]).decode("utf-8", "replace"))
        except Exception:
            page = ""
        if RE_CC.search(page) and RE_DEFENSE.search(page):
            out.append({"id": "CC-" + ident(i["lien"]), "juridiction": "Conseil constitutionnel", "type": "Décision",
                        "intitule": i["titre"], "date_mise_en_ligne": jour(i["date"]), "lien": i["lien"],
                        "passage": extrait_autour(page, RE_CC.pattern)})
        time.sleep(1)
    return out


# ------------------------------------------------------------------ principal
def main():
    os.makedirs(os.path.join(DOSSIER, "archives"), exist_ok=True)
    f_etat = os.path.join(DOSSIER, "etat-veille.json")
    etat = json.load(open(f_etat, encoding="utf-8")) if os.path.exists(f_etat) else {"transmis": {}}
    deja = etat["transmis"]

    resultat = {"genere_le": MAINTENANT.isoformat(timespec="seconds"),
                "periode": {"du": DEBUT.date().isoformat(), "au": MAINTENANT.date().isoformat()},
                "sources": {}, "actualites": [], "textes": [], "jurisprudence": []}

    def etape(nom, rubrique, fonction):
        try:
            r = fonction()
            details = {}
            if isinstance(r, tuple):
                r, details = r[0], {"documents_lus": r[1]}
            nouveaux = [e for e in r if e["id"] not in deja]
            resultat[rubrique] += nouveaux
            resultat["sources"][nom] = {"statut": "ok", "retenus": len(nouveaux), "deja_transmis": len(r) - len(nouveaux), **details}
            print("%-34s ok    %d retenus" % (nom, len(nouveaux)))
        except Exception as e:
            resultat["sources"][nom] = {"statut": "indisponible", "erreur": str(e)[:300]}
            print("%-34s ÉCHEC %s" % (nom, e), file=sys.stderr)

    etape("Actualités (Google Actualités)", "actualites", actualites)
    etape("Journal officiel (DILA)", "textes", textes_jorf)
    etape("Journal officiel de l'Union", "textes", textes_union)
    etape("Justice administrative", "jurisprudence", lambda: jurisprudence_administrative(deja))
    etape("Conseil constitutionnel", "jurisprudence", conseil_constitutionnel)
    etape("Cour de justice et Tribunal de l'UE", "jurisprudence", jurisprudence_union)

    if all(s["statut"] != "ok" for s in resultat["sources"].values()):
        sys.exit(1)
    texte = json.dumps(resultat, ensure_ascii=False, indent=1)
    open(os.path.join(DOSSIER, "veille-semaine.json"), "w", encoding="utf-8").write(texte)
    open(os.path.join(DOSSIER, "archives", "veille-%s.json" % MAINTENANT.date().isoformat()), "w", encoding="utf-8").write(texte)

    if os.environ.get("ESSAI") == "1":
        print("Lancement d'essai : la mémoire des éléments transmis n'est pas mise à jour.")
        return
    # Mémoire : on note ce qui vient d'être transmis et on oublie ce qui date de plus de 6 mois.
    for rub in ("actualites", "textes", "jurisprudence"):
        for e in resultat[rub]:
            deja[e["id"]] = MAINTENANT.date().isoformat()
    limite = (MAINTENANT - timedelta(days=183)).date().isoformat()
    etat["transmis"] = {k: v for k, v in deja.items() if v >= limite}
    json.dump(etat, open(f_etat, "w", encoding="utf-8"), ensure_ascii=False, indent=0)



if __name__ == "__main__":
    main()
