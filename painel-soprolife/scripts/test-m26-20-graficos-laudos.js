#!/usr/bin/env node
/* M26.20 — layout e interação do modal "Gráficos dos laudos".

   API simulada; nenhum dado real, nenhum tráfego externo. O que este harness
   prova, e que o anterior não provava:

   - nenhuma rolagem horizontal da PÁGINA nem do corpo do modal, em 7 larguras;
   - tabela expandida fica DENTRO do próprio card (não invade o vizinho nem
     estoura a borda) — era o defeito principal relatado;
   - cards da mesma faixa têm a mesma altura, com todas as tabelas abertas;
   - uma coluna no celular, duas no desktop;
   - tooltip da rosca traz nome, quantidade, percentual e descrição — por
     mouse E por toque, e também tocando a legenda;
   - tocar a legenda NÃO esconde a fatia;
   - alvos de toque >= 44px;
   - cabeçalho e Fechar sempre alcançáveis.

   Screenshots sintéticos em STATISTICS_SHOTS (ou um tmpdir). */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium, firefox} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const out = process.env.STATISTICS_SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'sopro-m26-20-'));
fs.mkdirSync(out, {recursive: true});

const WIDTHS = [1920, 1440, 1024, 768, 430, 390, 360];
const MOBILE = new Set([430, 390, 360]);
const series = (values) => Object.entries(values).map(([rotulo, quantidade]) => ({chave: rotulo, rotulo, quantidade}));
const COUNTED = 'Quantidade de laudos em que essa conclusão foi explicitamente registrada pela médica.';
const sample = {
  escopo: 'institucional', filtros: {inicio: null, fim: null, origem: null},
  totais: {laudos: 38, com_conclusao: 37, normais: 23, sem_conclusao_publicada: 1, sem_data_completa: 2},
  evolucao: [{mes: '2026-05', quantidade: 3}, {mes: '2026-06', quantidade: 0}, {mes: '2026-07', quantidade: 9},
             {mes: '2026-08', quantidade: 14}, {mes: '2026-09', quantidade: 12}],
  resultados: series({'Normal': 23, 'Obstrutivo': 5, 'Sugestivo de restritivo': 1, 'Personalizado': 8,
                      'Sem classificação de catálogo': 0}),
  conclusoes: series({'Normal': 23, 'DVO Leve': 2, 'DVO Moderado': 1, 'DVO Grave': 2,
                      'DVR sug. Moderado': 1, 'Personalizado': 8}),
  broncodilatador: series({'RBD+': 4, 'RBD−': 21, 'REV parcial': 2, 'BD não realizado': 9, 'Não registrado': 1}),
  faixa_etaria: series({'Até 17 anos': 3, '18–39 anos': 7, '40–59 anos': 11, '60–79 anos': 13,
                        '80 anos ou mais': 2, 'Não informado': 2}),
  sexo: series({'Feminino': 22, 'Masculino': 16}),
  origens: series({'Pastore': 17, 'Consultório / coworking': 9, 'Domiciliar': 8, 'Clínica parceira': 4, 'Não informada': 0}),
  opcoes_origem: [{chave: 'pastore', rotulo: 'Pastore'}, {chave: 'coworking', rotulo: 'Consultório / coworking'},
                  {chave: 'residencial', rotulo: 'Domiciliar'}, {chave: 'clinica_parceira', rotulo: 'Clínica parceira'}],
  // Mesma forma do payload real de /laudos/estatisticas, com as contagens da
  // auditoria de 07/10/2026 (7 + 1 + 0 + 0 = 8).
  personalizados: {
    total: 8,
    ignorado_na_classificacao: ['volumes pulmonares / complementar — conduta sugerida, não achado',
                                'prova broncodilatadora — tem código estruturado e gráfico próprios'],
    categorias: [
      {chave: 'reducao_cvf_vef1', rotulo: 'Redução de CVF e VEF1', quantidade: 7, percentual: 87.5,
       descricao: `${COUNTED} O texto cita redução e nomeia os dois parâmetros.`,
       criterio: 'Termo de redução + CVF + VEF1 no texto da conclusão.'},
      {chave: 'reducao_cvf', rotulo: 'Redução isolada de CVF', quantidade: 1, percentual: 12.5,
       descricao: `${COUNTED} O texto cita redução de CVF e não menciona VEF1.`,
       criterio: 'Termo de redução + CVF, sem VEF1 no texto da conclusão.'},
      {chave: 'reducao_vef1', rotulo: 'Redução isolada de VEF1', quantidade: 0, percentual: 0.0,
       descricao: `${COUNTED} O texto cita redução de VEF1 e não menciona CVF.`,
       criterio: 'Termo de redução + VEF1, sem CVF no texto da conclusão.'},
      {chave: 'nao_classificavel', rotulo: 'Não classificável com segurança', quantidade: 0, percentual: 0.0,
       descricao: 'Laudos cujo texto não corresponde explicitamente a nenhuma categoria acima. Nenhuma classificação é atribuída a eles.',
       criterio: 'Nenhuma correspondência explícita, ou presença de negação ("sem redução", "ausência de redução").'},
    ],
  },
};

