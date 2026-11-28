"""Recherche de stage (fonctionnalité enfichable).

Pour une liste de postes voulus : recherches préremplies sur Indeed, LinkedIn, Glassdoor, Welcome to the Jungle,
HelloWork, JobTeaser et Google ; suivi des offres repérées (lues à partir de leur lien quand le site le permet) ;
lettres de motivation rédigées par Groq d'après un profil candidat valable pour tout cursus, ou lettre type
sans clé d'API.

Activation : une ligne dans FEATURES (backend/config.py). Retrait : supprimer cette ligne.
Tout est dans ce dossier ; les tables (préfixe stg_) sont créées au démarrage, sans toucher au cœur.
"""
from backend.core.features import Feature, NavItem

from . import store
from .assistant import ACTIONS, TOOLS
from .views import bp

feature = Feature(
    "stages",
    blueprints=[bp],
    # masqué pour qui a répondu « Non » à « Cherches-tu un stage ? » dans son compte
    nav=[NavItem("stages.index", "Stages", order=4, section="site", opens="stages",
                 hidden_if=("wants_internship", "non"))],
    schema=store.SCHEMA,
    migrations=store.MIGRATIONS,
    tools=TOOLS,      # l'assistant suit la recherche et propose des postes d'après la description de l'étudiant
    actions=ACTIONS,  # … qu'il ajoute à la liste seulement si l'étudiant clique « Choisir »
)
