"""Vision Analyst — 5th analyst analyzing chart images via mmproj-capable LLM.

Unlike the other analysts, this one does NOT use tools. It takes a base64 image
injected in the state (key: chart_image_b64) and returns a structured visual
report. If no image is present, it returns a minimal placeholder and the
pipeline continues normally.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)


VISION_SYSTEM_PROMPT = """Tu es un analyste technique visuel expert. Tu analyses des charts de trading (TradingView, Binance, etc.) pour identifier des patterns et niveaux clés que les indicateurs numériques ne capturent pas.

Analyse l'image fournie et produis un rapport structuré couvrant :

**IDENTIFICATION** : quel actif, quel timeframe, quel exchange (si visible)
**PATTERNS** : patterns chartistes identifiés (triangle, head & shoulders, channel, wedge, double top/bottom, cup & handle, flag, pennant, etc.)
**NIVEAUX CLÉS** : supports, résistances, zones de demande/offre annotées ou visibles
**TENDANCE VISUELLE** : direction du trend (haussier/baissier/range), force visuelle
**INDICATEURS VISUELS** : moyennes mobiles visibles, croisements, volume profile si visible
**ANNOTATIONS** : texte ou annotations manuelles dessinées sur le chart (flèches, "we are here", targets, etc.)
**BIAIS DIRECTIONNEL** : synthèse — bullish, bearish, ou neutral, avec justification visuelle
**CONFIANCE** : qualité de l'image (claire/floue/compressée), fiabilité de la lecture (haute/moyenne/basse)

Réponds en français. Sois précis sur les niveaux de prix. Si l'image est trop floue ou incompréhensible, dis-le clairement. Termine par un tableau Markdown récapitulatif."""


NO_IMAGE_REPORT = (
    "Aucune image disponible pour cette analyse.\n\n"
    "Le Vision Analyst n'a pas reçu de chart à analyser. Pour inclure une "
    "analyse visuelle, fournir l'argument --image lors de l'exécution de "
    "run_analysis.py."
)


def create_vision_analyst(llm):
    """Factory for the Vision Analyst node.

    Args:
        llm: The deep-thinking LLM (Qwen3.5-27B + mmproj on LXC 225).
             Must support OpenAI-compatible vision content arrays.
    """

    def vision_analyst_node(state):
        image_b64 = state.get("chart_image_b64")

        if not image_b64:
            return {"vision_report": NO_IMAGE_REPORT}

        ticker = state.get("company_of_interest", "")
        trade_date = state.get("trade_date", "")
        instrument_context = build_instrument_context(ticker)
        language_instruction = get_language_instruction()

        system_content = VISION_SYSTEM_PROMPT + "\n" + language_instruction

        user_text = (
            f"Ticker: {ticker}\n"
            f"Date: {trade_date}\n"
            f"Contexte: {instrument_context}\n\n"
            f"Analyse le chart ci-joint et produis ton rapport structuré."
        )

        # OpenAI-compatible multimodal content array
        human_msg = HumanMessage(
            content=[
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ]
        )

        try:
            result = llm.invoke(
                [SystemMessage(content=system_content), human_msg],
                config={"max_tokens": 8000},
            )
            content = result.content if hasattr(result, "content") else str(result)
            if isinstance(content, list):
                # Normalize list content (reasoning blocks etc.) to string
                content = "\n".join(
                    str(b.get("text", b)) if isinstance(b, dict) else str(b)
                    for b in content
                )
            report = str(content).strip() or "Vision Analyst: réponse vide du LLM."
        except Exception as e:
            report = (
                f"Vision Analyst: erreur lors de l'analyse de l'image — "
                f"{type(e).__name__}: {e}"
            )

        return {"vision_report": report}

    return vision_analyst_node
