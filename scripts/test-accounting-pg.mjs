// PostgreSQL engine checks: triggers, RLS and repeatable migration, no cloud credentials.
import {PGlite} from '@electric-sql/pglite';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const pg=new PGlite();
await pg.exec('CREATE ROLE anon; CREATE ROLE authenticated;');
const schema=readFileSync('supabase/schema.sql','utf8');
await pg.exec(schema);await pg.exec(schema);
async function post(source,department){
 const {rows}=await pg.query(`INSERT INTO ledger_journals(booked_on,description,department_id,source_key,payload_hash,created_at,created_by) VALUES('2026-10-10','Capital',$1,$2,'hash','2026-10-10','test') RETURNING id`,[department,source]);
 const id=rows[0].id;
 await pg.query(`INSERT INTO ledger_lines(journal_id,account_code,debit_cents,credit_cents) VALUES($1,'1.1.01',10000,0),($1,'3.1.01',0,10000)`,[id]);
 await pg.query(`UPDATE ledger_journals SET status='posted' WHERE id=$1`,[id]);return id;
}
const own=await post('own',1),other=await post('other',2);
await pg.exec("INSERT INTO departments(id,name) VALUES(1,'A'),(2,'B'); INSERT INTO employees(id,name,department_id,pin_hash,pin_salt) VALUES(1,'A',1,'test','test'),(2,'B',2,'test','test'); INSERT INTO time_entries(id,employee_id,kind,at,accepted) VALUES(1,1,'entrada','2026-10-10T12:00:00+00:00',1),(2,2,'entrada','2026-10-10T12:00:00+00:00',1);");
await assert.rejects(pg.query('UPDATE ledger_lines SET debit_cents=5 WHERE journal_id=$1',[own]),/immutable/);
await assert.rejects(pg.query('DELETE FROM ledger_journals WHERE id=$1',[own]),/immutable/);
const bad=await pg.query(`INSERT INTO ledger_journals(booked_on,description,source_key,payload_hash,created_at,created_by) VALUES('2026-10-10','Inválido','bad','hash','now','test') RETURNING id`);
await pg.query(`INSERT INTO ledger_lines(journal_id,account_code,debit_cents,credit_cents) VALUES($1,'1.1.01',1,0),($1,'3.1.01',0,2)`,[bad.rows[0].id]);
await assert.rejects(pg.query(`UPDATE ledger_journals SET status='posted' WHERE id=$1`,[bad.rows[0].id]),/Unbalanced/);
async function scope(role,department,work){
 await pg.exec('BEGIN;SET LOCAL ROLE sigi_accounting_api;');
 await pg.query(`SELECT set_config('sigi.role',$1,true),set_config('sigi.department_id',$2,true)`,[role,String(department||'')]);
 try{await work();await pg.exec('COMMIT')}catch(e){await pg.exec('ROLLBACK');throw e}
}
await scope('manager',1,async()=>{
 assert.deepEqual((await pg.query('SELECT id FROM ledger_journals ORDER BY id')).rows,[{id:own}]);
 assert.equal((await pg.query('SELECT * FROM ledger_lines')).rows.length,2);
 assert.deepEqual((await pg.query('SELECT id FROM effective_time_entries')).rows,[{id:1}]);
});
await assert.rejects(scope('manager',1,()=>post('forbidden',2)),/row-level security/);
await assert.rejects(scope('manager',1,()=>pg.query("INSERT INTO time_entry_adjustments(entry_id,corrected_at,reason,created_at,created_by,department_id) VALUES(2,'2026-10-10T13:00:00+00:00','Invalid scope','now','test',1)")),/row-level security/);
await scope('operator',1,async()=>{
 assert.equal((await pg.query('SELECT * FROM ledger_journals')).rows.length,0);
 assert.equal((await pg.query('SELECT * FROM ledger_accounts')).rows.length,0);
});
await pg.exec('BEGIN;SET LOCAL ROLE anon;');
await assert.rejects(pg.query('SELECT * FROM ledger_journals'),/permission denied/);await pg.exec('ROLLBACK');
await pg.exec('BEGIN;SET LOCAL ROLE authenticated;');
await assert.rejects(pg.query('SELECT * FROM effective_time_entries'),/permission denied/);await pg.exec('ROLLBACK');
assert.equal(BigInt((await pg.query(`SELECT SUM(l.debit_cents-l.credit_cents) AS difference FROM ledger_lines l JOIN ledger_journals j ON j.id=l.journal_id WHERE j.status='posted'`)).rows[0].difference),0n);
await pg.close();console.log('PostgreSQL: migration repeated; immutable and balanced journals; manager scope; operator, anon and authenticated denied.');
