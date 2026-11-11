# BIFlow — Scénarios des 6 agents

Ce document décrit, pour chacun des 6 agents, **qui le déclenche, ce qu'il reçoit, ce qu'il fait étape par
étape, ce qu'il produit, et ce qui se passe quand les choses tournent mal**. Les chiffres cités viennent du
run publié `20261030T135904Z-a68722` (extrait NYC 311, janvier 2023 à septembre 2026, échantillon d'environ
2 %), sauf mention contraire.

Fil conducteur : **le LLM comprend et rédige, le code calcule et vérifie.** Aucun chiffre affiché ne vient
du LLM.

---

## 0. Vue d'ensemble

### Acteurs

| Acteur | Rôle dans les scénarios |
|---|---|
| **Data engineer** | lance l'ingestion et le pipeline, consulte la quarantaine et les règles ETL |
| **Analyste / responsable métier** | lit le tableau de bord, pose des questions en langage naturel |
| **Auditeur / DPO** | vérifie la provenance des chiffres, le respect du seuil anti-identification |
| **Orchestrateur LangGraph** | enchaîne les agents, relance une étape, renvoie vers l'étape fautive |
| **LLM (optionnel)** | Mistral ou modèle local (Ollama `qwen2.5:7b`) ; question → SQL et rédaction |

### Enchaînement

```
Data Profiler → Qualité/ETL → KPI & Semantic → Analyste BI → Dashboard Generator → Auditeur/XAI → finalize
                    ▲               ▲                                                    │
                    └───────────────┴──────── finding "error" : renvoi (1 seule fois) ───┘
```

Règles communes à tous les agents (orchestrateur, `src/biflow/orchestrator/graph.py`) :

- après chaque agent, sa sortie est **validée** (schéma pydantic + contrôles métier `validate()`) ;
- une exception ou une validation en échec → l'étape est **relancée** (`BIFLOW_MAX_RETRIES`, 2 par défaut) ;
- après le dernier essai → run **FAILED**, les étapes suivantes ne tournent pas, **rien n'est publié** ;
- chaque tentative est écrite dans `audit.trace_log`.

### Scénario de bout en bout (résumé chiffré)

| Étape | Résultat sur le run de référence |
|---|---|
| 1. Data Profiler | 265 609 demandes profilées, 3 types de défauts trouvés |
| 2. Qualité/ETL | 139 lignes supprimées (et mises en quarantaine), 265 470 conservées, instantané `83d3805019db` |
| 3. KPI & Semantic | 5 KPI, 6 ventilations, 4 questions, 13 requêtes enregistrées |
| 4. Analyste BI | 10 insights (tendance + 5 anomalies), méthode STL |
| 5. Dashboard Generator | 13 éléments : 5 cartes KPI, 5 barres, 1 courbe, 1 table, 1 bloc texte |
| 6. Auditeur/XAI | 53/53 contrôles réussis → statut **SUCCESS**, run publié |

Durée totale : environ 10 secondes sans LLM, environ 1 min 30 avec le LLM local (`qwen2.5:7b`, rédaction des
insights comprise).

---

## 1. Agent Data Profiler

**Objectif :** dire, avant toute transformation, à quoi ressemblent les données et ce qui ne va pas dedans.
**Code :** `src/biflow/agents/data_profiler.py` — sans LLM.

| | |
|---|---|
| Déclencheur | début du run (`python -m biflow.run`) |
| Préconditions | un extrait existe : table `raw.nyc311` (PostgreSQL) ou fichier `--file` dans `data/raw/` |
| Entrée | `state.dataset` (table ou fichier, période) |
| Sortie | `ProfilerOutput` : nombre de lignes, taux de nulls, plage de dates, distributions, liste typée de défauts |

### Scénario nominal

1. L'orchestrateur appelle le Profiler avec la référence du jeu de données.
2. Le Profiler charge l'extrait brut et vérifie que les colonnes obligatoires sont présentes.
3. Il convertit les dates et les nombres ; une valeur présente mais non convertible est comptée comme
   « mauvais type ».
4. Il calcule les taux de valeurs nulles, la plage `created_date`, les répartitions par type de plainte,
   borough et statut.
