# Correction des échecs métier BIFlow — 28 octobre 2026

Les trois questions exactes ont été réexécutées avec **Granite3.3 2B et Ollama local**,
sur les entrées réelles validées. Les trois réponses passent les contrôles métier,
sans correction LLM et sans fallback. Le troisième résultat est un refus de conclure
avec `decision=insufficient_data`, et non une preuve de sous-effectif.

Les résultats reproductibles sont dans
[summary.json](outputs/business_intent/granite_20261028/summary.json), avec les réponses
brutes dans les audits et les fichiers `attempt_1.txt`. Aucun résultat de benchmark
historique ni notebook n'a été reconstruit. Les empreintes avant/après des archives
et notebooks contrôlés concordent (`historical_files_unchanged=true`).

## Causes démontrées

| Problème | Preuve dans le code et les archives | Correction |
|---|---|---|
| B analysait des moyennes temporelles | `decision_question()` dans `decision_tools.py` acceptait isolément `investig`, `drill` ou `agence`. `orchestrator.analyze()` envoyait alors la question entière à `analyze_decision()`. `build_decision_analysis()` choisissait systématiquement les deux derniers mois complets puis décomposait une moyenne. Le rapport `c79fac3eaecd416aa7dc8665341e968d` conserve bien la question exacte sur les médianes, mais publie `within_segment_increase`, Brooklyn et Bronx. | `routing.py` produit un `AnalyticalPlan` explicite avant sélection des outils. Pour B : médiane, classement → verrouillage → comparaisons limitées → priorités. L'exclusion temporelle est contraignante. Les accès directs aux outils et à la branche d'investigation respectent aussi ce routage. |
| Le test 7 devenait une analyse de qualité | La règle `any(word in text for word in ("manqu", ...))` de `tools.select_tools()` trouvait `manqu` dans **manque de personnel** et retournait seulement `data_quality`. L'archive `8c5064741fe54340a1654f12fd3c1ee6` montre cet outil, `decision=answer` et une recommandation de complétude. Aucun détecteur de demande de preuve causale ou de recrutement n'intervenait avant cette règle. | Détection prioritaire des demandes non démontrables et outil `check_capabilities`. Vérification des champs nécessaires dans les données validées. Refus explicite de prouver la cause et de calculer un recrutement; liste des six familles de données nécessaires et protocole de vérification. |
| Un JSON cohérent passait même hors sujet | `validate_plan()` contrôlait les codes, les références et les limites du plan de comparaison temporelle déjà choisi. `validate_draft()` contrôlait le langage et les preuves autorisés. Aucun de ces contrats ne confrontait ce choix initial au KPI, à la séquence et à l'interdiction temporelle de B. | `validate_scoped_report()` recalcule le plan à partir de la question exacte, puis vérifie le rapport final : chiffres, empreintes, sources, effectifs, dimensions, filtres, périodes, outils, décision, résultats requis, texte et rattachement des preuves. Une réponse vraie issue d'une autre question est rejetée. Même contrôle après réparation et fallback. Les anciennes branches ont aussi un contrôle final avant publication. |
| A n'établissait pas la contribution globale | L'ancien contrôleur classait d'abord les arrondissements par `delta_mean_days * sqrt(min(n))`, puis décomposait leurs moyennes séparément. Ces contributions ont pour dénominateur les clôtures de chaque arrondissement et ne constituent pas un classement des contributions à la moyenne globale. L'ancien contrôleur pouvait aussi sélectionner un gros contributeur non éligible comme cible de collecte. | Comparaison globale préalable, puis décomposition jointe arrondissement × plainte × agence avec les mêmes dénominateurs globaux. Classement opérationnel uniquement parmi les contributions positives avec au moins 30 durées valides dans **chaque** mois. Tous les petits groupes restent dans l'identité arithmétique et dans `analysis.json`. |
| Les périodes demandées pouvaient être ignorées | `build_decision_analysis()` utilisait `select_comparison_periods(metadata)` sans interpréter les dates de la question. Août/septembre correspondait par coïncidence aux deux derniers mois complets de cet extrait. | Lecture des mois français et ISO, propagation de l'année commune, contrôle des bornes et des mois partiels. Une période absente, partielle, inversée ou sans année conduit à une limite explicite, sans substitution. Quand aucun mois n'est nommé pour une comparaison mensuelle, le choix des deux derniers mois complets est consigné. |
| Risque d'association visuelle à une autre question | Streamlit conservait `active_run` après modification du texte, jusqu'au prochain clic. En revanche, la question exacte était bien transmise au service et un nouvel UUID était créé à chaque appel. Aucun cache de plan analytique ni mémo partagé entre requêtes n'a été trouvé dans cette voie. | Effacement du résultat dès modification de la question. `run_live()` contrôle l'égalité question soumise / question du rapport / événement de réception / identifiant du dossier. Trois rapports réels ont été restitués successivement dans AppTest avec contrôle de leurs IDs et de leurs textes. |

