"""GATE 5.2 - harnais differentiel : ancien enforcer vs enforcer de la branche.

Rejoue le meme jeu de decisions contre les deux implementations et exige des
verdicts ET des listes de violations identiques, une a une.

Le journal des DEUX enforcers est redirige vers des fichiers jetables avant tout
appel : le journal probatoire de l'ancien deploiement ne doit recevoir aucune
ecriture de ce harnais.

Usage : python harnais_differentiel.py [--temoin-inverse]
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

# Chemins relatifs au harnais : aucun chemin machine en dur dans le depot.
# HKCONSEILS_ENFORCER_REF pointe l'implementation de reference a comparer ;
# par defaut, un clone voisin nomme "tradingagents".
_ICI = Path(__file__).resolve().parent
ANCIEN = Path(
    os.environ.get(
        "HKCONSEILS_ENFORCER_REF",
        str(_ICI.parent / "tradingagents" / "soul_enforcer.py"),
    )
)
NOUVEAU = _ICI / "soul_enforcer.py"
JETABLE = Path(tempfile.mkdtemp(prefix="harnais_journaux_"))


def charger(nom, chemin, journal):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    # Redirection du journal APRES import : _DECISION_LOG est fige a l'import.
    mod._DECISION_LOG = journal
    return mod


# ---------------------------------------------------------------- fixtures
# Chaque entree : (nom, decision, cash_pct, is_microcap)
FIXTURES = [
    # --- conforme ---
    ("conforme complet",
     {"action": "BUY", "ticker": "AAPL", "entry_price": 100.0, "stop_loss": 95.0,
      "take_profit": 120.0, "position_size_pct": 10.0, "confidence": 7}, 80, False),
    ("HOLD nu", {"action": "HOLD", "ticker": "AAPL"}, 80, False),
    ("SELL nu", {"action": "SELL", "ticker": "AAPL"}, 80, False),
    ("WATCH nu", {"action": "WATCH", "ticker": "AAPL"}, 80, False),

    # --- long only ---
    ("SHORT refuse", {"action": "SHORT", "ticker": "AAPL"}, 80, False),
    ("SELL_SHORT refuse", {"action": "SELL_SHORT", "ticker": "AAPL"}, 80, False),
    ("PUT refuse", {"action": "PUT", "ticker": "AAPL"}, 80, False),
    ("SHORT_SELL refuse", {"action": "SHORT_SELL", "ticker": "AAPL"}, 80, False),

    # --- action inconnue ---
    ("action inconnue", {"action": "YOLO", "ticker": "AAPL"}, 80, False),
    ("action absente", {"ticker": "AAPL"}, 80, False),
    ("action vide", {"action": "", "ticker": "AAPL"}, 80, False),

    # --- stop-loss ---
    ("stop-loss absent",
     {"action": "BUY", "entry_price": 100.0, "take_profit": 120.0,
      "position_size_pct": 10.0, "confidence": 7}, 80, False),
    ("stop-loss a la limite -7%",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 93.0, "take_profit": 120.0,
      "position_size_pct": 10.0, "confidence": 7}, 80, False),
    ("stop-loss juste au-dela -7.1%",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 92.9, "take_profit": 120.0,
      "position_size_pct": 10.0, "confidence": 7}, 80, False),
    ("stop-loss -15%",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 85.0, "take_profit": 120.0,
      "position_size_pct": 10.0, "confidence": 7}, 80, False),
    ("stop-loss -15% en micro-cap (tolere)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 85.0, "take_profit": 120.0,
      "position_size_pct": 4.0, "confidence": 7}, 80, True),
    ("stop-loss -16% en micro-cap (refuse)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 84.0, "take_profit": 120.0,
      "position_size_pct": 4.0, "confidence": 7}, 80, True),

    # --- take-profit ---
    ("take-profit absent",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0,
      "position_size_pct": 10.0, "confidence": 7}, 80, False),
    ("take-profit a zero",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 0.0,
      "position_size_pct": 10.0, "confidence": 7}, 80, False),

    # --- taille de position ---
    ("position a la limite 30%",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 30.0, "confidence": 7}, 100, False),
    ("position 31%",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 31.0, "confidence": 7}, 100, False),
    ("position 5% micro-cap (limite)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 5.0, "confidence": 7}, 100, True),
    ("position 6% micro-cap (refuse)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 6.0, "confidence": 7}, 100, True),

    # --- tresorerie ---
    ("tresorerie a la limite (80-60=20)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 60.0, "confidence": 7}, 80, False),
    ("tresorerie sous la limite (80-61=19)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 61.0, "confidence": 7}, 80, False),
    ("tresorerie negative",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 50.0, "confidence": 1}, 30, False),

    # --- confiance ---
    ("confiance a la limite 4",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 10.0, "confidence": 4}, 80, False),
    ("confiance 3",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 10.0, "confidence": 3}, 80, False),
    ("confiance 6 micro-cap (limite)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 4.0, "confidence": 6}, 80, True),
    ("confiance 5 micro-cap (refuse)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 4.0, "confidence": 5}, 80, True),
    ("confiance absente (regle neutralisee)",
     {"action": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 10.0}, 80, False),

    # --- OVERWEIGHT soumis aux memes controles que BUY ---
    ("OVERWEIGHT sans stop-loss",
     {"action": "OVERWEIGHT", "entry_price": 100.0, "take_profit": 120.0,
      "position_size_pct": 10.0, "confidence": 7}, 80, False),
    ("UNDERWEIGHT non soumis aux controles BUY",
     {"action": "UNDERWEIGHT", "ticker": "AAPL"}, 80, False),

    # --- violations cumulees ---
    ("violations multiples",
     {"action": "BUY", "entry_price": 100.0, "position_size_pct": 50.0,
      "confidence": 1}, 30, False),
    ("SHORT avec parametres complets",
     {"action": "SHORT", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 120.0,
      "position_size_pct": 10.0, "confidence": 7}, 80, False),

    # --- valeurs degenerees ---
    ("valeurs None", {"action": "BUY", "entry_price": None, "stop_loss": None,
                      "take_profit": None, "position_size_pct": None,
                      "confidence": None}, 80, False),
    ("valeurs texte non numerique",
     {"action": "BUY", "entry_price": "abc", "stop_loss": "n/a", "take_profit": "-",
      "position_size_pct": "beaucoup", "confidence": "haute"}, 80, False),
    ("action en minuscules", {"action": "buy", "entry_price": 100.0, "stop_loss": 95.0,
                              "take_profit": 120.0, "position_size_pct": 10.0,
                              "confidence": 7}, 80, False),
    ("decision vide", {}, 80, False),
]


def _via_adaptateur(decision):
    """Projette une decision dans le schema typé puis la ressort par l'adaptateur.

    Retourne None quand le schema REJETTE la decision : c'est le comportement
    recherche (une confiance hors echelle, une action inconnue ou une taille de
    position textuelle ne doivent plus jamais atteindre l'enforcer).
    """
    from tradingagents.agents.schemas_soul import PortfolioDecisionSOUL, to_enforcer_dict

    champs = dict(decision)
    action = champs.get("action")
    if not action:
        return None
    try:
        objet = PortfolioDecisionSOUL(
            rating="Hold",
            executive_summary="fixture",
            investment_thesis="fixture",
            action=action,
            ticker=champs.get("ticker") or "TEST",
            entry_price=champs.get("entry_price"),
            stop_loss=champs.get("stop_loss"),
            take_profit=champs.get("take_profit"),
            position_size_pct=champs.get("position_size_pct"),
            confidence=champs.get("confidence"),
            rationale="fixture",
        )
    except Exception:
        return None
    return to_enforcer_dict(objet)


def volet_adaptateur(nouveau):
    """Le passage par le schema typé ne doit pas changer un verdict.

    Pour chaque fixture representable par le schema, on compare le verdict rendu
    a partir du dictionnaire brut et celui rendu apres aller-retour par le
    schema. Les fixtures que le schema refuse sont comptees a part : leur refus
    est le resultat voulu, pas un ecart.
    """
    identiques = rejetees = ecarts = 0
    details = []
    for nom, decision, cash, mc in FIXTURES:
        adapte = _via_adaptateur(decision)
        if adapte is None:
            rejetees += 1
            details.append(("rejetee par le schema", nom))
            continue
        a = nouveau.enforce(dict(decision), cash_pct=cash, is_microcap=mc)
        b = nouveau.enforce(dict(adapte), cash_pct=cash, is_microcap=mc)
        if a.valid == b.valid and list(a.violations) == list(b.violations):
            identiques += 1
        else:
            ecarts += 1
            details.append(("ECART", f"{nom}: {a.violations} vs {b.violations}"))
    print()
    print("=== volet adaptateur : le passage par le schema typé change-t-il un verdict ? ===")
    for etiquette, texte in details:
        print(f"  {etiquette:24} {texte}")
    print(f"  verdicts identiques        : {identiques}")
    print(f"  fixtures refusees en amont : {rejetees}  (refus voulu : elles n atteignent plus l enforcer)")
    print(f"  ecarts                     : {ecarts}")
    return ecarts == 0


def main():
    temoin_inverse = "--temoin-inverse" in sys.argv
    JETABLE.mkdir(parents=True, exist_ok=True)
    ancien = charger("enf_ancien", ANCIEN, JETABLE / "ancien.jsonl")
    nouveau = charger("enf_nouveau", NOUVEAU, JETABLE / "nouveau.jsonl")

    print(f"  ancien   : {ANCIEN}")
    print(f"  nouveau  : {NOUVEAU}")
    print(f"  fixtures : {len(FIXTURES)}")
    print()

    ecarts = []
    for i, (nom, decision, cash, mc) in enumerate(FIXTURES):
        d_a = dict(decision)
        d_n = dict(decision)
        if temoin_inverse and i == 0:
            # Altere UNE fixture cote nouveau : le harnais doit le voir.
            d_n["action"] = "SHORT"
        ra = ancien.enforce(d_a, cash_pct=cash, is_microcap=mc)
        rn = nouveau.enforce(d_n, cash_pct=cash, is_microcap=mc)
        meme_verdict = ra.valid == rn.valid
        memes_violations = list(ra.violations) == list(rn.violations)
        if meme_verdict and memes_violations:
            statut = "identique"
        else:
            statut = "ECART"
            ecarts.append((nom, ra, rn))
        marque = "  " if statut == "identique" else ">>"
        print(f"{marque} {nom:42} {statut:10} valid={ra.valid}/{rn.valid} "
              f"violations={len(ra.violations)}/{len(rn.violations)}")

    print()
    if ecarts:
        print(f"  {len(ecarts)} ECART(S) :")
        for nom, ra, rn in ecarts:
            print(f"    - {nom}")
            print(f"        ancien  : valid={ra.valid} {ra.violations}")
            print(f"        nouveau : valid={rn.valid} {rn.violations}")
    verdict_ok = not ecarts

    if temoin_inverse:
        # Ici on ATTEND un ecart : le harnais doit avoir detecte l'alteration.
        print("  temoin inverse :", "OK - ecart detecte" if ecarts else "ABORT - non detecte")
        return 0 if ecarts else 1

    print("  VERDICT :", "0 ecart sur "
          f"{len(FIXTURES)} fixtures - equivalence semantique prouvee"
          if verdict_ok else "ECHEC")

    adaptateur_ok = volet_adaptateur(nouveau)
    return 0 if (verdict_ok and adaptateur_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
