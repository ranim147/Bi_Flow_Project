# BIFlow — pipeline de données et BI Analyst

Version finale locale du 28 octobre 2026. Projet Windows, Python 3.13, 16 Go de RAM,
stockage de travail sur D:. Modèle exclusif : **Granite3.3 2B via Ollama local**.

Le pipeline exécutable est : acquisition → profiling → ETL → KPI → BI Analyst.
La nouvelle version contient **265,609 demandes**, **259,354 durées valides**
et **45 mois complets**, de janvier 2023 à septembre 2026.
La suite complète a exécuté **170 tests réussis**, sans échec, erreur ou test ignoré.
Six scénarios ont été exécutés avec le vrai Granite puis contrôlés par Python.
Après archivage, 28 tests de régression et l'affichage des six rapports récents
ont été vérifiés à nouveau, sans appel supplémentaire au modèle.

## 1. Architecture et responsabilités

```mermaid
flowchart LR
  A[NYC Open Data / fichiers locaux] --> B[RAW Parquet + provenance]
  B --> C[Profiling + seuils qualité]
  C --> D[ETL + CLEANED + quarantaine]
  D --> E[KPI exacts + catalogue sémantique]
  E --> F[BI Analyst Python + Granite / Ollama]
  F --> G[Validation finale + rapports JSON + Streamlit]
```

| Étape | Technologie | Entrées | Sorties et contrôles |
|---|---|---|---|
| Acquisition | Python, Requests, API SODA2, PyArrow | Période, schéma officiel, paramètres de sélection | Pages Parquet, SHA-256, comptages officiels, checkpoints, reprise par clé |
| Profiling | Pandas par lots, SQLite temporaire | RAW et manifeste | Types, valeurs manquantes, doublons, répartitions, dates, couverture, alertes |
| ETL | Pandas par lots de 5 000, PyArrow, SQLite | RAW + profiling validé | Colonnes normalisées, dates UTC, durées, états, CLEANED et quarantaine traçable |
| KPI | Pandas, NumPy, Parquet | CLEANED validé + agrégats officiels | Moyennes/médianes exactes, taux, distributions, dimensions, mois, backlog et catalogue |
| BI Analyst | Contrôleur Python, Pydantic, JSON Schema, Granite via Ollama | Version préparée et question | Plan, preuves autorisées, formulation contrainte, validation finale, fallback déclaré |
| Démonstration | Streamlit, AppTest | Rapports récents ou nouvelle question | Version des données, question et ID d'exécution cohérents, exports JSON/audits |

Les étapes amont sont des composants déterministes orchestrés par `bi_pipeline.cli`,
pas des agents LLM autonomes. Le BI Analyst possède un contrôleur identifiable :
routage, outils, séquence, périmètre verrouillé, une réparation autorisée puis fallback.
Dashboard Generator et BI Auditor restent hors périmètre ; leurs interfaces sont simulées.

Les travaux réalisés couvrent successivement :

1. Le prototype ETL/KPI en notebooks et le benchmark local des modèles, avec Python,
   Pandas, Jupyter et Ollama. Les notebooks exécutés et les résultats d'origine sont
   conservés dans `archive/notebooks/` et `archive/outputs/llm_benchmark_local/`.
2. Le BI Analyst, les contrats JSON et les contrôles de preuves, puis l'investigation
   et l'aide à la décision avec Pydantic, JSON Schema, SciPy et Statsmodels.
3. La démonstration Streamlit et la correction des échecs métier : moyenne/médiane,
   classement puis drill-down verrouillé, contributions réconciliées, refus des causes
   et recrutements non démontrables, effacement des résultats après changement de question.
   Les preuves de cette phase et ses 156 tests sont dans `archive/outputs/business_intent/`.
4. Le pipeline amont exécutable et versionné, la nouvelle acquisition historique, les
   contrôles qualité, la couche sémantique, les six nouvelles inférences réelles et le
   nettoyage final décrits ci-dessous. Aucun benchmark ancien n'a été réécrit.

## 2. Données et stratégie d'acquisition

