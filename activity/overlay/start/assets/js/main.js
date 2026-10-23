
const SPEC = {"title":"START!","box":[671,435,775,206],"ref":[673,444],"mode":"translate","x":[[0.5,14],[0.5333,14],[0.5667,14],[0.6,190],[0.6333,190],[0.6667,340],[0.7,340],[0.7333,366],[0.7667,366],[0.8,402],[0.8333,402],[0.8667,439],[0.9,439],[0.9333,452],[0.9667,468],[1.0,480],[1.0333,488],[1.0667,499],[1.1,514],[1.1333,514],[1.1667,530],[1.2,545],[1.2333,554],[1.2667,566],[1.3,580],[1.3333,586],[1.3667,602],[1.4,618],[1.4333,628],[1.4667,648],[1.5,673],[1.5333,673],[1.5667,707],[1.6,730],[1.6333,730],[1.6667,786],[1.7,786],[1.7333,819],[1.7667,1500],[1.8,2100]],"y":[[0.0,444],[3.0,444]],"opacity":[[0.0,0],[0.49,0],[0.56,0.55],[0.61,1.0],[1.78,1.0],[1.8,0.0],[3.0,0.0]],"scrim":[[0.0,0.0],[0.6,0.0],[0.6667,0.159],[0.7333,0.292],[0.8,0.447],[0.8667,0.46],[1.0,0.466],[1.2,0.468],[1.4,0.473],[1.6333,0.481],[1.7,0.15],[1.7667,0.068],[1.85,0.0],[3.0,0.0]],"duration":2.6};
const W = 1920, H = 1080;
const stage=document.getElementById('stage'), mark=document.getElementById('mark'),
      markImg=document.getElementById('markImg'), scrim=document.getElementById('scrim'),
      flash=document.getElementById('flash');

markImg.style.left=SPEC.box[0]+'px'; markImg.style.top=SPEC.box[1]+'px';
markImg.style.width=SPEC.box[2]+'px';  markImg.style.height=SPEC.box[3]+'px';
if(SPEC.mode==='scale'){ mark.style.transformOrigin=SPEC.anchor[0]+'px '+SPEC.anchor[1]+'px'; }

const Q=new URLSearchParams(location.search);
function fit(){
  const s=Math.min(innerWidth/W, innerHeight/H);
  stage.style.transform='translate(-50%,-50%) scale('+s+')';
}
addEventListener('resize',fit); fit();

const lerp=(a,b,u)=>a+(b-a)*u;
function samp(keys,t){
  const n=keys.length;
  if(t<=keys[0][0]) return keys[0][1];
  if(t>=keys[n-1][0]) return keys[n-1][1];
  for(let i=1;i<n;i++){
    if(t<=keys[i][0]){
      const t0=keys[i-1][0],v0=keys[i-1][1],t1=keys[i][0],v1=keys[i][1];
      return lerp(v0,v1,(t1>t0)?(t-t0)/(t1-t0):1);
    }
  }
  return keys[n-1][1];
}
function render(t){
  scrim.style.opacity=samp(SPEC.scrim,t);
  mark.style.opacity=samp(SPEC.opacity,t);
  if(SPEC.mode==='translate'){
    const x=samp(SPEC.x,t), y=samp(SPEC.y,t);
    mark.style.transform='translate('+(x-SPEC.ref[0])+'px,'+(y-SPEC.ref[1])+'px)';
  }else{
    const sx=samp(SPEC.sx,t), sy=samp(SPEC.sy,t),
          tx=samp(SPEC.tx,t), ty=samp(SPEC.ty,t);
    mark.style.transform='translate('+tx+'px,'+ty+'px) scale('+sx+','+sy+')';
  }
  if(SPEC.flash) flash.style.opacity=samp(SPEC.flash,t);
}

const DUR=SPEC.duration;
let playing=true, t0=performance.now(), offset=0;
if(Q.has('t')){ playing=false; offset=parseFloat(Q.get('t'))||0; }
if(Q.get('loop')==='0'){ /* play once */ }
const loopMode = Q.get('loop')==='1';

function frame(now){
  if(playing){
    let t=(now-t0)/1000+offset;
    if(loopMode) t=t%DUR;
    else if(t>DUR) t=DUR;
    render(t);
  }
  requestAnimationFrame(frame);
}
render(offset);
requestAnimationFrame(frame);

window.setTime=function(t){ playing=false; render(t); };
window.play=function(){ playing=true; t0=performance.now(); offset=0; };
window.ready=true;
