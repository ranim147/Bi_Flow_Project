# BIFlow — finalisation de l'Agent BI Analyst

Date : 28 octobre 2026. Modèle retenu : **`granite3.3:2b` via Ollama local**.

## Résultat et périmètre

Le prototype exécute `analyze(question, context)` de bout en bout sur les données réelles
préparées NYC 311. Python calcule les indicateurs et détermine les constats admissibles.
Granite fournit une interprétation et une recommandation structurées, dans un vocabulaire
métier contrôlé. Python valide la réponse puis construit le rapport et ses preuves.

La livraison concerne ce prototype et ses six scénarios. Elle ne démontre ni la qualité
de toute interprétation libre, ni l'intégration effective des autres agents. L'évaluation
humaine est exclue de cette phase. Aucun score humain n'est utilisé et aucun
`human_review.csv` n'a été complété ou créé par la nouvelle évaluation.

Les résultats de référence de cette livraison sont dans
[`outputs/bi_analyst_finalization/granite_v2_release_20261028/`](outputs/bi_analyst_finalization/granite_v2_release_20261028/).
Les campagnes intermédiaires restent conservées; elles ne remplacent pas cette référence.

## Diagnostic de l'exécution en échec

Fichiers examinés :
[`report.json`](outputs/bi_analyst_runs/58dbda6a956a4c3d8490070a3bedb61b/report.json) et
[`audit.jsonl`](outputs/bi_analyst_runs/58dbda6a956a4c3d8490070a3bedb61b/audit.jsonl).

| Problème | Observation dans l'audit | Traitement livré |
| --- | --- | --- |
| Preuves wildcard ou inventées | `delay.borough.*` apparaît dans les deux réponses | Identifiants exacts dans le schéma; vérification d'appartenance, d'unicité et de couverture en Python |
| Chiffres en prose | Valeurs de délai recopiées dans `insight`, avec risque de mauvaise association | Aucun nombre généré dans la prose LLM; valeurs et effectifs repris exclusivement des faits Python |
| Classement métier erroné | Brooklyn puis Staten Island présentés comme les plus rapides; les preuves montrent le contraire | Constats et extrema déterminés par Python; toute formulation hors vocabulaire vérifié est rejetée |
| Nature de l'indicateur | Le modèle parle de moyenne alors que la preuve contient une médiane | Description quantitative générée par Python avec l'indicateur et l'unité vérifiés |
| Causalité et significativité | Facteurs locaux supposés « influencer » ou « affecter » les délais, différences dites significatives sans test | Détection lexicale indépendante et catalogue contextuel excluant ces affirmations non étayées |
| Limites | Les limites générales sont présentes dans cette réponse; l'exclusion des groupes à faible effectif n'est pas explicitée malgré un groupe non éligible | Liste requise calculée selon le contexte, contrôlée intégralement; preuve du nombre de groupes exclus |
| Correction inefficace | Même contenu invalide répété après le premier rejet | Une seule correction avec codes d'erreur et exemples valides, sans réinjecter la longue réponse fautive |
| Latence | 120,023 s puis 102,706 s d'inférence; total 223,597 s | Contexte réduit, sortie bornée, modèle conservé en mémoire; temps de chaque étape conservés |

Le premier appel ancien chargeait le modèle pendant 27,689 s. Les prompts comptaient
2 377 puis 2 887 tokens, pour 390 puis 381 tokens générés. L'audit rejetait le wildcard
et les chiffres, mais indiquait `causal_claims_clear=true` malgré les formulations
spéculatives. Un schéma JSON structurellement valide ne suffisait donc pas.

Le code présent au début de cette intervention avait déjà introduit un mode plus rapide
de sélection de preuves et corrigé la distinction des demandes ouvertes. Ces modules ont
été réutilisés. Ce mode ne confiait plus d'interprétation rédigée à Granite; la présente
version réintroduit cette sortie, avec des formulations explicitement autorisées.

## Contrat Python / Granite

Le contrat interne `granite-draft-v2` contient exactement :

```json
{
  "finding": "descriptive",
  "evidence_ids": ["delay.borough.STATEN ISLAND", "delay.borough.BRONX"],
  "insight": "La comparaison des groupes est descriptive; la comparabilité des dossiers reste à vérifier.",
  "recommendation": "Vérifier la composition des dossiers et la comparabilité des groupes avant toute action.",
  "limitation_codes": ["closed_only", "no_cause", "no_sla", "nonrandom_sample", "sample_only"]
}
```

Cet exemple illustre la forme du contrat. En exécution, les identifiants et limites sont
ceux du schéma dynamique du scénario; ils doivent tous être présents. Le prompt reçoit
deux exemples complets valides pour ce contexte précis. Les calculs, sources, empreintes,
effectifs et décisions finales restent hors du contrat de génération.

