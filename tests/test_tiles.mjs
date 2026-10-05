import test from 'node:test';
import assert from 'node:assert/strict';
import {repeatIndex,chooseTile,carrierPosition,ruleResolver,javaRandom,javaBlockSeed,javaModelRandom,javaModelIndex} from '../engine/tiles.mjs';
import {readFileSync} from 'node:fs';
import {DIRECTIONS,FACE_EDGES,MASKS} from '../engine/core.mjs';

function block(entries=[],location={x:0,y:0,z:0}) {
  const map=new Set(entries.map(p=>p.join(',')));
  const get=p=>({location:p,typeId:map.has([p.x,p.y,p.z].join(','))?'minecraft:stone':'minecraft:air',
    offset(v) {return get({x:p.x+v.x,y:p.y+v.y,z:p.z+v.z});}});
  return get(location);
}
test('lighting carriers sit outside their host faces instead of inside stacked blocks',()=>{
  const p={x:8,y:8,z:8};
  assert.deepEqual(carrierPosition(p,'north'),{x:8.5,y:8.5,z:7.998});
  assert.deepEqual(carrierPosition(p,'east'),{x:9.002,y:8.5,z:8.5});
  assert.deepEqual(carrierPosition(p,'south'),{x:8.5,y:8.5,z:9.002});
  assert.deepEqual(carrierPosition(p,'west'),{x:7.998,y:8.5,z:8.5});
  assert.deepEqual(carrierPosition(p,'up'),{x:8.5,y:9.002,z:8.5});
  assert.deepEqual(carrierPosition(p,'down'),{x:8.5,y:7.998,z:8.5});
});
test('repeat has consistent face-local directions, including negative coordinates and chunk borders',()=>{
  for(const face of Object.keys(FACE_EDGES)) for(let n=-33;n<=33;n++) {
    const p={x:n,y:n,z:n},i=repeatIndex(p,face,3,2);
    assert.ok(i>=0 && i<6);
    const right=DIRECTIONS[FACE_EDGES[face][1]],down=DIRECTIONS[FACE_EDGES[face][2]];
    const add=v=>({x:p.x+v[0],y:p.y+v[1],z:p.z+v[2]});
    assert.equal(repeatIndex(add(right),face,3,2)%3,(i%3+1)%3);
    assert.equal(Math.floor(repeatIndex(add(down),face,3,2)/3),(Math.floor(i/3)+1)%2);
  }
});
test('the 47-tile method reaches all 47 selections from all 256 neighborhoods',()=>{
  const seen=new Set();
  for(let raw=0;raw<256;raw++) {
    const points=[[0,0,0]],edges=[[0,0,-1],[1,0,0],[0,0,1],[-1,0,0]];
    for(let i=0;i<4;i++) {
      if(raw&(1<<i)) points.push(edges[i]);
      if(raw&(1<<(i+4))) points.push(edges[i].map((v,a)=>v+edges[(i+1)%4][a]));
    }
    const selected=chooseTile({method:'ctm'},block(points),'up');
    assert.ok(selected.known && selected.visible);seen.add(selected.tile);
  }
  assert.equal(seen.size,47);assert.equal(MASKS.length,47);
});
test('connections hide interior faces and horizontal/vertical use face-local edges',()=>{
  const b=block([[0,0,0],[1,0,0],[0,0,-1]]);
  assert.equal(chooseTile({method:'fixed'},b,'east').visible,false);
  assert.equal(chooseTile({method:'horizontal'},b,'up').tile,0);
  assert.equal(chooseTile({method:'vertical'},b,'up').tile,0);
});
test('a different opaque full cube hides the covered face through the geometry provider',()=>{
  const b=block([[0,0,0]]),original=b.offset;
  b.offset=delta=>delta.x===1&&delta.y===0&&delta.z===0?{typeId:'minecraft:dirt'}:original(delta);
  const selected=chooseTile({method:'fixed'},b,'east',value=>value.typeId,b.typeId,{opaque:value=>value.typeId==='minecraft:dirt'});
  assert.equal(selected.visible,false);
});

