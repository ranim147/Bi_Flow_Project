# Rapport Analyste BI — NYC 311

Échantillon analysé : 10,000 lignes.
Période disponible : 2026-06-04 15:32:09+00:00 à 2026-10-14 09:05:00+00:00.

## Tendances

Volume : tendance à la baisse, pente descriptive -4 783.6 demandes/mois sur 4 mois.
Médiane des délais : tendance down sur 4 mois.

## Anomalies à examiner

Détection mensuelle non exécutée : 5 mois disponibles, alors qu’il faut au moins 7 pour comparer une période à son historique. Aucune conclusion d’absence d’anomalie n’est possible.

## Délais relativement élevés

- complaint_type APPLIANCE : médiane 17.2 jours (n=52, seuil comparatif P75=6.0 jours). Ce n’est pas un constat de non-respect d’un SLA.
- complaint_type Sidewalk Condition : médiane 11.9 jours (n=80, seuil comparatif P75=6.0 jours). Ce n’est pas un constat de non-respect d’un SLA.
- complaint_type Maintenance or Facility : médiane 10.7 jours (n=84, seuil comparatif P75=6.0 jours). Ce n’est pas un constat de non-respect d’un SLA.
- complaint_type DOOR/WINDOW : médiane 10.2 jours (n=88, seuil comparatif P75=6.0 jours). Ce n’est pas un constat de non-respect d’un SLA.
- complaint_type UNSANITARY CONDITION : médiane 10.2 jours (n=299, seuil comparatif P75=6.0 jours). Ce n’est pas un constat de non-respect d’un SLA.

## Recommandations prioritaires

1. Examiner un échantillon de dossiers dans les groupes aux délais relativement élevés afin d’identifier les étapes à investiguer.
2. Confirmer que les mois comparés sont complets et comparables, surtout aux bornes de l’extrait.

## Limites et vérifications

- Les résultats décrivent l’extrait fourni, pas nécessairement toutes les demandes de New York.
- Les demandes sans date de fermeture sont exclues des délais ; elles peuvent être en cours.
- Une anomalie statistique ne prouve ni erreur ni cause. Contrôler qualité et contexte opérationnel.
- Aucun objectif de délai/SLA n’a été fourni ; les délais sont comparés entre groupes admissibles seulement.
- Les effectifs faibles, mois incomplets et valeurs manquantes limitent les conclusions.

## Méthodes statistiques et évaluation

- Z-score classique : seuil ±3.0; MAD robuste : seuil ±3.5; référence minimale 6 périodes antérieures.
- Anomalies réelles : 0 comparaisons exécutées; 0 signal(s). Historique trop court : détection non exécutée.
- Tendance persistante : no_persistent_direction (4 périodes).
- STL : skipped — historique insuffisant : minimum 24 mois requis.
- Cible métier : cible métier non configurée. Aucun SLA n’a été inventé.
- Tests synthétiques : 13 exécutés, tous réussis : True. Ces résultats ne sont pas des résultats NYC 311.
- Rapport qualité externe : présent_mais_non_concordant (41165 lignes rapportées vs 10000 lignes chargées); source brute : contrôle_clé_raw_effectué.
- Base des volumes mensuels : official monthly aggregate; sample used for dimensions/delays; mois partiels exclus des tendances : ['2026-10']. Les comparaisons de catégories/délais utilisent des blocs temporels répartis, non un tirage aléatoire, donc un biais de sélection reste possible.
- Insights auditables produits : 18; preuves : 18; règle biflow-bi-analyst-rules-1.0.