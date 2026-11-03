# -*- coding: utf-8 -*-
"""
Outil de la veille hebdomadaire de la commande publique de défense — GMR DEFCOM.
Utilisé par la tâche planifiée Claude de chaque professeur.

  python3 veille.py avis
      Télécharge la collecte des MARCHÉS de la semaine (BOAMP + TED) depuis GitHub et écrit :
        - avis.txt        : liste compacte et décodée des avis à examiner ;
        - avis_index.json : détails de chaque avis (lien, pays, date, source), par référence.

  python3 veille.py veille
      Télécharge la collecte des ACTUALITÉS, TEXTES et JURISPRUDENCE de la semaine et écrit :
        - veille.txt        : liste compacte des éléments à examiner, par rubrique ;
        - veille_index.json : détails de chaque élément (lien, date, source…), par référence.

  python3 veille.py mail articles.json --cle CLE [--site https://chairedefcom.fr] [--jours 45]
                    [--semaine "Semaine du …"] [--pied "…"]
      Fabrique le mail à partir des fiches rédigées par Claude, en trois rubriques
      (Actualités, Marchés, Jurisprudence), avec un bouton « Publier » signé par fiche.
      Écrit mail.html et mail.txt ; si le mail est long, il est découpé en plusieurs envois
      (mail_1.html et mail_1.txt, mail_2.html…), à envoyer dans l'ordre.
      articles.json = liste de {
          "ref": référence lue dans avis.txt ou veille.txt (ex. "TED-700480-2026", "ACT-1a2b3c4d5e6f", "UE-62024CJ0583"),
          "rubrique": "actualites" | "marches" | "jurisprudence",
          "etiquette": "Appel d'offres" | "Attribution" | "Décret" | "Directive UE" | "Arrêt" | "Conclusions" | …,
          "titre": "...", "texte": "...",
          "theme": thème d'image, pour les actualités (voir THEMES),
          "affaire": numéro d'affaire, pour la jurisprudence de l'Union (ex. "C-583/24") }
"""
import base64, glob, hashlib, hmac, html, json, os, re, sys, urllib.request, zlib
from datetime import date, timedelta

SOURCE = "https://raw.githubusercontent.com/gmr-defcom/veille-marches-defcom/main/donnees/derniere-semaine.json"
SOURCE_VEILLE = "https://raw.githubusercontent.com/gmr-defcom/veille-marches-defcom/main/donnees/veille-semaine.json"
THEMES = ["naval", "aerien", "terrestre", "drones", "spatial", "cyber", "munitions", "industrie", "pme",
          "europe", "dga-essais", "exportation", "budget", "institutions", "juridique"]

PAYS = {"FRA": "France", "DEU": "Allemagne", "BEL": "Belgique", "NLD": "Pays-Bas", "LUX": "Luxembourg",
        "ITA": "Italie", "ESP": "Espagne", "PRT": "Portugal", "IRL": "Irlande", "DNK": "Danemark",
        "SWE": "Suède", "FIN": "Finlande", "EST": "Estonie", "LVA": "Lettonie", "LTU": "Lituanie",
        "POL": "Pologne", "CZE": "Tchéquie", "SVK": "Slovaquie", "HUN": "Hongrie", "AUT": "Autriche",
        "SVN": "Slovénie", "HRV": "Croatie", "ROU": "Roumanie", "BGR": "Bulgarie", "GRC": "Grèce",
        "CYP": "Chypre", "MLT": "Malte", "NOR": "Norvège", "CHE": "Suisse", "ISL": "Islande",
        "UKR": "Ukraine", "MDA": "Moldavie", "GBR": "Royaume-Uni"}
TYPE = {"cn-standard": "appel d'offres", "cn-social": "appel d'offres", "can-standard": "attribution",
        "can-social": "attribution", "can-modif": "modification de marché", "veat": "avis d'intention de conclure (VEAT)",
        "pin-only": "préinformation", "pin-buyer": "préinformation", "pin-rtl": "préinformation",
        "pmc": "consultation préalable du marché", "cn-desg": "concours", "can-desg": "résultat de concours"}
BASE = {"32009L0081": "directive 2009/81/CE", "32014L0024": "directive 2014/24/UE",
        "32014L0025": "directive 2014/25/UE", "32024R2509": "règlement financier UE", "other": "autre base"}
