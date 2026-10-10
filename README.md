# Unlock Fácil — painel administrativo independente

Este repositório contém **somente o painel administrativo** da Unlock Fácil. Ele é um projeto separado do site público estático (HTML/CSS/JS) hospedado na Netlify. Não inclui nem altera os arquivos do site público e não deve ser colocado dentro da pasta publicada pela Netlify.

## O que o painel faz

- Dashboard com faturamento diário, mensal e acumulado, despesas, anúncios, lucro estimado, comparações de períodos e vendas por ferramenta.
- Registro de vendas com telefone obrigatório, identificação automática do cliente pelo número e consulta do histórico de compras.
- Venda registrada como paga, com Pix pré-selecionado.
- Aba de vencimentos com contas expiradas, telefone e credenciais agrupadas por ferramenta, com opção para copiar `login:senha`.
- Catálogo editável: preço e duração dos planos. Alterações afetam novas vendas; registros antigos preservam os valores e a duração da venda original.
- Cadastro de despesas e gastos com anúncios.
- Banco SQLite criado automaticamente no primeiro uso.

## Estrutura

- `app.py` — aplicação Flask e rotas.
- `templates/` e `static/` — interface do painel.
- `scripts/` — utilitários de backup e configuração.
- `tests/` — testes automatizados.
- `instance/unlockfacil.sqlite3` — banco local criado em execução (não vai para o Git).

## Executar no UserLAnd (celular)

Entre na pasta deste projeto:

```bash
cd ~/unlockfacil_admin
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Configure um usuário e uma senha no terminal antes de iniciar. Use uma senha própria e não publique as credenciais:

```bash
export ADMIN_USERNAME='admin'
export ADMIN_PASSWORD='escolha-uma-senha-forte'
export COOKIE_SECURE=false
export DATABASE_PATH="$PWD/instance/unlockfacil.sqlite3"
mkdir -p instance
python -c "from app import create_app; from waitress import serve; serve(create_app(), host='0.0.0.0', port=5000)"
```

Abra no próprio celular `http://127.0.0.1:5000/admin/login`. Para acessar pelo PC, use `http://IP_DO_CELULAR:5000/admin/login` quando ambos estiverem na mesma rede Wi-Fi. Descubra o IP com `hostname -I`.

O terminal precisa permanecer ativo enquanto o servidor estiver rodando. Não configure redirecionamento de portas do roteador para expor esse servidor à internet. Em HTTP local, `COOKIE_SECURE=false` é necessário; em produção com HTTPS, configure `COOKIE_SECURE=true`.

## Login

O acesso usa somente `ADMIN_USERNAME` e `ADMIN_PASSWORD`, definidos no ambiente antes de iniciar o servidor. A sessão administrativa é mantida no banco SQLite. Não coloque credenciais reais no GitHub nem compartilhe o terminal com pessoas não autorizadas.

## GitHub separado do site

Crie um repositório privado novo, por exemplo `unlockfacil-admin`, e envie **o conteúdo desta pasta** para ele. Não use o repositório do site estático da Netlify para este painel. O `.gitignore` exclui `.env`, banco SQLite, backups e ambientes virtuais.

Depois, no celular, clone este repositório em uma pasta própria, por exemplo `~/unlockfacil_admin`. Para atualizar futuramente:

```bash
cd ~/unlockfacil_admin
git pull
```

Antes de atualizar um painel já em uso, faça backup de `instance/unlockfacil.sqlite3` e pare o servidor. O banco fica local no celular; ele **não** é um banco hospedado em uma VPS externa. O banco é criado automaticamente no diretório `instance/`.

## Testes

```bash
source .venv/bin/activate
pytest -q
```

## Observações

- O lucro exibido é uma estimativa: vendas pagas menos despesas e anúncios cadastrados. Não é cálculo contábil/fiscal e não desconta taxas automaticamente.
- As credenciais das contas ficam no banco local; proteja o acesso ao celular e faça backups em local privado.
- Não copie `instance/`, `.env` ou backups para o GitHub.