test('split lit IDs connect as one Java block while state matching remains distinct',()=>{
  const get=(x,y,z)=>({location:{x,y,z},typeId:y===0&&z===0?(x===0?'minecraft:redstone_ore':x===1?'minecraft:lit_redstone_ore':'minecraft:air'):'minecraft:air',
    permutation:{getAllStates:()=>({}),getState:()=>undefined},offset:delta=>get(x+delta.x,y+delta.y,z+delta.z)});
  const providers={logicalBlockOf:block=>block.typeId==='minecraft:lit_redstone_ore'?'minecraft:redstone_ore':block.typeId};
  assert.equal(chooseTile({method:'horizontal',connect:'block'},get(0,0,0),'up',undefined,undefined,providers).tile,0);
  assert.equal(chooseTile({method:'horizontal',connect:'state'},get(0,0,0),'up',undefined,undefined,providers).tile,3);
});
test('random stays deterministic and follows weights over many blocks',()=>{
  const rule={method:'random',tiles:['a','b'],weights:[1,3]};
  let second=0;
  for(let x=0;x<10000;x++) {
    const b=block([[x,0,0]],{x,y:0,z:0}),a=chooseTile(rule,b,'up');
    assert.equal(a.tile,chooseTile(rule,b,'up').tile);second+=a.tile;
  }
  assert.ok(second>7000 && second<8000);
});

test('Java repeat offsets and face symmetry match explicit coordinate examples',()=>{
  const p={x:2,y:3,z:4};
  const expected={down:7,up:22,north:17,south:17,west:19,east:15};
  for(const [face,index] of Object.entries(expected))assert.equal(repeatIndex(p,face,5,6),index,face);
  assert.equal(repeatIndex(p,'east',5,6,'opposite'),19);
  assert.equal(repeatIndex(p,'south',5,6,'opposite'),17);
  assert.equal(javaRandom(p,'east',0,'opposite'),javaRandom(p,'west'));
  assert.equal(javaRandom(p,'up',0,'all'),javaRandom(p,'down'));
});

test('state-axis repeat follows the column orientation and defers invalid states',()=>{
  const rule={method:'repeat',width:5,height:6,orient:'state_axis'};
  const b=block([[0,0,0]]);
  const column=axis=>({...b,permutation:{getState:name=>name==='pillar_axis'?axis:undefined}});
  assert.equal(chooseTile(rule,column('x'),'east').tile,25);
  assert.equal(chooseTile(rule,column('y'),'east').tile,4);
  assert.equal(chooseTile(rule,column('z'),'east').tile,0);
  assert.equal(chooseTile(rule,column('z'),'west').tile,4);
  assert.equal(chooseTile(rule,column(undefined),'east').tile,4);
  assert.equal(chooseTile(rule,column('unknown'),'east').known,false);
});

test('an unbound model orientation defers instead of displaying a guessed rotation',()=>{
  const selected=chooseTile({method:'fixed'},block([[0,0,0]]),'up',undefined,undefined,{orientationOf:()=>undefined});
  assert.equal(selected.known,false);assert.equal(selected.reason,'texture_orientation_provider');
});

test('random selection matches independent 64-bit reference vectors',()=>{
  const fixture=JSON.parse(readFileSync(new URL('./fixtures/tile-random.json',import.meta.url)));
  for(const vector of fixture.vectors)
    assert.equal(javaRandom(vector.location,vector.face,vector.loops),vector.expected,JSON.stringify(vector));
});

test('weighted model selection matches Java Random including rejection and signed overflow',()=>{
  const vectors=JSON.parse(readFileSync(new URL('./fixtures/java-block-model-random.json',import.meta.url)));
  for(const vector of vectors) {
    const [x,y,z]=vector.position,location={x,y,z};
    assert.equal(javaBlockSeed(location).toString(),vector.seed);
    assert.equal(javaModelRandom(location,vector.totalWeight),vector.draw,JSON.stringify(vector));
    assert.equal(javaModelIndex(location,[1,vector.totalWeight-1]),vector.draw===0?0:1);
  }
});

test('exclusive multipart choices use Java nextLong child seeds before weighting',()=>{
  const vectors=JSON.parse(readFileSync(new URL('./fixtures/java-multipart-model-random.json',import.meta.url)));
  for(const vector of vectors) {
    const [x,y,z]=vector.position,location={x,y,z};
    assert.equal(javaModelRandom(location,vector.totalWeight,'java-26.2-multipart-block-position'),vector.draw,JSON.stringify(vector));
    assert.equal(javaModelIndex(location,[1,vector.totalWeight-1],'java-26.2-multipart-block-position'),vector.draw===0?0:1);
  }
});

