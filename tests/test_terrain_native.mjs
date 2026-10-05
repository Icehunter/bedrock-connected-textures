import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {NativeSurfaces} from '../engine/terrain-native.mjs';
import {evaluate} from '../engine/terrain-rules.mjs';
import {traceSurface} from '../engine/trace.mjs';

const releaseProvider = JSON.parse(fs.readFileSync(new URL('./fixtures/release-provider.json',import.meta.url)));
releaseProvider.biome_colors = {'minecraft:plains':[.6,.78,.55]};
// Exercise the generic five-material selector independently of carrier choice.
const provider = structuredClone(releaseProvider);
for(const effect of Object.values(provider.effects)) { delete effect.native_disabled; delete effect.entity_surface; delete effect.compact_height; delete effect.height_profiles; delete effect.height_tuning; }
const ID = provider.effects.grass.native_block;
const output = {provider:provider.id,entity:provider.effects.grass.entity,tile:33,face:'up'};
function host() {
  const cells = new Map(), data = new Map(), writes = [];
  const dimension = {id:'minecraft:overworld',getBiome:()=>({id:'minecraft:plains'}),getBlock:location => {
    const key = [location.x,location.y,location.z].join(',');
    return {location,dimension,
      get typeId() { return cells.get(key)?.type ?? 'minecraft:air'; },
      get isAir() { return this.typeId === 'minecraft:air'; },
      get permutation() { return {getState:name => cells.get(key)?.states?.[name]}; },
      offset(delta) { return dimension.getBlock(Object.fromEntries(['x','y','z'].map(axis => [axis,location[axis]+delta[axis]]))); },
      getComponent() { throw new Error('Native tint must not read a script color'); },
      setType(type) { writes.push({key,type});cells.set(key,{type}); },
      setPermutation(value) { writes.push({key,...value});cells.set(key,value); }};
  }};
  const world = {getDimension:()=>dimension,getDynamicProperty:key=>data.get(key),setDynamicProperty:(key,value)=>data.set(key,value)};
  const native = new NativeSurfaces(world,(type,states)=>({type,states}),()=>[provider]);
  cells.set('0,0,0',{type:'minecraft:stone'});
  return {cells,data,writes,dimension,world,native};
}
const location = {x:0,y:0,z:0};

test('the traced sand block retains its NE contact through two cobblestone bridges',()=>{
  const trace=JSON.parse(fs.readFileSync(new URL('./fixtures/terrain-live-corner.json',import.meta.url)));
  const h=host();
  for(let row=0;row<7;row++)for(let col=0;col<7;col++) {
    const cell=trace.rows[row][col];if(!cell)continue;
    h.cells.set(`${col-3},0,${row-3}`,{type:trace.types[cell[0]]});
    h.cells.set(`${col-3},1,${row-3}`,{type:trace.types[cell[1]],states:{'bct:edges':cell[2],'bct:corners':cell[3]}});
  }
  assert.equal(h.dimension.getBlock(location).typeId,'minecraft:sand');
  assert.equal(h.dimension.getBlock({x:1,y:0,z:-1}).typeId,'minecraft:grass_block');
  assert.equal(h.dimension.getBlock({x:0,y:0,z:-1}).typeId,'minecraft:cobblestone');
  assert.equal(h.dimension.getBlock({x:1,y:0,z:0}).typeId,'minecraft:cobblestone');
  const wanted=evaluate(provider,h.dimension.getBlock(location));
  assert.equal(wanted.length,1);assert.equal(wanted[0].tile,16);
  h.native.sync(h.dimension,location,wanted);
  assert.equal(h.cells.get('0,1,0').states['bct:quadrant0'],4);
});

