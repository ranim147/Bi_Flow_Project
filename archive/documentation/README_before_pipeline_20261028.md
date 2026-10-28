# BIFlow BI Analyst (prototype)

## Correction des échecs métier — 28 octobre 2026

Le routage distingue maintenant explicitement intention, KPI, dimensions, filtres,
périodes, opérations successives et demandes non démontrables. Python verrouille le
périmètre après classement; Granite ne peut changer ni la médiane demandée ni cet
arrondissement. Une vérification du **rapport final**, également après correction ou
fallback, bloque les résultats hors sujet. Les dates demandées ne sont plus remplacées
silencieusement, et modifier la question dans Streamlit efface le résultat précédent.

Relances réelles avec **`granite3.3:2b` via Ollama**, sans correction ni fallback :

| Question | Résultat vérifié par Python | Statut / décision | Durée |
|---|---|---|---:|
| A — moyenne août/septembre 2026 | 4,555008 → 6,175843 jours, **+1,620835**; contributions globales réconciliées, petits groupes exclus des priorités | `validated` / `answer` | 104,68 s |
| B — médiane maximale puis drill-down | **Staten Island**, médiane **0,332269 jour**, n=386; catégories, agences et segments limités à cet arrondissement, sans comparaison temporelle | `validated` / `answer` | 59,23 s |
| 7 — preuve de sous-effectif et recrutement | Refus explicite des deux conclusions; six familles de données manquantes et protocole d'investigation | `validated` / `insufficient_data` | 67,93 s |

[Diagnostic prouvé, corrections, chiffres et liens vers chaque rapport/audit](BI_ANALYST_BUSINESS_FIXES.md).
[Résultats réels complets](outputs/business_intent/granite_20261028/summary.json).
[Vérification Streamlit sur les trois rapports réels](outputs/business_intent/granite_20261028/streamlit_verification.json).
[Suite complète de tests](outputs/business_intent/test_suite/summary.json).

**156 tests réussis, zéro échec, zéro erreur, aucun test ignoré** lors du dernier
passage complet. Les benchmarks historiques et les notebooks contrôlés sont inchangés.

```powershell
.\.venv\Scripts\python.exe tools\run_agent_tests.py
.\.venv\Scripts\python.exe -m bi_analyst.business_evaluation --root D:\Analyst_Agent --output outputs\business_intent\nouvelle_campagne
```

Les sections suivantes décrivent aussi les phases précédentes, dont les archives sont
conservées. La branche historique d'investigation temporelle reste disponible pour
les questions compatibles; elle ne prend plus la priorité sur une demande de médiane,
un classement imbriqué ou une interdiction temporelle.

## Démonstration pour la soutenance

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\demo\launch_demo.ps1
```

Ouvrir `http://127.0.0.1:8501`. Le mode initial est une **restitution enregistrée**, sans
appel à Ollama; le mode direct se choisit explicitement. Les quatre scénarios préparés
montrent comparaison réelle, drill-down réel, historique insuffisant et rejet d'une réponse
volontairement injectée. Cette injection n'est jamais présentée comme une sortie Granite.

Voir [le dossier de démonstration](demo/README.md) pour le lancement, les questions et le
déroulé; [BI_ANALYST_DEMO_READINESS.md](BI_ANALYST_DEMO_READINESS.md) présente la dernière
évaluation, les preuves de vérification et les limites. Le rapport de la phase d'investigation
précédente est conservé dans `BI_ANALYST_FINAL_REPORT.md`.

Agent BI Analyst pour le dataset préparé NYC 311. Le modèle retenu est exclusivement
`granite3.3:2b`, exécuté localement par Ollama. Python sélectionne les outils, calcule les
KPI, détermine les constats et reconstruit les preuves. Pour une investigation, Granite
assemble des hypothèses vérifiables, des contrôles, des actions conditionnelles et leurs
critères de confirmation ou de réfutation. Python lie chaque opération au périmètre réel.
Chaque réponse passe un contrôle Python indépendant avant publication dans le rapport.

Cette phase inclut uniquement une validation automatique. Aucune note humaine n'est
fabriquée, aucun `human_review.csv` n'est créé ou complété. Voir
[BI_ANALYST_FINAL_REPORT.md](BI_ANALYST_FINAL_REPORT.md) pour les résultats et limites.

