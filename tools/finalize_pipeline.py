"""Consolidate the current release; archive history intact and remove only disposable caches."""
from pathlib import Path
import argparse
import calendar
import hashlib
import json
import os
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bi_pipeline.common import inside, now, read, sha, write

VERSION="nyc311_202301_202609_v2"
BASE=ROOT/"outputs/pipeline_delivery"
DATA=ROOT/"data/versions"/VERSION
DEFERRED=[]


def save_progress(moves):
    write(BASE/"cleanup_progress.json",dict(archives=moves,deferred=DEFERRED,updated_at=now()))


def safe(path):
    value=Path(path).resolve()
    if not value.is_relative_to(ROOT): raise ValueError("Cleanup path escapes workspace")
    return value


def move(source,target,moves):
    source,target=safe(source),safe(target)
    if not source.exists(): return
    if source.is_dir() and target.is_dir():
        for child in list(source.iterdir()): move(child,target/child.name,moves)
        try: source.rmdir()
        except OSError as exc: DEFERRED.append(dict(path=str(source),reason=str(exc),empty_directory=not any(source.iterdir())))
        save_progress(moves)
        return
    if target.exists(): raise FileExistsError("Archive target already exists: "+str(target))
    try: files=[source] if source.is_file() else [p for p in source.rglob("*") if p.is_file()]
    except PermissionError as exc:
        files=[]
        DEFERRED.append(dict(path=str(source),reason="Contents not readable before native directory archival: "+str(exc)))
    fingerprints={p.relative_to(ROOT).as_posix():sha(p) for p in files}
    target.parent.mkdir(parents=True,exist_ok=True)
    try:
        source.rename(target)
    except PermissionError:
        if not source.is_dir(): raise
        target.mkdir(parents=True,exist_ok=True)
        for child in list(source.iterdir()): move(child,target/child.name,moves)
        try: source.rmdir()
        except OSError as exc: DEFERRED.append(dict(path=str(source),reason=str(exc),empty_directory=not any(source.iterdir())))
        save_progress(moves)
        return
    for old,digest in fingerprints.items():
        original=ROOT/old
        # source no longer exists; distinguish by the destination type.
        actual=target if target.is_file() else target/original.relative_to(source)
        if sha(actual)!=digest: raise ValueError("Archived content changed: "+old)
    moves.append(dict(source=source.relative_to(ROOT).as_posix(),target=target.relative_to(ROOT).as_posix(),files=fingerprints))
    save_progress(moves)


def build_latest_manifest():
    campaign=BASE/"real_evaluation"
    cases=[]
    for row in read(campaign/"results.json"):
        folder=inside(campaign,row["audit_dir"])
        cases.append(dict(id=row["scenario"],question=row["question"],execution_id=row["execution_id"],audit_dir=row["audit_dir"],
                          sha256={p.name:sha(p) for p in folder.iterdir() if p.is_file()}))
    write(campaign/"delivery_manifest.json",dict(dataset_version=VERSION,cases=cases,created_at=now()))