**Correction de la référence A :** l'exécution `83d99e0967cb46e2aa335f9ba358ea39`
contient une question sur l'arrondissement à médiane maximale, et non la question A.
La véritable exécution A retrouvée est
[`965531267c3e44708d99d25a6dfaae63`](outputs/demo_live/965531267c3e44708d99d25a6dfaae63/report.json),
avec une durée de 124,734778 s. Ces archives n'ont pas été modifiées.

## Architecture conservée et contrôles ajoutés

- `models.py` : contrat `AnalyticalPlan`, ajouté à la sortie v2. La projection explicite v1 reste disponible; les anciens rapports restent lisibles.
- `routing.py` : intention, KPI, dimensions demandées, filtres, mois, exclusion temporelle, séquence et demandes non prises en charge. Les négations « pas les moyennes » et « sans analyse temporelle » sont interprétées avant routage.
- `scoped_analytics.py` : exécution réelle des étapes, filtres explicites de catégories/agences, classement déterministe, ex æquo annoncés, verrouillage, médianes/effectifs et décomposition globale à partir des fonctions analytiques existantes.
- `scoped.py` : branche intégrée à l'orchestrateur existant, précontrôle Ollama, prompt court avec plan, faits ciblés, IDs autorisés, limites et conclusion attendue. Une seule réparation est permise; le fallback reste déclaré.
- `validation.py`, `verification.py`, `decision.py`, `orchestrator.py` : vérification du rapport matérialisé avant publication. Les faits et formulations sont reconstruits à partir de Python, sans validation d'un LLM par un autre LLM.
- `consumers.py`, schémas v1/v2, service et Streamlit : prise en charge du plan explicite et contrôles d'identité. Les exigences antérieures sur les preuves et les états de validation sont conservées.

Granite reste exclusivement `granite3.3:2b`, température 0, seed 42, contexte 4096,
maximum 384 tokens pour cette nouvelle branche. Il choisit des formulations et
recommandations autorisées dans un catalogue contextuel. Les tableaux numériques et
les conclusions quantitatives sont matérialisés par Python. Il s'agit d'une génération
contrainte, pas d'un raisonnement libre du modèle.

Une référence isolée à « cet arrondissement » sans nom ni classement préalable dans la
même question donne une demande de précision avec `insufficient_data`. Aucun périmètre
d'une précédente question n'est repris implicitement. Un test temporel demandé pour un
KPI non pris en charge donne une limite explicite plutôt qu'une analyse statique de remplacement.

## Résultats réels

| Test | Intention / KPI | Outils effectivement exécutés | Statut / décision | Durée | Correction / fallback |
|---|---|---|---|---:|---|
| A | investigation / moyenne | `filter_scope`, `compare_periods`, `decompose_mean_change` | `validated` / `answer` | 104,678250 s | 0 / non |
| B | classement puis drill-down / médiane | `filter_scope`, `rank_groups`, `lock_scope`, `compare_scoped_groups`, `prioritize_segments` | `validated` / `answer` | 59,230087 s | 0 / non |
| 7 | refus justifié / causalité et recrutement non démontrables | `check_capabilities` | `validated` / `insufficient_data` | 67,931051 s | 0 / non |