5. Il cherche 7 familles de défauts, chacune rattachée à une colonne, avec un compteur et quelques
   `unique_key` d'exemple :

   | Type | Contrôle |
   |---|---|
   | `duplicate` | `unique_key` déjà vue |
   | `invalid_type` | date ou nombre non convertible |
   | `missing` | `created_date`, `complaint_type`, `borough` vides ; demande « Closed » sans `closed_date` |
   | `inconsistent` | `closed_date` antérieure à `created_date` |
   | `invalid_format` | code postal qui n'a pas 5 chiffres |
   | `invalid_value` | borough hors des 5 officiels |
   | `out_of_range` | latitude / longitude hors de l'emprise de New York |

6. Il lit la fiche de provenance (`.meta.json`) de l'extrait : elle signale ici un échantillon non
   probabiliste, réserve reprise plus loin par l'Analyste et le dashboard.
7. Validation : colonnes obligatoires présentes, au moins 1 ligne, taux de nulls dans [0, 1].

### Exemple réel

> 265 609 demandes du 2023-01-01 au 2026-09-30.
> Défauts trouvés :
> - `closed_date [inconsistent]` : 139 lignes avec closed_date < created_date
> - `closed_date [missing]` : 1 089 demandes « Closed » sans closed_date
> - `borough [invalid_value]` : 284 boroughs hors des 5 officiels (ex. « Unspecified »)

### Scénarios alternatifs

| Cas | Comportement |
|---|---|
| Colonne obligatoire absente | `ValueError` → 2 relances → run **FAILED**, rien n'est publié |
| Extrait vide | validation `row_count >= 1` en échec → relances → **FAILED** |
| Colonne facultative absente | non bloquant : listée dans `absent_columns`, laissée vide |

### Critère d'acceptation

Sur les données synthétiques de l'évaluation (`python -m biflow.evaluation`), **9 types de défauts
injectés sur 9 sont détectés** (objectif ≥ 95 %).

---

## 2. Agent Qualité / ETL

**Objectif :** produire une couche propre, minimisée (RGPD) et figée, sans jamais perdre une ligne sans trace.
**Code :** `src/biflow/agents/quality_etl.py` — sans LLM.

| | |
|---|---|
| Déclencheur | sortie du Profiler validée |
| Entrées | données brutes, profil, référentiel `ref.sla_targets` |
| Sortie | `QualityOutput` : compteurs, règles appliquées, instantané parquet + empreinte SHA-256, quarantaine |

### Scénario nominal

1. Récupère les données brutes et le profil.
2. **Supprime** les lignes inutilisables, chacune copiée en quarantaine avec son motif :
   `unique_key` absente, `created_date` invalide, doublons (dernière version gardée),
   `closed_date < created_date`, `complaint_type` absent.
3. **Corrige** sans supprimer : borough hors liste → `UNSPECIFIED`, code postal invalide → NULL,
   coordonnées hors NYC → NULL, libellés normalisés (espaces, casse).
