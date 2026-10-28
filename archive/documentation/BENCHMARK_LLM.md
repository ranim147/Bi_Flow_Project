# BIFlow — exécuter le benchmark local

Le notebook à ouvrir est `notebooks/03_benchmark_llm.ipynb`. Les Notebooks 1 et 2, les
données et les anciennes sorties sont conservés. Lire aussi `AUDIT_BENCHMARK_LLM.md`.

## Exécution un modèle à la fois

Le Notebook 3 teste **Qwen → Gemma → Granite**, de façon séquentielle. Avant chaque
modèle, il décharge les modèles résidents d'Ollama avec `keep_alive=0` et vérifie
que `/api/ps` est vide. Il décharge aussi le modèle en fin de passage, même après
un échec du warmup ou une interruption Python normale.

Dans la cellule B, conserver :

```python
MODELS_TO_RUN = list(MODELS)
MEMORY_POLICY = "attempt"
```

Le mode `attempt` remplace l'ancien refus préventif : les repères de RAM affichent
un avertissement mais chaque modèle installé est réellement essayé, seul. Ollama
peut encore refuser un chargement si la mémoire réelle ne suffit pas. Cette erreur
est conservée, puis le notebook passe au modèle suivant après nettoyage.
Le mode facultatif `strict` rétablit les seuils préventifs.

Les réponses sont écrites après **chaque cas**, dans le journal global et dans
`models/qwen3_4b/`, `models/gemma3_4b/` ou `models/granite3.3_2b/`.
Après **chaque modèle**, ses scores et le tableau global sont sauvegardés avant
de charger le suivant. Les résultats des modèles précédents restent donc disponibles
si la campagne s'interrompt. Un arrêt brutal du processus conserve les lignes JSONL
déjà écrites ; seuls les checkpoints déjà achevés sont alors garantis.

Pour tester un seul modèle ultérieurement, définir par exemple
`MODELS_TO_RUN = ["qwen3:4b"]`. Chaque nouvelle campagne utilise son propre dossier.
Cela ne fusionne pas automatiquement des campagnes différentes dans une comparaison.
Les températures, prompts, schémas et options de génération restent identiques pour les trois modèles.
Le délai maximal d'un appel est de 300 secondes dans le notebook.

Validation de cette modification : **45 tests unitaires réussis** et exécution des
cellules du notebook avec inférences désactivées dans une copie de validation.
Un essai technique réel sur un cas par modèle a obtenu une réponse de Qwen et de
Granite. Gemma a été tenté entre les deux et a dépassé le délai de son warmup ;
son erreur est enregistrée. Cet essai n'est pas une campagne de sélection.
Sorties : `outputs/llm_benchmark_local/20261024T144409759565Z_bebce1/`.
Si `warmup_timeout` persiste, tester Gemma seul après libération de RAM et, au besoin,
augmenter `timeout=300` dans la création de `CLIENT` en cellule B. Augmenter ce délai
ne résout pas un véritable manque de mémoire.

Après mise à jour, **redémarrer le noyau**, puis exécuter les cellules B à I dans
l'ordre afin de recharger le module Python modifié. Les anciennes mesures Granite
sous `outputs/llm_benchmark_local/` restent intactes.