test('the traced red-sand block keeps its west sand edge and southeast corner',()=>{
  const trace=JSON.parse(fs.readFileSync(new URL('./fixtures/terrain-live-red-sand.json',import.meta.url)));
  const h=host();
  for(let row=0;row<7;row++)for(let col=0;col<7;col++) {
    const cell=trace.rows[row][col];if(!cell)continue;
    h.cells.set(`${col-3},0,${row-3}`,{type:trace.types[cell[0]]});
    h.cells.set(`${col-3},1,${row-3}`,{type:trace.types[cell[1]]});
  }
  const anchor={x:-1,y:0,z:0};
  assert.equal(h.dimension.getBlock(anchor).typeId,'minecraft:red_sand');
  assert.equal(h.dimension.getBlock({x:-2,y:0,z:0}).typeId,'minecraft:sand');
  assert.equal(h.dimension.getBlock({x:0,y:0,z:1}).typeId,'minecraft:sand');
  const wanted=evaluate(provider,h.dimension.getBlock(anchor));
  assert.equal(wanted.find(item=>item.entity===provider.effects.sand.entity).tile,40);
  h.native.sync(h.dimension,anchor,wanted);
  assert.deepEqual(h.cells.get('-1,1,0').states,{
    'bct:quadrant0':0,'bct:quadrant1':8,
    'bct:quadrant2':0,'bct:quadrant3':6,
  });
});

test('live trace records actual and selected masks in a bounded compass grid without writes',()=>{
  const h=host();h.cells.set('1,0,-1',{type:'minecraft:grass_block'});
  h.cells.set('0,0,-1',{type:'minecraft:stone'});h.cells.set('1,0,0',{type:'minecraft:stone'});
  h.native.sync(h.dimension,location,evaluate(provider,h.dimension.getBlock(location)));
  const before=h.writes.length;
  const player={dimension:h.dimension,location:{x:0,y:2,z:0},getRotation:()=>({x:45,y:120}),
    getBlockFromViewDirection:()=>({block:h.dimension.getBlock(location),face:'Up',faceLocation:{x:.5,y:1,z:.5}})};
  const trace=traceSurface(player,[provider],evaluate);
  assert.deepEqual(trace.anchor,location);assert.equal(trace.rows.length,7);
  assert.ok(trace.rows.every(row=>row.length===7));
  const center=trace.rows[3][3];assert.equal(trace.types[center[0]],'minecraft:stone');
  assert.equal(center[2],0);assert.equal(center[3],1);assert.equal(center[4][0][1],16);
  assert.equal(trace.types[trace.rows[2][4][0]],'minecraft:grass_block');
  assert.equal(h.writes.length,before);
});

test('every source direction selects its corresponding native edge or corner bit',()=>{
  const directions = [[0,-1],[1,0],[0,1],[-1,0],[1,-1],[1,1],[-1,1],[-1,-1]];
  for (const [index,[x,z]] of directions.entries()) {
    const h=host();
    for(const [cx,cz] of directions.slice(0,4))h.cells.set(`${cx},0,${cz}`,{type:'minecraft:stone'});
    h.cells.set(`${x},0,${z}`,{type:'minecraft:grass_block'});
    const wanted=evaluate(provider,h.dimension.getBlock(location));
    assert.equal(wanted.length,1);assert.equal(wanted[0].tile,1<<index);
    h.native.sync(h.dimension,location,wanted);
    const states=h.cells.get('0,1,0').states;
    for(let q=0;q<4;q++)assert.equal(states['bct:quadrant'+q],q===index%4?(index<4?1:4):0);
  }
});

test('native grass uses masks and preserves its supporting block',()=>{
  const h=host();h.native.sync(h.dimension,location,[output]);
  assert.equal(h.cells.get('0,0,0').type,'minecraft:stone');
  assert.equal(h.cells.get('0,1,0').type,ID);
  assert.deepEqual(h.cells.get('0,1,0').states,{'bct:quadrant0':1,'bct:quadrant1':4,'bct:quadrant2':0,'bct:quadrant3':0});
  const count=h.writes.length;h.native.sync(h.dimension,location,[output]);
  assert.equal(h.writes.length,count);
});

