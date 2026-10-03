package com.localsignage.cloudplayer

import android.app.*
import android.content.*
import android.os.*
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

class CloudHeartbeatService: Service(){
    private val h=Handler(Looper.getMainLooper()); private var run:Runnable?=null
    private val prefs by lazy{getSharedPreferences("localsignage_cloud",MODE_PRIVATE)}
    override fun onCreate(){super.onCreate();createChannel();startForeground(1001,notification("LocalSignage Cloud • service active"));schedule()}
    private fun schedule(){run=Runnable{heartbeat();h.postDelayed(run!!,15000)};h.post(run!!)}
    private fun heartbeat(){val s=prefs.getString("server_url","https://localsignage-cloud.onrender.com")?:return;val t=prefs.getString("device_token",null)?:return;val id=prefs.getString("device_id",null)?:return;Thread{try{val c=URL(s.removeSuffix("/")+"/api/devices/heartbeat").openConnection() as HttpURLConnection;c.requestMethod="POST";c.connectTimeout=30000;c.readTimeout=30000;c.setRequestProperty("Authorization","Bearer $t");c.setRequestProperty("Content-Type","application/json");c.doOutput=true;c.outputStream.use{it.write(JSONObject().apply{put("device_id",id);put("version","2.3.1");put("status","online")}.toString().toByteArray())};val code=c.responseCode;c.disconnect();sendBroadcast(Intent("com.localsignage.cloudplayer.HEARTBEAT").putExtra("ok",code in 200..299).putExtra("code",code))}catch(_:Exception){sendBroadcast(Intent("com.localsignage.cloudplayer.HEARTBEAT").putExtra("ok",false).putExtra("code",-1))}}.start()}
    private fun createChannel(){if(Build.VERSION.SDK_INT>=26){getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel("localsignage","LocalSignage",NotificationManager.IMPORTANCE_LOW))}}
    private fun notification(t:String)=if(Build.VERSION.SDK_INT>=26)Notification.Builder(this,"localsignage").setContentTitle("LocalSignage Cloud").setContentText(t).setSmallIcon(android.R.drawable.ic_media_play).build() else Notification.Builder(this).setContentTitle("LocalSignage Cloud").setContentText(t).setSmallIcon(android.R.drawable.ic_media_play).build()
    override fun onStartCommand(i:Intent?,f:Int,startId:Int)=START_STICKY
    override fun onBind(i:Intent?)=null
    override fun onDestroy(){run?.let{h.removeCallbacks(it)};run=null;super.onDestroy()}
}
