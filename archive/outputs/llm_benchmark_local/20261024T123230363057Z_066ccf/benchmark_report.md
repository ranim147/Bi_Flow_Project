# BIFlow — benchmark Ollama local

Exécution UTC : 2026-10-24T12:32:31.019007+00:00

Les mesures de qualité portent sur les réponses reçues. Une infrastructure indisponible n'a aucun score de qualité.
Les cas réels et synthétiques sont présentés séparément. Les alertes causales lexicales exigent une relecture humaine.

```text
        model data_kind  scheduled  responses_received  availability_rate objective_pass_rate_all_scheduled mean_latency_seconds p95_latency_seconds  quality_denominator json_valid_mean schema_valid_mean objective_pass_mean numeric_accuracy_mean source_accuracy_mean sample_size_accuracy_mean scope_accuracy_mean required_fact_recall_mean decision_ok_mean finding_ok_mean limits_ok_mean unsupported_claims numeric_mismatches prose_numeric_flags causal_flags
     qwen3:4b      real         24                   0                0.0                              None                 None                None                    0            None              None                None                  None                 None                      None                None                      None             None            None           None               None               None                None         None
     qwen3:4b synthetic          3                   0                0.0                              None                 None                None                    0            None              None                None                  None                 None                      None                None                      None             None            None           None               None               None                None         None
    gemma3:4b      real         24                   0                0.0                              None                 None                None                    0            None              None                None                  None                 None                      None                None                      None             None            None           None               None               None                None         None
    gemma3:4b synthetic          3                   0                0.0                              None                 None                None                    0            None              None                None                  None                 None                      None                None                      None             None            None           None               None               None                None         None
granite3.3:2b      real         24                   0                0.0                              None                 None                None                    0            None              None                None                  None                 None                      None                None                      None             None            None           None               None               None                None         None
granite3.3:2b synthetic          3                   0                0.0                              None                 None                None                    0            None              None                None                  None                 None                      None                None                      None             None            None           None               None               None                None         None
```

## Erreurs observées

model          status      
gemma3:4b      model_absent    27
granite3.3:2b  model_absent    27
qwen3:4b       model_absent    27

## Choix du modèle et limites

Aucun modèle sélectionné : comparaison réelle complète des trois modèles indisponible. Aucun score n'est inventé.

Le téléchargement n'est pas une mesure d'inférence. Warmup exclu ; cache, offload CPU/GPU et charge machine influencent les latences.
Une répétition ne permet pas d'estimer la stabilité. Température nulle et seed ne garantissent pas une reproduction bit à bit entre matériels et versions.
Les contrôles numériques vérifient les valeurs structurées, leurs unités, groupes, sources et effectifs ; les chiffres en prose sont signalés.
La grille humaine reste nécessaire pour l'utilité des actions, les causes implicites et les nombres écrits en lettres.
Les références sont des contrats déterministes. Le champ finding normalise l'expression et ne mesure pas à lui seul la qualité d'analyse.

## Données et environnement

- La provenance NYC Open Data est déclarée dans les métadonnées locales ; aucun nouvel extrait distant n'est téléchargé.
- Voir manifest.json pour versions, paramètres, empreintes, matériel et disponibilité.
