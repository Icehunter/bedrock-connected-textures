import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createConnectedSource} from '../engine/connected.mjs';
import {createCarrierBudget} from '../engine/settings.mjs';
import {fakeBedrock} from './fake_bedrock.mjs';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
const SAMPLES = process.env.BEDROCK_SAMPLES ?? fileURLToPath(new URL('../../bedrock-samples', import.meta.url));
const samplesUrl = (rel) => pathToFileURL(resolve(SAMPLES, rel));

const data=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');

function fixture() {
  const channels=[],runs=new Map(),intervals=new Map(),jobs=new Map(),actors=[];
  let next=0;
  const channel=()=>{
    const listeners=new Set();
    const result={subscribe(fn){listeners.add(fn);},unsubscribe(fn){listeners.delete(fn);},
      emit(event){for(const fn of [...listeners])fn(event);},get size(){return listeners.size;}};
    channels.push(result);return result;
  };
  const dimension={id:'minecraft:overworld',heightRange:{min:0,max:16},
    isChunkLoaded:()=>false,getEntities:({type}={})=>actors.filter(actor=>actor.isValid&&(!type||actor.typeId===type))};
  const typeAt=p=>p.x===0&&p.y===4&&p.z===0?'minecraft:stone':
    p.x===4&&p.y===4&&p.z===0?'minecraft:brick_block':'minecraft:air';
  dimension.getBlock=location=>({location,dimension,typeId:typeAt(location),permutation:{getState:()=>undefined},
    offset(delta){return dimension.getBlock(Object.fromEntries(['x','y','z'].map(axis=>[axis,location[axis]+delta[axis]])));}});
  const actor=(typeId,location)=>{
    const dynamic=new Map(),properties=new Map(),tags=new Set();
    const result={id:'actor-'+actors.length,typeId,location,dimension,isValid:true,
      addTag:tag=>tags.add(tag),removeTag:tag=>tags.delete(tag),hasTag:tag=>tags.has(tag),getTags:()=>[...tags],remove(){this.isValid=false;},
      teleport(value){this.location=value;},setRotation(){},setProperty:(key,value)=>properties.set(key,value),
      getProperty:key=>properties.get(key),
      getDynamicProperty:key=>dynamic.get(key),setDynamicProperty:(key,value)=>dynamic.set(key,value)};
    actors.push(result);return result;
  };
  const world={afterEvents:{playerPlaceBlock:channel(),playerBreakBlock:channel(),entityLoad:channel()},
    getDynamicProperty:()=>undefined,getDimension:()=>dimension,getAllPlayers:()=>[],
    structureManager:{place(type,where,location){actor(type,{x:location.x+.5,y:location.y,z:location.z+.5});}}};
  const system={afterEvents:{scriptEventReceive:channel()},
    run(fn){const id=next++;runs.set(id,fn);return id;},
    runInterval(fn){const id=next++;intervals.set(id,fn);return id;},
    runJob(job){const id=next++;jobs.set(id,job);return id;},
    clearRun(id){runs.delete(id);intervals.delete(id);},clearJob(id){jobs.delete(id);}};
  const flush=()=>{while(runs.size){const [id,fn]=runs.entries().next().value;runs.delete(id);fn();}};
  return {world,system,dimension,actor,actors,channels,flush,runs,intervals,jobs,
    BlockVolume:class {},BlockTypes:{get:()=>({})},GraphicsMode:{RayTraced:'RayTraced',Fancy:'Fancy',Deferred:'Deferred',Simple:'Simple'}};
}

async function runtimeFactory(env) {
  const carriers=createCarrierBudget(()=>({maxCarriers:100000,maxCarriersPerChunk:100000}));
  return (configuration,options={})=>createConnectedSource(configuration,{api:env,carriers,...options});
}

