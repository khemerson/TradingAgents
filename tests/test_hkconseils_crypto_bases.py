"""Fork HKCONSEILS - garde-fous sur la surcharge des bases crypto.

La liste amont est finie ; notre liste de surveillance la deborde. La surcharge
comble l'ecart, mais elle doit rester ADDITIVE et ne jamais mapper vers un
symbole que la source ne connait pas.

Ces tests ne touchent pas au reseau : la connaissance des symboles par la source
a ete verifiee separement, une fois, et le resultat est fige dans le module.
"""

import pytest

from tradingagents.dataflows.crypto_bases_hkconseils import (
    ALIAS_ACTIFS,
    ALIAS_TICKERS,
    BASES_SUPPLEMENTAIRES,
    appliquer_alias,
    base_supplementaire,
)
from tradingagents.dataflows.stocktwits import _stocktwits_symbol
from tradingagents.dataflows.symbol_utils import _CRYPTO_BASES, crypto_base

# Bases confirmees par requete reelle le 2026-08-22 (HTTP 200, 30 messages).
VERIFIEES = ("BNB", "TRX", "TAO", "ZEC", "SUI")


def test_liste_amont_intacte():
    """La surcharge complete l'amont, elle ne le remplace pas."""
    assert len(_CRYPTO_BASES) == 11
    assert {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "LINK"} <= _CRYPTO_BASES


def test_surcharge_disjointe_de_l_amont():
    """Aucun doublon : la surcharge n'ajoute que ce qui manque."""
    assert not (BASES_SUPPLEMENTAIRES & _CRYPTO_BASES)


def test_surcharge_limitee_aux_bases_verifiees():
    """Rien n'entre dans la surcharge sans verification cote source."""
    assert BASES_SUPPLEMENTAIRES == frozenset(VERIFIEES)


@pytest.mark.parametrize("base", VERIFIEES)
def test_base_supplementaire_reconnue(base):
    assert base_supplementaire(base)
    assert crypto_base(f"{base}-USD") == base


@pytest.mark.parametrize("base", ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "LINK"])
def test_bases_amont_toujours_reconnues(base):
    """La surcharge ne doit rien casser de ce qui marchait."""
    assert crypto_base(f"{base}-USD") == base


@pytest.mark.parametrize("base", VERIFIEES)
def test_mapping_stocktwits(base):
    assert _stocktwits_symbol(f"{base}-USD") == f"{base}.X"


def test_actions_non_affectees():
    """Une action ne doit jamais etre prise pour une crypto."""
    for action in ("AAPL", "NVDA", "TSLA", "MSFT", "GOOGL", "META"):
        assert crypto_base(action) is None
        assert _stocktwits_symbol(action) == action


def test_base_inconnue_reste_inconnue():
    """Le controle de disparition : on n'a pas ouvert la porte a n'importe quoi."""
    for faux in ("ZZZZ", "NOTACOIN", "FOO"):
        assert crypto_base(f"{faux}-USD") is None
        assert not base_supplementaire(faux)


def test_symbole_a_suffixe_numerique_non_mappe():
    """SUI20947 : StockTwits repond 404 sur SUI20947.X, on ne mappe pas vers du vide."""
    assert crypto_base("SUI20947-USD") is None
    assert _stocktwits_symbol("SUI20947-USD") == "SUI20947-USD"


def test_alias_desactives_par_defaut():
    """Activer un alias change le symbole interroge : c'est une decision d'exploitation."""
    assert ALIAS_ACTIFS is False
    assert appliquer_alias("SUI20947-USD") == "SUI20947-USD"


def test_alias_documente_pour_le_cas_connu():
    """La cible de normalisation est declaree, meme si elle n'est pas appliquee."""
    assert ALIAS_TICKERS["SUI20947-USD"] == "SUI-USD"


def test_couverture_de_la_liste_de_surveillance():
    """Cible du GATE 8 : au moins 11 des 12 valeurs suivies sont couvertes."""
    surveillance = [
        "BTC-USD", "ETH-USD", "XRP-USD", "BNB-USD", "SOL-USD", "DOGE-USD",
        "ADA-USD", "TRX-USD", "LINK-USD", "TAO-USD", "ZEC-USD", "SUI20947-USD",
    ]
    couverts = [t for t in surveillance if crypto_base(t)]
    assert len(couverts) >= 11, f"seulement {len(couverts)}/12 couverts"
