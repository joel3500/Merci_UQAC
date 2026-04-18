# Merci_UQAC

Petit site hommage à l'UQAC construit sur la base de ton projet **Souvenir**.

## Ce que j'ai changé
- Nouveau nom : **Merci_UQAC**
- Nouveau texte orienté hommage à l'UQAC
- Remplacement de l'image principale par un **carrousel** alimenté avec les images fournies
- Champ `Filière` remplacé par `Programme / Service / Lien avec l'UQAC`
- Ajout du endpoint `GET /api/chat` pour charger l'historique côté front GitHub Pages

## Lancer en local
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## Déploiement GitHub + Render
### Backend / site complet sur Render
- Crée un nouveau service Render à partir du dépôt
- Le projet peut tourner tel quel avec le `render.yaml`

### Front statique sur GitHub Pages
- Le fichier racine `index.html` est prêt pour GitHub Pages
- Après déploiement Render, remplace partout `https://merci-uqac.onrender.com` par l'URL réelle de ton service Render si elle diffère
- Dans `app.py`, adapte aussi `PAGES_ORIGINS` avec l'URL GitHub Pages finale

## Base de données
- PostgreSQL par défaut si `DATABASE_URL` existe
- Sinon bascule automatique vers SQLite
