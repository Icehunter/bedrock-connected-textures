/** Runs in each converted pack's behavior pack and sends its data to the engine (sources.mjs) until acknowledged. */
export function publishSources({system,world,sources}) {
  const states=sources.map(source=>({...source,index:0,acknowledged:false,nonce:null}));
  let started=false;
  const receive=event=>{
    if(event.sourceEntity)return;
    if(event.id!=='bct:source_request'&&event.id!=='bct:source_ack')return;
    let message;
    try {message=JSON.parse(event.message);} catch {return;}
    for(const state of states) {
      if(event.id==='bct:source_ack'&&state.engine===message.engine&&state.provider===message.provider&&state.digest===message.digest) {
        state.acknowledged=true;state.nonce=message.nonce;
      } else if(event.id==='bct:source_request'&&state.nonce!==message.nonce) {
        // A new engine session asks again for everything.
        state.acknowledged=false;state.index=0;state.nonce=message.nonce;
      }
    }
  };
  system.afterEvents.scriptEventReceive.subscribe(receive);
  const begin=()=>{started=true;};
  world.afterEvents.worldLoad.subscribe(begin);
  // Also handles a module reloaded after the world's initial load event.
  const first=system.run(begin);
  const interval=system.runInterval(()=>{
    if(!started)return;
    let budget=8;
    for(const state of states) {
      if(state.acknowledged)continue;
      while(budget>0&&state.index<state.parts.length&&!state.acknowledged) {
        const index=state.index;
        const message=JSON.stringify({engine:state.engine,provider:state.provider,digest:state.digest,
          index,count:state.parts.length,payload:state.parts[index]});
        if(message.length>2048)throw new Error('Source message exceeds the script event size limit');
        system.sendScriptEvent('bct:source_data',message);state.index++;budget--;
      }
      if(state.index>=state.parts.length&&!state.acknowledged)state.index=0;
    }
  },1);
  return {dispose(){system.clearRun(first);system.clearRun(interval);system.afterEvents.scriptEventReceive.unsubscribe(receive);world.afterEvents.worldLoad.unsubscribe(begin);}};
}