test('authored model planes keep noncube eligibility, random alternatives, source tint and multipart empty choices',async()=>{
  const env=fixture(),createSource=await runtimeFactory(env);
  let root={x:0,y:4,z:0};
  const dimension=env.dimension;
  const get=location=>({location,dimension,typeId:location.y===root.y?'minecraft:short_grass':'minecraft:air',
    permutation:{getState:()=>undefined,getAllStates:()=>({})},
    offset(delta){return get(Object.fromEntries(['x','y','z'].map(axis=>[axis,location[axis]+delta[axis]])));}});
  dimension.getBlock=get;dimension.getBiome=()=>({id:'minecraft:plains'});
  const faces=['north','east','south','west','up','down'],tint=[77/255,153/255,51/255];
  const rule=(id,tiles,fallback=false)=>({id,method:fallback?'fixed':'random',blocks:[],faces,matchTiles:fallback?undefined:['plant'],
    tiles,fallback,entity:'model:'+id,spawn_structure:'model:'+id,tintPalette:[[1,1,1],tint]});
  const material={texture:'plant',worldFace:'north',tintIndex:0,fallbackRule:'plant_base',
    renderers:{plant:'plant_quad',plant_base:'plant_base_quad'}};
  const part={from:[0,0,8],to:[16,16,8],faces:{north:material}};
  const config={rules:[rule('plant',['alternative','<default>']),rule('plant_base',['plant'],true),
    {...rule('plant_quad',['alternative','<default>'],true),modelPart:true},
    {...rule('plant_base_quad',['plant'],true),modelPart:true}],
    fullCubeBlocks:['minecraft:stone'],modelPartBlocks:['minecraft:short_grass'],
    modelTintTypes:{'minecraft:short_grass':'grass'},grassTints:{'minecraft:plains':tint},
    baseTextureVariants:{'minecraft:short_grass':[{
      states:{},modelParts:[part],modelPartsGroups:[{modelSelection:'java-26.2-multipart-block-position',
        modelChoices:[{weight:1,modelParts:[part]},{weight:4,modelParts:[]}]}]}]}};
  const runtime=createSource(config,{providerId:'plant-source'});env.flush();
  assert.ok(runtime.sources.includes('minecraft:short_grass'));
  assert.equal(config.fullCubeBlocks.includes('minecraft:short_grass'),false);
  const selectedRules=new Set(),partCounts=new Set();
  for(let x=-50;x<50;x++){
    root={x,y:4,z:0};const selection=runtime.resolveFace(get(root),'north');
    assert.equal(selection.known,true);partCounts.add(selection.layers.length);
    for(const layer of selection.layers){selectedRules.add(layer.rule.id);assert.deepEqual(layer.tint,tint);assert.equal(layer.palette,1);assert.equal(layer.positionLayer,0);}
  }
  assert.deepEqual([...selectedRules].sort(),['plant_base_quad','plant_quad']);
  assert.deepEqual([...partCounts].sort(),[1,2],'independent multipart selection retains the empty model choice');
  root={x:0,y:4,z:0};runtime.update(dimension,root);
  const north=env.actors.find(actor=>actor.isValid);assert.ok(north);
  assert.ok(Math.abs(north.location.z+.002)<1e-12,'slot IDs do not move source planes away from their authored position');
  runtime.recover(north);env.flush();assert.ok(north.isValid);
  runtime.dispose();
});

test('derived leaf distance selects original multipart state and defers unknown nearby cells',async()=>{
  const env=fixture(),createSource=await runtimeFactory(env),dimension=env.dimension;
  let logDistance=2,unknown=false;
  const get=location=>({location,dimension,
    typeId:unknown&&location.x===1?undefined:location.y===4&&location.z===0&&location.x>=0&&location.x<logDistance?'minecraft:oak_leaves':
      location.y===4&&location.z===0&&location.x===logDistance?'minecraft:oak_log':'minecraft:air',
    permutation:{getState:name=>name==='persistent_bit'?false:undefined,getAllStates:()=>({persistent_bit:false})},
    offset(delta){const p=Object.fromEntries(['x','y','z'].map(axis=>[axis,location[axis]+delta[axis]]));return unknown&&p.x===1?undefined:get(p);}});
  dimension.getBlock=get;
  const variants=[2,7].map(distance=>({states:{persistent_bit:false,'bct:leaf_distance':distance},modelParts:[]}));
  const runtime=createSource({rules:[],modelPartBlocks:['minecraft:oak_leaves'],fullCubeBlocks:['minecraft:oak_leaves'],
    leafDistanceLeaves:['minecraft:oak_leaves'],leafDistanceLogs:['minecraft:oak_log'],baseTextureVariants:{'minecraft:oak_leaves':variants}});
  assert.equal(runtime.variantOf(get({x:0,y:4,z:0})).states['bct:leaf_distance'],2);
  logDistance=8;runtime.invalidate();
  assert.equal(runtime.variantOf(get({x:0,y:4,z:0})).states['bct:leaf_distance'],7);
  unknown=true;runtime.invalidate();
  assert.equal(runtime.resolveFace(get({x:0,y:4,z:0}),'north').known,false);
  runtime.dispose();
});

