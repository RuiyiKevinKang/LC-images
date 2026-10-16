/* Python runs in an isolated worker so the interface remains responsive. */
let runtimePromise;
let queue = Promise.resolve();
function progress(text) { self.postMessage({type:'progress',text}); }
async function runtime() {
  if (!runtimePromise) runtimePromise = (async () => {
    progress('正在从本站加载计算引擎。首次使用需要下载数十 MB，后续通常使用浏览器缓存…');
    const indexURL = new URL('vendor/pyodide/',self.location.href).href;
    importScripts(indexURL+'pyodide.js');
    const py = await loadPyodide({indexURL});
    progress('正在加载 NumPy 与 Excel 文件支持…');
    await py.loadPackage(['numpy','micropip']);
    py.globals.set('excel_wheels_json', JSON.stringify([
      indexURL+'et_xmlfile-2.0.0-py3-none-any.whl',
      indexURL+'openpyxl-3.1.5-py2.py3-none-any.whl'
    ]));
    await py.runPythonAsync('import micropip, json\nawait micropip.install(json.loads(excel_wheels_json), deps=False)');
    for (const name of ['bin_pixels.py','solve_A.py','web_bridge.py']) {
      const response = await fetch(new URL('python/'+name,self.location.href));
      if (!response.ok) throw new Error('无法加载求解源码 '+name+'（HTTP '+response.status+'）');
      py.FS.writeFile('/home/pyodide/'+name, await response.text(),{encoding:'utf8'});
    }
    py.runPython('import sys, json\nsys.path.insert(0,"/home/pyodide")\nimport web_bridge as bridge');
    py.FS.mkdirTree('/tmp/lc_uploads/G'); py.FS.mkdirTree('/tmp/lc_uploads/P');
    self.postMessage({type:'ready'});
    return py;
  })().catch(error => { runtimePromise = undefined; throw error; });
  return runtimePromise;
}
async function handle(data) {
  const py = await runtime();
  py.globals.set('command_json',JSON.stringify({...data,buffer:undefined}));
  if (data.action === 'upload') {
    const name = data.name.replace(/[^\p{L}\p{N}_.-]/gu,'_').slice(-150) || 'input.json';
    const path = '/tmp/lc_uploads/'+data.role+'/'+name;
    for(const previous of py.FS.readdir('/tmp/lc_uploads/'+data.role)) if(previous!=='.'&&previous!=='..') py.FS.unlink('/tmp/lc_uploads/'+data.role+'/'+previous);
    py.FS.writeFile(path,new Uint8Array(data.buffer));
    py.globals.set('uploaded_path',path);
    const result = JSON.parse(py.runPython('json.dumps(bridge.inspect_input(uploaded_path,json.loads(command_json)["role"],json.loads(command_json).get("options",{})),ensure_ascii=False)'));
    return {...result,path};
  }
  if (data.action === 'demo') {
    return JSON.parse(py.runPython('json.dumps(bridge.make_demo("/tmp/lc_demo"),ensure_ascii=False)'));
  }
  if (data.action === 'inspect') return JSON.parse(py.runPython('json.dumps(bridge.inspect_input(json.loads(command_json)["path"],json.loads(command_json)["role"],json.loads(command_json).get("options",{})),ensure_ascii=False)'));
  if (data.action === 'solve') {
    progress('正在进行空间分箱、时间对齐和 SVD 求解，请保留此页面…');
    return JSON.parse(py.runPython('c=json.loads(command_json)\njson.dumps(bridge.run_job(c["g_path"],c["p_path"],c.get("options",{})),ensure_ascii=False,allow_nan=False)'));
  }
  if (data.action === 'templates') return JSON.parse(py.runPython('json.dumps(bridge.make_templates("/tmp/lc_templates"),ensure_ascii=False)'));
  if (data.action === 'download') {
    if (!/^\/tmp\/lc_(result|templates)\//.test(data.path)) throw new Error('无效的下载路径');
    const bytes = py.FS.readFile(data.path).slice();
    return {buffer:bytes.buffer};
  }
  throw new Error('未知操作');
}
self.onmessage = ({data}) => {
  queue = queue.then(async () => {
    try {
      const result = await handle(data);
      if(result && result.buffer instanceof ArrayBuffer) self.postMessage({id:data.id,result},[result.buffer]);
      else self.postMessage({id:data.id,result});
    } catch(error) { self.postMessage({id:data.id,error:String(error.message||error)}); }
  });
};
