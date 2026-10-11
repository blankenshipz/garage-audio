// SVG nodes are created explicitly; imported names and metadata never become markup.
export function plotGeometry(series) {
  const clean = series.map(s => ({...s,points:(s.points || []).filter(p => Number.isFinite(p.frequency_hz) && p.frequency_hz > 0 && Number.isFinite(p.db))})).filter(s => s.points.length > 1);
  if (!clean.length) return null;
  const values = clean.flatMap(s => s.points.map(p => p.db));
  const frequencies = clean.flatMap(s => s.points.map(p => p.frequency_hz));
  const fmin = Math.min(20,...frequencies), fmax = Math.max(20000,...frequencies);
  const bottom = Math.floor(Math.min(...values)/10)*10-5, top = Math.ceil(Math.max(...values)/10)*10+5;
  const x = f => 49+Math.log(f/fmin)/Math.log(fmax/fmin)*441;
  const y = db => 205-(db-bottom)/(top-bottom)*180;
  return {clean,fmin,fmax,bottom,top,x,y};
}
export function renderPlot(container, series, title) {
  container.replaceChildren();
  const geometry = plotGeometry(series);
  if (!geometry) { const empty=document.createElement('p');empty.className='empty';empty.textContent='No verified curve available.';container.append(empty);return; }
  const {clean,fmin,fmax,bottom,top,x,y}=geometry;
  const ns='http://www.w3.org/2000/svg';
  const node=(tag,attrs={},text)=>{const el=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v)));if(text!==undefined)el.textContent=text;return el;};
  const svg=node('svg',{viewBox:'0 0 520 270',role:'img','aria-label':title});svg.append(node('title',{},title));
  const colors=['#d7b675','#94b5ab','#b79ec6'];
  for(let i=0;i<5;i++){const db=bottom+(top-bottom)*i/4;svg.append(node('line',{x1:49,y1:y(db),x2:490,y2:y(db),stroke:'#343a30','stroke-width':1}));svg.append(node('text',{x:40,y:y(db)+3,fill:'#969d8e','font-size':10,'text-anchor':'end'},db.toFixed(0)));}
  for(const f of [20,50,100,200,500,1000,2000,5000,10000,20000]){if(f<fmin||f>fmax)continue;svg.append(node('line',{x1:x(f),y1:25,x2:x(f),y2:205,stroke:'#292e26','stroke-width':1}));if([20,100,1000,10000,20000].includes(f))svg.append(node('text',{x:x(f),y:224,fill:'#969d8e','font-size':10,'text-anchor':'middle'},f>=1000?`${f/1000}k`:f));}
  clean.forEach((s,i)=>{svg.append(node('path',{d:s.points.map((p,j)=>`${j?'L':'M'}${x(p.frequency_hz).toFixed(2)},${y(p.db).toFixed(2)}`).join(' '),fill:'none',stroke:colors[i%3],'stroke-width':1.8}));svg.append(node('text',{x:49+i*148,y:249,fill:colors[i%3],'font-size':10},s.label));});
  svg.append(node('text',{x:12,y:14,fill:'#969d8e','font-size':10},'dB'),node('text',{x:490,y:224,fill:'#969d8e','font-size':10},'Hz'));container.append(svg);
}
