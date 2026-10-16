'use strict';
const $ = id => document.getElementById(id);
const state = {worker:null,pending:new Map(),seq:0,paths:{G:null,P:null},result:null,templates:null,busy:false,tab:'matrix'};
const MIME = {zip:'application/zip',xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',csv:'text/csv;charset=utf-8',json:'application/json',npy:'application/octet-stream',npz:'application/octet-stream'};
function message(text,kind='') { const el=$('message');el.hidden=false;el.className='message '+kind;el.textContent=text; }
function engine(text,kind='') { $('engineState').className='engine-state '+kind;$('engineState').replaceChildren(Object.assign(document.createElement('i'),{}),document.createTextNode(text)); }
function worker() {
  if(!state.worker) {
    state.worker = new Worker('worker.js');
    state.worker.onmessage = ({data}) => {
      if(data.type==='progress') {message(data.text,'loading');engine('计算引擎运行中','loading');return;}
      if(data.type==='ready') {engine('计算引擎已就绪','ready');return;}
      const task=state.pending.get(data.id);if(!task)return;
      state.pending.delete(data.id);
      if(data.error) task.reject(new Error(data.error));else task.resolve(data.result);
    };
    state.worker.onerror = event => {
      const error=new Error(event.message||'计算引擎加载失败，请检查网络后重试。');
      for(const task of state.pending.values())task.reject(error);
      state.pending.clear();state.worker.terminate();state.worker=null;engine('引擎加载失败，可重试','error');
    };
  }
  return state.worker;
}
function request(action,payload={},transfer=[]) {
  const id=++state.seq;
  return new Promise((resolve,reject)=>{state.pending.set(id,{resolve,reject});worker().postMessage({id,action,...payload},transfer);});
}
function busy(value) {
  state.busy=value;
  for(const id of ['demoBtn','solveBtn','gFile','pFile']) $(id).disabled=value;
  for(const el of document.querySelectorAll('.templateBtn'))el.disabled=value;
  $('cancelBtn').hidden=!value;
  $('solveBtn').textContent=value?'正在处理…':'求解映射矩阵 →';
}
function options() {
  if(!$('settingsForm').reportValidity())throw new Error('请先填写有效的求解设置。');
  const o={bin_width:Number($('binWidth').value),time_align:$('timeAlign').value,normalize_target:$('normalize').checked,video_index:Number($('videoIndex').value),max_fit_abs:Number($('maxFit').value),max_mass_error:Number($('maxMass').value)};
  if($('targetKind').value!=='auto')o.target_kind=$('targetKind').value;
  if($('requiredRank').value)o.required_rank=Number($('requiredRank').value);
  if($('cellArea').value)o.cell_area=Number($('cellArea').value);
  return o;
}
function showInput(role,summary,name) {
  const key=role.toLowerCase();
  $(key+'Name').textContent=name||summary.filename||'演示数据';
  $(key+'Info').textContent=`${summary.frames} 帧 · ${summary.cells} ${role==='G'?'输入像素 / 分箱':'概率网格'}${summary.videos>1?' · '+summary.videos+' 组视频':''} · ${summary.time_is_frame_index?'帧索引':'物理时间'}`;
  $(key+'Badge').textContent='已导入';$(key+'Badge').className='badge ready';
}
function invalidate() { state.result=null;$('exportBtn').disabled=true;$('emptyResult').hidden=false;for(const el of document.querySelectorAll('.result-panel'))el.hidden=true;$('resultStatus').textContent='等待重新计算';$('resultStatus').className='badge';for(const id of ['metricShape','metricRank','metricFit','metricMass'])$(id).textContent='—';$('resultSubtitle').textContent='输入或设置已更新，请重新求解。';$('previewNote').textContent='计算完成后可预览'; }
async function upload(role,file) {
  if(!file||state.busy)return;
  if(!/\.(xlsx|csv|tsv|json|npy|npz)$/i.test(file.name)){message('文件格式不支持。请选择 XLSX、CSV、TSV、JSON、NPY 或 NPZ。','error');return;}
  if(file.size>40*1024*1024){message('文件超过 40 MB 浏览器版本限制，请拆分数据或使用开源代码在本地运行。','error');return;}
  state.paths[role]=null;invalidate();busy(true);message('正在读取 '+file.name+'…','loading');
  const key=role.toLowerCase();$(key+'Badge').textContent='读取中';
  try {const buffer=await file.arrayBuffer();const result=await request('upload',{role,name:file.name,buffer,options:options()},[buffer]);if(result.status==='rejected')throw new Error((result.reasons||[]).join('；'));state.paths[role]=result.path;showInput(role,result,file.name);message(file.name+' 已读取。请确认两侧数据与求解设置。');}
  catch(error){$(key+'Badge').textContent='导入失败';$(key+'Badge').className='badge error';$(key+'Name').textContent='点击重新选择 '+role+' 文件';$(key+'Info').textContent='当前文件未被接受';message(cleanError(error),'error');}
  finally{busy(false);}
}
function cleanError(error) {const text=String(error.message||error);const lines=text.trim().split('\n');return lines.length>3?'处理失败：'+lines.slice(-2).join(' '):text;}
for(const role of ['G','P']) {
  const key=role.toLowerCase();$(key+'File').addEventListener('change',event=>upload(role,event.target.files[0]));
  const drop=$(key+'Drop');drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('dragging');});drop.addEventListener('dragleave',()=>drop.classList.remove('dragging'));drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('dragging');if(e.dataTransfer.files.length!==1){message('每个输入区域请放入一个数据文件。','error');return;}upload(role,e.dataTransfer.files[0]);});
}
$('settingsForm').addEventListener('change',()=>{if(state.result)invalidate();});
$('demoBtn').addEventListener('click',async()=>{
  busy(true);invalidate();message('正在准备可复现的演示数据…','loading');
  try {const demo=await request('demo');state.paths={G:demo.g_path,P:demo.p_path};for(const role of ['G','P'])showInput(role,await request('inspect',{role,path:state.paths[role],options:options()}),'演示 '+role+' · '+state.paths[role].split('/').pop());message('演示数据已就绪。点击「求解映射矩阵」查看真实计算结果。');}
  catch(error){message(cleanError(error),'error');}finally{busy(false);}
});
$('settingsForm').addEventListener('submit',async event=>{
  event.preventDefault();if(state.busy)return;
  if(!state.paths.G||!state.paths.P){message('请先分别导入 G 与 P 数据，也可以点击「载入演示数据」。','error');return;}
  let opts;try{opts=options();}catch(error){message(cleanError(error),'error');return;}
  invalidate();busy(true);message('正在检查输入并求解映射矩阵…','loading');
  try{state.result=await request('solve',{g_path:state.paths.G,p_path:state.paths.P,options:opts});renderResult(state.result);message(state.result.status==='accepted'?'求解完成，数值检验通过。可以预览并下载完整结果。':'本次输入未通过求解或数值检验：'+(state.result.reasons||[]).join('；'),state.result.status==='accepted'?'':'error');$('results').scrollIntoView({behavior:'smooth',block:'start'});engine('计算引擎已就绪','ready');}
  catch(error){message(cleanError(error),'error');engine('计算中断，可重试','error');}finally{busy(false);}
});
$('cancelBtn').addEventListener('click',()=>{if(state.worker)state.worker.terminate();state.worker=null;for(const task of state.pending.values())task.reject(new Error('已取消计算。请重新导入数据或载入演示。'));state.pending.clear();state.paths={G:null,P:null};state.templates=null;for(const k of ['g','p']){$(k+'Badge').textContent='待导入';$(k+'Badge').className='badge';$(k+'Name').textContent='点击选择或拖入 '+k.toUpperCase()+' 文件';}engine('计算引擎按需加载');invalidate();busy(false);message('已取消计算并清除引擎内的数据。请重新导入文件。');});
async function download(file) {
  if(state.busy)return;
  try{const response=await request('download',{path:file.path});const blob=new Blob([response.buffer],{type:file.mime||MIME[file.name.split('.').pop()]||'application/octet-stream'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=file.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),15000);}
  catch(error){message(cleanError(error),'error');}
}
for(const btn of document.querySelectorAll('.templateBtn'))btn.addEventListener('click',async()=>{
  if(state.busy)return;busy(true);
  try{if(!state.templates)state.templates=await request('templates');const role=btn.dataset.role;const file=state.templates.find(f=>f.name==='demo_'+role+'.xlsx')||state.templates.find(f=>f.name.endsWith('.xlsx')&&f.name.includes(role));busy(false);if(!file)throw new Error('模板文件尚未生成');await download(file);message('XLSX 示例模板已生成。替换数据时请保留列名与固定网格结构。');}
  catch(error){message(cleanError(error),'error');}finally{busy(false);}
});
$('exportBtn').addEventListener('click',()=>{const file=state.result?.downloads?.find(f=>f.name==='result.zip');if(file)download(file);});
function fmt(v){return typeof v==='number'&&Number.isFinite(v)?(v===0?'0':Math.abs(v)<.001||Math.abs(v)>1e5?v.toExponential(3):Number(v.toPrecision(6)).toString()):'—';}
function links(id,files) {$(id).replaceChildren();for(const file of files){const button=document.createElement('button');button.textContent='↓ '+file.name;button.addEventListener('click',()=>download(file));$(id).append(button);}}
function switchTab(tab) {state.tab=tab;for(const button of document.querySelectorAll('.tab')){const active=button.dataset.tab===tab;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}for(const el of document.querySelectorAll('.result-panel'))el.hidden=!state.result||el.id!=='panel-'+tab;$('emptyResult').hidden=!!state.result;}
for(const button of document.querySelectorAll('.tab')){button.addEventListener('click',()=>switchTab(button.dataset.tab));button.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const tabs=['matrix','probability','report'];const next=tabs[(tabs.indexOf(state.tab)+(event.key==='ArrowRight'?1:2))%3];switchTab(next);$('tab-'+next).focus();}});}
function renderResult(r) {
  const accepted=r.status==='accepted',m=r.metrics||{};
  $('tab-matrix').disabled=!accepted;$('tab-probability').disabled=!accepted;
  $('resultStatus').textContent=accepted?'检验通过':'未通过检验';$('resultStatus').className='badge '+(accepted?'ready':'error');
  $('resultSubtitle').textContent=accepted?`已完成 ${r.input?.frames??m.input_rank} 帧映射 · 用时 ${fmt(r.elapsed_seconds)} 秒 · 完整数据见下载文件`:'请查看检验报告中的具体原因，修改输入后重新求解。';
  $('metricShape').textContent=m.A_rows!=null?`${m.A_rows} × ${m.A_columns}`:'—';$('metricRank').textContent=fmt(m.A_rank);$('metricFit').textContent=fmt(m.maximum_fit_abs);$('metricMass').textContent=fmt(m.maximum_probability_sum_error);$('rankNote').textContent=m.input_rank!=null?'输入特征秩 '+m.input_rank:'要求 F 满行秩';
  $('exportBtn').disabled=!(r.downloads||[]).some(f=>f.name==='result.zip');$('previewNote').textContent=accepted?'热图最多显示左上 20 × 20 项':'报告可下载';
  links('matrixDownloads',(r.downloads||[]).filter(f=>['A.csv','A.npy','A_factors.npz','result.xlsx'].includes(f.name)));
  links('predictionDownloads',(r.downloads||[]).filter(f=>['prediction.csv','target_cells.csv','feature_metadata.csv'].includes(f.name)));
  links('reportDownloads',(r.downloads||[]).filter(f=>f.name==='report.json'));
  renderReport(r);if(accepted){drawMatrix(r.matrix_preview||[]);$('scaleMin').textContent=fmt(r.matrix_min);$('scaleMax').textContent=fmt(r.matrix_max);drawProbability(r.actual_preview||[],r.prediction_preview||[]);}switchTab(accepted?'matrix':'report');
}
function renderReport(r) {
  const container=$('reportContent');container.replaceChildren();
  if(r.reasons?.length){const box=document.createElement('div');box.className='report-reasons';box.textContent=r.reasons.join('；');container.append(box);}
  const labels={A_rows:'A 行数（活动网格）',A_columns:'A 列数（三帧分箱特征）',A_rank:'A 的实际秩',target_rank:'目标 P 的秩',input_rank:'输入 F 的秩',input_condition:'输入 F 条件数',maximum_seed_fit_abs:'基解最大拟合误差',maximum_fit_abs:'修正后最大拟合误差',minimum_probability:'重建最小概率',maximum_probability_sum_error:'概率和最大误差',numerical_offset_total:'非负修正总量',relative_L1_percent_mean:'平均 L1 误差（%）',TV_percent_mean:'平均总变差（%）',mass_RMSE:'概率质量 RMSE'};
  const table=document.createElement('table');table.className='report-table';for(const [key,label]of Object.entries(labels)){if(!(key in (r.metrics||{})))continue;const row=table.insertRow();row.insertCell().textContent=label;row.insertCell().textContent=fmt(r.metrics[key]);}container.append(table);
  const note=document.createElement('p');note.className='figure-caption';note.textContent='验收仅覆盖输入帧；条件数较大时，映射对输入扰动可能敏感。完整报告记录参数、文件校验值、特征顺序与目标网格信息。';container.append(note);
}
function drawMatrix(matrix) {
  const c=$('matrixCanvas'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);if(!matrix.length)return;
  const rows=matrix.length,cols=matrix[0].length,left=54,top=18,width=c.width-80,height=c.height-65,sw=width/cols,sh=height/rows,max=Math.max(...matrix.flat().map(Math.abs),1e-15);
  ctx.font='16px Segoe UI';ctx.fillStyle='#90a397';ctx.textAlign='center';
  for(let i=0;i<rows;i++)for(let j=0;j<cols;j++){const v=matrix[i][j]/max,base=v<0?[56,94,155]:[196,123,83],a=Math.abs(v);ctx.fillStyle=`rgb(${base.map(n=>Math.round(247*(1-a)+n*a)).join(',')})`;ctx.fillRect(left+j*sw,top+i*sh,Math.max(1,sw-1),Math.max(1,sh-1));}
  for(let j=0;j<cols;j+=Math.max(1,Math.ceil(cols/10)))ctx.fillText(String(j),left+(j+.5)*sw,top+height+25);
  ctx.textAlign='right';for(let i=0;i<rows;i+=Math.max(1,Math.ceil(rows/8)))ctx.fillText(String(i),left-12,top+(i+.5)*sh+5);
  ctx.textAlign='center';ctx.fillText('特征索引（从 0 开始）',left+width/2,c.height-4);ctx.save();ctx.translate(17,top+height/2);ctx.rotate(-Math.PI/2);ctx.fillText('活动网格索引',0,0);ctx.restore();
}
function drawProbability(actual,predicted) {
  const c=$('probCanvas'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);if(!actual.length)return;
  const left=65,top=25,width=c.width-90,height=c.height-75,max=Math.max(...actual,...predicted,1e-10)*1.15;
  ctx.font='14px Segoe UI';ctx.strokeStyle='#e9eeea';ctx.fillStyle='#91a497';ctx.textAlign='right';for(let i=0;i<5;i++){const y=top+height*i/4;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(left+width,y);ctx.stroke();ctx.fillText(fmt(max*(1-i/4)),left-10,y+5);}
  for(const [data,color,dash]of [[actual,'#658dbd',[]],[predicted,'#3b8970',[5,4]]]){ctx.strokeStyle=color;ctx.lineWidth=2;ctx.setLineDash(dash);ctx.beginPath();data.forEach((v,i)=>{const x=left+(data.length===1?width/2:i/(data.length-1)*width),y=top+height*(1-v/max);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();ctx.setLineDash([]);if(data.length<30)for(let i=0;i<data.length;i++){ctx.beginPath();ctx.arc(left+(data.length===1?width/2:i/(data.length-1)*width),top+height*(1-data[i]/max),3,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();}}
  ctx.fillStyle='#91a497';ctx.textAlign='center';ctx.fillText('原始网格索引（从 0 开始，含恒零网格）',left+width/2,c.height-3);ctx.fillText('0',left,top+height+22);ctx.fillText(String(actual.length-1),left+width,top+height+22);
}