4. **Minimise (RGPD)** : retire le texte libre `resolution_description`, arrondit les coordonnées à
   2 décimales (zone d'environ 1 km).
5. **Enrichit** : `resolution_hours`, jour, heure, jour de semaine, délai cible SLA, `within_sla`.
6. Écrit l'**instantané** `nyc311_clean.parquet` et calcule son empreinte. Tous les agents suivants lisent
   ce fichier et uniquement lui.
7. Si le run lit PostgreSQL, publie aussi `processed.nyc311_clean`.
8. Validation : `rows_out ≤ rows_in`, `rows_out + rows_dropped = rows_in`, pas de texte libre dans la
   sortie, **lignes en quarantaine = lignes supprimées**, lignes lues = lignes profilées (avertissement).

### Exemple réel

> 265 609 lues → 265 470 conservées → **139 supprimées** (règle `closed_date < created_date`), toutes en
> quarantaine. 283 boroughs ramenés à `UNSPECIFIED`. Texte libre retiré sur 265 470 lignes.
> Objectif SLA trouvé pour 57 960 demandes. Instantané `83d3805019db`. 12 règles tracées.

### Scénarios alternatifs

| Cas | Comportement |
|---|---|
| Toutes les lignes sont supprimées | validation `rows_out_not_empty` en échec → relances → **FAILED** |
| Une ligne disparaît sans quarantaine | validation `dropped_rows_quarantined` en échec → relances → **FAILED** |
| Panne simulée (`--simulate-failure quality_etl`) | 3 tentatives tracées dans `audit.trace_log`, puis **FAILED** ; KPI, Analyste, Dashboard et Auditeur ne tournent pas |
| L'Auditeur signale l'étape | relancée une fois avec le message dans `state.corrections["quality_etl"]`, noté dans ses `notes` |

### Critère d'acceptation

Bilan des lignes exact (lues = conservées + quarantaine) et aucune colonne de texte libre dans la couche propre.

---

## 3. Agent KPI & Semantic

**Objectif :** calculer les indicateurs à partir de définitions communes, et répondre aux questions métier
sans laisser le LLM produire un chiffre.
**Code :** `src/biflow/agents/kpi_semantic.py`, catalogue `src/biflow/semantic/kpi_catalog.yaml`.
**LLM :** traduction question → SQL (optionnelle).

| | |
|---|---|
| Déclencheur | sortie Qualité/ETL validée |
| Entrées | instantané, catalogue YAML (revu dans Git), questions `--question` |
| Sortie | `KPIOutput` : KPI, ventilations, réponses, journal de toutes les requêtes |

### Scénario nominal A — les KPI du catalogue

1. Charge l'instantané dans un **bac à sable DuckDB** (lecture seule, tables autorisées, temps et lignes
   limités).
2. Exécute les 5 KPI du catalogue : `total_requests`, `closure_rate`, `median_resolution_hours`,
   `sla_compliance_rate`, `requests_per_10k`.
3. Exécute les 6 ventilations (par borough, par type, par jour…).
4. **Masque** tout agrégat portant sur moins de N demandes (`BIFLOW_MIN_GROUP_SIZE`, 10 par défaut).
5. Enregistre chaque requête : id, SQL, empreinte de l'instantané, horodatage, résultat.
6. Validation : au moins un KPI, chaque KPI dans sa plage (ratio dans [0, 1], pourcentage dans [0, 100]…),
   aucun agrégat sous le seuil exposé.

> Exemple réel : 265 470 demandes · 97,7 % clôturées · délai médian 7,3 h · 93,5 % sous le délai cible ·
> 301,2 demandes pour 10 000 habitants.

### Scénario nominal B — une question métier

L'analyste demande : **« Combien de demandes de bruit dans le Bronx ? »**

1. La question est **contrôlée** : email, téléphone, adresse, n° de demande sont masqués avant tout envoi.
2. Contrôle de **périmètre** : la question ne demande ni cause, ni effectif, ni prévision → acceptée.
3. Avec LLM : le LLM reçoit la question contrôlée, le schéma, les définitions et les modalités des
   dimensions ; il renvoie du SQL. Sans LLM : la baseline à règles produit le SQL.
4. Le SQL passe dans le bac à sable ; il doit contenir une colonne `n` pour que le seuil s'applique.
5. **Contrôle croisé** : si la baseline reconnaît aussi la question, les deux résultats sont comparés
   (`concordant` / `divergent`). Une divergence est affichée « réponse à vérifier ».
6. Le résultat, après seuil, est enregistré comme toute autre requête.

> Exemple réel (baseline) : **15 443 demandes**.
> « Quel borough a le délai médian le plus long ? » → **STATEN ISLAND, 15,7 h** (n = 9 876).

### Scénarios alternatifs

| Cas | Comportement |
|---|---|
| **Question hors périmètre** : « Pourquoi les délais sont-ils plus longs à Staten Island ? » | refus motivé, **sans requête ni appel au LLM** : « les données montrent des volumes et des délais, pas leurs causes ». Idem pour « Combien d'agents faut-il recruter ? » (moyens) et les prévisions |
| SQL du LLM rejeté (UPDATE, table interdite, erreur) | l'erreur est renvoyée au LLM pour **une** correction ; si toujours en échec, la baseline prend le relais |
| Question non reconnue sans LLM : « Quelle agence traite le plus de demandes ? » | `mode = none`, réponse vide avec la raison ; avertissement, le run continue |
| Question avec une adresse ou un email | masqués dans `question_sent`, listés dans `personal_data_found` |
| KPI non calculable (aucune demande clôturée sur la période) | KPI ignoré, noté dans les `notes` |

