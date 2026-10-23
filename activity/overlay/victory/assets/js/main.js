
const SPEC = {"title":"VICTORY!","box":[503,407,947,222],"ref":[980,541],"mode":"scale","sx":[[0.28,1.254],[0.3333,0.981],[0.3667,0.864],[0.4,0.927],[0.4333,0.985],[0.45,1.0],[1.58,1.0],[1.65,1.0]],"sy":[[0.28,1.254],[0.3333,0.981],[0.3667,0.864],[0.4,0.927],[0.4333,0.985],[0.45,1.0],[1.58,1.0],[1.65,1.0]],"tx":[[0.0,0],[3.0,0]],"ty":[[0.0,0],[3.0,0]],"anchor":[980,541],"opacity":[[0.0,0],[0.275,0],[0.305,1.0],[1.5,1.0],[1.5667,0.85],[1.6,0.55],[1.6333,0.25],[1.6667,0.0],[1.7,0.0]],"scrim":[[0.0,0.0],[0.1333,0.013],[0.1667,0.028],[0.2,0.049],[0.2333,0.089],[0.2667,0.129],[0.3,0.224],[0.3333,0.32],[0.3667,0.449],[0.4,0.576],[0.4333,0.644],[0.4667,0.649],[0.6333,0.651],[0.6667,0.657],[0.7667,0.661],[1.0,0.664],[1.2,0.665],[1.5,0.667],[1.5333,0.681],[1.5667,0.711],[1.6,0.762],[1.6333,0.831],[1.6667,0.972],[1.7,1.0]],"duration":2.9};
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
