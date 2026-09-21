const fs=require('fs');
const mkEl=()=>({innerHTML:'',textContent:'',value:'',title:'',style:{},dataset:{},
  classList:{add(){},remove(){},toggle(){},contains(){return false}},
  setAttribute(){},getAttribute(){return ''},removeAttribute(){},appendChild(){},removeChild(){},
  addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],children:[],parentNode:null,
  offsetHeight:100,clientWidth:800,getBoundingClientRect:()=>({width:800,height:300,top:0,left:0}),
  insertAdjacentHTML(){},click(){},focus(){},blur(){},closest:()=>null});
const cache={};
global.window={addEventListener(){},location:{href:'',reload(){}},matchMedia:()=>({matches:false,addEventListener(){}}),innerWidth:411,localStorage:{getItem:()=>null,setItem(){}}};
global.document={getElementById:id=>cache[id]||(cache[id]=mkEl()),querySelector:()=>mkEl(),querySelectorAll:()=>[],
  createElement:()=>mkEl(),addEventListener(){},head:mkEl(),body:mkEl(),documentElement:mkEl()};
global.localStorage=window.localStorage; global.navigator={userAgent:'node'};
global.setTimeout=(f,t)=>0; global.clearTimeout=()=>{}; global.setInterval=()=>0; global.clearInterval=()=>{};
global.requestAnimationFrame=f=>0;
global.echarts={init:()=>({setOption(){},resize(){},dispose(){},on(){},getWidth:()=>800,getHeight:()=>300}),graphic:{LinearGradient:function(){}}};
eval(fs.readFileSync('data.js','utf8'));
const h=fs.readFileSync('index.html','utf8');
eval(h.match(/<script(?![^>]*src)[^>]*>([\s\S]*?)<\/script>/)[1]);

try{ init(); console.log('OK  init() 全流程通过'); }catch(e){ console.log('FAIL init(): '+e.message); process.exit(1); }

const fns=['renderMarketVolume','renderMacro','renderNodes','renderReco','renderCandidates','renderBoards','renderBigboards','renderModule4','renderDtLadder','renderChart'];
for(const f of fns){
  try{ if(typeof eval(f)==='function'){ eval(f+'()'); console.log('  OK   '+f+'()'); } else console.log('  --   '+f+' 已移除'); }
  catch(e){ console.log('  FAIL '+f+'(): '+e.message); }
}

console.log('\n--- 必看页 7 个区块输出 ---');
['mkt-indices','mkt-kpi','heat-grid','fund-rank','style-box','notes-box','style-note','mkt-mainnote']
  .forEach(id=>console.log('  '+id+'  '+(cache[id].innerHTML||'').length+' 字符'));

console.log('\n--- 已删模块的容器应「未被创建」---');
['board-perf','news-list','idx-bar','news-tabs'].forEach(id=>console.log('  #'+id+': '+(cache[id]?'WARN 仍被访问':'ok 未访问')));

console.log('\n--- 页面 HTML 残留检查 ---');
const body=h.match(/<body[\s\S]*?<\/body>/)[0];
['board-perf','news-list','idx-bar','news-tabs','news-tab','id="idx-date"'].forEach(k=>{
  console.log('  '+k+': '+(body.includes(k)?'WARN 仍存在':'ok 已清除'));
});
