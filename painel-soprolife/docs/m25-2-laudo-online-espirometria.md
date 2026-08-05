# M25.2 — Laudo online de espirometria (documento próprio da SoproLife)

Este runbook descreve o contrato implementado para desenvolvimento e teste
isolado. Ele **não** autoriza habilitação, implantação, uso de paciente
real, emissão de laudo clínico real nem contratação de provedor de
assinatura qualificada.

## Dois documentos separados

O marco formaliza a separação exigida pelo produto:

| Documento | Espécie de versão | Regra |
| --- | --- | --- |
| PDF técnico do equipamento MIR | `original` | intacto, byte a byte; nunca recebe assinatura, carimbo ou sobreposição; download próprio |
| Laudo médico da SoproLife | `laudo_previa`, `laudo_liberado`, `laudo_adendo` | gerado nativamente pelo Centro de Comando com reportlab |

O laudo nativo **não** embute nenhuma página, imagem ou miniatura do exame.
O próprio PDF declara, em texto, que o documento técnico da MIR é separado.

`GET /laudos/{id}/documentos` devolve os dois com caminhos de download
distintos. O nome sugerido do arquivo diferencia
`exame-tecnico-mir-…` de `laudo-…`.

## Fluxo da médica

```text
operacional envia PDF da MIR + atribui
  → atribuido
médica atribuída escolhe conclusão e gera prévia nativa
  → em_elaboracao        (POST .../laudo/previa)
médica confere a prévia (é o PDF final exato) e confirma
  → liberado             (POST .../assinar-e-liberar)
correção posterior
  → adendo               (POST .../adendo, versão nova, anterior preservada)
  → ou documento corretivo separado (POST .../nova-versao-corretiva)
```

O caminho M24C (`/compor`, `/preparar-assinatura`) permanece intacto e
disponível como anotação técnica sobre o PDF da MIR e como preparação para
uma futura assinatura qualificada. Ele não foi substituído nem removido.

## Estado `liberado` vs `assinado`

São deliberadamente distintos e **não** devem ser confundidos:

- `liberado` + `signature_status = liberada_institucional`: ação consciente
  da médica atribuída, autenticada na própria sessão. Prova quem liberou,
  qual texto, qual hash do PDF, quando e em qual fuso. **Não é** assinatura
  digital qualificada.
- `assinado` + `signature_status = assinada`: reservado para PAdES /
  ICP-Brasil, com provedor real. Continua inalcançável nesta entrega;
  `get_signature_provider()` segue devolvendo o provedor nulo.

O PDF liberado declara textualmente que a liberação
“não constitui, por si só, assinatura digital qualificada ICP-Brasil”.
Nenhum ponto do código, da API ou da interface afirma o contrário.

## Catálogo de conclusões

`app/services/report_conclusions.py` é a fonte única e fechada: 17
conclusões clínicas + `PERSONALIZADO`, e 5 complementos pós-BD. O painel
mostra a abreviação curta; o servidor converte para o texto por extenso.

O sistema **não** calcula grau, **não** interpreta valores de espirometria e
**não** pré-seleciona conclusão. A escolha é integralmente da médica, que
pode ainda reescrever livremente o texto final antes de assinar.

Complementos pós-broncodilatador só são oferecidos quando
`spirometry_exams.broncodilatador is True`. Sem fase pós-BD, apenas
`BD_NAO_REALIZADO` é aceito — e ele não acrescenta frase nenhuma.

A versão guarda tanto o texto assinado (`interpretation_text_snapshot`)
quanto a escolha de catálogo que o originou
(`conclusion_code_snapshot`, `bronchodilator_code_snapshot`), de modo que a
decisão permanece auditável mesmo depois de a redação ser editada.

## Local de realização

`app/services/report_locations.py` resolve o local, nesta ordem:

1. `report_documents.origin_partner_unit_id`;
2. `spirometry_exams.partner_unit_id`;
3. rótulo derivado da origem controlada (domicílio, coworking, empresa…).

Nenhum endereço fica fixo no template. `partner_units` ganhou
`logradouro`, `uf`, `cep` e `telefone_central`. Se a unidade não tiver
endereço cadastrado, o laudo sai com o nome da unidade e sem linha de
endereço — nada é inventado.

A migration só faz **backfill** do endereço da Pastore Ipanema quando essa
unidade **já existe** e ainda está sem logradouro. Ela nunca cria parceiro,
unidade, médico, paciente ou laudo.

## Assinatura manuscrita

`physician_signature_assets` guarda somente referência técnica: caminho
interno relativo, SHA-256, tamanho e dimensões. A imagem vive
exclusivamente sob `M15_REPORTS_STORAGE_DIR` (raiz privada `0700`, fora do
Git) e é lida apenas no instante em que o PDF liberado é desenhado.

