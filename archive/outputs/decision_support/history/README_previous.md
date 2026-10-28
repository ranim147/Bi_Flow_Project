# BIFlow BI Analyst (prototype)

Agent BI Analyst pour le dataset préparé NYC 311. Le modèle retenu est exclusivement
`granite3.3:2b`, exécuté localement par Ollama. Python sélectionne les outils, calcule les
KPI, détermine les constats et reconstruit les preuves. Granite produit une interprétation
et une recommandation courtes dans un vocabulaire métier autorisé par le contexte.
Chaque réponse passe un contrôle Python indépendant avant publication dans le rapport.

Cette phase inclut uniquement une validation automatique. Aucune note humaine n'est
fabriquée, aucun `human_review.csv` n'est créé ou complété. Voir
[BI_ANALYST_FINAL_REPORT.md](BI_ANALYST_FINAL_REPORT.md) pour les résultats et limites.

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
0, seed 42, contexte 4096, maximum 384 tokens, `top_k=40`, `top_p=1`, répétition 1 et
`keep_alive=10m`. Le timeout d'inférence est de 180 secondes par appel, au maximum deux
appels. CPU est accepté si Ollama
n'utilise pas le GPU; une erreur mémoire ou un timeout est journalisé explicitement.

Le contrat interne `granite-draft-v2` contient seulement `finding`, `evidence_ids`,
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

Les résultats réels de cette version sont conservés sous
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
les statuts de validation, les limites et les erreurs. Le contrat externe conserve
`biflow-bi-analyst-v1`; seul le contrat interne LLM change. Voir `bi_analyst\models.py` et
les schémas JSON exportés dans la campagne finale. Un consommateur doit lire `status`,
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