test('grouped source faces use one carrier per material with independent face culling and tint',async()=>{
  const env=fixture(),createSource=await runtimeFactory(env),dimension=env.dimension;
  let unknown=false;
  const get=location=>({location,dimension,typeId:location.x===0&&location.y===4&&location.z===0?'minecraft:fern':
      location.z===-1?'minecraft:stone':'minecraft:air',
    permutation:{getState:()=>undefined,getAllStates:()=>({})},
    offset(delta){const p=Object.fromEntries(['x','y','z'].map(axis=>[axis,location[axis]+delta[axis]]));return unknown&&p.z===-1?undefined:get(p);}});
  dimension.getBlock=get;dimension.getBiome=()=>({id:'minecraft:plains'});
  const tint=[77/255,153/255,51/255],faces=['north','east','south','west','up','down'];
  const rule=id=>({id,method:'fixed',blocks:[],faces,tiles:[id],fallback:true,modelPart:true,modelGroup:true,
    entity:'model:'+id,spawn_structure:'model:'+id,tintPalette:[[1,1,1],tint]});
  const config={rules:[rule('green'),rule('brown')],fullCubeBlocks:['minecraft:stone'],opaqueBlocks:['minecraft:stone'],
    modelPartBlocks:['minecraft:fern'],modelTintTypes:{'minecraft:fern':'grass'},grassTints:{'minecraft:plains':tint},
    baseTextureVariants:{'minecraft:fern':[{
      states:{},modelParts:[],modelRenderGroups:[
        {texture:'green',tintIndex:0,worldFace:'north',rule:'green',cullFaces:['north','south']},
        {texture:'brown',tintIndex:-1,worldFace:'north',rule:'brown',cullFaces:[]}]}]}};
  const runtime=createSource(config);env.flush();
  const selected=runtime.resolveFace(get({x:0,y:4,z:0}),'north');
  assert.equal(selected.known,true);assert.equal(selected.layers.length,2,'source faces are bundled by material');
  assert.equal(selected.layers[0].cullMask,1,'opaque north neighbor only hides the north bone');
  assert.deepEqual(selected.layers.map(layer=>[layer.tint,layer.palette]),[[tint,1],[[1,1,1],0]],
    'tinted foliage does not tint an untinted dirt or stem material');
  runtime.update(dimension,{x:0,y:4,z:0});
  assert.equal(runtime.status.actors,2,'face count does not multiply the material carrier count');
  assert.ok(env.actors.filter(actor=>actor.isValid).every(actor=>actor.getProperty('bct:cull_mask')===1));
  unknown=true;assert.equal(runtime.resolveFace(get({x:0,y:4,z:0}),'north').known,false);
  runtime.update(dimension,{x:0,y:4,z:0});assert.equal(runtime.status.actors,2,'unloaded culling neighbors preserve existing carriers');
  runtime.dispose();
});

