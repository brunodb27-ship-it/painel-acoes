# Painel de Ações

Site com os indicadores fundamentalistas de todas as ações da B3, histórico de até 20 anos
e simulador de investimento com dividendos reinvestidos. Atualiza sozinho todo dia útil.

**Endereço do site:** https://brunodb27-ship-it.github.io/painel-acoes/

## Como funciona

- Todo dia útil, às 19h30 (Brasília), o GitHub roda `pipeline/atualizar.py`, que:
  - baixa os balanços da CVM (DFP e ITR) e calcula P/L, P/VP, ROE, margens, dívida etc.;
  - busca cotações, proventos e histórico mensal (Yahoo Finance);
  - busca CDI e IPCA (Banco Central) e o Ibovespa, para o simulador comparar;
  - salva tudo em `docs/data/`.
- O site (`docs/index.html`) só lê esses arquivos. Não tem servidor nem mensalidade.

## Configuração (uma vez só)

1. **Subir os arquivos:** no repositório, abra **Add file → Upload files**, arraste o
   conteúdo da pasta e toque em **Commit changes**.
   - Confira se a pasta `.github/workflows` subiu. Se não aparecer, toque em
     **Add file → Create new file**, digite o nome `.github/workflows/atualizar.yml`,
     cole o conteúdo do arquivo de mesmo nome e salve.
2. **Ligar o site:** **Settings → Pages**. Em *Branch*, escolha `main` e a pasta `/docs`,
   depois toque em **Save**. Em 1 ou 2 minutos o site está no ar no endereço acima.
3. **Primeira atualização:** aba **Actions** → **Atualizar dados** → **Run workflow**.
   Leva uns 10 minutos. Depois disso, ela roda sozinha.

## Se algo der errado

Na aba **Actions**, a execução com X vermelho mostra o erro. Tire um print e mande para o Claude.
