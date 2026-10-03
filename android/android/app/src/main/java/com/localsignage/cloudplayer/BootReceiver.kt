package com.localsignage.cloudplayer

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class BootReceiver: BroadcastReceiver(){
    override fun onReceive(context:Context,intent:Intent){
        val p=context.getSharedPreferences("localsignage_cloud",Context.MODE_PRIVATE)
        if(p.getString("device_token",null)!=null){
            val i=Intent(context,CloudHeartbeatService::class.java)
            try{if(android.os.Build.VERSION.SDK_INT>=26)context.startForegroundService(i) else context.startService(i)}catch(_:Exception){}
        }
    }
}