test('solid blocks, water and plants are never overwritten',()=>{
  for(const type of ['minecraft:oak_planks','minecraft:water','minecraft:tall_grass']) {
    const h=host();h.cells.set('0,1,0',{type});h.native.sync(h.dimension,location,[output]);
    assert.equal(h.cells.get('0,1,0').type,type);assert.equal(h.writes.length,0);
  }
});

test('player replacement survives both reconciliation and clear',()=>{
  const h=host();h.native.sync(h.dimension,location,[output]);
  h.cells.set('0,1,0',{type:'minecraft:oak_planks'});
  h.native.sync(h.dimension,location,[]);h.native.clear();
  assert.equal(h.cells.get('0,1,0').type,'minecraft:oak_planks');
  assert.equal(h.native.size,0);
});

test('ownership survives reload and clear removes only the carrier',()=>{
  const h=host();h.native.sync(h.dimension,location,[output]);
  const restarted=new NativeSurfaces(h.world,(type,states)=>({type,states}),()=>[provider]);
  restarted.recover();assert.equal(restarted.size,1);restarted.clear();
  assert.equal(h.cells.get('0,1,0').type,'minecraft:air');
  assert.equal(h.cells.get('0,0,0').type,'minecraft:stone');
});

test('rules retain mixed material transitions with a carrier above',()=>{
  const h=host();h.cells.set('0,0,-1',{type:'minecraft:grass_block'});h.cells.set('1,0,0',{type:'minecraft:sand'});
  const block=h.dimension.getBlock(location);
  const before=evaluate(provider,block);assert.equal(before.length,2);
  assert.equal(before[0].tint,undefined);
  h.native.sync(h.dimension,location,before);
  assert.deepEqual(evaluate(provider,block),before);
  const writes=h.writes.length;h.native.sync(h.dimension,location,[...before].reverse());
  assert.equal(h.writes.length,writes);
  assert.equal(h.cells.get('0,1,0').states['bct:quadrant0'],1);
  assert.equal(h.cells.get('0,1,0').states['bct:quadrant1'],2);
  h.cells.set('-1,0,0',{type:'minecraft:red_sand'});
  h.native.sync(h.dimension,location,evaluate(provider,block));
  assert.equal(h.cells.get('0,1,0').states['bct:quadrant0'],1);
  assert.equal(h.cells.get('0,1,0').states['bct:quadrant1'],2);
  assert.equal(h.cells.get('0,1,0').states['bct:quadrant3'],3);
});

test('no supporting block means no native transition even with four sand corners',()=>{
  const h=host();h.cells.delete('0,0,0');
  for(const [x,z] of [[-1,-1],[1,-1],[1,1],[-1,1]])h.cells.set(`${x},0,${z}`,{type:'minecraft:sand'});
  h.native.sync(h.dimension,location,evaluate(provider,h.dimension.getBlock(location)));
  assert.equal(h.native.size,0);assert.equal(h.writes.length,0);
});

test('removing the supporting surface clears the carrier immediately',()=>{
  const h=host();h.cells.set('0,0,-1',{type:'minecraft:grass_block'});
  h.native.sync(h.dimension,location,evaluate(provider,h.dimension.getBlock(location)));
  assert.equal(h.native.size,1);
  h.cells.delete('0,0,0');
  h.native.sync(h.dimension,location,evaluate(provider,h.dimension.getBlock(location)));
  assert.equal(h.cells.get('0,1,0').type,'minecraft:air');
  assert.equal(h.native.size,0);
});

test('retired provider carriers remain tracked until their chunk can be cleaned',()=>{
  const h=host();h.native.sync(h.dimension,location,[output]);
  const retired=new NativeSurfaces(h.world,(type,states)=>({type,states}),()=>[]);
  retired.recover();assert.equal(retired.size,1);
  retired.clear();
  assert.equal(h.cells.get('0,1,0').type,'minecraft:air');
  assert.equal(retired.size,0);
});