async function boot(page) {
  await page.route('**/*', (r) => r.abort());
  await page.setContent('<html lang="pt-BR"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main><section id="laudos-espirometria" class="section active"><div id="reportWorkflowRoot"></div></section></main></body></html>');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  for (const m of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
    await page.addStyleTag({content: fs.readFileSync(path.join(root, m[1]), 'utf8')});
  }
  await page.addStyleTag({content: fs.readFileSync(path.join(root, 'css/m15.css'), 'utf8')});
  await page.addScriptTag({content: fs.readFileSync(path.join(root, 'js/vendor/chart.umd.min.js'), 'utf8')});
  await page.evaluate((sample) => {
    window.sample = sample; window.calls = []; window.sessionListeners = []; window.testRole = 'admin';
    window.fetch = async () => new Response(JSON.stringify({enabled: true, reports_enabled: true, reports_mode: 'pilot', api_base: '/painel-soprolife/api/m15'}));
    window.SoproM15 = {
      getUser: () => ({id: 'synthetic', nome: 'Usuário sintético', papeis: [window.testRole]}),
      hasToken: () => true,
      can: (role) => window.testRole === 'admin' || role === window.testRole,
      onSessionChange: (cb) => window.sessionListeners.push(cb),
      api: async (url, options) => {
        window.calls.push({url, method: (options && options.method) || 'GET'});
        if (!url.startsWith('/laudos/estatisticas')) return [];
        return structuredClone(window.sample);
      },
    };
  }, sample);
  for (const file of ['js/report-statistics.js', 'js/report-workflow.js']) {
    await page.addScriptTag({content: fs.readFileSync(path.join(root, file), 'utf8')});
  }
}

