# Runbook de bascule — re-fork v0.3.1

> **Ce document ne s'exécute pas de lui-même.** Il décrit une bascule qui reste une décision
> d'exploitation. Aucune de ses étapes n'a été appliquée : la crontab, les services et le
> déploiement en place sont intacts.

Cible : faire du re-fork (`TradingAgents-v031`, branche `refork-v0.3.1`) le déploiement qui exécute
les analyses planifiées, à la place du déploiement actuel.

---

## 1. Ce que la bascule ne change PAS

À lire en premier : ces éléments sont hors de son périmètre et ne doivent pas bouger.

| Élément | Pourquoi il ne bouge pas |
|---|---|
| **Journal probatoire de production** | C'est une pièce justificative. Le re-fork écrit dans un fichier **distinct** ; les deux ne se mélangent jamais. Ne jamais fusionner, ne jamais purger. |
| **Liste de surveillance** | Les mêmes 18 valeurs, 6 actions et 12 cryptos. La bascule ne change ni son contenu ni la rotation. |
| **Structure W2** | La règle d'évitement du dimanche matin, qui protège le cycle hebdomadaire de la concurrence sur le calculateur, est reprise à l'identique. |
| **Base de suivi des positions** | Ressource partagée avec le tableau de bord. Le re-fork y écrit par le même chemin ; aucune migration. |
| **Déploiement actuel** | Il reste en place, démarrable, jusqu'à décision explicite de retrait. C'est le filet du retour arrière. |

---

## 2. Pré-requis — à vérifier AVANT toute action

Chaque ligne doit être verte. Une seule au rouge annule la bascule.

### 2.1 Configuration du déploiement en place

Deux variables ont été ajoutées à sa configuration lors d'une action antérieure, parce que les
adresses ont quitté le code. **Sans elles, le triage viserait la machine locale.**

```bash
grep -c '^export HKCONSEILS_BRAIN_URL='    ~/tradingagents/.env    # attendu : 1
grep -c '^export HKCONSEILS_BRAIN_URL_V1=' ~/tradingagents/.env    # attendu : 1
```

Si l'une manque : la restaurer depuis `~/backups/prod-env/` **avant** tout `git pull` sur ce
déploiement.

### 2.2 Configuration du re-fork

```bash
cd ~/TradingAgents-v031
for v in OPENAI_COMPATIBLE_API_KEY TRADINGAGENTS_LLM_PROVIDER TRADINGAGENTS_LLM_BACKEND_URL \
         TRADINGAGENTS_DEEP_THINK_LLM TRADINGAGENTS_QUICK_THINK_LLM \
         HKCONSEILS_BRAIN_URL HKCONSEILS_BRAIN_URL_V1 HKCONSEILS_GATEWAY_URL; do
  grep -q "^export $v=" .env && echo "OK   $v" || echo "MANQUE $v"
done
```

### 2.3 État du code et des suites

```bash
cd ~/TradingAgents-v031
git status --porcelain                 # attendu : vide
git rev-parse --abbrev-ref HEAD        # attendu : refork-v0.3.1
.venv/bin/pytest -q | tail -1          # attendu : 0 échec
.venv/bin/python test_soul_enforcer.py    | grep Results   # attendu : 19/19
.venv/bin/python test_enforcer_journal.py | tail -1        # attendu : tous passent
.venv/bin/python harnais_differentiel.py  | grep VERDICT   # attendu : 0 écart
```

### 2.4 Arbitrage préalable

- **Couverture crypto** : 11 des 12 valeurs suivies sont couvertes. `SUI20947-USD` ne l'est pas — la
  source ne connaît pas ce symbole. Trancher : activer l'alias vers `SUI`, retirer la valeur de la
  liste, ou accepter la dégradation en la documentant. **Décision requise avant bascule.**

---

## 3. Période de co-observation — AVANT la bascule

Le principe : faire tourner les deux en parallèle, sans que le re-fork écrive quoi que ce soit
d'engageant, et comparer.

**Durée proposée : 7 jours consécutifs**, dont un dimanche (pour couvrir le cycle hebdomadaire).

**Mise en place** — une entrée de planification *supplémentaire*, qui ne remplace rien :

```
# co-observation, décalée d'une heure pour ne pas concurrencer le cycle en place
30 7 * * *  cd ~/TradingAgents-v031 && .venv/bin/python pipeline_runner.py --mode watch \
            >> data/coobs_$(date +\%Y\%m\%d).log 2>&1
```

> Le décalage n'est pas cosmétique : lancer les deux en même temps ferait entrer les deux
> déploiements en concurrence sur le calculateur et fausserait à la fois les durées et la
> comparaison.

