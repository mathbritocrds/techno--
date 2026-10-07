// Browser regression check for the FastAPI app, in development and built output.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
const output = process.env.QA_SCREENSHOTS || '/workspace/screenshots';
mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE_PATH?{executablePath:process.env.BROWSER_EXECUTABLE_PATH}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
const verdict=[];
try {
  for(const [name,port,cwd] of [['dev',18080,resolve('.')],['built',18081,resolve('dist/sigi')]]) {
    const db=resolve(mkdtempSync(resolve(tmpdir(),'sigi-qa-')),'qa.db');
    const server=spawn('python',['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)],{cwd,env:{...process.env,DB_PATH:db,SUPABASE_DATABASE_URL:''},stdio:['ignore','ignore','pipe']});
    let serverErrors='';server.stderr.on('data',chunk=>serverErrors+=chunk.toString());
    const base=`http://127.0.0.1:${port}`;
    try {
      let ready=false;
      for(let i=0;i<80;i++){try{if((await fetch(base+'/auth/status')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
      assert(ready,serverErrors);
      const registration=await fetch(base+'/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:'qa@example.test',username:'QA SIGI',password:'qa-test-password',company_name:'Empresa de teste'})});
      const auth=await registration.json();assert(auth.token,JSON.stringify(auth));
      const headers={'Content-Type':'application/json',Authorization:'Bearer '+auth.token};
      const post=async(path,body)=>{const r=await fetch(base+path,{method:'POST',headers,body:JSON.stringify(body)});const result=await r.json();assert(r.ok,JSON.stringify(result));return result;};
      const employee=await post('/employees',{name:'Colaborador QA',role:'Atendimento',salary:5000,pin:'1234'});
      const profile=await fetch(base+`/employees/${employee.id}/payroll-profile`,{method:'PUT',headers,body:JSON.stringify({hired_on:'2024-01-01'})});assert(profile.ok);
      const incomplete=await (await fetch(base+'/dashboard',{headers})).json();assert.equal(incomplete.setup_completed,false);
      await fetch(base+'/settings/workplace',{method:'PUT',headers,body:JSON.stringify({lat:-23.55,lng:-46.63,radius_m:100})});
      await post('/products',{name:'Produto QA'});await post('/materials',{name:'Material QA'});await post('/tasks',{title:'Primeira tarefa QA'});await post('/spaces',{name:'Espaço inicial'});
      await post('/finance',{kind:'receber',description:'Venda QA anterior',amount:10000,due:'2026-09-10'});await post('/finance',{kind:'receber',description:'Venda QA',amount:15000,due:'2026-10-10'});await post('/finance',{kind:'pagar',description:'Insumos QA',amount:2000,due:'2026-10-12',category:'cost'});
      await post('/cost-analyses',{title:'Análise QA',revenue:15000,material:3000,opex:2500,tax:500});
      const department=await post('/departments',{name:'Atendimento QA',lead_employee_id:employee.id});
      await post('/auth/register',{email:'manager@example.test',password:'manager-test-password',role:'manager',department_id:department.id,employee_id:employee.id});
      const publicEmployee=await post('/employees',{name:'Ponto PIN QA',salary:2000,pin:'4321'});
      const publicProfile=await fetch(base+`/employees/${publicEmployee.id}/payroll-profile`,{method:'PUT',headers,body:JSON.stringify({hired_on:'2026-01-01'})});assert(publicProfile.ok);
      await post(`/employees/${employee.id}/account`,{email:'employee@example.test',password:'employee-test-password'});
      for(const [viewport,width,height] of [['desktop',1280,900],['mobile',390,844]]) {
        const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'}),page=await context.newPage();
        if(process.env.QA_FONT_CSS) await context.route('https://fonts.googleapis.com/**',route=>route.fulfill({contentType:'text/css',body:readFileSync(process.env.QA_FONT_CSS,'utf8')}));
        const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',e=>{if(e.type()==='error')errors.push(e.text()+' '+e.location().url);});
        await page.goto(base,{waitUntil:'networkidle'});
        await page.locator('#lu').fill('qa@example.test');await page.locator('#ls').fill('qa-test-password');await page.locator('#lf .btn').click();
        await page.locator('[data-sector="rh"]').waitFor();
        await page.locator('[data-dre-charts]').waitFor();assert.equal(await page.locator('[data-setup]').count(),0);
        await page.screenshot({path:`${output}/sigi-${name}-painel-${viewport}.png`,fullPage:true});
        const navigate=async(group,tab)=>{if(await page.locator(`[data-sector="${group}"]`).getAttribute('aria-expanded')!=='true')await page.locator(`[data-sector="${group}"]`).click();await page.locator(`#submenuItems [data-t="${tab}"]`).click();};
        await page.locator('[data-sector="rh"]').click();assert.equal(await page.locator('[data-sector="rh"]').getAttribute('aria-expanded'),'true');await page.locator('[data-sector="rh"]').click();assert.equal(await page.locator('[data-sector="rh"]').getAttribute('aria-expanded'),'false');
        await navigate('rh','folha');
        await page.getByRole('heading',{name:'Encargos, benefícios e provisões'}).waitFor();
        assert.equal(await page.locator('.salary-compact').count(),2);
        await page.screenshot({path:`${output}/sigi-${name}-folha-${viewport}.png`,fullPage:true});
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow on payroll');
        await page.getByRole('button',{name:'RH / benefícios',exact:true}).first().click();await page.locator('[data-profile="vt_amount"]').fill('220');await page.locator('[data-profile="va_amount"]').fill('500');await page.getByRole('button',{name:'Salvar dados de RH'}).click();await page.locator('#taskOverlay').waitFor({state:'hidden'});
        if(viewport==='desktop') {
          page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'Fechar folha e gerar contas a pagar'}).click();await page.getByRole('button',{name:'Folha fechada',exact:true}).waitFor();
        }
        await navigate('finance','financeiro');await page.getByRole('heading',{name:'DRE integrada · regime de competência'}).waitFor();
        if(await page.getByRole('button',{name:'Marcar como pago',exact:true}).count()) await page.getByRole('button',{name:'Marcar como pago',exact:true}).first().click();await page.locator('input[aria-label^="Anexar comprovante"]').first().waitFor();
        await page.locator('input[aria-label^="Anexar comprovante"]').first().setInputFiles({name:'comprovante.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7\nQA receipt')});await page.getByRole('button',{name:'comprovante.pdf',exact:true}).last().waitFor();
        const csv=Buffer.from(`ano;mes;setor_id;categoria;descricao;valor;referencia\n2026;10;${department.id};cost;Importação ${viewport};300,00;qa-${viewport}\n`);
        await page.locator('#dreImportFile').setInputFiles({name:'dre.csv',mimeType:'text/csv',buffer:csv});await page.getByRole('button',{name:'Confirmar importação',exact:true}).click();await page.locator('#taskOverlay').waitFor({state:'hidden'});
        await page.getByRole('cell',{name:'Custos operacionais',exact:true}).first().waitFor();
        const downloadWait=page.waitForEvent('download');await page.getByRole('button',{name:'Exportar DRE CSV / Excel',exact:true}).click();const download=await downloadWait;assert(download.suggestedFilename().startsWith('dre-'));
        await page.getByRole('combobox',{name:'Ordenar DRE',exact:true}).selectOption('expense');await page.getByRole('heading',{name:'Detalhamento por mês, setor e categoria'}).waitFor();
        await page.screenshot({path:`${output}/sigi-${name}-financeiro-${viewport}.png`,fullPage:true});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow on finance');
        await navigate('tasks','espacos');await page.getByRole('heading',{name:'Espaços e agenda'}).waitFor();
        await page.locator('#sn').fill('Espaço '+viewport);await page.locator('#spaceCapacity').fill('20');await page.locator('#spaceShared').selectOption('1');await page.getByRole('button',{name:'Adicionar recurso',exact:true}).click();await page.locator(`input[data-sn][value="Espaço ${viewport}"]`).waitFor();await page.getByRole('button',{name:'Equipe automática',exact:true}).last().waitFor();
        await page.getByRole('button',{name:'Equipe automática',exact:true}).last().click();await page.locator('#staffEmployee').selectOption(String(employee.id));await page.locator('#staffBefore').fill('30');await page.getByRole('button',{name:'Vincular funcionário',exact:true}).click();await page.getByRole('button',{name:'Remover',exact:true}).waitFor();await page.locator('#taskDialogContent').getByRole('button',{name:'Fechar',exact:true}).click();
        await page.locator('#bookingSpace').selectOption({label:'Espaço '+viewport});await page.locator('#bookingTitle').fill('Evento '+viewport);await page.locator('#bookingAttendees').fill('10');await page.locator('#bookingExclusive').selectOption('0');await page.locator('#bookingCompatibility').fill('workshop');await page.locator('#bookingStart').fill(viewport==='desktop'?'2026-10-15T10:00':'2026-10-16T10:00');await page.locator('#bookingEnd').fill(viewport==='desktop'?'2026-10-15T12:00':'2026-10-16T12:00');await page.getByRole('button',{name:'Confirmar reserva',exact:true}).click();
        await page.waitForFunction(title=>document.querySelector('.booking-chip')?.parentElement.parentElement.textContent.includes(title)||[...document.querySelectorAll('.booking-chip')].some(n=>n.textContent.includes(title)), 'Evento '+viewport);
        await page.getByText('Editar eventos e consultar escalas',{exact:true}).click();await page.getByRole('button',{name:'Editar',exact:true}).last().click();await page.locator('#eventTitle').fill('Evento editado '+viewport);await page.getByRole('button',{name:'Salvar e atualizar escala',exact:true}).click();await page.locator('#taskOverlay').waitFor({state:'hidden'});
        await page.screenshot({path:`${output}/sigi-${name}-espacos-${viewport}.png`,fullPage:true});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow on spaces');
        await navigate('security','seguranca');await page.getByRole('combobox',{name:'Funcionário cadastrado',exact:true}).waitFor();
        await navigate('rh','departamentos');await page.getByRole('combobox',{name:'Chefe cadastrado',exact:true}).first().waitFor();
        assert.deepEqual(errors,[]);
        verdict.push({build:name,viewport,ok:true,flows:['navigation','onboarding','dashboard-charts','payroll','benefits','close','payment','receipt','dre-import','dre-export','manager-dre','employee-gps','space','staff','event','edit']});
        await context.close();
        // Department managers can use DRE without loading admin-only endpoints.
        const managerContext=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'}),managerPage=await managerContext.newPage();
        if(process.env.QA_FONT_CSS)await managerContext.route('https://fonts.googleapis.com/**',route=>route.fulfill({contentType:'text/css',body:readFileSync(process.env.QA_FONT_CSS,'utf8')}));
        await managerPage.goto(base);await managerPage.locator('#lu').fill('manager@example.test');await managerPage.locator('#ls').fill('manager-test-password');await managerPage.locator('#lf .btn').click();await managerPage.locator('[data-sector="finance"]').click();await managerPage.locator('#submenuItems [data-t="financeiro"]').click();await managerPage.getByRole('heading',{name:'Financeiro do departamento'}).waitFor();await managerPage.getByRole('button',{name:'Exportar DRE CSV / Excel'}).waitFor();assert.equal(await managerPage.locator('#submenuItems [data-t="folha"]').count(),0);assert(await managerPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow on manager finance');await managerContext.close();
        // GPS consent and employee entry work on both viewports.
        const employeeContext=await browser.newContext({viewport:{width,height},geolocation:{latitude:-23.55,longitude:-46.63,accuracy:10},permissions:['geolocation'],reducedMotion:'reduce'}),employeePage=await employeeContext.newPage();
        if(process.env.QA_FONT_CSS)await employeeContext.route('https://fonts.googleapis.com/**',route=>route.fulfill({contentType:'text/css',body:readFileSync(process.env.QA_FONT_CSS,'utf8')}));
        await employeePage.goto(base);await employeePage.getByRole('button',{name:'Funcionário',exact:true}).click();await employeePage.locator('#lt').filter({hasText:'Acesso do funcionário'}).waitFor();await employeePage.locator('#lu').fill('employee@example.test');await employeePage.locator('#ls').fill('employee-test-password');await employeePage.locator('#lf .btn').click();await employeePage.locator('#employeeClockConsent').check();
        if(viewport==='desktop'){await employeePage.getByRole('button',{name:/Registrar entrada com GPS/}).click();await employeePage.getByRole('button',{name:/Registrar saída com GPS/}).waitFor();}
        assert(await employeePage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow on employee point');
        await employeePage.screenshot({path:`${output}/sigi-${name}-ponto-${viewport}.png`,fullPage:true});await employeeContext.close();
        if(viewport==='desktop'){
          const publicContext=await browser.newContext({viewport:{width,height},geolocation:{latitude:-23.55,longitude:-46.63,accuracy:10},permissions:['geolocation'],reducedMotion:'reduce'}),publicPage=await publicContext.newPage();
          if(process.env.QA_FONT_CSS)await publicContext.route('https://fonts.googleapis.com/**',route=>route.fulfill({contentType:'text/css',body:readFileSync(process.env.QA_FONT_CSS,'utf8')}));
          await publicPage.goto(base);await publicPage.getByRole('button',{name:'Bater ponto',exact:true}).click();await publicPage.locator('#le').selectOption(String(publicEmployee.id));await publicPage.locator('#lp').fill('4321');await publicPage.locator('#lc').check();await publicPage.getByRole('button',{name:'Registrar ponto',exact:true}).click();await publicPage.locator('#lt').filter({hasText:'Ponto registrado'}).waitFor();await publicContext.close();
        }
      }
    } finally {server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));}
  }
} finally {await browser.close();}
console.log(JSON.stringify(verdict,null,2));
