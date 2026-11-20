/* Graphic vocabulary stays local and cannot interpolate arbitrary markup. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'..'),context={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(root,'js/ui-icons.js'),'utf8'),context);
const svg=context.window.SoproIcons.svg;
for(const name of ['calendar','chart','partners','alert','activity','check','search']){
 const value=svg(name);assert(value.includes('<svg')&&value.includes('aria-hidden="true"')&&value.includes('focusable="false"'));
 assert(!/https?:|<image|<script/.test(value));
}
for(const unknown of ['constructor','__proto__','<img src=x onerror=alert(1)>'])assert.equal(svg(unknown),svg('info'));
for(const name of ['app','crm-workspace','m15-datepicker','central-cadastros','fiscal-command-center','report-workflow']){
 const source=fs.readFileSync(path.join(root,'js',name+'.js'),'utf8');assert(!/[\u{1F300}-\u{1FAFF}]/u.test(source),'No emoji UI constants: '+name);
}
const dates=fs.readFileSync(path.join(root,'js/m15-datepicker.js'),'utf8');
assert(!/\.innerHTML\s*=/.test(dates),'Calendar continues to build its DOM without HTML injection');
console.log('Local decorative SVG vocabulary, unknown-name safety, emoji-free UI and calendar DOM PASS');
