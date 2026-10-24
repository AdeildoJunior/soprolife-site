#!/usr/bin/env node
// M26.37 — teste visual do módulo Custos & Investimentos DENTRO da EEGLife.
//
// O que ele prova, e por que cada coisa importa:
//   1. a tela do painel amarelo deixa de ser estrutura vazia e mostra o
//      investimento real da marca;
//   2. os números do rateio aparecem de fato no DOM — Adeildo R$ 12.000,00 e
//      Faustino R$ 12.290,00, somando o ativo UMA vez (R$ 24.290,00);
//   3. nenhum número da SoproLife entra nesta tela;
//   4. R$ 48.580,00 — o total que apareceria se o mesmo ativo fosse contado
//      duas vezes — não existe em lugar algum;
//   5. nada estoura a caixa em 6 larguras, do desktop ao 320.
//
// Nenhuma conexão externa: só o resumo local da EEGLife é servido, e qualquer
// outra requisição é abortada. O tema dourado é aplicado pelo CSS real.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium, firefox } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const out = process.env.VISUAL_SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'sopro-m26-37-'));
fs.mkdirSync(out, { recursive: true });

const BASE = 'http://painel.invalido/painel-soprolife/index.html';
const RESUMO_EEG = 'data/custos-investimentos-eeglife.local.json';
const resumoEeg = fs.readFileSync(path.join(root, RESUMO_EEG), 'utf8');
// Lido só para montar a lista de números PROIBIDOS nesta tela.
const resumoSopro = JSON.parse(fs.readFileSync(
  path.join(root, 'data', 'custos-investimentos-summary.local.json'), 'utf8'));