### Critères de comparaison, relevés chaque jour

| Indicateur | Où le lire | Attendu |
|---|---|---|
| Provenance de la décision | `soul_decision_source` du rapport de run | `typed` sur **100 %** des runs |
| Recours à l'analyse textuelle | `soul_decision_counter.text` | **0** |
| Direction de la décision | comparaison avec le déploiement en place, même valeur, même jour | même sens (haussier / neutre / baissier) |
| Violations relevées | journal du re-fork | aucune violation inexpliquée |
| Durée par valeur | `elapsed_seconds` | dans ±30 % du déploiement en place |
| Sentiment crypto | rapport de sentiment | bande et note présentes, sources réelles citées |
| Jetons générés | journal du moteur | aucune génération au plafond |

---

## 4. Critères GO / NO-GO — chiffrés

**GO** si, sur les 7 jours :

1. **100 %** des runs en provenance `typed`, compteur textuel à **0** ;
2. **≥ 90 %** de concordance de direction avec le déploiement en place ;
3. **0** violation inexpliquée, et le refus reste démontrable (rejet provoqué rejoué avant bascule) ;
4. **0** génération atteignant le plafond ;
5. durée médiane par valeur **≤ 1,3×** celle du déploiement en place ;
6. **≥ 11/12** valeurs crypto avec un sentiment alimenté ;
7. **0** écriture inattendue hors du re-fork et de son répertoire de données.

**NO-GO** dès qu'un seul de ces points est manqué. En particulier, **tout** recours à l'analyse
textuelle est un NO-GO : il signifie que l'enforcer a travaillé sur des valeurs reconstruites.

---

## 5. Bascule — séquence

> Fenêtre : hors 05:45-07:15, et hors exécution planifiée en cours. Vérifier :
> `ps -eo cmd | grep "[r]un_analysis\|[p]ipeline_runner"` → aucune ligne.

**Étape 1 — figer un point de retour**

```bash
crontab -l > ~/backups/crontab.pre-bascule.$(date +%Y%m%d-%H%M%S)
cd ~/tradingagents && git rev-parse HEAD > ~/backups/prod-head.pre-bascule
```

**Étape 2 — retirer les entrées de planification en place**

Trois entrées pointent le déploiement actuel (cycle hebdomadaire, relève de signaux, surveillance).
Les **commenter**, ne pas les supprimer : le retour arrière consiste à les décommenter.

**Étape 3 — activer les entrées du re-fork**

Mêmes cadences, mêmes modes, seul le répertoire change : `~/tradingagents` → `~/TradingAgents-v031`.
Retirer l'entrée de co-observation du §3, devenue redondante.

**Étape 4 — services**

Le service de consultation des rapports sert le répertoire de sorties du déploiement en place.
Adapter son répertoire de travail, puis le recharger. Le tableau de bord et l'agent ne sont **pas**
concernés : ils lisent la base de suivi, inchangée.

**Étape 5 — vérification à chaud**

Attendre la première exécution planifiée, puis contrôler : provenance `typed`, compteur textuel à 0,
rapport écrit dans le répertoire du re-fork, journal du re-fork alimenté, journal de production
inchangé.

---

## 6. Retour arrière — une seule séquence, à blanc d'abord

**Objectif : moins de 5 minutes.** À répéter à blanc **avant** la bascule, chronomètre en main.

```bash
# 1. arrêter ce qui tourne
pkill -f "TradingAgents-v031.*pipeline_runner" || true

# 2. restaurer la planification
crontab ~/backups/crontab.pre-bascule.<HORODATAGE>

# 3. rendre le service de consultation à son répertoire d'origine
#    (répertoire de travail précédent, puis rechargement)

# 4. vérifier
crontab -l | grep -c TradingAgents-v031      # attendu : 0
cd ~/tradingagents && git rev-parse HEAD     # attendu : identique à prod-head.pre-bascule
```

Le déploiement en place n'a pas été modifié : il redémarre tel quel. **Le retour arrière ne dépend
d'aucune restauration de données** — c'est ce qui le rend rapide et sûr.

### Répétition à blanc

Sauvegarder la planification, la restaurer immédiatement depuis la sauvegarde, vérifier qu'elle est
identique au point de départ, et chronométrer. Tant que cette répétition n'a pas été faite, la
bascule ne doit pas être engagée.

---

## 7. Après la bascule

- **Ne pas retirer** l'ancien déploiement avant **30 jours** de fonctionnement nominal.
- Journal probatoire de production : **archivé, jamais purgé**.
- Surveiller le compteur de recours textuel : toute valeur non nulle est une anomalie à instruire.
- Le nettoyage de l'historique du dépôt reste une opération distincte, gelée.