## Aide à la décision et investigation

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.cli --root D:\Analyst_Agent --question "Investiguer la hausse des délais à Brooklyn et préciser les segments, agences et périodes à examiner."
```

`analyze(question, context)` conserve son interface. Les questions de dégradation,
d'hypothèses, de contribution, de backlog ou de comparabilité des agences activent
`investigate_degradation`; les anciennes questions descriptives conservent leur voie v1.
Les nouveaux audits se trouvent dans `outputs\bi_analyst_decision_runs\<execution_id>\`.

L'outil compare les deux derniers mois complets contigus déclarés par les métadonnées,
sur les **dossiers clôturés dans chaque mois**. Il calcule moyenne, médiane, effectifs,
exclusions et volumes, puis décompose exactement la variation de la **moyenne** entre :

- changement des délais à l'intérieur des segments catégorie/agence;
- changement des poids de ces segments dans les clôtures observées;
- groupes entrants ou sortants, sans moyenne manquante inventée.

Les décompositions marginales par catégorie et par agence sont aussi conservées; ce sont
des partitions alternatives à ne pas additionner. Le backlog et son ancienneté sont
reconstitués aux fins de périodes uniquement pour les dossiers de l'extrait. Les arrivées
ultérieures sont exclues. Ce stock n'est pas celui de toute la ville et ne permet pas de
déduire des besoins en personnel.

Le classement des pistes combine contribution positive, volume valide et complétude.
Les faibles effectifs restent dans l'identité comptable mais sont exclus du classement
opérationnel. Un gros contributeur peu observé devient une cible de collecte de données.
Les comparaisons brutes entre agences ne sont pas interprétées comme des écarts de performance
quand leurs catégories diffèrent. Aucune contribution n'est assimilée à une cause.

La sortie v2 ajoute `decision_support`, avec les huit champs métier demandés : `problem`,
`priority_scope`, `evidence_ids`, `investigation_hypotheses`, `investigation_steps`,
`recommended_actions`, `expected_verification`, `limitations`. Chaque hypothèse est marquée
`unverified` et précise ses données supplémentaires. Les étapes contiennent les filtres,
les périodes, les champs et la comparaison à effectuer. Chaque action indique une priorité
justifiée et les décisions envisageables selon les résultats de la vérification.

Granite choisit et ordonne des opérations dans un catalogue lié aux preuves. Les codes
sont développés en plans contextualisés par Python. Ce choix permet de bloquer les chiffres
libres, les périmètres inventés, les causes affirmées et les actions sans preuve. Ce n'est
pas un raisonnement libre sans restriction. Le prompt utilise les preuves Python structurées;
les valeurs ne sont jamais recopiées dans la sortie LLM.

Le schéma JSON est contraint par contexte puis validé indépendamment : références exactes,
limites complètes, hypothèses admissibles, correspondance entre hypothèse, étape, action et
vérification. Une correction est autorisée par problème, puis un fallback Python est indiqué
dans `origin`. Paramètres : température 0, seed 42, contexte 4096, maximum 640 tokens et
`keep_alive=10m`. Au plus deux périmètres sont développés par requête générale pour borner
le coût local; l'ensemble des comparaisons reste dans `analysis.json`.

Évaluation avec Granite, comprenant trois questions réelles et huit cas synthétiques
explicitement séparés :

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.decision_evaluation --root D:\Analyst_Agent --output outputs\decision_support\ma_nouvelle_campagne
```

Le dossier de sortie doit être nouveau. `--real-only`, `--synthetic-only` et `--baseline`
permettent de limiter la campagne. Une erreur sur les données réelles ne déclenche jamais
de simulation. L'API de test `analyze_synthetic_decision` exige explicitement une fixture
et produit des rapports et preuves marqués `synthetic`.

Les résultats de livraison sont dans `outputs\decision_support\granite_investigation_20261028\`.
Les contrôles automatiques recalculent les preuves et reconstruisent les plans à partir
des réponses enregistrées; les taux réels et synthétiques ne sont pas mélangés.

Campagne principale : trois réponses réelles validées sans fallback; sept des huit cas
synthétiques validés sans fallback. Le cas restant a déclenché le repli fiable. Après
resserrement de la cardinalité des listes, un retest séparé de ce cas passe dès la
première réponse en conservant `insufficient_data`. Les métriques initiales sont préservées.
Voir le rapport final pour les temps et les résultats détaillés.

Recalculer une campagne enregistrée sans aucun appel Ollama :

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.decision_evaluation --root D:\Analyst_Agent --output outputs\decision_support\granite_investigation_20261028 --verify-only
```

Un ou plusieurs `--scenario <identifiant>` permettent une contre-épreuve ciblée dans un
nouveau dossier, par exemple `--synthetic-only --scenario insufficient_recommendation`.

