# SoproLife Marketing & SEO

Use esta skill para tarefas de marketing, SEO, Search Console, GA4, conteúdo, páginas locais e crescimento orgânico da SoproLife.

## Regras centrais

- Marca pública: SoproLife.
- Não usar número antigo/truncado.
- WhatsApp oficial: (21) 99890-1775 / 55 21 99890-1775.
- Foco inicial: B2B/PCMSO, clínicas, empresas, medicina do trabalho e parcerias.
- Manter linguagem profissional, médica e acessível.
- Não prometer diagnóstico ou resultado clínico.
- Não inventar dados de performance.
- Dados de Search Console e GA4 devem ser agregados e seguros.

## SEO local

Priorizar:
- espirometria no Rio de Janeiro;
- espirometria RJ;
- espirometria ocupacional;
- espirometria para PCMSO;
- espirometria domiciliar;
- teleconsulta respiratória;
- parceria com clínicas e empresas.

## Segurança de dados

- Nunca expor pacientes, telefone, pedido médico, CPF, laudo ou observação privada.
- Métricas públicas devem ser agregadas.
- Configurações de GA4, Search Console, IDs e tokens ficam fora do Git.

## Aprendizado — ADC e escopos Google

Quando a VPS mostrar:
- `Reauthentication is needed`
- `ACCESS_TOKEN_SCOPE_INSUFFICIENT`
- `Insufficient Permission`
- `acesso negado`

Reautenticar ADC com escopos explícitos:

`gcloud auth application-default login --no-launch-browser --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets.readonly,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/analytics.readonly"`

Depois rodar `update-local-data.sh` e conferir Search Console, GA4 e Sheets.

## Regra de validação

Antes de concluir tarefa de marketing/SEO:
- rodar atualização local;
- verificar se SC=True e GA4=True quando esperado;
- confirmar que nenhum ID sensível foi commitado.
