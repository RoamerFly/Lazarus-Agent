const {test}=require('node:test');
const assert=require('node:assert/strict');
const {point}=require('../web/preview.js');
test('portrait letterboxing maps visible center to original phone pixels',()=>{
  const rect={left:10,top:20,width:600,height:600};
  assert.deepEqual(point(rect,576,1280,310,320,1080,2400),{x:540,y:1200});
  assert.equal(point(rect,576,1280,20,320,1080,2400),null);
});
test('landscape screen and edges are mapped without hard coded dimensions',()=>{
  const rect={left:0,top:0,width:600,height:800};
  assert.deepEqual(point(rect,1280,576,300,400,2400,1080),{x:1200,y:540});
  assert.equal(point(rect,1280,576,300,50,2400,1080),null);
});
