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

console.log('\n--- 连板高度梯队：主板 · 只跟踪最高板一条线（2026-09-23 用户最终要求）---');
try{
  const L=(global.__D && global.__D.ladder)?global.__D.ladder.slice(-7):[];
  const last=L[L.length-1]||{};
  console.log('  末条 '+last.date+' top='+last.top+' top_n='+last.top_n
    +' 名单='+JSON.stringify((last.top_list||[]).map(x=>x.name)));
  // 🔴 单条线：每天必须有 top（板数）与 top_n（家数）
  const badDay=L.filter(s=>!(s.top>=1) || !(s.top_n>=1));
  console.log('  '+(badDay.length===0?'OK  ':'FAIL ')+'每天都有 top/top_n（异常 '+badDay.length
    +' 天'+(badDay.length?'：'+badDay.map(s=>s.date).join(','):'')+'）');
  // 🔴 top_n 必须 == top_list 长度（点上的数字 = 名单只数）
  const mism=L.filter(s=>(s.top_n||0)!==((s.top_list||[]).length));
  console.log('  '+(mism.length===0?'OK  ':'FAIL ')+'top_n == top_list.length（不符 '+mism.length
    +' 天'+(mism.length?'：'+mism.map(s=>s.date+'('+s.top_n+'/'+(s.top_list||[]).length+')').join(','):'')+'）');
  // 🔴 只统计主板：top_list 里不许出现 300/301/688/689/4xx/8xx/92xx
  const bad=[];
  L.forEach(s=>(s.top_list||[]).forEach(x=>{
    const c=String(x.code||'');
    if(c.startsWith('300')||c.startsWith('301')||c.startsWith('688')||c.startsWith('689')
       ||c[0]==='4'||c[0]==='8'||c.startsWith('92')) bad.push(c);
  }));
  console.log('  '+(bad.length===0?'OK  ':'FAIL ')+'只统计主板（越界代码 '+bad.length+' 个'
    +(bad.length?'：'+bad.slice(0,6).join(','):'')+'）');
  // 🔴 top 必须 == 当日涨停池真实最高连板（用 levels 交叉验证：levels 最高层 == top）
  const badTop=L.filter(s=>{
    const mx=Math.max(...((s.levels||[]).map(v=>v.boards)), 0);
    return mx>0 && mx!==s.top;
  });
  console.log('  '+(badTop.length===0?'OK  ':'FAIL ')+'top == levels 最高层（不符 '+badTop.length
    +' 天'+(badTop.length?'：'+badTop.map(s=>s.date+' top'+s.top+'/max'+Math.max(...((s.levels||[]).map(v=>v.boards)),0)).join(','):'')+'）');
}catch(e){ console.log('  FAIL 最高板断言异常: '+e.message); }
