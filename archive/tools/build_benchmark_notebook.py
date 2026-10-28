"""Construit uniquement Notebook 3 ; sauvegarde sa version précédente s'il existe."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
cells = []


def md(source):
    cells.append(nbf.v4.new_markdown_cell(source.strip()))


def code(source):
    cells.append(nbf.v4.new_code_cell(source.strip()))


md("""
# BIFlow — Notebook 3 : benchmark LLM local

Comparer **Qwen3 4B**, **Gemma 3 4B** et **Granite 3.3 2B** pour l'Agent BI Analyst.
Les modèles interprètent des preuves calculées par le pipeline ; ils ne calculent pas les KPI.
Toutes les inférences utilisent Ollama sur cette machine, sans clé, API payante ou quota cloud.

**Exécution VS Code :** sélectionner le noyau `.venv` de ce projet, puis exécuter les cellules
dans l'ordre A → I. Les téléchargements sont désactivés par défaut. Chaque exécution crée un
nouveau dossier sous `outputs/llm_benchmark_local/` et conserve les anciennes sorties.
Si un modèle manque, le notebook s'exécute et consigne son indisponibilité, avec des scores
de qualité manquants. Cela ne constitue pas un benchmark mesuré des modèles absents.
""")
md("""
## A. Objectif et protocole expérimental

Les trois modèles reçoivent les mêmes cas dans le même ordre, le même prompt et le même
schéma JSON. Température **0**, seed **42**, contexte **4096**, sortie maximale **900 tokens**,
top_k **40**, top_p **1**, repeat_penalty **1** et raisonnement désactivé.
Un warmup identique précède chaque modèle et reste hors métriques. Les appels sont séquentiels,
sans réparation de JSON, changement automatique de paramètres ni nouvelle tentative cachée.
Les paramètres ne garantissent pas des résultats identiques entre matériels et versions.

Les cas réels proviennent des CSV validés et des métadonnées locales. Les cas artificiels
(valeur manquante, hausse et baisse anormales) sont explicitement marqués `synthetic` et
analysés séparément. Si un fichier requis manque, tout le jeu passe en simulation clairement
annoncée. Un fichier présent mais incohérent provoque une erreur, sans substitution silencieuse.

Les métriques objectives sont : disponibilité, JSON strict, schéma, exactitude des nombres
**associés à leur indicateur/groupe/unité**, sources, effectifs, rappel des preuves, respect des
limites et décisions de refus. La pertinence des insights et des actions reste une évaluation
humaine. Aucun LLM ne sert de juge. Aucun gagnant n'est désigné sans comparaison réelle complète.
""")
md("""
## B. Vérification de l'environnement

Cette cellule affiche Python, CPU, RAM disponible, GPU NVIDIA si accessible et espace disque.
Un GPU n'est pas obligatoire ; avec une petite mémoire vidéo, Ollama peut utiliser CPU et RAM.
Les marges de RAM sont des repères indicatifs, pas des exigences constructeur. Le mode
`MEMORY_POLICY="attempt"` tente chaque modèle installé, même sous ce repère, après
déchargement des modèles résidents. Une erreur mémoire réelle d'Ollama reste enregistrée.

Sur la machine auditée : environ **15,75 Gio RAM**, **4,8 Gio disponibles** et **MX350 2 Gio**.
C: est presque plein ; conserver les modèles sur D:. Commencer par Granite seul permet un
essai plus léger. Les modèles 4B sont essayés un par un après libération de la mémoire d'Ollama.
""")
code("""
from pathlib import Path
import importlib.util
import json
import os
import sys

ROOT = next((p for p in [Path.cwd(), *Path.cwd().parents]
             if (p / "benchmark_llm/protocol.py").is_file()), None)
if ROOT is None:
    raise FileNotFoundError("Ouvrez le notebook dans le projet BIFlow contenant benchmark_llm/.")
