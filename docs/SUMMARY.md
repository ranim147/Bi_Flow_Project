# BIFlow — Synthèse de la fondation (infra + orchestrateur)

*Date : 23/10/2026 — rôle : Orchestrateur / infra*

## Ce qui a été construit

- **Infra locale** : `docker-compose.yml` avec PostgreSQL 16 et Adminer (port 8081, car 8080 est souvent
  pris), identifiants lus depuis `.env`.
- **Schéma de base** (SQLAlchemy Core, `src/biflow/db/models.py`, initialisation idempotente
  `python -m biflow.db.init_db`) : schémas `raw`, `processed`, `ref`, `audit`.
- **Ingestion NYC 311** (`python -m biflow.ingestion`) : API Socrata avec `$select`, `$where`, `$order`,
  `$limit`/`$offset`, token optionnel, retries HTTP (429/5xx). Sauvegarde dans `data/raw/` (parquet ou CSV),
  puis **upsert** dans `raw.nyc311` (COPY vers une table temporaire puis `INSERT … ON CONFLICT`), ce qui
  permet de relancer sans risque. Contrôles de base sur l'extrait (schéma, doublons, closed < created).
- **Orchestrateur LangGraph** : un nœud par agent et un nœud `finalize`, avec un state pydantic typé.
  Une étape en échec est relancée 2 fois (configurable). La validation se fait après chaque étape. La
  boucle de rétroaction de l'Auditeur renvoie une seule fois vers l'étape fautive. Statuts finaux :
  `success`, `failed`, `unreliable` ; seul `success` publie le résultat.
- **Classe `BaseAgent`** : retries, validation, chronométrage et écriture dans `audit.trace_log` sont
  automatiques. Les 6 agents sont des **stubs** documentés qui renvoient des sorties factices valides.
- **CLI** `python -m biflow.run` avec un résumé du run, l'option `--no-db` et des options de simulation
  (`--simulate-failure`, `--simulate-audit-flag`, `--persistent`).
- **Qualité** : 26 tests pytest, `Makefile` et `scripts/tasks.ps1` (Windows), README en français avec
  un diagramme mermaid et la section « Pour les coéquipiers ».

## Structure

```
biflow/
├── docker-compose.yml, pyproject.toml, requirements.txt, Makefile, .env.example
├── scripts/tasks.ps1
├── data/{raw,processed,test}/        (ignoré par git)
├── docs/SUMMARY.md
├── notebooks/
├── src/biflow/
│   ├── run.py                       CLI du pipeline
│   ├── common/                      config, logging, schéma des colonnes, load_raw_frame
│   ├── db/                          engine, models, init_db, loader (upsert)
│   ├── ingestion/                   socrata.py (client) + __main__.py (CLI)
│   ├── orchestrator/                state, graph, validation, tracing, report
│   └── agents/                      base.py, testing.py, 6 stubs
└── tests/                           conftest, test_ingestion, test_db, test_orchestrator
```

## Tables

| Table | Rôle |
|---|---|
| `raw.nyc311` | demandes brutes, PK `unique_key`, + `ingested_at` |
| `ref.boroughs` | 5 boroughs, `population` (+ année et source) **à compléter** |
| `ref.sla_targets` | délai cible en heures pour 6 types de plainte : **valeurs PLACEHOLDER** (`is_placeholder = true`, « TO BE DEFINED ») |
| `audit.trace_log` | une ligne par tentative d'étape + une ligne `__run__` par run (statut, durées, lignes in/out, message, détails JSONB) |
| `processed.*` | vide, réservé à l'agent Qualité/ETL |

## Run de test

- Ingestion `--max-rows 5000` sur les 12 derniers mois (types `Noise*` et `Street*`) : **5 000 lignes**
  chargées (14 types de plainte ; `created_date` du 19/10/2026 au 22/10/2026, car le tri est du plus récent
  au plus ancien). La relance a donné 0 nouvelle ligne et 5 000 mises à jour : l'upsert est idempotent.
- Pipeline : run nominal `success`. La démo de panne donne 3 essais puis `failed`. La boucle Auditeur
  donne un renvoi vers `kpi_semantic` puis `success`, et un problème persistant donne `unreliable`. Tout
  est tracé dans `audit.trace_log`.
- **Tests : 26 passés, 0 échec** (ingestion : construction de requêtes et pagination simulée ; DB : upsert,
  idempotence, NULL, doublons, seeds, trace ; orchestrateur : bout en bout, retry, échec, validation
  bloquante, boucle Auditeur).

## Limites connues

- **Les 6 agents sont des stubs** : leurs sorties sont factices (le résumé les marque `[stub]`).
- **Plafond et couverture temporelle** : la pagination va du plus récent au plus ancien. Avec environ
  1 700 demandes « Noise/Street » par jour, le plafond par défaut de 200 000 lignes couvre environ
  4 mois, pas 12. Pour une année complète, il faut augmenter `--max-rows` (environ 600 000 lignes) ou
  découper la période.
