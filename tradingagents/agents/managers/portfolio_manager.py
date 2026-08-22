"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

import logging

from tradingagents.agents.schemas import render_pm_decision
from tradingagents.agents.schemas_soul import PortfolioDecisionSOUL, to_enforcer_dict
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


logger = logging.getLogger(__name__)


def create_portfolio_manager(llm):
    # Fork HKCONSEILS : le schema etendu porte les entrees dont l'enforcer SOUL
    # depend (prise de benefice, taille de position numerique, confiance bornee).
    # Le schema amont n'est pas modifie : PortfolioDecisionSOUL en herite.
    structured_llm = bind_structured(llm, PortfolioDecisionSOUL, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        # Fork HKCONSEILS : on capture l'objet typé, pas seulement son rendu.
        # C'est lui qui alimente l'enforcer ; le markdown reste inchangé pour le
        # journal de decisions, l'affichage et les rapports sauvegardes.
        soul_decision = None
        final_trade_decision = None
        if structured_llm is not None:
            try:
                parsed = structured_llm.invoke(prompt)
                if parsed is not None:
                    # Le rendu markdown ne depend QUE du schema amont : il reste
                    # possible meme si l'objet ne porte pas les champs SOUL.
                    final_trade_decision = render_pm_decision(parsed)
                    if isinstance(parsed, PortfolioDecisionSOUL):
                        soul_decision = to_enforcer_dict(parsed)
                    else:
                        logger.warning(
                            "Portfolio Manager: decision typee sans les champs "
                            "SOUL (%s) - l'enforcer lira une decision "
                            "reconstruite par analyse textuelle",
                            type(parsed).__name__,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Portfolio Manager: sortie structuree indisponible (%s) ; "
                    "repli sur le texte libre",
                    exc,
                )
        if final_trade_decision is None:
            # Filet de securite. Ce chemin ne doit PAS etre le mode nominal :
            # l'enforcer y perd ses entrees typees. Trace explicitement pour
            # qu'une recurrence se voie.
            logger.warning(
                "Portfolio Manager: aucune decision typee obtenue - repli sur "
                "l analyse textuelle (signal d anomalie, pas un mode de "
                "fonctionnement)"
            )
            final_trade_decision = llm.invoke(prompt).content

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            # Fork HKCONSEILS : None quand la sortie structuree a echoue, ce qui
            # renvoie l'enforcer vers son filet d'analyse textuelle.
            "soul_decision": soul_decision,
        }

    return portfolio_manager_node
