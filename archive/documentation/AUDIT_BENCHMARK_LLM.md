# BIFlow — audit préalable et reconstruction du Notebook 3

Audit réalisé le **24 octobre 2026**, depuis `D:\Analyst_Agent`.
Le plan a été présenté avant la création des fichiers de benchmark. Aucun fichier
préexistant n'a été supprimé ou réécrit. Les nouvelles sorties ont leur propre dossier.

## Arborescence examinée

```text
notebooks/
  01_etl_kpi.ipynb        26 cellules, dont 13 Python
  02_bi_analyst.ipynb     27 cellules, dont 13 Python
data/raw/                deux extraits et métadonnées d'extraction
data/processed/          CSV nettoyé, résumé KPI, ventilations, catalogue
outputs/bi_analyst/      groupes, tendances, anomalies, insights et preuves
outputs/llm_benchmark/   sorties et archives de l'ancien benchmark
biflow_analyste/         ancien jeu simulé et contrats JSON
tools/ollama/            installation Ollama déjà présente
tools/ollama_models/     modèle Llama 3.2 3B déjà présent
.venv/                   Python 3.13.2 et bibliothèques d'analyse/Jupyter
```

`RESUME_PROJET.md` est **absent de l'espace de travail** ; son contenu n'a donc pas
pu être examiné. Aucun ancien fichier `03*.ipynb`, checkpoint ou source Python de
benchmark n'a été retrouvé dans les fichiers du projet examinés. Les scripts racine
`_mk.py` et `_rebuild_nb.py` sont vides ; `.tmp_run_nb.py` exécute seulement le Notebook 2.
Les fichiers du projet sont actuellement non suivis dans Git : aucun ancien Notebook 3
suivi n'est disponible via l'index pour comparaison.

## Ancien Notebook 3 : ce que prouvent ses sorties

Le diagnostic repose sur les sorties conservées, pas sur un notebook source absent :

- `outputs/llm_benchmark/benchmark_runs.csv` rapporte un appel Mistral Small avec
  **HTTP 401 : clé expirée**. Cette dépendance cloud ne convient pas au nouveau périmètre.
- L'appel `llama3.2:3b` a renvoyé un JSON syntaxiquement lisible, mais avec des clés
  différentes du contrat et **sans `scenario_id`** : statut `invalid_response`.
  Le mode JSON seul ne garantissait pas le schéma attendu.
- Le dernier rapport conserve **zéro réponse valide** et un mode `pilot`, avec deux
  tentatives sur un cas synthétique. Ce n'est pas une comparaison des trois nouveaux modèles.
- Plusieurs CSV de métriques ne contiennent aucune mesure. Les archives restent intactes.

Ces traces établissent les causes des dernières tentatives échouées. Elles ne permettent
pas d'affirmer que l'ensemble du code source disparu a été audité.

## Données disponibles et cohérence

| Élément | Constat |
|---|---|
| `data/processed/nyc311_clean.csv` | 10 000 lignes, 49 colonnes, juin à octobre 2026 |
| Délais valides | 9 285 ; 715 délais manquants ou exclus |
| Délai moyen exporté | 4,106830597189813 jours |
| Délai médian exporté | 0,18195601851851853 jour |
| Taux de fermeture exporté | 92,91 %, soit 9 291 / 10 000 dates de création valides |
| `kpi_monthly.csv` | cinq mois de l'échantillon, dont octobre partiel |
| `group_comparisons.csv` | 143 groupes, métriques et effectifs ; minimum admissible : 30 |
| `trend_analysis.csv` | quatre mois complets, volumes de population des métadonnées |
| `anomalies.csv` | zéro ligne ; historique insuffisant pour les méthodes prévues |
| `biflow_analyste/nyc311_clean.csv` | ancien jeu simulé de 41 165 lignes, année 2025 |

