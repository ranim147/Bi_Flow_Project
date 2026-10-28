# BIFlow BI Analyst — préparation de la démonstration académique

Date : 28 octobre 2026. Modèle exclusif : **`granite3.3:2b` via Ollama local**.

## État de préparation

La démonstration locale est prête dans le périmètre décrit ci-dessous : interface
Streamlit, quatre séquences préparées, restitutions vérifiées, accès au rapport et aux
audits, tests et campagne réelle séparée. Elle ne constitue pas une validation humaine
de l'utilité métier ni une intégration des autres agents.

| Élément | État et preuve |
| --- | --- |
| Interface `analyze(question, context)` | Conservée, voies descriptives v1 et investigation v2 testées |
| Groupes, catégories, agences, périodes | Outils implémentés, calculs réels et tests métier |
| Contributions | Identité exacte de décomposition de moyenne, composition et groupes non appariés séparés |
| Tendances et anomalies | Algorithmes présents; prérequis temporels stricts; les données réelles actuelles restent insuffisantes pour ces tests |
| Effectifs et limites | Seuils, exclusions, durées invalides, échantillon et périodes partielles conservés |
| Recommandations | Plans contextualisés : périmètre, filtres, données supplémentaires, priorités et critères de vérification |
| Fiabilité LLM | JSON contraint, contrôles Python indépendants, une correction, puis fallback explicite |
| Interface académique | Streamlit indépendante du Dashboard Generator; dix tests AppTest réussis |
| Restitution | Trois exécutions Granite authentiques copiées, plus une injection contrôlée distincte; aucune inférence pendant la lecture |
| Contrats de consommateurs | Trois vues simulées, neuf tests; aucun autre agent développé |
| Suite complète | **142 tests réussis**, dont les régressions historiques |
| Lancement Windows | Script PowerShell et fichier `.cmd` exécutables depuis le dossier du projet |
| Serveur local | Le lanceur a servi `/` et `/_stcore/health` avec HTTP 200; processus temporaire arrêté après vérification |

## Commandes exactes

Dans PowerShell, depuis `D:\Analyst_Agent` :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\demo\launch_demo.ps1
```

Ouvrir **http://127.0.0.1:8501**. L'application démarre en **Restitution enregistrée**.
Le fichier `demo\LANCER_DEMO.cmd` fournit aussi un lancement par double-clic.
Le changement de politique PowerShell est limité au processus lancé; aucune politique
globale du poste n'est modifiée.

Précontrôle complet, sans génération :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\demo\launch_demo.ps1 -CheckOnly -CheckOllama
```

Cette commande vérifie Streamlit, les quatre archives, les contrats, les empreintes des
données et la disponibilité de Granite. Le modèle n'est pas téléchargé automatiquement.
Pour le mode direct, Ollama doit être démarré et Granite déjà installé.