PROC = {"open": "procédure ouverte", "restricted": "procédure restreinte",
        "neg-w-call": "négociée avec mise en concurrence préalable",
        "neg-wo-call": "négociée sans mise en concurrence", "comp-dial": "dialogue compétitif",
        "innovation": "partenariat d'innovation", "oth-single": "autre procédure (une étape)",
        "oth-mult": "autre procédure (plusieurs étapes)", "comp-tend": "procédure concurrentielle avec négociation"}

# Bruit fréquent du BOAMP : acheteurs non militaires et services courants d'intendance
BOAMP_HORS = re.compile(r"police municipale|commune|mairie|ville d|ville de|dgac|france travail|dga tech", re.I)
OBJET_HORS = re.compile(r"nettoyage|vitrerie|restauration collective|denrées|fromage|pain|viennoiserie|boisson|"
                        r"déneigement|reprographie|blanchisserie|espaces verts|gardiennage|transport de personnel|"
                        r"rénovation thermique|réhabilitation énergétique|isolation|toiture|chauffage", re.I)


def premier(v):
    return (v[0] if isinstance(v, list) and v else v) or ""


def titre_ted(n):
    t = n.get("notice-title") or {}
    return t.get("fra") or t.get("eng") or next(iter(t.values()), "")


def acheteur_ted(n):
    b = n.get("buyer-name") or {}
    return premier(b.get("fra") or b.get("eng") or next(iter(b.values()), [""]))


MAX_TED = 35        # avis TED soumis à Claude (les mieux classés)
MAX_ACTUALITES = 30 # actualités soumises à Claude (les mieux classées)
RE_ACT_UTILE = re.compile(r"contrat|command|attribu|march[ée]|appel d.offres|DGA|armement|programme|livr|acqui|achat|"
                          r"industri|BITD|PME|SAFE|EDIP|fonds europ|financ|export|usine|production|munition|"
                          r"procurement|contract|tender|industry|award", re.I)


def priorite_actualite(a):
    officiel = 0 if a.get("volet") not in (None, "", "presse") and "presse" not in str(a.get("volet")).lower() else 1
    return (-len(RE_ACT_UTILE.findall((a.get("titre") or "") + " " + (a.get("chapeau") or ""))), officiel)


def priorite_ted(n):
    cpv = n.get("classification-cpv") or []
    militaire = any(str(c)[:3] in ("353", "354", "355", "356", "357") for c in cpv)
    return (0 if premier(n.get("buyer-country")) == "FRA" else 1, 0 if militaire else 1)


def charger(url):
    """Télécharge le fichier de la collecte ; à défaut, lit la copie locale (dépôt cloné avec git)."""
    try:
        return json.loads(urllib.request.urlopen(url + "?t=" + str(date.today()), timeout=60).read().decode("utf-8"))
    except Exception as e:
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donnees", url.rsplit("/", 1)[1])
        if os.path.exists(local):
            print("Téléchargement impossible (%s) : copie locale utilisée (%s)." % (e, local))
            return json.loads(open(local, encoding="utf-8").read())
        raise


