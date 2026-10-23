
const SPEC={"dur":4.0,"mark":[617,466,687,153],"scrim":[[0.0,0.0],[0.2,0.019],[0.2333,0.071],[0.2667,0.18],[0.3,0.236],[0.3333,0.403],[0.3667,0.612],[0.4,0.604],[4.0,0.604]],"markOp":[[0.0,0],[0.2333,0],[0.2666,0.03],[0.2999,0.27],[0.3332,0.91],[0.3665,1.0],[4.0,1.0]],"sx":[[0.22,1.15],[0.2333,1.115],[0.2667,0.973],[0.3,0.967],[0.3333,0.97],[0.3667,0.996],[0.4,1.0],[4.0,1.0]],"sy":[[0.22,1.15],[0.2333,1.115],[0.2667,0.973],[0.3,0.967],[0.3333,0.97],[0.3667,0.996],[0.4,1.0],[4.0,1.0]],"tx":[[0.0,0]],"ty":[[0.0,0],[0.6664,0],[0.6998,-56],[0.7331,-116],[0.7664,-219],[0.7997,-232],[4.0,-232]],"strip":[0,551,1920,94],"stripT":[0,545,1920,6],"stripB":[0,645,1920,10],"stripOp":[[0,0],[0.9663,0],[0.9996,0],[1.033,1.0],[4.0,1.0]],"bordOp":[[0,0],[0.9996,0],[1.033,1.0],[4.0,1.0]],"btn":[1757,950,111,92],"btnY":[[1.3329,160],[1.3662,53],[1.3995,19],[1.4328,16],[1.4661,11],[1.4995,8],[1.5328,5],[1.5661,3],[1.5994,2],[1.6328,1],[1.6661,0],[4.0,0]],"btnOp":[[0,0],[1.3329,0],[1.3662,0.7],[1.3995,1.0],[4.0,1.0]],"tchip":[786,434,348,60],"clock":[806,441,39,43],"trowX":[[0.7997,-919],[0.833,-919],[0.8664,-293],[0.8997,-166],[0.933,-166],[0.9663,-71],[0.9996,-37],[1.033,-19],[1.0663,-7],[1.0996,-1],[1.1329,0],[4.0,0]],"trowOp":[[0,0],[0.7664,0],[0.7997,1.0],[4.0,1.0]],"barC":[880,587,391,22],"barG":[1280,587,66,22],"hdiv":[748,581,20,35],"grpcX":[[0.8997,-584],[0.933,-584],[0.9663,-258],[0.9996,-148],[1.033,-88],[1.0663,-44],[1.0996,-16],[1.1329,-2],[1.1663,0],[4.0,0]],"grpcOp":[[0,0],[0.8664,0],[0.8997,0.3],[0.933,0.3],[0.9663,1.0],[4.0,1.0]],"confirm":[781,931,360,115],"confY":[[1.1663,160],[1.1996,124],[1.2329,56],[1.2995,23],[1.3662,13],[1.3995,7],[1.4328,4],[1.4661,1],[1.4995,0],[4.0,0]],"confOp":[[0,0],[1.1663,0],[1.1996,1.0],[4.0,1.0]],"bcard":[30,27,172,143],"bchip":[193,27,691,78],"banX":[[0.3999,-236],[0.4332,-195],[0.4665,-136],[0.4998,-94],[0.5331,-53],[0.5665,-26],[0.5998,-11],[0.6331,-3],[0.6664,0],[3.632,0],[3.6654,-214],[3.6987,-469],[3.732,-673],[3.7653,-775],[3.7986,-1250],[4.0,-1250]],"banOp":[[0,0],[0.3665,0],[0.3999,0.29],[0.4332,0.33],[0.4665,0.59],[0.4998,0.9],[0.5331,1.0],[4.0,1.0]],"cardOp":[[0,0],[0.4332,0],[0.4665,0.33],[0.4998,0.61],[0.5331,0.89],[0.5665,1.0],[4.0,1.0]],"nowload":[1389,941,442,74],"nlOp":[[0,0],[0.2333,0],[0.2666,1.0],[0.4665,1.0],[0.4998,0],[4.0,0]]};
const W=1920, H=1080;
const stage=document.getElementById('stage');
const sc=document.getElementById('scrim'), mark=document.getElementById('mark'),
      strip=document.getElementById('strip'), stripT=document.getElementById('stripTL'),
      stripB=document.getElementById('stripBL'), trow=document.getElementById('trow'),
      grpc=document.getElementById('grpc'), conf=document.getElementById('confirm'),
      nl=document.getElementById('nowload'),
      btn=document.getElementById('btn');

