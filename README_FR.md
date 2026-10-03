# LocalSignage Cloud V2.1

Fondation cloud + gestion Media/Playlist + Player Android. La V1.22 locale reste indépendante.

## Ce que V2.1 ajoute

- Dashboard Cloud avec login admin.
- Pairing Android par code à 6 chiffres.
- Heartbeat toutes les 20 secondes.
- Un device passe OFFLINE après 45 secondes sans heartbeat.
- Bibliothèque Media : URL ou upload de vidéo/image.
- Playlists avec ordre des médias.
- API `/api/player/config` consommée par le Player Android.
- Player Android V2.1 : lit automatiquement la playlist et boucle dessus.
- Vidéos via Media3/ExoPlayer.
- Images avec durée configurable.

## Déploiement Render

1. Remplacer le contenu du dépôt GitHub par ce projet.
2. Render → Web Service → connecter le dépôt.
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Variables: `JWT_SECRET`, `ADMIN_USER`, `ADMIN_PASSWORD`.

### Important pour la production

Le plan Render Free et son filesystem local sont adaptés au test, pas au stockage média permanent. Les uploads peuvent disparaître après un redéploiement/restart. Pour la production, utiliser PostgreSQL + stockage objet persistant (S3/R2/Bunny Storage, etc.).

## API Media

- `GET /api/media`
- `POST /api/media` — JSON URL
- `POST /api/media/upload` — multipart upload
- `DELETE /api/media/{id}`

## API Playlist

- `GET /api/playlists`
- `POST /api/playlists`
- `PUT /api/playlists/{id}`
- `DELETE /api/playlists/{id}`

## API Player

- `GET /api/player/config?device_id=...` avec Bearer device token.

Le Player récupère la première playlist et ses médias toutes les 30 secondes.