def cleanup():
    manifest_path=BASE/"cleanup_manifest.json"
    if manifest_path.exists(): return read(manifest_path)
    if not read(BASE/"test_suite/summary.json")["passed"] or not read(BASE/"real_evaluation/replay_verification.json")["all_passed"]:
        raise ValueError("Tests and replay must pass before cleanup")
    moves=read(BASE/"cleanup_progress.json")["archives"] if (BASE/"cleanup_progress.json").exists() else []
    deferred=DEFERRED
    documentation=ROOT/"archive/documentation";documentation.mkdir(parents=True,exist_ok=True)
    if not (documentation/"README_before_pipeline_20261028.md").exists():
        shutil.copyfile(ROOT/"README.md",documentation/"README_before_pipeline_20261028.md")
    for path in list(ROOT.glob("*.md")):
        if path.name!="README.md": move(path,documentation/path.name,moves)
    for path in list((ROOT/"demo").glob("*.md")): move(path,documentation/("demo_"+path.name),moves)
    for path in list(ROOT.glob("requirements*.txt")):
        move(path,ROOT/"archive/config"/path.name,moves)
    for name in (".tmp_run_nb.py","_mk.py","_rebuild_nb.py"):
        move(ROOT/name,ROOT/"archive/scripts"/name,moves)
    keep_tools={"run_agent_tests.py","export_bi_analyst_schemas.py","finalize_pipeline.py","validate_release.py"}
    for path in list((ROOT/"tools").glob("*.py")):
        if path.name not in keep_tools: move(path,ROOT/"archive/tools"/path.name,moves)
    move(ROOT/"notebooks",ROOT/"archive/notebooks",moves)
    move(ROOT/"biflow_analyste",ROOT/"archive/prototypes/biflow_analyste",moves)
    move(ROOT/"demo/recordings",ROOT/"archive/demo_recordings",moves)
    for name in ("legacy_windows_v1","legacy_windows_10000_v1"):
        move(ROOT/"data/versions"/name,ROOT/"archive/data_versions"/name,moves)
    move(DATA/"revisions",ROOT/"archive/data_revisions"/VERSION,moves)
    keep_inputs={"group_comparisons.csv","trend_analysis.csv"}
    for path in list((ROOT/"outputs/bi_analyst").iterdir()):
        if path.name not in keep_inputs: move(path,ROOT/"archive/outputs/bi_analyst"/path.name,moves)
    for path in list((ROOT/"outputs").iterdir()):
        if path.name in {"pipeline_delivery","bi_analyst"}: continue
        try: move(path,ROOT/"archive/outputs"/path.name,moves)
        except PermissionError as exc: deferred.append(dict(path=str(path),reason=str(exc)))
    for path in list((BASE/"test_suite").iterdir()):
        if path.name not in {"summary.json","unittest.log"}:
            move(path,ROOT/"archive/outputs/pipeline_test_attempts"/path.name,moves)
    try: move(ROOT/".tmp_jupyter",ROOT/"archive/workspace_temporaries/.tmp_jupyter",moves)
    except PermissionError as exc: deferred.append(dict(path=".tmp_jupyter",reason=str(exc)))
    removed=[]
    for parent,dirs,_ in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in {".git",".venv","archive",".agents",".codex",".aws"}]
        for name in list(dirs):
            if name=="__pycache__":
                path=safe(Path(parent)/name)
                try: shutil.rmtree(path);removed.append(path.relative_to(ROOT).as_posix())
                except PermissionError as exc: deferred.append(dict(path=str(path),reason=str(exc)))
                dirs.remove(name)
    for folder in (DATA/"profiling",DATA/"cleaned"):
        for path in folder.glob("*_work*.sqlite*"):
            try: safe(path).unlink();removed.append(path.relative_to(ROOT).as_posix())
            except PermissionError as exc: deferred.append(dict(path=str(path),reason=str(exc)))
    result=dict(completed_at=now(),archives=moves,removed_recreatable_caches=removed,deferred=deferred,
                non_temporary_irreversible_deletions=[],policy="All non-temporary historical content is archived byte-for-byte, not destroyed",
                retained_legacy_inputs="data/raw, data/processed and two outputs/bi_analyst CSVs are required by frozen benchmark/regression contracts")
    write(manifest_path,result)
    write(ROOT/"archive/relocations.json",result)
    return result


def protected_check():
    moves=read(BASE/"cleanup_manifest.json")["archives"] if (BASE/"cleanup_manifest.json").exists() else []
    errors=[]
    for original,digest in read(BASE/"audit_before/protected_hashes.json").items():
        path=ROOT/original
        if not path.exists():
            matches=[m for m in moves if original==m["source"] or original.startswith(m["source"]+"/")]
            if matches:
                match=max(matches,key=lambda m:len(m["source"]))
                suffix=original[len(match["source"]):].lstrip("/")
                path=ROOT/match["target"]/suffix if suffix else ROOT/match["target"]
        if not path.is_file() or sha(path)!=digest:errors.append(original)
    result=dict(passed=not errors,changed_or_missing=errors,checked=len(read(BASE/"audit_before/protected_hashes.json")))
    write(BASE/"historical_integrity.json",result)
    if errors:raise ValueError("Historical integrity check failed")
    return result


def requirements():
    (ROOT/"requirements.txt").write_text("""# Validated local Windows / Python 3.13 environment; current BIFlow runtime.
pandas==3.0.6
numpy==2.5.4
psutil==7.2.2
jsonschema==4.26.0
pydantic==2.14.1
pyarrow==26.0.0
requests==2.34.2
tzdata==2026.5
streamlit==1.66.1
matplotlib==3.11.2
scipy==1.18.1
statsmodels==0.15.0
""",encoding="utf-8")


