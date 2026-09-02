"""Superfície PÚBLICA mínima do portal de resultados (M26.4).

Este pacote é o ÚNICO código servido na internet. Ele não importa nenhum
router do Command Center, não monta `/api/v1` e não conhece CRM, financeiro,
fila médica, usuários nem documentos administrativos. O que ele sabe fazer
está inteiro em `routes.py`, e são cinco coisas.
"""
