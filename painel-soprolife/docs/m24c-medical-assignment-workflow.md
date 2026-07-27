# M24C — atribuição médica e fluxo clínico controlado

Este runbook descreve o contrato implementado para desenvolvimento e testes
isolados. Ele não autoriza habilitação, implantação, criação de médico real,
uso de paciente real, emissão de laudo clínico ou conexão com provedor de
assinatura.

## Estado de segurança

Laudos permanecem desligados por padrão nas duas camadas:

- backend: `M15_REPORTS_ENABLED=false`;
- frontend: `reports_enabled=false` em `data/m15-config.json`.

Sem ambas as habilitações explícitas, toda a entrada fica oculta e a API
recusa `/laudos` antes de processar multipart. O override
`soproM24AReports=on` existe somente em loopback para testes isolados. Não há
raiz de storage provisionada nem `ReadWritePaths` de laudos na unit systemd.
M24C não muda nenhum desses fatos.

## Ameaças de RBAC e papel médico

`medico` é um papel explícito e isolado. Ele não herda `leitura`,
`operacional`, `gestor` ou `admin`; nenhum desses papéis herda `medico`.
Autoria clínica exige, ao mesmo tempo:

1. papel `medico` explicitamente vinculado à conta;
2. conta ativa;
3. perfil profissional ativo e verificado;
4. atribuição ativa daquele documento ao mesmo perfil.

Uma conta pode ter vários papéis explícitos, mas a parte administrativa da
conta não substitui perfil nem atribuição. Em particular, um admin não
atribuído não é autor médico.

| Capacidade | `medico` atribuído | `operacional` | `gestor`/`admin` sem atribuição |
| --- | --- | --- | --- |
| Ver fila clínica própria | sim | não | não |
| Ver paciente dentro do documento atribuído | sim | não | não |
| Ver PDF original autenticado | sim | não | não |
| Escolher template aprovado e editar interpretação | sim | não | não |
| Gerar prévia e preparar assinatura | sim | não | não |
| Localizar exame, enviar PDF e atribuir | não | sim | conforme hierarquia operacional |
| Acompanhar código/origem/status técnico | não | sim | conforme hierarquia |
| Atribuir ou reatribuir antes do rascunho | não | sim | conforme hierarquia operacional |
| Administrar papel/perfil/template | não | não | somente `admin` |
| Marcar como assinado ou liberar | não | não | não |

O frontend de uma conta exclusivamente médica oculta as demais entradas e
seções. A API continua sendo a fronteira de autorização; ocultação visual não
é usada como controle de acesso.

## Perfil profissional e administração

`physician_profiles` possui relação um-para-um com `users` e guarda nome
profissional, CRM normalizado para 1–12 dígitos, UF em allowlist fechada das
27 UFs, RQE opcional, estado ativo, estado/evidência de verificação e
timestamps.

O banco impede dois perfis ativos com o mesmo CRM/UF. Em PostgreSQL, trigger
também recusa CRM não normalizado. Ativação exige conta ativa, papel médico,
nome, CRM, UF e verificação completa com timestamp e usuário técnico
verificador. Suspender o perfil ou remover o papel bloqueia imediatamente
novas atribuições, fila, visualização e operações clínicas.

O workspace administrativo:

- seleciona apenas usuário já existente;
- concede ou remove o papel médico explícito;
- cria ou revisa o perfil;
- verifica, ativa ou suspende;
- não cria conta e não recebe senha;
- não expõe hash, token, CPF, endereço ou segredo.

Alterar o perfil não reescreve versões clínicas anteriores.

## Origem e atribuição

O recebimento operacional localiza o exame pelo código institucional `ESP`,
seleciona um perfil ativo/verificado, escolhe a origem e envia o PDF no mesmo
POST transacional.

As origens fechadas são:

- `pastore`;
- `coworking`;
- `residencial`;
- `clinica_parceira`;
- `empresa_pcmso`;
- `outro`.

O rótulo opcional é somente operacional e não aceita identidade, contato ou
informação clínica. Uma referência técnica de unidade parceira pode ser usada
quando houver unidade já cadastrada e coerente com `clinica_parceira`.

