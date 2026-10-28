// Browser checks for density, preserved colors, local search and logical sections.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { tmpdir } from 'node:os';
import assert from 'node:assert/strict';

const output = process.env.QA_SCREENSHOTS || '/workspace/screenshots';
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE_PATH ? {executablePath: process.env.BROWSER_EXECUTABLE_PATH} : {}), args: ['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
const verdict = [];
try {
  for (const [build, port, cwd] of [['dev',18082,resolve('.')],['built',18083,resolve('dist/sigi')]]) {
    const server = spawn('python',['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)], {cwd,env:{...process.env,DB_PATH:resolve(mkdtempSync(resolve(tmpdir(),'sigi-density-')),'qa.db'),SUPABASE_DATABASE_URL:''},stdio:['ignore','ignore','pipe']});
    let log = ''; server.stderr.on('data',chunk => log += chunk);
    const base = `http://127.0.0.1:${port}`;
    try {
      let ready = false;
      for (let i=0;i<100;i++) { try { if ((await fetch(base+'/auth/status')).ok) { ready=true;break; } } catch {} await new Promise(r=>setTimeout(r,100)); }
      assert(ready,log);
      const request = async (path, body, token, method='POST') => {
        const response = await fetch(base+path,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(body?{body:JSON.stringify(body)}:{})});
        const value = await response.json(); assert(response.ok,JSON.stringify(value)); return value;
      };
      await request('/auth/register',{email:'density@example.test',username:'Gestor SIGI',password:'density-test-password',company_name:'SIGI Operações'});
      const {token} = await request('/auth/login',{email:'density@example.test',password:'density-test-password'});
      for (let i=1;i<=12;i++) await request('/employees',{name:`Pessoa Ágil ${String(i).padStart(2,'0')}`,role:'Operação',salary:2000,pin:'1234',hired_on:'2026-01-01'},token);
      for (let i=1;i<=30;i++) await request('/materials',{name:`Aço ${String(i).padStart(2,'0')}`,unit:'kg',stock:10,minimum_stock:2,unit_cost:12.5},token);
      for (let i=0;i<4;i++) await request('/tasks',{title:'Tarefa '+i,status:[0,0,1,2][i],severity:i===0?'critico':'normal'},token);
      await request('/settings/workplace',{lat:-23.55,lng:-46.63,radius_m:100},token,'PUT');
      await request('/products',{name:'Produto operacional'},token);
      await request('/spaces',{name:'Área operacional'},token);
      const month = new Date().toLocaleDateString('sv',{timeZone:'America/Sao_Paulo'}).slice(0,7);
      await request('/finance',{kind:'receber',description:'Venda operacional',amount:25000,due:month+'-15'},token);
      await request('/finance',{kind:'pagar',description:'Insumo operacional',amount:1500,due:month+'-20'},token);
      for (const [viewport,width,height,hasTouch] of [['desktop',1280,900,false],['mobile',390,844,true]]) {
        const context = await browser.newContext({viewport:{width,height},hasTouch,reducedMotion:'reduce',colorScheme:'light'});
        if (process.env.QA_FONT_CSS) await context.route('https://fonts.googleapis.com/**',route=>route.fulfill({contentType:'text/css',body:readFileSync(process.env.QA_FONT_CSS,'utf8')}));
        const page = await context.newPage(), errors=[];
        page.on('pageerror',e=>errors.push(e.message)); page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
        await page.goto(base); await page.locator('#lu').fill('density@example.test'); await page.locator('#ls').fill('density-test-password'); await page.locator('#lf .btn').click();
        await page.locator('.density-day-strip').waitFor();
        assert(!await page.locator('#main').evaluate(node=>node.hasAttribute('data-density-simple')),'Keep the dashboard outside simplified page styling');
        assert.deepEqual(await page.locator('.density-day-cards strong').allTextContents(),['2','1','0','1']);
        assert.equal(await page.locator('.overview-kpis .card').count(),4);
        const fits = async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Page overflow');
        await fits();
        await page.screenshot({path:`${output}/sigi-density-${build}-dashboard-${viewport}.png`,fullPage:true,animations:'disabled'});
        const navigate = async (group,tab) => {
          if (await page.locator(`[data-sector="${group}"]`).getAttribute('aria-expanded')!=='true') await page.locator(`[data-sector="${group}"]`).click();
          await page.locator(`#submenuItems [data-t="${tab}"]`).click();
        };
        await navigate('finance','materia'); await page.locator('.density-table-tools').waitFor();
        const tools = page.locator('.density-table-tools');
        const search = tools.locator('input[type=search]'), column = tools.locator('select');
        assert.equal(await page.locator('tr[data-k]:visible').count(),20);
        await page.locator('tr[data-k]').last().evaluate(node=>node.hidden=true);
        await page.emulateMedia({media:'print'});
        assert.equal(await page.locator('tr[data-k]:visible').count(),29,'Print shows all local pages but preserves original hidden flags');
        await page.emulateMedia({media:'screen'});
        await page.locator('tr[data-k]').last().evaluate(node=>node.hidden=false);
        await page.locator('.density-pager').getByRole('button',{name:'Próxima',exact:true}).click();
        assert.equal(await page.locator('tr[data-k]:visible').count(),10);
        await page.locator('.density-pager select').selectOption('0');
        assert.equal(await page.locator('tr[data-k]:visible').count(),30);
        await page.locator('.density-pager select').selectOption('20');
        let calls = 0; const watch = request => {if(new URL(request.url()).pathname.startsWith('/materials')) calls++;}; page.on('request',watch);
        await column.selectOption('0'); await search.fill('aco 01');
        await page.waitForFunction(()=>document.querySelector('.density-table-count')?.textContent==='1 de 30 registros nesta página');
        assert.equal(await page.locator('tr[data-k]:visible').count(),1);
        assert.equal(await page.locator('tr[data-k]:visible input[aria-label="Nome"]').inputValue(),'Aço 01');
        await search.fill('inexistente'); await page.locator('.density-empty:not([hidden])').waitFor();
        await tools.getByRole('button',{name:'Limpar busca',exact:true}).click();
        await page.waitForFunction(()=>document.querySelector('.density-table-count')?.textContent==='30 de 30 registros nesta página');
        assert.equal(await page.locator('tr[data-k]:visible').count(),20);
        assert.equal(calls,0,'Local search must not call or modify the API'); page.off('request',watch);
        const stock = viewport==='desktop'?'11':'12';
        await page.locator('tr[data-k] input[aria-label="Estoque"]').first().fill(stock);
        await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/materials/1' && r.request().method()==='PUT'),page.locator('tr[data-k] input[aria-label="Estoque"]').first().press('Tab')]);
        await page.waitForFunction(stock=>document.querySelector('tr[data-k] input[aria-label="Estoque"]')?.value===stock && document.querySelector('.density-table-tools'),stock);
        const materials = await request('/materials',null,token,'GET'); assert.equal(materials[0].stock,Number(stock));
        await fits();
        await page.mouse.move(0,0);
        const paints = ()=>page.evaluate(()=>Object.fromEntries(['body','.app main .card','.app main .btn','.app main input','.app main th','.app main td'].map(selector=>{const node=document.querySelector(selector),s=getComputedStyle(node);return [selector,[s.backgroundColor,s.color,s.borderTopColor,s.boxShadow]];})));
        for (const theme of ['light','dark']) {
          if (await page.locator('html').getAttribute('data-theme')!==theme) await page.getByRole('button',{name:'Alternar tema claro ou escuro',exact:true}).click();
          await page.locator('html.tt').waitFor({state:'detached'});
          const compact = await paints();
          await page.evaluate(()=>document.querySelector('link[href="/density.css"]').disabled=true);
          assert.deepEqual(await paints(),compact,'Existing colors must stay identical in '+theme);
          await page.evaluate(()=>document.querySelector('link[href="/density.css"]').disabled=false);
        }
        await page.screenshot({path:`${output}/sigi-density-${build}-materials-${viewport}.png`,fullPage:true,animations:'disabled'});
        await navigate('finance','financeiro'); await page.locator('[data-density-view="operation"][aria-pressed=true]').waitFor();
        assert.equal(await page.locator('[data-finance-accounts] .density-table-tools').count(),0,'Reuse finance server search');
        await page.locator('#fd').fill('Rascunho preservado');
        await page.evaluate(()=>window.__originalDescription=document.querySelector('#fd'));
        await page.locator('[data-density-view="results"]').click(); assert(!await page.locator('#fd').isVisible());
        await page.getByRole('heading',{name:'DRE integrada · regime de competência',exact:true}).waitFor();
        await page.locator('[data-density-view="operation"]').click();
        assert.equal(await page.locator('#fd').inputValue(),'Rascunho preservado');
        assert(await page.evaluate(()=>window.__originalDescription===document.querySelector('#fd')),'Tabs must keep original nodes');
        await page.locator('[data-density-view="operation"]').press('ArrowRight'); await page.locator('[data-density-view="results"][aria-pressed=true]').waitFor();
        await page.locator('[data-density-view="results"]').press('End'); await page.locator('[data-density-view="all"][aria-pressed=true]').waitFor();
        await page.locator('[data-density-view="operation"]').click();
        await page.locator('input[aria-label="Buscar lançamento financeiro"]').fill('Venda operacional'); await page.getByRole('button',{name:'Buscar contas',exact:true}).click();
        await page.getByRole('cell',{name:'Venda operacional',exact:true}).waitFor(); assert.equal(await page.locator('[data-finance-accounts] tbody tr').count(),1);
        await page.locator('[data-density-view="results"]').click();
        await page.getByRole('button',{name:'Atualizar consulta',exact:true}).click(); await page.locator('[data-density-view="results"][aria-pressed=true]').waitFor();
        await fits(); await page.screenshot({path:`${output}/sigi-density-${build}-finance-${viewport}.png`,fullPage:true,animations:'disabled'});
        await navigate('rh','ponto'); await page.locator('[data-density-view="work"][aria-pressed=true]').waitFor();
        const timesheet = page.locator('.card').filter({has:page.getByRole('heading',{name:'Espelho de ponto',exact:true})}).locator('table');
        const compactHeight = await timesheet.locator('tr').nth(1).evaluate(node=>node.getBoundingClientRect().height);
        await page.evaluate(()=>document.querySelector('link[href="/density.css"]').disabled=true);
        const originalHeight = await timesheet.locator('tr').nth(1).evaluate(node=>node.getBoundingClientRect().height);
        await page.evaluate(()=>document.querySelector('link[href="/density.css"]').disabled=false);
        if(!hasTouch)assert(compactHeight<=originalHeight*.6,'Read-only rows should fit about twice as many records');
        await page.locator('[data-density-view="settings"]').click(); await page.locator('#cn').waitFor();
        assert(await page.locator('#cn').evaluate(node=>node.parentElement.classList.contains('density-flat-grid')));
        if(width>380){const positions=await page.locator('#cn,#cj').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().top));assert.equal(positions[0],positions[1],'Company fields should share one grid row');}
        await fits();
        await navigate('rh','folha'); await page.locator('[data-density-view="team"][aria-pressed=true]').waitFor();
        assert(await page.locator('.employee-grid').isVisible()); assert(!await page.locator('.hr-overview').isVisible());
        assert(!await page.locator('.payroll-kpis').isVisible(),'Keep payroll totals in the analysis section');
        assert.equal(await page.locator('.employee-record[open]').count(),0);
        assert.equal(await page.locator('.employee-card').count(),12);
        const firstEmployee=page.locator('.employee-card').first();
        assert.deepEqual(await firstEmployee.locator('.employee-row').locator('.employee-name,.employee-role,.employee-avatar').allTextContents(),['PÁ','Pessoa Ágil 01','Operação']);
        assert(!await firstEmployee.locator('.employee-pay').isVisible());
        assert(!await firstEmployee.locator('.employee-actions').isVisible());
        const rowHeight=await firstEmployee.locator('.employee-row').evaluate(node=>node.getBoundingClientRect().height);assert(rowHeight>=44&&rowHeight<=80);
        await firstEmployee.locator('.employee-row').focus();await page.keyboard.press('Enter');await firstEmployee.locator('.employee-pay').waitFor();
        await page.locator('.employee-card').nth(1).locator('.employee-row').click();
        assert.equal(await page.locator('.employee-record[open]').count(),1,'Only one employee should expand at a time');
        assert(!await firstEmployee.locator('.employee-pay').isVisible());
        const secondEmployee=page.locator('.employee-card').nth(1);
        await secondEmployee.getByRole('button',{name:'Editar cadastro',exact:true}).click();
        await page.locator('#ec').fill('Operação sênior');await page.getByRole('button',{name:'Salvar cadastro',exact:true}).click();await page.locator('#taskOverlay').waitFor({state:'hidden'});
        await secondEmployee.locator('.employee-role').filter({hasText:'Operação sênior'}).waitFor();
        assert(await secondEmployee.locator('.employee-pay').isVisible(),'Keep the selected employee expanded after saving');
        await secondEmployee.locator('.employee-row').focus();await page.keyboard.press('Space');assert(!await secondEmployee.locator('.employee-pay').isVisible());
        await page.screenshot({path:`${output}/sigi-employee-bars-${build}-${viewport}.png`,fullPage:true,animations:'disabled'});
        await page.getByRole('searchbox',{name:'Buscar funcionário',exact:true}).fill('Pessoa Ágil 01');assert.equal(await page.locator('.employee-card:visible').count(),1);
        await page.locator('[data-density-view="charts"]').click();await page.getByRole('heading',{name:'Entenda os valores da equipe',exact:true}).waitFor();assert(await page.locator('.payroll-kpis').isVisible());await fits();
        await navigate('security','seguranca');await page.locator('[data-density-view="accounts"][aria-pressed=true]').waitFor();
        await page.locator('[data-density-view="settings"]').click();await page.getByRole('heading',{name:'Matriz de permissões',exact:true}).waitFor();await fits();
        await navigate('finance','contabilidade');await page.locator('[data-statement-row="net_profit"]').waitFor();assert.equal(await page.locator('[data-statement-row]').count(),15);assert.equal(await page.locator('#accountingRoot .density-table-tools').count(),0);
        await page.emulateMedia({media:'print'});assert.equal(await page.locator('.density-view-nav:visible').count(),0);await page.emulateMedia({media:'screen'});
        assert.deepEqual(errors,[]);verdict.push({build,viewport,colors:'unchanged',rowHeight:{original:originalHeight,compact:compactHeight},filters:true,tabs:true,originalNodes:true,consoleErrors:errors});
        await context.close();
      }
    } finally { server.kill(); await new Promise(resolve=>server.once('close',resolve)); }
  }
} finally { await browser.close(); }
console.log(JSON.stringify(verdict,null,2));
