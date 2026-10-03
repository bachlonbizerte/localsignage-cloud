import os, sqlite3, secrets, hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import jwt
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen
import json
from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
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
app=FastAPI(title='LocalSignage Cloud API', version='2.4.0')
app.mount('/media', StaticFiles(directory=MEDIA_DIR), name='media')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
clients=set()

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, email TEXT UNIQUE, name TEXT, password_hash TEXT, google_id TEXT UNIQUE, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, device_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, platform TEXT, version TEXT, token_hash TEXT, last_seen TEXT, online INTEGER DEFAULT 0, playlist_id INTEGER, display_mode TEXT DEFAULT 'fit', orientation TEXT DEFAULT 'auto', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pair_codes(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL, used INTEGER DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL, duration REAL DEFAULT 10, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playlists(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playlist_items(id INTEGER PRIMARY KEY, playlist_id INTEGER NOT NULL, media_id INTEGER NOT NULL, position INTEGER NOT NULL, duration REAL DEFAULT 10, FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE, FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE);
    ''')
    # Multi-user ownership migration. Existing data is assigned to the existing admin account.
    ucols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
    if 'email' not in ucols: c.execute('ALTER TABLE users ADD COLUMN email TEXT')
    if 'name' not in ucols: c.execute('ALTER TABLE users ADD COLUMN name TEXT')
    if 'google_id' not in ucols: c.execute('ALTER TABLE users ADD COLUMN google_id TEXT')
    # Ensure the configured admin exists, preserving existing credentials.
    admin=c.execute('SELECT * FROM users WHERE username=?',(ADMIN_USER,)).fetchone()
    if not admin:
        c.execute('INSERT INTO users(username,email,name,password_hash,created_at) VALUES(?,?,?,?,?)',(ADMIN_USER, ADMIN_USER if '@' in ADMIN_USER else None, ADMIN_USER, password_hash(ADMIN_PASSWORD), now()))
    admin_id=c.execute('SELECT id FROM users WHERE username=?',(ADMIN_USER,)).fetchone()['id']
    # Add owner columns to existing tables.
    for table in ('devices','pair_codes','media','playlists'):
        cols={r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
        if 'owner_user_id' not in cols: c.execute(f'ALTER TABLE {table} ADD COLUMN owner_user_id INTEGER')
        c.execute(f'UPDATE {table} SET owner_user_id=? WHERE owner_user_id IS NULL',(admin_id,))
    c.execute("UPDATE users SET email=? WHERE id=? AND (email IS NULL OR email='')",(ADMIN_USER if '@' in ADMIN_USER else ADMIN_USER+'@localsignage.local',admin_id))
    c.execute("UPDATE users SET name=? WHERE id=? AND (name IS NULL OR name='')",(ADMIN_USER,admin_id))
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
    c.commit(); c.close()

def now(): return datetime.now(timezone.utc).isoformat()
def token(payload): return jwt.encode(payload, SECRET, algorithm='HS256')
def auth(authorization: str=Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Authentication required')
    try:
        p=jwt.decode(authorization[7:], SECRET, algorithms=['HS256'])
        if not p.get('uid'): raise HTTPException(401,'Session requires re-login')
        return p
    except HTTPException: raise
    except Exception: raise HTTPException(401,'Invalid or expired token')

def owner_id(payload):
    try: return int(payload['uid'])
    except Exception: raise HTTPException(401,'Invalid account')

def hash_token(t): return hashlib.sha256(t.encode()).hexdigest()

class Login(BaseModel): username:str; password:str
class Signup(BaseModel): email:str; password:str; name:str=''
class PairStart(BaseModel): pass
class PairRequest(BaseModel): code:str; device_id:str; name:str='LocalSignage Player'; platform:str='android'; version:str='2.0.0'
class Heartbeat(BaseModel): device_id:str; version:str='2.0.0'; status:str='online'

@app.on_event('startup')
def startup(): init_db()

@app.get('/')
def root(): return FileResponse(BASE/'app'/'static'/'index.html')
@app.get('/health')
def health(): return {'ok':True,'service':'LocalSignage Cloud','version':'2.4.0','time':now()}
@app.get('/api/state')
def state():
    c=db(); refresh_device_status(c); c.commit(); n=c.execute('SELECT COUNT(*) FROM devices').fetchone()[0]; online=c.execute('SELECT COUNT(*) FROM devices WHERE online=1').fetchone()[0]; c.close()
    return {'version':'2.4.0','devices':n,'online':online}

@app.post('/api/auth/signup')
def signup(x:Signup):
    email=x.email.strip().lower()
    if '@' not in email or len(email)>254: raise HTTPException(400,'Email invalide')
    if len(x.password)<8: raise HTTPException(400,'Le mot de passe doit contenir au moins 8 caractères')
    name=(x.name or email.split('@')[0]).strip()[:100]
    c=db()
    if c.execute('SELECT 1 FROM users WHERE lower(email)=?',(email,)).fetchone(): c.close(); raise HTTPException(409,'Email déjà utilisé')
    username=email
    try:
        cur=c.execute('INSERT INTO users(username,email,name,password_hash,created_at) VALUES(?,?,?,?,?)',(username,email,name,password_hash(x.password),now()))
        uid=cur.lastrowid; c.commit()
    except sqlite3.IntegrityError:
        c.rollback(); c.close(); raise HTTPException(409,'Compte déjà existant')
    c.close()
    return {'access_token':token({'uid':uid,'sub':email,'name':name,'exp':datetime.now(timezone.utc)+timedelta(hours=24)}),'token_type':'bearer','user':{'id':uid,'email':email,'name':name}}

@app.post('/api/auth/login')
def login(x:Login):
    ident=x.username.strip().lower()
    c=db(); u=c.execute('SELECT * FROM users WHERE lower(email)=? OR lower(username)=?',(ident,ident)).fetchone(); c.close()
    if not u or not u['password_hash'] or not password_verify(x.password,u['password_hash']): raise HTTPException(401,'Email ou mot de passe incorrect')
    return {'access_token':token({'uid':u['id'],'sub':u['email'] or u['username'],'name':u['name'] or u['username'],'exp':datetime.now(timezone.utc)+timedelta(hours=24)}),'token_type':'bearer','user':{'id':u['id'],'email':u['email'] or u['username'],'name':u['name'] or u['username']}}

@app.get('/api/auth/me')
def me(p:dict=Depends(auth)):
    c=db(); u=c.execute('SELECT id,email,name FROM users WHERE id=?',(owner_id(p),)).fetchone(); c.close()
    if not u: raise HTTPException(401,'Account not found')
    return dict(u)

@app.get('/api/auth/google')
def google_login(request:Request):
    cid=os.getenv('GOOGLE_CLIENT_ID','').strip()
    if not cid: raise HTTPException(503,'Google Sign-In not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in Render.')
    redirect=os.getenv('GOOGLE_REDIRECT_URI','').strip() or str(request.base_url).rstrip('/')+'/api/auth/google/callback'
    state=secrets.token_urlsafe(24)
    params={'client_id':cid,'redirect_uri':redirect,'response_type':'code','scope':'openid email profile','access_type':'offline','prompt':'select_account','state':state}
    return RedirectResponse('https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(params))

@app.get('/api/auth/google/callback')
def google_callback(request:Request, code:str='', state:str=''):
    cid=os.getenv('GOOGLE_CLIENT_ID','').strip(); secret=os.getenv('GOOGLE_CLIENT_SECRET','').strip()
    if not cid or not secret: raise HTTPException(503,'Google Sign-In not configured')
    redirect=os.getenv('GOOGLE_REDIRECT_URI','').strip() or str(request.base_url).rstrip('/')+'/api/auth/google/callback'
    try:
        body=urlencode({'code':code,'client_id':cid,'client_secret':secret,'redirect_uri':redirect,'grant_type':'authorization_code'}).encode()
        req=UrlRequest('https://oauth2.googleapis.com/token',data=body,headers={'Content-Type':'application/x-www-form-urlencoded'},method='POST')
        with urlopen(req,timeout=15) as resp: tok=json.loads(resp.read().decode())
        req2=UrlRequest('https://openidconnect.googleapis.com/v1/userinfo',headers={'Authorization':'Bearer '+tok['access_token']})
        with urlopen(req2,timeout=15) as resp: info=json.loads(resp.read().decode())
        email=(info.get('email') or '').lower().strip(); gid=info.get('sub')
        if not email or not gid: raise ValueError('Google account did not return email')
    except Exception as e: raise HTTPException(400,'Google authentication failed: '+str(e))
    c=db(); u=c.execute('SELECT * FROM users WHERE google_id=? OR lower(email)=?',(gid,email)).fetchone()
    if u:
        c.execute('UPDATE users SET google_id=?,email=?,name=? WHERE id=?',(gid,email,info.get('name') or email.split('@')[0],u['id'])); uid=u['id']; name=info.get('name') or u['name'] or email.split('@')[0]
    else:
        cur=c.execute('INSERT INTO users(username,email,name,google_id,created_at) VALUES(?,?,?,?,?)',(email,email,info.get('name') or email.split('@')[0],gid,now())); uid=cur.lastrowid; name=info.get('name') or email.split('@')[0]
    c.commit(); c.close()
    t=token({'uid':uid,'sub':email,'name':name,'exp':datetime.now(timezone.utc)+timedelta(hours=24)})
    return RedirectResponse('/?google_token='+t)

@app.post('/api/devices/pair/start')
def pair_start(p:dict=Depends(auth)):
    code=f'{secrets.randbelow(1000000):06d}'; exp=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
    c=db(); c.execute('INSERT INTO pair_codes(code,expires_at,created_at,owner_user_id) VALUES(?,?,?,?)',(code,exp,now(),owner_id(p))); c.commit(); c.close()
    return {'code':code,'expires_at':exp}

@app.post('/api/devices/pair')
def pair(x:PairRequest):
    c=db(); row=c.execute('SELECT * FROM pair_codes WHERE code=? AND used=0 ORDER BY id DESC LIMIT 1',(x.code,)).fetchone()
    if not row or datetime.fromisoformat(row['expires_at']) < datetime.now(timezone.utc): c.close(); raise HTTPException(400,'Invalid or expired pairing code')
    device_token=secrets.token_urlsafe(40); h=hash_token(device_token)
    old=c.execute('SELECT id FROM devices WHERE device_id=?',(x.device_id,)).fetchone()
    owner=row['owner_user_id']
    if old: c.execute('UPDATE devices SET name=?,platform=?,version=?,token_hash=?,last_seen=?,online=1,owner_user_id=? WHERE device_id=?',(x.name,x.platform,x.version,h,now(),owner,x.device_id))
    else: c.execute("INSERT INTO devices(device_id,name,platform,version,token_hash,last_seen,online,assignment_type,created_at,owner_user_id) VALUES(?,?,?,?,?,?,?,?,?,?)",(x.device_id,x.name,x.platform,x.version,h,now(),1,'none',now(),owner))
    c.execute('UPDATE pair_codes SET used=1 WHERE id=?',(row['id'],)); c.commit(); c.close()
    return {'device_id':x.device_id,'device_token':device_token,'server_url':'/','message':'paired'}

@app.post('/api/devices/heartbeat')
def heartbeat(x:Heartbeat, authorization:str=Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Device token required')
    t=authorization[7:]; c=db(); d=c.execute('SELECT * FROM devices WHERE device_id=? AND token_hash=?',(x.device_id,hash_token(t))).fetchone()
    if not d: c.close(); raise HTTPException(401,'Invalid device token')
    c.execute('UPDATE devices SET version=?,last_seen=?,online=1 WHERE device_id=?',(x.version,now(),x.device_id)); c.commit(); c.close(); return {'ok':True,'server_time':now()}

OFFLINE_AFTER=45

def refresh_device_status(c):
    cutoff=datetime.now(timezone.utc)-timedelta(seconds=OFFLINE_AFTER)
    c.execute("UPDATE devices SET online=0 WHERE online=1 AND (last_seen IS NULL OR last_seen < ?)",(cutoff.isoformat(),))

@app.get('/api/devices')
def devices(p:dict=Depends(auth)):
    uid=owner_id(p); c=db(); refresh_device_status(c); c.commit(); rows=[]
    for r in c.execute('SELECT id,device_id,name,platform,version,last_seen,online,playlist_id,assignment_type,media_id,live_url,live_protocol,display_mode,orientation,created_at FROM devices WHERE owner_user_id=? ORDER BY id DESC',(uid,)):
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
def list_media(p:dict=Depends(auth)):
    uid=owner_id(p); c=db(); rows=[dict(r) for r in c.execute('SELECT * FROM media WHERE owner_user_id=? ORDER BY id DESC',(uid,))]; c.close(); return rows

@app.post('/api/media')
def create_media(x:MediaCreate, p:dict=Depends(auth)):
    uid=owner_id(p)
    if x.kind not in ('video','image','audio'): raise HTTPException(400,'Invalid media kind')
    c=db(); cur=c.execute('INSERT INTO media(name,url,kind,duration,created_at,owner_user_id) VALUES(?,?,?,?,?,?)',(x.name,x.url,x.kind,max(1,x.duration),now(),uid)); c.commit(); mid=cur.lastrowid; row=dict(c.execute('SELECT * FROM media WHERE id=?',(mid,)).fetchone()); c.close(); return row

@app.post('/api/media/upload')
async def upload_media(file:UploadFile=File(...), name:str=Form(None), kind:str=Form('video'), duration:float=Form(10), p:dict=Depends(auth)):
    uid=owner_id(p)
    safe=''.join(ch for ch in (file.filename or 'media') if ch.isalnum() or ch in '._-') or 'media'
    target=MEDIA_DIR/f'{secrets.token_hex(6)}_{safe}'
    with target.open('wb') as out:
        while True:
            chunk=await file.read(1024*1024)
            if not chunk: break
            out.write(chunk)
    url=f'/media/{target.name}'
    display=name or file.filename or target.name
    c=db(); cur=c.execute('INSERT INTO media(name,url,kind,duration,created_at,owner_user_id) VALUES(?,?,?,?,?,?)',(display,url,kind,max(1,duration),now(),uid)); c.commit(); mid=cur.lastrowid; row=dict(c.execute('SELECT * FROM media WHERE id=?',(mid,)).fetchone()); c.close(); return row

@app.delete('/api/media/{media_id}')
def delete_media(media_id:int, p:dict=Depends(auth)):
    uid=owner_id(p); c=db(); row=c.execute('SELECT url FROM media WHERE id=? AND owner_user_id=?',(media_id,uid)).fetchone()
    if not row: c.close(); raise HTTPException(404,'Media not found')
    if row['url'].startswith('/media/'):
        try: (MEDIA_DIR/row['url'].split('/media/',1)[1]).unlink(missing_ok=True)
        except Exception: pass
    c.execute('DELETE FROM media WHERE id=?',(media_id,)); c.execute('DELETE FROM playlist_items WHERE media_id=?',(media_id,)); c.commit(); c.close(); return {'ok':True}

@app.get('/api/playlists')
def list_playlists(p:dict=Depends(auth)):
    uid=owner_id(p); c=db(); pls=[]
    for p in c.execute('SELECT * FROM playlists WHERE owner_user_id=? ORDER BY id DESC',(uid,)):
        d=dict(p); d['items']=[dict(r) for r in c.execute('SELECT pi.id,pi.media_id,pi.position,pi.duration,m.name,m.url,m.kind FROM playlist_items pi JOIN media m ON m.id=pi.media_id WHERE pi.playlist_id=? ORDER BY pi.position',(p['id'],))]; pls.append(d)
    c.close(); return pls

@app.post('/api/playlists')
def create_playlist(x:PlaylistCreate, p:dict=Depends(auth)):
    uid=owner_id(p); c=db()
    try:
        cur=c.execute('INSERT INTO playlists(name,created_at,owner_user_id) VALUES(?,?,?)',(x.name,now(),uid)); pid=cur.lastrowid
        for pos,mid in enumerate(x.media_ids):
            m=c.execute('SELECT duration FROM media WHERE id=? AND owner_user_id=?',(mid,uid)).fetchone()
            if m: c.execute('INSERT INTO playlist_items(playlist_id,media_id,position,duration) VALUES(?,?,?,?)',(pid,mid,pos,m['duration']))
        c.commit()
    except sqlite3.IntegrityError: c.rollback(); c.close(); raise HTTPException(400,'Playlist name already exists')
    row=dict(c.execute('SELECT * FROM playlists WHERE id=?',(pid,)).fetchone()); c.close(); return row

@app.put('/api/playlists/{playlist_id}')
def update_playlist(playlist_id:int, x:PlaylistCreate, payload:dict=Depends(auth)):
    uid=owner_id(payload); c=db(); p=c.execute('SELECT id FROM playlists WHERE id=? AND owner_user_id=?',(playlist_id,uid)).fetchone()
    if not p: c.close(); raise HTTPException(404,'Playlist not found')
    c.execute('UPDATE playlists SET name=? WHERE id=?',(x.name,playlist_id)); c.execute('DELETE FROM playlist_items WHERE playlist_id=?',(playlist_id,))
    for pos,mid in enumerate(x.media_ids):
        m=c.execute('SELECT duration FROM media WHERE id=? AND owner_user_id=?',(mid,uid)).fetchone()
        if m: c.execute('INSERT INTO playlist_items(playlist_id,media_id,position,duration) VALUES(?,?,?,?)',(playlist_id,mid,pos,m['duration']))
    c.commit(); c.close(); return {'ok':True}

@app.delete('/api/playlists/{playlist_id}')
def delete_playlist(playlist_id:int, payload:dict=Depends(auth)):
    uid=owner_id(payload); c=db(); c.execute('DELETE FROM playlist_items WHERE playlist_id IN (SELECT id FROM playlists WHERE id=? AND owner_user_id=?)',(playlist_id,uid)); c.execute('DELETE FROM playlists WHERE id=? AND owner_user_id=?',(playlist_id,uid)); c.commit(); c.close(); return {'ok':True}

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
    return {'version':'2.4.0','device_id':device_id,'assignment_type':assignment_type,'playlist':dict(p) if p else None,'media':media,'live':live,'items':items,'display_mode':d['display_mode'] or 'fit','orientation':d['orientation'] or 'auto','server_time':now()}

class DeviceAssignment(BaseModel):
    type: str = 'none'
    playlist_id: int | None = None
    media_id: int | None = None
    live_url: str | None = None
    live_protocol: str | None = None
    display_mode: str = 'fit'
    orientation: str = 'auto'

@app.put('/api/devices/{device_id}/assignment')
def assign_device(device_id:str, x:DeviceAssignment, payload:dict=Depends(auth)):
    kind=x.type.lower().strip()
    if kind not in ('none','playlist','media','live'):
        raise HTTPException(400,'Invalid assignment type')
    uid=owner_id(payload); c=db(); d=c.execute('SELECT id FROM devices WHERE device_id=? AND owner_user_id=?',(device_id,uid)).fetchone()
    if not d:
        c.close(); raise HTTPException(404,'Device not found')
    if kind=='playlist':
        if x.playlist_id is None or not c.execute('SELECT id FROM playlists WHERE id=? AND owner_user_id=?',(x.playlist_id,uid)).fetchone():
            c.close(); raise HTTPException(404,'Playlist not found')
    if kind=='media':
        if x.media_id is None or not c.execute('SELECT id FROM media WHERE id=? AND owner_user_id=?',(x.media_id,uid)).fetchone():
            c.close(); raise HTTPException(404,'Media not found')
    if kind=='live' and not (x.live_url or '').strip():
        c.close(); raise HTTPException(400,'Live URL is required')
    display_mode=x.display_mode.lower().strip()
    orientation=x.orientation.lower().strip()
    if display_mode not in ('fit','fill','stretch','zoom'):
        c.close(); raise HTTPException(400,'Invalid display mode')
    if orientation not in ('auto','landscape','portrait'):
        c.close(); raise HTTPException(400,'Invalid orientation')
    c.execute("UPDATE devices SET assignment_type=?, playlist_id=?, media_id=?, live_url=?, live_protocol=?, display_mode=?, orientation=? WHERE device_id=?",
              (kind, x.playlist_id if kind=='playlist' else None, x.media_id if kind=='media' else None,
               (x.live_url or '').strip() if kind=='live' else None, x.live_protocol if kind=='live' else None,
               display_mode, orientation, device_id))
    c.commit(); c.close()
    return {'ok':True,'device_id':device_id,'assignment_type':kind,'playlist_id':x.playlist_id if kind=='playlist' else None,'media_id':x.media_id if kind=='media' else None,'live_url':x.live_url if kind=='live' else None,'live_protocol':x.live_protocol if kind=='live' else None,'display_mode':display_mode,'orientation':orientation}

class DevicePlaylist(BaseModel):
    playlist_id: int | None = None

@app.put('/api/devices/{device_id}/playlist')
def assign_device_playlist_compat(device_id:str, x:DevicePlaylist, _:dict=Depends(auth)):
    return assign_device(device_id, DeviceAssignment(type='playlist' if x.playlist_id is not None else 'none', playlist_id=x.playlist_id), _)

@app.delete('/api/devices/{device_id}')
def delete_device(device_id:str, payload:dict=Depends(auth)):
    uid=owner_id(payload); c=db(); c.execute('DELETE FROM devices WHERE device_id=? AND owner_user_id=?',(device_id,uid)); c.commit(); c.close(); return {'ok':True}

@app.websocket('/ws/dashboard')
async def ws(ws:WebSocket):
    await ws.accept(); clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)
