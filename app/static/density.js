// Additive DOM enhancements: no API interception, globals, reparenting or business-rule changes.
(() => {
  'use strict';
  const tableStates = new WeakMap();
  const savedViews = new Map();
  let sequence = 0;
  let scheduled = false;
  const normalize = value => value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase('pt-BR').replace(/\s+/g, ' ').trim();
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const rootChild = (main, node) => {
    while (node && node.parentElement !== main) node = node.parentElement;
    return node;
  };

  function dailySummary(main) {
    if (!main.querySelector('.overview-kpis')) return;
    const legend = [...main.querySelectorAll('.task-stage-legend b')];
    const operations = main.querySelector('.overview-operations');
    const alerts = [...(operations?.querySelectorAll('h3') || [])].find(h => h.textContent.startsWith('Alertas operacionais'));
    const present = main.querySelector('.overview-kpis .card:last-child .d')?.textContent.match(/^\s*(\d+)/)?.[1];
    if (legend.length < 2 || present === undefined || !alerts) return;
    const values = [['A fazer', legend[0].textContent], ['Em andamento', legend[1].textContent], ['Trabalhando agora', present], ['Alertas', alerts.querySelector('.cn')?.textContent || '0']];
    let strip = main.querySelector('.density-day-strip');
    if (!strip) {
      strip = element('section', 'density-day-strip');
      strip.setAttribute('aria-label', 'Resumo operacional atual');
      strip.append(element('div', 'density-day-heading', 'Operação agora'));
      const cards = element('div', 'density-day-cards');
      values.forEach(([label, value]) => {
        const card = element('div', 'card');
        card.append(element('span', '', label), element('strong', '', value));
        cards.append(card);
      });
      strip.append(cards);
      main.querySelector('.section-heading').after(strip);
    } else {
      strip.querySelectorAll('strong').forEach((node, i) => { if (node.textContent !== values[i][1]) node.textContent = values[i][1]; });
    }
  }

  function logicalSections(main) {
    if (main.querySelector('.density-view-nav')) return;
    let module, definitions, assignments = [];
    const children = [...main.children];
    if (main.querySelector('.finance-toolbar')) {
      module = 'finance';
      definitions = [['operation', 'Contas e lançamentos'], ['results', 'DRE e fluxo'], ['history', 'Histórico']];
      for (const node of children) {
        if (node.matches('[data-finance-accounts]') || node.querySelector('#fk')) assignments.push([node, 'operation']);
        else if (node.matches('[data-finance-history]')) assignments.push([node, 'history']);
        else if (node.matches('[data-finance-flow]') || node.querySelector('.dre-controls,.cost-chart') || node.matches('section.card') && node.querySelector('.leg')) assignments.push([node, 'results']);
      }
    } else if (main.querySelector('.payroll-toolbar')) {
      module = 'hr';
      definitions = [['team', 'Equipe e pagamentos'], ['charts', 'Custos e gráficos']];
      assignments = children.filter(n => n.matches('.employee-filter,.employee-grid,#hrNoResults,.hr-overview')).map(n => [n, n.matches('.hr-overview') ? 'charts' : 'team']);
    } else if (main.querySelector('#rr') && main.querySelector('#cn')) {
      module = 'time';
      definitions = [['work', 'Jornada e registros'], ['settings', 'Local e empresa']];
      const settings = rootChild(main, main.querySelector('#rr'));
      assignments = children.filter(n => n === settings || n.matches('.card')).map(n => [n, n === settings ? 'settings' : 'work']);
    } else if (main.querySelector('#accountUsername')) {
      module = 'security';
      definitions = [['accounts', 'Conta e equipe'], ['settings', 'Permissões e parâmetros'], ['reports', 'Produtividade e auditoria']];
      assignments = children.filter(n => n.matches('section.card')).map(n => {
        const title = normalize(n.querySelector('h3')?.textContent || '');
        return [n, /matriz|parametros/.test(title) ? 'settings' : /produtividade|auditoria/.test(title) ? 'reports' : 'accounts'];
      });
    }
    if (!module || new Set(assignments.map(a => a[1])).size < 2) return;
    definitions = definitions.filter(([key]) => assignments.some(a => a[1] === key));
    definitions.push(['all', 'Tudo']);
    const nav = element('nav', 'density-view-nav');
    nav.dataset.densityModule = module;
    nav.setAttribute('aria-label', 'Seções da página');
    const activate = (key, focus = false) => {
      savedViews.set(module, key);
      assignments.forEach(([node, group]) => node.toggleAttribute('data-density-inactive', key !== 'all' && key !== group));
      nav.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.densityView === key)));
      if (focus) nav.querySelector(`[data-density-view="${key}"]`).focus();
    };
    definitions.forEach(([key, title]) => {
      const button = element('button', 'chip', title);
      button.type = 'button';
      button.dataset.densityView = key;
      button.addEventListener('click', () => activate(key));
      nav.append(button);
    });
    nav.addEventListener('keydown', event => {
      const buttons = [...nav.querySelectorAll('button')];
      const index = buttons.indexOf(document.activeElement);
      if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      activate(buttons[next].dataset.densityView, true);
    });
    assignments.forEach(([node, group]) => node.dataset.densityPanel = group);
    assignments[0][0].before(nav);
    const selected = savedViews.get(module);
    activate(definitions.some(([key]) => key === selected) ? selected : definitions[0][0]);
  }

  function gridFields(container) {
    if (container.classList.contains('density-flat-grid') || container.closest('.login,#accountingRoot')) return;
    const children = [...container.children];
    const pairs = children.map((label, index) => ({label, control: children[index + 1]})).filter(({label, control}) => label.tagName === 'LABEL' && label.htmlFor && control?.id === label.htmlFor && control.matches('input:not([type=checkbox]):not([type=file]),select'));
    if (pairs.length < 2 || children.some(node => node.matches('table,.grid,.row:has(table)'))) return;
    container.classList.add('density-flat-grid');
    let row = 1, column = 1;
    const used = new Set();
    for (const child of children) {
      if (used.has(child)) continue;
      const pair = pairs.find(pair => pair.label === child);
      if (pair) {
        pair.label.classList.add('density-field-label');
        pair.control.classList.add('density-field-control');
        for (const node of [pair.label, pair.control]) {
          node.style.setProperty('--density-column', column);
          node.style.setProperty('--density-label-row', row);
          node.style.setProperty('--density-control-row', row + 1);
        }
        used.add(pair.control);
        if (column === 2) { row += 2; column = 1; } else column = 2;
      } else {
        if (column === 2) { row += 2; column = 1; }
        child.style.gridRow = row++;
      }
    }
  }

  function rowValues(row) {
    return [...row.cells].map(cell => {
      const copy = cell.cloneNode(true);
      copy.querySelectorAll('select,input,textarea,button').forEach(node => node.remove());
      const fields = [...cell.querySelectorAll('input:not([type=checkbox]):not([type=file]),select,textarea')].map(field => field.tagName === 'SELECT' ? [...field.selectedOptions].map(o => o.textContent).join(' ') : field.value);
      return normalize([copy.textContent, ...fields].join(' '));
    });
  }

  function filterable(table) {
    // Business filters and statement totals stay under their existing controls.
    return !table.closest('#accountingRoot,[data-finance-accounts],[data-finance-flow]') && !table.closest('.card')?.querySelector('.dre-controls') && !table.closest('#main')?.querySelector('.board-tools');
  }

  function searchTable(table) {
    if (!filterable(table)) return;
    const rows = [...table.rows].filter(row => row.querySelector('td') && !row.closest('tfoot') && !(row.cells.length === 1 && row.cells[0].colSpan > 1));
    let state = tableStates.get(table);
    if (!rows.length) { if (state) { state.rows = rows; state.apply(); } return; }
    if (!state) {
      const heading = table.closest('.card')?.querySelector('h3,h2')?.textContent.trim() || table.rows[0]?.textContent.trim().slice(0,60) || 'Registros';
      const id = ++sequence;
      const tools = element('div', 'density-table-tools');
      const label = element('label', 'density-search', 'Busca nesta página');
      const input = element('input'); input.type = 'search'; input.id = `density-search-${id}`; input.placeholder = 'Digite para filtrar'; input.autocomplete = 'off'; label.htmlFor = input.id; label.append(input);
      input.setAttribute('aria-label', 'Busca nesta página: ' + heading);
      const fieldLabel = element('label', 'density-column', 'Campo');
      const select = element('select'); select.setAttribute('aria-label', 'Campo da busca: ' + heading);
      select.append(new Option('Todos os campos', ''));
      [...table.rows[0].cells].forEach((cell, index) => { const title = cell.textContent.trim(); if (title) select.append(new Option(title, String(index))); });
      fieldLabel.append(select);
      const clear = element('button', 'chip', 'Limpar busca'); clear.type = 'button';
      const count = element('span', 'density-table-count'); count.id = `density-count-${id}`; count.setAttribute('role', 'status'); count.setAttribute('aria-live', 'polite'); count.setAttribute('aria-atomic', 'true');
      input.setAttribute('aria-describedby', count.id);
      const note = element('p', 'density-filter-note', 'Filtra somente os registros carregados nesta página. Totais e exportações permanecem completos.'); note.hidden = true;
      const empty = element('p', 'density-empty', 'Nenhum registro corresponde à busca.'); empty.hidden = true;
      const pager = element('div', 'density-pager');
      const pageLabel = element('label', '', 'Linhas');
      const pageSize = element('select'); pageSize.setAttribute('aria-label', 'Linhas por página: ' + heading);
      for (const value of [20,50,100,0]) pageSize.append(new Option(value ? String(value) : 'Todas', String(value)));
      pageLabel.append(pageSize);
      const previous = element('button', 'chip', 'Anterior'); previous.type = 'button';
      const next = element('button', 'chip', 'Próxima'); next.type = 'button';
      const pageStatus = element('span'); pageStatus.setAttribute('role','status'); pageStatus.setAttribute('aria-live','polite');
      pager.append(pageLabel, previous, pageStatus, next);
      tools.append(label, fieldLabel, clear, count);
      table.before(tools, note); table.after(empty, pager);
      state = {rows: [], input, select, tools, note, empty, clear, count, timer: null, page: 1};
      state.apply = () => {
        const words = normalize(input.value).split(' ').filter(Boolean);
        const matching = [];
        for (const row of state.rows) {
          const values = rowValues(row);
          const text = select.value === '' ? values.join(' ') : values[Number(select.value)] || '';
          const matches = words.every(word => text.includes(word));
          row.toggleAttribute('data-density-filtered', !matches);
          if (matches && !row.hidden) matching.push(row);
        }
        const visible = matching.length;
        const size = Number(pageSize.value) || Math.max(1,visible);
        const pages = Math.max(1,Math.ceil(visible/size));
        state.page = Math.min(state.page,pages);
        const start = (state.page-1)*size;
        const displayed = new Set(matching.slice(start,start+size));
        state.rows.forEach(row=>row.toggleAttribute('data-density-page-inactive',!displayed.has(row)));
        pager.hidden = state.rows.length <= 20;
        previous.disabled = state.page <= 1;
        next.disabled = state.page >= pages;
        const pageDescription = `Página ${state.page} de ${pages} · ${visible ? start+1 : 0} a ${Math.min(start+size,visible)}`;
        if (pageStatus.textContent !== pageDescription) pageStatus.textContent = pageDescription;
        const total = state.rows.filter(row => !row.hidden).length;
        const description = `${visible} de ${total} registros nesta página`;
        if (count.textContent !== description) count.textContent = description;
        note.hidden = !words.length;
        empty.hidden = visible > 0 || total === 0;
        clear.disabled = !input.value && select.value === '';
      };
      input.addEventListener('input', () => { clearTimeout(state.timer); state.page=1; state.timer = setTimeout(state.apply, 100); });
      select.addEventListener('change', () => { state.page=1; state.apply(); });
      pageSize.addEventListener('change', () => { state.page=1; state.apply(); });
      previous.addEventListener('click', () => { state.page--; state.apply(); });
      next.addEventListener('click', () => { state.page++; state.apply(); });
      clear.addEventListener('click', () => { clearTimeout(state.timer); input.value = ''; select.value = ''; state.page=1; state.apply(); input.focus(); });
      input.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); clear.click(); } });
      table.addEventListener('change', state.apply);
      tableStates.set(table, state);
    }
    state.rows = rows;
    state.apply();
  }

  function enhance() {
    scheduled = false;
    observer.disconnect();
    try {
      const main = document.querySelector('#main');
      if (main) {
        dailySummary(main);
        logicalSections(main);
        main.querySelectorAll('table').forEach(searchTable);
        main.querySelectorAll('.card,form').forEach(gridFields);
      } else if (!document.querySelector('.app')) savedViews.clear();
      document.querySelectorAll('#taskDialogContent form,#taskDialogContent .card').forEach(gridFields);
    } finally {
      observer.observe(document.body, {childList: true, subtree: true});
    }
  }
  const observer = new MutationObserver(records => {
    const structural = records.some(record => !record.target.closest?.('.density-table-tools,.density-filter-note,.density-empty,.density-pager') && [...record.addedNodes, ...record.removedNodes].some(node => node.nodeType === Node.ELEMENT_NODE));
    if (structural && !scheduled) { scheduled = true; requestAnimationFrame(enhance); }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', enhance, {once: true});
  else enhance();
})();
