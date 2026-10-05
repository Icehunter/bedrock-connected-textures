import assert from 'node:assert/strict';
import {listenSources,checksum} from '../engine/sources.mjs';
import {publishSources} from '../engine/publisher.mjs';
const listeners=new Set(),runs=new Map(),intervals=new Map();let handle=0,maxMessage=0;
const signal={subscribe:fn=>listeners.add(fn),unsubscribe:fn=>listeners.delete(fn)};
const system={afterEvents:{scriptEventReceive:signal},run:fn=>{runs.set(++handle,fn);return handle;},
  runInterval:fn=>{intervals.set(++handle,fn);return handle;},clearRun:id=>{runs.delete(id);intervals.delete(id);},
  sendScriptEvent(id,message){maxMessage=Math.max(maxMessage,message.length);for(const fn of [...listeners])fn({id,message});}};
const world={afterEvents:{worldLoad:{subscribe(){},unsubscribe(){}}}};
const tick=()=>{for(const [id,fn] of [...runs]){runs.delete(id);fn();}for(const fn of [...intervals.values()])fn();};
const data={rules:[{id:'test',tiles:Array.from({length:1200},(_,i)=>'tile_"'+i+'\\test')}],style:'\u6f22'};
const text=JSON.stringify(data),packet={engine:'connected',provider:'source-a',digest:checksum(text),parts:text.match(/[\s\S]{1,750}/g)};
const loaded=[];
const receiver=listenSources({system,onSource:(part,id,data)=>loaded.push([id,data])});
const publisher=publishSources({system,world,sources:[packet]});
for(let i=0;i<100;i++)tick();
assert.deepEqual(loaded,[['source-a',data]]);assert.ok(maxMessage<=2048);
for(let i=0;i<10;i++)tick();assert.equal(loaded.length,1,'acknowledged data is not reprocessed');
receiver.dispose();
const next=listenSources({system,onSource:(part,id,data)=>loaded.push([id,data])});
for(let i=0;i<100;i++)tick();assert.equal(loaded.length,2,'engine restart requests the source automatically');
next.dispose();publisher.dispose();assert.equal(listeners.size,0);
const assembled=[];const shuffled=listenSources({system,onSource:(part,id,data)=>assembled.push(data)});
const send=(index,payload,digest=checksum('[1,2,3]'))=>system.sendScriptEvent('bct:source_data',JSON.stringify({engine:'terrain',provider:'source-b',digest,index,count:2,payload}));
send(1,'3]');send(1,'3]');send(0,'[1,2,');assert.deepEqual(assembled,[[1,2,3]],'out-of-order and duplicate chunks assemble exactly once');
send(0,'[5,','00000000');send(1,'6]','00000000');assert.equal(assembled.length,1,'checksum rejects mixed/corrupt data');
shuffled.dispose();