- `ref.sla_targets` et `ref.boroughs.population` sont à définir.
- Les données récentes changent encore (statut, closed_date) : relancer l'ingestion les met à jour,
  mais aucune ingestion incrémentale automatique (planifiée) n'est prévue.
- Pas de système de migrations versionnées (Alembic) : `init_db` crée les tables manquantes mais ne
  modifie pas une table existante. Si une colonne change, il faut supprimer le volume Docker ou passer à
  Alembic.
- La sortie de chaque agent est stockée dans le state en dict JSON, sans DataFrame : les gros résultats
  doivent passer par la base.
- Les retries relancent l'agent à l'identique : c'est utile pour les erreurs transitoires (réseau, LLM),
  pas pour un bug déterministe.

## Prochaines étapes par coéquipier

| Coéquipier | Prochaines étapes |
|---|---|
| **Data Profiler** | Profil réel via `load_raw_frame(state.dataset)` : nulls, distributions, plage de dates, anomalies (dates incohérentes, coordonnées hors NYC, zips invalides, borough « Unspecified ») listées dans `anomalies` |
| **Qualité / ETL** | Règles de nettoyage, calcul de `resolution_hours`, jointures `ref.*`, écriture de `processed.nyc311_clean`, liste des règles dans `rules_applied` ; compléter `ref.boroughs.population` (source officielle) |
| **KPI & Semantic** | Définir les KPI (volume, délai médian, respect SLA, taux de clôture, demandes pour 10 000 habitants) et la couche sémantique ; **valider les valeurs de `ref.sla_targets`** avec l'équipe |
| **Analyste BI** | Insights (tendances, comparaisons entre boroughs, types hors SLA), chacun justifié par `kpi_refs` et `evidence` |
| **Dashboard Generator** | Spec des graphiques + application Streamlit/Plotly qui lit la spec et la base ; publication uniquement si le run est `success` |
| **Auditeur / XAI** | Lire `audit.trace_log` du run, recalculer des KPI de façon indépendante, vérifier les insights, émettre des `findings` ciblés et une explication lisible |
| **Orchestrateur / infra** (moi) | Ingestion de la période complète, ingestion incrémentale, Alembic si le schéma évolue, CI GitHub Actions (pytest + Postgres), revue des contrats entre agents |

---

# MVP : les 6 agents réels

*Date : 28/10/2026*

## Ce qui a été ajouté