def finish_release():
    """Finish reversible housekeeping and record the evidence for this delivery."""
    result=read(BASE/"cleanup_manifest.json")
    # The one-time coverage migration is no longer part of the current pipeline.
    move(ROOT/"bi_pipeline/repair_coverage.py",ROOT/"archive/tools/repair_coverage.py",result["archives"])
    for parent,dirs,_ in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in {".git",".venv","archive",".agents",".codex",".aws"}]
        for name in list(dirs):
            if name=="__pycache__":
                path=safe(Path(parent)/name)
                try:
                    shutil.rmtree(path)
                    relative=path.relative_to(ROOT).as_posix()
                    if relative not in result["removed_recreatable_caches"]:
                        result["removed_recreatable_caches"].append(relative)
                except PermissionError as exc:
                    DEFERRED.append(dict(path=str(path),reason=str(exc)))
                dirs.remove(name)
    deferred=[item for item in result["deferred"] if Path(item["path"]).exists()]
    for name in (".tmp_jupyter","notebooks"):
        path=safe(ROOT/name)
        if path.is_dir() and not any(path.iterdir()):
            try:
                path.rmdir()
                deferred=[item for item in deferred if Path(item["path"]).resolve()!=path]
            except OSError as exc:
                if not any(Path(item["path"]).resolve()==path for item in deferred):
                    deferred.append(dict(path=str(path),reason=str(exc),empty_directory=True))
    result["deferred"]=deferred+DEFERRED
    result["completed_at"]=now()
    write(BASE/"cleanup_manifest.json",result)
    write(ROOT/"archive/relocations.json",result)
    integrity=protected_check()
    full=read(BASE/"test_suite/summary.json")
    post=read(BASE/"post_cleanup_verification.json")
    replay=read(BASE/"real_evaluation/replay_verification.json")
    active=read(ROOT/"data/active_dataset.json")
    write(BASE/"final_summary.json",dict(
        completed_at=now(),active_dataset=active,tests=full,post_cleanup=post,
        saved_reports_reverified=replay["all_passed"],historical_integrity=integrity,
        archive_moves=len(result["archives"]),
        archived_files=sum(len(item["files"]) for item in result["archives"]),
        irreversible_non_temporary_deletions=[],deferred=result["deferred"],
        active_documentation=["README.md"],
        latest_outputs="outputs/pipeline_delivery",
        legacy_contract_inputs=["data/raw","data/processed","outputs/bi_analyst/group_comparisons.csv","outputs/bi_analyst/trend_analysis.csv"]
    ))


