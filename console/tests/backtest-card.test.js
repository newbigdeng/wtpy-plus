import test from 'node:test';
import assert from 'node:assert/strict';
import {cardCaption,cardPercent} from '../src/js/backtest-card.js';

test('missing results stay unknown while an actual completed zero is displayed',()=>{
  assert.equal(cardPercent(null,'total_return'),'—');
  assert.equal(cardCaption(null),'暂无已完成结果');
  assert.equal(cardPercent({metrics:{total_return:0}},'total_return'),'0.00%');
  assert.equal(cardPercent({metrics:{total_return:-2.32}},'total_return'),'-2.32%');
});

test('historical and unknown code snapshots are not presented as current code results',()=>{
  assert.match(cardCaption({mode:'plus',matches_saved_code:false}),/历史代码/);
  assert.match(cardCaption({mode:'fast',matches_saved_code:null}),/代码版本未知/);
  assert.equal(cardCaption({mode:'plus',matches_saved_code:true}),'Plus');
});
