# Déroulé de soutenance — BIFlow BI Analyst

## Avant l'arrivée du professeur

Depuis PowerShell, dans `D:\Analyst_Agent`, lancer l'interface locale :

```powershell
.\.venv\Scripts\python.exe -m streamlit run demo\streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Ouvrir `http://127.0.0.1:8501`. Vérifier les quatre scénarios prédéfinis en **restitution
enregistrée** avant la séance. Le README du dossier donne les commandes d'installation,
de lancement Windows et de validation des enregistrements.

Pour une exécution en direct, vérifier séparément Ollama :

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version'
& '.\tools\ollama\ollama.exe' list
```

Seul `granite3.3:2b` est utilisé. La restitution enregistrée sert de voie principale pendant
la soutenance; une lenteur ou une panne d'Ollama ne doit jamais être masquée par un passage
silencieux du direct à une ancienne réponse.

## Phrase d'ouverture

« Python calcule et documente les faits. Granite assemble une interprétation ou un plan
d'investigation contraint par ces preuves. Un validateur indépendant bloque les sorties
non fiables. L'application que je présente est une interface de démonstration, pas le
Dashboard Generator. »

Préciser immédiatement si l'écran montre une **restitution d'exécution réelle antérieure**
ou une **nouvelle exécution en direct**.

## Démonstration 1 — comparer les délais réels par arrondissement

Scénario : `compare_boroughs`.

Question : « Compare les délais de résolution par arrondissement. »

1. Montrer les outils sélectionnés : contrôle de qualité puis comparaison de groupes.
2. Montrer l'indicateur exact et son unité : médiane des délais valides des demandes clôturées.
3. Ouvrir une preuve : arrondissement, effectif, source et empreinte.
4. Lire l'insight, sa recommandation et les limites d'échantillonnage.
5. Télécharger le rapport JSON, puis ouvrir l'audit et la validation initiale.

À expliquer : un délai descriptif plus élevé ne prouve ni une mauvaise organisation ni un
dépassement de SLA. Les groupes à faible effectif ne deviennent pas un classement opérationnel.

## Démonstration 2 — localiser les investigations à Brooklyn

Scénario : `drilldown_brooklyn`.

Question : « Investiguer la hausse des délais à Brooklyn; préciser les catégories, agences
et périodes prioritaires. »

1. Montrer les périodes réellement retenues et la base temporelle : clôtures dans chaque mois.
2. Distinguer la variation de **moyenne** de celle de **médiane**.
3. Ouvrir la décomposition : évolution des délais internes, composition et groupes non appariés.
4. Montrer le filtre exact proposé : arrondissement, catégorie et agence; ne pas remplacer
   la cible produite par une cible annoncée à l'avance si les données ou preuves changent.
5. Lire les étapes concrètes : champs à extraire, périodes à comparer et données supplémentaires.
6. Montrer, pour une hypothèse, les observations qui la soutiendraient et celles qui l'écarteraient.

À expliquer : les contributions sont descriptives. Les journaux de traitement et la
complexité des dossiers sont des données à demander, pas des causes déjà démontrées.
Un backlog de l'échantillon ne justifie pas automatiquement davantage de personnel.

## Démonstration 3 — reconnaître un historique insuffisant

Scénario : `insufficient_history`.

Question : « Peut-on conclure à l'absence d'anomalie avec cet historique mensuel ? »

1. Montrer l'historique complet disponible et le seuil du test.
2. Lire `decision=insufficient_data` et la limite correspondante.
3. Expliquer qu'une détection non exécutable ne démontre pas l'absence d'anomalie.
4. Montrer les données supplémentaires demandées et le statut de validation de cette prudence.

À expliquer : « réponse validée » et « données suffisantes pour conclure » sont deux questions
différentes. Un refus justifié constitue un comportement attendu de l'agent.

## Démonstration 4 — bloquer une réponse incorrecte

Scénario : `controlled_rejection`.

Afficher explicitement le mode **test contrôlé de rejet**. La réponse incorrecte est une
entrée d'essai dérivée d'une réponse authentique archivée, puis altérée et injectée;
ne pas dire que Granite vient de la produire spontanément. Ce test n'appelle pas Ollama.

1. Montrer les champs rendus incorrects et les codes de rejet du validateur.
2. Distinguer la première tentative et la correction éventuelle.
3. Montrer le fallback Python et son origine explicite.
4. Vérifier que les chiffres inventés et affirmations non admissibles ne passent pas dans
   le rapport final, puis ouvrir l'audit conservant l'incident d'essai.

À expliquer : ce test éprouve une barrière de fiabilité. Il n'est pas ajouté aux taux de
réussite ou d'erreur naturels de Granite dans la campagne de questions métier.

## Relier la démonstration à l'évaluation

La restitution peut utiliser des rapports issus de campagnes antérieures : montrer leur
date et leur identifiant d'origine. Le rapport de préparation présente séparément la
nouvelle campagne Granite et ses répétitions. Une validation de consommateur JSON ou un
recalcul de preuves n'est pas une nouvelle génération du modèle.

## Questions possibles du professeur

| Question | Réponse précise |
| --- | --- |
| Granite calcule-t-il les KPI ? | Non. Les valeurs et effectifs sont calculés par Python et liés aux sources. |
| Quel est alors le rôle du LLM ? | Choisir et ordonner des interprétations ou opérations d'investigation admissibles selon les preuves, avec des contrôles métier indépendants. |
| Les causes sont-elles trouvées ? | Non. Le plan formule des hypothèses non vérifiées et indique quelles observations permettraient de les examiner. |
| Le JSON valide suffit-il ? | Non. Les références, périmètres, limites, décisions et opérations doivent également être cohérents. |
| Est-ce une évaluation humaine ? | Non. Les résultats présentés sont des mesures automatiques et les exécutions réellement enregistrées. |
| Est-ce déjà intégré aux autres agents ? | Le contrat JSON est testé avec des consommateurs simulés; les autres agents ne sont pas développés ici. |
| Peut-on changer librement les dates dans la question ? | La branche d'investigation sélectionne actuellement la dernière paire de mois complets contigus; elle ne parse pas un intervalle arbitraire écrit en prose. |

Conclure avec le rapport `BI_ANALYST_DEMO_READINESS.md` : montrer ce qui a été effectivement
exécuté et testé, les taux avec leurs dénominateurs, puis les limites restantes.
