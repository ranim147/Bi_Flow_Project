# BIFlow — benchmark Ollama local

Exécution UTC : 2026-10-24T18:25:26.163700+00:00

Les mesures de qualité portent sur les réponses reçues. Une infrastructure indisponible n'a aucun score de qualité.
Les cas réels et synthétiques sont présentés séparément. Les alertes causales lexicales exigent une relecture humaine.

```text
        model data_kind  scheduled  responses_received  complete_responses  availability_rate  objective_pass_rate_all_scheduled  mean_latency_seconds  p95_latency_seconds  quality_denominator  json_valid_mean  schema_valid_mean  objective_pass_mean  numeric_accuracy_mean  source_accuracy_mean  sample_size_accuracy_mean  scope_accuracy_mean  required_fact_recall_mean  decision_ok_mean  finding_ok_mean  limits_ok_mean  unsupported_claims  numeric_mismatches  prose_numeric_flags  causal_flags
     qwen3:4b      real         24                  24                  24                1.0                                0.0             76.790020           113.933443                   24              1.0           1.000000                  0.0               0.625000              0.666667                   0.666667             0.416667                   0.458333          0.208333         0.250000        0.000000                 9.0                 1.0                 76.0          17.0
     qwen3:4b synthetic          3                   3                   3                1.0                                0.0             78.255458            86.308595                    3              1.0           1.000000                  0.0               1.000000              1.000000                   1.000000             1.000000                   1.000000          0.333333         0.000000        0.000000                 0.0                 0.0                 11.0           0.0
    gemma3:4b      real         24                   0                   0                0.0                                NaN                   NaN                  NaN                    0              NaN                NaN                  NaN                    NaN                   NaN                        NaN                  NaN                        NaN               NaN              NaN             NaN                 NaN                 NaN                  NaN           NaN
    gemma3:4b synthetic          3                   0                   0                0.0                                NaN                   NaN                  NaN                    0              NaN                NaN                  NaN                    NaN                   NaN                        NaN                  NaN                        NaN               NaN              NaN             NaN                 NaN                 NaN                  NaN           NaN
granite3.3:2b      real         24                  24                  24                1.0                                0.0             62.869564            88.308823                   24              1.0           0.916667                  0.0               0.886364              0.886364                   0.886364             0.886364                   0.916667          0.291667         0.208333        0.250000                 8.0                 0.0                 69.0          15.0
granite3.3:2b synthetic          3                   3                   3                1.0                                0.0             67.461043            77.905490                    3              1.0           1.000000                  0.0               1.000000              1.000000                   1.000000             1.000000                   1.000000          0.333333         0.000000        0.666667                 0.0                 0.0                  5.0           1.0
```

## Erreurs observées

model          status        
gemma3:4b      warmup_timeout    27
granite3.3:2b  success           27
qwen3:4b       success           27

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