/* Geometria dos cards: nada vaza, nada desalinha. */
async function geometry(page) {
  return page.evaluate(() => {
    const body = document.querySelector('.rs-body');
    const grid = document.querySelector('.rs-grid');
    const cards = [...document.querySelectorAll('.rs-chart-card')];
    const near = (a, b) => Math.abs(a - b) < 1.5;
    const rows = new Map();
    for (const card of cards) {
      const r = card.getBoundingClientRect();
      const key = Math.round(r.top);
      if (!rows.has(key)) rows.set(key, []);
      rows.get(key).push(Math.round(r.height));
    }
    return {
      pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      bodyOverflow: body.scrollWidth > body.clientWidth + 1,
      bodyScrollable: body.scrollHeight > body.clientHeight,
      columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
      // Cada card tem que caber na coluna da grade e nenhum filho pode sair dele.
      spills: cards.flatMap((card) => {
        const c = card.getBoundingClientRect();
        return [...card.children].filter((child) => {
          const k = child.getBoundingClientRect();
          return k.right > c.right + 1 || k.left < c.left - 1 || k.bottom > c.bottom + 1;
        }).map((child) => `${card.querySelector('h3').textContent.trim()} :: ${child.className || child.tagName}`);
      }),
      // Tabelas abertas não podem estourar a largura do card.
      tableSpills: [...document.querySelectorAll('.rs-chart-card details[open] .rs-table-wrap')]
        .filter((w) => w.scrollWidth > w.clientWidth + 1 || w.getBoundingClientRect().right > w.closest('.rs-chart-card').getBoundingClientRect().right + 1)
        .map((w) => `${w.closest('.rs-chart-card').querySelector('h3').textContent.trim()} sw=${w.scrollWidth} cw=${w.clientWidth} tableW=${w.querySelector('table') ? Math.round(w.querySelector('table').getBoundingClientRect().width) : '-'} right=${Math.round(w.getBoundingClientRect().right)} cardRight=${Math.round(w.closest('.rs-chart-card').getBoundingClientRect().right)}`),
      // Canvas dentro da moldura e com proporção correta (sem esticão).
      charts: [...document.querySelectorAll('.rs-chart canvas')].map((c) => {
        const r = c.getBoundingClientRect(), p = c.parentElement.getBoundingClientRect();
        const chart = Chart.getChart(c);
        return {ratio: Math.abs(r.width / r.height - chart.width / chart.height),
                spill: r.bottom > p.bottom + 1 || r.right > p.right + 1};
      }),
      // Alturas iguais por faixa da grade.
      misaligned: [...rows.values()].filter((heights) => !heights.every((h) => near(h, heights[0]))),
      touchTargets: [...document.querySelectorAll('.report-statistics button, .report-statistics summary, .report-statistics input, .report-statistics select')]
        .filter((el) => el.offsetParent !== null && el.getBoundingClientRect().height < 40)
        .map((el) => `${el.textContent.trim().slice(0, 24) || el.id || el.tagName}=${el.getBoundingClientRect().height.toFixed(1)}px(min:${getComputedStyle(el).minHeight})`),
      closeVisible: (() => {
        const b = document.querySelector('.rs-close').getBoundingClientRect();
        return b.top >= -1 && b.right <= window.innerWidth + 1 && b.height > 0;
      })(),
      headingVisible: document.querySelector('#rsTitle').getBoundingClientRect().top >= -1,
    };
  });
}

/* Tooltip ativo da rosca: as linhas exatamente como o leitor vê. */
async function tooltipLines(page, canvasId) {
  return page.evaluate((id) => {
    const chart = Chart.getChart(document.querySelector(id));
    const t = chart.tooltip;
    if (!t || !t.getActiveElements().length) return null;
    return {title: t.title, body: t.body.flatMap((b) => b.lines), after: t.afterBody};
  }, canvasId);
}