Sources techniques : [déchargement et concurrence Ollama](https://docs.ollama.com/faq),
[modèles résidents](https://docs.ollama.com/api/ps).

## Installation Python et noyau VS Code

Depuis PowerShell :

```powershell
Set-Location D:\Analyst_Agent
.\.venv\Scripts\python.exe -m pip install -r requirements-benchmark.txt
.\.venv\Scripts\python.exe -m ipykernel install --user --name biflow-local --display-name "BIFlow (.venv)"
```

Dans VS Code, ouvrir le Notebook 3 et sélectionner **BIFlow (.venv)**, ou directement
`D:\Analyst_Agent\.venv\Scripts\python.exe`. Exécuter toutes les cellules **dans l'ordre A à I**.
Les bibliothèques requises sont déjà présentes dans l'environnement audité. `unittest`
fait partie de Python ; ni `pytest`, ni SDK Python Ollama, ni dotenv ne sont nécessaires.
Les versions directes sont figées dans `requirements-benchmark.txt` et les versions
effectives sont conservées dans chaque manifeste. Le protocole est testé sur Python 3.13.2.

## Ollama : installation et lancement sous Windows

Ollama **est déjà installé**, le serveur répond à `http://127.0.0.1:11434`, version
observée **0.40.10**. Le binaire local est `D:\Analyst_Agent\tools\ollama\ollama.exe`.

Pour installer sur une autre machine, ou si le binaire manque :

```powershell
Set-Location D:\Analyst_Agent
New-Item -ItemType Directory -Path .\tools -Force
Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile '.\tools\OllamaSetup.exe'
& '.\tools\OllamaSetup.exe' /DIR="D:\Analyst_Agent\tools\ollama"
```

Source : [installation Windows Ollama](https://docs.ollama.com/windows).
Le chemin sur D: tient compte du peu d'espace libre sur C:. L'installation facultative
ci-dessus n'a pas été lancée pendant la reconstruction.

Pour configurer le stockage, quitter d'abord l'application Ollama depuis son icône
de notification, puis démarrer le serveur dans un terminal PowerShell dédié :

```powershell
Set-Location D:\Analyst_Agent
$env:OLLAMA_MODELS = 'D:\Analyst_Agent\tools\ollama_models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_NO_CLOUD = '1'
& '.\tools\ollama\ollama.exe' serve
```

Ces variables s'appliquent au serveur lancé dans ce terminal. Elles ne reconfigurent
pas un serveur déjà actif. Le notebook ne peut pas déduire avec certitude le dossier
du processus serveur ; vérifier ce point avant les téléchargements. Un serveur déjà
actif sur le port convient pour consulter les modèles et exécuter le notebook.

Dans un **second terminal**, vérifier la disponibilité et la machine :

```powershell
Set-Location D:\Analyst_Agent
& '.\tools\ollama\ollama.exe' list
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version'
.\.venv\Scripts\python.exe -X utf8 -c "from pathlib import Path; import json; from benchmark_llm.runner import environment_report; print(json.dumps(environment_report(Path.cwd()),indent=2))"
.\.venv\Scripts\python.exe -X utf8 -c "from benchmark_llm.runner import check_registry; import json; print(json.dumps(check_registry(),indent=2))"
```

La dernière commande lit seulement les manifestes du registre ; elle ne télécharge
pas les poids. Si la vérification distante échoue, vérifier la connexion et consulter
les pages officielles avant de lancer un pull.

## Modèles et ressources

| Modèle demandé | Tag officiel | Taille indicative |
|---|---|---:|
| Qwen3 4B | `qwen3:4b` | 2,5 Go |
| Gemma 3 4B | `gemma3:4b` | 3,4 Go |
| Granite 3.3 2B | `granite3.3:2b` | 1,5 Go |

Tags consultés le 24/10/2026 : [Qwen](https://ollama.com/library/qwen3:4b),
[Gemma](https://ollama.com/library/gemma3:4b),
[Granite](https://ollama.com/library/granite3.3:2b). Les tailles peuvent évoluer.

La machine auditée a 15,75 Gio de RAM, environ 4,8 Gio alors disponibles, un
i5-1135G7 (4 cœurs / 8 threads) et une MX350 avec 2 Gio de VRAM. Cela impose
probablement une exécution CPU ou partiellement GPU, avec une latence à mesurer.
Les repères libres indicatifs sont **3,5 Gio pour Granite, 5,5 pour Qwen, 6,5 pour Gemma**,
contexte et cache compris. Ils ne bloquent plus l'inférence en mode `attempt`.

**Variante plus légère proposée :** commencer par Granite seul, puis libérer de la
RAM et essayer les deux modèles 4B l'un après l'autre. Le tableau restera incomplet
tant que les trois modèles n'auront pas été évalués. Aucun remplacement automatique
des modèles demandés n'est effectué. Aucun poids n'a été téléchargé lors de ce travail.

Après vérification des ressources et du stockage serveur, télécharger **Granite d'abord** :

```powershell
& 'D:\Analyst_Agent\tools\ollama\ollama.exe' pull granite3.3:2b
```

Lorsque la RAM disponible permet de poursuivre :

```powershell
& 'D:\Analyst_Agent\tools\ollama\ollama.exe' pull qwen3:4b
& 'D:\Analyst_Agent\tools\ollama\ollama.exe' pull gemma3:4b
```

Les commandes `pull` du terminal n'appliquent pas les seuils du notebook. Refaire le
diagnostic RAM avant leur usage. Le notebook ne télécharge rien tant que
`DOWNLOAD_MODELS=False`. Son option de téléchargement automatique exige aussi
`BIFLOW_SERVER_STORAGE_CONFIRMED=1`, à définir seulement après contrôle du serveur.

## Cellules à exécuter et sorties

1. **B** : dépendances, diagnostic et configuration. Garder `DOWNLOAD_MODELS=False`.
2. **C–D** : validation et construction du jeu de benchmark.
3. **E** : disponibilité locale, version et digests.
4. **F** : campagne complète, mêmes cas pour les trois modèles.
5. **G** : tests et scores déterministes ; grille humaine.
6. **H–I** : tableaux, graphiques, erreurs et conclusion conditionnelle.

Après installation des modèles, réexécuter **toutes** les cellules pour une nouvelle
campagne cohérente. `REPETITIONS=3` permet d'observer plusieurs réponses, avec un temps
total proportionnellement plus long ; les latences sur ce matériel peuvent être élevées.

Chaque campagne crée `outputs/llm_benchmark_local/<identifiant UTC unique>/` :

- `manifest.json` : matériel, versions, configuration, digests, empreintes et avertissements ;
- `inputs/` : copies des agrégats, cas, références, métadonnées et empreintes des fichiers ;
- `responses.jsonl` : réponses brutes, enveloppes Ollama, durées et erreurs, écrites immédiatement ;
- `models/<modèle>/` : journal individuel, scores CSV, tableau comparatif et progression ;
- `scores_detailed.csv` et `model_comparison.csv` : métriques objectives, origines séparées ;
- `errors_and_hallucination_flags.csv` : erreurs et signaux à relire ;
- `human_review.csv` : notes subjectives **vides** avant évaluation humaine ;
- `comparison_real.png`, `comparison_synthetic.png` : selon les origines présentes ;
- `warmups.json`, `unit_tests.txt`, `benchmark_report.md` et détails `/api/show` si modèle exécuté.

Si les modèles manquent, `responses.jsonl` contient des enregistrements d'indisponibilité,
sans réponse inventée. Les scores de qualité et latences sont vides. Une disponibilité
de zéro est une observation d'infrastructure, pas une mesure de qualité du modèle.

## Tests et erreurs

```powershell
Set-Location D:\Analyst_Agent
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

- Serveur arrêté / Ollama absent : le notebook donne la marche à suivre et journalise l'indisponibilité.
- Modèle absent : `model_absent`, pas de téléchargement implicite.
- Mémoire estimée faible : avertissement en mode `attempt`, sans exclusion préventive.
- Mémoire réellement insuffisante : erreur explicite du serveur, conservée dans les résultats.
- Timeout : erreur conservée et cas restants du modèle suspendus.
- JSON invalide / tronqué : contenu conservé, aucune réparation silencieuse.
- Fichier absent : simulation explicitement marquée ; fichier incohérent : arrêt de validation.

Les tests adversariaux fabriquent leurs propres réponses, uniquement pour éprouver
l'évaluateur. Ils ne sont jamais comptés comme performances des modèles.

## Choix puis intégration à l'Agent BI Analyst

Le rapport refuse de sélectionner un modèle si la comparaison réelle des trois est
incomplète. Après une campagne complète, examiner la conformité, les erreurs, les
latences et remplir la grille humaine (quatre critères de zéro à trois). Un candidat
objectif reste provisoire jusqu'à cette revue. Les alertes lexicales causales ne sont
pas des verdicts sémantiques ; relire toutes les réponses, pas seulement les alertes.

Après sélection, rendre `BI_ANALYST_MODEL` configurable dans le Notebook 2, remplacer
l'appel de synthèse par un adaptateur utilisant le contrat JSON et valider les valeurs
contre les preuves avant affichage. Conserver le rapport déterministe en cas d'erreur.
Cette intégration n'est pas appliquée tant qu'aucun modèle n'a été mesuré et retenu.