test('vine ceiling attachment uses downward support rather than visual opacity and defers unknown shapes',async()=>{
  const env=fixture(),createSource=await runtimeFactory(env),dimension=env.dimension;
  let ceiling='minecraft:air',state={},unloaded=false;
  const get=location=>({location,dimension,typeId:location.y===4?'minecraft:vine':location.y===5?ceiling:'minecraft:air',
    permutation:{getState:name=>location.y===4&&name==='vine_direction_bits'?1:state[name],getAllStates:()=>state},
    offset(delta){const p=Object.fromEntries(['x','y','z'].map(axis=>[axis,location[axis]+delta[axis]]));return unloaded&&p.y===5?undefined:get(p);}});
  dimension.getBlock=get;
  const runtime=createSource({rules:[],modelPartBlocks:['minecraft:vine'],fullCubeBlocks:['minecraft:stone','minecraft:honey_block'],
    leafDistanceLeaves:['minecraft:oak_leaves'],baseTextureVariants:{'minecraft:vine':[true,false].map(up=>({
      states:{vine_direction_bits:1,'bct:vine_up':up},modelParts:[]}))}});
  const selected=()=>runtime.variantOf(get({x:0,y:4,z:0}))?.states['bct:vine_up'];
  assert.equal(selected(),false);
  for(const type of ['minecraft:stone','minecraft:oak_leaves','minecraft:farmland','minecraft:grass_path','minecraft:oak_double_slab']) {
    ceiling=type;assert.equal(selected(),true,type);
  }
  ceiling='minecraft:oak_slab';state={'minecraft:vertical_half':'bottom'};assert.equal(selected(),true);
  state={'minecraft:vertical_half':'top'};assert.equal(selected(),false);
  ceiling='minecraft:oak_stairs';state={upside_down_bit:false};assert.equal(selected(),true);
  state={upside_down_bit:true};assert.equal(selected(),false);
  ceiling='minecraft:oak_fence';assert.equal(runtime.resolveFace(get({x:0,y:4,z:0}),'north').known,false);
  ceiling='minecraft:honey_block';assert.equal(runtime.resolveFace(get({x:0,y:4,z:0}),'north').known,false,
    'visual full-cube eligibility does not prove a full collision face');
  ceiling='minecraft:stone';unloaded=true;assert.equal(runtime.resolveFace(get({x:0,y:4,z:0}),'north').known,false);
  runtime.dispose();
});

test('partial model tile connections require a selected source face on the neighbor',async()=>{
  const env=fixture(),createSource=await runtimeFactory(env),dimension=env.dimension;
  let attachment='west',cubeNeighbor=false;
  const get=location=>({location,dimension,typeId:location.x===0&&location.z===0&&[4,5].includes(location.y)?
      location.y===5&&cubeNeighbor?'minecraft:stone':'minecraft:vine':'minecraft:air',
    permutation:{getState:name=>name==='attachment'?(location.y===4?'north':attachment):undefined,getAllStates:()=>({})},
    offset(delta){return get(Object.fromEntries(['x','y','z'].map(axis=>[axis,location[axis]+delta[axis]])));}});
  dimension.getBlock=get;
  const faces=['north','east','south','west','up','down'];
  const authored={id:'vine',method:'vertical',connect:'tile',blocks:[],faces,matchTiles:['vine'],tiles:['bottom','middle','top','single']};
  const renderer={...authored,id:'vine_part',fallback:true,entity:'model:vine',spawn_structure:'model:vine'};
  const material=face=>({texture:'vine',tintIndex:-1,worldFace:face,renderers:{vine:'vine_part'}});
  const variant=attachment=>({states:{attachment},modelParts:[{faces:attachment==='north'?
    {north:material('north'),south:material('south')}:{west:material('west'),east:material('east')}}]});
  const runtime=createSource({rules:[authored,renderer],fullCubeBlocks:['minecraft:stone'],modelPartBlocks:['minecraft:vine'],
    baseTextures:{'minecraft:vine':Object.fromEntries(faces.map(face=>[face,'vine'])),
      'minecraft:stone':Object.fromEntries(faces.map(face=>[face,'vine']))},
    baseTextureVariants:{'minecraft:vine':[variant('north'),variant('west')]}});
  const select=()=>runtime.resolveFace(get({x:0,y:4,z:0}),'north').layers[0];
  assert.equal(select().tile,3,'a vine attached on the other axis has no matching north quad');
  attachment='north';assert.equal(select().tile,0,'the selected north source face joins the host north face');
  assert.equal(runtime.resolveFace(get({x:0,y:4,z:0}),'south').layers[0].tile,0,
    'authored double-sided back faces intentionally participate in their own face direction');
  cubeNeighbor=true;assert.equal(select().tile,0,'native full cube sprite matching retains its original behavior');
  runtime.dispose();
});