test('carrier retirement preserves a block that replaced the recorded owned type',()=>{
  const h=host();h.native.sync(h.dimension,location,[output]);assert.equal(h.native.size,1);
  const replacement='example:other_owned_surface';
  h.native.providers=()=>[provider,{effects:{replacement:{native_block:replacement}}}];h.native.idCache=null;
  h.cells.set('0,1,0',{type:replacement});
  h.native.clear();assert.equal(h.cells.get('0,1,0').type,replacement);
  assert.equal(h.native.size,0);
});

test('all mixed neighborhoods preserve every selected material in one carrier',()=>{
  const offsets=[[0,-1],[1,0],[0,1],[-1,0],[1,-1],[1,1],[-1,1],[-1,-1]];
  const types=['minecraft:stone','minecraft:grass_block','minecraft:sand','minecraft:red_sand'];
  const allowed=new Set(Array.from({length:16},(_,i)=>i).filter(i=>![5,10,15].includes(i)));
  const h=host();
  for(let code=0;code<65536;code++) {
    for(let i=0;i<8;i++)h.cells.set(`${offsets[i][0]},0,${offsets[i][1]}`,{type:types[(code>>(2*i))&3]});
    const outputs=evaluate(provider,h.dimension.getBlock(location));
    h.native.sync(h.dimension,location,outputs);
    if(!outputs.length)continue;
    const states=h.cells.get('0,1,0').states;
    for(const output of outputs) {
      const id=Object.values(provider.effects).find(e=>e.entity===output.entity).native_material;
      let mask=0;
      for(let i=0;i<4;i++) {
        const value=states['bct:quadrant'+i];assert.ok(allowed.has(value));
        if(value%4===id)mask|=1<<i;
        if(Math.floor(value/4)===id)mask|=1<<(i+4);
      }
      assert.equal(mask,output.tile,`neighborhood ${code}, material ${id}`);
    }
  }
});


test('all sand pairs spread only from higher priority into lower priority in all directions',()=>{
  const names=['sand','suspicious_sand','red_sand','soul_sand'];
  for(let a=0;a<names.length;a++)for(let b=0;b<names.length;b++)for(const [x,z] of [[0,-1],[1,0],[0,1],[-1,0]]) {
    const h=host();h.cells.set('0,0,0',{type:'minecraft:'+names[b]});h.cells.set(`${x},0,${z}`,{type:'minecraft:'+names[a]});
    const wanted=evaluate(provider,h.dimension.getBlock(location));
    assert.equal(wanted.length,a<b?1:0,`${names[a]} onto ${names[b]}`);
    if(a<b)assert.equal(wanted[0].entity,provider.effects[names[a]].entity);
  }
});

test('all five sources can coexist with stable priority and distinct native materials',()=>{
  const h=host();
  const contacts=[['grass',1],['sand',2],['red_sand',4],['suspicious_sand',8],['soul_sand',16]];
  const wanted=contacts.map(([name,tile])=>({provider:provider.id,entity:provider.effects[name].entity,tile}));
  h.native.sync(h.dimension,location,wanted);
  assert.deepEqual(h.cells.get('0,1,0').states,{'bct:quadrant0':1,'bct:quadrant1':2,'bct:quadrant2':3,'bct:quadrant3':0});
  assert.deepEqual(h.cells.get('0,2,0').states,{'bct:quadrant0':8,'bct:quadrant1':0,'bct:quadrant2':0,'bct:quadrant3':1});
  const writes=h.writes.length;h.native.sync(h.dimension,location,[...wanted].reverse());assert.equal(h.writes.length,writes);
  assert.equal(h.native.offset(provider.effects.suspicious_sand.native_block),2);
  const competing=Object.entries(provider.effects).map(([name,effect])=>({provider:provider.id,entity:effect.entity,tile:1}));
  h.native.sync(h.dimension,location,competing.reverse());
  assert.equal(h.cells.get('0,1,0').states['bct:quadrant0'],1);
  assert.equal(h.cells.get('0,2,0').type,'minecraft:air');
});