Um índice parcial único garante exatamente uma atribuição ativa por
documento. PostgreSQL valida que ela aponta para perfil ativo/verificado,
conta ativa e papel médico explícito. Cada atribuição cria evento técnico
append-only.

Reatribuição:

- é exclusiva do operacional;
- exige o ID da atribuição esperada para detectar concorrência;
- só ocorre em `atribuido`, antes de `clinical_started_at`;
- encerra a linha anterior e cria outra, sem reescrever histórico;
- usa somente motivo fechado:
  `assignment_correction`, `physician_unavailable`,
  `profile_suspended` ou `operational_redistribution`;
- não possui motivo em texto livre.

## Fila e workspace médico

“Meus laudos” retorna somente código do laudo, código e data do exame,
origem, timestamp de atribuição, status e estado de assinatura. Não retorna
nome de paciente, CPF, contato, interpretação, corpo de template, filename ou
caminho.

Ao abrir um documento atualmente atribuído, o médico recebe a identidade
mínima necessária ao documento. O PDF original e cada versão gerada são
servidos por endpoint autenticado com `private, no-store`, `nosniff`,
auditoria mínima e URL de objeto temporária no navegador.

O workspace oferece templates aprovados, tooltip/texto completo, editor,
página, topo/rodapé, prévia e comparação entre original e gerado. Não há
interpretação automática de valores de espirometria, diagnóstico por IA ou
conclusão automática.

## Ciclo clínico

O ciclo novo é:

```text
operacional envia + atribui
  → atribuido
médico atribuído gera prévia
  → em_elaboracao
médico atribuído marca pronto
  → assinatura_pendente
provedor qualificado futuro
  → assinado (não alcançável nesta entrega)
```

`gestor` e `admin` não finalizam nem assinam por seu papel administrativo.
`operacional` não compõe nem altera interpretação. A preparação copia os
bytes revalidados e congela todas as evidências, mas usa provider
`unconfigured` e nunca produz sucesso.

Somente uma versão genuinamente assinada, com evidência futura de PAdES,
cadeia ICP-Brasil, hash e perfil atribuído correspondentes, poderá abrir um
documento corretivo separado. O predecessor não é atualizado, substituído ou
apagado. Esta condição só é exercitada por fixture interna isolada; não existe
provider mock de sucesso no runtime.

## Evidência imutável

Cada versão composta ou preparada congela:

- ID técnico, nome profissional, CRM/UF e RQE do médico;
- origem, rótulo e referência técnica de unidade;
- ID, código, revisão, texto e hash do template;
- interpretação e hash;
- ID, código, revisão, texto renderizado e hash do rodapé;
- emissão, página, posição, hash/tamanho/páginas do PDF.

Versões, eventos de atribuição, revisões de template e rodapés são imutáveis
na aplicação e por trigger em PostgreSQL. A linha de atribuição só aceita a
transição ativa→encerrada uma vez.

## Templates provisórios

O catálogo inicial contém exatamente:

1. `NORMAL_PROVISORIO`;
2. `OBSTRUTIVO_PROVISORIO`;
3. `OBSTRUTIVO_BD_PROVISORIO`;
4. `SUGESTIVO_RESTRITIVO_PROVISORIO`;
5. `MISTO_PROVISORIO`;
6. `INESPECIFICO_QUALIDADE_PROVISORIO`.

Todos são `draft`, `clinically_approved=false` e têm apenas:

```text
TEXTO CLÍNICO PENDENTE DE APROVAÇÃO — NÃO UTILIZAR EM PRODUÇÃO
```

Eles aparecem com alerta visual no catálogo administrativo, mas não no
seletor médico. O único override,
`M15_REPORTS_TEST_ALLOW_PROVISIONAL_TEMPLATES=true`, funciona somente em
`dev`, deve ser definido explicitamente por teste isolado e é recusado em
`prod`. Texto aprovado futuro entra como nova revisão; a anterior não muda.

## Rodapé TESTE