markImgS();
function markImgS(){
  const mi=document.getElementById('markImg');
  mi.style.left=SPEC.mark[0]+'px'; mi.style.top=SPEC.mark[1]+'px';
  mi.style.width=SPEC.mark[2]+'px'; mi.style.height=SPEC.mark[3]+'px';
  mark.style.transformOrigin='958px 542px';
}
setEL(strip, SPEC.strip, 0, 0);
setEL(stripT, SPEC.stripT, 0, 0); setEL(stripB, SPEC.stripB, 0, 0);
setEL(tchip, SPEC.tchip, 0, 0); setEL(document.getElementById('clock'), SPEC.clock, 0, 0);
setEL(document.getElementById('barC'), SPEC.barC, 0, 0);
setEL(document.getElementById('barG'), SPEC.barG, 0, 0);
setEL(document.getElementById('hdiv'), SPEC.hdiv, 0, 0);
conf.style.width=SPEC.confirm[2]+'px'; conf.style.height=SPEC.confirm[3]+'px';
setImg(conf, SPEC.confirm);
setImg(btn, SPEC.btn);
setImg(nl, SPEC.nowload);
function setImg(el, b){ el.style.left=b[0]+'px'; el.style.top=b[1]+'px';
  el.style.width=b[2]+'px'; el.style.height=b[3]+'px';
  const img=el.querySelector('img'); if(img){img.style.width=b[2]+'px'; img.style.height=b[3]+'px';} }
function setEL(el, b, dx, dy){ el.style.left=b[0]+'px'; el.style.top=b[1]+'px';
  el.style.width=b[2]+'px'; el.style.height=b[3]+'px';
  if(el.tagName==='IMG'&&el.id){ el.style.width=b[2]+'px'; el.style.height=b[3]+'px'; }
  if(el.querySelector && el.querySelector('img')){
    const im=el.querySelector('img'); im.style.width=b[2]+'px'; im.style.height=b[3]+'px';
  } }

const Q=new URLSearchParams(location.search);
function fit(){
  const s=Math.min(innerWidth/W, innerHeight/H);
  stage.style.transform='translate(-50%,-50%) scale('+s+')';
}
addEventListener('resize',fit); fit();

const lerp=(a,b,u)=>a+(b-a)*u;
function samp(k,t){
  const n=k.length;
  if(t<=k[0][0]) return k[0][1];
  if(t>=k[n-1][0]) return k[n-1][1];
  for(let i=1;i<n;i++){
    if(t<=k[i][0]){ const u=(k[i][0]>k[i-1][0])?(t-k[i-1][0])/(k[i][0]-k[i-1][0]):1;
      return lerp(k[i-1][1],k[i][1],u); }
  }
  return k[n-1][1];
}
const hides=(Q.get('panel')==='0');
if(hides){ [strip,stripT,stripB,trow,grpc,conf,btn].forEach(e=>e.style.display='none'); }
if(Q.get('nl')==='0') nl.style.display='none';