| Responsabilité | Composant |
| --- | --- |
| Chargement des données réelles, vérification des entrées, calcul des KPI | `analytics.py`, modules existants réutilisés |
| Sélection des outils et des faits pertinents | `tools.py`, contrôleur dans `orchestrator.py` |
| Interprétation et recommandation dans le vocabulaire admissible | Granite, contrat de `draft.py` |
| JSON strict, schéma, cohérence métier, preuves et limites | `validate_draft`, indépendant du décodage Ollama |
| Valeurs, sources, effectifs, classement descriptif et rapport final | Python, à partir des preuves originales |
| Recalcul et vérification d'une campagne enregistrée | `verification.py`, sans appel LLM |

Le contrat externe conserve **`biflow-bi-analyst-v1`**. Les champs `BIAnalysisReport`,
`BIInsight`, `EvidenceFact`, `status`, `decision`, `validation`, `source_refs` et
`tool_trace` restent utilisables par l'Orchestrateur, le Dashboard Generator et l'Auditor/XAI.
Les schémas Pydantic exportés figurent dans la campagne de livraison.

Le texte final associe l'interprétation Granite validée à une description quantitative
Python. Les chiffres présents dans cette dernière sont autorisés parce qu'ils sont calculés
et matérialisés par Python. Ils ne sont jamais acceptés dans le brouillon LLM. L'audit
conserve séparément le brouillon et décrit cette matérialisation.

## Validation et fallback

Les contrôles bloquent les JSON invalides, clés dupliquées, nombres non finis, champs
supplémentaires, références inconnues, wildcards, doublons, preuves requises absentes,
constats incompatibles avec Python, limites manquantes ou étrangères au contexte, chiffres
ou nombres écrits en lettres dans la prose, formulations causales et recommandations
non étayées. Une réponse tronquée ou provenant d'un autre modèle est rejetée.

Le catalogue contextuel est une restriction assumée : Granite choisit entre des
interprétations et investigations prudentes compatibles avec les faits. Une phrase hors
catalogue est refusée même si elle pourrait être raisonnable. Ces contrôles ne prétendent
pas comprendre et valider universellement une prose libre. Ils n'ont pas été relâchés
pour augmenter les résultats.

Après un rejet, une seule correction est possible. Si elle échoue, le rapport porte
`status=fallback`, les erreurs du LLM sont conservées et les calculs Python restent
disponibles. Un timeout ou une erreur mémoire provoque également le fallback, sans
répéter une requête coûteuse. Si le serveur est indisponible ou Granite absent au
précontrôle, le rapport porte `failed/refuse`. Aucune donnée simulée ne remplace une
entrée réelle absente ou incohérente.

Les tests provoquent effectivement une correction réussie, deux réponses rejetées,
un timeout initial, un timeout de correction, une réponse tronquée et une identité de
modèle incorrecte. Aucun fallback n'est dissimulé sous le statut `validated`.

## Corrections supplémentaires du contrôleur

- Les questions de synthèse utilisent les outils qualité et KPI; les questions de données
  manquantes utilisent l'outil qualité. Les scans inutiles sont évités.
- Le mot « mensuel » déclenche désormais l'outil temporel. Dans l'ancienne campagne de
  six scénarios, `insufficient_history` n'avait aucune preuve de la métrique temporelle
  attendue, malgré un statut validé.
- Des faits explicites documentent les périodes complètes disponibles et les seuils
  d'historique requis. La condition d'une anomalie est distinguée de celle d'une tendance.
- Une question sur les délais par type de plainte ne transmet plus des volumes sous le
  seul prétexte que le mot « plaintes » apparaît. Les demandes explicites de volume restent
  prises en charge.
- La couverture exige toutes les métriques attendues. Une preuve quelconque ou un JSON
  valide ne suffit plus à déclarer cette couverture complète.

## Validation progressive et résultats réels

La première exécution réelle, avant les campagnes de six cas, est conservée sous
[`807727e5148d41508faa3120a36eba15`](outputs/bi_analyst_runs/807727e5148d41508faa3120a36eba15/report.json).
Elle valide la réponse initiale en 54,660 s, dont 29,575 s de chargement. Une seconde
vérification de ce scénario à chaud, avant la campagne suivante, est conservée sous
[`c7a48a18cce04b65b29b19fc7d35241c`](outputs/bi_analyst_runs/c7a48a18cce04b65b29b19fc7d35241c/report.json)
et prend 15,172 s.

La campagne de livraison valide **6 réponses initiales sur 6**, sans correction, fallback
ni échec. Quatre réponses sont descriptives et deux reconnaissent un historique insuffisant.
Les **35 preuves structurées** comparées au baseline Python ne présentent aucune
discordance numérique ou de provenance. Toutes les métriques attendues sont présentes.
Le recalcul hors ligne confirme les six rapports (`all_scenarios_verified=true`).