test('Java tile numbering matches the reference for every neighborhood on every face',()=>{
  const fixture=JSON.parse(readFileSync(new URL('./fixtures/tile-neighborhoods.json',import.meta.url)));
  for(const face of Object.keys(FACE_EDGES)) for(let raw=0;raw<256;raw++) {
    const edges=FACE_EDGES[face].map(direction=>DIRECTIONS[direction]);
    const points=[[0,0,0]];
    for(let i=0;i<4;i++) {
      if(raw&(1<<i))points.push(edges[i]);
      if(raw&(1<<(i+4)))points.push(edges[i].map((v,axis)=>v+edges[(i+1)%4][axis]));
    }
    for(const [method,expected] of [['ctm',fixture.expected],['horizontal+vertical',fixture.hv],['vertical+horizontal',fixture.vh]])
      assert.equal(chooseTile({method},block(points),face).tile,expected[raw],`${method} ${face} ${raw}`);
  }
});

test('all UV rotations and reflections query Java texture-space neighbors before selecting a tile',()=>{
  const fixture=JSON.parse(readFileSync(new URL('./fixtures/tile-neighborhoods.json',import.meta.url)));
  // Upstream DirectionMaps arrays in texture left/down/right/up order,
  // expressed in the world-face up/right/down/left edge indices.
  const javaDirections=[[3,2,1,0],[2,1,0,3],[1,0,3,2],[0,3,2,1],
    [1,2,3,0],[0,1,2,3],[3,0,1,2],[2,3,0,1]];
  for(const face of Object.keys(FACE_EDGES))for(let orientation=0;orientation<8;orientation++)for(let raw=0;raw<256;raw++) {
    const edges=FACE_EDGES[face].map(direction=>DIRECTIONS[direction]);
    const points=[[0,0,0]];
    for(let i=0;i<4;i++) {
      if(raw&(1<<i))points.push(edges[i]);
      if(raw&(1<<(i+4)))points.push(edges[i].map((v,axis)=>v+edges[(i+1)%4][axis]));
    }
    const occupied=new Set(points.map(point=>point.join(',')));
    const oriented=javaDirections[orientation].toReversed().map(index=>edges[index]);
    let textureMask=0;
    for(let edge=0;edge<4;edge++) {
      if(occupied.has(oriented[edge].join(',')))textureMask|=1<<edge;
      if(occupied.has(oriented[edge].map((v,axis)=>v+oriented[(edge+1)%4][axis]).join(',')))textureMask|=1<<(edge+4);
    }
    const b=block(points),providers={orientationOf:()=>orientation};
    const horizontal=((textureMask&8)?1:0)+((textureMask&2)?2:0);
    const vertical=((textureMask&4)?1:0)+((textureMask&1)?2:0);
    for(const [method,expected] of [['ctm',fixture.expected[textureMask]],['horizontal+vertical',fixture.hv[textureMask]],
      ['vertical+horizontal',fixture.vh[textureMask]],['horizontal',[3,2,0,1][horizontal]],['vertical',[3,2,0,1][vertical]]]) {
      const result=chooseTile({method},b,face,undefined,undefined,providers);
      assert.equal(result.tile,expected,`${method} ${face} orientation ${orientation} raw ${raw}`);
      assert.equal(result.orientation,orientation);
    }
    const fixed=chooseTile({method:'ctm',orient:'none'},b,face,undefined,undefined,providers);
    assert.equal(fixed.tile,fixture.expected[raw]);assert.equal(fixed.orientation,orientation);
  }
});

test('output matching chains connection, repeat and weighted variation without reapplying a rule',()=>{
  const rules=[
    {id:'base',blocks:['minecraft:stone'],faces:['up'],method:'horizontal',tiles:['right','middle','left','alone']},
    {id:'repeat',blocks:[],matchTiles:['middle'],faces:['up'],method:'repeat',width:2,height:1,tiles:['middle','middle_b']},
    {id:'random',blocks:[],matchTiles:['middle','middle_b'],faces:['up'],method:'random',tiles:['variant_a','variant_b'],weights:[0,1]},
  ];
  const resolve=ruleResolver(rules),b=block([[0,0,0],[-1,0,0],[1,0,0]]);
  const selected=resolve(b,'up');
  assert.equal(selected.rule.id,'random');assert.equal(selected.texture,'variant_b');
  assert.equal(selected.tile,1);
});

