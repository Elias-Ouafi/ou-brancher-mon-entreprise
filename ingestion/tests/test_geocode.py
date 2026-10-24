from __future__ import annotations

from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.geocode import (
    REGIONS,
    AdresseAmbigueError,
    AdresseIntrouvableError,
    BanClient,
    GeocodingError,
    code_region,
    geocode,
    normalise,
)

BAN_URL = "https://ban.test/geocodage"


@pytest.fixture
def ban_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"ban_base_url": BAN_URL})


def feature(**props: Any) -> dict[str, Any]:
    defaults = {
        "label": "12 Rue de la Paix 69003 Lyon",
        "score": 0.96,
        "type": "housenumber",
        "citycode": "69383",
        "city": "Lyon",
        "postcode": "69003",
        "context": "69, Rhône, Auvergne-Rhône-Alpes",
    }
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [4.8456, 45.7578]},
                "properties": defaults | props,
            }
        ],
    }


# BAN et ODRÉ n'écrivent pas les régions de la même façon
def test_region_names_match_whatever_the_spelling() -> None:
    assert code_region("Auvergne-Rhône-Alpes") == "84"
    assert code_region("AUVERGNE-RHONE-ALPES") == "84"
    assert code_region("Provence-Alpes-Côte d'Azur") == "93"
    assert code_region("PROVENCE-ALPES-COTE D'AZUR") == "93"
    assert code_region("Île-de-France") == "11"
    assert code_region("ILE DE FRANCE") == "11"
    assert code_region("Pays de la Loire") == "52"


def test_unknown_region_returns_none() -> None:
    assert code_region("Bourgogne") is None
    assert code_region("") is None


def test_normalise_strips_accents_case_and_punctuation() -> None:
    assert normalise("Côte d'Azur") == normalise("COTE D AZUR")


def test_every_region_code_is_unique_and_two_digits() -> None:
    assert len(REGIONS) == 18
    assert all(len(c) == 2 and c.isdigit() for c in REGIONS)
    assert len(set(REGIONS.values())) == len(REGIONS)