### Critères d'acceptation

| Mesure (évaluation, seed 42) | Baseline | LLM local qwen2.5:7b |
|---|---|---|
| SQL correct sur 51 questions | 61 % | **88 %** (objectif ≥ 80 %) |
| KPI = calcul manuel | 5/5 | 5/5 |
| Questions hors périmètre refusées | 3/3 | 3/3 |

---

## 4. Agent Analyste BI

**Objectif :** transformer les KPI en constats lisibles, chacun avec ses preuves chiffrées.
**Code :** `src/biflow/agents/analyste_bi.py`, `src/biflow/analysis/anomalies.py`.
**LLM :** reformulation des textes (optionnelle, contrôlée).

| | |
|---|---|
| Déclencheur | sortie KPI validée |
| Entrées | KPI et ventilations, instantané, provenance de l'extrait |
| Sortie | `AnalysisOutput` : insights (titre, texte, `evidence`, `kpi_refs`), anomalies, tendance |

### Scénario nominal

1. **Faits de synthèse** (gabarits) : volume et type dominant, délais et boroughs extrêmes, respect du SLA,
   demandes par habitant.
2. **Tendance** : moyenne journalière de la dernière année comparée à l'année précédente.
3. **Anomalies** : décomposition STL de la série journalière + z-score robuste, pour toute la ville et pour
   chaque borough assez volumineux (moyenne ≥ N demandes par jour).
4. Pour chaque pic, cherche le **type de plainte le plus en hausse** ce jour-là par rapport aux 28 jours
   précédents, présenté comme **« piste à vérifier, pas une cause »**.