test('secondary carriers preserve player blocks, recover, and clear when the surface is covered',()=>{
  const extra={provider:provider.id,entity:provider.effects.suspicious_sand.entity,tile:2};
  for(const type of ['minecraft:oak_planks','minecraft:water','minecraft:tall_grass']) {
    const h=host();h.cells.set('0,2,0',{type});h.native.sync(h.dimension,location,[output,extra]);h.native.clear();
    assert.equal(h.cells.get('0,2,0').type,type);
  }
  const h=host();h.native.sync(h.dimension,location,[output,extra]);assert.equal(h.native.size,2);
  const restarted=new NativeSurfaces(h.world,(type,states)=>({type,states}),()=>[provider]);
  restarted.recover();assert.equal(restarted.size,2);
  h.cells.set('0,1,0',{type:'minecraft:oak_planks'});restarted.sync(h.dimension,location,[output,extra]);
  assert.equal(h.cells.get('0,1,0').type,'minecraft:oak_planks');assert.equal(h.cells.get('0,2,0').type,'minecraft:air');assert.equal(restarted.size,0);
});



test('a rejected secondary block cannot abort other overlays and retries are bounded',()=>{
  const h=host();let tick=0, reject=true, attempts=0;const errors=[];
  const extraID=provider.effects.suspicious_sand.native_block;
  const native=new NativeSurfaces(h.world,(type,states)=>{
    if(type===extraID) {attempts++;if(reject)throw new Error('Rejected block geometry');}
    return {type,states};
  },()=>[provider],96,()=>tick,message=>errors.push(message));
  const extra={provider:provider.id,entity:provider.effects.suspicious_sand.entity,tile:2};
  assert.doesNotThrow(()=>native.sync(h.dimension,location,[output,extra]));
  assert.equal(h.cells.get('0,1,0').type,ID);assert.equal(native.size,1);assert.equal(attempts,1);
  for(let i=0;i<20;i++)native.sync(h.dimension,location,[output,extra]);
  assert.equal(attempts,1);assert.equal(errors.length,1);
  native.sync(h.dimension,{x:1,y:0,z:0},[output]);assert.equal(h.cells.get('1,1,0').type,ID);
  reject=false;tick=1200;native.sync(h.dimension,location,[output,extra]);
  assert.equal(attempts,2);assert.equal(h.cells.get('0,2,0').type,extraID);assert.equal(native.size,3);
});

test('live diagnostics decode every material on both carrier heights',()=>{
  const h=host();
  const extra={provider:provider.id,entity:provider.effects.suspicious_sand.entity,tile:2};
  h.native.sync(h.dimension,location,[output,extra]);
  const player={dimension:h.dimension,location:{x:0,y:2,z:0},getRotation:()=>({x:45,y:120}),getBlockFromViewDirection:()=>({block:h.dimension.getBlock({x:0,y:2,z:0}),face:'Up'})};
  const trace=traceSurface(player,[provider],evaluate);
  assert.deepEqual(trace.anchor,location);
  const carriers=trace.rows[3][3][5];assert.equal(carriers.length,2);
  assert.equal(trace.types[carriers[1][1]],provider.effects.suspicious_sand.native_block);
  assert.deepEqual(carriers[1][2],[0,1,0,0]);
  assert.deepEqual(carriers[1][3],[[provider.effects.suspicious_sand.entity,2,0],[provider.effects.soul_sand.entity,0,0]]);
  assert.deepEqual(carriers[0][3][0],[provider.effects.grass.entity,1,2]);
});