Les durées ci-dessus sont les mesures `timing_ms.total` des rapports, arrêtées à leur
matérialisation. Le contrôle final recalculé, la vérification indépendante de campagne
et l'exercice Streamlit s'ajoutent à ces mesures; elles ne représentent pas le temps
total de la campagne de livraison.

### A — hausse globale et contributions

Exécution [`2c4492324c464b609a16bec09cd811d4`](outputs/business_intent/granite_20261028/runs/2c4492324c464b609a16bec09cd811d4/report.json).
[Analyse](outputs/business_intent/granite_20261028/runs/2c4492324c464b609a16bec09cd811d4/analysis.json),
[audit](outputs/business_intent/granite_20261028/runs/2c4492324c464b609a16bec09cd811d4/audit.jsonl),
[réponse Granite brute](outputs/business_intent/granite_20261028/runs/2c4492324c464b609a16bec09cd811d4/attempt_1.txt),
[vérification indépendante](outputs/business_intent/granite_20261028/runs/2c4492324c464b609a16bec09cd811d4/verification.json).

Périmètre : extrait global, dossiers clôturés en août et septembre 2026, durées valides.

| KPI Python | Août | Septembre |
|---|---:|---:|
| Moyenne, jours | 4,555008010 | 6,175842730 |
| Effectif valide | 1 981 | 2 158 |

Hausse : **1,620834720 jour**. Décomposition : évolution interne **+1,320947640**,
composition **−0,193458130**, groupes nouveaux/absents **+0,493345210**.
Résidu numérique : environ **−1,22 × 10⁻¹⁵ jour**.

Le contrôle indépendant calcule pour chaque segment `somme_durées_septembre / n_global_septembre
− somme_durées_août / n_global_août`, puis vérifie l'égalité à sa contribution et la somme globale.

| Priorités éligibles, par contribution globale positive | Contribution, jours | n août / septembre |
|---|---:|---:|
| Brooklyn / UNSANITARY CONDITION / HPD | +0,214093892 | 37 / 37 |
| Queens / Noise - Residential / NYPD | +0,000968849 | 30 / 54 |
| Bronx / Noise - Residential / NYPD | +0,000685350 | 48 / 47 |

Bronx / UNSANITARY CONDITION / HPD contribue descriptivement +0,080280044 jour,
mais avec **24 / 16** durées valides : il reste exclu des priorités opérationnelles.
Queens / Elevator / DOB (+0,111924080, n=1/5) et Brooklyn / APPLIANCE / HPD
(+0,110491241, n=2/9) sont également non classables. Ces groupes figurent dans les
calculs détaillés; la priorité éligible ne représente donc pas tous les contributeurs observés.

Les deux mois sont entièrement couverts selon les bornes des métadonnées et la date
d'extraction du 17 octobre. Cela ne garantit ni exhaustivité des fenêtres ni comparabilité
des cohortes : les dossiers créés avant juin ne sont pas inclus et la sélection des clôtures
peut différer. Les résultats restent descriptifs; octobre partiel n'est pas substitué.

Granite a retenu : « La comparaison temporelle porte sur le KPI et les mois verrouillés
par Python; les écarts restent descriptifs. » Il recommande d'examiner les segments
éligibles et leurs étapes de traitement. Python ajoute les chiffres, les segments et
un protocole sur les horodatages, l'attente, le traitement, la complexité et les exclusions.

### B — maximum des médianes puis périmètre unique

Exécution [`7f77fcf858cc46d1a07297f60537a0b2`](outputs/business_intent/granite_20261028/runs/7f77fcf858cc46d1a07297f60537a0b2/report.json).
[Analyse](outputs/business_intent/granite_20261028/runs/7f77fcf858cc46d1a07297f60537a0b2/analysis.json),
[audit](outputs/business_intent/granite_20261028/runs/7f77fcf858cc46d1a07297f60537a0b2/audit.jsonl),
[réponse Granite brute](outputs/business_intent/granite_20261028/runs/7f77fcf858cc46d1a07297f60537a0b2/attempt_1.txt),
[vérification indépendante](outputs/business_intent/granite_20261028/runs/7f77fcf858cc46d1a07297f60537a0b2/verification.json).