def cmd_avis():
    d = charger(SOURCE)
    index, lignes = {}, []
    p = d.get("periode", {})
    lignes.append("COLLECTE DU %s (période du %s au %s)" % (d.get("genere_le", "?")[:10], p.get("du"), p.get("au")))
    for nom, s in d.get("sources", {}).items():
        lignes.append("- %s : %s, %d avis" % (nom, s.get("statut"), len(s.get("avis", []))))

    # BOAMP
    b = d.get("sources", {}).get("BOAMP", {}).get("avis", [])
    ecartes = 0
    lignes.append("\n=== BOAMP (France) ===")
    for a in b:
        if BOAMP_HORS.search(a.get("nomacheteur") or "") or OBJET_HORS.search(a.get("objet") or ""):
            ecartes += 1
            continue
        ref = "BOAMP-" + (a.get("idweb") or "")
        index[ref] = {"source": "BOAMP", "url": a.get("url_avis") or "https://www.boamp.fr/",
                      "pays": "France", "date": (a.get("dateparution") or "")[:10]}
        lignes.append("[%s] %s | %s | %s | %s | %s | %s%s" % (
            ref, a.get("dateparution"), a.get("nomacheteur"), a.get("objet"), a.get("nature_libelle"),
            a.get("procedure_libelle") or a.get("type_procedure") or "?",
            ", ".join(a.get("descripteur_libelle") or []),
            (" | titulaire : %s" % a["titulaire"]) if a.get("titulaire") else ""))
    lignes.append("(%d avis BOAMP écartés automatiquement : acheteurs non militaires ou services courants)" % ecartes)

    # TED
    t = sorted(d.get("sources", {}).get("TED", {}).get("avis", []), key=priorite_ted)
    t_omis = max(0, len(t) - MAX_TED)
    t = t[:MAX_TED]   # présélection : seuls les mieux classés sont soumis à Claude
    lignes.append("\n=== TED (Union européenne) — classés : acheteurs français, puis matériel militaire (CPV 353-357) ===")
    stats = {}
    for n in t:
        num = n.get("publication-number")
        ref = "TED-%s" % num
        pays = PAYS.get(premier(n.get("buyer-country")), premier(n.get("buyer-country")))
        index[ref] = {"source": "TED", "url": "https://ted.europa.eu/fr/notice/-/detail/%s" % num,
                      "pays": pays, "date": (n.get("publication-date") or "")[:10]}
        base = BASE.get(premier(n.get("legal-basis")), premier(n.get("legal-basis")) or "?")
        stats[base] = stats.get(base, 0) + 1
        fa = any(x not in ("none", None) for x in (n.get("framework-agreement-lot") or []))
        val = ("%s %s" % (n.get("result-value-notice"), n.get("result-value-cur-notice") or "")) if n.get("result-value-notice") else ""
        cpv = ",".join(sorted(set(str(c) for c in (n.get("classification-cpv") or []))))
        lignes.append("[%s] %s | %s | %s | %s | %s | %s | %s%s%s%s | CPV %s" % (
            ref, (n.get("publication-date") or "")[:10], pays, acheteur_ted(n), titre_ted(n),
            TYPE.get(n.get("notice-type"), n.get("notice-type")), base,
            PROC.get(n.get("BT-105-Procedure"), n.get("BT-105-Procedure") or "procédure non renseignée"),
            " | accord-cadre" if fa else "", " | PROCÉDURE ACCÉLÉRÉE" if str(n.get("BT-106-Procedure")) == "true" else "",
            (" | montant %s" % val) if val else "", cpv))
    lignes.append("(répartition TED par base juridique : %s ; %d autres avis TED, moins prioritaires, non transmis)"
                  % (", ".join("%s %d" % kv for kv in stats.items()), t_omis))
    open("avis.txt", "w", encoding="utf-8").write("\n".join(lignes))
    json.dump(index, open("avis_index.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("avis.txt écrit : %d avis à examiner (%d BOAMP écartés automatiquement)." % (len(index), ecartes))


# ------------------------------------------------------------------ actualités, textes, jurisprudence
def court(t, n):
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def cmd_veille():
    d = charger(SOURCE_VEILLE)
    index, L = {}, []
    p = d.get("periode", {})
    L.append("COLLECTE DU %s (période du %s au %s)" % (d.get("genere_le", "?")[:10], p.get("du"), p.get("au")))
    for nom, s in d.get("sources", {}).items():
        L.append("- %s : %s%s" % (nom, s.get("statut"), (", %s retenus" % s.get("retenus")) if s.get("statut") == "ok"
                                  else " — " + s.get("erreur", "")))

    acts = sorted(d.get("actualites", []), key=priorite_actualite)
    omis = max(0, len(acts) - MAX_ACTUALITES)
    L.append("\n=== ACTUALITÉS (presse et sites officiels ; présélection des %d mieux classées, %d autres non transmises) ==="
             % (min(len(acts), MAX_ACTUALITES), omis))
    for a in acts[:MAX_ACTUALITES]:
        index[a["id"]] = {"source": a.get("source", ""), "url": a.get("lien", ""), "pays": "", "date": a.get("date", ""),
                          "rubrique": "actualites"}
        chapeau = a.get("chapeau") or ""
        if chapeau.lower().startswith((a.get("titre") or "").lower()[:40]):   # Google Actualités répète souvent le titre
            chapeau = ""
        L.append("[%s] %s | %s | %s | %s%s" % (a["id"], a.get("date"), a.get("volet"), a.get("source"), a.get("titre"),
                                               (" | chapeau : " + court(chapeau, 220)) if chapeau else ""))

    L.append("\n=== TEXTES PUBLIÉS AU JOURNAL OFFICIEL (rubrique Actualités) ===")
    for t in d.get("textes", []):
        index[t["id"]] = {"source": "JORF" if t.get("origine") == "JORF" else "JOUE", "url": t.get("lien", ""),
                          "pays": "France" if t.get("origine") == "JORF" else "Union européenne",
                          "date": t.get("date_publication") or t.get("date_signature", ""), "rubrique": "actualites"}
        L.append("[%s] %s | %s | %s | %s%s" % (
            t["id"], t.get("origine"), t.get("nature"),
            ("publié le %s" % t["date_publication"]) if t.get("date_publication") else ("adopté le %s" % t.get("date_signature")),
            t.get("titre"), (" | " + t["ministere"]) if t.get("ministere") else ""))

    L.append("\n=== JURISPRUDENCE ===")
    for j in d.get("jurisprudence", []):
        jid = j["id"]
        if jid.startswith("UE-"):
            index[jid] = {"source": "CURIA / EUR-Lex", "url": j.get("lien", ""), "pays": "Union européenne",
                          "date": j.get("date_decision", ""), "rubrique": "jurisprudence", "affaire": j.get("affaire", "")}
            L.append("[%s] %s | %s | affaire %s | %s | cite : %s | %s" % (
                jid, j.get("juridiction"), j.get("type"), j.get("affaire"), j.get("date_decision"),
                ", ".join(j.get("textes_cites", [])), court(j.get("intitule"), 300)))
        elif jid.startswith("CC-"):
            index[jid] = {"source": "Conseil constitutionnel", "url": j.get("lien", ""), "pays": "France",
                          "date": j.get("date_mise_en_ligne", ""), "rubrique": "jurisprudence"}
            L.append("[%s] Conseil constitutionnel | %s | mis en ligne le %s | extrait : %s" % (
                jid, j.get("intitule"), j.get("date_mise_en_ligne"), court(j.get("passage"), 600)))
        else:
            index[jid] = {"source": "Justice administrative", "url": j.get("lien", ""), "pays": "France",
                          "date": j.get("date_decision", ""), "rubrique": "jurisprudence"}
            L.append("[%s] %s | n° %s | décision du %s, mise en ligne le %s | %s %s | %s%s | critères : %s\n"
                     "    DÉBUT : %s\n    PASSAGE : %s\n    DISPOSITIF : %s" % (
                jid, j.get("juridiction"), j.get("numero"), j.get("date_decision"), j.get("date_mise_en_ligne"),
                j.get("type"), j.get("formation"), ("ECLI " + j["ecli"]) if j.get("ecli") else "sans ECLI",
                (" | publication " + j["publication_recueil"]) if j.get("publication_recueil") else "",
                ", ".join(j.get("criteres", [])), court(j.get("debut"), 900), court(j.get("passage"), 700),
                court(j.get("dispositif"), 700)))
    if not d.get("jurisprudence"):
        L.append("(aucune décision correspondant aux critères n'a été mise en ligne cette semaine)")
    open("veille.txt", "w", encoding="utf-8").write("\n".join(L))
    json.dump(index, open("veille_index.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("veille.txt écrit : %d actualités, %d textes, %d décisions." % (
        len(d.get("actualites", [])), len(d.get("textes", [])), len(d.get("jurisprudence", []))))


# ------------------------------------------------------------------ mail
RUBRIQUES = [("actualites", "Actualités"), ("marches", "Marchés"), ("jurisprudence", "Jurisprudence")]
MENTION_CONCLUSIONS = ("Conclusions de l'avocat général : il s'agit d'un avis indépendant rendu à la Cour, qui éclaire "
                       "l'affaire mais ne la lie pas et ne préjuge pas de la décision à venir.")


def lien_signe(article, cle, site):
    """Lien « Publier » : contenu compressé (deflate) puis encodé, et signé par la clé (HMAC-SHA256)."""
    brut = json.dumps(article, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    z = base64.urlsafe_b64encode(c.compress(brut) + c.flush()).decode().rstrip("=")
    s = hmac.new(cle.encode(), z.encode(), hashlib.sha256).hexdigest()
    return "%s/veille/publication/publier.php?z=%s&s=%s" % (site.rstrip("/"), z, s)


def jjmm(iso):
    try:
        y, m, j = iso.split("-")
        return "%s/%s" % (j, m)
    except Exception:
        return iso


def bouton(href):
    return ('<table cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 4px 0;"><tr>'
            '<td bgcolor="#1e7a46" style="background-color:#1e7a46;border:2px solid #16603a;border-radius:6px;">'
            '<a href="%s" style="display:block;padding:11px 22px;font:bold 15px Arial,sans-serif;color:#ffffff;'
            'text-decoration:none;background-color:#1e7a46;"><span style="color:#ffffff;">&#10003;&nbsp; Publier</span>'
            '</a></td></tr></table>') % html.escape(href)


def lire_index():
    index = {}
    for f in ("avis_index.json", "veille_index.json"):
        try:
            for k, v in json.load(open(f, encoding="utf-8")).items():
                v.setdefault("rubrique", "marches")
                index[k] = v
        except FileNotFoundError:
            pass
    return index


LIMITE_ENVOI = 60000   # octets de fiches par mail : au-delà, le mail est découpé en plusieurs envois


def cmd_mail(args):
    fichier = args[0]
    opts = dict(zip(args[1::2], args[2::2]))
    cle, site = opts.get("--cle", ""), opts.get("--site", "https://chairedefcom.fr")
    jours = int(opts.get("--jours", "45"))
    actif = bool(cle) and not cle.startswith("<")
    articles = json.load(open(fichier, encoding="utf-8"))
    index = lire_index()
    exp = (date.today() + timedelta(days=jours)).isoformat()
    F, G = "font-family:Arial,Helvetica,sans-serif;", "font-family:Georgia,serif;"
    P = "margin:0 0 8px;font-size:14px;line-height:1.55;color:#1b2127;"
    for ancien in glob.glob("mail.html") + glob.glob("mail.txt") + glob.glob("mail_*.html") + glob.glob("mail_*.txt"):
        os.remove(ancien)   # jamais de fichier d'un essai précédent

    def titre_rubrique(libelle, n, suite=False):
        return ('<h2 style="%smargin:26px 0 2px;font-size:20px;color:#0F1A2E;border-bottom:2px solid #C5A55A;'
                'padding-bottom:4px;">%s%s <span style="font:normal 13px Arial,sans-serif;color:#5c6670;">(%d)</span></h2>'
                % (G, libelle, " (suite)" if suite else "", n))

    # 1. Une carte HTML (et un texte) par fiche, rangées par rubrique
    cartes, total = {}, {}
    for code, libelle in RUBRIQUES:
        liste = [a for a in articles if (a.get("rubrique") or index.get(a["ref"], {}).get("rubrique", "marches")) == code]
        total[code] = len(liste)
        cartes[code] = []
        for a in liste:
            info = index.get(a["ref"], {})
            etiquette = a.get("etiquette") or ""
            corps = a["texte"]
            if code == "jurisprudence" and "conclusion" in etiquette.lower() and MENTION_CONCLUSIONS not in corps:
                corps += "\n\n" + MENTION_CONCLUSIONS
            source = info.get("source", "")
            if source.startswith("http"):
                source = re.sub(r"^https?://(www\.)?", "", source).split("/")[0]
            meta = " · ".join(x for x in (jjmm(info.get("date", "")), etiquette.upper(), info.get("pays", ""),
                                           "Source : " + source if source else "") if x)
            donnees = {"v": 2, "c": code, "t": a["titre"], "x": corps, "u": info.get("url", ""), "r": etiquette,
                       "p": info.get("pays", ""), "o": source,
                       "j": info.get("date") or date.today().isoformat(), "exp": exp}
            if code == "actualites":
                donnees["i"] = a.get("theme") if a.get("theme") in THEMES else "institutions"
            if a.get("affaire") or info.get("affaire"):
                donnees["a"] = a.get("affaire") or info.get("affaire")
            b = bouton(lien_signe(donnees, cle, site)) if actif else \
                '<p style="%smargin:6px 0 0;font-size:12px;color:#8B2635;">Bouton « Publier » inactif : clé de publication non renseignée.</p>' % F
            paras = "".join('<p style="%s">%s</p>' % (P, html.escape(x)) for x in corps.split("\n\n") if x.strip())
            carte = ('<table width="100%%" cellpadding="0" cellspacing="0" style="margin-top:14px;border:1px solid #d9ddda;border-radius:6px;">'
                     '<tr><td style="padding:16px 18px;%s">'
                     '<p style="margin:0 0 6px;font-size:12px;color:#5c6670;">%s%s</p>'
                     '<p style="%smargin:0 0 8px;font-size:17px;font-weight:bold;color:#1b2127;line-height:1.3;">%s</p>%s'
                     '<p style="margin:0 0 10px;font-size:13px;"><a href="%s" style="color:#1f5673;">Voir la source</a></p>%s'
                     '</td></tr></table>' % (F, html.escape(meta), (" · Image : " + donnees["i"]) if "i" in donnees else "",
                                              G, html.escape(a["titre"]), paras, html.escape(info.get("url", "")), b))
            cartes[code].append((carte, "%s\n%s\n%s\nSource : %s\n" % (meta, a["titre"], corps, info.get("url", ""))))

    # 2. Répartition en envois de LIMITE_ENVOI octets au plus (Gmail coupe les mails trop longs)
    envois, courant, taille = [], [], 0
    for code, libelle in RUBRIQUES:
        for carte, txt in cartes[code]:
            n = len(carte.encode("utf-8"))
            if courant and taille + n > LIMITE_ENVOI:
                envois.append(courant)
                courant, taille = [], 0
            courant.append((code, carte, txt))
            taille += n
    envois.append(courant)
    vides = [libelle for code, libelle in RUBRIQUES if total[code] == 0]

    pied = opts.get("--pied", "")
    noms = []
    for k, envoi in enumerate(envois, 1):
        blocs, texte, vus = [], [], set()
        for code, libelle in RUBRIQUES:
            ici = [(c, t) for cd, c, t in envoi if cd == code]
            if not ici:
                continue
            suite = any(cd == code for e in envois[:k - 1] for cd, c, t in e)
            blocs.append(titre_rubrique(libelle, total[code], suite))
            texte.append("== %s%s ==" % (libelle.upper(), " (suite)" if suite else ""))
            for c, t in ici:
                blocs.append(c)
                texte.append(t)
        if k == len(envois):
            for libelle in vides:
                blocs.append(titre_rubrique(libelle, 0))
                blocs.append('<p style="%s%scolor:#5c6670;margin-top:10px;">Rien à signaler cette semaine.</p>' % (F, P))
                texte.append("== %s ==\nRien à signaler cette semaine.\n" % libelle.upper())
        mention = (" · envoi %d/%d" % (k, len(envois))) if len(envois) > 1 else ""
        corps_mail = (
            '<div style="%sbackground:#f3f4f2;padding:16px 0;"><table width="100%%" cellpadding="0" cellspacing="0" '
            'style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid #d9ddda;border-radius:8px;"><tr><td style="padding:22px 24px;%s">'
            '<p style="margin:0;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#A68A3E;font-weight:bold;">GMR DEFCOM · Veille hebdomadaire%s</p>'
            '<h1 style="%smargin:4px 0 6px;font-size:24px;color:#0F1A2E;">Veille de la commande publique de défense</h1>'
            '<p style="margin:0 0 14px;color:#5c6670;font-size:14px;">%s · cette semaine : %d actualités · %d marchés · %d décisions</p>'
            '<table width="100%%" cellpadding="0" cellspacing="0"><tr><td bgcolor="#eef1f6" style="background-color:#eef1f6;border-radius:6px;padding:10px 14px;font-size:14px;line-height:1.6;color:#1B2A4A;%s">'
            'Cliquez sur <b style="color:#1e7a46;">✓ Publier</b> : la page de relecture du site s’ouvre ; vous pouvez y modifier le titre, '
            'le texte et l’image, puis cliquer sur « Publier sur le site ». Ne rien faire = rien n’est publié.'
            '</td></tr></table>%s'
            '<p style="margin:22px 0 0;font-size:12px;line-height:1.6;color:#5c6670;">%s'
            'Éclairages juridiques indicatifs, établis automatiquement : ils ne constituent pas un avis juridique.</p>'
            '</td></tr></table></div>') % (F, F, mention.upper(), G, html.escape(opts.get("--semaine", "Semaine")),
                                           total["actualites"], total["marches"], total["jurisprudence"], F, "".join(blocs),
                                           (html.escape(pied) + "<br>") if pied and k == len(envois) else "")
        base = "mail" if len(envois) == 1 else "mail_%d" % k
        open(base + ".html", "w", encoding="utf-8").write(corps_mail)
        open(base + ".txt", "w", encoding="utf-8").write("\n".join(texte))
        noms.append("%s.html (%d octets)" % (base, len(corps_mail.encode("utf-8"))))
    print("%d fiches, boutons %s." % (len(articles), "actifs" if actif else "INACTIFS"))
    if len(envois) == 1:
        print("Un seul envoi : mail.html et mail.txt.")
    else:
        print("%d ENVOIS à faire, dans l'ordre (un message par fichier, objet suffixé « (k/%d) ») : %s"
              % (len(envois), len(envois), ", ".join(noms)))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "avis":
        cmd_avis()
    elif len(sys.argv) >= 2 and sys.argv[1] == "veille":
        cmd_veille()
    elif len(sys.argv) >= 3 and sys.argv[1] == "mail":
        cmd_mail(sys.argv[2:])
    else:
        print(__doc__)
