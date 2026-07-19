"""Camada de prontidão de migração Sheets -> PostgreSQL (M15.6A).

Não é um importador paralelo: reutiliza app.importer.csv_import como motor
de dry-run/execução e acrescenta o contrato de governança — manifesto de
snapshot, registro de mapeamento versionado, portões de execução, evidência
de rollback e reconciliação determinística.
"""
