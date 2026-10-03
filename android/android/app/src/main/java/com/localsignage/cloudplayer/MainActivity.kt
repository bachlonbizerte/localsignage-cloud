package com.localsignage.cloudplayer

import android.app.*
import android.content.*
import android.content.pm.ActivityInfo
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.*
import android.view.*
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.Player
import androidx.media3.common.PlaybackException
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import org.json.JSONArray
import org.json.JSONObject
import java.io.*
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {
    private val cloudDefault="https://localsignage-cloud.onrender.com"
    private val prefs by lazy { getSharedPreferences("localsignage_cloud", MODE_PRIVATE) }
    private val exec=Executors.newFixedThreadPool(5); private val h=Handler(Looper.getMainLooper())
    private lateinit var status:TextView; private lateinit var info:TextView; private lateinit var container:FrameLayout; private lateinit var cfgText:TextView
    private var player:ExoPlayer?=null; private var current=0; private var items=JSONArray(); private var assignment="none"; private var mode="fit"; private var orientation="auto"
    private var configJob:Runnable?=null; private var imageJob:Runnable?=null; private var destroyed=false; private var lastConfigKey=""
    private val id:String get()=prefs.getString("device_id",null) ?: UUID.randomUUID().toString().also{prefs.edit().putString("device_id",it).apply()}
    private val cacheDirPath:String get(){val d=File(filesDir,"media_cache");if(!d.exists())d.mkdirs();return d.absolutePath}


    private val hbReceiver=object:BroadcastReceiver(){override fun onReceive(c:Context?,i:Intent?){if(!::status.isInitialized)return;val ok=i?.getBooleanExtra("ok",false)==true;val code=i?.getIntExtra("code",-1)?:-1;runOnUiThread{status.text=if(ok)"● CLOUD ONLINE" else if(code==401)"● TOKEN À VÉRIFIER" else "● CLOUD EN ATTENTE";status.setTextColor(if(ok)Color.GREEN else Color.YELLOW)}}}

    override fun onCreate(b:Bundle?){super.onCreate(b);val filter=IntentFilter("com.localsignage.cloudplayer.HEARTBEAT");if(Build.VERSION.SDK_INT>=33)registerReceiver(hbReceiver,filter,Context.RECEIVER_NOT_EXPORTED)else registerReceiver(hbReceiver,filter);window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);hideUi();if(prefs.getString("device_token",null)!=null){startServiceSafe();showConnected()}else setup()}
    override fun onResume(){super.onResume();window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);if(prefs.getString("device_token",null)!=null&&!destroyed){startServiceSafe();if(::container.isInitialized)fetchConfig()}}
    override fun onPause(){super.onPause();window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)}
    private fun hideUi(){window.decorView.systemUiVisibility=View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY}
    private fun startServiceSafe(){try{if(Build.VERSION.SDK_INT>=26)startForegroundService(Intent(this,CloudHeartbeatService::class.java))else startService(Intent(this,CloudHeartbeatService::class.java))}catch(_:Exception){}}
    private fun root()=FrameLayout(this).apply{setBackgroundColor(Color.BLACK)}
    private fun tv(s:String,z:Float,c:Int=Color.WHITE)=TextView(this).apply{text=s;textSize=z;setTextColor(c);gravity=Gravity.CENTER}
    private fun fld(hint:String,value:String="")=EditText(this).apply{this.hint=hint;setText(value);textSize=17f;setTextColor(Color.WHITE);setHintTextColor(Color.GRAY);setSingleLine(true);setPadding(18,0,18,0);minHeight=62;background=GradientDrawable().apply{cornerRadius=12f;setColor(Color.rgb(25,25,29));setStroke(1,Color.DKGRAY)}}
    private fun setup(message:String=""){stopPlaybackOnly();val r=root();val c=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL;gravity=Gravity.CENTER_HORIZONTAL;setPadding(70,40,70,40)};c.addView(tv("LocalSignage Cloud",30f));c.addView(tv("PLAYER V2.3",16f,Color.LTGRAY));c.addView(Space(this),LinearLayout.LayoutParams(1,20));val server=fld("https://localsignage-cloud.onrender.com",prefs.getString("server_url",cloudDefault)?:cloudDefault);val name=fld("Android Player",prefs.getString("device_name","Android Player")?:"Android Player");val code=fld("6 chiffres");code.inputType=2;fun add(l:String,e:EditText){c.addView(tv(l,13f,Color.LTGRAY),LinearLayout.LayoutParams(-1,36));c.addView(e,LinearLayout.LayoutParams(-1,64).apply{bottomMargin=12})};add("CLOUD SERVER",server);add("DEVICE NAME",name);add("PAIRING CODE (6 CHIFFRES)",code);val b=Button(this).apply{text="CONNECTER AU CLOUD";isAllCaps=false};c.addView(b,LinearLayout.LayoutParams(-1,64));val m=tv(message,14f,Color.YELLOW);c.addView(m);c.addView(tv("Device ID\n$id",12f,Color.GRAY));val sc=ScrollView(this);sc.addView(c);r.addView(sc,FrameLayout.LayoutParams(-1,-2,Gravity.CENTER));setContentView(r);b.setOnClickListener{pair(server.text.toString().trim().removeSuffix("/"),name.text.toString().trim().ifEmpty{"Android Player"},code.text.toString().trim(),b,m)}}
    private fun pair(server:String,name:String,code:String,b:Button,m:TextView){if(code.length!=6){m.text="Code invalide";return};b.isEnabled=false;m.text="Connexion au Cloud...";exec.execute{try{val body=JSONObject().apply{put("code",code);put("device_id",id);put("name",name);put("platform","android");put("version","2.3.1")}.toString();val r=req("$server/api/devices/pair","POST",body,null);if(r.code !in 200..299)throw Exception(err(r));val j=JSONObject(r.body);prefs.edit().putString("server_url",server).putString("device_name",name).putString("device_token",j.getString("device_token")).apply();runOnUiThread{startServiceSafe();showConnected()}}catch(e:Exception){runOnUiThread{b.isEnabled=true;m.text="Erreur: ${e.message}"}}}}
    private var controlOverlay: View?=null
    private var overlayJob:Runnable?=null

    override fun dispatchTouchEvent(ev: android.view.MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_UP && ::container.isInitialized && prefs.getString("device_token",null)!=null) {
            toggleOverlay()
        }
        return super.dispatchTouchEvent(ev)
    }

    private fun toggleOverlay(){
        val ov=controlOverlay ?: return
        overlayJob?.let{h.removeCallbacks(it)}
        ov.visibility=if(ov.visibility==View.VISIBLE) View.GONE else View.VISIBLE
        if(ov.visibility==View.VISIBLE){
            hideUi()
            overlayJob=Runnable{ov.visibility=View.GONE;hideUi()}
            h.postDelayed(overlayJob!!,5000)
        }else hideUi()
    }

    private fun showConnected(){
        destroyed=false
        val r=root()
        container=FrameLayout(this).apply{setBackgroundColor(Color.BLACK)}
        r.addView(container,FrameLayout.LayoutParams(-1,-1))

        val ov=LinearLayout(this).apply{
            orientation=LinearLayout.VERTICAL
            setPadding(18,12,18,12)
            setBackgroundColor(Color.argb(150,0,0,0))
            visibility=View.GONE
        }
        controlOverlay=ov

        status=tv("● CONNEXION AU CLOUD...",18f,Color.YELLOW)
        status.gravity=Gravity.LEFT
        ov.addView(status)

        cfgText=tv("CONFIG: chargement...",12f,Color.LTGRAY)
        cfgText.gravity=Gravity.LEFT
        ov.addView(cfgText)

        info=tv("${prefs.getString("device_name","Android Player")} • $id",11f,Color.LTGRAY)
        info.gravity=Gravity.LEFT
        ov.addView(info)

        val gear=Button(this).apply{text="⚙";textSize=18f}
        ov.addView(gear,LinearLayout.LayoutParams(66,58))
        gear.setOnClickListener{showSettings()}

        r.addView(ov,FrameLayout.LayoutParams(-1,-2,Gravity.TOP))
        setContentView(r)
        hideUi()
        startServiceSafe()
        fetchConfig()
    }

    private fun showSettings(){val b=AlertDialog.Builder(this).setTitle("LocalSignage Player").setMessage("Device: $id\n\nLe token est conservé. Pour refaire le pairing, utilise uniquement le bouton Réinitialiser.").setPositiveButton("Fermer",null).setNegativeButton("Réinitialiser pairing"){_,_->prefs.edit().remove("device_token").remove("last_config").apply();stopService(Intent(this,CloudHeartbeatService::class.java));setup("Pairing réinitialisé")}.create();b.show()}
    private fun fetchConfig(){configJob?.let{h.removeCallbacks(it)};val s=prefs.getString("server_url",cloudDefault)?:cloudDefault;val t=prefs.getString("device_token",null)?:return;exec.execute{try{val r=req("$s/api/player/config?device_id=$id","GET",null,t);runOnUiThread{when{r.code==401->{cfgText.text="CONFIG: token refusé • pairing à vérifier";loadLastConfig()};r.code in 200..299->{prefs.edit().putString("last_config",r.body).apply();applyConfig(JSONObject(r.body))};else->cfgText.text="CONFIG: HTTP ${r.code} • cache conservé"}}}catch(e:Exception){runOnUiThread{cfgText.text="CONFIG: hors ligne • cache conservé";loadLastConfig()}}};configJob=Runnable{fetchConfig()};h.postDelayed(configJob!!,2000)}
    private fun loadLastConfig(){val x=prefs.getString("last_config",null)?:return;try{applyConfig(JSONObject(x))}catch(_:Exception){}}
    private fun applyConfig(j:JSONObject){val stable=JSONObject(j.toString());stable.remove("server_time");val key=stable.toString();if(key==lastConfigKey)return;lastConfigKey=key;assignment=j.optString("assignment_type","none");items=j.optJSONArray("items")?:JSONArray();current=0;mode=j.optString("display_mode","fit");orientation=j.optString("orientation","auto");applyOrientation();cfgText.text="CONFIG: ${assignment.uppercase()} • ${items.length()} média • $mode • $orientation";if(assignment=="live"){val l=j.optJSONObject("live");if(l!=null)playLive(l.optString("url"),l.optString("protocol","auto")) else noContent()}else if(items.length()>0){val allVideo=(0 until items.length()).all{items.optJSONObject(it)?.optString("kind")=="video"};if(allVideo)playVideoPlaylist() else playCurrent()}else noContent();prefetchAll()}
    private fun applyOrientation(){requestedOrientation=when(orientation){"portrait"->ActivityInfo.SCREEN_ORIENTATION_PORTRAIT;"landscape"->ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE;else->ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED}}
    private fun noContent(){container.removeAllViews();container.addView(tv("LOCAL SIGNAGE CLOUD\n\nAucun contenu assigné",28f),FrameLayout.LayoutParams(-1,-1))}
    private fun isHls(u:String):Boolean{val x=u.lowercase().substringBefore("?");return x.endsWith(".m3u8")||u.lowercase().contains(".m3u8?")}
    private fun hlsItem(uri:String):MediaItem{val b=MediaItem.Builder().setUri(uri);if(isHls(uri))b.setMimeType(MimeTypes.APPLICATION_M3U8);return b.build()}
    private fun playCurrent(){if(items.length()==0)return;val it=items.optJSONObject(current)?:return;val url=it.optString("url");val kind=it.optString("kind");ensureCached(url,kind){local->if(kind=="image")showImage(local,it.optDouble("duration",10.0).toLong())else showVideo(local)}}
    private fun cacheName(url:String):String{val h=MessageDigest.getInstance("SHA-256").digest(url.toByteArray()).joinToString(""){String.format("%02x",it)};return "$cacheDirPath/$h"}
    private fun ensureCached(url:String,kind:String,done:(String)->Unit){if(url.startsWith("rtsp://")||url.startsWith("rtmp://")||isHls(url)){done(resolve(url));return};val local=cacheName(url);val f=File(local);if(f.exists()&&f.length()>0){done(Uri.fromFile(f).toString());return};cfgText.text="CACHE: téléchargement...";exec.execute{try{val c=URL(resolve(url)).openConnection() as HttpURLConnection;c.connectTimeout=30000;c.readTimeout=60000;c.instanceFollowRedirects=true;val tmp=File(local+".part");c.inputStream.use{inp->FileOutputStream(tmp).use{out->inp.copyTo(out)}};c.disconnect();if(tmp.length()>0){tmp.renameTo(f);runOnUiThread{done(Uri.fromFile(f).toString())}}else throw IOException("fichier vide")}catch(e:Exception){runOnUiThread{cfgText.text="CACHE: ${e.message}; lecture réseau";done(resolve(url))}}}}
    private fun prefetchAll(){for(i in 0 until items.length()){val it=items.optJSONObject(i)?:continue;val u=it.optString("url");val k=it.optString("kind");if(u.isNotEmpty()&&!u.startsWith("rtsp://")&&!u.startsWith("rtmp://")&&!isHls(u))exec.execute{try{val local=cacheName(u);if(File(local).exists())return@execute;val c=URL(resolve(u)).openConnection() as HttpURLConnection;c.connectTimeout=20000;c.readTimeout=60000;val tmp=File(local+".part");c.inputStream.use{inp->FileOutputStream(tmp).use{out->inp.copyTo(out)}};c.disconnect();if(tmp.length()>0)tmp.renameTo(File(local))}catch(_:Exception){}}}}
    private fun playerView():PlayerView{imageJob?.let{h.removeCallbacks(it)};val pv=PlayerView(this).apply{useController=false;setBackgroundColor(Color.BLACK);resizeMode=when(mode){"fill","stretch"->AspectRatioFrameLayout.RESIZE_MODE_FILL;"zoom"->AspectRatioFrameLayout.RESIZE_MODE_ZOOM;else->AspectRatioFrameLayout.RESIZE_MODE_FIT}};return pv}
    private fun showVideo(uri:String){player?.release();container.removeAllViews();val pv=playerView();container.addView(pv,FrameLayout.LayoutParams(-1,-1));val p=ExoPlayer.Builder(this).build();player=p;pv.player=p;p.addListener(object:Player.Listener{
override fun onPlayerError(error: PlaybackException){
    var c: Throwable? = error
    val details = StringBuilder()
    var n = 0

    while(c != null && n < 6){
        details.append("\\n")
            .append(c.javaClass.simpleName)
            .append(": ")
            .append(c.message ?: "")
        c = c.cause
        n++
    }

    cfgText.text = "LECTURE ERROR: ${error.errorCodeName}${details}"
}});p.setMediaItem(hlsItem(uri));p.prepare();p.playWhenReady=true}
    private fun playVideoPlaylist(){player?.release();container.removeAllViews();val pv=playerView();container.addView(pv,FrameLayout.LayoutParams(-1,-1));val p=ExoPlayer.Builder(this).build();player=p;pv.player=p;val media=mutableListOf<MediaItem>();for(i in 0 until items.length()){val it=items.optJSONObject(i)?:continue;val u=resolve(it.optString("url"));val isHlsSource=isHls(u);val local=cacheName(u);val f=File(local);val uri=if(!isHlsSource&&f.exists()&&f.length()>0)Uri.fromFile(f).toString() else u;media.add(hlsItem(uri))};if(media.isEmpty())return;p.repeatMode=Player.REPEAT_MODE_ALL;p.addListener(object:Player.Listener{override fun onPlaybackStateChanged(st:Int){when(st){Player.STATE_BUFFERING->cfgText.text="LECTURE: buffering...";Player.STATE_READY->cfgText.text="LECTURE: OK • playlist"}}});p.setMediaItems(media,0,0);p.prepare();p.playWhenReady=true}
    private fun showImage(uri:String,d:Long){player?.release();player=null;container.removeAllViews();val iv=ImageView(this).apply{setBackgroundColor(Color.BLACK);scaleType=when(mode){"fill","zoom"->ImageView.ScaleType.CENTER_CROP;"stretch"->ImageView.ScaleType.FIT_XY;else->ImageView.ScaleType.FIT_CENTER}};container.addView(iv,FrameLayout.LayoutParams(-1,-1));try{iv.setImageURI(Uri.parse(uri))}catch(_:Exception){};imageJob=Runnable{next()};h.postDelayed(imageJob!!,d.coerceAtLeast(1)*1000)}
    private fun playLive(url:String,protocol:String){imageJob?.let{h.removeCallbacks(it)};player?.release();player=null;val p=protocol.lowercase();if(p=="rtmp"||url.startsWith("rtmp://")){container.removeAllViews();container.addView(tv("LIVE RTMP\n\nNon support direct Media3. Utiliser HLS/RTSP ou un gateway.",24f),FrameLayout.LayoutParams(-1,-1));return};showVideo(resolve(url))}
    private fun next(){if(items.length()==0)return;current=(current+1)%items.length();playCurrent()}
    private fun resolve(u:String)=if(u.startsWith("http://")||u.startsWith("https://")||u.startsWith("rtsp://")||u.startsWith("rtmp://"))u else (prefs.getString("server_url",cloudDefault)?:cloudDefault).removeSuffix("/")+"/"+u.removePrefix("/")
    private data class R(val code:Int,val body:String)
    private fun req(url:String,method:String,body:String?,bearer:String?):R{val c=URL(url).openConnection() as HttpURLConnection;c.requestMethod=method;c.connectTimeout=30000;c.readTimeout=60000;c.useCaches=false;c.setRequestProperty("Accept","application/json");if(bearer!=null)c.setRequestProperty("Authorization","Bearer $bearer");if(body!=null){c.setRequestProperty("Content-Type","application/json");c.doOutput=true;c.outputStream.use{it.write(body.toByteArray())}};val code=c.responseCode;val st=if(code>=400)c.errorStream else c.inputStream;val body2=st?.bufferedReader()?.use{it.readText()}?:"";c.disconnect();return R(code,body2)}
    private fun err(r:R)=try{JSONObject(r.body).optString("detail",r.body.take(180))}catch(_:Exception){r.body.take(180)}
    private fun stopPlaybackOnly(){configJob?.let{h.removeCallbacks(it)};imageJob?.let{h.removeCallbacks(it)};configJob=null;imageJob=null;player?.release();player=null}
    override fun onDestroy(){destroyed=true;try{unregisterReceiver(hbReceiver)}catch(_:Exception){};stopPlaybackOnly();exec.shutdownNow();super.onDestroy()}
}