O rodapé `TESTE_NAO_ASSINADO`, versão 1, contém nome profissional, CRM/UF,
RQE opcional, exame, origem, emissão, código/versão e estado da assinatura.
Não contém CNPJ, endereço, responsável técnico ou redação jurídica
inventados. Toda versão inclui:

```text
MODELO DE TESTE — DOCUMENTO NÃO ASSINADO E SEM VALIDADE PARA LIBERAÇÃO
```

Ele não é aprovado para produção. Redação legal final e rodapé de produção
continuam bloqueadores.

## Assinatura

O alvo legal declarado é assinatura eletrônica qualificada do médico com
ICP-Brasil e PDF independentemente verificável. Nenhum fornecedor foi
selecionado. Não há credencial, SDK, chamada de rede, QR visual ou imagem de
assinatura. Consulte
`m24c-signature-provider-decision.md` para os requisitos pendentes.

## Auditoria e privacidade

Auditoria de M24C aceita somente IDs técnicos, códigos institucionais,
status, versão/página/posição, origem fechada, motivo fechado e provider
técnico. Nunca registra:

- paciente, CPF, telefone ou e-mail;
- filename ou caminho absoluto;
- bytes ou conteúdo do PDF;
- interpretação ou corpo de template;
- nome/CRM privado do médico;
- credencial, certificado ou segredo.

As entregas inline/download mantêm o evento mínimo existente:
`report_version_id`, `delivery_mode` e `institutional_status`.

## Retenção

O comportamento aprovado é preservação conservadora:

- nenhum PDF ou registro é excluído automaticamente;
- versões finalizadas/assinadas e evidências permanecem preservadas;
- não há endpoint clínico de exclusão;
- não há job de purge;
- reconciliação destrutiva continua limitada a arquivos regulares que sejam
  órfãos confirmados de banco, com guardas e autorização já documentadas;
- política de retenção de negócio permanece pendente de aprovação.

Nenhum prazo de retenção foi inventado. Não configure rotação ou limpeza
agendada por suposição.

## Migração e downgrade

A head M24C é `4c9e2f7a6b31`, descendente de `8d4b1a2c9f70`. A migração é
aditiva, preserva estados legados e cria constraints/índices/triggers.

O downgrade só é reversível quando não há perfil, atribuição, evento, papel
médico concedido, dado clínico M24C ou revisão real de template. Se houver
qualquer linha que seria perdida, ele falha fechado e preserva a head e os
dados. Em ambiente com dados, rollback é por flags/código e restauração
coordenada, não por descarte do schema.

## Compatibilidade MIR

Uma amostra local, quando presente, é tratada apenas como compatibilidade
visual desidentificada. Ela passa pelo validador M24B real; derivados ficam
em diretório temporário, são revalidados e removidos. Nenhuma imagem, tabela
ou número é interpretado clinicamente e a amostra não entra no Git.

Resultado local de 2026-07-27: `PASS`. O arquivo tinha 38.866 bytes, uma
página, `MediaBox [0, 0, 595, 842]`, sem `CropBox`/`TrimBox` explícitas,
rotação 0 e área efetiva `[0, 0, 595, 842]`. Topo e rodapé foram compostos
separadamente com texto exclusivamente TESTE, os dois derivados passaram
novamente pelo validador, exibiram o aviso de documento não assinado e foram
removidos. O SHA-256 do original permaneceu
`dba51baf8c74eccdf21aa15627c00daa04363f2ea6ea99bf11071164a4bcf4ad`.

## NO-GO para produção

Mesmo com testes verdes, M24C continua NO-GO enquanto faltarem, no mínimo:

- conteúdo clínico real formalmente aprovado e governado;
- redação jurídica/rodapé final;
- provedor qualificado selecionado e integrado conforme a decisão técnica;
- validação jurídica, PAdES/ICP-Brasil e verificação ITI;
- raiz privada provisionada, backup/restauração e `ReadWritePaths` aprovados;
- política de retenção de negócio;
- autorização explícita de habilitação e gates pré/pós-implantação.

Não implantar, não habilitar e não criar dados reais como parte deste marco.