test('Java logical block-only rules select authored partial faces on native aliases',async()=>{
  const env=fixture(),createSource=await runtimeFactory(env),dimension=env.dimension;
  const get=location=>({location,dimension,typeId:location.y===4?'minecraft:grass_path':'minecraft:air',
    permutation:{getState:()=>undefined,getAllStates:()=>({})},
    offset(delta){return get(Object.fromEntries(['x','y','z'].map(axis=>[axis,location[axis]+delta[axis]])));}});
  dimension.getBlock=get;
  const authored={id:'path',method:'repeat',blocks:['minecraft:dirt_path'],faces:['up'],tiles:['path0','path1'],width:2,height:1};
  const runtime=createSource({rules:[authored,{...authored,id:'path_part',fallback:true,entity:'model:path',spawn_structure:'model:path'}],
    fullCubeBlocks:['minecraft:stone'],modelPartBlocks:['minecraft:grass_path'],
    nativeBlockJavaIds:{'minecraft:grass_path':'minecraft:dirt_path'},
    baseTextureVariants:{'minecraft:grass_path':[{states:{},javaBlock:'minecraft:dirt_path',modelParts:[{faces:{
      up:{texture:'path_native',worldFace:'up',tintIndex:-1,renderers:{path:'path_part'}}}}]}]}});
  const selected=runtime.resolveFace(get({x:1,y:4,z:0}),'up');
  assert.equal(selected.known,true);assert.equal(selected.layers.length,1);
  assert.equal(selected.layers[0].rule.id,'path_part');assert.equal(selected.layers[0].tile,1);
  runtime.dispose();
});

test('two converted packs load configurations and disposes one without touching the other',async()=>{
  const env=fixture(),createSource=await runtimeFactory(env);
  const faces=['north','east','south','west','up','down'];
  const config=(id,block)=>({rules:[{id,method:'fixed',blocks:[block],faces,tiles:[id],entity:'bct:'+id,spawn_structure:'bct:'+id}]});
  const stone=createSource(config('stone','minecraft:stone'),{providerId:'stone-source'});
  const brick=createSource(config('brick','minecraft:brick_block'),{providerId:'brick-source'});
  env.flush();
  stone.update(env.dimension,{x:0,y:4,z:0});brick.update(env.dimension,{x:4,y:4,z:0});
  assert.equal(stone.status.actors,6);assert.equal(brick.status.actors,6);
  for(const entity of env.actors) {
    assert.equal(entity.getDynamicProperty('bct:source'),entity.typeId==='bct:stone'?'stone-source':'brick-source');
    env.world.afterEvents.entityLoad.emit({entity});
  }
  env.flush();
  assert.equal(stone.status.actors,6);assert.equal(brick.status.actors,6);
  assert.equal(env.actors.filter(actor=>actor.isValid).length,12,'other providers do not interpret an actor as a duplicate');
  stone.dispose();stone.dispose();
  assert.equal(stone.status.disposed,true);assert.equal(stone.status.enabled,false);
  assert.equal(env.actors.filter(actor=>actor.isValid&&actor.typeId==='bct:stone').length,0);
  assert.equal(env.actors.filter(actor=>actor.isValid&&actor.typeId==='bct:brick').length,6);
  stone.update(env.dimension,{x:0,y:4,z:0});env.flush();
  assert.equal(env.actors.filter(actor=>actor.isValid&&actor.typeId==='bct:stone').length,0,'disposed source stays inactive');
  brick.dispose();
  const late=createSource(config('stone','minecraft:stone'),{providerId:'late-source'});
  late.dispose();env.flush();
  assert.equal(env.runs.size,0);assert.equal(env.actors.filter(actor=>actor.isValid).length,0,'pending startup is canceled');
  let owner='lower';
  const lower=createSource(config('lower','minecraft:stone'),{providerId:'lower',ownsBlock:()=>owner==='lower'});
  const higher=createSource(config('higher','minecraft:stone'),{providerId:'higher',ownsBlock:()=>owner==='higher'});
  env.flush();
  lower.update(env.dimension,{x:0,y:4,z:0});higher.update(env.dimension,{x:0,y:4,z:0});
  assert.equal(lower.status.actors,6);assert.equal(higher.status.actors,0);
  assert.deepEqual(higher.resolveFace(env.dimension.getBlock({x:0,y:4,z:0}),'north').layers,[],
    'a source without ownership cannot duplicate the active source face');
  owner='higher';lower.refreshOwnership();higher.refreshOwnership();
  higher.update(env.dimension,{x:0,y:4,z:0});
  assert.equal(lower.status.actors,0);assert.equal(higher.status.actors,6);
  assert.equal(env.actors.filter(actor=>actor.isValid).length,6,'priority change removes the formerly active surface');
  lower.dispose();higher.dispose();
  
});

