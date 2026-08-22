"""Analyste vision — 5e maillon de la chaine d'analystes, lit une image de graphique.

Contrairement aux autres analystes, celui-ci n'utilise **aucun outil** : il lit une
image encodee en base64 deposee dans l'etat (cle ``chart_image_b64``) et rend un
rapport visuel structure. Sans image, il rend un rapport de substitution et la
chaine se poursuit normalement.

Portage v0.3.1 : le contexte d'instrument est desormais resolu une fois pour toutes
au demarrage du run et depose dans l'etat (``instrument_context``) ; on le lit au
lieu de le reconstruire, ce qui aligne cet analyste sur les quatre autres.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)

# Plafond de sortie propre a cet agent : une description d'image n'a pas besoin du
# plafond global. Transmis en kwarg d'invocation — surtout pas via ``config=``, qui
# est le RunnableConfig de LangChain et ignorerait silencieusement la valeur.
VISION_MAX_TOKENS = 8000

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
    """Fabrique le noeud de l'analyste vision.

    Args:
        llm: le modele « cerveau », qui doit accepter les tableaux de contenu
             multimodaux de l'API OpenAI-compatible (projecteur multimodal actif).
    """

    def vision_analyst_node(state):
        image_b64 = state.get("chart_image_b64")

        if not image_b64:
            return {"vision_report": NO_IMAGE_REPORT}

        ticker = state.get("company_of_interest", "")
        trade_date = state.get("trade_date", "")
        # v0.3.1 : identite resolue au demarrage du run. Repli sur une
        # reconstruction locale si l'etat ne la porte pas (appel isole, test).
        instrument_context = state.get("instrument_context") or build_instrument_context(
            ticker, state.get("asset_type", "stock")
        )
        language_instruction = get_language_instruction()

        system_content = VISION_SYSTEM_PROMPT + "\n" + language_instruction

        user_text = (
            f"Ticker: {ticker}\n"
            f"Date: {trade_date}\n"
            f"Contexte: {instrument_context}\n\n"
            f"Analyse le chart ci-joint et produis ton rapport structuré."
        )

        # Tableau de contenu multimodal, format OpenAI-compatible
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
                max_tokens=VISION_MAX_TOKENS,
            )
            content = result.content if hasattr(result, "content") else str(result)
            if isinstance(content, list):
                # Normalise un contenu en blocs (raisonnement, etc.) vers une chaine
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
