#!/usr/bin/env python3
"""Gera uma chave TOTP para cadastrar manualmente em um app autenticador."""
import pyotp

secret = pyotp.random_base32()
print("Chave TOTP (cadastre em um autenticador e guarde no arquivo de ambiente privado):")
print(secret)
print("Não publique nem envie esta chave. Se ela for exposta, gere outra e reinicie as sessões.")
