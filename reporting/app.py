from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ingestion.config import Settings, get_settings
from ingestion.diagnostic import (
    ACTIVITES,
    Diagnostic,
    DiagnosticIndisponibleError,
    DonneesAbsentesError,
    avec_risques_locaux,
    completer_en_ligne,
    essentiel_en_dict,
    formater_diagnostic,
    formater_essentiel,
    lire_diagnostic,
)
from ingestion.geocode import AdresseAmbigueError, AdresseIntrouvableError, GeocodingError, geocode
from reporting import carte

logger = logging.getLogger(__name__)

# Boucle locale seulement : l'application ne s'expose jamais au réseau
HOTE = "127.0.0.1"
PORT = 8770
MARQUEUR_APP = "/*__APP__*/false"
COMMANDE_REFRESH = "python -m ingestion.cli refresh --months 180"
TYPES = {
    "pdf": "application/pdf",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


class ProjetInvalideError(ValueError):
    pass


@dataclass(frozen=True)
class Projet:
    adresse: str
    puissance_mw: float | None = None
    conso_mwh: float | None = None
    activite: str | None = None
    surface_m2: float | None = None
    ca_eur: float | None = None
    va_eur: float | None = None
    # Appels à la demande de `diagnose` (Géorisques, urbanisme, eau, solaire, desserte)
    complet: bool = False


def _nombre(corps: dict[str, Any], cle: str, libelle: str) -> float | None:
    valeur = corps.get(cle)
    if valeur in (None, ""):
        return None
    try:
        nombre = float(str(valeur).replace(",", ".").replace(" ", ""))
    except ValueError:
        raise ProjetInvalideError(f"{libelle} : nombre attendu, pas {valeur!r}") from None
    if nombre <= 0:
        raise ProjetInvalideError(f"{libelle} : valeur positive attendue")
    return nombre


def lire_projet(corps: dict[str, Any]) -> Projet:
    adresse = str(corps.get("adresse") or "").strip()
    if not adresse:
        raise ProjetInvalideError("adresse manquante")
    activite = corps.get("activite") or None
    if activite is not None and activite not in ACTIVITES:
        raise ProjetInvalideError(f"activité inconnue : {activite}")
    projet = Projet(
        adresse=adresse,
        puissance_mw=_nombre(corps, "puissance_mw", "puissance"),
        conso_mwh=_nombre(corps, "conso_mwh", "consommation"),
        activite=activite,
        surface_m2=_nombre(corps, "surface_m2", "surface"),
        ca_eur=_nombre(corps, "ca_eur", "chiffre d'affaires"),
        va_eur=_nombre(corps, "va_eur", "valeur ajoutée"),
        complet=bool(corps.get("complet")),
    )
    # Même règle que la ligne de commande : les parts se calculent sur la consommation
    if (projet.ca_eur or projet.va_eur) and projet.conso_mwh is None:
        raise ProjetInvalideError(
            "chiffre d'affaires et valeur ajoutée demandent la consommation annuelle"
        )
    return projet


# La commande équivalente, citée au pied du rapport pour le refaire sans l'application
def commande(projet: Projet, format: str) -> str:
    morceaux = ["python -m ingestion.cli diagnose", f'--adresse "{projet.adresse}"']
    for option, valeur in (
        ("--puissance", projet.puissance_mw),
        ("--conso", projet.conso_mwh),
        ("--activite", projet.activite),
        ("--surface", projet.surface_m2),
        ("--ca", projet.ca_eur),
        ("--va", projet.va_eur),
    ):
        if valeur is not None:
            texte = f"{valeur:g}" if isinstance(valeur, float) else valeur
            morceaux.append(f"{option} {texte}")
    if not projet.complet:
        morceaux.append("--sans-risques")
    morceaux.append(f"--format {format}")
    return " ".join(morceaux)


def diagnostiquer(projet: Projet, settings: Settings) -> Diagnostic:
    diagnostic = lire_diagnostic(
        geocode(projet.adresse, settings=settings),
        puissance_mw=projet.puissance_mw,
        conso_mwh=projet.conso_mwh,
        ca_eur=projet.ca_eur,
        va_eur=projet.va_eur,
        surface_m2=projet.surface_m2,
        activite=projet.activite,
        settings=settings,
    )
    if projet.complet:
        return completer_en_ligne(diagnostic, settings=settings)
    return avec_risques_locaux(diagnostic, settings=settings)


@dataclass(frozen=True)
class Reponse:
    statut: HTTPStatus
    type: str
    corps: bytes
    fichier: str | None = None


def _json(statut: HTTPStatus, valeur: Any) -> Reponse:
    corps = json.dumps(valeur, ensure_ascii=False, default=str).encode("utf-8")
    return Reponse(statut, "application/json; charset=utf-8", corps)


def _erreur(error: Exception) -> Reponse:
    match error:
        case ProjetInvalideError():
            return _json(HTTPStatus.BAD_REQUEST, {"erreur": str(error)})
        case AdresseAmbigueError():
            return _json(
                HTTPStatus.CONFLICT,
                {"erreur": "Adresse ambiguë : choisir la commune.", "candidats": error.candidats},
            )
        case AdresseIntrouvableError():
            return _json(
                HTTPStatus.NOT_FOUND,
                {"erreur": f"Adresse introuvable ({error}). Préciser la rue et la commune."},
            )
        case DonneesAbsentesError():
            return _json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"erreur": f"{error}. Charger les données : {COMMANDE_REFRESH}"},
            )
        case _:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"erreur": str(error)})


