# Résumé — Agent BI Analyst BIFlow

**Projet :** BIFlow, prototype d'Agent BI Analyst pour les données NYC 311  
**Date du résumé :** 26 octobre 2026  
**État :** agent implémenté, exécuté sur six scénarios réels et validé structurellement;
qualité sémantique toujours soumise à une revue humaine

## Objectif

Construire un agent BI modulaire qui répond à une question métier à partir de preuves
calculées en Python, puis utilise uniquement le modèle local `granite3.3:2b` pour formuler
une interprétation. Les sorties du modèle ne sont pas considérées comme fiables par défaut :
elles sont validées avant de pouvoir être transmises à un autre agent BIFlow.

## Audit et décisions de conception

- Les fonctions statistiques initiales étaient principalement dans le Notebook 2; certaines
  fonctions de benchmark et de validation existaient déjà dans `benchmark_llm`.
- La logique analytique commune a été extraite dans un package réutilisable, au lieu d'être
  dupliquée dans le notebook.
- Le Notebook 2 a été adapté pour importer les fonctions partagées. Les Notebooks 1 et 3
  n'ont pas été modifiés.
- Les données brutes ne sont pas modifiées. L'agent refuse les sources réelles absentes ou
  incohérentes au lieu de les remplacer par des données synthétiques.
- L'architecture repose sur une orchestration Python explicite, avec registre d'outils
  contrôlé; LangGraph n'a pas été ajouté.

## Diagnostic des premiers échecs et corrections

| Symptôme observé | Cause dans l'ancienne version | Correction apportée |
|---|---|---|
| `delay.borough.*` et identifiants inconnus | Le modèle devait recopier des identifiants libres; le schéma ne limitait pas leurs valeurs au jeu de preuves courant. | Le modèle renvoie des slots (`E1`, `E2`...) définis comme enum JSON dynamique. Python traduit les slots vers les identifiants exacts; doublons et slots inconnus sont rejetés. |
| Chiffres inventés ou moyenne annoncée à la place d'une médiane | Les valeurs détaillées étaient données au modèle et une prose libre lui était demandée; le validateur devait ensuite détecter toute erreur sémantique. | Aucune valeur KPI, effectif, seuil ou source n'est envoyé à Granite. Les scopes descriptifs peuvent contenir un nom de groupe ou une période. Le modèle ne produit plus de texte libre; Python calcule les différences et rédige les constats à partir des preuves sélectionnées. |
| Hypothèses causales formulées comme des explications | Les formulations causales dépassaient les motifs lexicaux du filtre initial. | Le modèle ne peut plus produire de prose causale. Les recommandations sont des codes autorisés rendus par des modèles de texte Python prudents; aucune causalité n'est affirmée. |
| La correction répétait les mêmes défauts | Le prompt de correction réinjectait la réponse invalide et un contexte long, ce qui encourageait sa répétition. | Au plus une réparation transmet uniquement les codes d'erreur, les mêmes slots autorisés et un prompt court. |
| Toutes les réponses utiles finissaient en fallback | La réponse libre était souvent rejetée après génération; le fallback était donc le chemin dominant. | Le nouveau format de sélection a été exécuté sur six scénarios réels : six rapports ont passé la validation structurelle. Le fallback reste réservé aux erreurs de génération ou à une sélection invalide. |
| Environ 224 secondes pour la démonstration initiale | L'audit mesure une réponse de 390 tokens, environ 80 secondes de génération, avec deux appels et un prompt contenant des valeurs et états détaillés. | Sélection à choix fermés, preuve compacte sans valeurs et maximum de 128 tokens. La dernière campagne a mesuré environ 9,0 secondes par scénario en moyenne sur la machine locale. |
| 715 lignes décrites comme « délais manquants » | Le décompte assimilait demandes ouvertes et demandes clôturées sans durée valide. | Les faits distinguent maintenant 709 demandes non clôturées et 6 clôtures sans durée valide; les 9 285 durées valides concernent les demandes clôturées. |
| Une demande générale était refusée faute d'historique d'anomalie | Le statut `skipped` de tout outil d'anomalie rendait l'ensemble de la question insuffisant. | L'insuffisance est maintenant déterminée selon le type d'analyse demandé. Un scan non demandé ne bloque pas une synthèse ou une comparaison disponible. |

