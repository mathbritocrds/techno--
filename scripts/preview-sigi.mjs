import { existsSync, mkdirSync, openSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
const pidPath = resolve('dist/sigi-preview.pid');
if (existsSync(pidPath)) {
  const pid = Number(readFileSync(pidPath, 'utf8'));
  try { process.kill(pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
  unlinkSync(pidPath);
}
if (process.argv[2] === 'stop') process.exit(0);
if (!existsSync('dist/sigi/app/main.py')) throw new Error('Run npm run build first.');
mkdirSync('data', { recursive: true });
const log = openSync('dist/sigi-preview.log', 'a');
const child = spawn('python', ['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8081'], {cwd:resolve('dist/sigi'),env:{...process.env,DB_PATH:resolve('data/flux.db')},detached:true,stdio:['ignore',log,log]});
writeFileSync(pidPath,String(child.pid));child.unref();
for (let i=0;i<40;i++) { try { const r=await fetch('http://127.0.0.1:8081/docs'); if(r.ok){console.log('SIGI production preview ready');process.exit(0);} }catch{} await new Promise(r=>setTimeout(r,250)); }
throw new Error('Production preview failed; inspect dist/sigi-preview.log.');