5. Chaque insight porte ses `evidence` (les nombres sur lesquels il s'appuie).
6. Avec LLM : les textes sont réécrits pour des non-techniciens. **Tout nombre du nouveau texte absent des
   `evidence` fait rejeter la réécriture** ; le gabarit est gardé.
7. Validation : au moins un insight, `kpi_refs` existants, aucun nombre sans preuve.

### Exemple réel

> **Tendance** — Volume journalier moyen en hausse de 9,8 % entre la dernière année et l'année précédente.
>
> **Anomalie : toute la ville, 2026-02-24** — 460 demandes au lieu d'environ 218 attendues (z = 16,4).
> Type le plus en hausse : *Snow or Ice* (231 ce jour-là, contre 20,5 par jour) : piste à vérifier, pas une cause.
>
> **Anomalie : toute la ville, 2023-09-29** — 389 demandes au lieu d'environ 182 (z = 14,3).
> Type le plus en hausse : *Sewer Maintenance* — jour des inondations de New York.
>
> **Anomalie : BRONX, 2024-09-15** — 139 demandes au lieu d'environ 36 (z = 17,0), *Noise - Residential*.

### Scénarios alternatifs

| Cas | Comportement |
|---|---|
| Le LLM écrit « environ 500 demandes » alors que l'evidence dit 460 | texte rejeté, gabarit gardé, compté dans les `notes` (« 1 rejeté ») |
| LLM indisponible ou réponse illisible | rédaction abandonnée, tous les gabarits gardés, le run continue |
| Période de moins de 7 jours | insight « Détection d'anomalies non réalisée » à la place des anomalies |
| Borough trop petit (moins de N demandes par jour) | pas d'analyse d'anomalies sur ce borough (seuil) |

### Critères d'acceptation

Anomalie injectée retrouvée (bonne zone, bon jour) dans 100 % des cas, 0 faux positif ;
**0 insight avec un nombre non justifié**.

---

## 5. Agent Dashboard Generator

**Objectif :** décider quoi afficher et comment, sous forme d'une spécification vérifiable, sans rien
publier lui-même.
**Code :** `src/biflow/agents/dashboard_generator.py` ; affichage `src/biflow/dashboard/app.py`.

| | |
|---|---|
| Déclencheur | sortie Analyste validée |
| Entrées | KPI, ventilations, réponses, insights, anomalies |
| Sortie | `dashboard_spec.json` + son empreinte ; liste des graphiques avec leurs `query_ids` |

### Scénario nominal

1. Applique les règles de choix :

   | Donnée | Graphique |
   |---|---|
   | KPI global | carte KPI |
   | dimension temporelle (`created_day`) | courbe, jours d'anomalie marqués |
   | dimension ≤ 6 modalités | barres verticales |
   | dimension > 6 modalités | barres horizontales, top 15 |
   | réponses aux questions | table (avec mode, SQL, refus, contrôle croisé) |
   | insights | bloc texte (avec « rédigé par gabarit / LLM ») |

2. Chaque graphique chiffré garde la liste des requêtes qui l'ont produit (`query_ids`).
3. Écrit `dashboard_spec.json` et calcule son empreinte.
4. Validation : au moins un graphique, KPI référencés existants, **aucun graphique chiffré sans requête
   d'origine**.

> Exemple réel : 13 éléments — 5 cartes KPI, 4 barres verticales, 1 barre horizontale (types de plainte),
> 1 courbe journalière, 1 table de questions, 1 bloc d'insights.

### Ce que voit l'utilisateur ensuite (application web)

1. L'API (`GET /api/published`) lit `data/processed/published.json` : dernier run **publié**, jamais un run
   en échec ou non fiable.
2. Elle **recalcule l'empreinte** de la spec ; si elle ne correspond pas (ou si la spec a disparu), elle
   refuse de l'envoyer et le front affiche le motif.
3. Pages : *Tableau de bord* (« Voir le SQL » sous chaque chiffre : requête + empreinte de l'instantané),
   *Poser une question* (sur l'instantané audité, mêmes garde-fous que l'agent KPI),
   *Audit et provenance* (réserves, défauts, règles ETL, contrôles), *Historique* (tous les runs).

Le dashboard Streamlit du premier prototype (`src/biflow/dashboard/app.py`) applique les mêmes règles.

### Scénarios alternatifs

| Cas | Comportement |
|---|---|
| Spec modifiée à la main après le run | empreinte différente → l'app refuse d'afficher ; l'Auditeur l'aurait aussi signalé |
| Run final FAILED ou UNRELIABLE | `published.json` n'est pas mis à jour : le dashboard reste sur le dernier run validé |
| Aucune question posée | pas de table « questions » |

---

## 6. Agent Auditeur / XAI

**Objectif :** ne laisser publier que des chiffres prouvés, et expliquer en clair d'où ils viennent.
**Code :** `src/biflow/agents/auditor_xai.py`. C'est le code qui vérifie, pas un LLM.

| | |
|---|---|
| Déclencheur | sortie Dashboard validée |
| Entrées | tout le state (sorties des 5 agents, validations, historique) + instantané |
| Sortie | `AuditOutput` : `approved`, liste des contrôles, `findings` adressés à l'étape fautive, explication |

### Scénario nominal

1. **Données** : lignes profilées = lignes lues par l'ETL ; empreinte de l'instantané recalculée = empreinte
   enregistrée ; nombre de lignes de l'instantané = nombre annoncé.
2. **KPI** : toutes les requêtes portent sur le même instantané ; chaque KPI affiché = résultat enregistré
   de sa requête.
3. **Reproductibilité** : chaque requête est **rejouée** ; même résultat attendu.
4. **Recalcul indépendant** en pandas (sans le SQL du catalogue) de 4 KPI.
5. **Confidentialité** : aucun agrégat de moins de N demandes exposé.
6. **Insights** : chaque nombre du texte est dans les `evidence`, et les `evidence` qui citent un KPI ont
   sa valeur exacte.
7. **Dashboard** : empreinte de la spec inchangée, chaque carte et chaque ventilation affichée = valeur
   vérifiée.
8. Rédige l'explication et rend son verdict : aucun finding `error` → **SUCCESS**, le run est publié.

### Exemple réel (explication produite)

> Données : 265609 demandes lues, 139 supprimées par les règles de nettoyage (12 règles tracées), 265470
> conservées dans l'instantané 83d3805019db.
> KPI : 5 indicateurs et 6 ventilations calculés par SQL (DuckDB, lecture seule) à partir du catalogue Git ;
> 13 requêtes enregistrées et rejouées. Agrégats de moins de 10 demandes masqués.
> Insights : 10 insights, chiffres issus des KPI et de la détection d'anomalies (stl) ; rédaction : gabarits sans LLM.
> Contrôles d'audit : 53/53 réussis ; 0 nouvel(s) essai(s) d'étape pendant le run.
> Aucun écart : chaque chiffre affiché correspond à une requête enregistrée et rejouable.

### Scénario de correction — la boucle de rétroaction

Commande : `python -m biflow.run --simulate-audit-flag kpi_semantic`

1. L'Auditeur émet un finding `error` visant `kpi_semantic`.
2. L'orchestrateur renvoie le run vers **l'étape fautive la plus en amont** et y place le message dans
   `state.corrections["kpi_semantic"]`.
3. KPI & Semantic, puis Analyste, Dashboard et Auditeur sont **réexécutés**.
4. Le 2e audit ne trouve plus d'écart → **SUCCESS**, publié. Le renvoi apparaît dans les `notes` et la trace.

### Scénario de blocage — run non fiable

Commande : `python -m biflow.run --simulate-audit-flag kpi_semantic --persistent`

1. Même renvoi que ci-dessus (une seule boucle autorisée, `BIFLOW_MAX_FEEDBACK_ROUNDS`).
2. Le 2e audit trouve toujours l'écart → statut **UNRELIABLE**.
3. **Rien n'est publié** : le dashboard garde le dernier run validé.

### Autres cas

| Cas | Comportement |
|---|---|
| Instantané modifié entre ETL et audit | `empreinte_instantane` en échec → renvoi vers `quality_etl` |
| KPI affiché différent de sa requête | `kpi_egal_requete` en échec → renvoi vers `kpi_semantic` |
| Insight avec un nombre inventé | `insight_nombres` en échec → renvoi vers `analyste_bi` |
| Spec du dashboard modifiée | `empreinte_dashboard` en échec → renvoi vers `dashboard_generator` |
| Contrôle en avertissement chez un agent | rapporté comme finding `warning`, sans renvoi ni blocage |

### Critère d'acceptation

**4 écarts volontaires sur 4 détectés** dans l'évaluation, run `unreliable`, non publié.

---

## 7. Récapitulatif des commandes de démonstration

Les 4 scénarios se lancent aussi depuis l'application web (`.\scripts\tasks.ps1 web`, puis
<http://localhost:8000>, page *Scénarios*) : même pipeline, avec le suivi en direct de chaque agent.

```powershell
# Nominal : les 6 agents, puis publication
.\.venv\Scripts\python -m biflow.run --no-db --file data/raw/nyc311_2023-01_2026-09_echantillon.parquet `
    --question "Combien de demandes de bruit dans le Bronx ?" `
    --question "Pourquoi les délais sont-ils plus longs à Staten Island ?"

# Agent 2 en panne : relances puis FAILED
.\.venv\Scripts\python -m biflow.run --no-db --simulate-failure quality_etl

# Auditeur : correction réussie
.\.venv\Scripts\python -m biflow.run --no-db --simulate-audit-flag kpi_semantic

# Auditeur : blocage, rien n'est publié
.\.venv\Scripts\python -m biflow.run --no-db --simulate-audit-flag kpi_semantic --persistent

# L'application web (scénarios en direct, tableau de bord du dernier run publié)
.\scripts\tasks.ps1 web --llm ollama      # ou sans --llm : baseline à règles, plus rapide
```

| Scénario | Agents concernés | Statut final | Publié ? |
|---|---|---|---|
| Nominal | 1 → 6 | SUCCESS | oui |
| Panne d'une étape | 2 (3 tentatives) | FAILED | non |
| Écart corrigé | 6 → 3 → 4 → 5 → 6 | SUCCESS | oui |
| Écart persistant | 6 → 3 → 4 → 5 → 6 | UNRELIABLE | non |
| Question hors périmètre | 3 | refus motivé (le run continue) | — |
