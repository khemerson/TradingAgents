# HKCONSEILS — configuration du fork

Ce document décrit la configuration locale du fork TradingAgents de HKCONSEILS
(base upstream **v0.3.1**, commit `01477f9`).

> **Ce dépôt est public.** Aucune valeur d'infrastructure, adresse ou identifiant
> ne figure ici : seuls les *noms* de variables sont documentés. Les valeurs
> vivent dans `.env`, qui n'est jamais commité (`.gitignore`).

---

## 1. Principe : une seule source de vérité

`DEFAULT_CONFIG` applique les surcharges `TRADINGAGENTS_*` **au moment de son
import** (`tradingagents/default_config.py::_apply_env_overrides`). Toute la
configuration passe donc par `.env` — et **pas** par des valeurs codées en dur
dans `run_analysis.py`, qui écraseraient silencieusement l'environnement.

`pipeline_runner.py` lit `.env` lui-même et propage chaque variable à
l'environnement du sous-processus `run_analysis.py` (le préfixe `export` est
géré). Le mécanisme fonctionne donc aussi bien en cron qu'en lancement manuel.

---

## 2. Variables d'environnement

### 2.1 Accès au fournisseur LLM

| Variable | Rôle |
|---|---|
| `TRADINGAGENTS_LLM_PROVIDER` | Fournisseur. Valeur retenue : `openai_compatible` |
| `TRADINGAGENTS_LLM_BACKEND_URL` | Point d'entrée LiteLLM (`.../v1`) |
| `OPENAI_COMPATIBLE_API_KEY` | **Clé maître LiteLLM.** ⚠️ Le provider `openai_compatible` lit *cette* variable, **pas** `OPENAI_API_KEY` (cf. `tradingagents/llm_clients/api_key_env.py`) |