Pour lancer un autre port :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\demo\launch_demo.ps1 -Port 8503
```

Arrêter le serveur avec `Ctrl+C`. Il écoute uniquement sur `127.0.0.1`.

## Les quatre démonstrations

### Comparaison réelle des délais par arrondissement

Question : « Compare les délais de résolution par arrondissement. »

Montrer les outils de qualité et comparaison, la médiane des durées valides, les effectifs,
les sources et les groupes exclus. La médiane descriptive la plus élevée concerne Staten
Island et la plus faible le Bronx dans l'extrait préparé. Ne pas en déduire une cause,
un jugement d'efficacité globale ou un dépassement de SLA.

### Drill-down réel à Brooklyn

Question : « Investiguer la hausse des délais à Brooklyn; préciser les catégories,
agences et périodes prioritaires. »

La comparaison porte sur les clôtures d'août et septembre 2026. La moyenne de Brooklyn
passe d'environ 3,922 à 7,318 jours, tandis que sa médiane baisse d'environ 0,297 à
0,220 jour. La décomposition ne concerne donc pas une dégradation uniforme des délais.

Le segment HPD / `UNSANITARY CONDITION` compte 37 clôtures valides dans chaque période.
Sa contribution nette descriptive est d'environ 0,648 jour à la variation de moyenne.
Le plan cible les dossiers de cette catégorie et agence, les journaux d'affectation et
de traitement, puis la complexité et la priorité des dossiers. Il indique ce qu'il faudrait
observer pour soutenir ou écarter l'hypothèse; aucune cause n'est présentée comme prouvée.

### Historique insuffisant

Question : « Peut-on conclure à l'absence d'anomalie avec cet historique mensuel ? »

Montrer les quatre mois complets disponibles et les seuils requis. Une anomalie mensuelle
nécessite au moins sept mois contigus; le test saisonnier de tendance exige au moins
trente-six mois complets. L'agent retourne une réponse acceptée par les contrôles,
avec `decision=insufficient_data`. Une détection non exécutable ne démontre pas l'absence
d'anomalie. Cette prudence est distincte d'un fallback LLM.

### Rejet contrôlé d'une réponse incorrecte

Le contenu incorrect est **injecté volontairement à partir d'une réponse réelle archivée**.
Il n'est pas une réponse spontanée de Granite et aucun modèle n'est appelé pendant ce test.
L'entrée contient un wildcard, un chiffre inventé, une causalité supposée et une action
non étayée. Les deux validations rejettent l'entrée puis le fallback Python reconstruit
un rapport factuel. Le journal porte un marqueur `controlled_fault_injection`.

Deux modalités sont possibles : restituer ce test déjà enregistré, ou l'exécuter à nouveau
avec le vrai validateur et les calculs Python. L'interface les identifie comme tels. Ce
test et les restitutions sont exclus des taux de réussite Granite.

## Ce que l'interface montre

La zone de question est éditable en mode direct. Les scénarios prédéfinis fixent les
questions des restitutions. Les onglets montrent synthèse, outils, preuves, plans
d'investigation, limites, validation, tentatives, corrections, fallback, temps et JSON.
Les téléchargements fournissent rapport JSON, audit JSONL et analyse détaillée lorsqu'elle
existe. Une navigation ou un téléchargement ne déclenche pas une nouvelle inférence.

Chaque résultat indique sa question réellement analysée, son identifiant, sa date et son
origine. En restitution, le temps montré est celui de l'exécution originale. Le résultat
n'est jamais réétiqueté « direct » parce que le sélecteur de mode a changé.

Les réponses brutes rejetées sont visibles uniquement dans une section d'audit explicitement
signalée; elles ne deviennent pas des conclusions acceptées. Les limites et les décisions
`insufficient_data` ou `refuse` restent visibles.

## Évaluation finale réelle

La nouvelle campagne exécute deux répétitions des six scénarios existants avec Granite,
puis un drill-down supplémentaire. Les résultats sont écrits dans un nouveau dossier;
aucune campagne ancienne n'est réécrite.

Résultats de la campagne nouvelle : **six scénarios distincts répétés deux fois**, soit
douze analyses, puis **un drill-down réel supplémentaire**. Au total : sept questions
distinctes, treize exécutions `analyze()` et treize réponses réellement reçues d'Ollama.

| Mesure | Six scénarios × deux répétitions | Drill-down supplémentaire |
| --- | ---: | ---: |
| Exécutions | 12 | 1 |
| JSON bruts valides | 12/12, 100 % | 1/1, 100 % |
| Réponses avec références correctes | 12/12, 100 % | 1/1, 100 % |
| Granite accepté dès le premier essai | 12/12, 100 % | 1/1, 100 % |
| Granite accepté après correction | 0/12, 0 % | 0/1, 0 % |
| Fallback Python | 0/12, 0 % | 0/1, 0 % |
| Échec technique | 0 | 0 |
| Conclusions prudentes `insufficient_data` | 4 | 0 |
| Signaux d'affirmations non vérifiées bloqués | 0 | 0 |
| Contrats et preuves recalculées corrects | 12/12 | 1/1 |
| Temps moyen | 26,42 s | 47,06 s |
| Temps médian | 24,61 s | 47,06 s |

Sur l'ensemble des treize exécutions réelles, le temps moyen est de **28,00 s** et le
temps médian de **24,62 s**. La première analyse inclut un chargement à froid; les suivantes
réutilisent le modèle lorsque sa résidence en mémoire le permet.

Ces taux concernent les sorties structurées contraintes et les contrôles décrits, pas
une note de qualité humaine ou une capacité générale de raisonnement libre. Aucune
correction n'a été nécessaire dans cette campagne; la procédure de correction et de
fallback est néanmoins éprouvée dans les tests et l'injection distincte.

| Scénario existant | Répétition un | Répétition deux | Décision dans les deux cas |
| --- | ---: | ---: | --- |
| Délais par arrondissement | 59,66 s | 23,74 s | `answer` |
| Types de plaintes | 27,02 s | 17,12 s | `answer` |
| Anomalie mensuelle | 26,04 s | 21,03 s | `insufficient_data` |
| Comité de pilotage | 20,17 s | 24,59 s | `answer` |
| Données manquantes | 24,62 s | 17,54 s | `answer` |
| Absence d'anomalie avec cet historique | 29,04 s | 26,42 s | `insufficient_data` |

Les résultats détaillés et tous les rapports effectivement obtenus sont dans
[`granite_campaign/metrics.json`](outputs/bi_analyst_demo_readiness/20261028/granite_campaign/metrics.json)
et son dossier `audit/`. Le journal de suite est
[`tests_full.log`](outputs/bi_analyst_demo_readiness/20261028/tests_full.log).

Les trois restitutions authentiques du dossier `demo/recordings/` proviennent des campagnes
antérieures indiquées dans leurs métadonnées et ont été revérifiées. Elles sont distinctes
de cette nouvelle campagne : ni leur chargement ni l'injection n'ajoutent une exécution au
dénominateur des treize appels réels ci-dessus.

### Définition des métriques

Le taux de JSON valides porte sur toutes les réponses brutes réellement reçues d'Ollama,
corrections incluses. Le taux de références correctes utilise le même dénominateur; un
schéma illisible échoue conservativement au contrôle des références.

Les taux d'acceptation initiale, d'acceptation après correction et de fallback portent sur
des exécutions complètes `analyze()`. Pour un rapport multi-problèmes, tous les problèmes
doivent avoir passé la première tentative pour compter comme réussite initiale. Les taux
initial et après correction sont exclusifs. Un `validated/insufficient_data` est compté
séparément d'un échec de génération.

Les « affirmations non vérifiées bloquées » sont mesurées comme des catégories de signaux
du validateur par réponse : preuve inconnue, chiffre ou causalité en prose, recommandation,
interprétation, constat ou périmètre non étayé. Ce nombre n'est pas une mesure sémantique
universelle et plusieurs signaux peuvent concerner une même phrase. Le journal conserve
les erreurs exactes.

Dans le test injecté, les deux réponses sont rejetées : dix signaux sont enregistrés,
correspondant à cinq catégories répétées sur les deux tentatives. Ils ne sont pas ajoutés
aux résultats du modèle et ne représentent pas dix affirmations naturelles distinctes.

La moyenne et la médiane utilisent `report.timing_ms.total`, avec chargement et corrections
éventuels. Les temps d'analyse, inférence, chargement Ollama et validation restent
disponibles dans chaque rapport et audit. Les durées ne sont pas des garanties de production.

## Vérifications exécutées

- Suite complète de 142 tests, comprenant les huit comportements métier précédemment
  demandés, les contrôles stricts et les régressions statistiques.
- Dix tests AppTest : les quatre archives réelles ont été affichées avec construction du
  client Ollama interdite; modes, insuffisance, fallback, téléchargements et erreurs sont couverts.
- Huit tests du service de démonstration : empreinte altérée, chemin hors dossier, origine
  d'injection masquée, test contrôlé et routage du direct.
- Neuf tests des consommateurs simulés : 17 rapports historiques v1/v2 acceptés; références,
  statuts, données mixtes et plans corrompus rejetés.
- Six tests analytiques indépendants supplémentaires : identité et réversibilité de la
  décomposition, partition des lignes, frontières de backlog et vérification réelle des
  prérequis et de la divergence moyenne/médiane.
- Quatre tests des dénominateurs et du calcul des métriques; aucune performance simulée
  n'est publiée dans la campagne réelle.
- Précontrôle complet du lanceur, démarrage headless par le script Windows et HTTP 200
  sur la page et le point de santé. Le serveur de test a ensuite été arrêté.

AppTest vérifie l'application simulée et ses éléments; le contrôle HTTP vérifie le serveur.
Ce travail ne comprend pas de revue visuelle humaine de la page ni de notation par le
professeur. Voir la [documentation officielle AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest).

## Contrat destiné aux autres agents

`BIAnalysisReport` conserve les contrats v1 et v2. Les questions descriptives émettent v1;
les investigations émettent v2 avec `decision_support`. `to_legacy_dict()` fournit une
projection explicite pour un consommateur strict v1 et refuse de faire passer du
synthétique pour du réel.

Les adaptateurs de `bi_analyst/consumers.py` produisent seulement des vues de lecture
simulées pour l'Orchestrateur, le Dashboard Generator et le BI Auditor/XAI. Ils contrôlent
le schéma, les références et les associations; le recalcul des statistiques est assuré
par les vérifications de campagne. Ils n'implémentent et n'appellent aucun autre agent.

## Limites restantes et points à corriger avant un usage plus large

- Le routage et la reconnaissance des filtres restent lexicaux. Les dates arbitraires
  écrites dans une question ne sont pas analysées; le drill-down utilise automatiquement
  les deux derniers mois complets contigus.
- Granite assemble des opérations admissibles à partir de preuves. Il ne produit pas de
  raisonnement libre universellement vérifié; le catalogue d'hypothèses et le nombre de
  périmètres sont bornés.
- Les périodes de clôture ne sont pas des cohortes entrantes de maturité identique. Une
  hausse de moyenne peut accompagner la clôture de dossiers anciens; elle n'établit pas
  une cause et n'implique pas une hausse de médiane.
- L'échantillon par fenêtres n'est pas aléatoire. Les volumes et stocks ouverts sont ceux
  de l'extrait; l'historique des réouvertures et la charge complète des équipes manquent.
- Les données réelles ne permettent pas actuellement les tests temporels longs. Les
  algorithmes sont testés sur cas synthétiques distincts, sans les présenter comme NYC réel.
- La latence locale peut perturber une soutenance. La restitution est la voie conseillée;
  une panne ne doit pas déclencher de remplacement silencieux du direct par une archive.
- La compatibilité des contrats est testée avec des consommateurs simulés. Leur branchement
  réel, le déploiement, une étude utilisateur et l'évaluation humaine restent hors périmètre.

## Dossier livré et reproduction

Le dossier [demo/](demo/) contient le README, les lanceurs Windows, l'interface,
`questions.json`, les quatre rapports avec audits et preuves de vérification, la fiche
des rôles et le déroulé de soutenance. Les dépendances sont dans `requirements-demo.txt`;
`requirements-demo.lock.txt` conserve les versions transitives résolues sur ce poste.

Les résultats du précontrôle, du contrôle HTTP et de la revérification hors ligne sont
archivés sous `outputs/bi_analyst_demo_readiness/20261028/`, avec `environment.json`,
`delivery_verification.json` et `delivery_manifest.json`. Les treize rapports de campagne
et les quatre restitutions ont été revérifiés après leur enregistrement. L'environnement
testé utilise Python 3.13.2, Streamlit 1.66.1 et Ollama 0.40.11; les versions et empreintes
du modèle et des fichiers sont conservées.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m bi_analyst.demo_evaluation --root D:\Analyst_Agent --output outputs\bi_analyst_demo_readiness\nouvelle_campagne --repetitions 2
```

Le dossier d'une nouvelle campagne doit être inexistant. Les copies de démonstration ont
des empreintes propres et les métadonnées de leurs sources historiques. Leur vérification
ne transforme pas une ancienne exécution en nouvelle génération. Aucun score humain,
résultat ou succès Granite n'a été synthétisé.