Les volumes d'échantillon ne sont pas les volumes de population. Le plan d'échantillonnage
déclaré utilise des fenêtres espacées et n'est pas un tirage aléatoire simple. Octobre
ne doit pas être comparé directement aux mois complets. Les dates source sans fuseau ont
été parsées comme UTC par le pipeline existant ; aucune nouvelle interprétation de fuseau
n'est introduite ici.

Le taux de fermeture compte les dates de fermeture présentes dans la cohorte, tandis que
les KPI de délai excluent les durées incohérentes : leurs populations diffèrent. Le
nouveau chargeur vérifie les valeurs selon ces définitions existantes, sans les réécrire.
La provenance NYC Open Data est celle déclarée dans le fichier local de métadonnées ;
les lignes n'ont pas été revérifiées contre une nouvelle extraction distante.

## Notebook 1 : exécution et complétude réellement vérifiées

Le Notebook 1 contient **12 sections numérotées de 1 à 12**, pas un découpage littéral
A à F. L'audit a lu les sections effectivement présentes : environnement, profilage,
nettoyage, doublons, qualité, préparation, KPI, dimensions, catalogue, visualisations,
exports/contrôles et limites. Aucun achèvement de sections historiques B à F absentes
n'est supposé à partir du nom du fichier.

Les **13 cellules Python ont été réexécutées avec succès**, dans une copie isolée des
données sous `.tmp_jupyter/audit_20261024/`, avec le Python `.venv`. Le noyau Jupyter a
réellement exécuté les cellules, graphiques et assertions ; les exports d'essai ont été
écrits uniquement dans cette copie. Les empreintes des notebooks/données/sorties existants
avant et après l'audit sont identiques. Preuve :
`.tmp_jupyter/audit_20261024/audit_execution.json`.

Points à améliorer ultérieurement, sans blocage pour le benchmark actuel :

- La description du chemin d'entrée comporte des répétitions de texte.
- Les délais par arrondissement/type et volumes par agence sont calculés en mémoire,
  mais ne sont pas tous exportés par Notebook 1. Le benchmark utilise les délais exportés
  par Notebook 2 et les valide contre le CSV nettoyé.
- Les dépendances de `requirements.txt` ne sont pas figées ; le benchmark dispose de
  son propre fichier de versions directes vérifiées.

## Agent BI Analyst / Notebook 2

Le code de l'agent est contenu dans `02_bi_analyst.ipynb`, avec fonctions déterministes
de groupes, tendances, Z-score, MAD, STL, preuves et insights. Le dernier appel est une
synthèse Llama locale, avec modèle fixé à `llama3.2:3b`.

Les **12 cellules déterministes ont été réexécutées avec succès** dans la copie isolée.
L'appel Llama de la cellule d'indice 25 a été explicitement exclu de cet audit ; son
succès historique dans `analysis_summary.json` n'est pas présenté comme une nouvelle mesure.

Points de vigilance identifiés sans modifier cet agent :

- Affectations/lectures des métadonnées répétées dans la cellule de chargement.
- Le contrôle brut de doublons lit `nyc311_sample.csv`, alors que Notebook 1 privilégie
  `nyc311_sample_jun_oct_2026.csv`. Le signal de qualité peut concerner un autre extrait.
- Le rapport qualité hérité porte sur 41 165 lignes et est correctement signalé comme
  non concordant avec les 10 000 lignes actuelles.
- `scan_monthly_anomalies()` construit `reference_period` à partir de `counts` au lieu
  du tableau `ordered` reçu. Cette dépendance extérieure est latente : le chemin ne
  s'exécute pas avec les quatre mois complets actuels. À corriger avant un historique long.
- Le scan par groupes ne reprend pas explicitement l'exclusion des mois partiels.
- La synthèse libre actuelle interdit les chiffres mais n'applique pas le contrat JSON
  proposé ici ; son remplacement devra attendre une sélection mesurée et revue.

## Environnement et ressources

