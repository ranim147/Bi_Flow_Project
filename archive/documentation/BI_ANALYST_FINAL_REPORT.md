# Agent BI Analyst BIFlow — aide à la décision et investigation

Date : 28 octobre 2026. Modèle exclusif : **`granite3.3:2b` via Ollama local**.

## Livraison et périmètre

L'agent dépasse la restitution des KPI : il compare des périodes, localise les segments
contributeurs, distingue changements de délais et de composition, examine le stock ouvert
observé et produit des plans d'investigation vérifiables. `analyze(question, context)`
reste le point d'entrée. Les anciens outils et contrôles sont conservés.

Python établit les constats et les preuves. Granite sélectionne et ordonne des hypothèses
et opérations admissibles selon ces preuves. Python développe ces opérations en filtres,
comparaisons, demandes de données et décisions conditionnelles. La sortie décrit une
investigation à conduire; elle ne prétend pas avoir identifié une cause.

L'agent n'autorise pas un raisonnement textuel sans restriction. Ce choix maintient les
garanties de référence, de périmètre et d'absence de chiffres libres. Le progrès métier
provient des analyses nouvelles et de plans concrets, pas d'un simple changement de prompt.

La campagne de livraison est conservée dans
[`outputs/decision_support/granite_investigation_20261028/`](outputs/decision_support/granite_investigation_20261028/).
Les campagnes antérieures restent intactes. Le rapport et le README précédents sont
archivés sous [`outputs/decision_support/history/`](outputs/decision_support/history/).
Aucune évaluation humaine n'est intégrée et aucun fichier de notation humaine n'est rempli.
Le Dashboard Generator et les autres agents ne sont pas développés dans cette livraison.

## Analyses ajoutées

Les fonctions de `analytics.py` complètent les modules existants :

- `prepare_investigation_frame` vérifie les clés, dates, durées et leur observabilité à la
  date d'extraction. Les clôtures futures ne sont pas utilisées comme des observations
  déjà disponibles. Les durées absentes ou incohérentes ne deviennent jamais des zéros.
- `select_comparison_periods` retient les deux derniers mois complets contigus couverts
  par les métadonnées. Un mois partiel ou une discontinuité ne déclenche aucune substitution.
- `compare_delay_periods` calcule moyenne, médiane, clôtures, durées valides, exclusions et
  variation par arrondissement ou autre partition.
- `decompose_delay_change` décompose exactement la variation de **moyenne** par catégorie,
  agence ou intersection catégorie/agence. Les groupes faibles restent dans l'identité
  comptable, même s'ils ne peuvent pas être classés opérationnellement.
- `backlog_snapshots` reconstitue les dossiers ouverts observés aux fins de périodes,
  leurs anciennetés et les arrivées dans l'échantillon, sans inclure des arrivées futures.
- `agency_comparability` mesure le recouvrement des catégories entre agences et identifie
  les catégories communes suffisamment observées. Les moyennes brutes ne deviennent pas
  un classement d'efficacité des agences.

Le nouvel outil `investigate_degradation`, enregistré dans `tools.py`, orchestre ces
calculs. `decision_tools.py` choisit les périmètres et preuves; `investigation.py` définit
les opérations admissibles; `decision.py` exécute et audite la branche d'investigation.

### Définition des périodes et du KPI

Les délais comparés concernent les **dossiers clôturés dans chaque mois**, sur les dates
UTC du fichier préparé. Il ne s'agit pas des dossiers créés ce mois-là ni d'une cohorte
de demandes suivie avec une maturité identique. Une hausse peut donc aussi accompagner
la clôture de dossiers anciens. Cette limite est explicite dans chaque plan.

L'intervalle retenu sur les données réelles est août / septembre 2026; octobre est partiel.
La date de référence est celle des métadonnées d'extraction, pas la date du poste de travail.
La comparaison de deux périodes est descriptive : elle ne remplace pas le test saisonnier
de tendance ni les conditions historiques plus longues des outils existants.

### Décomposition et contribution

Pour un groupe commun aux deux périodes, de poids `w` parmi les clôtures à durée valide
et de moyenne `m` :

```text
effet interne      = (w_avant + w_après) / 2 × (m_après − m_avant)
effet composition  = (m_avant + m_après) / 2 × (w_après − w_avant)
```

Pour les groupes nouveaux ou sortants, le terme observé `w × m` est conservé séparément.
Aucune moyenne manquante n'est imputée. La somme des effets internes, de composition et
des groupes non appariés doit réconcilier la variation globale à une tolérance numérique
absolue de `1e-9` jour. Un échec de cette identité bloque l'analyse.

