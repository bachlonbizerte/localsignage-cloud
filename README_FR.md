# LocalSignage Cloud V2.0 Foundation

Fondation cloud séparée de LocalSignage V1.22. Ne remplace pas la version locale.

## Test gratuit sur Internet avec Render
1. Créer un compte Render.
2. Mettre ce dossier dans un dépôt GitHub.
3. Render → New → Web Service → connecter le dépôt.
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Plan: Free.
7. Ajouter `ADMIN_PASSWORD` dans Environment Variables.
8. Render donne une URL `https://...onrender.com`.
9. Ouvrir `/` pour le dashboard ou `/docs` pour Swagger.

### Important
Le Free Render est parfait pour le test mais le service peut s'arrêter après 15 min sans trafic et redémarrer avec environ une minute de délai. Le filesystem local est éphémère: cette V2 utilise donc cette SQLite uniquement pour la fondation/test. Pour une vraie production, on passera à PostgreSQL + stockage objet/persistant. Render propose aussi un Postgres gratuit, mais sa durée est limitée à 30 jours. Voir la documentation Render.

## Test sans même publier le projet
Sur ton PC, démarre l'API puis utilise Cloudflare Quick Tunnel:
`cloudflared tunnel --url http://localhost:8000`
Cela donne une URL temporaire `trycloudflare.com`. C'est prévu pour développement/test et l'URL cesse de fonctionner quand cloudflared est arrêté.

## API
- `POST /api/auth/login`
- `POST /api/devices/pair/start` (admin)
- `POST /api/devices/pair` (player)
- `POST /api/devices/heartbeat` (player token)
- `GET /api/devices` (admin)
- `DELETE /api/devices/{device_id}` (admin)
- `WS /ws/dashboard`
- `GET /health`


### V2.0.1 Render
Cette version utilise PBKDF2-HMAC-SHA256 pour les mots de passe et ne dépend plus de Passlib/bcrypt, afin d’éviter les incompatibilités observées sur Python 3.14 de Render.
