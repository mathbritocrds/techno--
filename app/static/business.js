// Additive business controls reuse SIGI's existing cards, tables and dialog.
const originalPayrollView = TAB.folha;
TAB.folha = async function () {
  const html = await originalPayrollView();
  const month = S.payrollMonth || new Date().toISOString().slice(0, 7);
  const [p, annual] = await Promise.all([api('/payroll?month=' + month), api('/payroll/annual-cost?year=' + month.slice(0, 4))]);
  S.businessPayroll = p;
  let output = html.replace('<h1>Folha de pagamento</h1>', `<h1>Folha de pagamento</h1><div class="row"><label>Competência <input type="month" value="${esc(month)}" onchange="S.payrollMonth=this.value;refresh()"></label><button class="chip" onclick="run(closePayroll,this)" ${p.closed ? 'disabled' : ''}>${p.closed ? 'Folha fechada' : 'Fechar folha e gerar contas a pagar'}</button></div>`);
  output = output.replace('INSS de 11% até o teto e encargos de 28% são estimativas: confirme com sua contabilidade.', 'INSS e IRRF usam as tabelas de 2026. FGTS é encargo do empregador. Configure admissão, benefícios e alíquotas em RH.');
  for (const e of p.employees) {
    output = output.replace(`aria-label="Salário base" onchange="run(()=>edE(${e.id}`, `class="salary-compact" ${p.closed ? 'disabled' : ''} aria-label="Salário base" onchange="run(()=>edE(${e.id}`);
    output = output.replace(`aria-label="Benefícios" onchange="run(()=>edE(${e.id}`, `${p.closed ? 'disabled' : ''} aria-label="Benefícios" onchange="run(()=>edE(${e.id}`);
    output = output.replace(`onclick="run(()=>delE(${e.id},this),this)"`, `onclick="run(()=>openHR(${e.id}),this)">RH / benefícios</button> <button class="chip" onclick="run(()=>delE(${e.id},this),this)"`);
  }
  const details = `<section class="card tw" style="margin-top:12px"><h3>Encargos, benefícios e provisões</h3><p class="sub">FGTS acumulado soma as competências fechadas neste sistema; não representa o saldo bancário. Benefícios antigos continuam como concessão em dinheiro. VT e VA são concedidos à parte.</p><table><tr><th>Funcionário</th><th>IRRF</th><th>FGTS / acumulado</th><th>VT / desconto</th><th>VA / desconto</th><th>13º proporcional</th><th>Férias + 1/3</th><th>Provisão mensal 13º / férias</th></tr>${p.employees.map(e => `<tr><td>${esc(e.name)}${!e.profile_complete ? '<small> · admissão pendente</small>' : ''}</td><td>${R(e.irrf)}</td><td>${R(e.fgts)} / ${R(e.fgts_accumulated)}</td><td>${R(e.vt_amount)} / ${R(e.vt_discount)}</td><td>${R(e.va_amount)} / ${R(e.va_discount)}</td><td>${e.thirteenth_months}/12 · ${R(e.thirteenth_proportional)}</td><td>${R(e.vacation_total)}</td><td>${R(e.thirteenth_provision)} / ${R(e.vacation_provision)}</td></tr>`).join('')}</table></section>`;
  const annualView = `<section class="card tw" style="margin-top:12px"><details><summary>Custo anual por colaborador · ${annual.year}</summary><p class="sub">Fechado é histórico congelado; projeção usa as condições atuais e as horas já registradas. A provisão de férias substitui a parcela salarial das férias para evitar contar um 14º salário.</p><table><tr><th>Nome</th><th>Salários</th><th>Benefícios</th><th>FGTS</th><th>Encargos</th><th>Provisões líquidas</th><th>Fechado</th><th>Projetado</th><th>Total anual</th></tr>${annual.employees.map(e => `<tr><td>${esc(e.name)}</td>${['salary','benefits','fgts','employer_charges','provisions','actual_closed','projected_open','total'].map(k => `<td>${R(e[k])}</td>`).join('')}</tr>`).join('')}</table></details></section>`;
  return output + details + annualView;
};
async function closePayroll() {
  if (!confirm('Fechar esta competência? Os valores serão congelados e serão criadas contas a pagar de salário.')) return;
  await api('/payroll/close', 'POST', {month: S.businessPayroll.month});
  toast('Folha fechada e salários enviados ao financeiro.', 'ok');
  await refresh();
}
function businessDialog(title, content) {
  $('#taskDialogContent').innerHTML = `<div class="task-modal-head"><h2 id="taskModalTitle">${esc(title)}</h2><button class="chip" onclick="closeTask()">Fechar</button></div>${content}`;
  $('#taskOverlay').hidden = false;
  $('#taskDialogContent').querySelector('input,select,button')?.focus();
}
const profileFields = [
  ['hired_on','Admissão','date'], ['terminated_on','Desligamento','date'], ['dependents','Dependentes','number'],
  ['alimony','Pensão judicial mensal','number'], ['other_deductions','Outras deduções legais IRRF','number'],
  ['vt_amount','VT mensal concedido','number'], ['vt_rate','Desconto VT (fração, até 0,06)','number'],
  ['va_amount','VA mensal concedido','number'], ['va_discount','Desconto VA mensal','number'],
  ['employer_rate','Encargos patronais além do FGTS (fração)','number'], ['variable_average','Média de adicionais para provisões','number']
];
async function openHR(id) {
  const profile = await api(`/employees/${id}/payroll-profile`), e = S.businessPayroll.employees.find(item => item.id === id);
  S.hrProfile = profile;
  businessDialog('RH · ' + e.name, `<p class="sub">Alíquotas patronais devem refletir o regime da empresa, RAT/FAP e terceiros. VA usa a coparticipação contratada. A média de adicionais é informada pelo RH.</p><form onsubmit="event.preventDefault();run(()=>saveHR(${id}))"><div class="row">${profileFields.map(([key,label,type]) => `<label class="business-field">${label}<input data-profile="${key}" type="${type}" ${type === 'number' ? 'min="0" step="0.01"' : ''} value="${esc(String(profile[key]))}"></label>`).join('')}<label class="business-field">FGTS<select data-profile="fgts_rate"><option value="0.08" ${profile.fgts_rate===.08?'selected':''}>8% · CLT</option><option value="0.02" ${profile.fgts_rate===.02?'selected':''}>2% · Aprendiz</option></select></label><label class="business-field">Direito a férias (dias)<select data-profile="vacation_days">${[30,24,18,12].map(n=>`<option ${profile.vacation_days===n?'selected':''}>${n}</option>`).join('')}</select></label></div><button class="btn" type="submit">Salvar dados de RH</button></form><hr><h3>Pagamento de férias · cálculo bruto</h3><p class="sub">Proporcionais: ${R(e.vacation_proportional)}. Saldos completos por período abaixo. Vencidas após o prazo concessivo incluem dobra.</p><div class="row"><label class="business-field">Período aquisitivo<select id="vacationCycle">${e.vacation_cycles.filter(c=>c.remaining_days>0).map(c=>`<option value="${c.start}">${c.start} · ${c.remaining_days} dias${c.overdue?' · vencidas':''}</option>`).join('')}</select></label><label class="business-field">Início<input id="vacationStart" type="date"></label><label class="business-field">Dias<input id="vacationDays" type="number" min="1" max="30" value="30"></label></div><div class="row"><button class="chip" onclick="run(()=>quoteVacation(${id}),this)">Calcular férias</button><button class="chip" onclick="run(()=>recordVacation(${id}),this)">Registrar gozo de férias</button></div><p id="vacationQuote" class="sub" aria-live="polite"></p>`);
}
async function saveHR(id) {
  const profile = {};
  document.querySelectorAll('[data-profile]').forEach(input => profile[input.dataset.profile] = ['hired_on','terminated_on'].includes(input.dataset.profile) ? input.value : +input.value);
  await api(`/employees/${id}/payroll-profile`, 'PUT', profile);
  closeTask(); toast('Dados de RH salvos.', 'ok'); await refresh();
}
function vacationPayload() { return {acquisition_start: $('#vacationCycle').value, starts_on: $('#vacationStart').value, days: +$('#vacationDays').value}; }
async function quoteVacation(id) {
  const q = await api(`/employees/${id}/vacations/quote`, 'POST', vacationPayload());
  $('#vacationQuote').textContent = `Remuneração ${R(q.base)} + terço ${R(q.constitutional_third)} = ${R(q.gross)} bruto. Pagamento até ${dbr(q.payment_due)}. ${q.note}`;
}
async function recordVacation(id) {
  await api(`/employees/${id}/vacations`, 'POST', vacationPayload());
  closeTask(); toast('Gozo registrado; saldo de férias atualizado.', 'ok'); await refresh();
}
const originalFinanceView = TAB.financeiro;
const financialCategories = {cost:'Custos operacionais',operating:'Despesas operacionais',personnel:'Pessoal avulso',tax:'Impostos / deduções da receita',financial:'Despesas financeiras'};
TAB.financeiro = async function () {
  let html = await originalFinanceView();
  const month = S.financeMonth, start = S.dreStart || month, end = S.dreEnd || month;
  const [dre, payments] = await Promise.all([api(`/finance/dre?start=${start}&end=${end}`), api('/finance/payments?month=' + month)]);
  html = html.replace('<input id="fd"', `<select id="fcategory" aria-label="Classificação DRE">${Object.entries(financialCategories).map(([k,v])=>`<option value="${k}" ${k==='operating'?'selected':''}>${v}</option>`).join('')}</select><input id="fcompetence" type="month" aria-label="Competência contábil" value="${month}"><input id="fd"`);
  return html + `<section class="card tw" style="margin-top:12px"><h3>DRE integrada · regime de competência</h3><div class="row"><label>De <input type="month" value="${start}" onchange="S.dreStart=this.value;refresh()"></label><label>Até <input type="month" value="${end}" onchange="S.dreEnd=this.value;refresh()"></label></div><table>${[['Receita bruta','revenue'],['Impostos / deduções','tax'],['Custos operacionais','cost'],['Resultado bruto','gross_profit'],['Despesas operacionais','operating'],['Despesas com pessoal + encargos + provisões','personnel'],['Despesas financeiras','financial'],['Resultado líquido','net_profit']].map(([label,key])=>`<tr><th>${label}</th><td>${R(dre[key])}</td></tr>`).join('')}<tr><th>Margem líquida</th><td>${dre.net_margin_pct}%</td></tr></table><p class="sub">${esc(dre.note)} Folhas incluídas: ${dre.closed_payroll_months.map(esc).join(', ') || 'nenhuma'}.</p></section><section class="card tw" style="margin-top:12px"><h3>Histórico de pagamentos e recebimentos · ${month}</h3><p class="sub">Pagamento preservado com autor, data e valor. Comprovantes PDF, PNG ou JPG, até 6 MB.</p>${payments.length?`<table><tr><th>Descrição</th><th>Valor</th><th>Pago em / autor</th><th>Comprovantes</th></tr>${payments.map(p=>`<tr><td>${esc(p.description)}<small> · ${p.kind==='pagar'?'Pagamento':'Recebimento'}</small></td><td>${R(p.amount)}</td><td>${new Date(p.paid_at).toLocaleString('pt-BR')}<br>${esc(p.actor)}</td><td>${p.attachments.map(a=>`<button class="chip" onclick="run(()=>downloadPaymentFile(${p.id},${a.id}),this)">${esc(a.name)}</button>`).join('')}<label class="chip">Anexar<input type="file" accept="application/pdf,image/png,image/jpeg" aria-label="Anexar comprovante ${esc(p.description)}" onchange="run(()=>uploadPaymentFile(${p.id},this))"></label></td></tr>`).join('')}</table>`:'<p class="sub">Nenhum pagamento registrado neste mês.</p>'}</section>`;
};
addTx = async function () {
  const kind = $('#fk').value;
  await api('/finance','POST',{kind,description:$('#fd').value.trim(),amount:+$('#fv').value,due:$('#ft').value,department_id:kind==='pagar'?(+$('#fdep').value||null):null,category:kind==='receber'?'revenue':$('#fcategory').value,competence:$('#fcompetence').value});
  await refresh();
};
async function uploadPaymentFile(id, input) {
  const file = input.files?.[0]; if (!file) return;
  if (file.size > 6*1024*1024) throw new Error('Comprovante deve ter até 6 MB.');
  const content = await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(reader.error);reader.readAsDataURL(file)});
  await api(`/finance/payments/${id}/attachments`,'POST',{name:file.name,mime:file.type,content_base64:content});
  toast('Comprovante anexado.', 'ok'); await refresh();
}
async function downloadPaymentFile(payment, file) {
  const response = await fetch(`/finance/payments/${payment}/attachments/${file}`,{headers:{Authorization:'Bearer '+TOKEN}});
  if (!response.ok) throw new Error('Não foi possível baixar o comprovante.');
  const link=document.createElement('a'),url=URL.createObjectURL(await response.blob());link.href=url;link.download='comprovante';link.click();URL.revokeObjectURL(url);
}
const originalSpacesView = TAB.espacos;
const duties = {preparation:'Preparação',service:'Atendimento',maintenance:'Manutenção'};
TAB.espacos = async function () {
  let html = await originalSpacesView();
  const [spaces, employees] = await Promise.all([api('/spaces'),ROLE==='admin'?api('/employees'):Promise.resolve([])]);
  S.staffEmployees = employees.filter(e=>e.active);
  html = html.replace('Reservas não podem se sobrepor no mesmo espaço.', 'Eventos podem compartilhar espaços conforme capacidade e compatibilidade. A escala considera preparação e manutenção.');
  for (const s of spaces) {
    html=html.replace(`<button class="chip" onclick="run(()=>edS(${s.id}),this)">`, `<label>Capacidade<input data-capacity type="number" min="1" value="${s.capacity}"></label><label>Compartilhamento<select data-shared><option value="0">Exclusivo</option><option value="1" ${s.allow_shared?'selected':''}>Permitir eventos compatíveis</option></select></label><button class="chip" onclick="run(()=>openSpaceStaff(${s.id}),this)">Equipe automática</button><button class="chip" onclick="run(()=>edS(${s.id}),this)">`);
  }
  html=html.replace('<input id="se"', '<input id="spaceCapacity" type="number" min="1" value="1" aria-label="Capacidade"><select id="spaceShared" aria-label="Compartilhamento"><option value="0">Exclusivo</option><option value="1">Compartilhado</option></select><input id="se"');
  html=html.replace('<input id="bookingTitle"', '<input id="bookingAttendees" type="number" min="1" value="1" aria-label="Participantes"><select id="bookingExclusive" aria-label="Exclusividade"><option value="1">Evento exclusivo</option><option value="0">Pode compartilhar</option></select><input id="bookingCompatibility" placeholder="Grupo de compatibilidade"><input id="bookingTitle"');
  const bookings = S.businessBookings || [];
  return html + `<section class="card tw" style="margin-top:12px"><details><summary>Editar eventos e consultar escalas</summary>${bookings.length?`<table><tr><th>Evento</th><th>Espaço</th><th>Status</th><th>Participantes</th><th>Ações</th></tr>${bookings.map(b=>`<tr><td>${esc(b.title)}</td><td>${esc(b.space_name)}</td><td>${esc({confirmed:'Confirmado',tentative:'Provisório',cancelled:'Cancelado',completed:'Concluído'}[b.status])}</td><td>${b.attendees}</td><td><button class="chip" onclick="run(()=>showEventStaff(${b.id}),this)">Escala</button>${b.can_edit?` <button class="chip" onclick="editEvent(${b.id})">Editar</button>`:''}</td></tr>`).join('')}</table>`:'<p class="sub">Sem eventos neste mês.</p>'}</details></section>`;
};
addS = async function () {
  await api('/spaces','POST',{name:$('#sn').value.trim(),kind:$('#sk').value,admin_email:$('#se').value.trim(),capacity:+$('#spaceCapacity').value,allow_shared:$('#spaceShared').value==='1'});await refresh();
};
edS = async function (id) {
  const c=$(`[data-k="s${id}"]`);
  await api('/spaces/'+id,'PUT',{name:c.querySelector('[data-sn]').value.trim(),kind:c.querySelector('[data-sk]').value,admin_email:c.querySelector('[data-se]').value.trim(),capacity:+c.querySelector('[data-capacity]').value,allow_shared:c.querySelector('[data-shared]').value==='1'});toast('Espaço salvo.','ok');await refresh();
};
addBooking = async function () {
  if (!$('#bookingStart').value || !$('#bookingEnd').value) throw new Error('Preencha início e fim.');
  await api('/space-bookings','POST',{space_id:+$('#bookingSpace').value,title:$('#bookingTitle').value.trim(),starts_at:new Date($('#bookingStart').value).toISOString(),ends_at:new Date($('#bookingEnd').value).toISOString(),attendees:+$('#bookingAttendees').value,exclusive:$('#bookingExclusive').value==='1',compatibility:$('#bookingCompatibility').value.trim(),department_id:ROLE==='admin'?(+$('#bookingDepartment').value||null):null});toast('Evento agendado e escala gerada.','ok');await refresh();
};
function localInputTime(iso) {const d=new Date(iso);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function editEvent(id) {
  const b=S.businessBookings.find(b=>b.id===id);
  businessDialog('Editar evento', `<label>Título<input id="eventTitle" value="${esc(b.title)}"></label><label>Detalhes<textarea id="eventDetails">${esc(b.details)}</textarea></label><div class="row"><label>Início<input id="eventStart" type="datetime-local" value="${localInputTime(b.starts_at)}"></label><label>Fim<input id="eventEnd" type="datetime-local" value="${localInputTime(b.ends_at)}"></label><label>Participantes<input id="eventAttendees" type="number" min="1" value="${b.attendees}"></label><label>Status<select id="eventStatus">${Object.entries({confirmed:'Confirmado',tentative:'Provisório',completed:'Concluído',cancelled:'Cancelado'}).map(([k,v])=>`<option value="${k}" ${b.status===k?'selected':''}>${v}</option>`).join('')}</select></label><label>Exclusividade<select id="eventExclusive"><option value="1" ${b.exclusive?'selected':''}>Exclusivo</option><option value="0" ${!b.exclusive?'selected':''}>Compartilhável</option></select></label><label>Compatibilidade<input id="eventCompatibility" value="${esc(b.compatibility)}"></label></div><button class="btn" onclick="run(()=>saveEvent(${id}),this)">Salvar e atualizar escala</button>`);
}
async function saveEvent(id) {
  const old=S.businessBookings.find(b=>b.id===id);
  const payload={space_id:old.space_id,department_id:old.department_id,title:$('#eventTitle').value.trim(),details:$('#eventDetails').value,starts_at:new Date($('#eventStart').value).toISOString(),ends_at:new Date($('#eventEnd').value).toISOString(),attendees:+$('#eventAttendees').value,status:$('#eventStatus').value,exclusive:$('#eventExclusive').value==='1',compatibility:$('#eventCompatibility').value.trim()};
  await api(`/space-bookings/${id}`,'PATCH',payload);closeTask();toast('Evento e escala atualizados.','ok');await refresh();
}
async function openSpaceStaff(id) {
  const rules=await api(`/spaces/${id}/staff-rules`);
  businessDialog('Equipe automática do espaço', `<p class="sub">Cada vínculo gera uma escala quando um evento é criado ou editado. Minutos antes/depois reservam tempo de preparação e manutenção.</p>${rules.map(r=>`<p>${esc(r.name)} · ${duties[r.duty]} · ${r.before_minutes} min antes / ${r.after_minutes} min depois <button class="chip" onclick="run(()=>removeStaffRule(${id},${r.id}),this)">Remover</button></p>`).join('')||'<p class="sub">Nenhum funcionário vinculado.</p>'}<div class="row"><label>Funcionário<select id="staffEmployee">${S.staffEmployees.map(e=>`<option value="${e.id}">${esc(e.name)}</option>`).join('')}</select></label><label>Função<select id="staffDuty">${Object.entries(duties).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label><label>Minutos antes<input id="staffBefore" type="number" min="0" value="0"></label><label>Minutos depois<input id="staffAfter" type="number" min="0" value="0"></label></div><button class="btn" onclick="run(()=>saveStaffRule(${id}),this)" ${S.staffEmployees.length?'':'disabled'}>Vincular funcionário</button>`);
}
async function saveStaffRule(id) {await api(`/spaces/${id}/staff-rules`,'POST',{employee_id:+$('#staffEmployee').value,duty:$('#staffDuty').value,before_minutes:+$('#staffBefore').value,after_minutes:+$('#staffAfter').value});await openSpaceStaff(id);}
async function removeStaffRule(space,id) {await api(`/spaces/${space}/staff-rules/${id}`,'DELETE');await openSpaceStaff(space);}
async function showEventStaff(id) {const staff=await api(`/space-bookings/${id}/staff`);businessDialog('Escala do evento',staff.map(s=>`<p><b>${esc(s.name)}</b> · ${duties[s.duty]}<br>${new Date(s.starts_at).toLocaleString('pt-BR')} — ${new Date(s.ends_at).toLocaleString('pt-BR')}</p>`).join('')||'<p class="sub">Sem funcionários vinculados ao espaço.</p>');}
const originalEmployeeReservations = employeeReservations;
employeeReservations = async function () {const html=await originalEmployeeReservations(),shifts=await api('/employee/event-shifts');return html+`<section class="card" style="margin-top:12px"><h3>Minha escala de eventos</h3>${shifts.map(s=>`<p><b>${esc(s.title)}</b> · ${esc(s.space_name)} · ${duties[s.duty]}<br>${new Date(s.starts_at).toLocaleString('pt-BR')} — ${new Date(s.ends_at).toLocaleString('pt-BR')}</p>`).join('')||'<p class="sub">Você não tem escalas de eventos.</p>'}</section>`;};