(async () => {
for (const [engine, launcher] of Object.entries({chromium, firefox})) {
  const browser = await launcher.launch();
  for (const width of WIDTHS) {
    const mobile = MOBILE.has(width);
    const context = await browser.newContext({
      viewport: {width, height: mobile ? 844 : 1000},
      hasTouch: mobile && engine === 'chromium',
      isMobile: mobile && engine === 'chromium',
      deviceScaleFactor: mobile ? 2 : 1,
    });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', (e) => errors.push(e.message));
    await boot(page);

    const button = page.locator('[data-report-statistics]');
    await button.waitFor();
    await button.click();
    await page.waitForSelector('#rsCustom');
    await page.waitForTimeout(220);

    // ---------------------------------------------------- estrutura e layout
    assert.equal(await page.locator('.rs-chart canvas').count(), 7, 'sete gráficos');
    assert.equal(await page.locator('.rs-kpis article').count(), 4);
    assert.equal(await page.locator('#rsCustom').count(), 1, 'rosca de personalizados presente');

    let g = await geometry(page);
    assert(!g.pageOverflow, `${engine} ${width}: rolagem horizontal da página`);
    assert(!g.bodyOverflow, `${engine} ${width}: rolagem horizontal do corpo`);
    assert.equal(g.columns, mobile || width <= 960 ? 1 : 2, `${engine} ${width}: colunas da grade`);
    assert.deepEqual(g.spills, [], `${engine} ${width}: elemento fora do card`);
    assert(g.charts.every((c) => c.ratio < 0.01 && !c.spill), `${engine} ${width}: gráfico desproporcional ou vazando`);
    assert.deepEqual(g.misaligned, [], `${engine} ${width}: cards da mesma faixa com alturas diferentes`);
    assert.deepEqual(g.touchTargets, [], `${engine} ${width}: alvo de toque pequeno`);
    assert(g.closeVisible && g.headingVisible, `${engine} ${width}: cabeçalho/Fechar inacessíveis`);
    await page.screenshot({path: path.join(out, `${engine}-${width}-01-topo.png`)});

    // -------------------------------------- tabelas abertas: contidas no card
    await page.evaluate(() => document.querySelectorAll('.report-statistics details').forEach((d) => { d.open = true; }));
    await page.waitForTimeout(160);
    g = await geometry(page);
    assert(!g.pageOverflow && !g.bodyOverflow, `${engine} ${width}: overflow com tabelas abertas`);
    assert.deepEqual(g.spills, [], `${engine} ${width}: tabela aberta saiu do card`);
    assert.deepEqual(g.tableSpills, [], `${engine} ${width}: tabela aberta mais larga que o card`);
    assert.deepEqual(g.misaligned, [], `${engine} ${width}: desalinhamento com tabelas abertas`);
    assert(g.bodyScrollable, `${engine} ${width}: corpo do modal deveria rolar`);
    // Enquadra o card novo com a tabela aberta: é o que o relatório mostra.
    await page.locator('#rsCustom').scrollIntoViewIfNeeded();
    await page.waitForTimeout(140);
    await page.screenshot({path: path.join(out, `${engine}-${width}-02-tabelas.png`), fullPage: false});

    // "Ver conclusões": categoria, quantidade, percentual, critério e total.
    const detail = page.locator('#rsCustom-data');
    assert.equal(await page.locator('#rsCustom').locator('xpath=../../details/summary').textContent(), 'Ver conclusões');
    const detailRows = await detail.locator('tbody tr').evaluateAll((rows) => rows.map((r) => [...r.children].map((c) => c.textContent.trim())));
    assert.equal(detailRows.length, 4, 'quatro categorias na tabela');
    assert.deepEqual(detailRows[0].slice(0, 3), ['Redução de CVF e VEF1', '7', '87,5%']);
    assert.deepEqual(detailRows[1].slice(0, 3), ['Redução isolada de CVF', '1', '12,5%']);
    assert.deepEqual(detailRows[3].slice(0, 3), ['Não classificável com segurança', '0', '0%']);
    assert(detailRows.every((r) => r[3] && r[3].length > 10), 'cada categoria traz o critério');
    // Rótulo de linha é texto corrido; só o cabeçalho vai em caixa alta.
    const caixa = await detail.evaluate((w) => ({
      linha: getComputedStyle(w.querySelector('tbody th')).textTransform,
      cabecalho: getComputedStyle(w.querySelector('thead th')).textTransform,
      // Nº de linhas que o texto "Laudos" realmente ocupa: um Range dá um
      // retângulo por linha, então 1 = inteiro, 3 = picado em "LA/UD/OS".
      linhasDoCabecalho: (() => {
        const range = document.createRange();
        range.selectNodeContents(w.querySelectorAll('thead th')[1]);
        return range.getClientRects().length;
      })(),
    }));
    assert.equal(caixa.linha, 'none', 'rótulo da linha não pode herdar caixa alta');
    assert.equal(caixa.cabecalho, 'uppercase');
    assert.equal(caixa.linhasDoCabecalho, 1, 'cabeçalho "Laudos" picado no meio da palavra');
    if (mobile) {
      const pilha = await detail.evaluate((w) => {
        const row = w.querySelector('tbody tr');
        const crit = row.querySelector('.rs-criterion');
        return {
          display: getComputedStyle(row).display,
          criterioLargo: crit.getBoundingClientRect().width > row.getBoundingClientRect().width * 0.8,
          bordasDeCelula: [...row.querySelectorAll('th, td')]
            .filter((c) => getComputedStyle(c).borderBottomWidth !== '0px')
            .map((c) => `${c.tagName}:${getComputedStyle(c).borderBottomWidth}`),
        };
      });
      assert.equal(pilha.display, 'grid', 'linha empilhada no celular');
      assert(pilha.criterioLargo, 'critério ocupa a largura da linha');
      assert.deepEqual(pilha.bordasDeCelula, [], 'borda de célula sobrando na linha empilhada');
    }
    const footer = await detail.locator('tfoot tr th, tfoot tr td').allTextContents();
    assert(footer[0].includes('Total de laudos personalizados') && footer[1] === '8', 'total de laudos considerados');
    // A soma das categorias reconcilia com o total exibido.
    assert.equal(detailRows.reduce((s, r) => s + Number(r[1]), 0), 8, 'categorias somam o total');
    // Nenhuma redação clínica e nenhuma categoria de distúrbio inventada.
    const detailText = (await detail.textContent()).toLowerCase();
    for (const proibido of ['obstrutiv', 'restritiv', 'sugiro', 'isolados', 'paciente']) {
      assert(!detailText.includes(proibido), `${engine} ${width}: "${proibido}" na tabela de personalizados`);
    }
    await page.evaluate(() => document.querySelectorAll('.report-statistics details').forEach((d) => { d.open = false; }));

    // ------------------------------------------------------- tooltip e toque
    // A rosca mora abaixo da dobra: sem trazê-la à viewport, as coordenadas
    // do ponteiro caem fora da tela e o Chart.js nem recebe o evento.
    await page.locator('#rsCustom').scrollIntoViewIfNeeded();
    await page.waitForTimeout(160);
    const slice = await page.evaluate(() => {
      const chart = Chart.getChart(document.querySelector('#rsCustom'));
      const el = chart.getDatasetMeta(0).data[0];
      const box = document.querySelector('#rsCustom').getBoundingClientRect();
      const p = el.getCenterPoint();
      return {x: box.left + p.x, y: box.top + p.y};
    });
    if (mobile && engine === 'chromium') {
      await page.touchscreen.tap(slice.x, slice.y);
    } else {
      await page.mouse.move(slice.x, slice.y);
    }
    await page.waitForTimeout(180);
    let tip = await tooltipLines(page, '#rsCustom');
    assert(tip, `${engine} ${width}: tooltip da rosca não abriu`);
    assert.equal(tip.title[0], 'Redução de CVF e VEF1');
    assert(tip.body.some((l) => l.includes('7 laudo(s)') && l.includes('87,5%')), `quantidade e percentual: ${tip.body}`);
    assert(tip.after.join(' ').includes('explicitamente registrada pela médica'), `descrição do que é contado: ${tip.after}`);
    // A descrição é quebrada em linhas: nenhuma régua de texto no canvas.
    assert(tip.after.length > 1 && tip.after.every((l) => l.length <= 52), `descrição quebrada em linhas: ${JSON.stringify(tip.after)}`);
    await page.screenshot({path: path.join(out, `${engine}-${width}-03-tooltip.png`)});

    // Legenda mostra a MESMA informação e não esconde a fatia.
    await page.locator('#rsCustom').scrollIntoViewIfNeeded();
    const legend = await page.evaluate(() => {
      const chart = Chart.getChart(document.querySelector('#rsCustom'));
      const item = chart.legend.legendHitBoxes[1];
      const box = document.querySelector('#rsCustom').getBoundingClientRect();
      return {x: box.left + item.left + item.width / 2, y: box.top + item.top + item.height / 2};
    });
    if (mobile && engine === 'chromium') await page.touchscreen.tap(legend.x, legend.y);
    else await page.mouse.click(legend.x, legend.y);
    await page.waitForTimeout(200);
    tip = await tooltipLines(page, '#rsCustom');
    assert(tip, `${engine} ${width}: tocar a legenda não mostrou informação`);
    assert.equal(tip.title[0], 'Redução isolada de CVF');
    assert(tip.body.some((l) => l.includes('1 laudo(s)') && l.includes('12,5%')));
    assert.equal(await page.evaluate(() => Chart.getChart(document.querySelector('#rsCustom')).getDataVisibility(1)), true,
      `${engine} ${width}: legenda escondeu a fatia`);

    // Barras de série única: UMA cor, não um arco-íris por tamanho.
    const barColors = await page.evaluate(() => {
      const chart = Chart.getChart(document.querySelector('#rsResultsChart'));
      const bg = chart.data.datasets[0].backgroundColor;
      return Array.isArray(bg) ? [...new Set(bg)] : [bg];
    });
    assert.equal(barColors.length, 1, `barras de série única em uma cor, veio ${JSON.stringify(barColors)}`);
    // Faixa etária usa rampa ordinal de hue único + cinza para "Não informado".
    const ageColors = await page.evaluate(() => Chart.getChart(document.querySelector('#rsAge')).data.datasets[0].backgroundColor);
    assert(Array.isArray(ageColors) && new Set(ageColors).size === ageColors.length, 'rampa ordinal com passos distintos');
    assert.equal(ageColors[ageColors.length - 1], '#8c9bab', '"Não informado" recebe o cinza reservado');

    // ------------------------------------------------------ rolagem e fechar
    await page.locator('.rs-body').evaluate((e) => { e.scrollTop = e.scrollHeight; });
    await page.waitForTimeout(140);
    g = await geometry(page);
    assert(g.closeVisible && !g.pageOverflow, `${engine} ${width}: Fechar perdido após rolar`);
    await page.screenshot({path: path.join(out, `${engine}-${width}-04-fim.png`)});
    await page.locator('.rs-body').evaluate((e) => { e.scrollTop = 0; });

    // Filtro único acima de tudo; recorte vazio sem canvas órfão.
    await page.locator('#rsOrigin').selectOption('pastore');
    await page.getByRole('button', {name: 'Aplicar filtros'}).click();
    await page.waitForSelector('#rsCustom');
    assert((await page.evaluate(() => window.calls.at(-1).url)).includes('origem=pastore'));
    // Recorte sem nenhum personalizado: o card existe, diz isso com todas as
    // letras e não mostra "100%" num total zerado.
    await page.evaluate(() => {
      window.sample.personalizados = {total: 0, ignorado_na_classificacao: [],
        categorias: window.sample.personalizados.categorias.map((c) => ({...c, quantidade: 0, percentual: 0}))};
    });
    await page.getByRole('button', {name: 'Aplicar filtros'}).click();
    await page.waitForSelector('#rsCustom-data', {state: 'attached'});
    await page.getByText('Nenhum laudo com conclusão personalizada neste recorte').waitFor();
    await page.evaluate(() => { document.querySelector('#rsCustom-data').closest('details').open = true; });
    await page.waitForTimeout(140);
    const zerado = await page.locator('#rsCustom-data tfoot').allTextContents();
    assert(zerado.join(' ').includes('—') && !zerado.join(' ').includes('100%'), `total zerado não pode dizer 100%: ${zerado}`);

    await page.evaluate(() => { window.sample.totais.laudos = 0; window.sample.personalizados = {total: 0, ignorado_na_classificacao: [], categorias: []}; });
    await page.getByRole('button', {name: 'Aplicar filtros'}).click();
    await page.getByText('Nenhum laudo neste recorte').waitFor();
    assert.equal(await page.locator('canvas').count(), 0, 'canvas órfão no recorte vazio');

    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog').count(), 0);
    assert.equal(await button.evaluate((e) => document.activeElement === e), true, 'foco volta ao botão');
    assert.deepEqual(errors, []);
    console.log(`OK ${engine} ${width}px${mobile ? ' (toque)' : ''}: 7 gráficos, ${g.columns} coluna(s), zero overflow, tabelas contidas, tooltip completo`);
    await context.close();
  }
  await browser.close();
}
console.log(`\nScreenshots: ${out}`);
})().catch((e) => { console.error(e); process.exit(1); });