Proibições sustentadas pelo código:

- nunca versionada no repositório;
- nunca devolvida por API, nem em bytes nem em caminho;
- nunca entregue em JavaScript ou em URL pública/permanente;
- nunca registrada em log (auditoria guarda só hash e dimensões);
- nunca presente em fixture, exemplo ou teste — a suíte gera um PNG
  geométrico sintético, sem semelhança com assinatura real.

**Onde cadastrar o ativo autorizado** (quando existir e for aprovado):

```text
POST /api/v1/laudos/admin/medicos/{physician_profile_id}/assinatura
  multipart: arquivo=<PNG>, confirmacao="ATIVO DE ASSINATURA AUTORIZADO"
  autorização: papel admin (nem a própria médica cadastra o próprio ativo)
```

O arquivo é gravado em
`<M15_REPORTS_STORAGE_DIR>/assinaturas/<physician_profile_id>/<asset_id>.png`.

O sistema é **funcional sem o ativo**: sem imagem cadastrada, o laudo é
liberado normalmente e sai apenas com o bloco identificador da médica. A
área reservada da assinatura permanece limpa e do mesmo tamanho.

Um novo cadastro **revoga** o anterior sem apagá-lo, para que laudos já
liberados continuem apontando para o hash que realmente usaram. Ativo
cadastrado que não puder ser lido, ou cujo hash/dimensão divergir, **falha
fechado** e interrompe a liberação — nunca é ignorado em silêncio.

## Conteúdo do PDF

Logo oficial, título, paciente (nome, nascimento, idade, sexo, registro),
exame (código, data/hora, fase pós-BD, indicação clínica), local de
realização, conclusão, observações, adendos, aviso sobre o PDF da MIR,
bloco de identificação e validação (código, versão, data/hora da liberação,
QR Code quando há URL configurada) e a área exclusiva de identificação e
assinatura médica.

Garantias de layout: o cursor reserva altura antes de desenhar e quebra
página quando um bloco não cabe; a área de assinatura é atômica e fecha o
documento; nada é desenhado sobre logo, assinatura ou rodapé.

Campos sem dado cadastrado imprimem “não informada/não informado”.

## Código e endereço de validação

A liberação aloca um `validation_code` opaco de 12 caracteres (alfabeto sem
`0/O/1/I/L`), único no banco, não derivado de dado do paciente.

`M15_REPORTS_VALIDATION_BASE_URL` (HTTPS obrigatório) habilita a URL e o QR
Code impressos. Sem a variável, o laudo sai apenas com o código textual —
nenhuma URL é inventada.

`GET /laudos/validacao/{codigo}` exige **sessão autenticada** e devolve
somente dados institucionais e técnicos (código, versão, hash, data de
liberação, identificação da médica, natureza da liberação). Nunca paciente,
conclusão ou texto clínico. Verificação pública anônima permanece uma
decisão de privacidade em aberto e **não** foi implementada.

## Auditoria

Eventos registrados: `laudo_nativo_previa_gerada`,
`laudo_assinado_e_liberado`, `laudo_adendo_publicado`,
`assinatura_medica_cadastrada`, `assinatura_medica_revogada`, além dos
eventos M24C já existentes.

A allowlist recursiva de `app/audit.py` continua sendo a fronteira: as
chaves novas são apenas identificadores técnicos, códigos de catálogo,
hashes e booleanos. Nenhum registro carrega nome de paciente, texto
clínico, filename, caminho absoluto ou bytes de documento.

## Imutabilidade

`report_addenda` entra na mesma proteção append-only das demais evidências:
trigger no PostgreSQL e guarda de sessão SQLAlchemy (`app/db.py`).
`report_document_versions` permanece imutável — todo novo conteúdo é uma
linha e um arquivo novos; nada é sobrescrito.

Depois de `liberado`, o documento recusa nova prévia
(`laudo_bloqueado_para_edicao`) e nova liberação (`laudo_ja_liberado`).

## Migração

Head: `a3f1d7c25e90`, descendente de `c657f22bf857`. Aditiva: todas as
colunas nascem nullable e as CHECKs apenas ALARGAM conjuntos permitidos.

O downgrade **falha fechado** se existir qualquer laudo liberado, versão de
laudo nativo, adendo, ativo de assinatura ou assinatura institucional.

## Configuração nova

```text
M15_REPORTS_VALIDATION_BASE_URL   # opcional, HTTPS, base do QR/validação
M15_REPORTS_SIGNATURE_MAX_BYTES   # opcional, padrão 2 MiB
```