(async () => {
  let checks = 0;
  for (const [engine, launcher] of Object.entries({ chromium, firefox })) {
    const browser = await launcher.launch({ headless: true });
    for (const width of [1440, 1280, 1024, 768, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 1200 } });
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));

      const original = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
      let html = original
        .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '')
        .replace(/<link\b[^>]*>/g, '');
      html = html.replace('./assets/soprolife-logo.png',
        'data:image/png;base64,' + fs.readFileSync(path.join(root, 'assets/soprolife-logo.png')).toString('base64'));

      // A página é SERVIDA de uma URL, não injetada com setContent: sem URL
      // base o navegador não resolve "./data/…" e a tela erraria por motivo
      // do harness, não do código. Em produção o painel mora em
      // /painel-soprolife/, e é essa a base reproduzida aqui.
      //
      // Só o resumo da EEGLife é servido. Se a tela tentasse buscar o resumo
      // da SoproLife, a requisição seria ABORTADA e o teste veria a tela de
      // erro — o isolamento é verificado, não assumido.
      await page.route('**/*', (route) => {
        const url = route.request().url();
        if (url === BASE) {
          return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
        }
        if (url.includes('custos-investimentos-eeglife')) {
          return route.fulfill({ contentType: 'application/json', body: resumoEeg });
        }
        return route.abort();
      });
      await page.goto(BASE);
      for (const m of original.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
        await page.addStyleTag({ content: fs.readFileSync(path.join(root, m[1]), 'utf8') });
      }

      // A casca decide o contexto pela identidade do servidor. Sem gate ela é
      // fail-closed e remove a EEGLife da árvore — por isso o gate é dublado
      // com o papel que autoriza, que é o que acontece em produção para admin.
      await page.evaluate(() => {
        window.SoproBootGate = { pronto: Promise.resolve({ papeis_efetivos: ['admin'] }) };
      });
      await page.addScriptTag({ content: fs.readFileSync(path.join(root, 'js/eeg-operacao.js'), 'utf8') });
      await page.addScriptTag({ content: fs.readFileSync(path.join(root, 'js/life-command-center.js'), 'utf8') });

      await page.waitForFunction(() => window.LifeCommandCenter
        && window.LifeCommandCenter.allowedContexts().indexOf('eeglife') !== -1);
      await page.evaluate(() => {
        window.LifeCommandCenter.setContext('eeglife');
        window.LifeCommandCenter.openEegModule('custos');
      });
      await page.waitForFunction(() => {
        const s = document.querySelector('[data-eeg-section="custos"]');
        return s && !s.hidden && !/Carregando/.test(s.innerText);
      }, { timeout: 15000 });

      const secao = page.locator('[data-eeg-section="custos"]');
      const r = await page.evaluate(() => {
        const s = document.querySelector('[data-eeg-section="custos"]');
        return {
          montado: s.getAttribute('data-eeg-vivo') === 'montado',
          texto: s.innerText,
          overflow: document.documentElement.scrollWidth > innerWidth + 1,
          // Tabela que estoura a própria caixa indica layout quebrado.
          estouro: [...s.querySelectorAll('table')].some((t) => {
            const w = t.closest('.table-wrap');
            return w ? t.scrollWidth > w.scrollWidth + 1 && w.scrollWidth >= w.clientWidth : false;
          }),
          // O painel da EEGLife tem de estar visível e com o tema próprio.
          painelVisivel: !document.getElementById('lifePanelEegLife').hidden,
          corpo: getComputedStyle(document.querySelector('#lifePanelEegLife .main')).backgroundColor,
        };
      });

      assert.equal(errors.length, 0, `${engine} ${width}: erro de JS — ${errors.join(' | ')}`);
      assert.equal(r.montado, true, `${engine} ${width}: o módulo de custos não assumiu a seção`);
      assert.equal(r.painelVisivel, true, `${engine} ${width}: painel da EEGLife não visível`);
      assert.equal(r.overflow, false, `${engine} ${width}: overflow horizontal da página`);
      assert.equal(r.estouro, false, `${engine} ${width}: tabela estourou a caixa`);
      // O corpo da EEGLife não pode ficar no azulado da SoproLife (#f4f7fb):
      // cor literal em .main venceu token uma vez e só apareceu medindo.
      assert.notEqual(r.corpo, 'rgb(244, 247, 251)',
        `${engine} ${width}: corpo da EEGLife ficou com o fundo da SoproLife`);
      checks += 6;

      // Intl pt-BR usa U+00A0 depois de "R$"; sem normalizar tudo falha.
      const texto = r.texto.replace(/ /g, ' ');
      const PRESENTES = [
        'Nano EEG iCelera',
        'R$ 24.290,00',   // o ativo, contado uma vez
        'R$ 2.429,00',    // parcela mensal combinada
        'R$ 12.000,00',   // Adeildo responde por
        'R$ 12.290,00',   // Faustino responde por
        'R$ 1.200,00',    // parcela de Adeildo
        'R$ 1.229,00',    // parcela de Faustino
        '0/10',           // nenhuma fatura quitada
        'Equipamento',
      ];
      for (const esperado of PRESENTES) {
        assert(texto.includes(esperado),
          `${engine} ${width}: "${esperado}" ausente da tela de custos da EEGLife`);
        checks += 1;
      }

      // Duplicidade: 24.290 x 2. Se este número aparecer, algum total somou o
      // mesmo ativo duas vezes.
      for (const proibido of ['R$ 48.580,00', '48.580']) {
        assert(!texto.includes(proibido),
          `${engine} ${width}: investimento duplicado na tela — "${proibido}"`);
        checks += 1;
      }
      // Nenhum item da SoproLife pode aparecer nesta tela.
      for (const item of resumoSopro.itens) {
        assert(!texto.includes(item.nome),
          `${engine} ${width}: item da SoproLife na tela da EEGLife — "${item.nome}"`);
        checks += 1;
      }
      for (const valor of ['R$ 20.415,76', 'R$ 2.333,80', 'R$ 23.961,61', 'R$ 9.500,00']) {
        assert(!texto.includes(valor),
          `${engine} ${width}: número da SoproLife na tela da EEGLife — "${valor}"`);
        checks += 1;
      }

      await secao.screenshot({ path: path.join(out, `m26-37-eeglife-custos-${engine}-${width}.png`) });
      await page.close();
    }
    await browser.close();
  }
  console.log(`OK — ${checks} verificações visuais passaram. Evidências: ${out}`);
})().catch((e) => { console.error('FALHOU:', e.message); process.exit(1); });