## Éléments implémentés

Le package [`bi_analyst/`](bi_analyst/) contient :

- [`models.py`](bi_analyst/models.py) — modèles Pydantic du contexte, des preuves,
  des insights, des validations et du rapport.
- [`analytics.py`](bi_analyst/analytics.py) — chargement des données réelles et fonctions
  de comparaison, de tendances, de qualité des données et de détection d'anomalies.
- [`tools.py`](bi_analyst/tools.py) — sélection contrôlée des outils selon la question et
  génération d'éléments de preuve.
- [`orchestrator.py`](bi_analyst/orchestrator.py) — interface
  `analyze(question, context)`, pré-vérification Ollama, appels au modèle, correction
  limitée, fallback déterministe et journalisation.
- [`validation.py`](bi_analyst/validation.py) — vérifications JSON/Pydantic, identifiants
  de slots et codes autorisés, limites obligatoires et statut de validation. Les chiffres et
  le texte final sont Python, pas du texte généré par le modèle.
- [`cli.py`](bi_analyst/cli.py) — interface en ligne de commande avec ou sans LLM.
- [`evaluation.py`](bi_analyst/evaluation.py) — comparaison appariée sur six scénarios
  réels; les évaluations humaines restent vides jusqu'à leur réalisation.

Des tests ont été ajoutés dans [`tests/test_bi_analyst.py`](tests/test_bi_analyst.py).
Les dépendances du prototype sont listées dans
[`requirements-agent.txt`](requirements-agent.txt). Les commandes d'installation,
d'exécution et de test figurent dans [`README.md`](README.md).

## Règles analytiques et de validation

- Les valeurs numériques sont calculées par Python, pas par le LLM.
- Les groupes de délais comportant moins de **30 observations valides** ne sont pas classés
  opérationnellement; le nombre de groupes exclus est tracé.
- Les anomalies mensuelles exigent six périodes complètes antérieures, donc au moins sept
  mois contigus; une série à périodes manquantes ou dupliquées est refusée sans imputation.
  Z-score utilise un seuil de 3,0, MAD robuste un seuil de 3,5; une dispersion nulle ne
  produit pas de signal. L'historique réel actuel est insuffisant pour ces tests.
- Une tendance mensuelle significative est recherchée par un test saisonnier de
  Mann–Kendall (comparaison des mêmes mois calendaires, approximation normale avec
  correction des ex æquo, alpha 0,05), uniquement sur au moins 36 mois complets
  consécutifs. Le test ne corrige pas l'autocorrélation temporelle, limite signalée dans les
  preuves/limites du rapport. Les périodes courtes ou discontinues ne donnent pas lieu à une
  conclusion de tendance; les variations restent descriptives.
- Les périodes partielles sont exclues. Les demandes ouvertes sont distinguées des clôtures
  sans durée valide; les délais absents ne sont jamais remplacés par zéro.
- Aucun SLA ni lien de causalité n'est inventé.
- Granite reçoit une liste de slots sans valeurs numériques, puis choisit des preuves et un
  code de recommandation contraints par JSON Schema. Une sélection invalide peut faire
  l'objet d'une seule réparation; si elle échoue, le rapport utilise un fallback Python
  explicite et conserve réponses et validations dans les journaux d'audit.
- Le rapport contient un identifiant d'exécution, les preuves et leurs sources, les traces
  d'outils, les limites, le statut de validation et les éventuelles erreurs.

## Vérifications effectuées

- **Suite complète : 74 tests réussis.**
- Les modules Python et les tests passent la compilation `compileall`.
- Les six scénarios ont été exécutés sur les données réelles avec Python et Granite :
  délais par arrondissement, types de plaintes, anomalie mensuelle, comité de pilotage,
  données manquantes et historique insuffisant.