**Pourquoi `openai_compatible` plutôt que `openai` + `backend_url`** : ce provider
construit un `LocalCompatibleChatOpenAI`, qui n'impose pas le `tool_choice` en
forme objet que les serveurs locaux (llama.cpp, vLLM, LM Studio) rejettent
(upstream #1057). C'est ce qui sécurise les *structured outputs* des agents
décisionnels face à notre moteur local.

Le registre déclare `openai_compatible` en `key_optional=True` : sans clé, un
littéral `EMPTY` serait envoyé et LiteLLM répondrait `401`. **Vérifié** : la clé
est bien transmise dans l'en-tête `Authorization`.

### 2.2 Modèles

| Variable | Rôle |
|---|---|
| `TRADINGAGENTS_DEEP_THINK_LLM` | Modèle « cerveau » (managers, chercheurs, décision) |
| `TRADINGAGENTS_QUICK_THINK_LLM` | Modèle « ouvrier » (analystes, débatteurs risque) |

Les deux alias sont servis par LiteLLM, qui route vers les moteurs `llama.cpp`
correspondants. Le fork n'adresse **aucun** moteur en direct : LiteLLM est le
point d'entrée unique du chemin trading.

### 2.3 Résilience

| Variable | Valeur | Rôle |
|---|---|---|
| `TRADINGAGENTS_LLM_MAX_RETRIES` | `3` | Budget de reprise SDK, transmis à tous les fournisseurs (upstream #1091). Absorbe une rafale de `429` due à la co-tenancy GPU au lieu d'avorter le run. Arrive sous forme de chaîne et est converti par `trading_graph._coerce_max_retries` |

### 2.4 Paramètres de run

| Variable | Valeur | Rôle |
|---|---|---|
| `TRADINGAGENTS_OUTPUT_LANGUAGE` | `French` | Langue des rapports et de la décision finale (le débat interne reste en anglais) |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | `1` | Tours du débat haussier/baissier |
| `TRADINGAGENTS_MAX_RISK_ROUNDS` | `1` | Tours du débat risque |

### 2.5 Variables propres au fork (préfixe `HKCONSEILS_`)

| Variable | Défaut | Rôle |
|---|---|---|
| `HKCONSEILS_MAX_TOKENS` | `16000` | Plafond de sortie. **Volontairement non défini dans `.env`** — voir §3 |
| `HKCONSEILS_SDK_TIMEOUT` | `1800` | Délai SDK en secondes. **Volontairement non défini dans `.env`** — voir §4 |
| `HKCONSEILS_ENFORCER_LOG` | `<clone>/logs/enforcer_decisions.jsonl` | Journal probatoire de l'enforcer |
| `HKCONSEILS_REFUSED_LOG_DIR` | `<clone>/data` | Journal des refus d'écriture paper-trading |
| `PAPER_TRADING_DB` | base du tableau de bord | Base SQLite paper-trading (ressource **partagée** avec le tableau de bord — ne pas rediriger sans arbitrage) |
| `HKCONSEILS_REF_PRICE` / `_SOURCE` / `_TS` / `_TICKER` | — | Prix de référence injecté par `pipeline_runner.py` avant le sous-processus |
| `HKCONSEILS_MICROCAP` | — | `1` active les extensions de prompt micro-cap |
| `DEBATE_SUMMARIZER_ENABLED` | `True` | Interrupteur du résumé de débat (mode observation) |

Les identifiants Telegram du triage vivent dans `.env.triage` (`chmod 600`,
jamais commité).

---

## 3. Plafond de sortie : 16000

**Valeur calibrée**, issue d'un audit de 14 jours sur le déploiement précédent :
sortie maximale observée ≤ 7K jetons, prompt de production ≤ 30K, contexte par
emplacement 48128. 16000 laisse le double de marge sur le pic constaté.

> ⚠️ Ne jamais relever cette valeur sans décision humaine explicite. Une valeur
> de 40000 a circulé dans d'anciennes notes : elle n'a **jamais** correspondu à
> l'état réel du système et a été écartée.

Le plafond est appliqué à **deux étages indépendants** :

1. **LiteLLM** porte le même plafond côté serveur pour chaque modèle ;
2. **le client** (`tradingagents/llm_clients/openai_client.py`, patch du fork) :
   `max_tokens` rejoint `_PASSTHROUGH_KWARGS` et un `setdefault` applique
   `_HK_DEFAULT_MAX_TOKENS` (surchargeable par `HKCONSEILS_MAX_TOKENS`).

Le `setdefault` garantit qu'une valeur explicite de l'appelant reste
prioritaire — l'analyste vision passe par exemple son propre plafond. Le
plafond n'est pas appliqué à la Responses API native d'OpenAI, qui utilise
`max_output_tokens`.

`HKCONSEILS_MAX_TOKENS` n'est **pas** défini dans `.env` : le code et LiteLLM
portent déjà la valeur, une troisième source ne serait qu'une occasion de dérive.

### Vérification du plafond

⚠️ **Vérifier l'attribut du client ne suffit pas.** `langchain-openai` sérialise
`max_tokens` sous le nom **`max_completion_tokens`** dans le corps HTTP. Un test
qui se contente de lire `llm.max_tokens` ne verrait pas une rupture de
sérialisation.

`tests/test_hkconseils_max_tokens.py` couvre les deux niveaux — dont trois tests
qui montent un serveur d'écho local et lisent le corps réellement émis :

```bash
.venv/bin/pytest -q tests/test_hkconseils_max_tokens.py
```

**Contrôle de bout en bout** (client → LiteLLM → moteur), à rejouer après toute
montée de version de LiteLLM, `langchain-openai` ou `llama.cpp` : émettre un
appel avec un plafond volontairement bas et une consigne de réponse longue. La
chaîne l'honore si la réponse revient tronquée — `finish_reason: "length"` et un
nombre de jetons de sortie égal au plafond demandé.

---

## 4. Délai SDK : 1800 s

**Valeur calibrée** sur l'exploitation du déploiement précédent. Les chercheurs
haussier et baissier dépassent le défaut de la bibliothèque sur les gros tickers ;
un cycle hebdomadaire mesure 1013 à 2317 secondes par valeur pour une quinzaine
d'appels enchaînés.

Ce délai **n'a aucun chemin de configuration en amont**. `timeout` figure bien
dans `_PASSTHROUGH_KWARGS`, mais ce filtre ne retient que ce que le graphe
transmet — et ni `DEFAULT_CONFIG` ni `trading_graph._get_provider_kwargs()` ne
portent la clé. Sans le défaut posé par le fork, le délai retombe donc sur celui
du SDK `openai` (**lecture 600 s**), en deçà de la valeur calibrée.

Le fork pose ce défaut dans `openai_client.py`, au même endroit que le plafond :
`_HK_DEFAULT_SDK_TIMEOUT`, surchargeable par `HKCONSEILS_SDK_TIMEOUT`. Le
`setdefault` préserve la priorité de l'appelant. Contrairement au plafond, le
délai s'applique **aussi** sous Responses API : c'est un réglage de transport,
pas de génération.

### Vérification du délai

⚠️ Même précaution que pour le plafond : lire l'attribut du client ne suffit pas.
La valeur doit atteindre le **client HTTP réel** et gouverner la coupure.

`tests/test_hkconseils_sdk_timeout.py` couvre les deux niveaux — le délai porté
par l'objet `httpx` effectivement utilisé, et une preuve comportementale contre
un serveur volontairement muet, qui vérifie que la coupure suit le délai demandé
et non le défaut de la bibliothèque :

```bash
.venv/bin/pytest -q tests/test_hkconseils_sdk_timeout.py
```

À rejouer après toute montée de `openai`, `langchain-openai` ou `httpx` : ces
bibliothèques peuvent renommer le champ ou changer la façon dont le délai est
propagé au transport.

---

## 5. Dépendances hors `pyproject.toml`

Trois paquets sont requis par la couche locale mais **absents** de
`pyproject.toml` — délibérément : les fichiers qui les utilisent ne sont pas
commités sur ce dépôt public, et le `pyproject` l'est.

| Paquet | Version | Requis par |
|---|---|---|
| `fastapi` | 0.135.3 | `serve_results.py` (service de consultation des rapports) |
| `uvicorn` | 0.44.0 | idem |
| `Telethon` | 1.43.0 | `signal_triage.py` (import paresseux) |

Après recréation de l'environnement :

```bash
.venv/bin/pip install "fastapi==0.135.3" "uvicorn==0.44.0" "Telethon==1.43.0"
```

`rank-bm25` n'est **pas** réinstallé : la mémoire BM25 a été retirée en amont
(v0.2.4) au profit d'un journal de décisions persistant, et la couche locale ne
l'utilise pas.

---

## 6. Fournisseurs de données

La chaîne par défaut de v0.3.1 est conservée : `yfinance` pour les cours, les
indicateurs, les fondamentaux et les actualités ; `fred` pour le macro ;
`polymarket` pour les marchés de prédiction.

**Alpha Vantage n'est pas activé** — aucune clé n'est configurée, et le fork ne
l'a jamais utilisé. Le correctif amont de filtrage prospectif sur Alpha Vantage
(#1115) ne concerne donc pas ce déploiement. Aucune trace de Finnhub non plus.

Depuis la v0.3.0, la liste configurée est la chaîne de résolution **exacte** :
aucun repli silencieux vers un fournisseur non choisi.

---

## 7. Patchs du fork sur le package

Surface volontairement réduite. Trois patchs de l'ancien fork ont été
**abandonnés**, leur objet étant désormais couvert en amont :

| Ancien patch | Pourquoi il disparaît |
|---|---|
| Désactivation de la Responses API sur `base_url` personnalisée | Natif en v0.3.1 : `_is_native_openai_base_url()` (#1024) |
| Surcharge de `base_url` par modèle | Sans objet : LiteLLM route par modèle |

Le patch de **délai SDK est en revanche conservé** (§4) : contrairement à ce
qu'on pouvait supposer, v0.3.1 n'offre aucun chemin de configuration pour ce
réglage.

Patchs conservés : le plafond de sortie (§3), le délai SDK (§4), l'analyste
vision et le résumeur de débat (voir le rapport de portage).