def test_address_resolves_to_its_territory(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(json=feature())

    territoire = geocode("12 rue de la Paix, Lyon", settings=ban_settings)

    assert territoire.code_insee_commune == "69383"
    assert territoire.commune == "Lyon"
    assert territoire.code_postal == "69003"
    assert territoire.code_departement == "69"
    assert territoire.departement == "Rhône"
    assert territoire.region == "Auvergne-Rhône-Alpes"
    # La clé de jointure avec les jeux ODRÉ, absente de la réponse BAN
    assert territoire.code_insee_region == "84"
    assert (territoire.latitude, territoire.longitude) == (45.7578, 4.8456)
    assert territoire.est_precis


def test_query_is_sent_with_the_address_and_a_few_candidates(
    httpx_mock: HTTPXMock, ban_settings: Settings
) -> None:
    httpx_mock.add_response(json=feature())

    geocode("12 rue de la Paix, Lyon", settings=ban_settings)

    request = httpx_mock.get_requests()[0]
    assert str(request.url).startswith(f"{BAN_URL}/search")
    assert request.url.params["q"] == "12 rue de la Paix, Lyon"
    assert request.url.params["limit"] == "5"
    assert request.url.params["index"] == "address"


# Outre-mer, département et région portent le même nom
def test_overseas_context_with_two_segments(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(
        json=feature(citycode="97411", city="Saint-Denis", context="974, La Réunion")
    )

    territoire = geocode("Saint-Denis", settings=ban_settings)

    assert territoire.code_departement == "974"
    assert territoire.region == "La Réunion"
    assert territoire.code_insee_region == "04"


# 2A n'est pas un nombre : un code département reste du texte
def test_corsica_department_codes_are_kept_as_text(
    httpx_mock: HTTPXMock, ban_settings: Settings
) -> None:
    httpx_mock.add_response(
        json=feature(citycode="2A004", city="Ajaccio", context="2A, Corse-du-Sud, Corse")
    )

    territoire = geocode("Ajaccio", settings=ban_settings)

    assert territoire.code_departement == "2A"
    assert territoire.code_insee_region == "94"


def test_no_result_raises(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(json={"type": "FeatureCollection", "features": []})

    with pytest.raises(AdresseIntrouvableError, match="aucun resultat"):
        geocode("zzzz", settings=ban_settings)


# Inutile de consommer un appel pour une chaîne vide
def test_empty_address_never_calls_the_api(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    with pytest.raises(AdresseIntrouvableError, match="adresse vide"):
        geocode("   ", settings=ban_settings)

    assert httpx_mock.get_requests() == []


def candidats(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [f["features"][0] for f in features]}


# « 1 place de la mairie, Fort-de-France » : la BAN propose Dracy-le-Fort à 0,59
def test_low_score_is_refused(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(json=feature(score=0.59, label="1 Place de la Mairie 71640 Dracy"))

    with pytest.raises(AdresseIntrouvableError, match=r"trop approximatif.*0.59"):
        geocode("1 place de la mairie, Fort-de-France", settings=ban_settings)


def test_same_street_in_several_communes_is_ambiguous(
    httpx_mock: HTTPXMock, ban_settings: Settings
) -> None:
    httpx_mock.add_response(
        json=candidats(
            feature(score=0.98, citycode="33063", postcode="33000", label="3 A. 33000 Bordeaux"),
            feature(score=0.975, citycode="02691", postcode="02100", label="3 A. 02100 Quentin"),
            feature(score=0.90, citycode="59512", postcode="59100", label="3 A. 59100 Roubaix"),
        )
    )

    with pytest.raises(AdresseAmbigueError) as erreur:
        geocode("3 allée des peupliers", settings=ban_settings)

    assert erreur.value.candidats == ["3 A. 33000 Bordeaux", "3 A. 02100 Quentin"]


def test_homonym_communes_are_told_apart_by_department(
    httpx_mock: HTTPXMock, ban_settings: Settings
) -> None:
    commune = {"score": 0.958, "type": "municipality", "label": "Saint-Denis", "postcode": None}
    httpx_mock.add_response(
        json=candidats(
            feature(**commune, citycode="97411", context="974, La Réunion"),
            feature(**commune, citycode="93066", context="93, Seine-Saint-Denis, Île-de-France"),
        )
    )

    with pytest.raises(AdresseAmbigueError) as erreur:
        geocode("Saint-Denis", settings=ban_settings)

    assert erreur.value.candidats == ["Saint-Denis (974)", "Saint-Denis (93)"]


# Rue Garibaldi traverse trois arrondissements de Lyon : une seule commune
def test_arrondissements_of_one_city_are_not_ambiguous(
    httpx_mock: HTTPXMock, ban_settings: Settings
) -> None:
    httpx_mock.add_response(
        json=candidats(
            feature(score=0.762, type="street", citycode="69383"),
            feature(score=0.761, type="street", citycode="69387"),
        )
    )

    assert geocode("10 rue Garibaldi, Lyon", settings=ban_settings).code_insee_commune == "69383"


def test_a_clear_winner_is_kept(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(
        json=candidats(
            feature(score=0.964, type="municipality", citycode="69123", label="Lyon"),
            feature(score=0.856, type="street", citycode="56002", label="Lyonne 56190 Ambon"),
        )
    )

    assert geocode("Lyon", settings=ban_settings).code_insee_commune == "69123"


def test_postcode_in_the_request_must_match(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(
        json=candidats(
            feature(score=0.97, citycode="33063", postcode="33000"),
            feature(score=0.80, citycode="91161", postcode="91380", label="Chilly-Mazarin"),
        )
    )
    territoire = geocode("3 allée des peupliers 91380", settings=ban_settings)
    assert territoire.code_insee_commune == "91161"

    httpx_mock.add_response(json=feature(postcode="33000"))
    with pytest.raises(AdresseIntrouvableError, match="code postal 75001"):
        geocode("3 allée des peupliers 75001", settings=ban_settings)


def test_commune_level_match_is_flagged(
    httpx_mock: HTTPXMock,
    ban_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    httpx_mock.add_response(json=feature(type="municipality", label="Lyon"))

    with caplog.at_level("WARNING"):
        territoire = geocode("Lyon", settings=ban_settings)

    assert not territoire.est_precis
    assert "maille" in caplog.text


# Mieux vaut un code absent qu'un code inventé : la jointure échouera visiblement
def test_unknown_region_is_warned_and_code_left_empty(
    httpx_mock: HTTPXMock,
    ban_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    httpx_mock.add_response(json=feature(context="99, Ailleurs, Région Inconnue"))

    with caplog.at_level("WARNING"):
        territoire = geocode("ailleurs", settings=ban_settings)

    assert territoire.code_insee_region is None
    assert "non reconnue" in caplog.text


def test_response_without_citycode_raises(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(json=feature(citycode=None))

    with pytest.raises(GeocodingError, match="sans code commune"):
        geocode("quelque part", settings=ban_settings)


def test_malformed_context_raises(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(json=feature(context="69"))

    with pytest.raises(GeocodingError, match="context inattendu"):
        geocode("quelque part", settings=ban_settings)


def test_transient_status_is_retried(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(status_code=503)
    httpx_mock.add_response(json=feature())

    with BanClient(ban_settings) as client:
        territoire = client.geocode("12 rue de la Paix, Lyon")

        assert territoire.code_insee_commune == "69383"
        assert client.calls == 2


# Une 400 ne deviendra pas une 200 au deuxième essai
def test_client_error_is_not_retried(httpx_mock: HTTPXMock, ban_settings: Settings) -> None:
    httpx_mock.add_response(status_code=400)

    with BanClient(ban_settings) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.geocode("12 rue de la Paix, Lyon")

        assert client.calls == 1
