#!/usr/bin/env node
// M26.21 — teste visual/responsivo do módulo Custos & Investimentos.
// Carrega o resumo REAL gerado (nenhum número sintético misturado), renderiza
// as 6 abas em 6 larguras e confere: ausência de overflow, ausência de erro de
// JS, e que os números reconciliados aparecem de fato no DOM.
// Nenhuma conexão externa: todas as requisições são abortadas.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium, firefox } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const out = process.env.VISUAL_SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'sopro-m26-21-'));
fs.mkdirSync(out, { recursive: true });

const summary = JSON.parse(fs.readFileSync(
  path.join(root, 'data', 'custos-investimentos-summary.local.json'), 'utf8'));

const ABAS = ['resumo', 'recorrentes', 'equipamentos', 'parcelamentos', 'socios', 'pendencias'];

(async () => {
  let checks = 0;
  for (const [engine, launcher] of Object.entries({ chromium, firefox })) {
    const browser = await launcher.launch({ headless: true });
    for (const width of [1440, 1280, 1024, 768, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 1200 } });
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.route('**/*', (route) => route.abort());

      let html = fs.readFileSync(path.join(root, 'index.html'), 'utf8')
        .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '')
        .replace(/<link\b[^>]*>/g, '');
      html = html.replace('./assets/soprolife-logo.png',
        'data:image/png;base64,' + fs.readFileSync(path.join(root, 'assets/soprolife-logo.png')).toString('base64'));
      await page.setContent(html);
      const original = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
      for (const m of original.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
        await page.addStyleTag({ content: fs.readFileSync(path.join(root, m[1]), 'utf8') });
      }
      await page.addScriptTag({ content: fs.readFileSync(path.join(root, 'js/vendor/chart.umd.min.js'), 'utf8') });
      await page.addScriptTag({ content: fs.readFileSync(path.join(root, 'js/app.js'), 'utf8') });

      await page.evaluate((ci) => {
        state.custosInvestimentos = ci;
        setPremiumChartDefaults();
        Chart.defaults.animation = false;
        renderCustosInvestimentos();
        document.querySelectorAll('.section').forEach((e) => e.classList.toggle('active', e.id === 'custos-investimentos'));
      }, summary);

      let texto = '';
      for (const aba of ABAS) {
        await page.evaluate((aba) => {
          document.querySelectorAll('.ci-tab').forEach((b) => b.classList.toggle('active', b.dataset.ciTab === aba));
          document.querySelectorAll('.ci-pane').forEach((p) => p.toggleAttribute('hidden', p.id !== `ci-${aba}`));
          resizeCharts();
        }, aba);
        await page.waitForTimeout(250);

        const r = await page.evaluate((aba) => {
          const pane = document.querySelector(`#ci-${aba}`);
          return {
            overflow: document.documentElement.scrollWidth > innerWidth + 1,
            vazio: pane.textContent.trim().length === 0,
            // Tabela que estoura a própria caixa indica layout quebrado.
            estouro: [...pane.querySelectorAll('table')].some((t) => {
              const w = t.closest('.table-wrap');
              return w ? t.scrollWidth > w.scrollWidth + 1 && w.scrollWidth >= w.clientWidth : false;
            }),
            canvasQuebrado: [...pane.querySelectorAll('canvas')]
              .filter((c) => c.getClientRects().length)
              .some((c) => { const b = c.getBoundingClientRect(); return b.width <= 0 || b.height <= 0; }),
          };
        }, aba);

        assert.equal(r.overflow, false, `${engine} ${width} ${aba}: overflow horizontal da página`);
        assert.equal(r.vazio, false, `${engine} ${width} ${aba}: aba renderizou vazia`);
        assert.equal(r.canvasQuebrado, false, `${engine} ${width} ${aba}: canvas com tamanho zero`);
        checks += 3;
        // innerText só enxerga o que está visível: o texto é acumulado aba a
        // aba enquanto cada uma está aberta.
        texto += await page.evaluate((aba) => document.querySelector(`#ci-${aba}`).innerText, aba);
        texto += await page.evaluate(() => document.querySelector('#ciStats').innerText);
        await page.screenshot({ path: path.join(out, `m26-21-${engine}-${width}-${aba}.png`), fullPage: true });
      }

      // --- os números reconciliados precisam aparecer de fato ---
      // Intl.NumberFormat pt-BR usa ESPAÇO NÃO SEPARÁVEL (U+00A0) depois de
      // "R$"; sem normalizar, toda comparação com espaço comum falha.
      const norm = (t) => t.replace(/\u00a0/g, ' ');
      const achatado = norm(texto);
      const precisa = [
        'R$ 8.324,64',    // Adeildo desembolsado (com MIR e seringa confirmados na M26.21A)
        'R$ 10.536,12',   // Faustino desembolsado
        'R$ 1.500,00',    // Koko pago Adeildo / pendente
        'R$ 6.500,00',    // Koko pago Faustino
        'R$ 9.500,00',    // Koko custo efetivo
        'R$ 500,00',      // bonificação
        'R$ 1.040,00',    // contabilidade Adeildo
        'R$ 1.560,00',    // contabilidade Faustino
        'R$ 50,06',       // impostos
        'R$ 130,00',      // Mais Laudo
        'R$ 1.573,80',    // compromisso mensal Adeildo
        'R$ 4.985,84',    // MIR: 4 parcelas confirmadas
        'R$ 798,80',      // seringa: 4 parcelas confirmadas
        'R$ 8.676,96',    // saldo pendente de Adeildo após a confirmação
      ];
      if (process.env.M26_21_DEBUG) console.log(`[dbg] ${engine} ${width} texto=${texto.length} :: ${texto.slice(0,300)}`);
      for (const t of precisa) {
        assert(achatado.includes(t), `${engine} ${width}: "${t}" ausente do módulo`);
        checks += 1;
      }
      // E o que NÃO pode aparecer.
      for (const proibido of ['R$ 10.000,00', 'R$ 3.000,00', 'R$ 2.500,00']) {
        assert(!achatado.includes(proibido),
          `${engine} ${width}: valor incorreto "${proibido}" apareceu (Koko nominal/adiantamento errado)`);
        checks += 1;
      }
      // Item de histórico NÃO confirmado não pode exibir R$ 0,00 em lugar
      // nenhum da sua linha: ali o dado é desconhecido e o painel mostra "—".
      // (Zero é legítimo quando é FATO confirmado — ex.: Adeildo não pagou
      // imposto nenhum, e aí "R$ 0,00" é a informação correta.)
      for (const nome of ['Google Workspace', 'Tailscale', 'Hostinger VPS']) {
        for (const l of achatado.split('\n').filter((l) => l.includes(nome))) {
          assert(!l.includes('R$ 0,00'),
            `${engine} ${width}: "${nome}" tem histórico não confirmado e exibiu R$ 0,00 — ${l.trim()}`);
          checks += 1;
        }
      }
      // Custo de valor variável aparece como "variável", nunca como R$ 0,00/mês.
      for (const nome of ['Impostos / Tributos da empresa', 'Mais Laudo']) {
        for (const l of achatado.split('\n').filter((l) => l.includes(nome))) {
          assert(l.includes('variável') || !l.includes('R$ 0,00/mês'),
            `${engine} ${width}: "${nome}" exibiu mensal R$ 0,00 em vez de "variável" — ${l.trim()}`);
          checks += 1;
        }
      }
      // Nenhuma PARCELA pode mais aparecer com o status "aguardando confirmação":
      // a M26.21A confirmou as 4 vencidas de cada parcelamento do cartão. A frase
      // segue legítima na nota de política, que vale para as parcelas futuras —
      // por isso a checagem olha as linhas do histórico, não a aba inteira.
      const linhasParcela = await page.evaluate(() => {
        document.querySelectorAll('.ci-pane').forEach((p) => p.toggleAttribute('hidden', p.id !== 'ci-parcelamentos'));
        return [...document.querySelectorAll('#ci-parcelamentos tbody tr')].map((tr) => tr.innerText);
      });
      assert(linhasParcela.length > 0, `${engine} ${width}: histórico de parcelas vazio`);
      for (const l of linhasParcela) {
        assert(!l.includes('aguardando confirmação'),
          `${engine} ${width}: parcela ainda em "aguardando confirmação" — ${l.replace(/\s+/g, ' ').trim()}`);
        checks += 1;
      }
      assert.equal(errors.length, 0, `${engine} ${width}: erro de JS — ${errors.join(' | ')}`);
      checks += 1;
      await page.close();
    }
    await browser.close();
  }
  console.log(`OK — ${checks} verificações visuais passaram. Evidências: ${out}`);
})().catch((e) => { console.error('FALHOU:', e.message); process.exit(1); });