| Scénario | Validation initiale | Décision | Temps agent |
| --- | --- | --- | ---: |
| Délais par arrondissement | Validée | `answer` | 18,75 s |
| Délais par type de plainte | Validée | `answer` | 22,50 s |
| Pic anormal mensuel | Validée | `insufficient_data` | 27,93 s |
| Comité de pilotage | Validée | `answer` | 21,99 s |
| Données manquantes | Validée | `answer` | 21,65 s |
| Absence d'anomalie avec cet historique | Validée | `insufficient_data` | 21,49 s |

Les mesures détaillées sont disponibles dans
[`metrics.json`](outputs/bi_analyst_finalization/granite_v2_release_20261028/metrics.json) et
[`verification.json`](outputs/bi_analyst_finalization/granite_v2_release_20261028/verification.json).
Les statuts `insufficient_data` ne sont pas des échecs de génération : la conclusion
prudente a elle-même passé les contrôles. Le champ historique
`agent_refusal_or_controlled_failure_rate` conserve son ancienne définition agrégée;
les nouveaux champs séparent explicitement `insufficient_data_count=2`,
`fallback_count=0`, `failed_count=0` et `llm_fallback_rate=0`.

Les six dossiers de scénario conservent la réponse initiale, sa validation, la correction
éventuelle, son contrôle, le fallback éventuel, les preuves, les temps et les rapports
appariés Python/Granite. Dans la campagne de livraison, les corrections et fallbacks
absents sont enregistrés explicitement par `null` et `false`.

Les résultats métier à retenir sont les suivants :

- Arrondissements : médiane la plus élevée à Staten Island (environ 0,332 jour, 386 délais
  valides), la plus faible dans le Bronx (environ 0,147 jour, 1 828 délais valides).
- Types de plaintes : la médiane descriptive la plus élevée parmi les groupes éligibles
  retenus concerne APPLIANCE (environ 17,245 jours, 52 délais valides). Cela ne démontre
  ni une cause ni un dépassement de SLA.
- Synthèse : 10 000 demandes, 9 285 durées valides, moyenne d'environ 4,107 jours,
  médiane d'environ 0,182 jour, taux de clôture de 92,91 %.
- Qualité : 709 demandes non clôturées et 6 clôtures sans durée valide. Les 715 durées
  indisponibles ne doivent pas être toutes qualifiées de données manquantes sur clôtures.
- Historique : quatre mois complets; le scan d'anomalies exige au moins sept périodes
  contiguës et la tendance saisonnière au moins trente-six. Aucune absence d'anomalie
  n'est donc affirmée sur les données actuelles.

## Performances et comparaison Granite

La campagne Granite précédente
[`fd44245e94c34707a59d2d3e7e152db6`](outputs/bi_analyst_evaluations/fd44245e94c34707a59d2d3e7e152db6/metrics.json)
utilisait le mode de sélection de preuves et de code de recommandation, avec tous les
textes rendus par Python. La comparaison suivante n'utilise aucun score humain.

| Mesure automatique | Ancien sélecteur Granite | Version livrée |
| --- | ---: | ---: |
| Rapports au statut validé | 6/6 | 6/6 |
| Faits structurés comparés | 33 | 35 |
| Discordances numériques / provenance | 0 / 0 | 0 / 0 |
| Scénarios avec toutes les métriques attendues | 5/6, constaté dans les fichiers | 6/6 |
| Réponses `insufficient_data` | 2 | 2 |
| Temps moyen agent | 10,04 s | 22,38 s |
| Temps moyen baseline Python | 1,37 s | 1,35 s |

Le nouveau contrat est **plus lent que le sélecteur antérieur**, car il produit aussi des
champs textuels contraints, tous les identifiants pertinents et les codes de limites.
Le périmètre des contrôles et des preuves est également élargi. Il serait incorrect de
présenter cette comparaison comme un gain de vitesse général.

Par rapport à l'exécution libre en échec expressément signalée, le scénario arrondissement
passe de 223,597 s avec deux réponses rejetées à 18,75 s à chaud avec une réponse validée.
Le gain comprend la suppression de la seconde génération et l'absence de chargement à
froid; il ne mesure donc pas à lui seul l'accélération du modèle. Le premier essai nouveau
à froid prenait 54,660 s. Les durées comparées ne constituent pas un essai matériel apparié.

Paramètres fixés : température 0, seed 42, contexte 4 096, maximum 384 tokens,
`top_k=40`, `top_p=1`, `repeat_penalty=1`, `keep_alive=10m`, timeout 180 secondes par
appel, flux désactivé. Deux appels au maximum sont possibles. La seed ne garantit pas
une identité binaire des résultats entre versions ou matériels différents.