ECHECS_PREVUS = (ProjetInvalideError, GeocodingError, DiagnosticIndisponibleError, OSError)


# L'épingle de la dernière analyse reste : la carte n'est refaite que si ses données ont changé
def page(settings: Settings) -> Reponse:
    chemin = carte.chemin(settings, None)
    if not carte.carte_a_jour(chemin, settings):
        ancienne = chemin.read_text(encoding="utf-8") if chemin.exists() else ""
        carte.generer_carte(settings=settings, output=chemin)
        refaite = carte.reprendre_adresse(ancienne, chemin.read_text(encoding="utf-8"))
        chemin.write_text(refaite, encoding="utf-8")
    html = chemin.read_text(encoding="utf-8").replace(MARQUEUR_APP, "true", 1)
    return Reponse(HTTPStatus.OK, "text/html; charset=utf-8", html.encode("utf-8"))


# Analyse l'adresse et épingle son verdict sur la carte, que la page recharge ensuite
def analyser(corps: dict[str, Any], settings: Settings) -> Reponse:
    try:
        projet = lire_projet(corps)
        diagnostic = diagnostiquer(projet, settings)
        carte.mettre_a_jour_carte(
            settings=settings,
            territoire=diagnostic.territoire,
            verdict_adresse=carte.verdict_carte(diagnostic),
        )
    except ECHECS_PREVUS as error:
        return _erreur(error)
    texte = formater_diagnostic(diagnostic) if projet.complet else formater_essentiel(diagnostic)
    return _json(
        HTTPStatus.OK,
        {
            "adresse": diagnostic.territoire.adresse,
            "texte": texte,
            "essentiel": essentiel_en_dict(diagnostic),
            "commande": commande(projet, "pdf"),
        },
    )


def rapport(corps: dict[str, Any], settings: Settings) -> Reponse:
    format = corps.get("format", "pdf")
    if format not in TYPES:
        return _json(HTTPStatus.BAD_REQUEST, {"erreur": f"format inconnu : {format}"})
    # Import paresseux : matplotlib et reportlab ne servent qu'au rapport
    from reporting.implantation import generer_rapport_implantation

    try:
        projet = lire_projet(corps)
        diagnostic = diagnostiquer(projet, settings)
        # Markdown sans graphiques : un seul fichier à télécharger, sans images à côté
        chemin = generer_rapport_implantation(
            diagnostic,
            format=format,
            settings=settings,
            graphiques=format != "md",
            commande=commande(projet, format),
        )
    except ECHECS_PREVUS as error:
        return _erreur(error)
    return Reponse(HTTPStatus.OK, TYPES[format], chemin.read_bytes(), fichier=chemin.name)


def traiter(methode: str, chemin: str, corps: bytes, settings: Settings) -> Reponse:
    if methode == "GET" and chemin in ("/", "/index.html"):
        return page(settings)
    if methode == "POST" and chemin in ("/api/analyse", "/api/rapport"):
        try:
            donnees = json.loads(corps or b"{}")
        except json.JSONDecodeError:
            return _json(HTTPStatus.BAD_REQUEST, {"erreur": "corps JSON illisible"})
        if not isinstance(donnees, dict):
            return _json(HTTPStatus.BAD_REQUEST, {"erreur": "objet JSON attendu"})
        return (analyser if chemin == "/api/analyse" else rapport)(donnees, settings)
    return _json(HTTPStatus.NOT_FOUND, {"erreur": f"{methode} {chemin} inconnu"})


def creer_serveur(settings: Settings | None = None, *, port: int = PORT) -> ThreadingHTTPServer:
    settings = settings or get_settings()

    class Gestionnaire(BaseHTTPRequestHandler):
        def _repondre(self, methode: str) -> None:
            longueur = int(self.headers.get("Content-Length") or 0)
            corps = self.rfile.read(longueur) if longueur else b""
            reponse = traiter(methode, self.path.split("?", 1)[0], corps, settings)
            self.send_response(reponse.statut)
            self.send_header("Content-Type", reponse.type)
            self.send_header("Content-Length", str(len(reponse.corps)))
            self.send_header("Cache-Control", "no-store")
            if reponse.fichier:
                self.send_header("Content-Disposition", f'attachment; filename="{reponse.fichier}"')
            self.end_headers()
            self.wfile.write(reponse.corps)

        def do_GET(self) -> None:
            self._repondre("GET")

        def do_POST(self) -> None:
            self._repondre("POST")

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("%s %s", self.address_string(), format % args)

    return ThreadingHTTPServer((HOTE, port), Gestionnaire)


def url(serveur: ThreadingHTTPServer) -> str:
    return f"http://{HOTE}:{serveur.server_address[1]}/"