- **Les 6 agents sont réels** (plus aucun stub), conformes à l'étape 2 de l'étude d'architecture :
  - *Data Profiler* : défauts typés par colonne (doublon, manquant, mauvais type, format, valeur invalide,
    hors plage, incohérence), avec quelques `unique_key` en exemple ;
  - *Qualité / ETL* : 12 règles comptées et tracées, minimisation RGPD, enrichissement (délai, SLA),
    instantané parquet + empreinte SHA-256, copie dans `processed.nyc311_clean` si le run lit PostgreSQL ;
  - *KPI & Semantic* : catalogue YAML (`src/biflow/semantic/kpi_catalog.yaml`), SQL exécuté par DuckDB en
    lecture seule, journal des requêtes, seuil anti-identification, question → SQL (Mistral ou baseline) ;
  - *Analyste BI* : STL + z-score robuste (ville et chaque borough), insights avec `evidence`, rédaction
    Mistral optionnelle avec rejet de tout nombre non justifié ;
  - *Dashboard* : spec JSON (valeurs + requêtes d'origine) et app Streamlit + Plotly qui vérifie l'empreinte ;
  - *Auditeur / XAI* : rejeu des requêtes, recalcul indépendant en pandas, contrôles des insights et du
    dashboard, explication en clair. Les options de simulation (`--simulate-audit-flag`) sont conservées.
- **Orchestrateur** : `state.workspace` (artefacts du run), `state.questions`, `run.json` écrit à la fin de
  chaque run, `published.json` seulement si le run est `success`.
- **CLI** : `--question` (répétable), `--output-dir` ; sans PostgreSQL, le dernier extrait de `data/raw` est utilisé.
- **Évaluation** : `python -m biflow.evaluation` (jeu synthétique à défauts et anomalie connus, 26 questions,
  4 écarts volontaires pour l'Auditeur).
- **Infra** : `connect_timeout` de 3 s (sous Windows, une base absente bloquait environ 100 s par connexion),
  populations 2020 dans `ref.boroughs`, nouvelles dépendances `duckdb`, `statsmodels`, `pyyaml`.
- **Tests : 53 passés** sans PostgreSQL (+ 7 tests `db` ignorés sans base). Les tests n'appellent jamais l'API Mistral.

## Écarts avec l'étude d'architecture

| Étude (MVP) | Implémentation | Raison |
|---|---|---|
| Script Python, puis LangGraph à l'étape 3 | LangGraph dès le MVP | déjà en place et testé (retry, boucle de l'Auditeur) ; chaque agent reste une fonction simple |
| DuckDB pour stocker et exécuter le SQL | PostgreSQL pour stocker, DuckDB pour exécuter le SQL sur l'instantané | choix de l'équipe : garder la base existante ; DuckDB sert de bac à sable en lecture seule |
| Table `audit_log` | `audit.trace_log` (étapes) + `queries` dans `run.json` (SQL, instantané, résultat) | pas de nouvelle table tant que le schéma n'est pas versionné (Alembic) |

## Limites connues

- **Biais des périodes récentes** : sur un extrait des derniers jours, seules les demandes déjà clôturées
  entrent dans le délai médian et le SLA (sur l'extrait réel du 19 au 22/10, délai médian 1,1 h et SLA à 97 %).
  Il faut une période plus ancienne, ou une analyse de survie, pour des délais représentatifs.
- **Délais cibles SLA** toujours provisoires (`is_placeholder = true`).
- **Baseline question → SQL** : elle couvre les formulations prévues ; sur des reformulations libres, elle
  échoue ou répond à côté. L'apport de Mistral reste à mesurer (aucune clé configurée pendant le développement).
- Le contrôle des données personnelles dans la question repose sur des motifs simples : il ne détecte pas un nom isolé.
- Le seuil anti-identification s'applique aux réponses des questions grâce à la colonne `n` : une requête du
  LLM sans `n` lui est renvoyée pour correction, puis la baseline prend le relais.

---

# Données réelles, LLM local et compléments

*Date : 30/10/2026*

## Ce qui a été ajouté

- **Données réelles de l'équipe** : `python -m biflow.ingestion.import_pages` regroupe des pages brutes
  (parquet/CSV) en un extrait `data/raw` avec sa fiche de provenance `.meta.json` (source, empreinte, réserves).
  Extrait importé : 265 609 demandes, 2023-01 à 2026-09, depuis l'acquisition du dépôt `Bi_Flow_Project`.
- **Colonnes facultatives** (`descriptor`, `incident_zip`, `resolution_description`, `latitude`, `longitude`) :
  absentes d'un extrait, elles sont ajoutées vides et signalées par le Data Profiler.
- **LLM remplaçable** (`src/biflow/llm/client.py`, `BIFLOW_LLM_PROVIDER`) : Mistral ou Ollama (modèle local).
  Le prompt question → SQL reçoit les modalités des dimensions portées par au moins N demandes.
- **Contrôle croisé** LLM / baseline sur chaque réponse ; **refus motivé** des questions hors périmètre
  (`src/biflow/semantic/scope.py`) ; **quarantaine** des lignes supprimées avec leur règle.
- **Analyste BI** : tendance sur un an quand l'historique couvre 2 ans ; type de plainte le plus en hausse
  pour chaque anomalie (piste, pas une cause) ; réserves si les données sont échantillonnées.
- **Dashboard** : onglets *Tableau de bord*, *Poser une question* (`ask()` sur l'instantané audité, empreinte
  vérifiée) et *Audit et provenance*.
- **Démo** : `scripts/demo.ps1` (et `make demo`), déroulé dans `docs/DEMO.md`.

## Corrections trouvées sur les données réelles

- La limite de 1 000 lignes (prévue pour le SQL généré) bloquait la série journalière du catalogue
  (1 369 jours) : le SQL du catalogue, relu dans Git, a sa propre limite (100 000).
- La tendance « première contre dernière semaine » n'avait pas de sens sur 45 mois : fenêtre adaptée
  (année contre année précédente).
- La baseline répondait à côté sur « quelle agence... » (elle donnait le total) : elle répond maintenant
  « non reconnue » quand le regroupement demandé lui est inconnu.
- `scripts/tasks.ps1` : `$ErrorActionPreference = "Stop"` transformait les logs Python (stderr) en erreur
  fatale sous Windows PowerShell 5.1.

## Vérifications

- **Tests : 86 passés**, y compris les 6 tests `db` sur PostgreSQL (Docker) ; ruff sans erreur.
- **Chemin PostgreSQL** testé de bout en bout : lecture de `raw.nyc311` et `ref.*`, écriture de
  `processed.nyc311_clean`, 7 lignes dans `audit.trace_log`.
- **Évaluation** : voir le tableau du README (baseline 61 %, LLM local 88 % sur 51 questions).
- **Contrôle croisé avec l'implémentation de l'équipe** : même borough au délai médian le plus long
  (STATEN ISLAND, 15,736 h).

## Limites restantes

- Mistral non testé (pas de clé) ; le LLM local a été mesuré à la place.
- Mesure du LLM légèrement optimiste (prompt ajusté sur le même jeu de questions) : prévoir un jeu neuf.
- Échantillon non probabiliste : volumes et taux pour 10 000 habitants non extrapolables à la ville.
- Délais cibles SLA toujours provisoires.
