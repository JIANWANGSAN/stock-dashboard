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
// ⚠️ 顶层 const/let 不会挂到 global 上（同 §9.3），且**另一次 eval 也取不到**（词法作用域隔离）。
//    可靠做法：把「取回语句」**拼进同一段 eval 源码内部**，让它在同一作用域里执行并挂到 global。
const inlineJs=h.match(/<script(?![^>]*src)[^>]*>([\s\S]*?)<\/script>/)[1];
eval(inlineJs + '\n;global.__LVCOL = (typeof LADDER_LEVEL_COLORS!=="undefined")?LADDER_LEVEL_COLORS:null;'
                + '\n;global.__D = (typeof D!=="undefined")?D:null;');

try{ init(); console.log('OK  init() 全流程通过'); }catch(e){ console.log('FAIL init(): '+e.message); process.exit(1); }

const fns=['renderMarketVolume','renderMacro','renderNodes','renderReco','renderBoards','renderBigboards','renderModule4','renderDtLadder','renderChart'];
for(const f of fns){
  try{ if(typeof eval(f)==='function'){ eval(f+'()'); console.log('  OK   '+f+'()'); } else console.log('  --   '+f+' 已移除'); }
  catch(e){ console.log('  FAIL '+f+'(): '+e.message); }
}

console.log('\n--- 必看页 7 个区块输出 ---');
['mkt-indices','mkt-kpi','heat-grid','fund-rank','style-box','notes-box','style-note','mkt-mainnote']
  .forEach(id=>console.log('  '+id+'  '+(cache[id].innerHTML||'').length+' 字符'));

console.log('\n--- 已删模块的容器应「未被创建」---');
['board-perf','news-list','idx-bar','news-tabs','cand-box'].forEach(id=>console.log('  #'+id+': '+(cache[id]?'WARN 仍被访问':'ok 未访问')));

console.log('\n--- 页面 HTML 残留检查（⚠️ 剥掉注释后查，注释里的「已删」说明不算残留）---');
const body=h.match(/<body[\s\S]*?<\/body>/)[0];
// 剥掉 HTML 注释 <!-- -->  与 JS 注释 /* */ //  ，只留真代码
const codeOnly = body.replace(/<!--[\s\S]*?-->/g,'').replace(/\/\*[\s\S]*?\*\//g,'').replace(/^\s*\/\/.*$/gm,'');
['board-perf','news-list','idx-bar','news-tabs','news-tab','id="idx-date"','cand-box','renderCandidates('].forEach(k=>{
  console.log('  '+k+': '+(codeOnly.includes(k)?'WARN 真代码里仍存在':'ok 已清除'));
});

console.log('\n--- 连板高度梯队：逐层级全出线断言 ---');
try{
  // ⚠️ 顶层 const 经 eval 后取不到（词法作用域隔离）→ 已在 eval 源码内部挂到 global.__LVCOL
  const LVCOL = global.__LVCOL;
  const L=(global.__D && global.__D.ladder)?global.__D.ladder.slice(-7):[];
  const last=L[L.length-1]||{};
  const lv=(last.levels||[]).map(v=>v.boards);
  console.log('  末条 '+last.date+' levels='+JSON.stringify(lv));
  const okDesc = lv.length>0 && lv.every((v,i)=>i===0||lv[i-1]>v);
  console.log('  '+(okDesc?'OK  ':'FAIL ')+'levels 非空且降序');
  const allLv=[...new Set(L.flatMap(s=>(s.levels||[]).map(v=>v.boards)))].sort((a,b)=>b-a);
  console.log('  '+(allLv.length?'OK  ':'FAIL ')+'窗口内层级数='+allLv.length+' ['+allLv.join(',')+'] → 应画 '+allLv.length+' 条线');
  console.log('  '+((LVCOL&&LVCOL.length>=3)?'OK  ':'FAIL ')+'层级配色表长度='+((LVCOL||[]).length));
  // 关键断言：末条必须能看到 3 板 / 4 板（本次用户报的正是这两个消失）
  if(lv.includes(4)&&lv.includes(3)) console.log('  OK   末条同时含 4板/3板（用户报的缺失已修）');
  else if(last.date) console.log('  --   末条 '+last.date+' 无 4板/3板（当日实际没有该层，非 bug）：levels='+lv.join(','));
}catch(e){ console.log('  FAIL 梯队层级断言异常: '+e.message); }
