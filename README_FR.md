# LocalSignage Cloud V2.1.2

Correctifs:
- Heure affichée en Tunisie (Africa/Tunis) dans le dashboard.
- Etat ONLINE/OFFLINE calculé selon le dernier heartbeat (45 secondes).
- Association d'une playlist à chaque device/écran.
- Migration automatique de la colonne `playlist_id` sur les anciennes bases SQLite.
- `/api/player/config` renvoie uniquement la playlist assignée au device.
- Dashboard: menu Playlist / écran + bouton Assigner.

Déploiement Render: pousser les fichiers sur la branche `main`; avec Auto-Deploy = On Commit, Render déploie automatiquement.