test('graphics mode gating uses the stable server 2.10 enum and client-only query metadata',async()=>{
  const metadata=JSON.parse(await readFile(samplesUrl('metadata/script_modules/@minecraft/server-bindings_2.10.0.json'),'utf8'));
  const mode=metadata.enums.find(value=>value.name==='GraphicsMode');
  assert.equal(mode.constants.find(value=>value.name==='RayTraced').value,'RayTraced');
  const player=metadata.classes.find(value=>value.name==='Player');
  assert.equal(player.properties.find(value=>value.name==='graphicsMode').type.name,'GraphicsMode');
  const queries=JSON.parse(await readFile(samplesUrl('metadata/molang_modules/mojang-molang-queries.json'),'utf8'));
  const query=queries.queries.find(value=>value.name==='query.graphics_mode_is_any');
  assert.match(query.description,/raytraced/);assert.match(query.description,/Client/);
});

test('model layers keep connected-texture matching, source tint, alpha fallbacks and authored weighted variants',async()=>{
  const faces=['north','east','south','west','up','down'];
  const all=value=>Object.fromEntries(faces.map(face=>[face,value]));
  const fallback=(id,texture)=>({id,fallback:true,method:'fixed',blocks:[],faces,tiles:[texture]});
  const rules=[
    {id:'grass_top',method:'repeat',blocks:[],faces:['up'],matchTiles:['grass_top'],tiles:['top0','top1','top2','top3'],width:2,height:2,
      tintPalette:[[1,1,1],[77/255,153/255,51/255],[102/255,128/255,153/255]]},
    {id:'grass_side',method:'horizontal',connect:'tile',blocks:[],faces:['north'],matchTiles:['grass_side'],tiles:['side0','side1','side2','side3']},
    {id:'edge_overlay',method:'overlay_fixed',blocks:[],faces:['north'],matchTiles:['dirt'],tiles:['edge'],tintIndex:-1},
    {id:'leaf',method:'fixed',blocks:[],faces:['up'],matchTiles:['leaf'],tiles:['leaf_texture']},
    fallback('detail_native','detail'),fallback('stone_native_a','stone_a'),fallback('stone_native_b','stone_b'),fallback('dirt_native','dirt'),
  ];
  const config={rules,sourceBlocks:['minecraft:stone','minecraft:dirt'],modelRenderBlocks:['minecraft:stone'],
    modelTextureRules:{detail:'detail_native',stone_a:'stone_native_a',stone_b:'stone_native_b',dirt:'dirt_native'},
    grassTints:{'minecraft:plains':[.3,.6,.2]},
    foliageTints:{'minecraft:plains':[.2,.5,.1]},
    customTints:{stone_climate:{'minecraft:plains':[.6,.7,.8]}},
    customTintBlocks:['minecraft:iron_ore'],
    modelTintTypes:{'minecraft:grass':'grass','minecraft:oak_leaves':'foliage','minecraft:birch_leaves':[.502,.655,.333],
      'minecraft:iron_ore':'stone_climate'},
    nativeBlockJavaIds:{'minecraft:grass':'minecraft:grass_block'},
    baseTextures:{'minecraft:dirt':all('dirt')},textureOrientations:{'minecraft:dirt':all(0)},
    baseTextureVariants:{
      'minecraft:grass':[{states:{},faces:{...all('dirt'),up:'grass_top'},orientations:all(0),tintIndices:{up:0},
        faceLayers:{north:[{texture:'grass_side',orientation:0,tintIndex:0},{texture:'detail',orientation:2,tintIndex:-1}]}}],
      'minecraft:stone':[{states:{},modelSelection:'java-26.2-block-position',modelChoices:[
        {weight:1,faces:all('stone_a'),orientations:all(3),tintIndices:all(-1)},
        {weight:1,faces:all('stone_b'),orientations:all(2),tintIndices:all(-1)},
      ]}],
      ...Object.fromEntries(['minecraft:oak_leaves','minecraft:birch_leaves','minecraft:basalt','minecraft:iron_ore'].map(block=>[block,
        [{states:{},faces:all('leaf'),orientations:all(0),tintIndices:all(block==='minecraft:iron_ore'?-1:0)}]])),
    }};
  const env=fakeBedrock();
  const runtime=createConnectedSource(config,{api:env.api,carriers:createCarrierBudget(()=>({maxCarriers:100,maxCarriersPerChunk:100}))});
  const dimension={getBiome:()=>({id:'minecraft:plains'})};
  function get(x,y,z,type='minecraft:grass') {
    return {location:{x,y,z},dimension,typeId:y===0&&z===0&&x>=0&&x<=1?type:'minecraft:air',
      permutation:{getState:()=>undefined,getAllStates:()=>({})},offset:v=>get(x+v.x,y+v.y,z+v.z,type)};
  }
  const top=runtime.resolveFace(get(0,0,0),'up');
  assert.ok(runtime.sources.includes('minecraft:grass'),'model layers make their block eligible for scanning without a block-ID rule');
  assert.equal(top.known,true);assert.equal(top.layers.length,1);assert.equal(top.layers[0].rule.id,'grass_top');
  assert.deepEqual(top.layers[0].tint,[.3,.6,.2]);
  assert.equal(top.layers[0].palette,1,'source tint selects a baked color rather than an entity shader multiplier');
  const side=runtime.resolveFace(get(0,0,0),'north');
  assert.deepEqual(side.layers.map(layer=>[layer.rule.id,layer.layer]),[['grass_side',1],['detail_native',2],['edge_overlay',3]]);
  assert.equal(side.layers[0].tile,2,'extra-layer matching connects to the neighboring grass-side layer');
  assert.deepEqual(side.layers[0].tint,[.3,.6,.2]);
  assert.deepEqual(side.layers[1].tint,[1,1,1]);assert.equal(side.layers[1].orientation,2);
  assert.deepEqual(side.layers[2].tint,[1,1,1],'explicit overlay tint does not inherit the source model tint twice');
  const weighted=runtime.resolveFace(get(0,0,0,'minecraft:stone'),'up');
  assert.equal(weighted.layers.length,1);assert.equal(weighted.layers[0].rule.id,'stone_native_b');
  assert.equal(weighted.layers[0].texture,'stone_b');assert.equal(weighted.layers[0].orientation,2);
  const native=runtime.resolveFace(get(0,0,0,'minecraft:dirt'),'up');
  assert.deepEqual(native.layers,[],'an ordinary native face is not duplicated just because its sprite has a fallback rule');
  assert.deepEqual(runtime.resolveFace(get(0,0,0,'minecraft:oak_leaves'),'up').layers[0].tint,[.2,.5,.1]);
  assert.deepEqual(runtime.resolveFace(get(0,0,0,'minecraft:birch_leaves'),'up').layers[0].tint,[.502,.655,.333]);
  assert.deepEqual(runtime.resolveFace(get(0,0,0,'minecraft:basalt'),'up').layers[0].tint,[1,1,1],
    'a block without a color provider stays untinted even when its source model contains tintindex0');
  assert.deepEqual(runtime.resolveFace(get(0,0,0,'minecraft:iron_ore'),'up').layers[0].tint,[.6,.7,.8]);
  const candidate={rule:rules[0],tile:2,orientation:3};
  assert.deepEqual(runtime.paletteSelection(candidate,[.4,.5,.6]),{...candidate,tint:[.4,.5,.6],palette:2});
  assert.equal(runtime.paletteSelection(candidate,[.9,.1,.2]),undefined,'unknown colors do not silently select the white texture');
});
