#!/usr/bin/env python3
"""Cria uma cópia consistente do SQLite usando a API de backup nativa."""
import argparse
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, help="Diretório de backup fora da pasta pública e, idealmente, fora da VPS")
    args = parser.parse_args()
    source = Path(os.environ.get("DATABASE_PATH", "instance/unlockfacil.sqlite3")).resolve()
    destination_dir = Path(args.destination).resolve()
    if not source.is_file():
        raise SystemExit(f"Banco de origem não encontrado: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = destination_dir / f"unlockfacil-{stamp}.sqlite3"
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            target.unlink(missing_ok=True)
            raise SystemExit("A verificação de integridade do backup falhou.")
    finally:
        dst.close()
        src.close()
    os.chmod(target, 0o600)
    print(f"Backup verificado: {target}")
    print("Importante: copie-o para armazenamento externo e teste a restauração periodicamente.")

if __name__ == "__main__":
    main()
