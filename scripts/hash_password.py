#!/usr/bin/env python3
"""Gera um hash Argon2id para a senha administrativa, sem exibir a senha."""
from getpass import getpass
from argon2 import PasswordHasher
from argon2.exceptions import HashingError

def main():
    first = getpass("Nova senha administrativa (mínimo 14 caracteres): ")
    second = getpass("Repita a senha: ")
    if len(first) < 14:
        raise SystemExit("A senha precisa ter pelo menos 14 caracteres.")
    if first != second:
        raise SystemExit("As senhas não conferem.")
    try:
        print(PasswordHasher().hash(first))
    except HashingError as exc:
        raise SystemExit("Não foi possível gerar o hash.") from exc

if __name__ == "__main__":
    main()