test('the reported mixed-sand layout selects extra sands on cobble and preserves grass and normal sand',()=>{
  const trace=JSON.parse(fs.readFileSync(new URL('./fixtures/terrain-live-mixed-sands.json',import.meta.url)));
  const h=host();
  for(let row=0;row<7;row++)for(let col=0;col<7;col++) {
    const cell=trace.rows[row][col];if(!cell)continue;
    h.cells.set(`${col-3},0,${row-3}`,{type:trace.types[cell[0]]});
    h.cells.set(`${col-3},1,${row-3}`,{type:trace.types[cell[1]]});
  }
  // Cobble immediately north of suspicious sand: south-facing suspicious edge.
  const cobble={x:0,y:0,z:-1};assert.equal(h.dimension.getBlock(cobble).typeId,'minecraft:cobblestone');
  const wanted=evaluate(provider,h.dimension.getBlock(cobble));
  assert.equal(wanted.find(item=>item.entity===provider.effects.suspicious_sand.entity).tile,4);
  h.native.sync(h.dimension,cobble,wanted);
  assert.equal(h.cells.get('0,2,-1').states['bct:quadrant2'],1);
  // Next cobble has the soul-sand south edge and suspicious-sand southwest corner.
  const next={x:1,y:0,z:-1};h.native.sync(h.dimension,next,evaluate(provider,h.dimension.getBlock(next)));
  assert.deepEqual(h.cells.get('1,2,-1').states,{'bct:quadrant0':0,'bct:quadrant1':0,'bct:quadrant2':6,'bct:quadrant3':0});
  // The red host retains normal-sand west and southeast contacts.
  const red={x:2,y:0,z:2};assert.equal(h.dimension.getBlock(red).typeId,'minecraft:red_sand');
  h.native.sync(h.dimension,red,evaluate(provider,h.dimension.getBlock(red)));
  assert.deepEqual(h.cells.get('2,1,2').states,{'bct:quadrant0':0,'bct:quadrant1':8,'bct:quadrant2':0,'bct:quadrant3':6});
  for(let row=1;row<6;row++)for(let col=1;col<6;col++) {
    const old=trace.rows[row][col];if(!old)continue;
    const anchor={x:col-3,y:0,z:row-3};const outputs=evaluate(provider,h.dimension.getBlock(anchor));
    const grass=outputs.find(item=>item.entity===provider.effects.grass.entity)?.tile ?? 0;
    assert.equal(grass,old[2]+16*old[3],`grass at ${anchor.x},${anchor.z}`);
  }
});


test('switching providers keeps native grass and clears the removed sand carrier',()=>{
  const h=host();h.native.sync(h.dimension,location,[output,{provider:provider.id,entity:provider.effects.suspicious_sand.entity,tile:2}]);
  const native=new NativeSurfaces(h.world,(type,states)=>({type,states}),()=>[releaseProvider]);
  native.sync(h.dimension,location,[output,{provider:provider.id,entity:provider.effects.suspicious_sand.entity,tile:2}]);
  assert.equal(h.cells.get('0,1,0').type,releaseProvider.effects.grass.native_block);assert.equal(h.cells.get('0,2,0').type,'minecraft:air');assert.equal(native.size,1);
  assert.ok(native.ids().has(releaseProvider.effects.suspicious_sand.native_block));
  assert.equal(native.offset(releaseProvider.effects.suspicious_sand.native_block),2);
  const writes=h.writes.length;
  native.sync(h.dimension,location,Object.values(releaseProvider.effects).map(effect=>({provider:releaseProvider.id,entity:effect.entity,tile:8})));
  assert.equal(h.cells.get('0,1,0').type,releaseProvider.effects.grass.native_block);assert.equal(h.cells.get('0,2,0').type,'minecraft:air');assert.equal(native.size,1);
  assert.equal(h.cells.get('0,0,0').type,'minecraft:stone');
});