// ---- editable text nodes: always live text (edit the default strings in this file) ----
function seededFromParam(k){
  const map={time:'timeTxt',name:'nameTxt',hp:'hpTxt',rtitle:'repTitleTxt'};
  return map[k];
}
 ['time','name','rtitle'].forEach(function(k){
  if(Q.has(k)){ const id=seededFromParam(k); document.getElementById(id).textContent=Q.get(k); }
});
if(Q.has('hp')&&Q.has('hpmax')){ document.getElementById('hpTxt').textContent=Q.get('hp')+'/'+Q.get('hpmax'); }
function applyTexts(){
  // hp bar width from digits
  const hpx=document.getElementById('hpTxt');
  const m=/^(\d+)\s*\/\s*(\d+)$/.exec(hpx.textContent);
  const barC=document.getElementById('barC'), barG=document.getElementById('barG');
  if(m && m[2]>0){
    let r=parseInt(m[1],10)/parseInt(m[2],10); r=Math.max(0,Math.min(1,r));
    const w=Math.round(r*450);
    barC.style.width=w+'px';
    barG.style.left=(880+w+9)+'px';
    barG.style.width=Math.max(0,466-w-9)+'px';
  }
  const hf=Q.get('hpfill');
  if(hf!==null && !isNaN(parseFloat(hf))){
    const p=Math.max(0,Math.min(100,parseFloat(hf)))/100;
    const w=Math.round(p*450);
    barC.style.width=w+'px';
    barG.style.left=(880+w+9)+'px';
    barG.style.width=Math.max(0,466-w-9)+'px';
  }
}
applyTexts();

function render(t){
  sc.style.opacity=samp(SPEC.scrim,t);
  mark.style.opacity=samp(SPEC.markOp,t);
  const sx=samp(SPEC.sx,t), sy=samp(SPEC.sy,t);
  mark.style.transform='translate('+samp(SPEC.tx,t)+'px,'+samp(SPEC.ty,t)+'px) scale('+sx+','+sy+')';
  if(!hides){
    strip.style.opacity=samp(SPEC.stripOp,t);
    stripT.style.opacity=samp(SPEC.bordOp,t);
    stripB.style.opacity=samp(SPEC.bordOp,t);
    const tx=samp(SPEC.trowX,t);
    trow.style.opacity=samp(SPEC.trowOp,t);
    trow.style.transform='translate('+tx+'px,0)';
    const gx=samp(SPEC.grpcX,t);
    grpc.style.opacity=samp(SPEC.grpcOp,t);
    grpc.style.transform='translate('+gx+'px,0)';
    const cy=samp(SPEC.confY,t);
    conf.style.opacity=samp(SPEC.confOp,t);
    conf.style.transform='translate(0,'+cy+'px)';
    nl.style.opacity=samp(SPEC.nlOp,t);
    btn.style.opacity=samp(SPEC.btnOp,t);
    btn.style.transform='translate(0,'+samp(SPEC.btnY,t)+'px)';
    btn.classList.toggle('off', samp(SPEC.btnOp,t)<0.5);
  }
}
const DUR=SPEC.dur;
let playing=true, t0=performance.now(), offset=0;
if(Q.has('t')){ playing=false; offset=parseFloat(Q.get('t'))||0; }
const loopMode=Q.get('loop')==='1';
function frame(now){
  if(playing){
    let t=(now-t0)/1000+offset;
    if(loopMode) t=t%DUR; else if(t>DUR) t=DUR;
    render(t);
  }
  requestAnimationFrame(frame);
}
render(offset); requestAnimationFrame(frame);
window.setTime=function(t){ playing=false; render(t); };
window.play=function(){ playing=true; t0=performance.now(); offset=0; };
window.ready=true;

// ---- Game Analysis modal ----
const rep=document.getElementById('rep'), repBack=document.getElementById('repBack');
function openRep(){ rep.classList.add('show'); repBack.style.display='block'; }
function closeRep(){ rep.classList.remove('show'); repBack.style.display='none'; }
window.openReport=openRep; window.closeReport=closeRep;
document.getElementById('repClose').addEventListener('click',function(e){e.stopPropagation();closeRep();});
repBack.addEventListener('click',closeRep);
btn.addEventListener('click',function(e){ e.stopPropagation(); openRep(); });
if(Q.get('report')==='1') openRep();
// ---- Game analysis text ----
const repBody = document.getElementById('repBody');
function setAnalysis(text){
  const body = String(text || '').trim();
  repBody.textContent = body || 'No major mistakes found.';
}
window.setAnalysis = setAnalysis;
if(Q.has('analysis')) setAnalysis(Q.get('analysis'));