| Arrondissement éligible | Médiane, jours | n valide |
|---|---:|---:|
| **Staten Island** | **0,332268519** | **386** |
| Brooklyn | 0,228483796 | 3 080 |
| Manhattan | 0,170665509 | 1 712 |
| Queens | 0,165943287 | 2 274 |
| Bronx | 0,147436343 | 1 828 |

Le périmètre verrouillé est `{"borough": "STATEN ISLAND"}`. Aucun mois de comparaison
n'est sélectionné. Les preuves de drill-down concernent exclusivement ce périmètre et
`median_resolution_days`, sans période de référence temporelle.

| Catégories éligibles dans Staten Island | Médiane, jours | n |
|---|---:|---:|
| Damaged Tree | 2,052123843 | 44 |
| Illegal Parking | 0,098802083 | 46 |
| Noise - Residential | 0,037905093 | 47 |

| Agences éligibles dans Staten Island | Médiane, jours | n |
|---|---:|---:|
| DOT | 6,523414352 | 37 |
| DPR | 2,052123843 | 56 |
| DSNY | 1,863258102 | 40 |
| DEP | 0,515972222 | 37 |
| NYPD | 0,050555556 | 175 |

Ces comparaisons d'agences sont descriptives : leur composition en catégories diffère.
La priorité conjointe éligible est **Damaged Tree / DPR**, médiane 2,052123843 jours,
n=44. Les deux autres segments présentés sont Illegal Parking / NYPD (n=46) et
Noise - Residential / NYPD (n=47). Les petits groupes, dont HPD (n=19), restent hors
classement; leurs médianes et effectifs sont conservés dans l'analyse détaillée.

Granite a retenu : « Le classement et les comparaisons portent sur le KPI demandé et le
périmètre verrouillé par Python. » Il recommande d'examiner étapes et composition des
dossiers dans les segments éligibles. Python ajoute le maximum exact, les comparaisons
et le protocole circonscrit à Staten Island, avec critères soutenant ou réfutant l'hypothèse.

### 7 — refus explicite de causalité et de dimensionnement

Exécution [`0bfc38e2d76d4a61af3e1407de09b61a`](outputs/business_intent/granite_20261028/runs/0bfc38e2d76d4a61af3e1407de09b61a/report.json).
[Analyse](outputs/business_intent/granite_20261028/runs/0bfc38e2d76d4a61af3e1407de09b61a/analysis.json),
[audit](outputs/business_intent/granite_20261028/runs/0bfc38e2d76d4a61af3e1407de09b61a/audit.jsonl),
[réponse Granite brute](outputs/business_intent/granite_20261028/runs/0bfc38e2d76d4a61af3e1407de09b61a/attempt_1.txt),
[vérification indépendante](outputs/business_intent/granite_20261028/runs/0bfc38e2d76d4a61af3e1407de09b61a/verification.json).

Le contrôleur détecte `unproven_causality` et `staffing_without_capacity_data`.
Les six faits de disponibilité sont faux après inspection du schéma de l'entrée validée.
Le rapport final dit explicitement :

> Je ne peux pas démontrer que les retards résultent d'un manque de personnel : les délais observés ne constituent pas une preuve causale.
>
> Je ne peux pas calculer un nombre d'agents à recruter sans données d'effectifs, de charge et de capacité adaptées.

Il demande les effectifs par agence et période, charge/dossiers assignés, capacité,
temps de traitement et d'attente, complexité, historique des affectations. Le protocole
relie ces sources, contrôle la complexité et les facteurs de confusion, examine des
variations d'effectifs documentées et des groupes témoins, puis envisage une estimation
de capacité sous hypothèses de service. Aucun nombre de recrutements n'est publié.

Granite a retenu : « Les informations nécessaires à la demande centrale sont insuffisantes;
une investigation documentée est nécessaire. » La formulation explicite des deux impossibilités
est liée par Python à la demande détectée et ne peut être remplacée par une recommandation
générique sur la qualité. Le statut technique `validated` signifie ici que ce refus de
conclure a passé les contrôles; `decision` reste **`insufficient_data`**.

