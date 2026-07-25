#!/usr/bin/env node
// M22 — contrato estático do formulário Pastore e do fechamento mensal.
"use strict";

const fs = require("fs");
const path = require("path");
const root = path.resolve(__dirname, "..");
const read = (...parts) => fs.readFileSync(path.join(root, ...parts), "utf8");
let failures = 0;
function check(name, condition) {
  console.log(`  ${condition ? "PASS" : "FAIL"}: ${name}`);
  if (!condition) failures += 1;
}

const central = read("js", "central-cadastros.js");
const settlement = read("js", "pastore-settlement.js");
const index = read("index.html");
const css = read("css", "pastore-settlement.css");
const schemas = read("nucleo-m15", "app", "schemas.py");
const attendances = read("nucleo-m15", "app", "routers", "attendances.py");
const router = read("nucleo-m15", "app", "routers", "pastore.py");
const models = read("nucleo-m15", "app", "models.py");
const migration = read(
  "nucleo-m15", "migrations", "versions",
  "b8c4e6d21a90_m22_pastore_monthly_settlement.py"
);

console.log("\nA) Formulário Pastore exclusivo");
check("configuração vem do endpoint canônico fail-closed",
  /api\("\/pastore\/configuracao-atendimento"\)/.test(central));
check("parceiro aparece somente leitura", /pastoreReadonly\("Parceiro"/.test(central));
check("unidade única é exibida sem seletor",
  /unidades\.length === 1[\s\S]{0,180}?pastoreReadonly\("Unidade operacional"/.test(central));
check("múltiplas unidades usam seletor filtrado",
  /unidades\.length > 1[\s\S]{0,300}?sel\("esp_unidade"/.test(central));
check("modalidade Pastore é exatamente Clínica parceira e somente leitura",
  /pastoreReadonly\("Modalidade", "Clínica parceira"/.test(central));
check("origem Pastore é somente leitura",
  /pastoreReadonly\("Origem",[\s\S]{0,100}?"Pastore"/.test(central));
check("local genérico existe somente no ramo não-Pastore",
  /if \(!ehPastore\)[\s\S]{0,900}?Local \/ unidade de atendimento/.test(central));
check("controles de pagamento existem somente no ramo não-Pastore",
  /if \(!ehPastore\)[\s\S]{0,1500}?Valor da espirometria[\s\S]{0,400}?Status do pagamento[\s\S]{0,300}?Data de recebimento[\s\S]{0,300}?Forma de pagamento/.test(central));
check("Pastore sai de montarFinanceiro antes de ler controles financeiros",
  /function montarFinanceiro[\s\S]{0,180}?if \(tipo === TIPO_PASTORE\) return null/.test(central));
check("payload Pastore contém apenas vínculos técnicos de parceiro/unidade",
  /if \(tipo === TIPO_PASTORE\)[\s\S]{0,350}?bloco\.partner_id = pastore\.partner\.id;[\s\S]{0,100}?bloco\.partner_unit_id = unidade;[\s\S]{0,80}?\} else \{[\s\S]{0,300}?local_atendimento/.test(central));

console.log("\nB) Backend não monetário");
check("schema rejeita qualquer financeiro Pastore",
  /Espirometria Pastore não aceita pagamento direto do paciente/.test(schemas));
check("backend deriva modalidade, local e origem",
  /modalidade = "clinica_parceira"/.test(attendances) &&
  /local_atendimento = unit\.nome/.test(attendances) &&
  /origem = partner\.nome/.test(attendances));
check("backend retorna zero lançamentos no Pastore",
  /if payload\.tipo == TIPO_PASTORE:[\s\S]{0,350}?return \[\]/.test(attendances));

console.log("\nC) Fechamento mensal");
check("modelo reaproveita PartnerSettlement e adiciona itens por exame",
  /class PartnerSettlement\(/.test(models) &&
  /class PartnerSettlementItem\(/.test(models));
check("grupo protegido por parceiro, unidade e competência",
  /uq_partner_settlement_competencia_unidade/.test(models));
check("exame é único entre fechamentos",
  /spirometry_exam_id:[\s\S]{0,180}?unique=True/.test(models));
check("recibo de fechamento é único",
  /partner_settlement_id:[\s\S]{0,180}?unique=True/.test(models));
check("valor inicial nunca é inferido",
  /valor_total=None/.test(router));
check("recebimento exige gestor e cria receita agregada",
  /receive_monthly_settlement/.test(router) &&
  /require_role\(ROLE_GESTOR\)/.test(router) &&
  /categoria="Recebimento de parceiro"/.test(router));
check("recibo não se liga a exame/paciente individual",
  /partner_settlement_id=settlement\.id/.test(router) &&
  !/spirometry_exam_id=exam/.test(
    router.slice(router.indexOf("def receive_monthly_settlement"))
  ));

console.log("\nD) UI, histórico e segurança");
check("indicadores Pastore ficam separados no Financeiro",
  /pastoreFinanceIndicatorRoot/.test(index) &&
  /Pastore — aguardando fechamento/.test(settlement) &&
  /Pastore — recebido/.test(settlement));
check("fluxo mensal está na área Parcerias",
  /pastoreSettlementRoot/.test(index) &&
  /Fechamento mensal Pastore/.test(settlement));
check("RBAC gestor controla botões de mutação",
  /m15\(\)\.can\("gestor"\)/.test(settlement));
check("viewports 1000, 800 e estreito têm regras explícitas",
  /max-width: 1000px/.test(css) && /max-width: 800px/.test(css) &&
  /max-width: 520px/.test(css));
check("proveniência exata e dez códigos estão na migração",
  /Rateio gerencial provisório do total histórico/.test(migration) &&
  (migration.match(/"LAN-0000(?:0[4-9]|10|11|12|13)"/g) || []).length === 10);
check("migração não contém escrita Google Sheets",
  !/sheets\.googleapis|spreadsheets\.values|script\.google\.com/.test(migration));
check("frontend não contém escrita Google Sheets",
  !/sheets\.googleapis|spreadsheets\.values|script\.google\.com/.test(central + settlement));

console.log();
if (failures) {
  console.log(`RESULTADO: ${failures} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos M22 passaram.");