## NO-GO para produção

M25.2 **não** encerra nenhum bloqueador anterior. Continuam pendentes, no
mínimo:

- aprovação clínica e jurídica formal do texto do laudo e do rodapé;
- decisão jurídica sobre a suficiência da liberação institucional para
  entrega ao paciente, ou contratação de assinatura qualificada
  (PAdES/ICP-Brasil) conforme `m24c-signature-provider-decision.md`;
- ativo de assinatura manuscrita autorizado pela Dra. Ana Cristina;
- raiz privada provisionada com `ReadWritePaths` aprovado, backup
  coordenado banco+storage e ensaio de restauração;
- política de retenção de negócio;
- decisão de privacidade sobre validação pública anônima;
- autorização explícita de habilitação e gates pré/pós-implantação.

M25.2 **não alterou nenhuma flag**. O estado versionado continua exatamente
como o M24D o deixou:

- backend: `M15_REPORTS_ENABLED=false` e `M15_REPORTS_MODE=disabled` por
  padrão em `app/config.py` — nenhuma rota `/laudos` é servida sem que as
  duas variáveis sejam definidas explicitamente no ambiente de implantação,
  junto da autorização dedicada do piloto;
- frontend: `reports_enabled=true` e `reports_mode=pilot` em
  `data/m15-config.json`, que é a decisão de ativação versionada do piloto
  interno controlado do M24D e, sozinha, não habilita nada — o backend
  continua sendo a fronteira fail-closed.

O modo `production` permanece bloqueado incondicionalmente
(`relatorios_producao_bloqueada`).

---

## M25.4 — enxugamento visual, selo e cadastro da assinatura

### O que mudou no documento

O laudo perdeu camadas de repetição, não informação:

- `Documento` e `Versão` saíram do bloco de validação — os dois já constam
  do cabeçalho e do rodapé de toda página (eram a terceira repetição).
- Cabeçalhos soltos de seção viraram título EMBUTIDO no cartão
  (`draw_data_card`), removendo uma camada visual por bloco.
- Os rótulos deixaram de ecoar o título do cartão
  ("PACIENTE › PACIENTE" virou "PACIENTE › NOME").
- A nota sobre o PDF da MIR saiu da caixa própria e virou nota de rodapé:
  a informação continua obrigatória, mas parou de competir em peso visual
  com a conclusão médica.
- `RELEASE_STATEMENT` foi encurtada mantendo as três afirmações que precisam
  constar: quem liberou e como, o que prova a integridade, e o que a
  liberação **não** é.

### Selo institucional

`_Composer.draw_verification_seal()` desenha um selo circular próprio da
SoproLife (dois anéis, um "visto" e o texto do estado).

Regras que o selo respeita:

- **Só aparece em documento LIBERADO.** Numa prévia seria mentira visual.
- Fica FORA da faixa reservada da assinatura manuscrita — nunca por cima.
- O texto diz "LIBERAÇÃO INSTITUCIONAL", nunca "assinado digitalmente":
  o selo não pode sugerir ICP-Brasil.
- As posições verticais do texto são conferidas contra a corda do anel
  interno; num círculo a largura disponível cai conforme se afasta do
  centro, e foi assim que "INSTITUCIONAL" vazou para fora na primeira versão.

O selo é inspirado apenas na ORGANIZAÇÃO de um laudo profissional. Nenhuma
marca, arte ou texto de concorrente foi copiado.

### Cadastro do ativo de assinatura (agora com interface)

Os endpoints existiam desde a M25.2, mas **não havia interface**: na prática
não era possível cadastrar a assinatura sem chamar a API à mão.

Onde fica: **Administração → Contas médicas → selecione a médica →
"Assinatura manuscrita (imagem)"**.

O que a tela faz:

- mostra se existe ativo, com hash e dimensões;
- recebe o PNG e envia a confirmação exigida pela API;
- revoga o ativo atual (o anterior é preservado, nunca apagado).

Não há preview da imagem **de propósito**: a API nunca devolve os bytes nem
o caminho do arquivo. A ausência de preview é a garantia funcionando, não
uma lacuna.

O proxy local passou a aceitar `DELETE` (`_M15_METHODS`) porque a revogação
usa esse verbo; sem isso o botão recebia 405 do próprio proxy, antes de a API
decidir sobre autorização. `PUT` e `HEAD` seguem bloqueados.

### Campos de identificação com interface

`crm_display` e `especialidade` — que a M25.3 tornou graváveis — ganharam
campos no formulário de perfil médico. Sem eles o laudo saía sem
especialidade e com o CRM em dígitos crus.