missing = [name for name in ["pandas", "numpy", "matplotlib", "psutil", "jsonschema", "nbformat", "nbclient", "ipykernel"]
           if importlib.util.find_spec(name) is None]
if missing:
    raise ImportError("Dépendances absentes : " + ", ".join(missing) +
                      ". Installer avec le Python du noyau : python -m pip install -r requirements-benchmark.txt")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from IPython.display import display, Image, Markdown
from benchmark_llm.data import load_bundle, build_cases
from benchmark_llm.protocol import MODELS, OPTIONS, SCHEMA
from benchmark_llm.runner import (OllamaClient, OllamaFailure, environment_report,
    check_registry, prepare_models, new_run_directory, run_benchmark, score_runs, visualize, write_report)

ENVIRONMENT = environment_report(ROOT)
print(json.dumps(ENVIRONMENT, ensure_ascii=False, indent=2))
RUN_MODELS = True                  # Les modèles absents sont consignés sans téléchargement.
DOWNLOAD_MODELS = False            # Lire E et le guide avant de changer cette valeur.
CHECK_REMOTE_REGISTRY = False      # Optionnel ; aucune dépendance réseau pour l'inférence.
REPETITIONS = 1                    # Mettre 3 ou davantage pour étudier la stabilité.
MODELS_TO_RUN = list(MODELS)        # Qwen, puis Gemma, puis Granite ; un seul chargé à la fois.
MEMORY_POLICY = "attempt"          # Les repères RAM avertissent sans empêcher l'essai.
RUN_UNIT_TESTS = True
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
CLIENT = OllamaClient(OLLAMA_URL, timeout=300)
""")
md("""
## C. Chargement et validation des données

Les entrées nécessaires sont le CSV nettoyé, le résumé KPI, les volumes mensuels/par borough,
les comparaisons de délais et tendances du Notebook 2, ainsi que les métadonnées d'extraction.
Les valeurs et effectifs sont revérifiés par Python contre les données nettoyées avant tout
appel LLM. Les variations officielles sont confrontées aux métadonnées de population.
Les sources locales sont identifiées et hachées ; la provenance NYC Open Data est déclarée
par les métadonnées, sans prétendre à une nouvelle vérification distante des lignes.

Les données de `biflow_analyste/` correspondent à une ancienne simulation et ne sont pas
utilisées comme observations NYC 311. Aucune ligne brute ou adresse n'est envoyée au modèle.
""")
code("""
BUNDLE = load_bundle(ROOT)
print("Origine principale :", BUNDLE["kind"])
print("Contrôles qualité :", BUNDLE["quality"])
for warning in BUNDLE["warnings"]:
    print("NOTE :", warning)
display(BUNDLE["summary"])
display(BUNDLE["groups"].head(8))
display(BUNDLE["trend"])
""")
md("""
## D. Construction des cas de test

Chaque cas expose une question, des preuves atomiques, le contexte, une référence de correction
et les critères à vérifier. Les références restent hors du prompt envoyé aux modèles.
`n` désigne la population ou le dénominateur de la preuve : demandes de l'extrait pour un volume,
délais valides du groupe pour une médiane, mois précédent pour une variation de population.

Le seuil minimal des groupes est **30 délais valides**. Une médiane sous ce minimum peut être
citée à titre descriptif, mais ne suffit pas à promouvoir le groupe comme priorité.
Le JSON conserve `null` pour une valeur manquante, sans la remplacer par zéro.

Pour les anomalies synthétiques, Python calcule le Z-score avec écart-type d'échantillon
(`ddof=1`) contre sept références antérieures et un seuil de trois écarts-types. Le modèle
reçoit le score et le statut déjà calculés. Les fichiers réels actuels ne permettent pas de
détection longue : absence de résultat ne veut pas dire absence d'anomalie.
""")
code("""
CASES = build_cases(BUNDLE)
print("Nombre de cas :", len(CASES))
display(pd.DataFrame([{k: c[k] for k in ["case_id", "category", "data_kind", "task"]} for c in CASES]))
display(pd.DataFrame(CASES).groupby(["data_kind", "category"]).size().rename("cas").to_frame())
print("Exemple de référence vérifiable (non envoyée au modèle) :")
print(json.dumps(CASES[0], ensure_ascii=False, indent=2))
""")
md("""
## E. Chargement des modèles Ollama

