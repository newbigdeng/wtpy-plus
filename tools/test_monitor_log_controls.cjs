/* Focused behavior checks: pausing/resuming MQ, file polling, group preferences. */
const assert = require('node:assert/strict');
const path = require('node:path');
const store = new Map();
const timers = new Map();
let nextTimer = 0;
global.localStorage = {getItem: k => store.has(k) ? store.get(k) : null,
    setItem: (k,v) => store.set(k,v)};
global.setInterval = (fn, ms) => { const id=++nextTimer; timers.set(id,{fn,ms}); return id; };
global.clearInterval = id => timers.delete(id);
const controls = require(path.resolve(__dirname,
    '../wtpy/monitor/static/console/monitor-log-controls.js'));
const view = {groupinfo:{id:'live'},logInterval:0,reads:0,queryLogs(){this.reads++;}};
controls.selectGroup(view,{id:'live',datmod:'auto'});
assert.equal(controls.shouldAppend(view),true);
controls.reset(view); assert.equal(timers.size,0); // no hidden HTTP polling in MQ mode
view.autoLog=false;controls.save(view);controls.reset(view);
assert.equal(controls.shouldAppend(view),false);
view.groupinfo={id:'archive'};controls.selectGroup(view,{id:'archive',datmod:'mannual'});
assert.equal(view.autoLog,false);
view.autoLog=true;controls.save(view);controls.reset(view);controls.reset(view);
assert.equal(timers.size,1);assert.equal([...timers.values()][0].ms,15000);
[...timers.values()][0].fn();assert.equal(view.reads,1);
view.groupinfo={id:'live'};controls.selectGroup(view,{id:'live',datmod:'auto'});
assert.equal(timers.size,0);assert.equal(view.autoLog,false); // remembered independently
view.autoLog=true;controls.save(view);controls.reset(view);
assert.equal(controls.shouldAppend(view),true);
global.localStorage={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
controls.selectGroup(view,{id:'other',datmod:'auto'});assert.equal(view.autoLog,true);
controls.save(view);controls.stop(view);assert.equal(timers.size,0);
const oldRead=controls.beginRead(view);
view.groupinfo={id:'switch'};controls.selectGroup(view,{id:'switch',datmod:'mannual'});
assert.equal(controls.acceptRead(view,oldRead),false);
const older=controls.beginRead(view),latest=controls.beginRead(view);
assert.equal(controls.acceptRead(view,older),false);
assert.equal(controls.acceptRead(view,latest),true);
view.logScroll=false;view.autoLog=true;controls.reset(view);assert.equal(timers.size,1);
controls.dispose(view);assert.equal(timers.size,0);
assert.equal(controls.acceptRead(view,latest),false);
controls.reset(view); // A late queryLogs(true) response must not recreate its timer.
assert.equal(timers.size,0);assert.equal(controls.shouldAppend(view),false);
controls.selectGroup(view,{id:'late',datmod:'auto'});
assert.equal(view.logControlsDisposed,true);
console.log('PASS: MQ pause/resume, one file timer, per-group persistence, storage fallback, cross-group reads and late-response disposal.');