Les durées de précontrôle, chargement des données, inférence, chargement du modèle,
évaluation du prompt, génération et validation sont conservées. Les mesures Ollama en
nanosecondes sont converties en millisecondes sans les additionner au temps mur déjà
mesuré. Le temps `data_validation` ne cumule plus le précontrôle Ollama.
La campagne de livraison utilise 460 à 549 tokens de prompt et génère 156 à 192 tokens
par réponse. Les validations durent environ 1,4 à 2,6 ms; les chargements à chaud
environ 5,6 à 10,7 ms. Les six scénarios réutilisent le même modèle résident.

Le serveur observé est Ollama 0.40.11; le digest Granite est
`07bd1f170855240f9e162bf54ea494a8bc1c73d8cbd1365d7fccbeb7d2504947`.
L'environnement Windows dispose de 15,75 Gio de RAM et d'une NVIDIA GeForce MX350 de
2 048 Mio. L'offload partiel reste géré par Ollama; le GPU n'est pas une condition
d'exécution. L'état observé et la mémoire disponible sont archivés dans `environment.json`.
Les autres applications peuvent modifier les temps; ce travail n'est pas un benchmark
matériel contrôlé. Les campagnes intermédiaires exécutées pendant les tests sont conservées
séparément; la campagne de livraison est exécutée sans suite de tests en parallèle.

L'utilisation d'un JSON Schema dans `format`, de paramètres reproductibles et des durées
retournées suit la documentation officielle Ollama :
[sorties structurées](https://docs.ollama.com/capabilities/structured-outputs) et
[API chat](https://docs.ollama.com/api/chat). La compatibilité est également constatée
par les appels locaux enregistrés, pas seulement déduite de la documentation.

## Tests, contrats et preuves de livraison

La suite complète a exécuté **88 tests avec succès**. Les **14 tests ciblés Granite**
ont également réussi après la réduction finale du contexte pour les délais de plaintes.
Les journaux sont conservés dans
[`tests_release_20261028.log`](outputs/bi_analyst_finalization/tests_release_20261028.log) et
[`tests_granite_release_20261028.log`](outputs/bi_analyst_finalization/tests_granite_release_20261028.log).
Les tests historiques du benchmark utilisent des clients simulés; aucune comparaison
réelle Granite/Qwen/Gemma n'a été relancée.

Le nouveau fichier `tests/test_granite_finalization.py` couvre les contrats stricts,
les erreurs adversariales, les six scénarios d'intégration, les corrections et fallbacks,
le routage, la sérialisation du contrat externe, l'absence de fichier de notation humaine,
le refus d'écraser une campagne, le recalcul de preuves altérées et les chemins relatifs
de la commande de vérification.

`verification.json` recalcule les faits depuis les fichiers réels et vérifie les valeurs,
unités, populations, effectifs, sources, empreintes, méthodes, seuils, décisions, limites,
références et textes finaux. Les deux schémas JSON exportés rendent le contrat examinable
sans exécuter le code. `historical_reference_hashes.json` référence les anciens fichiers
consultés; les campagnes historiques n'ont pas été réécrites.

## Limites connues et statut de finalisation

Le prototype est opérationnel dans le périmètre des six scénarios et du vocabulaire
contrôlé. L'acceptation automatique porte sur le contrat, la cohérence avec Python et la
provenance. Elle n'est ni une validation humaine, ni une mesure de l'utilité métier
générale, ni une autorisation d'action opérationnelle.

Le routage reste lexical et ne couvre pas toutes les formulations de questions.
Le catalogue d'interprétations est volontairement limité. Le rapport contient une synthèse
unique et les preuves nécessaires, pas une analyse libre exhaustive. Un contexte court ou
une génération plafonnée peut conduire au fallback. Un modèle absent exige une intervention
sur l'environnement; aucun autre modèle ne le remplace.

L'extrait préparé est non aléatoire et ne représente pas toute la population new-yorkaise.
Les délais portent sur les clôtures à durée valide; les groupes sous trente observations
ne sont pas classés. Octobre 2026 est partiel. Les variations mensuelles observées restent
descriptives. Le test de tendance saisonnier ne corrige pas l'autocorrélation. Aucune
causalité n'est établie et aucun SLA n'est configuré.

L'intégration réelle du Dashboard Generator, de l'Orchestrateur et de l'Auditor/XAI,
leur déploiement, l'évaluation humaine et le développement des autres agents restent
hors périmètre. La réussite des tests unitaires seule n'a pas servi à conclure : des
inférences Granite réelles et un recalcul hors ligne des rapports sont inclus dans la livraison.
