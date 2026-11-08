import {useCallback,useEffect,useRef,useState} from 'react';
import {get} from './api';

export default function useLiveResource(path,interval=15000) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
  const refreshRef=useRef(()=>{});
  useEffect(()=>{
    let stopped=false,controller=null,busy=false;
    async function refresh(){
      if(busy)return;
      busy=true;controller=new AbortController();
      const timeout=setTimeout(()=>controller?.abort(),20000);
      try{
        const result=await get(path,{signal:controller.signal});
        if(!stopped){setData(result);setError('');}
      }catch(e){if(!stopped)setError(e.name==='AbortError'?'Refresh timed out. Retrying automatically.':e.message);}
      finally{clearTimeout(timeout);busy=false;if(!stopped)setLoading(false);}
    }
    refreshRef.current=refresh;setLoading(true);refresh();
    const timer=setInterval(refresh,interval);
    return()=>{stopped=true;clearInterval(timer);controller?.abort();};
  },[path,interval]);
  const refresh=useCallback(()=>refreshRef.current(),[]);
  return {data,error,loading,refresh};
}