Cette identité concerne une **moyenne**, jamais une décomposition additive de médiane.
Les partitions marginales catégorie et agence recouvrent les mêmes dossiers : leurs
contributions ne doivent pas être additionnées à celles de l'intersection catégorie/agence.
Une contribution est une attribution arithmétique descriptive, pas une cause.

### Priorité et solidité des preuves

La priorité des segments éligibles utilise :

```text
max(contribution positive, zéro)
× racine(effectif valide minimal entre périodes)
× complétude minimale des durées entre périodes
```

Ce score sert au tri des investigations; il ne mesure ni un bénéfice économique ni un
besoin d'effectifs. Le seuil d'éligibilité réutilise les trente durées valides par groupe
et période du projet. Les groupes nouveaux, sortants, manquants ou sous ce seuil ne sont
pas classés opérationnellement.

Un gros contributeur peu observé n'est pas masqué au profit d'un petit contributeur mieux
documenté : s'il dépasse le double de la contribution de la cible éligible, il devient une
cible de collecte et de contrôle, avec une limitation explicite. Ce seuil est une règle
de tri du prototype, pas un test statistique ni un SLA.

La requête générale développe les deux périmètres prioritaires selon l'ampleur de la hausse
et les effectifs. Les autres comparaisons demeurent dans `analysis.json`. Une question
nommant un arrondissement conserve ce filtre au lieu de le remplacer par un autre.

## Plans d'investigation produits

Chaque problème retenu contient les huit champs demandés :

| Champ | Contenu contrôlé |
| --- | --- |
| `problem` | Constat compatible avec la comparaison Python, y compris l'absence de hausse |
| `priority_scope` | Arrondissement, catégorie, agence, mois avant/après et base temporelle |
| `evidence_ids` | Références exactes, résolues vers les preuves calculées |
| `investigation_hypotheses` | Hypothèses marquées `unverified`, avec données supplémentaires nécessaires |
| `investigation_steps` | Jeu de données à examiner, filtres, champs et comparaison précise |
| `recommended_actions` | Ordre, justification, action de vérification et décisions conditionnelles |
| `expected_verification` | Observations soutenant ou écartant chaque hypothèse |
| `limitations` | Couverture, effectifs, échantillon, sélection des clôtures et données absentes |

Le catalogue couvre les étapes de traitement, le mix, le backlog, la comparabilité des
agences, la qualité, la collecte d'historique et la vérification d'une prémisse de hausse.
Les opérations ne consistent pas à « améliorer les performances ». Elles demandent par
exemple les journaux d'affectation d'une agence dans une catégorie précise et comparent
les délais d'attente et de traitement entre les périodes, à complexité comparable.

Si un backlog observé augmente, son contrôle devient obligatoire dans le plan. Ce contrôle
utilise les dates d'instantanés, pas un filtre de clôture qui exclurait les dossiers ouverts.
Les effectifs et budgets ne sont pas des leviers automatiquement proposés.

## Contrats, contrôles et compatibilité

La branche descriptive conserve `biflow-bi-analyst-v1`, sans nouveau champ dans sa
sérialisation. La branche d'investigation émet `biflow-bi-analyst-v2`, ajoutant
`decision_support` aux champs historiques. L'interface `analyze()` ne change pas.

Pour un consommateur strict v1, `report.to_legacy_dict()` retire l'extension et conserve
les constats, recommandations, preuves, statuts et références historiques. Les textes de
synthèse de cette projection incluent le périmètre réel et les périodes. La projection
refuse de faire passer un scénario synthétique pour un résultat réel.

Les schémas sont livrés dans [`schemas/`](schemas/) :
`BIAnalysisReport.v1.schema.json`, `BIAnalysisReport.v2.schema.json`,
`DecisionDraft.v1.schema.json`. Le schéma interne est spécialisé dynamiquement par contexte.

Granite reçoit des preuves structurées Python et un registre de vérifications admissibles.
Il retourne des codes d'hypothèses, d'opérations, de limites et des identifiants exacts.
Python reconstruit les textes, filtres, chiffres et sources. Les codes courts réduisent le
coût de génération; ils n'assouplissent aucune contrainte.

Le validateur indépendant vérifie JSON strict, absence de clés supplémentaires ou
dupliquées, schéma contextuel, preuve exacte et complète, périmètre, constat, limites,
hypothèses requises et correspondance entre hypothèse, étape, action et vérification.
Les références inconnues, wildcards, actions génériques, causes affirmées, chiffres libres,
identités de modèle erronées et réponses tronquées sont bloqués.