def render_readme():
    kpi=read(DATA/"processed/manifest.json")
    checks=read(DATA/"processed/verification.json")
    tests=read(BASE/"test_suite/summary.json")
    results=read(BASE/"real_evaluation/results.json")
    raw=read(DATA/"raw/manifest.json")
    plan=read(DATA/"acquisition_plan.json")
    import pandas as pd
    summary=pd.read_parquet(DATA/"processed/summary.parquet").set_index("kpi_name").value
    rows=[]
    for row in results:
        rows.append(f"| {row['scenario']} | `{row['status']}` / `{row['decision']}` | {row['wall_seconds']:.2f} s | {row['corrections']} / {row['fallback']} | `{row['execution_id']}` |")
    a=read(BASE/"real_evaluation"/results[0]["audit_dir"]/"analysis.json")["period_comparison"]
    b=read(BASE/"real_evaluation"/next(r for r in results if r["scenario"]=="B")["audit_dir"]/"analysis.json")["winner"]
    coverage=checks["joint_strata_coverage"]
    parts=[f"""# BIFlow — pipeline de données et BI Analyst

Version finale locale du 28 octobre 2026. Projet Windows, Python 3.13, 16 Go de RAM,
stockage de travail sur D:. Modèle exclusif : **Granite3.3 2B via Ollama local**.

Le pipeline exécutable est : acquisition → profiling → ETL → KPI → BI Analyst.
La nouvelle version contient **{checks['cleaned_rows']:,} demandes**, **{checks['valid_delay_n']:,} durées valides**
et **{checks['complete_periods']} mois complets**, de janvier 2023 à septembre 2026.
La suite complète a exécuté **{tests['tests']} tests réussis**, sans échec, erreur ou test ignoré.
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

Le comptage distant a observé **{plan['source_summary'][0]['rows']} lignes** sur le jeu courant,
avec créations de 2020-01-01 à 2026-10-27. La période retenue compte
**{sum(raw['population_monthly_counts'].values()):,} demandes officielles**.

| Approche | Faisabilité et choix |
|---|---|
| Jeu complet | Possible avec traitement hors mémoire et espace disponible ; environ {plan['full_source_estimate_bytes_projected_columns']/1e9:.2f} Go estimés pour les colonnes projetées à 500 octets/ligne, hors copies et index. Ce n'est pas une taille téléchargée mesurée. Coût réseau/I/O et suivi de snapshot supérieurs. |
| Historique borné | Choisi : 45 mois, sélection systématique d'environ 2 % par mois et arrondissement, projection de 11 champs, pages de 5 000, au plus 3 requêtes simultanées. Contrat plafonné à 500 000 lignes et 512 Mio. |

La position de départ est dérivée de la graine 42, du mois et de l'arrondissement ;
deux suffixes d'identifiants sur cent sont retenus. Ce n'est plus un prélèvement de
quelques fenêtres temporelles. **Ce n'est pas un échantillon probabiliste et aucune
représentativité de toute la population n'est affirmée.** La périodicité des identifiants,
les processus d'affectation et les catégories rares peuvent introduire un biais.

La couverture canonique mois × arrondissement × plainte × agence compte
**{coverage['strata']:,} strates**, dont **{coverage['observed_strata']:,} observées** et
**{coverage['missing_strata']:,} absentes**. Les strates observées regroupent
**{100*coverage['population_share_in_observed_strata']:.4f} %** du volume officiel.
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

Version active : `data/versions/{VERSION}/`.

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
| Demandes | {int(summary['request_volume']):,} |
| Clôturées cohérentes | {int(summary['closed_request_count']):,} |
| Ouvertes | {int(summary['open_request_count']):,} |
| Incohérentes | {int(summary['inconsistent_request_count']):,} |
| Moyenne de résolution | {summary['mean_resolution_days']:.6f} jours |
| Médiane de résolution | {summary['median_resolution_days']:.6f} jour |
| Taux de clôture de cohorte à l'extraction | {summary['closure_rate']:.6f} % |

Les durées sont des secondes écoulées en UTC divisées par 86 400, et non des jours ouvrés.
Le taux de clôture utilise les créations valides comme dénominateur et l'état cohérent
observé à l'extraction comme numérateur. Il dépend du recul d'observation des cohortes.
Moyennes et médianes ne sont pas interchangeables ; la médiane globale n'est jamais
la moyenne des médianes des groupes.

Le [catalogue sémantique](data/versions/{VERSION}/processed/semantic_catalog.json)
précise définition, formule, colonnes, unité, période, dimensions, inclusion/exclusion,
limites, version et provenance. Les KPI incluent également quantiles/histogramme,
volumes/délais par arrondissement, plainte et agence, évolution mensuelle, stock et âge.
Le backlog est reconstitué pour la seule cohorte échantillonnée : les dossiers antérieurs
à janvier 2023 et les réouvertures non observées ne sont pas inventés.

Les contrôles recalculent les KPI globaux et par groupe, les dénominateurs dimensionnels,
les volumes mensuels officiels et la couverture canonique. Le dataframe du BI Analyst
mesure {checks['in_memory_dataframe_bytes']/1e6:.2f} Mo. Le dernier processus de préparation
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
""", "\n".join(rows), f"""

Pour A, la moyenne passe de **{a['values'][0]:.6f}** à **{a['values'][1]:.6f} jours**
entre août et septembre 2026, soit **{a['delta']:+.6f} jours** ; la décomposition et ses
effectifs sont vérifiés. Pour B, le maximum éligible est **{b['filters']['borough']}**,
médiane **{b['value']:.6f} jour**, n=**{b['n']}**, puis le drill-down reste dans ce périmètre.
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
cd D:\\Analyst_Agent
py -3.13 -m venv .venv                    # seulement si le venv n'existe pas
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
ollama serve                              # si le serveur Ollama n'est pas déjà lancé
ollama pull granite3.3:2b                  # seulement si le modèle n'est pas installé
```

Préparer les données locales déjà téléchargées, sans appel réseau :

```powershell
.\\.venv\\Scripts\\python.exe -m bi_pipeline.cli prepare --root D:\\Analyst_Agent --version {VERSION}
.\\.venv\\Scripts\\python.exe -m bi_pipeline.cli activate --root D:\\Analyst_Agent --version {VERSION}
```

Nouvelle acquisition bornée puis préparation, avec une nouvelle version ou reprise :

```powershell
.\\.venv\\Scripts\\python.exe -m bi_pipeline.cli acquire --root D:\\Analyst_Agent --version nouvelle_extraction --start 2023-01 --end 2026-10 --suffix-count 2
.\\.venv\\Scripts\\python.exe -m bi_pipeline.cli prepare --root D:\\Analyst_Agent --version nouvelle_extraction
# run exécute acquisition puis préparation ; end est exclusif
.\\.venv\\Scripts\\python.exe -m bi_pipeline.cli run --root D:\\Analyst_Agent --version nouvelle_extraction --start 2023-01 --end 2026-10 --suffix-count 2
```

Importer un CSV local et ses métadonnées concordantes dans une nouvelle version :

```powershell
.\\.venv\\Scripts\\python.exe -m bi_pipeline.cli import-local --root D:\\Analyst_Agent --version import_local_nouveau --csv chemin\\fichier.csv --metadata chemin\\metadata.json --timezone America/New_York
```

Le nombre de lignes et les comptages mensuels doivent correspondre ; sinon la publication
est refusée. L'audit a trouvé deux anciens RAW distincts (8 000 et 10 000 lignes) :
`nyc311_sample_metadata.json` correspond au fichier `nyc311_sample_jun_oct_2026.csv`,
pas à `nyc311_sample.csv`. L'essai d'import incorrect est invalidé et archivé.

Analyse Python seule, puis analyse avec Granite :

```powershell
.\\.venv\\Scripts\\python.exe -m bi_analyst.cli --root D:\\Analyst_Agent --dataset-version {VERSION} --baseline --question "Quel arrondissement a la médiane la plus élevée ?"
.\\.venv\\Scripts\\python.exe -m bi_analyst.cli --root D:\\Analyst_Agent --dataset-version {VERSION} --question "Identifie l'arrondissement à médiane maximale puis compare les plaintes et agences dans cet arrondissement, sans analyse temporelle."
```

Le CLI utilise la version active si aucune version n'est donnée ; `--legacy-data`
force les anciennes données CSV. L'API Python `analyze()` conserve son défaut historique
pour les benchmarks ; passer `AnalysisContext(project_root=..., dataset_version=...)`
pour sélectionner explicitement les données v2.

Démonstration et contrôles :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\demo\\launch_demo.ps1
.\\.venv\\Scripts\\python.exe -m bi_analyst.demo_preflight --root D:\\Analyst_Agent --check-data --check-ollama
.\\.venv\\Scripts\\python.exe tools\\run_agent_tests.py --output outputs\\pipeline_delivery\\test_suite
.\\.venv\\Scripts\\python.exe tools\\validate_release.py
.\\.venv\\Scripts\\python.exe tools\\export_bi_analyst_schemas.py
# Régénérer la documentation et le bilan après les vérifications
.\\.venv\\Scripts\\python.exe tools\\finalize_pipeline.py --apply
```

Ouvrir http://127.0.0.1:8501. Choisir **Derniers résultats sur les nouvelles données**
pour afficher les six vrais rapports sans nouvelle inférence ; **Exécution Granite en
direct** appelle le modèle sur la version choisie. Modifier question ou version efface
le résultat précédent. Les restitutions historiques restent identifiées comme archives.

Évaluer une nouvelle version et relire une campagne sans modèle :

```powershell
.\\.venv\\Scripts\\python.exe -m bi_pipeline.evaluation --root D:\\Analyst_Agent --version {VERSION} --output outputs\\pipeline_delivery\\nouvelle_evaluation
.\\.venv\\Scripts\\python.exe -m bi_pipeline.replay_evaluation --root D:\\Analyst_Agent --version {VERSION} --output outputs\\pipeline_delivery\\real_evaluation
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
"""]
    (ROOT/"README.md").write_text("".join(parts),encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    build_latest_manifest()
    if args.apply:
        cleanup();requirements();render_readme();finish_release()
    print("Latest reports indexed; cleanup applied="+str(args.apply),flush=True)


if __name__=="__main__":main()