Source officielle : [NYC 311 2020–présent](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9).
L'[archive 2010–2019](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-2019/76ig-c548)
est distincte. La [notice NYC](https://www.nyc.gov/opendata/news/all-news/311-Service-Requests-Updates)
documente la séparation et les nouveaux libellés Problem/Problem Detail ; les noms API
`complaint_type` et `descriptor` sont conservés.

Le comptage distant a observé **22950021 lignes** sur le jeu courant,
avec créations de 2020-01-01 à 2026-10-27. La période retenue compte
**13,326,818 demandes officielles**.

| Approche | Faisabilité et choix |
|---|---|
| Jeu complet | Possible avec traitement hors mémoire et espace disponible ; environ 11.48 Go estimés pour les colonnes projetées à 500 octets/ligne, hors copies et index. Ce n'est pas une taille téléchargée mesurée. Coût réseau/I/O et suivi de snapshot supérieurs. |
| Historique borné | Choisi : 45 mois, sélection systématique d'environ 2 % par mois et arrondissement, projection de 11 champs, pages de 5 000, au plus 3 requêtes simultanées. Contrat plafonné à 500 000 lignes et 512 Mio. |

La position de départ est dérivée de la graine 42, du mois et de l'arrondissement ;
deux suffixes d'identifiants sur cent sont retenus. Ce n'est plus un prélèvement de
quelques fenêtres temporelles. **Ce n'est pas un échantillon probabiliste et aucune
représentativité de toute la population n'est affirmée.** La périodicité des identifiants,
les processus d'affectation et les catégories rares peuvent introduire un biais.

La couverture canonique mois × arrondissement × plainte × agence compte
**35,002 strates**, dont **19,231 observées** et
**15,771 absentes**. Les strates observées regroupent
**98.2079 %** du volume officiel.
Ce dernier taux n'est ni une précision statistique ni une garantie de représentativité.
Les effectifs sont agrégés après harmonisation des libellés, avant jointure, pour éviter
les doubles comptes dus aux variantes de casse. La révision initiale est archivée.

Les volumes mensuels officiels sont stockés séparément des statistiques détaillées de
l'extrait. Aucune période absente n'est fabriquée, aucun délai manquant n'est remplacé par zéro.
Chaque requête, filtre, page, effectif, empreinte et date d'extraction est conservé.
Les API exposent des dates flottantes sans fuseau : America/New_York est la convention
documentée de reporting retenue, conformément à la [recommandation Socrata](https://dev.socrata.com/docs/datatypes/floating_timestamp)
sur le fuseau de l'éditeur. Ce choix est explicite ; les chaînes source sont conservées,
les heures ambiguës/inexistantes lors des changements d'heure sont exclues des durées.
Les instants sont convertis en UTC ; les mois et clôtures mensuelles restent en heure de New York.

## 3. Qualité, ETL et couches de stockage

Version active : `data/versions/nyc311_202301_202609_v2/`.

```text
raw/          pages Parquet immuables, schéma API, agrégats, checkpoints, manifeste
profiling/    rapport complet : types, manquants, doublons, statuts, dimensions et dates
cleaned/      partitions normalisées et manifeste de provenance
quarantine/   observations à examiner, motifs, partition et ligne source
processed/    KPI Parquet, catalogue sémantique, vérification et manifeste
```

Seuils : moins de 30 observations valides ⇒ pas de classement opérationnel des délais.
Plus de 1 % de clés manquantes, créations invalides ou conflits temporels ⇒ blocage
qualité. Les durées négatives ou supérieures à 3 650 jours sont exclues des KPI de délai.
Les durées supérieures à 90 jours restent des valeurs à examiner, pas des valeurs supprimées.
Les doublons sont détectés sur toute la version, au-delà des frontières de lots ; la
première occurrence stable est conservée, les suivantes sont gardées en quarantaine.

Sur cette extraction : 36 créations et 39 clôtures sont non convertibles sans ambiguïté,
133 conflits fermeture/création sont signalés et 8 199 longues durées sont à examiner.
Il n'y a pas de clé manquante ni de doublon supprimé. Les demandes ouvertes, clôturées
et incohérentes restent distinguées. Les avertissements ne deviennent pas des causes métier.

Les rapports de profiling, les empreintes et les politiques sont transmis à l'ETL.
Une étape en échec arrête la publication suivante et laisse ses journaux et checkpoints.
Un verrouillage temporaire Windows sur un cache SQLite est journalisé ; il ne transforme
pas une donnée validée en erreur métier. Les index SQLite sont temporaires et recréables.

## 4. KPI et catalogue sémantique

| KPI sur la cohorte préparée | Valeur |
|---|---:|
| Demandes | 265,609 |
| Clôturées cohérentes | 259,354 |
| Ouvertes | 4,975 |
| Incohérentes | 1,280 |
| Moyenne de résolution | 12.330657 jours |
| Médiane de résolution | 0.304850 jour |
| Taux de clôture de cohorte à l'extraction | 97.658271 % |

Les durées sont des secondes écoulées en UTC divisées par 86 400, et non des jours ouvrés.
Le taux de clôture utilise les créations valides comme dénominateur et l'état cohérent
observé à l'extraction comme numérateur. Il dépend du recul d'observation des cohortes.
Moyennes et médianes ne sont pas interchangeables ; la médiane globale n'est jamais
la moyenne des médianes des groupes.

Le [catalogue sémantique](data/versions/nyc311_202301_202609_v2/processed/semantic_catalog.json)
précise définition, formule, colonnes, unité, période, dimensions, inclusion/exclusion,
limites, version et provenance. Les KPI incluent également quantiles/histogramme,
volumes/délais par arrondissement, plainte et agence, évolution mensuelle, stock et âge.
Le backlog est reconstitué pour la seule cohorte échantillonnée : les dossiers antérieurs
à janvier 2023 et les réouvertures non observées ne sont pas inventés.

Les contrôles recalculent les KPI globaux et par groupe, les dénominateurs dimensionnels,
les volumes mensuels officiels et la couverture canonique. Le dataframe du BI Analyst
mesure 39.55 Mo. Le dernier processus de préparation
a mesuré un pic de 378,05 Mo de working set avec profiling réutilisé après contrôle,
hors processus Ollama ; ce n'est pas un benchmark universel des ressources.

## 5. BI Analyst et validations

Le plan Python identifie intention, moyenne/médiane/volume/taux, dimensions, filtres,
périodes, ordre des opérations et demandes non démontrables. Le classement peut être
suivi d'un drill-down verrouillé dans un seul arrondissement. Une comparaison temporelle
interdite ne peut pas être réintroduite par Granite. Les contributions à une moyenne
sont réconciliées avec la variation globale ; elles ne constituent pas une causalité.

Granite reçoit un contexte réduit et des références autorisées. La génération est
contrainte par un catalogue de formulations : Python calcule et matérialise les chiffres.
Le rapport final est vérifié après génération, correction ou fallback. Les références,
sources, empreintes, effectifs, limitations, décisions et correspondances à la question
restent contrôlés. Une demande de recrutement ou de preuve causale sans données de
capacité produit `insufficient_data`, avec données nécessaires et protocole d'investigation.

Les 45 périodes permettent le test saisonnier de Mann–Kendall et les scans Z-score/MAD.
La corrélation sérielle n'est pas corrigée dans Mann–Kendall ; les scans d'anomalie ne
corrigent ni saisonnalité ni multiplicité. Ce sont des signaux à investiguer.

## 6. Résultats réels de validation

| Scénario | Statut / décision | Durée totale vérifications incluses | Corrections / fallback | Exécution |
|---|---|---:|---|---|
| A | `validated` / `answer` | 242.88 s | 0 / False | `adddd0d6624e4ba2925dae1789a01d28` |
| B | `validated` / `answer` | 175.28 s | 0 / False | `d7d56684c106487ea308d1c180691815` |
| 7 | `validated` / `insufficient_data` | 92.33 s | 0 / False | `9bf20515b53e4ae3a90006c79f9c9264` |
| trend | `validated` / `answer` | 84.25 s | 0 / False | `7e086c55679c4024b04af0cbc91b0686` |
| anomalies | `validated` / `answer` | 143.32 s | 0 / False | `d283a7e20a5b46358566f36d09e0203f` |
| rates | `validated` / `answer` | 116.19 s | 0 / False | `7d3ccf6b86ca4c719577b4acdc93e949` |

Pour A, la moyenne passe de **10.443907** à **11.800671 jours**
entre août et septembre 2026, soit **+1.356764 jours** ; la décomposition et ses
effectifs sont vérifiés. Pour B, le maximum éligible est **STATEN ISLAND**,
médiane **0.655677 jour**, n=**9874**, puis le drill-down reste dans ce périmètre.
Le test 7 refuse explicitement la causalité et le calcul d'effectifs sans données adaptées.
Les six cas passent sans fallback. Les durées ci-dessus incluent les recalculs indépendants,
et ne sont pas uniquement les temps d'inférence.

Les [rapports et réponses brutes](outputs/pipeline_delivery/real_evaluation/results.json),
le [recalcul sans modèle](outputs/pipeline_delivery/real_evaluation/replay_verification.json),
les [contrôles statistiques](outputs/pipeline_delivery/real_evaluation/statistical_checks.json),
les [tests](outputs/pipeline_delivery/test_suite/summary.json) et les audits sont conservés.
La comparaison [ancienne/nouvelle version](outputs/pipeline_delivery/real_evaluation/old_new_comparison.json)
signale les différences de population, méthode, fuseau et date de suivi : une différence
de médiane ou de taux ne doit pas être présentée comme une amélioration du service.

## 7. Installation et commandes Windows

```powershell
cd D:\Analyst_Agent
py -3.13 -m venv .venv                    # seulement si le venv n'existe pas
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama serve                              # si le serveur Ollama n'est pas déjà lancé
ollama pull granite3.3:2b                  # seulement si le modèle n'est pas installé
```

Préparer les données locales déjà téléchargées, sans appel réseau :

```powershell
.\.venv\Scripts\python.exe -m bi_pipeline.cli prepare --root D:\Analyst_Agent --version nyc311_202301_202609_v2
.\.venv\Scripts\python.exe -m bi_pipeline.cli activate --root D:\Analyst_Agent --version nyc311_202301_202609_v2
```

Nouvelle acquisition bornée puis préparation, avec une nouvelle version ou reprise :

```powershell
.\.venv\Scripts\python.exe -m bi_pipeline.cli acquire --root D:\Analyst_Agent --version nouvelle_extraction --start 2023-01 --end 2026-10 --suffix-count 2
.\.venv\Scripts\python.exe -m bi_pipeline.cli prepare --root D:\Analyst_Agent --version nouvelle_extraction
# run exécute acquisition puis préparation ; end est exclusif
.\.venv\Scripts\python.exe -m bi_pipeline.cli run --root D:\Analyst_Agent --version nouvelle_extraction --start 2023-01 --end 2026-10 --suffix-count 2
```

Importer un CSV local et ses métadonnées concordantes dans une nouvelle version :

```powershell
.\.venv\Scripts\python.exe -m bi_pipeline.cli import-local --root D:\Analyst_Agent --version import_local_nouveau --csv chemin\fichier.csv --metadata chemin\metadata.json --timezone America/New_York
```

Le nombre de lignes et les comptages mensuels doivent correspondre ; sinon la publication
est refusée. L'audit a trouvé deux anciens RAW distincts (8 000 et 10 000 lignes) :
`nyc311_sample_metadata.json` correspond au fichier `nyc311_sample_jun_oct_2026.csv`,
pas à `nyc311_sample.csv`. L'essai d'import incorrect est invalidé et archivé.

Analyse Python seule, puis analyse avec Granite :

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.cli --root D:\Analyst_Agent --dataset-version nyc311_202301_202609_v2 --baseline --question "Quel arrondissement a la médiane la plus élevée ?"
.\.venv\Scripts\python.exe -m bi_analyst.cli --root D:\Analyst_Agent --dataset-version nyc311_202301_202609_v2 --question "Identifie l'arrondissement à médiane maximale puis compare les plaintes et agences dans cet arrondissement, sans analyse temporelle."
```

Le CLI utilise la version active si aucune version n'est donnée ; `--legacy-data`
force les anciennes données CSV. L'API Python `analyze()` conserve son défaut historique
pour les benchmarks ; passer `AnalysisContext(project_root=..., dataset_version=...)`
pour sélectionner explicitement les données v2.

Démonstration et contrôles :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\demo\launch_demo.ps1
.\.venv\Scripts\python.exe -m bi_analyst.demo_preflight --root D:\Analyst_Agent --check-data --check-ollama
.\.venv\Scripts\python.exe tools\run_agent_tests.py --output outputs\pipeline_delivery\test_suite
.\.venv\Scripts\python.exe tools\validate_release.py
.\.venv\Scripts\python.exe tools\export_bi_analyst_schemas.py
# Régénérer la documentation et le bilan après les vérifications
.\.venv\Scripts\python.exe tools\finalize_pipeline.py --apply
```

Ouvrir http://127.0.0.1:8501. Choisir **Derniers résultats sur les nouvelles données**
pour afficher les six vrais rapports sans nouvelle inférence ; **Exécution Granite en
direct** appelle le modèle sur la version choisie. Modifier question ou version efface
le résultat précédent. Les restitutions historiques restent identifiées comme archives.

Évaluer une nouvelle version et relire une campagne sans modèle :

```powershell
.\.venv\Scripts\python.exe -m bi_pipeline.evaluation --root D:\Analyst_Agent --version nyc311_202301_202609_v2 --output outputs\pipeline_delivery\nouvelle_evaluation
.\.venv\Scripts\python.exe -m bi_pipeline.replay_evaluation --root D:\Analyst_Agent --version nyc311_202301_202609_v2 --output outputs\pipeline_delivery\real_evaluation
```

Le dossier d'une nouvelle évaluation doit être nouveau. Une panne réseau ou Ollama
n'est jamais compensée par des données fictives ou un autre modèle. Les fixtures
synthétiques sont limitées aux tests et explicitement marquées.

## 8. Organisation et nettoyage

```text
bi_pipeline/      pipeline amont exécutable et contrôles
bi_analyst/       analyse, routage, preuves, validation et services
benchmark_llm/    client Ollama et contrats historiques encore importés par le runtime
demo/            application Streamlit et lanceurs
tests/           tests unitaires et d'intégration
schemas/         contrats JSON utilisés
tools/           outils courants de test, schémas et livraison
data/versions/   version courante RAW → CLEANED → PROCESSED
outputs/pipeline_delivery/  dernière évaluation et preuves nécessaires
archive/         anciens scripts, notebooks, rapports et résultats, conservés intacts
README.md        document principal unique de la partie active
requirements.txt dépendances actuelles uniques
```

Les anciens résultats, notebooks et rapports ont été **archivés**, pas détruits.
Le [manifeste de déplacements](archive/relocations.json) conserve les chemins d'origine
et les empreintes. Les anciennes sources RAW/CSV et deux CSV `outputs/bi_analyst`
restent à leur emplacement car les benchmarks et contrats de régression les utilisent.
Les caches Python et index temporaires recréables sont supprimés ; les objets verrouillés
restent signalés dans le [bilan de nettoyage](outputs/pipeline_delivery/cleanup_manifest.json).
Aucune suppression irréversible de contenu non temporaire n'a été effectuée.
Les [empreintes historiques](outputs/pipeline_delivery/historical_integrity.json)
vérifient la conservation des données, notebooks, benchmarks et preuves précédents.

## 9. Limites et reprise

- Le détail reste un extrait systématique non aléatoire : pas d'extrapolation des délais
  à toute la ville, pas de confiance statistique inventée, pas de promesse de représentativité.
- Les taux et délais dépendent de la cohorte et de la date de suivi ; le backlog ne décrit
  ni les dossiers antérieurs à la période ni les réouvertures non observées.
- Les mois demandés doivent être disponibles et complets ; aucune substitution silencieuse.
- Les différences entre agences exigent un contrôle de composition et de complexité.
- Aucune preuve d'effectifs, de capacité, de recrutement ou de causalité n'est disponible.
- Les tests temporels restent exploratoires avec les limites d'autocorrélation et de saisonnalité citées.
- Les requêtes API ne sont pas une transaction globale ; révisions, comptages et empreintes sont enregistrés.
- Les transformations ne dépendent d'aucune API payante ; seule une nouvelle acquisition
  exige Internet. RAW et checkpoints permettent la reprise ; prepare et replay sont locaux.

Les tentatives, refus de qualité et corrections de couverture restent tracés dans les
journaux, y compris les incidents de verrouillage Windows. Le modèle Granite n'est ni
le calculateur statistique ni le validateur de ses propres sorties.
