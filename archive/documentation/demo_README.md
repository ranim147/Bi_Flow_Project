# Démonstration académique BIFlow BI Analyst

Depuis PowerShell dans `D:\Analyst_Agent` :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
.\demo\launch_demo.ps1
```

`requirements-demo.lock.txt` conserve également le graphe exact de dépendances résolu sur
Windows/Python 3.13.2 pour reproduire cet environnement. L'environnement et les empreintes
de livraison sont archivés dans `outputs\bi_analyst_demo_readiness\20261028\`.

Si la politique PowerShell bloque le script, utiliser la commande limitée à ce processus :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\demo\launch_demo.ps1
```

Ou double-cliquer sur `demo\LANCER_DEMO.cmd`. Ouvrir `http://127.0.0.1:8501`.
Arrêter avec `Ctrl+C` dans le terminal. Le serveur écoute uniquement sur l'interface locale.

## Mode conseillé pour la soutenance

L'application démarre en **RESTITUTION ENREGISTRÉE**. Aucun appel à Ollama n'est effectué
dans ce mode. Les durées affichées sont celles des exécutions d'origine, pas le temps
nécessaire à ouvrir une archive. Leur date et leur identifiant sont visibles.

Les trois résultats ordinaires proviennent d'exécutions locales réelles de Granite sur
l'extrait NYC 311 préparé. Le quatrième est un test d'injection explicitement identifié :
une réponse réelle a été volontairement altérée, puis soumise au vrai validateur et au
vrai calcul Python. Ce contenu incorrect ne doit jamais être attribué à Granite.

Les copies se trouvent dans `recordings/`, avec rapports, audits, preuves de recalcul,
métadonnées et empreintes. Elles ne nécessitent pas que le serveur Ollama soit disponible.
Le dossier complet du projet reste nécessaire au lancement Python et aux schémas.

## Quatre séquences préparées

1. **Comparer les arrondissements** : médianes, unités, effectifs, preuves et groupes exclus.
2. **Investiguer Brooklyn** : comparaison août/septembre, catégories/agences, contribution,
   hypothèses non vérifiées, filtres, données supplémentaires et observations attendues.
3. **Historique insuffisant** : refus analytique de conclure à une absence d'anomalie;
   `validated/insufficient_data` est une réponse prudente acceptée, pas un fallback.
4. **Rejet contrôlé** : wildcard, chiffre libre, cause supposée et action sans preuve sont
   bloqués. Une correction contrôlée échoue aussi, puis Python fournit un fallback fiable.

Voir [SOUTENANCE.md](SOUTENANCE.md) pour le déroulé et
[PYTHON_GRANITE_OLLAMA_VALIDATION.md](PYTHON_GRANITE_OLLAMA_VALIDATION.md) pour les rôles.
Les questions exactes sont dans [questions.json](questions.json).

## Exécution locale en direct

Vérifier l'environnement, les empreintes des entrées et la disponibilité du modèle :

```powershell
.\demo\launch_demo.ps1 -CheckOnly -CheckOllama
```

Si le serveur est arrêté, le lancer dans un autre terminal :

```powershell
& '.\tools\ollama\ollama.exe' serve
```

Le modèle retenu doit déjà être installé. Le lanceur ne télécharge rien et ne change pas
de modèle. Dans l'interface, sélectionner **Exécution Granite en direct**, choisir ou
modifier la question puis lancer explicitement l'analyse. Un clic de téléchargement ou
de navigation ne doit pas relancer une inférence.

Les nouvelles analyses sont auditées sous `outputs\demo_live\<execution_id>\`. Le test
d'injection à la demande utilise `outputs\demo_controlled_tests\` et ne lance aucun modèle.
Pour garder un autre port : `.\demo\launch_demo.ps1 -Port 8503`.

## Vérifications et nouvelle campagne

```powershell
.\.venv\Scripts\python.exe -m bi_analyst.demo_preflight --check-data --check-ollama
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m bi_analyst.demo_evaluation --root D:\Analyst_Agent --output outputs\bi_analyst_demo_readiness\ma_campagne --repetitions 2
```

Le dossier d'une nouvelle campagne doit être inexistant. Cette commande effectue douze
analyses des six scénarios existants, puis un drill-down réel supplémentaire. Les métriques
séparent réponses JSON, validation initiale, corrections, fallbacks et insuffisance justifiée.
L'injection et les restitutions ne sont pas incluses dans les métriques Granite.

Pour reconstruire les copies depuis les campagnes sources présentes :

```powershell
.\.venv\Scripts\python.exe tools/build_demo_recordings.py --refresh
```

Cette opération réécrit seulement le dossier de démonstration; elle ne modifie aucune
campagne source. Les statistiques restent descriptives, les hypothèses restent à vérifier
et le mode de raisonnement de Granite reste limité aux opérations admissibles.