test('tile rules precede block rules; skip falls through and default restores the native face',()=>{
  const a={id:'block',blocks:['minecraft:stone'],faces:['up'],method:'fixed',tiles:['base']};
  const b={id:'tile',blocks:[],matchTiles:['minecraft:stone'],faces:['up'],method:'fixed',tiles:['tile']};
  const stone=block([[0,0,0]]);
  assert.equal(ruleResolver([a,b])(stone,'up').texture,'tile');
  assert.equal(ruleResolver([a,{...b,tiles:['<skip>']}])(stone,'up').texture,'base');
  assert.equal(ruleResolver([a,{...b,tiles:['<default>']}])(stone,'up').visible,false);
});

test('ctm_repeat preserves connection cases while advancing the surface pattern',()=>{
  const rule={method:'ctm_repeat',width:2,height:2};
  for(const face of Object.keys(FACE_EDGES)) for(let x=-17;x<=17;x++) {
    const location={x,y:8,z:3};
    const b=block([[x,8,3]],location);
    const selected=chooseTile(rule,b,face);
    assert.equal(Math.floor(selected.tile/4),chooseTile({method:'ctm'},b,face).tile);
    assert.equal(selected.tile%4,repeatIndex(location,face,2,2));
    assert.ok(selected.tile>=0 && selected.tile<188);
  }
});

test('horizontal repeat retains end connections and a three-block wood pattern',()=>{
  const b=block([[0,0,0],[1,0,0]]);
  const rule={method:'horizontal_repeat',width:3,height:1};
  const selected=chooseTile(rule,b,'up');
  assert.equal(Math.floor(selected.tile/3),chooseTile({method:'horizontal'},b,'up').tile);
  assert.equal(selected.tile%3,repeatIndex(b.location,'up',3,1));
});

test('biome providers preserve inclusion, exclusion, priority and unknown locations',()=>{
  const base={id:'base',method:'fixed',blocks:['minecraft:stone'],faces:['up'],tiles:['dry']};
  const wet={...base,id:'wet',filename:'a',tiles:['wet'],biomes:{ids:['minecraft:swampland'],exclude:false}};
  const fallback={...base,filename:'b'};
  const b=block([[0,0,0]]);
  assert.equal(ruleResolver([wet,fallback],{biomeOf:()=> 'minecraft:swampland'})(b,'up').texture,'wet');
  assert.equal(ruleResolver([wet,fallback],{biomeOf:()=> 'minecraft:plains'})(b,'up').texture,'dry');
  assert.equal(ruleResolver([wet,fallback])(b,'up').known,false);
  const excluded={...wet,biomes:{...wet.biomes,exclude:true}};
  assert.equal(ruleResolver([excluded,fallback],{biomeOf:()=> 'minecraft:swampland'})(b,'up').texture,'dry');
  assert.equal(ruleResolver([excluded,fallback],{biomeOf:()=> 'minecraft:plains'})(b,'up').texture,'wet');
});

test('block-specific states remain alternatives and constrain tile matching',()=>{
  const rule={method:'fixed',blocks:['minecraft:stone','minecraft:oak_log'],matchTiles:['shared'],faces:['up'],tiles:['selected'],
    blockMatchers:[{block:'minecraft:stone',states:{}},{block:'minecraft:oak_log',states:{pillar_axis:['x','z']}}]};
  const resolve=ruleResolver([rule]),b=block([[0,0,0]]);
  assert.equal(resolve(b,'up',()=> 'shared').texture,'selected');
  const log={...b,typeId:'minecraft:oak_log',permutation:{getState:()=> 'y'}};
  assert.equal(resolve(log,'up',()=> 'shared').visible,false);
  assert.equal(resolve({...log,permutation:{getState:()=> 'z'}},'up',()=> 'shared').texture,'selected');
  assert.equal(resolve({...b,typeId:'minecraft:dirt'},'up',()=> 'shared').visible,false);
  assert.equal(resolve(b,'up',()=> 'different_face_texture').visible,false);
  assert.equal(resolve({...log,permutation:{getState:()=> 'z'}},'up',()=> 'endgrain').visible,false);
});
