import test from 'node:test';
import assert from 'node:assert/strict';
import { createViews, NO_VIEWS, showsAt } from '../engine/views.mjs';

const OVERWORLD = 'minecraft:overworld';

/** One player at x 0, y 64, z 0 looking along +z, and views read one tick at a time. */
function setup(settings = {}) {
  const limits = { viewAngle: 140, farDistance: 48, nearDistance: 16, joinTicks: 0, ...settings };
  const system = { currentTick: 0 };
  const player = { id: 'p', dimension: { id: OVERWORLD }, location: { x: 0, y: 64, z: 0 }, view: { x: 0, y: 0, z: 1 },
    getHeadLocation() { return { x: this.location.x, y: this.location.y + 1.62, z: this.location.z }; },
    getViewDirection() { return this.view; } };
  const views = createViews({ system, limits: () => limits });
  const next = (players = [player]) => { system.currentTick++; views.update(players); };
  next();
  return { system, player, views, next };
}
const turn = degrees => ({ x: Math.sin(degrees * Math.PI / 180), y: 0, z: Math.cos(degrees * Math.PI / 180) });

test('a block is seen inside the view cone and within farDistance, any part of it counting', () => {
  const { views } = setup();
  const sees = (x, y, z) => views.sees(OVERWORLD, { x, y, z });
  assert.equal(sees(0, 65, 10), true, 'straight ahead');
  assert.equal(sees(0, 65, -10), false, 'behind');
  assert.equal(sees(0, 65, 47), true);
  assert.equal(sees(0, 65, 60), false, 'beyond farDistance');
  assert.equal(sees(26, 65, 10), true, '69 degrees off the view direction: inside the 140 degree cone');
  assert.equal(sees(10, 65, 0), false, '90 degrees off: outside');
  assert.equal(sees(1, 65, 0), true, 'right beside the head the block itself reaches into the cone');
  assert.equal(sees(0, 65, 0), true, 'the head is inside it');
  assert.equal(views.sees('minecraft:nether', { x: 0, y: 65, z: 10 }), false, 'another dimension');
});

test('nearDistance counts for joinTicks after a player arrives: joining, a teleport, another dimension', () => {
  const { player, views, next } = setup({ joinTicks: 5 });
  const ahead = { x: 0, y: 65, z: 20 };
  assert.equal(views.sees(OVERWORLD, ahead), false, 'right after joining, 20 blocks is beyond nearDistance');
  for (let tick = 0; tick < 5; tick++) next();
  assert.equal(views.sees(OVERWORLD, ahead), true, 'after the window farDistance counts');
  player.location = { x: 10, y: 64, z: 0 };
  next();
  assert.equal(views.sees(OVERWORLD, { x: 10, y: 65, z: 20 }), true, 'walking is no arrival');
  player.location = { x: 200, y: 64, z: 0 };
  next();
  assert.equal(views.sees(OVERWORLD, { x: 200, y: 65, z: 20 }), false, 'a teleport starts the window again');
  for (let tick = 0; tick < 5; tick++) next();
  player.dimension = { id: 'minecraft:nether' };
  next();
  assert.equal(views.sees('minecraft:nether', { x: 200, y: 65, z: 20 }), false, 'so does another dimension');
});

test('the stamp changes when a head moves or a view turns enough, and when players come or go', () => {
  const { player, views, next } = setup();
  const first = views.stamp;
  next();
  assert.equal(views.stamp, first, 'nothing moved');
  player.view = turn(3);
  next();
  assert.equal(views.stamp, first, 'a small turn');
  player.view = turn(10);
  next();
  assert.equal(views.stamp, first + 1, 'a turn of 10 degrees');
  player.location = { x: 0.3, y: 64, z: 0 };
  next();
  assert.equal(views.stamp, first + 1, 'a small step');
  player.location = { x: 1, y: 64, z: 0 };
  next();
  assert.equal(views.stamp, first + 2);
  next([]);
  assert.equal(views.stamp, first + 3, 'the player left');
});

test('views are read once per tick, and a player whose view the game cannot give may be looking anywhere', () => {
  const { system, player, views } = setup();
  let reads = 0;
  player.getViewDirection = () => { reads++; throw new Error('not spawned'); };
  const players = [player];
  system.currentTick++;
  views.update(players);
  views.update(players);
  assert.equal(reads, 1, 'asked twice in one tick for the same players (replacements, then leaves): read once');
  assert.equal(views.sees(OVERWORLD, { x: 0, y: 65, z: -10 }), true, 'behind counts as well');
  assert.equal(views.sees(OVERWORLD, { x: 0, y: 65, z: -60 }), false, 'but not beyond farDistance');
});

test('without views nothing is seen; a block shows unless every neighbor is an opaque full cube', () => {
  assert.equal(NO_VIEWS.sees(OVERWORLD, { x: 0, y: 0, z: 0 }), false);
  const stone = new Set(['0,1,0', '0,-1,0', '1,0,0', '-1,0,0', '0,0,1']);
  const dimension = { getBlock: ({ x, y, z }) => ({ typeId: stone.has(`${x},${y},${z}`) ? 'minecraft:stone' : 'minecraft:air' }) };
  const solid = type => type === 'minecraft:stone';
  assert.equal(showsAt(dimension, { x: 0, y: 0, z: 0 }, solid), true, 'one side is open');
  stone.add('0,0,-1');
  assert.equal(showsAt(dimension, { x: 0, y: 0, z: 0 }, solid), false, 'walled in');
});

test('chunks rank by distance, with chunks a player looks at within farDistance after the band beyond them', () => {
  const { views } = setup();
  const rank = (cx, cz) => views.rank(OVERWORLD, cx, cz, true);
  // The player stands at z 0 looking along +z: chunk z 1 is ahead in view, chunk z -1 touches the head.
  assert.ok(rank(0, 4) < rank(0, 1), 'the band just beyond farDistance converts before what is already in view');
  assert.ok(rank(0, -2) < rank(0, -3), 'behind the player: nearest first');
  assert.ok(rank(0, -3) < rank(0, 2), 'a chunk behind converts before one in view at the same distance');
  assert.ok(views.rank(OVERWORLD, 0, 1, false) < views.rank(OVERWORLD, 0, 2, false), 'without waiting, distance only');
});

test('a travelling player ranks chunks ahead before chunks beside and behind', () => {
  const { player, views, next } = setup();
  for (let tick = 0; tick < 10; tick++) { player.location = { x: 0, y: 64, z: player.location.z + 1 }; next(); }
  const rank = (cx, cz) => views.rank(OVERWORLD, cx, cz, false);
  assert.ok(rank(0, 4) < rank(4, 0) && rank(4, 0) < rank(0, -4), 'ahead, then beside, then behind at the same distance');
});
