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
      for(const [viewport,width,height] of [['desktop',1280,900],['mobile',390,844]]) {
        const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'}),page=await context.newPage();
        if(process.env.QA_FONT_CSS) await context.route('https://fonts.googleapis.com/**',route=>route.fulfill({contentType:'text/css',body:readFileSync(process.env.QA_FONT_CSS,'utf8')}));
        const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',e=>{if(e.type()==='error')errors.push(e.text()+' '+e.location().url);});
        await page.goto(base,{waitUntil:'networkidle'});
        await page.locator('#lu').fill('qa@example.test');await page.locator('#ls').fill('qa-test-password');await page.locator('#lf .btn').click();
        await page.locator('nav [data-t="folha"]').waitFor();await page.locator('nav [data-t="folha"]').click();
        await page.getByRole('heading',{name:'Encargos, benefícios e provisões'}).waitFor();
        assert.equal(await page.locator('.salary-compact').count(),1);
        await page.screenshot({path:`${output}/sigi-${name}-folha-${viewport}.png`,fullPage:true});
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow on payroll');
        await page.getByRole('button',{name:'RH / benefícios',exact:true}).click();await page.locator('[data-profile="vt_amount"]').fill('220');await page.locator('[data-profile="va_amount"]').fill('500');await page.getByRole('button',{name:'Salvar dados de RH'}).click();await page.locator('#taskOverlay').waitFor({state:'hidden'});
        if(viewport==='desktop') {
          page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'Fechar folha e gerar contas a pagar'}).click();await page.getByRole('button',{name:'Folha fechada',exact:true}).waitFor();
        }
        await page.locator('nav [data-t="financeiro"]').click();await page.getByRole('heading',{name:'DRE integrada · regime de competência'}).waitFor();
        if(await page.getByRole('button',{name:'Marcar como pago',exact:true}).count()) await page.getByRole('button',{name:'Marcar como pago',exact:true}).first().click();await page.locator('input[type="file"]').first().waitFor();
        await page.locator('input[type="file"]').first().setInputFiles({name:'comprovante.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7\nQA receipt')});await page.getByRole('button',{name:'comprovante.pdf',exact:true}).last().waitFor();
        await page.screenshot({path:`${output}/sigi-${name}-financeiro-${viewport}.png`,fullPage:true});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow on finance');
        await page.locator('nav [data-t="espacos"]').click();await page.getByRole('heading',{name:'Espaços e agenda'}).waitFor();
        await page.locator('#sn').fill('Espaço '+viewport);await page.locator('#spaceCapacity').fill('20');await page.locator('#spaceShared').selectOption('1');await page.getByRole('button',{name:'Adicionar recurso',exact:true}).click();await page.locator(`input[data-sn][value="Espaço ${viewport}"]`).waitFor();await page.getByRole('button',{name:'Equipe automática',exact:true}).last().waitFor();
        await page.getByRole('button',{name:'Equipe automática',exact:true}).last().click();await page.locator('#staffEmployee').selectOption(String(employee.id));await page.locator('#staffBefore').fill('30');await page.getByRole('button',{name:'Vincular funcionário',exact:true}).click();await page.getByRole('button',{name:'Remover',exact:true}).waitFor();await page.locator('#taskDialogContent').getByRole('button',{name:'Fechar',exact:true}).click();
        await page.locator('#bookingSpace').selectOption({label:'Espaço '+viewport});await page.locator('#bookingTitle').fill('Evento '+viewport);await page.locator('#bookingAttendees').fill('10');await page.locator('#bookingExclusive').selectOption('0');await page.locator('#bookingCompatibility').fill('workshop');await page.locator('#bookingStart').fill(viewport==='desktop'?'2026-10-15T10:00':'2026-10-16T10:00');await page.locator('#bookingEnd').fill(viewport==='desktop'?'2026-10-15T12:00':'2026-10-16T12:00');await page.getByRole('button',{name:'Confirmar reserva',exact:true}).click();
        await page.waitForFunction(title=>document.querySelector('.booking-chip')?.parentElement.parentElement.textContent.includes(title)||[...document.querySelectorAll('.booking-chip')].some(n=>n.textContent.includes(title)), 'Evento '+viewport);
        await page.getByText('Editar eventos e consultar escalas',{exact:true}).click();await page.getByRole('button',{name:'Editar',exact:true}).last().click();await page.locator('#eventTitle').fill('Evento editado '+viewport);await page.getByRole('button',{name:'Salvar e atualizar escala',exact:true}).click();await page.locator('#taskOverlay').waitFor({state:'hidden'});
        await page.screenshot({path:`${output}/sigi-${name}-espacos-${viewport}.png`,fullPage:true});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow on spaces');
        assert.deepEqual(errors,[]);
        verdict.push({build:name,viewport,ok:true,flows:['login','payroll','benefits','close','payment','receipt','space','staff','event','edit']});
        await context.close();
      }
    } finally {server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));}
  }
} finally {await browser.close();}
console.log(JSON.stringify(verdict,null,2));
