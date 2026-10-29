# Démonstration BIFlow — déroulé (environ 10 minutes)

Prérequis : `.venv` installé, extrait `data/raw/nyc311_2023-01_2026-09_echantillon.parquet` présent
(sinon : `python -m biflow.ingestion.import_pages <dossier raw de l'acquisition> --name nyc311_2023-01_2026-09_echantillon`).
Optionnel : Ollama lancé avec `qwen2.5:7b` pour la partie LLM, Docker pour la partie PostgreSQL.

## 1. Le pipeline de bout en bout (2 min)

```powershell
.\scripts\demo.ps1 -NoDashboard
```

À montrer dans le résumé :

- les 6 étapes OK, avec leurs contrôles de validation ;
- **265 609 demandes lues, 139 supprimées** par des règles tracées (et gardées en quarantaine) ;
- les KPI calculés par SQL à partir du catalogue YAML ;
- la question « Pourquoi... » **refusée avec explication** : les données ne contiennent pas les causes ;
- l'audit : **53/53 contrôles**, chaque requête rejouée, run publié.

## 2. Le dashboard (3 min)

```powershell
.\.venv\Scripts\python -m streamlit run src/biflow/dashboard/app.py
```

- **Tableau de bord** : chaque carte et chaque graphique a un « Origine des chiffres » (SQL + empreinte).
- Les anomalies sur la courbe journalière, avec le type de plainte en hausse :
  - 2026-02-24 : *Snow or Ice* (231 demandes au lieu d'environ 20 par jour) ;
  - 2023-09-29 : *Sewer Maintenance* (le jour des inondations de New York).
  BIFlow écrit « piste à vérifier, pas une cause » : c'est l'analyste qui conclut.
- **Poser une question** : « Combien de demandes de bruit dans le Bronx ? » puis une question hors
  périmètre (« Combien d'agents faut-il recruter ? »). Avec une adresse dans la question, montrer le masquage.
- **Audit et provenance** : réserves sur l'échantillon, défauts du Profiler, règles de l'ETL, contrôles.

## 3. La sécurité : l'Auditeur bloque les erreurs (2 min)

```powershell
.\.venv\Scripts\python -m biflow.run --no-db --file data/raw/nyc311_2023-01_2026-09_echantillon.parquet --simulate-audit-flag kpi_semantic --persistent
```

Statut **NON FIABLE** : rien n'est publié, le dashboard garde le dernier run validé.
Autres démos : `--simulate-failure quality_etl` (relances puis ÉCHEC).

## 4. Les preuves du MVP (2 min)

```powershell
.\.venv\Scripts\python -m biflow.evaluation                      # baseline sans LLM (environ 10 s)
$env:BIFLOW_LLM_PROVIDER="ollama"; .\.venv\Scripts\python -m biflow.evaluation   # avec le LLM local (environ 10 min)
```

Résultats : `data/processed/evaluation/evaluation.md`. À souligner : défauts, KPI, anomalie et audit à 100 % ;
le SQL des reformulations montre l'apport (et les limites) du LLM face à la baseline.

## 5. Contrôle croisé avec l'implémentation de l'équipe

Sur la même extraction, le BI Analyst de l'équipe (dépôt `Bi_Flow_Project`, code indépendant) trouve
STATEN ISLAND comme borough au délai médian le plus long : 0,655677 jour = **15,736 h**, n = 9 874.
BIFlow trouve **15,736 h**, n = 9 876 (2 demandes d'écart dues aux règles de nettoyage).
Deux codes différents, même résultat.

## Questions probables

| Question | Réponse courte |
| :-- | :-- |
| Le LLM peut-il inventer un chiffre ? | Non : il rédige à partir des `evidence` ; tout nombre absent est rejeté et le gabarit est gardé. Les KPI viennent du SQL du catalogue. |
| Et si le SQL du LLM est dangereux ? | Bac à sable DuckDB : copie en mémoire, SELECT seul, tables autorisées, accès fichiers coupé, temps et lignes limités. |
| RGPD ? | Texte libre retiré, coordonnées à environ 1 km, seuil N = 10, question contrôlée avant envoi, jamais de ligne brute au LLM ; modèle local possible (Ollama). |
| Pourquoi pas de cause ? | Les données ne contiennent ni effectifs ni capacité : BIFlow refuse plutôt que d'inventer. |
| Pourquoi 2 % des données ? | Choix d'acquisition bornée (45 mois en environ 265 000 lignes) ; non probabiliste : les volumes ne s'extrapolent pas à la ville, d'où les réserves affichées. |
