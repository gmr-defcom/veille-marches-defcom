# -*- coding: utf-8 -*-
"""
Outil de la veille hebdomadaire des marchés de défense — GMR DEFCOM.
Utilisé par la tâche planifiée Claude de chaque professeur.

  python3 veille.py avis
      Télécharge la collecte de la semaine (BOAMP + TED) depuis GitHub et écrit :
        - avis.txt        : liste compacte et décodée des avis à examiner ;
        - avis_index.json : détails de chaque avis (lien, pays, date, source), par référence.

  python3 veille.py mail articles.json --cle CLE [--site https://chairedefcom.fr] [--jours 45]
      Fabrique le mail à partir des articles rédigés par Claude, avec un bouton
      « Publier » signé par article. Écrit mail.html (et mail.txt en secours).
      articles.json = liste de {"ref": "TED-700480-2026" ou "BOAMP-26-101062",
                                "etiquette": "Appel d'offres" | "Attribution" | "Préinformation" | ...,
                                "titre": "...", "texte": "..."}
"""
import base64, hashlib, hmac, html, json, re, sys, urllib.request
from datetime import date, timedelta

SOURCE = "https://raw.githubusercontent.com/gmr-defcom/veille-marches-defcom/main/donnees/derniere-semaine.json"

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


def priorite_ted(n):
    cpv = n.get("classification-cpv") or []
    militaire = any(str(c)[:3] in ("353", "354", "355", "356", "357") for c in cpv)
    return (0 if premier(n.get("buyer-country")) == "FRA" else 1, 0 if militaire else 1)


def cmd_avis():
    d = json.loads(urllib.request.urlopen(SOURCE + "?t=" + str(date.today()), timeout=60).read().decode("utf-8"))
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
    lignes.append("(répartition TED par base juridique : %s)" % ", ".join("%s %d" % kv for kv in stats.items()))
    open("avis.txt", "w", encoding="utf-8").write("\n".join(lignes))
    json.dump(index, open("avis_index.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("avis.txt écrit : %d avis à examiner (%d BOAMP écartés automatiquement)." % (len(index), ecartes))


# ------------------------------------------------------------------ mail
def lien_signe(article, cle, site):
    d = base64.urlsafe_b64encode(json.dumps(article, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    s = hmac.new(cle.encode(), d.encode(), hashlib.sha256).hexdigest()
    return "%s/veille/publication/publier.php?d=%s&s=%s" % (site.rstrip("/"), d, s)


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


def cmd_mail(args):
    fichier = args[0]
    opts = dict(zip(args[1::2], args[2::2]))
    cle, site = opts.get("--cle", ""), opts.get("--site", "https://chairedefcom.fr")
    jours = int(opts.get("--jours", "45"))
    actif = bool(cle) and not cle.startswith("<")
    articles = json.load(open(fichier, encoding="utf-8"))
    index = json.load(open("avis_index.json", encoding="utf-8"))
    exp = (date.today() + timedelta(days=jours)).isoformat()
    F, G = "font-family:Arial,Helvetica,sans-serif;", "font-family:Georgia,serif;"
    cartes, texte = [], []
    for a in articles:
        info = index.get(a["ref"], {})
        meta = " · ".join(x for x in (jjmm(info.get("date", "")), (a.get("etiquette") or "").upper(),
                                       info.get("pays", ""), "Source : " + info.get("source", "")) if x)
        donnees = {"v": 1, "t": a["titre"], "x": a["texte"], "u": info.get("url", ""),
                   "r": a.get("etiquette", ""), "p": info.get("pays", ""), "o": info.get("source", ""),
                   "j": info.get("date", date.today().isoformat()), "exp": exp}
        b = bouton(lien_signe(donnees, cle, site)) if actif else \
            '<p style="%smargin:6px 0 0;font-size:12px;color:#8B2635;">Bouton « Publier » inactif : clé de publication non renseignée.</p>' % F
        cartes.append(
            '<table width="100%%" cellpadding="0" cellspacing="0" style="margin-top:16px;border:1px solid #d9ddda;border-radius:6px;"><tr><td style="padding:16px 18px;%s">'
            '<p style="margin:0 0 6px;font-size:12px;color:#5c6670;">%s</p>'
            '<p style="%smargin:0 0 8px;font-size:17px;font-weight:bold;color:#1b2127;line-height:1.3;">%s</p>'
            '<p style="margin:0 0 8px;font-size:14px;line-height:1.55;color:#1b2127;">%s</p>'
            '<p style="margin:0 0 10px;font-size:13px;"><a href="%s" style="color:#1f5673;">Voir l’avis</a></p>%s'
            '</td></tr></table>' % (F, html.escape(meta), G, html.escape(a["titre"]),
                                    html.escape(a["texte"]).replace("\n\n", "</p><p style=\"margin:0 0 8px;font-size:14px;line-height:1.55;color:#1b2127;\">"),
                                    html.escape(info.get("url", "")), b))
        texte.append("%s\n%s\n%s\nSource : %s\n" % (meta, a["titre"], a["texte"], info.get("url", "")))
    pied = opts.get("--pied", "")
    corps = (
        '<div style="%sbackground:#f3f4f2;padding:16px 0;"><table width="100%%" cellpadding="0" cellspacing="0" '
        'style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid #d9ddda;border-radius:8px;"><tr><td style="padding:22px 24px;%s">'
        '<p style="margin:0;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#A68A3E;font-weight:bold;">GMR DEFCOM · Veille hebdomadaire</p>'
        '<h1 style="%smargin:4px 0 6px;font-size:24px;color:#0F1A2E;">Veille des marchés de défense</h1>'
        '<p style="margin:0 0 14px;color:#5c6670;font-size:14px;">%s · %d avis sélectionnés</p>'
        '<table width="100%%" cellpadding="0" cellspacing="0"><tr><td bgcolor="#eef1f6" style="background-color:#eef1f6;border-radius:6px;padding:10px 14px;font-size:14px;line-height:1.6;color:#1B2A4A;%s">'
        'Cliquez sur <b style="color:#1e7a46;">✓ Publier</b> : la page de relecture du site s’ouvre, où vous pouvez modifier le titre et le texte, puis cliquez sur « Publier sur le site ». Ne rien faire = rien n’est publié.'
        '</td></tr></table>%s'
        '<p style="margin:22px 0 0;font-size:12px;line-height:1.6;color:#5c6670;">%s'
        'Éclairages juridiques indicatifs, établis automatiquement : ils ne constituent pas un avis juridique.</p>'
        '</td></tr></table></div>') % (F, F, G, html.escape(opts.get("--semaine", "Semaine")), len(articles), F,
                                       "".join(cartes), (html.escape(pied) + "<br>") if pied else "")
    open("mail.html", "w", encoding="utf-8").write(corps)
    open("mail.txt", "w", encoding="utf-8").write("\n\n".join(texte))
    print("mail.html écrit : %d articles, %d caractères, boutons %s." % (len(articles), len(corps), "actifs" if actif else "INACTIFS"))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "avis":
        cmd_avis()
    elif len(sys.argv) >= 3 and sys.argv[1] == "mail":
        cmd_mail(sys.argv[2:])
    else:
        print(__doc__)
