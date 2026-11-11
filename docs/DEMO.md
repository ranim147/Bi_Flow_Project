# Démonstration BIFlow — déroulé de la soutenance

Validation technique et métier : présentation (`livrables/BIFlow_Validation.pptx`), puis démonstration
**en direct** de l'application. La vidéo `livrables/BIFlow_demo.mp4` (4 min, intégrée à la slide 15)
sert de secours si la machine ou le réseau posent problème.

## Avant la séance (la veille, puis 15 min avant)

```powershell
cd biflow
.\.venv\Scripts\python -m pytest -q                 # doit finir sans échec (tests PostgreSQL ignorés sans base)
ollama list                                         # qwen2.5:7b présent ; sinon : ollama pull qwen2.5:7b
.\scripts\tasks.ps1 web --llm ollama                # compile le front si besoin, puis http://localhost:8000
```

1. Ouvrir <http://localhost:8000> ; le bandeau en haut à droite doit afficher `LLM ollama:qwen2.5:7b`.
2. **Préchauffer le LLM** : page *Poser une question*, cliquer sur une question d'exemple (la première
   réponse prend 15 à 20 s, les suivantes 2 à 5 s).
3. Lancer une fois le **Run nominal** pour que le tableau de bord affiche un run du jour.
4. Garder un second onglet ouvert sur la page *Tableau de bord*.

Plan B sans LLM (machine lente, Ollama absent) : `.\scripts\tasks.ps1 web`. Les runs prennent alors environ
10 s, les questions du catalogue sont traitées par la baseline à règles, les reformulations libres moins
bien.

## Pendant la démonstration (environ 8 min)

### 1. Run nominal (2 min 30)

Page **Scénarios**, *Run nominal*, « Ralentir pour la présentation » coché, **Lancer le scénario**.

Pendant que les stations s'allument (environ 1 min 30 avec le LLM), commenter le journal :

- **Data Profiler** : 265 609 demandes, 3 types de défaut (ex. 1 089 demandes « Closed » sans date) ;
- **Qualité / ETL** : 139 lignes supprimées par des règles tracées, gardées en quarantaine ; texte libre
  retiré, coordonnées arrondies ; instantané `83d3805019db` ;
- **KPI & Semantic** : 5 KPI en SQL depuis le catalogue ; la question sur le Bronx traduite par le LLM ;
  la question « Pourquoi... » refusée ;
- **Analyste BI** : la partie la plus longue, le LLM rédige les insights ; tout nombre non prouvé est rejeté ;
- **Auditeur** : **53/53 contrôles**, chaque requête rejouée, run publié.

Cliquer ensuite sur les onglets d'agents : *Qualité / ETL* (règles, quarantaine), *KPI & Semantic* (« Voir
le SQL »), *Auditeur / XAI* (contrôles par étape, explication).

### 2. Tableau de bord et question (2 min)

- **Tableau de bord** : 97,7 % de demandes clôturées, délai médian 7,3 h ; survoler la courbe jusqu'au
  **24 février 2026** (460 demandes, *Snow or Ice*) : « une piste à vérifier, pas une cause » ; ouvrir
  « Voir le SQL » sous un KPI.
- **Poser une question** : « Combien de demandes de bruit dans le Bronx ? » → 15 443, SQL affiché,
  contrôle croisé concordant. Puis « Combien d'agents faut-il recruter ? » → refus motivé.

### 3. Les scénarios de robustesse (3 min 30)

Pour aller vite, décocher « Ralentir » et choisir des étapes en fin de pipeline.

| Scénario | Réglage conseillé | Durée avec LLM | À montrer |
|---|---|---|---|
| Panne d'un agent | Qualité / ETL | environ 20 s | 3 essais, puis « Bloqué » : rien n'est publié |
| Écart corrigé | KPI & Semantic (sens métier) ou Dashboard Generator (rapide) | 2 min 30 / 1 min 40 | la flèche de renvoi, « 2 passages », second audit 53/53 |
| Écart persistant | Dashboard Generator | environ 1 min 40 | « non fiable » ; l'onglet *Tableau de bord* n'a pas changé ; *Historique* |

Pendant les runs longs, parler de l'architecture ou répondre aux questions : le journal se met à jour seul.

## Questions probables

| Question | Réponse courte |
|---|---|
| Le LLM peut-il inventer un chiffre ? | Non. Les KPI viennent du SQL du catalogue ; le LLM rédige à partir des preuves et tout nombre absent des preuves est rejeté. |
| Et si le SQL du LLM est dangereux ? | Bac à sable DuckDB : copie en mémoire, SELECT seul, tables autorisées, fichiers coupés, temps et lignes limités. |
| Les pannes sont-elles réelles ? | Simulées, avec les mêmes options que la ligne de commande ; l'orchestrateur, les agents et les données sont les vrais. |
| Pourquoi un seul renvoi ? | Pour éviter une boucle infinie : si l'écart persiste, un humain doit regarder, rien n'est publié. |
| RGPD ? | Texte libre retiré, coordonnées à environ 1 km, seuil de 10 demandes, question contrôlée avant envoi, jamais de ligne brute au LLM, modèle local possible. |
| Pourquoi pas de cause ? | Les données ne contiennent ni effectifs ni capacité : BIFlow refuse plutôt que d'inventer. |
| Pourquoi 2 % des données ? | Acquisition bornée (45 mois en environ 265 000 lignes) ; échantillon non probabiliste, d'où la réserve affichée sur le tableau de bord. |
| Les délais cibles sont-ils officiels ? | Non, provisoires : le KPI « sous le délai cible » l'indique ; ils sont à valider par le service. |
| Comment savoir que les chiffres sont justes ? | L'Auditeur rejoue chaque requête et recalcule 4 KPI en pandas ; le BI Analyst de l'équipe, code indépendant, trouve le même borough au délai médian le plus long (Staten Island, 15,736 h). |

## Commandes de secours (ligne de commande)

```powershell
$f = "data/raw/nyc311_2023-01_2026-09_echantillon.parquet"
.\.venv\Scripts\python -m biflow.run --no-db --file $f                                          # nominal
.\.venv\Scripts\python -m biflow.run --no-db --file $f --simulate-failure quality_etl           # panne
.\.venv\Scripts\python -m biflow.run --no-db --file $f --simulate-audit-flag kpi_semantic       # écart corrigé
.\.venv\Scripts\python -m biflow.run --no-db --file $f --simulate-audit-flag kpi_semantic --persistent  # non fiable
```