Une seule correction est autorisée par problème. En cas d'échec, le plan Python porte
`origin=python_fallback` et le rapport `status=fallback`. Le taux de validation ne compte
pas ces fallbacks comme des réponses Granite réussies. Un serveur ou modèle absent au
précontrôle produit un échec explicite; des fichiers réels manquants ne déclenchent jamais
de données synthétiques.

Paramètres d'investigation : température zéro, seed 42, contexte 4096, maximum 640 tokens,
`top_k=40`, `top_p=1`, répétition 1, timeout 180 secondes par appel, `keep_alive=10m`.
Deux périmètres et une correction par périmètre bornent le nombre d'appels. Les temps de
chargement, prompt, génération, validation et analyse Python sont audités séparément.

## Démonstration réelle et résultats

La question demandée a été exécutée de bout en bout. Une hausse de moyenne est effectivement
observée; il n'est donc pas nécessaire d'inventer la prémisse ni de remplacer la démonstration
réelle par une simulation.

À Brooklyn, la moyenne passe d'environ **3,922 à 7,318 jours** pour **700 puis 736 clôtures
valides**. La médiane passe au contraire d'environ **0,297 à 0,220 jour**. Il serait incorrect
de présenter ce constat comme une dégradation générale de tous les délais.

Le segment **BROOKLYN / UNSANITARY CONDITION / HPD** est une cible documentée : **37 clôtures
valides dans chaque période**, moyenne d'environ **10,834 puis 24,288 jours**, contribution
descriptive nette d'environ **0,648 jour** à la variation de la moyenne de Brooklyn.

L'équipe est orientée vers les clôtures d'août et septembre de ce segment, les journaux
d'affectation et d'étapes, puis la complexité et la priorité des dossiers. Un écart persistant
dans la même étape à dossiers comparables soutiendrait cette piste; sa disparition après
contrôle de la composition ou de la complexité l'écarterait. Aucune cause n'est déclarée.

La démonstration générale examine aussi le Bronx. Certains contributeurs y sont trop peu
observés pour une attribution opérationnelle : l'agent demande alors de compléter les
données. Une question distincte sur Manhattan sert à vérifier que l'agent n'invente pas
une hausse lorsque la moyenne observée diminue.

La campagne principale comprend **trois questions réelles et huit cas synthétiques**.
Les onze rapports passent le recalcul automatique des faits, références, périmètres,
étapes, critères de vérification et textes matérialisés. Cela inclut le contrôle du
rapport en fallback; cela ne signifie pas que onze réponses Granite ont été acceptées.

| Données | Rapports vérifiés | Granite validé sans fallback | Corrections | Fallbacks | Temps moyen |
| --- | ---: | ---: | ---: | ---: | ---: |
| Réelles | 3/3 | 3/3, soit 100 % | 1 | 0 | 81,83 s |
| Synthétiques | 8/8 | 7/8, soit 87,5 % | 1 | 1 | 44,67 s |

| Scénario | Nature | Résultat Granite | Temps |
| --- | --- | --- | ---: |
| Démonstration demandée, deux périmètres | Réelle | Validé, réponses initiales | 118,13 s |
| Investigation ciblée Brooklyn | Réelle | Validé, réponse initiale | 44,48 s |
| Prémisse de hausse non confirmée à Manhattan | Réelle | Validé après correction; aucune hausse inventée | 82,88 s |
| Hausse concentrée sur une catégorie | Synthétique | Validé | 47,48 s |
| Hausse expliquée arithmétiquement par le mix | Synthétique | Validé, délais internes stables distingués | 45,68 s |
| Hausse et stock ouvert croissant | Synthétique | Validé, contrôle du backlog inclus | 62,71 s |
| Agences non comparables | Synthétique | Validé, absence de classement brut | 45,97 s |
| Faible effectif | Synthétique | Validé, `insufficient_data` | 27,72 s |
| Historique insuffisant | Synthétique | Validé, `insufficient_data` | 21,65 s |
| Cause non identifiable | Synthétique | Validé, hypothèses non vérifiées | 46,21 s |
| Recommandation sur données insuffisantes | Synthétique | Rejet des deux réponses; fallback Python | 59,94 s |

Les résultats sont disponibles dans
[`metrics.json`](outputs/decision_support/granite_investigation_20261028/metrics.json) et
[`replay_verification.json`](outputs/decision_support/granite_investigation_20261028/replay_verification.json).
Chaque dossier d'audit conserve les requêtes, schémas dynamiques, réponses brutes initiales
et corrigées, contrôles, temps, preuves, analyse complète et rapport final.

### Échec observé et correctif ciblé

