"""Surcharge HKCONSEILS des bases crypto reconnues.

Pourquoi ce module existe
-------------------------
La liste amont ``_CRYPTO_BASES`` est finie : elle couvre onze bases, dont sept
seulement figurent dans notre liste de surveillance. Les autres restaient sous
leur forme ``<BASE>-USD``, sur laquelle StockTwits repond 404 — leur sentiment
etait donc degrade **sans que rien ne le signale**.

Cette surcharge est **additive** : la liste amont n'est pas modifiee, elle est
completee. Un rapprochement amont qui l'enrichirait ne creerait aucun conflit,
et le doublon eventuel serait sans effet (union d'ensembles).

Regle de peuplement
-------------------
Une base n'entre ici **qu'apres verification par requete reelle** que la source
la connait. Mapper vers un symbole inexistant serait pire que ne pas mapper : on
echangerait une degradation visible contre une reponse vide silencieuse.

Verification du 2026-08-22, endpoint public StockTwits :

    BNB.X  200, 30 messages        TAO.X  200, 30 messages
    TRX.X  200, 30 messages        ZEC.X  200, 30 messages
    SUI.X  200, 30 messages        SUI20947.X  404  <- ecartee

Cote Reddit, les sondes reviennent en 429 (limitation de debit) y compris pour
des bases deja couvertes et operationnelles comme ETH : ce code ne dit rien de
la connaissance du symbole, il n'est donc pas retenu comme critere.
"""

from __future__ import annotations

# Bases verifiees comme connues de StockTwits, absentes de la liste amont.
BASES_SUPPLEMENTAIRES: frozenset[str] = frozenset({
    "BNB",   # BNB Chain
    "TRX",   # TRON
    "TAO",   # Bittensor
    "ZEC",   # Zcash
    "SUI",   # Sui
})

# Alias de ticker : forme rencontree -> forme que les sources connaissent.
# Cas SUI20947-USD : le suffixe numerique est un artefact de desambiguisation
# d'un fournisseur de cours ; les sources sociales ne connaissent que SUI.
#
# ATTENTION : ces alias sont DESACTIVES par defaut. Les activer modifie le
# symbole effectivement interroge pour un ticker de la liste de surveillance,
# ce qui releve d'une decision d'exploitation, pas d'un portage. Voir le
# rapport du GATE 8 pour l'arbitrage.
ALIAS_TICKERS: dict[str, str] = {
    "SUI20947-USD": "SUI-USD",
}

ALIAS_ACTIFS = False


def base_supplementaire(base: str | None) -> bool:
    """La base fait-elle partie de la surcharge HKCONSEILS ?"""
    return bool(base) and base.upper() in BASES_SUPPLEMENTAIRES


def appliquer_alias(ticker: str) -> str:
    """Remplace un ticker par sa forme reconnue, si les alias sont actifs."""
    if not ALIAS_ACTIFS:
        return ticker
    return ALIAS_TICKERS.get(ticker.strip().upper(), ticker)