Schémas : [rapport v2](schemas/BIAnalysisReport.v2.schema.json),
[projection v1](schemas/BIAnalysisReport.v1.schema.json),
[brouillon d'investigation](schemas/DecisionDraft.v1.schema.json).
La projection `report.to_legacy_dict()` conserve les champs v1 pour un consommateur strict;
elle refuse de présenter un rapport synthétique comme un rapport réel. Les anciennes
réponses v1 n'ajoutent aucun champ `decision_support` à leur sérialisation.

## Environnement Windows / VS Code

Dans le terminal PowerShell intégré à VS Code, depuis `D:\Analyst_Agent` :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-agent.txt
```

Ouvrir les notebooks avec le noyau du projet (`D:\Analyst_Agent\.venv\Scripts\python.exe`).
Les Notebooks 1 et 3 restent inchangés. Le Notebook 2 importe les fonctions statistiques
partagées de `bi_analyst.analytics`.

Vérifier le serveur et le modèle avant une inférence :

```powershell
& '.\tools\ollama\ollama.exe' list
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version'
```

Si nécessaire, démarrer le serveur Ollama dans un terminal PowerShell dédié :

```powershell
& '.\tools\ollama\ollama.exe' serve
```

Installer le modèle explicitement seulement s'il manque :

```powershell
& '.\tools\ollama\ollama.exe' pull granite3.3:2b
```

Le client vérifie que `granite3.3:2b` est installé avant d'analyser. Il n'utilise ni API
distante ni autre modèle. Les paramètres reprennent le protocole local audité : température
0, seed 42, contexte 4096, maximum 384 tokens pour la voie descriptive, `top_k=40`, `top_p=1`, répétition 1 et
`keep_alive=10m`. Le timeout d'inférence est de 180 secondes par appel, au maximum deux
appels par problème. CPU est accepté si Ollama
n'utilise pas le GPU; une erreur mémoire ou un timeout est journalisé explicitement.

Sur la voie descriptive historique, le contrat interne `granite-draft-v2` contient `finding`, `evidence_ids`,
`insight`, `recommendation`, `limitation_codes`. Les identifiants exacts et les formulations
admissibles sont contraints par un JSON Schema dynamique transmis à Ollama. Le prompt
court contient deux exemples JSON valides pour le contexte courant. Granite ne recopie
ni valeur, ni source, ni effectif. Il ne peut pas modifier le constat calculé par Python.

Le validateur vérifie également le JSON strict, la couverture et l'unicité des preuves,
les limites requises, les chiffres en prose, les causes supposées, les recommandations et
le vocabulaire autorisé. Une prose hors catalogue est rejetée : il ne s'agit pas d'un
validateur sémantique universel. Le rapport final associe l'interprétation Granite validée
à une description quantitative calculée par Python. Les nombres de cette description
proviennent exclusivement des faits vérifiés.

Une seule correction est autorisée, à partir des erreurs et du contrat; la longue réponse
erronée n'est pas réinjectée. Un second rejet, un timeout ou une erreur mémoire produit
un fallback Python explicite. Une API indisponible ou un modèle absent au précontrôle
produit `failed/refuse`. Aucun autre modèle n'est utilisé et aucun téléchargement n'est
automatique. Le schéma structuré a été vérifié avec Ollama `0.40.11`.

## Démonstration

Avec le serveur Ollama actif et Granite disponible :

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.cli --root D:\Analyst_Agent --question "Compare les délais de résolution par arrondissement."
```

La commande lance une vérification Ollama, valide les fichiers d'entrée réels, sélectionne
les outils autorisés, puis imprime le rapport Pydantic. Les journaux JSONL, le rapport
final, les réponses initiale/corrigée et les traces sont créés sous
`outputs\bi_analyst_runs\<execution_id>\`. Une sortie invalide n'est jamais retournée comme
réponse validée : une seule correction est permise, puis l'agent utilise un repli Python
déterministe avec le statut `fallback`.

Pour exécuter les mêmes calculs sans LLM :

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.cli --root D:\Analyst_Agent --baseline
```

## Tests

Tests métier et contrôles d'investigation, sans inférence :

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_decision_support.py" -v
```

Tests hors ligne (ils ne font aucune inférence) :

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_bi_analyst.py" -v
```

Suite existante et nouveaux tests :

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Comparer le baseline Python avec Granite sur les six scénarios NYC 311 :

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.evaluation --root D:\Analyst_Agent
```

La commande d'évaluation lance une inférence par scénario; le temps dépend du CPU/GPU et
peut être élevé. Pour vérifier d'abord la baseline seule, sans appel au modèle :

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.evaluation --root D:\Analyst_Agent --baseline-only
```

Chaque campagne conserve `metrics.json`, `progress.json`, les rapports appariés et leurs
audits. Un répertoire de campagne existant n'est jamais écrasé. Les cas synthétiques
des tests servent uniquement à éprouver les règles; ils ne sont pas mélangés aux résultats
réels. En mode `--baseline-only`, les métriques de comparaison avec le LLM restent à `null`;
seules les mesures du baseline Python sont rapportées.

Les résultats réels de la version descriptive précédente sont conservés sous
`outputs\bi_analyst_finalization\granite_v2_release_20261028\` et décrits dans le rapport
final. Les anciennes campagnes restent inchangées. Les audits conservent requête, schéma,
réponses initiale/corrigée, contrôles, éventuel fallback, preuves, rapport et temps par étape.
Les durées de chargement, évaluation du prompt et génération Ollama sont converties de
nanosecondes en millisecondes dans `timing_ms`. La reproductibilité dépend aussi de la
version du serveur, du digest du modèle et des ressources de la machine.

Rejouer les calculs et les contrôles des rapports enregistrés, sans inférence :

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.verification --root D:\Analyst_Agent --campaign outputs\bi_analyst_finalization\granite_v2_release_20261028 --previous outputs\bi_analyst_evaluations\fd44245e94c34707a59d2d3e7e152db6 --failed-run outputs\bi_analyst_runs\58dbda6a956a4c3d8490070a3bedb61b
```

`verification.json` compare les faits recalculés, les références, les dénominateurs, les
constats et le texte matérialisé. Les métriques attendues doivent toutes être présentes;
un JSON valide sans les faits demandés ne suffit pas.

## Contrat et limites

Utilisation Python :

```python
from pathlib import Path
from bi_analyst import AnalysisContext, analyze

report = analyze(
    "Compare les délais de résolution par arrondissement.",
    AnalysisContext(project_root=Path(r"D:\Analyst_Agent")),
)
payload = report.model_dump(mode="json")
```

`analyze(question, context) -> BIAnalysisReport` est l'interface destinée à l'orchestrateur,
au Dashboard Generator et au BI Auditor/XAI. Le rapport inclut l'identifiant d'exécution,
les preuves (KPI, groupe, unité, effectif, source et empreinte), les outils choisis,
les statuts de validation, les limites et les erreurs. Les réponses descriptives gardent
`biflow-bi-analyst-v1`; les investigations utilisent `biflow-bi-analyst-v2`, avec projection
v1 explicite. Voir `bi_analyst\models.py` et `schemas\`. Un consommateur doit lire `status`,
`decision` et `validation` : un fallback contient des faits fiables tout en conservant
les raisons du rejet LLM. Un rapport `validated/insufficient_data` est une réponse prudente
validée, sans conclusion temporelle. Les branchements effectifs des autres agents ne font
pas partie de cette livraison; la compatibilité vérifiée porte sur le contrat JSON.

Les analyses utilisent les CSV validés de `data\processed\` et les sorties statistiques du
projet. Une source manquante ou incohérente provoque un échec explicite; aucune simulation
ne remplace un fichier absent. Les tendances excluent octobre 2026, déclaré partiel. Les
anomalies requièrent six périodes antérieures complètes (au moins sept périodes contiguës
pour tester la plus récente); une série avec un trou ou une période répétée est refusée sans
imputation. Les méthodes Z-score (seuil absolu 3,0) et MAD (seuil robuste 3,5) sont
documentées dans `bi_analyst\analytics.py`; un MAD nul ne déclenche pas d'anomalie. Une
tendance mensuelle statistiquement significative est recherchée avec le test saisonnier de
Mann–Kendall (comparaison des mêmes mois calendaires, approximation normale corrigée des
ex æquo, alpha 0,05) sur au moins 36 mois complets consécutifs, soit trois cycles annuels.
Une série plus courte ou discontinue ne permet pas de conclure; les variations mensuelles
restent descriptives. Les données actuelles ne
permettent ni ce test de tendance ni un test d'anomalie mensuelle. Le test ne corrige pas
l'autocorrélation temporelle; les p-values doivent être interprétées avec prudence. Les
délais de groupe sous 30
observations valides sont exclus des classements. Les demandes non clôturées sont distinguées
des clôtures sans durée valide. Les comparaisons sont descriptives, l'échantillon est non
aléatoire, aucune causalité n'est établie et aucun SLA n'est configuré.
