#!/usr/bin/env python3
"""
SoproLife M23.1 — Regressão: allowlist de campos de followup-clinicas
sincronizada com o produtor real.

Contexto: o produtor (generate-followup-clinicas.py) escreve o campo
booleano "etapa_terminal" em cada registro há tempo — ele é consumido pela
própria _summarize() do gerador para separar prospecção ativa de etapas
terminais (ganho/perdido). O consumidor de segurança (check-access.sh,
validate_followup_clinicas) tinha uma ALLOWED_FIELDS incompleta que nunca
foi atualizada quando "etapa_terminal" foi adicionado ao formato do
registro, produzindo o falso positivo permanente "AVISO: campo inesperado
'etapa_terminal'" a cada execução. Corrigido no M23.1 (regra fica
autoconsistente com o produtor); este teste impede a mesma lacuna de
reaparecer se um campo novo for adicionado a um dos dois lados sem o outro.

100% offline: não gera dados reais, não acessa rede nem PostgreSQL. Extrai
a allowlist do texto-fonte de check-access.sh (sem executar o script) e o
conjunto de chaves realmente escritas por generate-followup-clinicas.py a
partir do dicionário `rec` construído em build_records().

Uso: python3 painel-soprolife/scripts/test-followup-clinicas-allowlist.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CHECK_ACCESS = RAIZ / "scripts" / "check-access.sh"
GERADOR = RAIZ / "scripts" / "generate-followup-clinicas.py"

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


check_access_text = CHECK_ACCESS.read_text(encoding="utf-8")
gerador_text = GERADOR.read_text(encoding="utf-8")

# Extrai a ALLOWED_FIELDS específica de validate_followup_clinicas(): é a
# única ocorrência no arquivo cujo comentário acima menciona B2B.
match = re.search(
    r"validate_followup_clinicas\(\).*?ALLOWED_FIELDS = \{([^}]*)\}",
    check_access_text, re.DOTALL,
)
caso("validate_followup_clinicas() e sua ALLOWED_FIELDS foram encontradas",
     match is not None)
allowed = set(re.findall(r'"([a-z_]+)"', match.group(1))) if match else set()
caso("allowlist não está vazia (regex não quebrou silenciosamente)",
     len(allowed) >= 5, str(allowed))

# Extrai as chaves literais do dict `rec` construído em build_records().
rec_match = re.search(r'rec: dict = \{([^}]*)\}', gerador_text, re.DOTALL)
caso("dicionário rec do produtor foi encontrado", rec_match is not None)
produced = set(re.findall(r'"([a-z_]+)":', rec_match.group(1))) if rec_match else set()
# Duas chaves são adicionadas condicionalmente fora do dict literal
# (telefone_whatsapp, whatsapp_url) — fazem parte do contrato real do
# registro e precisam estar na allowlist também.
produced |= {"telefone_whatsapp", "whatsapp_url"}
caso("conjunto de campos produzidos não está vazio", len(produced) >= 5, str(produced))

faltando = produced - allowed
caso("todo campo escrito pelo produtor está na allowlist do guarda de segurança",
     not faltando, f"faltando: {sorted(faltando)}")

caso("'etapa_terminal' especificamente está coberto (M23.1)",
     "etapa_terminal" in allowed)

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
