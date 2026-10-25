from __future__ import annotations

import csv
import io
import logging
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

JEU_ENTREPRISES = "consommation-annuelle-entreprise-par-adresse"
# Champs demandés à l'API, dans cet ordre : l'en-tête CSV donne des libellés, pas ces noms
CHAMPS = (
    "annee",
    "code_commune",
    "tri_des_adresses",
    "code_iris",
    "adresse",
    "nombre_de_sites",
    "consommation_annuelle_totale_de_ladresse_mwh",
    "code_grand_secteur",
    "code_secteur_naf2",
)
# Plus grande page servie par data-fair
TAILLE_PAGE = 10_000


class EnedisError(ExtractionError):
    pass


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        base_url=settings.enedis_base_url,
        timeout=max(settings.http_timeout_seconds, 120),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


# Dernière année publiée : la photographie la plus récente des entreprises raccordées
def derniere_annee(http: httpx.Client, settings: Settings) -> str:
    reponse = _retrying(settings)(
        lambda: _verifier(http.get(f"{JEU_ENTREPRISES}/values_agg", params={"field": "annee"}))
    )
    annees = [agg["value"] for agg in reponse.json().get("aggs", [])]
    if not annees:
        raise EnedisError("consommation des entreprises Enedis : aucune année publiée")
    return max(annees)


# Une page CSV, lue par position puisque l'ordre des colonnes suit `select`
def lire_page(contenu: bytes) -> list[dict[str, Any]]:
    lecteur = csv.reader(io.StringIO(contenu.decode("utf-8-sig")))
    entete = next(lecteur, None)
    if entete is None:
        return []
    if len(entete) != len(CHAMPS):
        raise EnedisError(f"consommation des entreprises Enedis : {len(entete)} colonnes reçues")
    return [dict(zip(CHAMPS, (v or None for v in ligne), strict=True)) for ligne in lecteur]


# Consommation annuelle de chaque adresse d'entreprise raccordée à Enedis, pour une année
def telecharger_conso_entreprises(
    *,
    annee: str | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    http = client or _client(settings)
    try:
        annee = annee or derniere_annee(http, settings)
        url: str | None = f"{JEU_ENTREPRISES}/lines"
        params: dict[str, Any] | None = {
            "format": "csv",
            "size": TAILLE_PAGE,
            "qs": f"annee:{annee}",
            "sort": "_i",
            "select": ",".join(CHAMPS),
        }
        lignes: list[dict[str, Any]] = []
        pages = 0
        while url:
            reponse = _retrying(settings)(
                lambda url=url, params=params: _verifier(http.get(url, params=params))
            )
            page = lire_page(reponse.content)
            lignes += page
            pages += 1
            # La page suivante vient de l'en-tête Link, avec son curseur `after`
            suivante = reponse.links.get("next", {}).get("url")
            url, params = (suivante, None) if page and suivante else (None, None)
    finally:
        if client is None:
            http.close()
    logger.info("enedis entreprises annee=%s lignes=%d pages=%d", annee, len(lignes), pages)
    return lignes