| Ressource | Observation |
|---|---|
| CPU | Intel Core i5-1135G7, 4 cœurs, 8 threads |
| RAM totale | 15,75 Gio |
| RAM libre au diagnostic | environ 4,8 Gio, variable pendant le travail |
| GPU | NVIDIA GeForce MX350, 2 048 Mio de VRAM |
| Espace libre C: | environ 2,1 Gio |
| Espace libre D: | environ 740 Gio |
| Ollama | serveur actif, version 0.40.10 |
| Modèle local initial | `llama3.2:3b` uniquement |

Les trois tags demandés ont été vérifiés dans le catalogue officiel :
[Qwen3 4B](https://ollama.com/library/qwen3:4b),
[Gemma 3 4B](https://ollama.com/library/gemma3:4b),
[Granite 3.3 2B](https://ollama.com/library/granite3.3:2b).

Les modèles 4B ne sont pas assurés de tenir entièrement dans la VRAM disponible.
CPU/RAM ou offload partiel restent possibles, avec des temps à mesurer. La marge de
RAM actuellement disponible est inférieure aux seuils prudents retenus pour les modèles
4B. Variante proposée avant tout gros téléchargement : **Granite 2B d'abord**, puis les
4B un par un après libération de mémoire, poids stockés sur D:. Aucun modèle n'a été téléchargé.

Les bibliothèques requises par le nouveau benchmark sont installées. `pytest`, le SDK
Python `ollama` et `dotenv` sont absents, mais ne sont pas utilisés : tests `unittest`,
client HTTP standard et configuration sans clés. Les anciens SDK cloud restent dans
le projet sans devenir des dépendances obligatoires du benchmark local.

## Plan présenté et réalisation

1. Ajouter Notebook 3 A–I, sans toucher aux Notebooks 1/2.
2. Extraire le chargement/validation, le protocole, l'évaluation et le client dans
   `benchmark_llm/` pour permettre les tests indépendants de Jupyter.
3. Construire **27 cas : 24 fondés sur les données locales actuelles et 3 simulations
   clairement identifiées**, avec références tenues hors du prompt.
4. Figer prompt, schéma et paramètres, journaliser les appels et séparer les origines.
5. Produire CSV, JSONL, graphiques, rapport et grille humaine vide avant annotation.
6. Tester l'évaluateur, le repli synthétique et le client par simulations contrôlées,
   puis exécuter réellement Notebook 3 avec les modèles disponibles.

Le notebook s'exécute réellement de bout en bout. Les trois modèles demandés sont absents :
les **81 paires modèle/cas** sont journalisées comme indisponibles, avec **aucun score de
qualité ni latence inventé**. Les réponses des modèles demandés, performances d'inférence,
consommations pendant génération, pertinence et sélection restent **non testées**.
Les tests du chemin de génération utilisent des réponses simulées hors campagne, identifiées
comme telles et jamais prises pour des performances de modèles.

Les commandes exactes, cellules à exécuter, fichiers de sortie, grille humaine et prochaine
étape d'intégration sont documentés dans `BENCHMARK_LLM.md` et le notebook.

## Validation finale

- Notebook 3 validé au format nbformat et exécuté : **8 cellules Python sur 8**, aucune erreur.
- Tests unitaires : **37 réussis, zéro échec final**, y compris le chemin complet simulé,
  les sorties tronquées, timeouts et identité de modèle inattendue.
- Empreintes du code et des sept entrées concordantes avec le manifeste final.
- Sources des Notebooks 1/2 conservées ; anciens résultats conservés.
- Dernière sortie vérifiée : `outputs/llm_benchmark_local/20261024T123721362259Z_bf9291/`.
- Preuve machine : `validation_final.json` dans ce dossier ; détail des tests : `unit_tests.txt`.
- **Non testé : inférence des trois modèles demandés**, absents de l'installation locale.

Le premier lancement Jupyter dans le bac à sable a été refusé par Windows. L'exécution
locale autorisée hors de cette restriction a ensuite réussi. Les premiers tests de dossiers
temporaires ont également rencontré une restriction d'ACL Windows ; les fixtures héritent
désormais les permissions du dossier de travail. Ces incidents ne sont pas comptés comme
des échecs de modèles et les vérifications finales sont toutes passées.