## Vérification et reproduction

Les preuves fournies à Granite, leurs IDs autorisés, les plans exacts, les réponses brutes,
les éventuelles corrections et les durées sont conservés dans chaque `audit.jsonl` et dans
[results.json](outputs/business_intent/granite_20261028/results.json). Les rapports conservent
les sources et leurs empreintes. Les oracles pandas recalculent les résultats sans confier
de calcul au modèle.

Le [contrôle Streamlit](outputs/business_intent/granite_20261028/streamlit_verification.json)
restitue les **trois rapports réellement obtenus** dans AppTest : transmission exacte des
questions, effacement après édition, identité du rapport actif et présence de ses textes
et de son ID. Il ne s'agit pas de trois inférences supplémentaires ni d'une inspection
manuelle d'un navigateur. Les trois passages réussissent sans exception UI. Un avertissement
de nettoyage de répertoire temporaire Windows est apparu à la fermeture de ce premier
processus sous bac à sable; il n'affecte pas ces résultats ni les artefacts enregistrés.

Les nouveaux tests couvrent les questions exactes, les huit variantes, les négations,
les filtres, les années, les périodes partielles/absentes/inversées, les opérations
imbriquées, les ex æquo, la frontière n=29/n=30, les refus, les réparations, timeouts,
mauvais modèle, troncatures, fallbacks, la contamination entre questions et les rapports
modifiés après génération. Les anciennes validations sont conservées.

Une ancienne fixture du service simulait un rapport archivé pour une autre question.
Le nouveau contrôle l'a rejetée lors de la première suite complète. Ce rejet est conservé
comme assertion; le cas nominal fournit désormais une question et un rapport identiques.
Les huit tests du service ont ensuite passé leur reprise ciblée. Les journaux du premier
passage restent dans `test_suite/attempt_1_*`.

```powershell
# Tests unitaires et intégration, sans inférence réelle dans la suite
.\.venv\Scripts\python.exe tools\run_agent_tests.py

# Trois inférences réelles; le dossier de sortie doit être nouveau
.\.venv\Scripts\python.exe -m bi_analyst.business_evaluation --root D:\Analyst_Agent --output outputs\business_intent\nouvelle_campagne

# Relecture des trois rapports et réponses Granite sauvegardés, sans appel au modèle
.\.venv\Scripts\python.exe -m bi_analyst.business_evaluation --root D:\Analyst_Agent --output outputs\business_intent\granite_20261028 --verify-only
```

Le journal complet et le résultat chiffré de la suite se trouvent dans
[`outputs/business_intent/test_suite`](outputs/business_intent/test_suite/summary.json).
Dernier passage complet : **156 tests réussis, 0 échec, 0 erreur, 0 ignoré**;
502,766 secondes pour l'exécution unittest (506,702 secondes avec la préparation du
runner). Les 13 nouveaux tests métier et le nouveau test d'effacement du résultat UI
s'ajoutent aux tests existants. Le test de provenance existant vérifie désormais aussi
le rejet explicite d'une question différente de celle du rapport.
Les tests historiques du benchmark utilisent leurs faux clients existants; seul Granite
est appelé pour les nouvelles inférences réelles décrites ici.

La [relecture hors ligne](outputs/business_intent/granite_20261028/replay_verification.json)
effectuée après les corrections passe pour les trois exécutions : identité des questions,
faits recalculés, texte final, réponses Granite sauvegardées, identité du modèle, fin de
génération et empreintes historiques. Elle effectue zéro appel au modèle.

Le routage est déterministe et couvre les formulations françaises testées; ce prototype
n'est pas un interpréteur universel de toutes les questions BI. Les situations non prises
en charge doivent rester des limites explicites. La sélection des fenêtres, les durées
sur clôtures observées, les petits effectifs et l'absence de données de capacité limitent
les conclusions métier, même quand tous les contrôles automatiques passent.