- Ollama local répond et le tag exact `granite3.3:2b` est installé.
- Pylance ne signale pas d'erreur sur les modules BI Analyst modifiés. Les appels à
  `analyze` sont compatibles avec le protocole de client Ollama injecté.

### Résultat de la campagne baseline/Granite

La dernière campagne est disponible dans
[`outputs/bi_analyst_evaluations/fd44245e94c34707a59d2d3e7e152db6/`](outputs/bi_analyst_evaluations/fd44245e94c34707a59d2d3e7e152db6/).
L'extrait réel compte **10 000 lignes**, **9 291 demandes clôturées**, **9 285 durées de
résolution valides**, **6 clôtures sans durée valide** et **709 demandes non clôturées**.

- Scénarios réels : 6; rapports baseline validés : 100 %; rapports Granite validés par le
  contrat : 100 %.
- Faits structurés appariés : 33; discordances numériques : 0; discordances de provenance :
  0; erreurs de sélection de preuve : 0.
- Temps moyen de l'agent : environ **10,04 s**; baseline Python : environ **1,37 s**.
- Refus contrôlés : 2/6 dans les deux modes, correspondant aux demandes temporelles pour
  lesquelles les périodes disponibles ne suffisent pas.
- Exactitude des faits structurés : 100 % sur les faits comparés. Cela mesure la concordance
  avec le baseline Python, pas la pertinence sémantique. Cette dernière reste non mesurée.
- La grille [`human_review.csv`](outputs/bi_analyst_evaluations/fd44245e94c34707a59d2d3e7e152db6/human_review.csv)
  contient six lignes non notées; aucune évaluation humaine n'a été fabriquée.

La campagne mesure la conformité du contrat et l'exactitude des champs numériques liés aux
preuves. Le texte d'insight est construit par Python; les évaluations humaines restent
nécessaires pour juger la pertinence analytique des sélections et des recommandations.
Un exemple de rapport validé est
[`borough_resolution_delays.json`](outputs/bi_analyst_evaluations/fd44245e94c34707a59d2d3e7e152db6/borough_resolution_delays.json);
la réponse brute, les temps et le résultat de validation sont dans
[`audit.jsonl`](outputs/bi_analyst_evaluations/fd44245e94c34707a59d2d3e7e152db6/audit/57bb2b8c8f0a4b06bc46bf1e509998de/audit.jsonl).
L'ancienne démonstration rejetée reste consultable dans
[`outputs/bi_analyst_runs/58dbda6a956a4c3d8490070a3bedb61b/`](outputs/bi_analyst_runs/58dbda6a956a4c3d8490070a3bedb61b/).

## Conclusion et suites recommandées

L'agent est maintenant exécutable sur les données réelles, couvert par les tests et évalué
sur six scénarios appariés. Granite reste un choix provisoire : la validation structurelle
réussie n'est pas une validation humaine de pertinence.

Prochaines étapes suggérées :

1. Faire évaluer les six rapports par des réviseurs humains avec la grille fournie.
2. Répéter l'évaluation après toute évolution des preuves, du routeur ou des règles analytiques.
3. Transmettre aux agents BIFlow le contrat `BIAnalysisReport`; conserver les statuts
   `validated`, `fallback` et `failed` comme états distincts.

## Commandes courantes

Depuis PowerShell, à la racine `D:\Analyst_Agent` :

```powershell
# Installer les dépendances du prototype
.\.venv\Scripts\python.exe -m pip install -r requirements-agent.txt

# Exécuter l'agent avec Granite local
.\.venv\Scripts\python.exe -m bi_analyst.cli --root D:\Analyst_Agent --question "Compare les délais de résolution par arrondissement."

# Exécuter sans LLM
.\.venv\Scripts\python.exe -m bi_analyst.cli --root D:\Analyst_Agent --baseline

# Lancer les six scénarios en baseline seule
.\.venv\Scripts\python.exe -m bi_analyst.evaluation --root D:\Analyst_Agent --baseline-only

# Lancer les tests
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```
