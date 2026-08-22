"""Extension HKCONSEILS du schema de decision du Portfolio Manager.

Pourquoi ce fichier existe
--------------------------
L'enforcer SOUL est deterministe : il exige donc des ENTREES deterministes.
Le schema amont ``PortfolioDecision`` ne porte ni prise de benefice, ni taille de
position numerique, ni indice de confiance — trois entrees dont dependent des
regles SOUL. Jusqu'ici ces valeurs etaient reclamees par le prompt sous forme
d'un bloc JSON libre, puis relues par expressions regulieres sur de la prose.
Ce chemin produisait des valeurs fausses sans que rien ne le signale (une prise
de benefice a 3.0 sur un titre cotant 231, une confiance lue sur une echelle
/100 la ou l'enforcer attend /10).

On etend donc le schema plutot que de deviner : les champs deviennent typés, et
les bornes sont validees par le schema lui-meme. Le schema amont n'est pas
modifie — on en herite.

Ce fichier ne contient AUCUNE regle SOUL : les seuils, l'ordre d'evaluation et
les verdicts restent la propriete exclusive de soul_enforcer.py.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator

from tradingagents.agents.schemas import PortfolioDecision

# Bornes de l'echelle de confiance SOUL. Declarees ici pour que la description
# du champ, la validation et le message d'erreur ne puissent pas diverger.
CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 10.0

# Valeur par defaut et plafond de la fenetre de surveillance, repris du format
# SOUL existant (defaut 7 jours, maximum 30).
WATCH_EXPIRY_DEFAULT = 7
WATCH_EXPIRY_MAX = 30


class SOULAction(str, Enum):
    """Actions admises par l'enforcer.

    Reprend les cinq niveaux amont et y ajoute WATCH, defini par le format SOUL :
    le montage est interessant mais l'entree n'est pas opportune maintenant.
    WATCH ne consomme pas de capital.
    """

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    OVERWEIGHT = "OVERWEIGHT"
    UNDERWEIGHT = "UNDERWEIGHT"
    WATCH = "WATCH"


class PortfolioDecisionSOUL(PortfolioDecision):
    """Decision du Portfolio Manager, enrichie des entrees dont l'enforcer depend.

    Herite de l'integralite du schema amont (``rating``, ``executive_summary``,
    ``investment_thesis``, ``price_target``, ``time_horizon``), qui continue
    d'alimenter le rendu markdown et les consommateurs en aval.
    """

    action: SOULAction = Field(
        description=(
            "Action operationnelle. Exactement l'une de : BUY, SELL, HOLD, "
            "OVERWEIGHT, UNDERWEIGHT, WATCH. Doit rester coherente avec le champ "
            "rating ; utiliser WATCH lorsque le montage merite surveillance mais "
            "que l'entree n'est pas opportune maintenant."
        ),
    )
    ticker: str = Field(
        description="Symbole exact analyse, suffixe d'place compris (ex. -USD).",
    )
    entry_price: float | None = Field(
        default=None,
        description="Prix d'entree vise, dans la devise de cotation. Omettre si sans objet.",
    )
    stop_loss: float | None = Field(
        default=None,
        description=(
            "Prix d'invalidation, dans la devise de cotation — un PRIX, jamais un "
            "pourcentage. Obligatoire pour toute prise de position."
        ),
    )
    take_profit: float | None = Field(
        default=None,
        description=(
            "Prix objectif, dans la devise de cotation — un PRIX, jamais un "
            "pourcentage ni un multiple. Obligatoire pour toute prise de position."
        ),
    )
    position_size_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description=(
            "Taille de position en POURCENTAGE du portefeuille, entre 0 et 100. "
            "Nombre seul, sans signe pourcent ni texte (ecrire 3.0, pas '3% du "
            "portefeuille')."
        ),
    )
    strategy: str | None = Field(
        default=None,
        description="Cadre de reference retenu : Buffett, Dalio, Cohen, Simons ou Soros.",
    )
    confidence: float | None = Field(
        default=None,
        ge=CONFIDENCE_MIN,
        le=CONFIDENCE_MAX,
        description=(
            "Indice de confiance sur une echelle de 0 a 10, ou 10 vaut confiance "
            "maximale. IMPERATIF : l'echelle est /10, jamais /100 — une confiance "
            "de 65 pour cent s'ecrit 6.5, pas 65."
        ),
    )
    rationale: str = Field(
        default="",
        description="Justification synthetique de la decision, en une a trois phrases.",
    )

    # --- champs propres a WATCH -------------------------------------------
    entry_target: float | None = Field(
        default=None,
        description="WATCH uniquement : prix d'entree ideal a surveiller.",
    )
    conditions: str | None = Field(
        default=None,
        description="WATCH uniquement : conditions a reunir pour passer a BUY.",
    )
    expiry_days: int | None = Field(
        default=None,
        ge=1,
        le=WATCH_EXPIRY_MAX,
        description=(
            f"WATCH uniquement : duree de surveillance en jours "
            f"(defaut {WATCH_EXPIRY_DEFAULT}, maximum {WATCH_EXPIRY_MAX})."
        ),
    )

    @field_validator("action", mode="before")
    @classmethod
    def _normaliser_action(cls, v):
        """Accepte 'Buy' ou 'buy' aussi bien que 'BUY'.

        Tolerance de forme uniquement : aucune action nouvelle n'est admise, et
        une valeur inconnue reste rejetee par l'enumeration.
        """
        if isinstance(v, str):
            return v.strip().upper()
        return v

    @field_validator("ticker", mode="before")
    @classmethod
    def _normaliser_ticker(cls, v):
        return v.strip().upper() if isinstance(v, str) else v


def to_enforcer_dict(decision: PortfolioDecisionSOUL) -> dict:
    """Projette la decision typee dans la forme de dictionnaire qu'``enforce()`` consomme.

    Adaptateur de lecture strict : aucune valeur n'est devinee, deduite ni
    convertie d'echelle. Ce que le schema porte est transmis tel quel ; ce qu'il
    ne porte pas reste absent, et l'enforcer applique alors ses propres regles.
    """
    d = {
        "action": decision.action.value,
        "ticker": decision.ticker,
        "entry_price": decision.entry_price,
        "stop_loss": decision.stop_loss,
        "take_profit": decision.take_profit,
        "position_size_pct": decision.position_size_pct,
        "strategy": decision.strategy,
        "confidence": decision.confidence,
        "rationale": decision.rationale,
    }
    if decision.action is SOULAction.WATCH:
        d["entry_target"] = decision.entry_target
        d["conditions"] = decision.conditions
        d["expiry_days"] = (
            decision.expiry_days if decision.expiry_days is not None else WATCH_EXPIRY_DEFAULT
        )
    return d