Identifiants vérifiés dans le catalogue officiel le **24 octobre 2026** :

| Modèle | Identifiant | Téléchargement indicatif | Marge RAM disponible du protocole |
|---|---|---:|---:|
| Qwen3 4B | `qwen3:4b` | 2,5 Go | 5,5 Gio |
| Gemma 3 4B | `gemma3:4b` | 3,4 Go | 6,5 Gio |
| Granite 3.3 2B | `granite3.3:2b` | 1,5 Go | 3,5 Gio |

Sources : [Qwen](https://ollama.com/library/qwen3:4b),
[Gemma](https://ollama.com/library/gemma3:4b),
[Granite](https://ollama.com/library/granite3.3:2b),
[API chat et schéma JSON](https://docs.ollama.com/api/chat).
Les tailles et tags peuvent évoluer : les digests locaux sont conservés dans le manifeste.
Les marges RAM du tableau déclenchent un avertissement avec le réglage par défaut
`MEMORY_POLICY="attempt"`. Le mode facultatif `"strict"` rétablit le refus préventif.
Le notebook essaie chaque modèle installé en lui réservant seul la mémoire d'Ollama.

Ollama est déjà installé dans `tools/ollama/` sur la machine auditée. Le guide
`README.md#benchmark-llm-historique` documente le protocole historique et ses commandes PowerShell.
Si le serveur est arrêté, lancer `ollama serve` dans un terminal. Si le modèle manque,
le télécharger explicitement avec `ollama pull <identifiant>` après vérification des ressources.

`DOWNLOAD_MODELS=False` empêche tout téléchargement automatique. L'option de pull intégrée
vérifie d'abord le manifeste distant, la RAM et le disque ; elle exige aussi
`BIFLOW_SERVER_STORAGE_CONFIRMED=1`, après configuration du serveur pour stocker sur D:.
Ne pas définir cette variable avant d'avoir vérifié la configuration effective du serveur.
""")
code("""
if CHECK_REMOTE_REGISTRY:
    display(pd.DataFrame(check_registry()))
try:
    INSTALLED = CLIENT.installed()
    print("Version du serveur :", CLIENT.call("version", timeout=5))
    display(pd.DataFrame([{"model": model, "installed": model in INSTALLED,
                           "digest": INSTALLED.get(model, {}).get("digest")} for model in MODELS]))
    display(pd.DataFrame(prepare_models(CLIENT, ROOT, download=DOWNLOAD_MODELS)))
except OllamaFailure as exc:
    print("Ollama indisponible :", exc.status, str(exc))
    print("Vérifier l'installation puis démarrer ollama serve. Les cellules suivantes consigneront cette indisponibilité.")
""")
md("""
## F. Exécution du benchmark

Ordre automatique : **Qwen → Gemma → Granite**. Le notebook décharge d'abord tous les
modèles résidents via `keep_alive=0` et attend que `/api/ps` soit vide. Il teste ensuite tous
les cas du modèle courant, sauvegarde ses scores, le décharge et passe au suivant.
Le suivi affiche chaque cas et le dossier de résultats du modèle.
Référence : [déchargement Ollama](https://docs.ollama.com/faq#how-do-i-keep-a-model-loaded-in-memory-or-make-it-unload-immediately).

Chaque paire modèle/cas est journalisée immédiatement dans `responses.jsonl`, y compris
les absences, erreurs mémoire, timeouts, sorties vides ou tronquées. La réponse HTTP complète
et le contenu brut sont conservés. Le manifeste fige versions, empreintes de code/données,
prompt, paramètres, schéma, matériel, digests et ordre des appels. Les agrégats et références
sont copiés dans `inputs/` ; le CSV brut n'est pas dupliqué dans les sorties.

Les réponses sont aussi sauvegardées dans `models/<modèle>/responses.jsonl`. Après chaque
modèle, ses CSV et le tableau global sont écrits avant de charger le suivant. En cas
d'interruption du noyau, les réponses déjà écrites restent sur disque ; une interruption
Python normale déclenche aussi un checkpoint et le déchargement. Les anciens dossiers restent conservés.

Un timeout ou une erreur mémoire réelle suspend les cas restants du modèle pour éviter
d'enchaîner des générations surchargées. Le modèle est déchargé même si son warmup échoue.
La latence murale inclut l'appel complet ; les durées et nombres de tokens Ollama restent
disponibles dans le journal. La durée du warmup est enregistrée séparément.
""")
code("""
OUTPUT_DIR = new_run_directory(ROOT)
print("Campagne :", OUTPUT_DIR, flush=True)
RUNS, MANIFEST = run_benchmark(ROOT, BUNDLE, CASES, CLIENT, OUTPUT_DIR,
                              run_models=RUN_MODELS, repetitions=REPETITIONS,
                              models=MODELS_TO_RUN, memory_policy=MEMORY_POLICY)
print("Sorties :", OUTPUT_DIR)
display(pd.DataFrame(RUNS).groupby(["model", "status"]).size().rename("cas").to_frame())
""")
md("""
## G. Évaluation automatique

Le JSON doit être directement valide : les blocs Markdown, clés dupliquées et valeurs
`NaN`/`Infinity` sont rejetés. Le schéma est strict. Une valeur correcte avec une mauvaise
unité, une mauvaise source, un mauvais groupe ou un effectif erroné échoue. Ne rien citer
ne donne pas une bonne note : les preuves requises doivent être présentes et exactes.
Tolérance numérique absolue : **un millionième**, sans tolérance relative sur les gros volumes.

Les chiffres sont autorisés seulement dans les champs structurés des preuves. Les chiffres
en prose déclenchent un signal et une non-conformité au protocole. Les causes explicites dans
`causal_explanation` sont refusées. Les motifs causaux en prose sont des **alertes lexicales** :
ils peuvent signaler une négation prudente et ne constituent pas un verdict sémantique.
Une revue humaine reste nécessaire pour les causes implicites et les nombres écrits en lettres.

Les appels indisponibles ont des métriques de qualité manquantes. La disponibilité garde
tous les cas planifiés dans son dénominateur. Une sortie reçue mais invalide reste un échec
de JSON/schéma et de conformité. Aucune note subjective n'est inventée.
""")
code("""
SCORES, COMPARISON = score_runs(CASES, RUNS, OUTPUT_DIR)
display(SCORES[["model", "case_id", "data_kind", "status", "json_valid", "schema_valid",
                "numeric_accuracy", "source_accuracy", "sample_size_accuracy", "objective_pass"]].head(15))

if RUN_UNIT_TESTS:
    import subprocess
    test_result = subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest", "discover",
                                  "-s", str(ROOT / "tests"), "-v"], cwd=ROOT,
                                 capture_output=True, text=True, encoding="utf-8", timeout=120)
    (OUTPUT_DIR / "unit_tests.txt").write_text(test_result.stdout + test_result.stderr, encoding="utf-8")
    print(test_result.stderr[-1500:])
    assert test_result.returncode == 0, "Tests en échec : consulter unit_tests.txt avant toute conclusion."
""")
md("""
### Grille humaine indépendante des métriques objectives

Compléter `human_review.csv` uniquement après lecture de la réponse et de ses preuves dans
`inputs/cases.json`. Les cases sont laissées vides tant qu'aucune relecture n'a eu lieu.
Relire **chaque réponse reçue**, pas seulement les alertes automatiques ; consigner le relecteur.
Si possible, masquer les noms de modèles et faire une seconde lecture indépendante des litiges.

| Critère | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| Pertinence de l'insight | Hors sujet/contradictoire | Paraphrase vague | Interprétation correcte | Interprétation utile, précise et contextualisée |
| Utilité de la recommandation | Dangereuse/sans preuve | Générique | Action d'investigation adaptée | Action concrète, proportionnée et vérifiable |
| Prudence causale | Cause affirmée sans preuve | Ambiguïté causale | Hypothèse clairement conditionnelle | Distinction nette signal/hypothèse/preuve nécessaire |
| Qualité des limites | Limites omises | Limite vague | Limites principales explicitées | Limites reliées à la décision et aux populations |

Indiquer séparément `unsupported_causal_claim_yes_no` et un commentaire justifiant les notes.
Ces quatre notes ne sont pas fusionnées automatiquement avec les métriques objectives.
""")
md("""
## H. Comparaison et visualisations

Le tableau présente les trois modèles, séparément pour les observations réelles et les
simulations. Les graphiques distinguent disponibilité, conformité objective et latence.
Une qualité ou latence sans observation reste manquante ; les graphiques le disent explicitement.
`errors_and_hallucination_flags.csv` distingue les erreurs de génération, incohérences
numériques et signaux de prose à relire. Un signal lexical n'est pas un taux mesuré d'hallucination.
""")
code("""
display(COMPARISON)
FIGURES = visualize(COMPARISON, OUTPUT_DIR)
for figure in FIGURES:
    display(Image(filename=str(figure)))
REPORT_PATH = write_report(COMPARISON, SCORES, MANIFEST, OUTPUT_DIR)
print("Rapport :", REPORT_PATH)
print("Fichiers produits :")
for path in sorted(OUTPUT_DIR.iterdir()):
    print("-", path.name)
""")
md("""
## I. Choix du modèle et limites

Le rapport ne désigne aucun modèle si les trois n'ont pas une comparaison complète sur
les données réelles. Si cette condition est satisfaite, il propose un **candidat provisoire**
selon la conformité objective puis la latence ; la sélection pour BIFlow nécessite les notes
humaines et l'examen des risques de causalité. Les résultats synthétiques ne décident pas du
gagnant réel. Une répétition ne permet pas de conclure sur la stabilité : augmenter
`REPETITIONS` pour une campagne finale et comparer les nouvelles sorties datées.

**Limites actuelles :** échantillon sélectionné par fenêtres non aléatoires ; mois partiel ;
délais calculés uniquement sur les clôtures valides ; aucune preuve causale ni cible SLA ;
historique insuffisant pour les méthodes longues. Les tags de modèles peuvent changer,
d'où la conservation des digests, versions, paramètres et empreintes de code.

**Intégration suivante :** après validation objective et humaine, rendre configurable
`BI_ANALYST_MODEL` dans le Notebook 2. Introduire un adaptateur qui envoie seulement les
preuves agrégées, applique le schéma et les vérifications de ce benchmark à la sortie,
et conserve le rapport déterministe si la génération échoue. Ne pas réutiliser une valeur
générée pour mettre à jour un KPI du pipeline.
""")
code("""
display(Markdown(REPORT_PATH.read_text(encoding="utf-8")))
print("Relecture humaine :", OUTPUT_DIR / "human_review.csv")
""")

nb = nbf.v4.new_notebook(cells=cells, metadata={
    "kernelspec": {"display_name": "BIFlow (.venv)", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3.13.2"},
})
destination = ROOT / "notebooks/03_benchmark_llm.ipynb"
if destination.exists():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = ROOT / "outputs/llm_benchmark_local/notebook_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(destination, backup_dir / ("03_benchmark_llm_"+timestamp+".ipynb"))
nbf.validate(nb)
nbf.write(nb, destination)
print(destination)
