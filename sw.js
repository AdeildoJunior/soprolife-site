// M25.29E — service worker DESATIVADO de propósito.
//
// Nenhuma página deste site registra mais este arquivo, mas quem visitou o
// site enquanto o registro existia continua com a versão antiga instalada,
// com escopo "/" — que cobre também /painel-soprolife/. A versão antiga:
//
//   1. interceptava TODO GET, inclusive as respostas da API do painel;
//   2. guardava tudo num cache cujo nome trazia um placeholder de versão
//      que nunca chegou a ser substituído na publicação — então o cache
//      jamais invalidava e o navegador seguia servindo frontend velho;
//   3. em qualquer falha devolvia o conteúdo cacheado ou /offline.html.
//
// Combinado com downloads feitos por <a download>, isso fazia o navegador
// salvar HTML/JSON como se fosse o PDF pedido — a origem do arquivo
// "conteúdo 5.jsold" relatado pela operação.
//
// Este arquivo agora só faz uma coisa: se apagar. O navegador busca o
// sw.js de novo periodicamente, então as instalações fantasmas recebem
// esta versão e se desinstalam sozinhas.
//
// NÃO adicionar um handler de `fetch` aqui. Sem ele, o navegador ignora o
// service worker e vai direto à rede — que é exatamente o que queremos.

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const nomes = await caches.keys();
    await Promise.all(nomes.map((nome) => caches.delete(nome)));
    await self.registration.unregister();
    // Recarrega as abas abertas para que saiam do controle deste worker.
    const abas = await self.clients.matchAll({ type: 'window' });
    abas.forEach((aba) => aba.navigate(aba.url));
  })());
});