Dans le dernier cas synthétique, Granite a dupliqué `investigate_data_quality` dans la
liste d'actions et, initialement, le critère de vérification correspondant. Les deux
réponses ont été rejetées pour non-unicité et incohérence des opérations. Le fallback a
conservé la demande de données supplémentaires et la décision `insufficient_data`.

Le schéma dynamique limite désormais aussi la taille des listes au nombre d'hypothèses
admissibles. Quand une seule hypothèse est autorisée, chaque liste ne peut contenir qu'une
opération. Cette contrainte renforce les contrôles existants; aucun doublon n'est toléré.

Une contre-épreuve ciblée réelle via Granite a validé ce même cas synthétique dès la
première réponse, sans correction ni fallback, en **28,51 s**, toujours avec la décision
`insufficient_data`. Elle est enregistrée séparément dans
[`insufficient_recommendation_retest_20261028/`](outputs/decision_support/insufficient_recommendation_retest_20261028/).
Le taux principal de 87,5 % n'a pas été remplacé par celui de ce retest. Les onze rapports
principaux et le retest ont été revérifiés hors ligne après le correctif.

### Performance et matériel

La génération locale d'un plan reste coûteuse. La démonstration générale développe deux
périmètres et ne doit pas être comparée directement à une sélection de KPI. Le premier
essai utilisait des références et limites longues; les références courtes et registres de
codes réduisent désormais la sortie sans retirer les preuves complètes du rapport.

Les temps ne sont pas une mesure de production garantie : ils dépendent du matériel,
du cache et des autres applications. La configuration reste compatible avec le poste
Windows, environ seize Gio de RAM et une NVIDIA MX350 de deux Gio, avec offload partiel
ou CPU gérés par Ollama. Aucun autre modèle n'a été utilisé pour cette évaluation.

Le premier essai, avant raccourcissement des codes, est conservé dans
[`8d123b4f2fde463780fa5c1749f09e08`](outputs/bi_analyst_decision_runs/8d123b4f2fde463780fa5c1749f09e08/report.json).
Ses plans ont passé les contrôles, mais sa génération était longue. Les chiffres de
livraison proviennent exclusivement de la campagne finale identifiée ci-dessus.

## Tests métier et évaluation automatique

Les huit comportements demandés sont testés avec des fixtures explicitement synthétiques :
hausse concentrée sur une catégorie, effet de composition, hausse avec backlog, agences
non comparables, faible effectif, historique insuffisant, cause non identifiée et
recommandation demandée sur données insuffisantes. Ils ne sont pas présentés comme des
observations NYC 311 réelles.

Les tests contrôlent aussi l'identité exacte de la décomposition, les groupes nouveaux ou
sortants, les périodes trouées, les dates futures, les durées incohérentes, les corrections,
les fallbacks, l'identité du modèle et la compatibilité JSON v1/v2. Une vérification
adversariale altère un chiffre, le périmètre ou une hypothèse causale : le recalcul les rejette.

La suite complète a passé **105 tests**; le journal est conservé dans
[`tests_complete.log`](outputs/decision_support/tests_complete.log).
L'évaluation finale recalcule les preuves depuis les données sources, revalide les réponses
enregistrées et reconstruit les plans. Elle mesure les constats, références, périmètres,
étapes concrètes, hypothèses explicitement non vérifiées, résultats de vérification,
corrections, fallbacks et temps. Les résultats réels et synthétiques ont des dénominateurs
distincts; aucun score humain n'est fabriqué.

## Limites restantes

L'échantillonnage NYC 311 est non aléatoire et temporellement limité. Les volumes, stocks
et contributions calculés concernent l'extrait. Le backlog historique est une reconstruction
sur les dossiers présents, sans historique de réouverture ni instantanés exhaustifs.
Il ne permet pas d'estimer la charge globale des équipes.

Les périodes de clôture peuvent contenir des dossiers créés bien avant; les évolutions
de maturité, complexité, priorité ou politique de clôture doivent être vérifiées avec des
données supplémentaires. Un effet de composition du fichier peut aussi refléter le plan
d'échantillonnage. Aucun résultat ne prouve une causalité.

Le routage des questions reste lexical. Les comparaisons utilisent automatiquement les
deux derniers mois complets contigus; une interrogation arbitraire en langage naturel
sur d'autres dates n'est pas implémentée. Le nombre de périmètres et le catalogue
d'investigations sont bornés. Les opérations proposées constituent un plan à exécuter,
pas des extractions déjà effectuées dans les systèmes métier externes.

La pertinence générale des hypothèses hors de ce catalogue n'est pas évaluée. La réussite
automatique vérifie le contrat et la cohérence avec les données; elle ne constitue pas une
validation humaine ni une intégration réelle aux autres agents.
