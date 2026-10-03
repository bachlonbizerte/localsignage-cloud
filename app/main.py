import os, sqlite3, secrets, hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import jwt
from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE=Path(__file__).resolve().parent.parent
DB=Path(os.getenv('DB_PATH', BASE/'data'/'localsignage.db'))
DB.parent.mkdir(parents=True, exist_ok=True)
MEDIA_DIR=Path(os.getenv('MEDIA_DIR', BASE/'data'/'media'))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
SECRET=os.getenv('JWT_SECRET','CHANGE_ME_LOCALSIGNAGE_SECRET')
ADMIN_USER=os.getenv('ADMIN_USER','admin')
ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','admin123!')
PBKDF2_ITERATIONS=600_000

def password_hash(password: str) -> str:
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"

def password_verify(password: str, stored: str) -> bool:
    try:
        scheme,iters,salt_hex,digest_hex=stored.split('$',3)
        if scheme != 'pbkdf2_sha256': return False
        salt=bytes.fromhex(salt_hex)
        expected=bytes.fromhex(digest_hex)
        actual=hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(iters))
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False
app=FastAPI(title='LocalSignage Cloud API', version='2.3.1')
app.mount('/media', StaticFiles(directory=MEDIA_DIR), name='media')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
clients=set()

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, device_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, platform TEXT, version TEXT, token_hash TEXT, last_seen TEXT, online INTEGER DEFAULT 0, playlist_id INTEGER, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pair_codes(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL, used INTEGER DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL, duration REAL DEFAULT 10, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playlists(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playlist_items(id INTEGER PRIMARY KEY, playlist_id INTEGER NOT NULL, media_id INTEGER NOT NULL, position INTEGER NOT NULL, duration REAL DEFAULT 10, FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE, FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE);
    ''')
    # Safe migration for databases created by earlier versions.
    cols={r['name'] for r in c.execute('PRAGMA table_info(devices)')}
    if 'playlist_id' not in cols:
        c.execute('ALTER TABLE devices ADD COLUMN playlist_id INTEGER')
    if 'assignment_type' not in cols:
        c.execute("ALTER TABLE devices ADD COLUMN assignment_type TEXT DEFAULT 'playlist'")
    if 'media_id' not in cols:
        c.execute('ALTER TABLE devices ADD COLUMN media_id INTEGER')
    if 'live_url' not in cols:
        c.execute('ALTER TABLE devices ADD COLUMN live_url TEXT')
    if 'live_protocol' not in cols:
        c.execute('ALTER TABLE devices ADD COLUMN live_protocol TEXT')
    if 'display_mode' not in cols:
        c.execute("ALTER TABLE devices ADD COLUMN display_mode TEXT DEFAULT 'fit'")
    if 'orientation' not in cols:
        c.execute("ALTER TABLE devices ADD COLUMN orientation TEXT DEFAULT 'auto'")
    c.execute("UPDATE devices SET assignment_type='playlist' WHERE playlist_id IS NOT NULL")
    c.execute("UPDATE devices SET assignment_type='none' WHERE playlist_id IS NULL AND media_id IS NULL AND (live_url IS NULL OR live_url='')")
    if not c.execute('SELECT 1 FROM users WHERE username=?',(ADMIN_USER,)).fetchone():
        c.execute('INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)',(ADMIN_USER,password_hash(ADMIN_PASSWORD),now()))
    c.commit(); c.close()

def now(): return datetime.now(timezone.utc).isoformat()
def token(payload): return jwt.encode(payload, SECRET, algorithm='HS256')
def auth(authorization: str=Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Authentication required')
    try: return jwt.decode(authorization[7:], SECRET, algorithms=['HS256'])
    except Exception: raise HTTPException(401,'Invalid or expired token')

def hash_token(t): return hashlib.sha256(t.encode()).hexdigest()

class Login(BaseModel): username:str; password:str
class PairStart(BaseModel): pass
class PairRequest(BaseModel): code:str; device_id:str; name:str='LocalSignage Player'; platform:str='android'; version:str='2.0.0'
class Heartbeat(BaseModel): device_id:str; version:str='2.0.0'; status:str='online'

@app.on_event('startup')
def startup(): init_db()

@app.get('/')
def root(): return FileResponse(BASE/'app'/'static'/'index.html')
@app.get('/health')
def health(): return {'ok':True,'service':'LocalSignage Cloud','version':'2.3.1','time':now(),'timezone':'UTC (display: Africa/Tunis)'}
@app.get('/api/state')
def state():
    c=db(); refresh_device_status(c); c.commit(); n=c.execute('SELECT COUNT(*) FROM devices').fetchone()[0]; online=c.execute('SELECT COUNT(*) FROM devices WHERE online=1').fetchone()[0]; c.close()
    return {'version':'2.3.1','devices':n,'online':online}

@app.post('/api/auth/login')
def login(x:Login):
    c=db(); u=c.execute('SELECT * FROM users WHERE username=?',(x.username,)).fetchone(); c.close()
    if not u or not password_verify(x.password,u['password_hash']): raise HTTPException(401,'Invalid credentials')
    return {'access_token':token({'sub':u['username'],'exp':datetime.now(timezone.utc)+timedelta(hours=24)}),'token_type':'bearer'}

@app.post('/api/devices/pair/start')
def pair_start(_:dict=Depends(auth)):
    code=f'{secrets.randbelow(1000000):06d}'; exp=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
    c=db(); c.execute('INSERT INTO pair_codes(code,expires_at,created_at) VALUES(?,?,?)',(code,exp,now())); c.commit(); c.close()
    return {'code':code,'expires_at':exp}

@app.post('/api/devices/pair')
def pair(x:PairRequest):
    c=db(); row=c.execute('SELECT * FROM pair_codes WHERE code=? AND used=0 ORDER BY id DESC LIMIT 1',(x.code,)).fetchone()
    if not row or datetime.fromisoformat(row['expires_at']) < datetime.now(timezone.utc): c.close(); raise HTTPException(400,'Invalid or expired pairing code')
    device_token=secrets.token_urlsafe(40); h=hash_token(device_token)
    old=c.execute('SELECT id FROM devices WHERE device_id=?',(x.device_id,)).fetchone()
    if old: c.execute('UPDATE devices SET name=?,platform=?,version=?,token_hash=?,last_seen=?,online=1 WHERE device_id=?',(x.name,x.platform,x.version,h,now(),x.device_id))
    else: c.execute('INSERT INTO devices(device_id,name,platform,version,token_hash,last_seen,online,created_at) VALUES(?,?,?,?,?,?,?,?)',(x.device_id,x.name,x.platform,x.version,h,now(),1,now()))
    c.execute('UPDATE pair_codes SET used=1 WHERE id=?',(row['id'],)); c.commit(); c.close()
    return {'device_id':x.device_id,'device_token':device_token,'server_url':'/','message':'paired'}

@app.post('/api/devices/heartbeat')
def heartbeat(x:Heartbeat, authorization:str=Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Device token required')
    t=authorization[7:]; c=db(); d=c.execute('SELECT * FROM devices WHERE device_id=? AND token_hash=?',(x.device_id,hash_token(t))).fetchone()
    if not d: c.close(); raise HTTPException(401,'Invalid device token')
    c.execute('UPDATE devices SET version=?,last_seen=?,online=1 WHERE device_id=?',(x.version,now(),x.device_id)); c.commit(); c.close(); return {'ok':True,'server_time':now()}

OFFLINE_AFTER=90

def refresh_device_status(c):
    cutoff=datetime.now(timezone.utc)-timedelta(seconds=OFFLINE_AFTER)
    c.execute("UPDATE devices SET online=0 WHERE online=1 AND (last_seen IS NULL OR last_seen < ?)",(cutoff.isoformat(),))

@app.get('/api/devices')
def devices(_:dict=Depends(auth)):
    c=db(); refresh_device_status(c); c.commit(); rows=[]
    for r in c.execute('SELECT id,device_id,name,platform,version,last_seen,online,playlist_id,assignment_type,media_id,live_url,live_protocol,created_at FROM devices ORDER BY id DESC'):
        d=dict(r)
        # Always calculate ONLINE from the heartbeat timestamp so a stale DB flag
        # can never make an old device appear online.
        try:
            seen=datetime.fromisoformat(d['last_seen']) if d.get('last_seen') else None
            d['online']=bool(seen and (datetime.now(timezone.utc)-seen).total_seconds() <= OFFLINE_AFTER)
        except Exception:
            d['online']=False
        rows.append(d)
    c.close(); return rows


class MediaCreate(BaseModel):
    name:str
    url:str
    kind:str='video'
    duration:float=10

class PlaylistCreate(BaseModel):
    name:str
    media_ids:list[int]=[]

@app.get('/api/media')
def list_media(_:dict=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT * FROM media ORDER BY id DESC')]; c.close(); return rows

@app.post('/api/media')
def create_media(x:MediaCreate, _:dict=Depends(auth)):
    if x.kind not in ('video','image','audio'): raise HTTPException(400,'Invalid media kind')
    c=db(); cur=c.execute('INSERT INTO media(name,url,kind,duration,created_at) VALUES(?,?,?,?,?)',(x.name,x.url,x.kind,max(1,x.duration),now())); c.commit(); mid=cur.lastrowid; row=dict(c.execute('SELECT * FROM media WHERE id=?',(mid,)).fetchone()); c.close(); return row

@app.post('/api/media/upload')
async def upload_media(file:UploadFile=File(...), name:str=Form(None), kind:str=Form('video'), duration:float=Form(10), _:dict=Depends(auth)):
    safe=''.join(ch for ch in (file.filename or 'media') if ch.isalnum() or ch in '._-') or 'media'
    target=MEDIA_DIR/f'{secrets.token_hex(6)}_{safe}'
    with target.open('wb') as out:
        while True:
            chunk=await file.read(1024*1024)
            if not chunk: break
            out.write(chunk)
    url=f'/media/{target.name}'
    display=name or file.filename or target.name
    c=db(); cur=c.execute('INSERT INTO media(name,url,kind,duration,created_at) VALUES(?,?,?,?,?)',(display,url,kind,max(1,duration),now())); c.commit(); mid=cur.lastrowid; row=dict(c.execute('SELECT * FROM media WHERE id=?',(mid,)).fetchone()); c.close(); return row

@app.delete('/api/media/{media_id}')
def delete_media(media_id:int, _:dict=Depends(auth)):
    c=db(); row=c.execute('SELECT url FROM media WHERE id=?',(media_id,)).fetchone()
    if not row: c.close(); raise HTTPException(404,'Media not found')
    if row['url'].startswith('/media/'):
        try: (MEDIA_DIR/row['url'].split('/media/',1)[1]).unlink(missing_ok=True)
        except Exception: pass
    c.execute('DELETE FROM media WHERE id=?',(media_id,)); c.execute('DELETE FROM playlist_items WHERE media_id=?',(media_id,)); c.commit(); c.close(); return {'ok':True}

@app.get('/api/playlists')
def list_playlists(_:dict=Depends(auth)):
    c=db(); pls=[]
    for p in c.execute('SELECT * FROM playlists ORDER BY id DESC'):
        d=dict(p); d['items']=[dict(r) for r in c.execute('SELECT pi.id,pi.media_id,pi.position,pi.duration,m.name,m.url,m.kind FROM playlist_items pi JOIN media m ON m.id=pi.media_id WHERE pi.playlist_id=? ORDER BY pi.position',(p['id'],))]; pls.append(d)
    c.close(); return pls

@app.post('/api/playlists')
def create_playlist(x:PlaylistCreate, _:dict=Depends(auth)):
    c=db()
    try:
        cur=c.execute('INSERT INTO playlists(name,created_at) VALUES(?,?)',(x.name,now())); pid=cur.lastrowid
        for pos,mid in enumerate(x.media_ids):
            m=c.execute('SELECT duration FROM media WHERE id=?',(mid,)).fetchone()
            if m: c.execute('INSERT INTO playlist_items(playlist_id,media_id,position,duration) VALUES(?,?,?,?)',(pid,mid,pos,m['duration']))
        c.commit()
    except sqlite3.IntegrityError: c.rollback(); c.close(); raise HTTPException(400,'Playlist name already exists')
    row=dict(c.execute('SELECT * FROM playlists WHERE id=?',(pid,)).fetchone()); c.close(); return row

@app.put('/api/playlists/{playlist_id}')
def update_playlist(playlist_id:int, x:PlaylistCreate, _:dict=Depends(auth)):
    c=db(); p=c.execute('SELECT id FROM playlists WHERE id=?',(playlist_id,)).fetchone()
    if not p: c.close(); raise HTTPException(404,'Playlist not found')
    c.execute('UPDATE playlists SET name=? WHERE id=?',(x.name,playlist_id)); c.execute('DELETE FROM playlist_items WHERE playlist_id=?',(playlist_id,))
    for pos,mid in enumerate(x.media_ids):
        m=c.execute('SELECT duration FROM media WHERE id=?',(mid,)).fetchone()
        if m: c.execute('INSERT INTO playlist_items(playlist_id,media_id,position,duration) VALUES(?,?,?,?)',(playlist_id,mid,pos,m['duration']))
    c.commit(); c.close(); return {'ok':True}

@app.delete('/api/playlists/{playlist_id}')
def delete_playlist(playlist_id:int, _:dict=Depends(auth)):
    c=db(); c.execute('DELETE FROM playlist_items WHERE playlist_id=?',(playlist_id,)); c.execute('DELETE FROM playlists WHERE id=?',(playlist_id,)); c.commit(); c.close(); return {'ok':True}

def device_auth(authorization: str=Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Device token required')
    return authorization[7:]

@app.get('/api/player/config')
def player_config(device_id:str, authorization:str=Header(None)):
    t=device_auth(authorization)
    c=db(); d=c.execute('SELECT * FROM devices WHERE device_id=? AND token_hash=?',(device_id,hash_token(t))).fetchone()
    if not d:
        c.close(); raise HTTPException(401,'Invalid device token')
    assignment_type=d['assignment_type'] or ('playlist' if d['playlist_id'] is not None else 'none')
    p=None; items=[]; media=None; live=None
    if assignment_type == 'playlist' and d['playlist_id'] is not None:
        p=c.execute('SELECT * FROM playlists WHERE id=?',(d['playlist_id'],)).fetchone()
        if p:
            items=[dict(r) for r in c.execute('SELECT pi.id,pi.media_id,pi.position,pi.duration,m.name,m.url,m.kind FROM playlist_items pi JOIN media m ON m.id=pi.media_id WHERE pi.playlist_id=? ORDER BY pi.position',(p['id'],))]
    elif assignment_type == 'media' and d['media_id'] is not None:
        media_row=c.execute('SELECT * FROM media WHERE id=?',(d['media_id'],)).fetchone()
        if media_row:
            media=dict(media_row); items=[media]
    elif assignment_type == 'live' and d['live_url']:
        live={'url':d['live_url'],'protocol':d['live_protocol'] or 'auto'}
    c.close()
    return {'version':'2.3.1','device_id':device_id,'assignment_type':assignment_type,'playlist':dict(p) if p else None,'media':media,'live':live,'items':items,'display_mode':d['display_mode'] or 'fit','orientation':d['orientation'] or 'auto','server_time':now()}

class DeviceAssignment(BaseModel):
    type: str = 'none'
    playlist_id: int | None = None
    media_id: int | None = None
    live_url: str | None = None
    live_protocol: str | None = None
    display_mode: str = 'fit'
    orientation: str = 'auto'

@app.put('/api/devices/{device_id}/assignment')
def assign_device(device_id:str, x:DeviceAssignment, _:dict=Depends(auth)):
    kind=x.type.lower().strip()
    if kind not in ('none','playlist','media','live'):
        raise HTTPException(400,'Invalid assignment type')
    mode=x.display_mode.lower().strip()
    orient=x.orientation.lower().strip()
    if mode not in ('fit','fill','stretch','zoom'):
        raise HTTPException(400,'Invalid display mode')
    if orient not in ('auto','landscape','portrait'):
        raise HTTPException(400,'Invalid orientation')
    c=db(); d=c.execute('SELECT id FROM devices WHERE device_id=?',(device_id,)).fetchone()
    if not d:
        c.close(); raise HTTPException(404,'Device not found')
    if kind=='playlist':
        if x.playlist_id is None or not c.execute('SELECT id FROM playlists WHERE id=?',(x.playlist_id,)).fetchone():
            c.close(); raise HTTPException(404,'Playlist not found')
    if kind=='media':
        if x.media_id is None or not c.execute('SELECT id FROM media WHERE id=?',(x.media_id,)).fetchone():
            c.close(); raise HTTPException(404,'Media not found')
    if kind=='live' and not (x.live_url or '').strip():
        c.close(); raise HTTPException(400,'Live URL is required')
    c.execute("UPDATE devices SET assignment_type=?, playlist_id=?, media_id=?, live_url=?, live_protocol=?, display_mode=?, orientation=? WHERE device_id=?",
              (kind, x.playlist_id if kind=='playlist' else None, x.media_id if kind=='media' else None,
               (x.live_url or '').strip() if kind=='live' else None, x.live_protocol if kind=='live' else None, mode, orient, device_id))
    c.commit(); c.close()
    return {'ok':True,'device_id':device_id,'assignment_type':kind,'playlist_id':x.playlist_id if kind=='playlist' else None,'media_id':x.media_id if kind=='media' else None,'live_url':x.live_url if kind=='live' else None,'live_protocol':x.live_protocol if kind=='live' else None,'display_mode':mode,'orientation':orient}

class DevicePlaylist(BaseModel):
    playlist_id: int | None = None

@app.put('/api/devices/{device_id}/playlist')
def assign_device_playlist_compat(device_id:str, x:DevicePlaylist, _:dict=Depends(auth)):
    return assign_device(device_id, DeviceAssignment(type='playlist' if x.playlist_id is not None else 'none', playlist_id=x.playlist_id), _)

@app.delete('/api/devices/{device_id}')
def delete_device(device_id:str, _:dict=Depends(auth)):
    c=db(); c.execute('DELETE FROM devices WHERE device_id=?',(device_id,)); c.commit(); c.close(); return {'ok':True}

@app.websocket('/ws/dashboard')
async def ws(ws:WebSocket):
    await ws.accept(); clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)
