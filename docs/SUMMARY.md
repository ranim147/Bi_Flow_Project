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