test('grass carrier occupies only air and preserves terrain, water and plants',()=>{
  for(const type of ['minecraft:air','minecraft:oak_planks','minecraft:water','minecraft:tall_grass']) {
    const h=host();
    h.cells.set('0,1,0',{type});h.cells.set('0,2,0',{type});
    const native=new NativeSurfaces(h.world,(block,states)=>({type:block,states}),()=>[releaseProvider]);
    const outputs=Object.values(releaseProvider.effects).map(effect=>{
      assert.equal(Boolean(effect.entity_surface),effect.biome_tint!=='grass');
      return {provider:releaseProvider.id,entity:effect.entity,tile:1};
    });
    native.sync(h.dimension,location,outputs);native.clear();
    if(type!=='minecraft:air') assert.equal(h.writes.length,0);
    assert.equal(native.size,0);
    assert.equal(h.cells.get('0,0,0').type,'minecraft:stone');
    assert.equal(h.cells.get('0,1,0').type,type);assert.equal(h.cells.get('0,2,0').type,type);
  }
});

function entityTraceFixture() {
  const h=host();
  h.cells.set('0,0,-1',{type:'minecraft:grass_block'});
  h.cells.set('1,0,0',{type:'minecraft:suspicious_sand'});
  h.cells.set('-1,0,0',{type:'minecraft:soul_sand'});
  const native=new NativeSurfaces(h.world,(type,states)=>({type,states}),()=>[releaseProvider]);
  native.sync(h.dimension,location,evaluate(releaseProvider,h.dimension.getBlock(location)));
  const player={dimension:h.dimension,location:{x:0,y:2,z:0},getRotation:()=>({x:45,y:120}),
    getBlockFromViewDirection:()=>({block:h.dimension.getBlock(location),face:'Up'})};
  let nextID=0;
  function actor(name,mask,active=true) {
    const entity={id:'trace-'+nextID++,typeId:releaseProvider.effects[name].entity,isValid:true,location:{x:.5,y:1,z:.5},
      getProperty:key=>key==='bct:active'?active:key==='bct:tile'?mask:undefined};
    return {entity,dimension:h.dimension.id,...location};
  }
  const trace=records=>traceSurface(player,[releaseProvider],evaluate,records);
  const atCenter=entry=>entry.host.x===0&&entry.host.y===0&&entry.host.z===0;
  return {...h,actor,trace,atCenter};
}

test('entity diagnostics append active masks and locations without changing the cell fields',()=>{
  const h=entityTraceFixture();
  const suspicious=h.actor('suspicious_sand',2),soul=h.actor('soul_sand',8),grass=h.actor('grass',1);
  const inactive=h.actor('suspicious_sand',128,false),invalid=h.actor('soul_sand',64);
  invalid.entity.isValid=false;
  const otherDimension=h.actor('soul_sand',32);otherDimension.dimension='minecraft:nether';
  const baseline=h.trace([]),before=h.writes.length;
  const trace=h.trace([suspicious,soul,grass,inactive,invalid,otherDimension]);
  for(let row=0;row<7;row++)for(let col=0;col<7;col++) {
    assert.deepEqual(trace.rows[row][col]?.slice(0,6),baseline.rows[row][col]?.slice(0,6));
  }
  assert.deepEqual(trace.rows[3][3][6],[
    [suspicious.entity.typeId,2,suspicious.entity.location],
    [soul.entity.typeId,8,soul.entity.location],
  ]);
  assert.deepEqual(trace.mismatches.filter(h.atCenter),[]);
  assert.equal(h.writes.length,before);
});

test('entity diagnostics report missing or wrong masks and duplicate active carriers',()=>{
  const h=entityTraceFixture();
  const soul=h.actor('soul_sand',8),entity=releaseProvider.effects.suspicious_sand.entity;
  const mismatch=records=>h.trace(records).mismatches.filter(h.atCenter).find(entry=>entry.entity===entity);
  for(const [records,actual] of [[[soul],0],[[soul,h.actor('suspicious_sand',4)],4]]) {
    const entry=mismatch(records);
    assert.ok(entry);assert.equal(entry.renderer,'entity');assert.equal(entry.selected,2);assert.equal(entry.actual,actual);
  }
  const duplicate=mismatch([soul,h.actor('suspicious_sand',2),h.actor('suspicious_sand',2)]);
  assert.ok(duplicate);assert.equal(duplicate.renderer,'entity');assert.equal(duplicate.selected,2);
});
