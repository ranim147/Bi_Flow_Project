# Qui fait quoi dans BIFlow BI Analyst ?

Le prototype aide à décider **où investiguer**. Il ne prouve pas pourquoi un délai a augmenté
et ne décide pas automatiquement d'un budget ou d'un renforcement d'effectifs.

| Composant | Rôle vérifiable |
| --- | --- |
| **Python** | Charge les fichiers préparés, vérifie leur cohérence, choisit les outils, calcule KPI, effectifs, comparaisons, contributions, ancienneté et limites. Il reconstruit les valeurs et sources du rapport. |
| **Granite 3.3 2B** | Utilise ces preuves pour choisir et ordonner des interprétations ou opérations d'investigation admissibles. Dans la branche drill-down, chaque hypothèse a des données à demander, une comparaison et des critères de confirmation ou de réfutation. |
| **Ollama** | Exécute Granite localement. Il expose l'API, applique les paramètres d'inférence et le format JSON demandé, et fournit les durées de chargement et de génération. Ollama n'est pas le validateur métier. |
| **Pydantic et validateur Python** | Vérifient le contrat, les références exactes, les limites et la cohérence des opérations avec les faits. Ils rejettent les sorties non admissibles. Un JSON bien formé n'est pas une garantie de vérité. |
| **Fallback Python** | Produit un résultat déterministe fiable lorsque la réponse LLM ne passe pas les contrôles. Son origine reste visible; ce résultat ne compte pas comme une réussite Granite. |
| **Streamlit** | Présente la question, les outils, preuves, plans et audits. Cette interface de soutenance est indépendante du Dashboard Generator. |

## Du fichier au rapport

1. L'utilisateur pose une question; le contrôleur sélectionne les outils autorisés.
2. Python calcule les preuves et leurs références : indicateur, périmètre, valeur, unité,
   effectif, fichier source et empreinte.
3. Granite reçoit un contexte borné et un schéma adapté. Il ne recopie pas librement les chiffres.
4. Le validateur accepte la réponse ou demande une seule correction par problème.
5. En cas de nouveau rejet ou d'erreur d'inférence, le fallback prend le relais.
6. Python assemble le rapport JSON et conserve les réponses, contrôles et temps dans l'audit.

## Lire les statuts sans les confondre

- **Validé automatiquement** : la sortie a passé les contrôles du prototype. Ce n'est pas
  une note humaine et cela ne prouve pas une causalité.
- **Validé après correction** : la première sortie a été rejetée; une nouvelle tentative a passé les contrôles.
- **`insufficient_data`** : les preuves ne permettent pas la conclusion demandée. Cette
  réponse prudente peut elle-même être validée; ce n'est pas nécessairement un échec du LLM.
- **Fallback Python** : une sortie LLM non fiable a été bloquée. Lire aussi les raisons du rejet.
- **`failed` / `refuse`** : l'analyse n'a pas pu être conduite, par exemple à cause d'entrées
  absentes ou d'un serveur/modèle indisponible.

## Trois modes distincts

| Mode présenté | Ce qui se passe pendant la soutenance |
| --- | --- |
| **Restitution enregistrée** | L'application relit une véritable exécution antérieure et ses preuves. Aucun appel LLM n'est lancé. L'identifiant, la date source et le temps de génération appartiennent à cette exécution passée; ils ne sont pas attribués à la restitution du jour. |
| **Exécution en direct** | Une nouvelle question passe par Python, Ollama et Granite; un nouvel identifiant et un nouvel audit sont créés. La génération locale peut être lente. |
| **Test contrôlé de rejet** | Une réponse Granite authentique archivée est modifiée délibérément, puis injectée pour montrer le blocage et le fallback sur les données réelles. Aucun appel Ollama n'est nécessaire. Cette réponse altérée ne doit jamais être attribuée à une génération spontanée de Granite ni ajoutée à ses métriques de réussite. |

Les rapports copiés pour la restitution conservent leur provenance et leurs empreintes,
avec une vérification des preuves hors ligne. La campagne Granite nouvellement exécutée
pour l'évaluation est distincte de ces sources archivées; les dates, identifiants et
dénominateurs doivent rester séparés.

## Ce que les chiffres ne disent pas

La comparaison classique par arrondissement utilise une **médiane descriptive**. Le drill-down
temporel décompose une **variation de moyenne** entre dossiers clôturés dans les mois comparés.
Moyenne et médiane peuvent évoluer dans des directions opposées.

Les effets internes, effets de composition et groupes entrants/sortants réconcilient une
variation arithmétique; ils ne démontrent aucune cause. Les volumes et le backlog de
l'extrait ne représentent pas toute la population new-yorkaise. Les dossiers ouverts sont
reconstitués sans historique complet des réouvertures. Les groupes trop petits ne sont
pas classés opérationnellement.

Enfin, deux mois suffisent à une comparaison descriptive de périodes, mais pas au test
saisonnier de tendance : celui-ci demande au moins trente-six mois complets contigus.
Le scan mensuel d'anomalies requiert au moins six périodes de référence puis une période
à tester. L'historique réel disponible est trop court pour ces deux tests.
