# LocalSignage Cloud V2.2

## Correctifs
- ONLINE/OFFLINE calculé depuis le dernier heartbeat (45 s).
- Heure affichée en `Africa/Tunis` dans le Dashboard.
- Login utilise uniquement `ADMIN_USER` / `ADMIN_PASSWORD` des variables Render; aucun credential n'est prérempli dans l'interface.
- Assignation d'écran unifiée: `Aucun`, `Playlist`, `Media`, `Live`.
- API `/api/devices/{device_id}/assignment`.
- Player `/api/player/config` retourne le mode assigné.

## Render
Push vers GitHub `main` avec Auto-Deploy = On Commit.

Variables recommandées:
- `ADMIN_USER`
- `ADMIN_PASSWORD`
- `JWT_SECRET`
- `DB_PATH` (optionnel)
- `MEDIA_DIR` (optionnel)

## Live
Le Player Android V2.2 lit directement HLS et RTSP. RTMP/SRT nécessitent un gateway/transcodage cloud et ne sont pas lus directement par Media3 dans cette version.
