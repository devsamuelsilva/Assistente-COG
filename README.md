# Assistente do COG

Piloto local de consulta aos normativos do Centro de Operação da Geração. O programa roda neste PC, lê os normativos da pasta `acervo/`, encontra os trechos relevantes com busca local e pede ao GPT só a redação da resposta. Toda resposta cita o documento e a seção. Quando os trechos não sustentam a resposta, o assistente diz que o ponto não está no normativo e manda seguir o fluxo humano do COG.

O uso principal é o ambiente web neste PC, em `http://127.0.0.1:5173`. A integração com o Teams continua no código, mas é opcional e não é o caminho de teste atual.

## Como funciona

### Indexação (uma vez por mudança no acervo)

1. `app/extrair.py` lê cada PDF, DOCX, TXT ou MD de `acervo/`. O PDF é lido em modo de layout, e as colunas de tabela viram texto separado por ` | `.
2. Os metadados (código, revisão, título, vigência) vêm do arquivo `.meta.json` ao lado do normativo. Sem ele, o programa procura `Código:`, `Revisão:` e `Status:` no texto.
3. As imagens de conteúdo do PDF (quadros e tabelas em figura) são transcritas pelo GPT **uma única vez**. A transcrição fica gravada em `data/visao/` pelo hash da imagem. O logotipo que se repete em várias páginas é ignorado. Numa nova indexação, imagem que não mudou não gera nova chamada nem custo.
4. A transcrição entra no texto da página no fim da linha que diz "quadro a seguir", "tabela a seguir" ou "figura a seguir", sem partir a frase.
5. `app/chunking.py` corta o documento em trechos por seção numerada (por exemplo `5.1.1` ou `18.`), em janelas de até 2.000 caracteres com 200 de sobreposição. Cabeçalhos que se repetem em todas as páginas saem do texto. Página de sumário (5 ou mais linhas com pontilhado) vira um trecho "Sumário" e não abre seção, para não duplicar os nomes das seções verdadeiras.
6. `app/indice.py` calcula os embeddings no próprio PC e grava o índice em `data/indice/`. Essa etapa não usa a chave do GPT.

### Consulta (a cada pergunta)

1. A pergunta é completada com as perguntas anteriores do mesmo chat. Assim, um complemento curto como "com desligamento" ou "e de urgência" continua a pergunta anterior.
2. Um glossário local amplia siglas antes da busca. Hoje ele cobre WTG (aerogerador), SU (subestação unitária), seccionamento (desligamento, isolação, seccionadora) e urgências (urgência).
3. `app/buscar.py` combina busca por significado (embeddings) com busca por palavras (BM25) e escolhe os 6 melhores trechos. Depois acrescenta as partes seguintes da mesma seção e, quando a seção tem quadro transcrito, o trecho do quadro, mesmo que a busca tenha acertado outra parte da seção.
4. `app/rag.py` manda ao GPT só esses trechos, com um prompt de sistema rígido: responder somente com o que está escrito, não inferir, citar a seção e devolver JSON.
5. A resposta só é aceita como coberta se o GPT marcar coberto **e** citar uma seção que realmente estava entre os trechos enviados. Citação inventada vira recusa.
6. O resultado vai para o cache em `data/cache/` e para o registro em `data/logs/consultas.jsonl`.

### Cache

A mesma pergunta, no mesmo acervo e no mesmo contexto de conversa, sai do cache com custo zero. A chave do cache inclui o texto de todos os trechos indexados e o prompt de sistema. Portanto:

- reindexar com conteúdo diferente invalida as respostas antigas;
- mudar o prompt de sistema em `app/rag.py` invalida as respostas antigas;
- a mesma pergunta feita como continuação de outra conversa é uma consulta nova.

Resposta errada não fica "aprendida": corrigido o índice ou o prompt, a próxima consulta chama o GPT de novo.

## Preparar o PC

Na pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Abra o `.env` e preencha `OPENAI_API_KEY`. O modelo padrão é `gpt-4.1-mini`.

Para o ambiente web, instale o Node.js. A primeira execução de `scripts\web.ps1` roda `npm install`.

## Operação do dia a dia

Suba a API (porta 8080) numa janela:

```powershell
.\scripts\subir.ps1
```

Suba a tela (porta 5173) noutra janela:

```powershell
.\scripts\web.ps1
```

Abra `http://127.0.0.1:5173`.

Depois de mudar código Python, reinicie a API. Mudanças no front recarregam sozinhas enquanto `web.ps1` está no ar.

### Incluir ou atualizar um normativo

1. Na tela **Acervo**, solte o arquivo ou copie-o para `acervo/`.
2. Confira código, revisão, título e vigência no cartão e clique em **Salvar metadados**. Isso grava o `.meta.json` ao lado do arquivo.
3. Clique em **Indexar para a consulta**. Pelo terminal, o equivalente é `.\.venv\Scripts\python -m app indexar`.

Normativo com vigente desmarcado, ou com status substituído, revogado, obsoleto, cancelado ou não vigente, fica no índice mas não entra nas respostas. Enquanto houver um normativo real na pasta, o `exemplo-normativo.md` fica de fora.

### Corrigir a transcrição de um quadro

A transcrição automática pode errar em tabelas com células mescladas. Foi o caso do quadro de prazos da seção 5.1.1 da ITOM (página 11), corrigido à mão célula a célula. Para corrigir outro quadro:

1. Procure em `data/visao/` o JSON com o `arquivo` e a `pagina` do quadro.
2. Para pedir uma nova leitura ao GPT, apague esse JSON. Para corrigir à mão, edite o campo `texto`, com uma linha por célula no formato `Linha: … | Coluna: … | Valor: …`. Célula mesclada se repete em cada combinação que ela cobre.
3. Rode a indexação e reinicie a API.
4. Confira as seções (próximo tópico).

Mudar o pedido de transcrição em `app/visao.py` não refaz as transcrições já gravadas. Só o JSON apagado é lido de novo.

### Conferir as seções depois de indexar

A citação e a busca dependem do corte por seção. Um corte errado não dá erro na indexação: as respostas continuam saindo, mas citam a seção errada e misturam assuntos. Depois de incluir um normativo novo ou mexer em quadro:

1. Na tela **Acervo**, a pré-análise do documento lista as seções com página e número de trechos. Ela corta o texto do PDF **sem** os quadros transcritos, então mostra problemas de título no texto, mas não os causados pela inserção de um quadro.
2. Para ver as seções do índice que a consulta usa de fato, com os quadros, rode no terminal:

   ```powershell
   .\.venv\Scripts\python -c "import json; [print(t['pagina'], t['secao']) for t in json.load(open('data/indice/trechos.json', encoding='utf-8'))['trechos']]"
   ```

3. Confira a lista contra o sumário do PDF. Desconfie de uma seção com muitas partes (por exemplo "5.1.1 … (parte 30)"), de muitas partes seguidas com a mesma página ou de itens do sumário que não aparecem como seção. É sinal de que o corte parou de reconhecer os títulos a partir de algum ponto.
4. Rode a aba **Avaliação**: muitos cartões com "A seção citada não é a esperada" apontam para o mesmo problema.

Hoje a ITOM EGP 01 fica com 122 trechos: sumário, itens 1 a 20 com suas subseções e anexos 01 a 08, cada um com a página real. O acervo inteiro soma 139 trechos.

### Medir a acertividade (aba Avaliação)

A aba **Avaliação** roda um gabarito de perguntas com resposta conhecida pelo mesmo caminho do chat (glossário, busca, quadros, prompt e validação de citação) e compara cada resposta com a esperada. O número mede o sistema inteiro, não só o GPT. Nada é "treinado": quando uma pergunta erra, a correção é no índice, na transcrição de um quadro, no glossário ou no prompt, e a rodada seguinte mostra se melhorou.

Ciclo de uso:

1. Gere o gabarito no NotebookLM com o pedido abaixo e salve a resposta como `.json`.
2. Clique em **Importar gabarito** e escolha o arquivo. O gabarito anterior é substituído; as rodadas antigas continuam no gráfico. Se o arquivo tiver problema, a tela diz qual item corrigir (pergunta sem resposta, id repetido, JSON inválido).
3. Clique em **Rodar avaliação**. As perguntas rodam 4 por vez e a barra mostra o andamento. A primeira rodada após subir a API demora uns 15 segundos a mais para carregar o modelo de embeddings.
4. Veja a **acertividade por categoria** para achar o assunto mais fraco (clicar numa categoria filtra os cartões). Depois filtre por **Erro**, **Parcial**, **Recusa indevida** e **Invenção** e leia o diagnóstico de cada cartão.
5. Corrija a causa (veja a tabela de status), reindexe ou reinicie a API se for o caso, e rode de novo. O gráfico mostra a acertividade de cada rodada lado a lado.

O gabarito atual é `perguntas-itom-01.json`, gerado pelo NotebookLM: 200 perguntas da ITOM EGP 01 em 6 categorias (Definições e Conceitos, Prazos e SI-PS, Roteiro de Manobras, Documentos Vinculados, Segurança e LOTO, Responsabilidades). Ele não tem perguntas que devem ser recusadas, então o indicador **Invenções** fica em zero por falta de teste. Use o pedido complementar abaixo para acrescentá-las.

Uma rodada sem mudança no índice nem no prompt sai toda do cache, com custo zero. A rodada guarda a `versao` (assinatura do índice e do prompt): duas rodadas com a mesma versão testaram o mesmo sistema.

#### Formato do gabarito

Uma lista JSON (também aceita `{"perguntas": [...]}` e cercas de código ```` ```json ````):

```json
[
  {
    "id": "itom-001",
    "pergunta": "Qual o prazo de solicitação de uma SI de alta complexidade com desligamento, corretiva programada?",
    "resposta_esperada": "15 dias úteis.",
    "documento": "ITOM EGP 01",
    "secao": "5.1.1",
    "valores": ["15 dias úteis"],
    "deve_recusar": false
  },
  {
    "id": "itom-090",
    "pergunta": "Qual a cor do uniforme do operador do COG?",
    "resposta_esperada": "",
    "documento": "ITOM EGP 01",
    "secao": "",
    "valores": [],
    "deve_recusar": true
  }
]
```

| Campo | Obrigatório | Uso |
| --- | --- | --- |
| `id` | não | Identificador único. Sem ele, vira `q-001`, `q-002`… Id repetido recusa o arquivo. |
| `pergunta` | sim | Texto enviado ao assistente, como o operador escreveria. |
| `resposta_esperada` | sim, exceto recusa | Resposta correta segundo o normativo. É o que o juiz compara. |
| `documento` | não | Código do normativo. Só agrupa o resumo do gabarito. |
| `secao` | não | Seção que deve ser citada (`5.1.1` ou o nome da seção). `5.1` aceita `5.1.1`, mas não `5.10`. |
| `valores` | não | Termos que precisam aparecer na resposta, como prazos e nomes. A comparação ignora acento e maiúsculas. |
| `deve_recusar` | não | `true` quando o normativo não responde. O acerto é o assistente recusar. |

O formato que o NotebookLM costuma devolver também é aceito como vem: a lista pode estar dentro de qualquer chave (por exemplo `perguntas_e_respostas`), o `id` pode ser número, `resposta` vale como `resposta_esperada` e `categoria` é guardada para a acertividade por categoria. A `referencia` em texto livre vira seção e documento: "ITOM EGP 01, Item 2 e Item 18" vira documento `ITOM EGP 01` e seções `2; 18` (basta citar uma delas); "Tabela 5.1.1" vira `5.1.1`; "Anexos 03 e 04" vira `Anexo 03; Anexo 04`. Referência sem número, como "Folha de Rosto", fica sem conferência de seção.

#### Pedido pronto para o NotebookLM

Cole no NotebookLM com a ITOM 01 selecionada:

```text
Com base somente na ITOM EGP 01, gere um gabarito de avaliação para um assistente que responde dúvidas dos operadores do COG.

Gere 100 perguntas variadas cobrindo: prazos de solicitação de SI por complexidade e natureza (inclusive urgência e emergência), classificação de complexidade, escolha da natureza, responsáveis por cada etapa, fluxo de solicitação, emissão e liberação da SI-PS, documentos exigidos e exceções. Escreva como um operador escreveria no chat: frases curtas, informais, algumas com siglas (SI, WTG, SU). Inclua umas 10 perguntas que sejam continuação curta de uma pergunta anterior, reescritas de forma completa para fazer sentido sozinhas.

Gere também 10 perguntas plausíveis para o COG que a ITOM 01 NÃO responde, com "deve_recusar": true e "resposta_esperada" vazia.

Para cada pergunta, a "resposta_esperada" deve ser curta e literal ao normativo (prazo, valor, nome ou lista exata), "secao" deve ser o número da seção onde está a resposta (por exemplo "5.1.1") e "valores" deve listar os termos que obrigatoriamente aparecem numa resposta correta (prazos com unidade, nomes de natureza, cargos).

Responda somente com o JSON, sem texto antes ou depois, neste formato:
[{"id": "itom-001", "pergunta": "...", "resposta_esperada": "...", "documento": "ITOM EGP 01", "secao": "...", "valores": ["..."], "deve_recusar": false}]
```

Revise o JSON antes de importar: o NotebookLM também erra, principalmente em quadros. Um gabarito errado derruba a acertividade sem culpa do assistente.

#### Pedido complementar: perguntas que devem ser recusadas

Para medir invenção num gabarito que já existe, peça ao NotebookLM:

```text
Gere 15 perguntas plausíveis para operadores do COG que a ITOM EGP 01 NÃO responde (por exemplo, assuntos de outros normativos, valores que a ITOM não define ou procedimentos que ela não cobre). Escreva como um operador escreveria no chat.

Responda somente com o JSON, sem texto antes ou depois, neste formato:
[{"id": "recusa-001", "categoria": "Fora da ITOM", "pergunta": "...", "resposta": "", "referencia": "", "deve_recusar": true}]
```

Cole os itens gerados dentro da lista `perguntas_e_respostas` do arquivo atual, antes do `]` final, separados por vírgula, e importe de novo. Os ids `recusa-…` não colidem com os números do gabarito atual.

#### Como cada pergunta é julgada

A comparação é híbrida. Primeiro vêm as regras objetivas: recusa correta, seção citada igual à esperada e todos os `valores` presentes. Depois um juiz GPT compara a resposta com a esperada e devolve `correto`, `parcial` ou `incorreto` com o motivo. O veredito do juiz fica em `data/avaliacao/juiz/`, então a mesma resposta não é julgada duas vezes.

| Status | Quando | Onde costuma estar a correção |
| --- | --- | --- |
| Acerto | Juiz disse correto e as regras conferem; ou recusou quando devia. | — |
| Parcial | Juiz disse parcial, ou disse correto mas a seção citada ou algum valor não confere. | Prompt (resposta incompleta), corte de seção errado (veja "Conferir as seções") ou gabarito com `valores` ou `referencia` imprecisos. |
| Erro | Juiz disse incorreto. | Transcrição do quadro, trecho cortado no lugar errado ou prompt. |
| Recusa indevida | O assistente recusou, mas o gabarito tem resposta. O cartão diz se a seção esperada chegou a ser recuperada. | Seção não recuperada: glossário ou busca. Seção recuperada: prompt rígido demais ou quadro mal transcrito. |
| Invenção | O assistente respondeu uma pergunta que devia recusar. | Prompt. É o status mais grave para a confiança. |
| Falha | Erro técnico na pergunta (rede, chave, índice). Não entra no cálculo. | Terminal da API. |

A acertividade é acertos divididos pelas perguntas sem falha. Os cartões de invenção e erro são os que valem apresentar primeiro para a coordenação, junto com a evolução no gráfico.

#### Custo

Cada pergunta sem cache custa uma resposta e um julgamento, em torno de US$ 0,003 a 0,005 com o `gpt-4.1-mini`. A rodada das 200 perguntas atuais sai por volta de US$ 0,60 a 1,00 e leva alguns minutos; uma rodada repetida sem mudança sai de graça. Reindexar ou mudar o prompt invalida o cache, e a rodada seguinte paga de novo. O custo aparece no cartão **Custo da rodada** e também na aba **Uso**. As perguntas da avaliação ficam no registro com `origem: "avaliacao"` e não aparecem no Histórico nem na nuvem de perguntas.

### Perguntar pelo terminal

```powershell
.\.venv\Scripts\python -m app perguntar "qual o prazo de solicitação para alta complexidade com desligamento?"
```

### Rodar os testes

```powershell
.\.venv\Scripts\python -m unittest discover -s tests
```

São 29 testes. O `test_documento_real_substitui_o_exemplo` falha enquanto houver normativos reais em `acervo/`, porque ele espera só o arquivo de exemplo. Os demais usam pastas temporárias e passam.

## Registro de correções

Problemas achados no uso do piloto e como foram resolvidos. Serve de histórico para a coordenação e de checklist quando entrar um normativo novo.

| Problema | Causa | Correção |
| --- | --- | --- |
| Pergunta de natureza da SI (alta complexidade, com desligamento, programada) recusada. | O quadro de prazos da 5.1.1 estava numa parte da seção que a busca não trazia, e a transcrição automática desalinhou as células mescladas. | A busca passou a incluir o trecho do quadro da mesma seção. O quadro da página 11 foi transcrito à mão, célula a célula. |
| Prazo de urgência respondido como 15 dias. | Transcrição do quadro e pergunta com "urgências" no plural. | Coluna de urgência corrigida para "Inferior a 2 dias úteis"; glossário com urgências; regra no prompt para responder só a célula da coluna pedida. |
| Quase toda a ITOM citada como 5.1.1. | O quadro da página 11 era inserido no meio da frase "quadro a seguir, de acordo com a…". O marcador de fim ficou colado no resto da frase, e o corte achou que o quadro não terminava, ignorando todos os títulos seguintes (5.2 a 20 e anexos). | O quadro passou a entrar no fim da linha, e o corte reconhece o fim do quadro mesmo com texto colado. A 5.1.1 caiu de 44 para 11 partes. |
| Seções duplicadas com pontilhado ("5.1 UTILIZAÇÃO DA SI-ps ......"). | As linhas do sumário eram lidas como títulos. | Página de sumário vira um trecho "Sumário" e não abre seção. |
| Gabarito do NotebookLM recusado na importação. | O arquivo usa `perguntas_e_respostas`, `resposta` e `referencia` em texto livre. | A importação aceita esse formato como vem. |

## Estrutura de pastas e arquivos

### Raiz

| Arquivo | Função |
| --- | --- |
| `README.md` | Este documento. |
| `requirements.txt` | Dependências Python da API, da leitura de documentos, da busca local e do cliente OpenAI. |
| `.env.example` | Modelo do `.env`, com todas as variáveis comentadas. |
| `.env` | Chave do GPT, modelo, pastas, porta e segredos do Teams. Fica fora do git. |
| `.gitignore` | Deixa fora do git o `.env`, o ambiente `.venv`, a pasta `data/`, os normativos reais e os artefatos do front. |

### `acervo/` — normativos

Pasta lida pela indexação. Os normativos reais ficam fora do git. Só o arquivo de exemplo é versionado.

| Arquivo | Função |
| --- | --- |
| `exemplo-normativo.md` | Normativo fictício (IT-EXEMPLO-001) para testar o assistente sem documento real. Sai do índice quando existe um normativo real. |
| `ITOM EGP 01 Rev.02 (1).pdf` | ITOM 01, revisão 02: solicitação, emissão e liberação de SI-PS, prazos por complexidade e natureza, MO, AI, ATEIE, bloqueio de energias, roteiro de manobras, liberação e retorno à operação, responsabilidades e anexos 01 a 08. Tem 9 imagens de conteúdo transcritas em `data/visao/` (o quadro de prazos da página 11 e os formulários dos anexos). |
| `NO 01 - Pós Operação.pdf` | NO-01, revisão 00: pós-operação, triagem de ocorrências e critérios de RAO/RAI. |
| `*.pdf.meta.json` | Metadados de cada normativo: `codigo`, `revisao`, `titulo` e `vigente`. Gravados pela tela Acervo. |
| `NO 01 - Pos Operacao.pdf.meta.json.txt` | Cópia solta de metadados salva com extensão `.txt`. Por ser `.txt`, entra na leitura como documento. Pode ser apagada. |

### `app/` — núcleo em Python

| Arquivo | Função |
| --- | --- |
| `__init__.py` | Marca o pacote e guarda a versão. |
| `__main__.py` | Linha de comando: `python -m app indexar`, `perguntar "…"` e `servir`. |
| `config.py` | Lê o `.env` e define chave, modelo, pastas, host, porta, modelo de embeddings, quantidade de trechos (`TOP_K`) e o aviso ao operador. Remove `OPENAI_BASE_URL` vazio para o SDK usar o endereço padrão. |
| `extrair.py` | Lê PDF (modo layout), DOCX, TXT e MD, página a página. Resolve os metadados. Lista as imagens de conteúdo do PDF, ignorando o logotipo que aparece em 3 ou mais páginas, e insere a transcrição do quadro no fim da linha que o anuncia ("quadro a seguir"), entre os marcadores de início e fim da figura. |
| `visao.py` | Transcreve cada imagem de conteúdo com o GPT, uma vez, célula a célula. Grava em `data/visao/{hash}.json` e reaproveita nas indexações seguintes. Imagem sem texto útil fica gravada como `SEM_CONTEUDO` e não entra no índice. |
| `chunking.py` | Corta o documento em trechos por seção. Item numerado com título em maiúscula abre seção; linha que começa com número mas é conteúdo (por exemplo "7 dias úteis") não abre. O quadro transcrito fica dentro da seção que o anuncia, entre "Transcrição da figura da página N" e "Fim da transcrição da figura"; dentro do quadro nenhuma linha abre seção. Página de sumário vira um trecho só. |
| `indice.py` | Monta o índice: extrai, transcreve os quadros, fatia, calcula embeddings locais e grava `data/indice/`. Também calcula a impressão digital do acervo (para avisar que o índice ficou desatualizado) e a assinatura do conteúdo usada no cache. |
| `buscar.py` | Busca híbrida (embeddings + BM25, fundidos por RRF), glossário de siglas e expansão para as partes vizinhas da seção e para o trecho do quadro da mesma seção. Decide se há base mínima para chamar o GPT. |
| `rag.py` | Prompt de sistema, montagem do pedido com trechos e conversa, chamada ao GPT (temperatura 0, resposta em JSON), validação das citações, cache e registro de cada consulta. |
| `cache.py` | Chave e leitura/gravação das respostas em `data/cache/`. |
| `registro.py` | Acrescenta cada consulta em `data/logs/consultas.jsonl`, com data, autor, origem, pergunta, resposta, citações, trechos e tokens. A gravação tem trava para as perguntas paralelas da avaliação. |
| `uso.py` | Lê o registro e calcula consultas, chamadas cobradas, cache, tokens e custo estimado em dólar pela tabela pública do `gpt-4.1-mini` (US$ 0,40 por milhão de tokens de entrada e US$ 1,60 na saída). A função `custo_usd` também é usada pela avaliação. |
| `avaliacao.py` | Avaliação da acertividade: importa e valida o gabarito (no formato próprio ou no do NotebookLM, convertendo a `referencia` em seções), roda as perguntas 4 por vez pelo mesmo `responder` do chat, aplica as regras objetivas e o juiz GPT (com cache), classifica cada pergunta, calcula a acertividade geral e por categoria e grava a rodada em `data/avaliacao/rodadas/`. Guarda o andamento da rodada em curso para a tela. |
| `painel.py` | Regras do ambiente web: pré-análise dos arquivos (páginas, seções, trechos, avisos; sem os quadros transcritos), envio com nome seguro e limite de 25 MB, gravação de metadados, remoção e consulta pelo chat. |
| `server.py` | API FastAPI em `127.0.0.1:8080`. Rotas `/api/acervo` (listar, enviar, remover), `/api/acervo/meta`, `/api/indexar`, `/api/chat` (aceita até 8 turnos de conversa), `/api/uso`, `/api/avaliacao` (estado, `gabarito`, `rodar` e `rodadas/{id}`), `/saude` e `/teams/outgoing`. Carrega o modelo de embeddings ao subir. |
| `teams.py` | Integração opcional com o Teams: confere a assinatura HMAC do outgoing webhook, limpa a menção e publica a resposta no incoming webhook. |

### `web/` — ambiente web (React + Vite)

| Arquivo | Função |
| --- | --- |
| `index.html` | Página base que carrega o React. |
| `package.json` / `package-lock.json` | Dependências do front (React 19, Vite 6) e comandos `dev`, `build` e `preview`. |
| `vite.config.js` | Servidor de desenvolvimento em `127.0.0.1:5173`, com as chamadas `/api` repassadas para a API na 8080. |
| `src/main.jsx` | Ponto de entrada do React. |
| `src/App.jsx` | Topo e abas **Acervo**, **Consulta**, **Histórico**, **Uso** e **Avaliação**. Na Consulta, a página ocupa a altura da tela para a conversa rolar por dentro. |
| `src/api.js` | Funções que chamam a API: acervo, metadados, remoção, indexação, chat (com o histórico da conversa), uso e avaliação (estado, envio do gabarito, rodar e ler rodada). |
| `src/registro.js` | Hook que carrega o registro de uso, compartilhado por Consulta, Histórico e Uso. |
| `src/Acervo.jsx` | Envio de arquivos, pré-análise por documento (páginas, seções, avisos), edição de metadados, remoção e botão de indexar. |
| `src/Chat.jsx` | Chat de consulta. Envia as 4 últimas trocas como contexto, rola até a resposta mais nova ao responder e, com o chat vazio, mostra a nuvem das perguntas já respondidas (sem as da avaliação). |
| `src/Nuvem.jsx` | Nuvem de até 16 perguntas anteriores que tiveram resposta coberta. Cada pergunta flutua devagar e aparece inteira. Clicar faz a pergunta de novo. Recusas não entram. |
| `src/Leitura.jsx` | Mostra a resposta com listas `a)`, `b)`, `i)`, `1)` separadas em linhas. |
| `src/Historico.jsx` | Últimas 10 perguntas agrupadas pelo documento citado, com busca por texto da pergunta ou da resposta. Ignora as perguntas da avaliação. |
| `src/Uso.jsx` | Números de uso e custo, gráfico de custo por dia e barras de custo por documento. |
| `src/Avaliacao.jsx` | Aba Avaliação: importar gabarito, rodar, barra de andamento, gráfico de acertividade por rodada (clicar na barra abre a rodada), indicadores, acertividade por categoria (clicar filtra), filtro por status e cartões com esperada, obtida, citação, motivo do juiz e diagnóstico. |
| `src/formato.js` | Funções de apoio: quebra da resposta em blocos de leitura, formatação de dólar, número e data, agrupamento por documento e séries de custo. |
| `src/styles.css` | Estilo das telas, da conversa com rolagem interna e da nuvem flutuante. O movimento para quem pede menos animação no sistema. |

### `scripts/` — atalhos do Windows

| Arquivo | Função |
| --- | --- |
| `subir.ps1` | Sobe a API com o Python do `.venv`. |
| `web.ps1` | Instala as dependências do front na primeira vez e sobe a tela. |
| `tunel-cloudflare.ps1` | Abre um túnel Cloudflare até a API. Só serve para o Teams. |
| `cloudflared.config.example.yml` | Modelo da configuração do túnel. A cópia preenchida (`cloudflared.yml`) fica fora do git. |

### `tests/` — testes automáticos

| Arquivo | O que confere |
| --- | --- |
| `test_acervo.py` | Leitura, metadados e troca do exemplo pelo normativo real; corte por item numerado sem cabeçalho repetido; colunas do layout; linha de prazo que não vira seção; quadro dentro da seção; filtro do logotipo; transcrição que não é pedida duas vezes; glossário; inclusão do trecho do quadro quando a busca acerta outra parte da seção; quadro anunciado no meio da frase que não engole as seções seguintes; página de sumário num trecho só. |
| `test_rag.py` | Resposta com citação válida, recusa de citação inventada e continuação de conversa que não reaproveita o cache da pergunta isolada. |
| `test_painel.py` | Envio, pré-análise, metadados, remoção e recusa de nome fora do acervo. |
| `test_uso.py` | Consulta em cache fora do custo e lista da mais nova para a mais antiga. |
| `test_avaliacao.py` | Rodada com gerador e juiz falsos que produz todos os status; segunda rodada inteira do cache, sem chamada e sem custo; validação do gabarito importado; formato do NotebookLM (lista dentro de outra chave, `resposta`, `referencia`, `categoria`); conferência da seção citada, inclusive com várias seções e anexos. |
| `test_teams.py` / `test_webhook.py` | Assinatura HMAC, limpeza da menção, formatos de cartão e recusa de chamada sem assinatura. |

### `data/` — gerado neste PC (fora do git)

| Pasta | Conteúdo |
| --- | --- |
| `data/indice/` | `trechos.json` (texto e metadados de cada trecho: arquivo, código, revisão, vigência, seção e página) e `vetores.npy` (embeddings). Refeito a cada indexação. |
| `data/visao/` | Uma transcrição por imagem de conteúdo, pelo hash. É o que evita pagar a leitura da imagem de novo. |
| `data/cache/` | Respostas já geradas, uma por arquivo. Pode ser apagada; a próxima pergunta chama o GPT de novo. |
| `data/logs/consultas.jsonl` | Registro de todas as consultas. Alimenta Histórico, Uso e a nuvem de perguntas. As da avaliação têm `origem: "avaliacao"`. |
| `data/avaliacao/` | `gabarito.json` (o último importado), `rodadas/` (uma rodada por arquivo, com o resultado de cada pergunta) e `juiz/` (vereditos do juiz em cache). Apagar `rodadas/` zera o gráfico. |
| `data/modelos/` | Modelo local de embeddings (cerca de 100 MB), baixado na primeira indexação. |

## Canal do Teams (opcional)

O Teams exige um endereço HTTPS público. O outgoing webhook não existe em canal privado. Para usar mesmo assim:

1. Suba a API com `.\scripts\subir.ps1`.
2. Abra um túnel com hostname fixo até `http://127.0.0.1:8080`: copie `scripts\cloudflared.config.example.yml` para `scripts\cloudflared.yml`, preencha id do túnel, credenciais e hostname, e rode `.\scripts\tunel-cloudflare.ps1`.
3. No canal, crie um outgoing webhook apontando para `https://seu-hostname/teams/outgoing` e copie o token para `TEAMS_OUTGOING_HMAC_SECRET`.
4. Crie um incoming webhook (ou o fluxo "Post to a channel when a webhook request is received") e copie a URL para `TEAMS_INCOMING_WEBHOOK_URL`. `TEAMS_POST_FORMAT` aceita `adaptive`, `messagecard` ou `texto`.
5. Reinicie a API. No canal, mencione o webhook com a pergunta.

## Limites deste piloto

- Só responde com este PC ligado e a API no ar. Tudo escuta em `127.0.0.1`.
- A citação aponta o arquivo local e a seção, não um link do SharePoint.
- Quadro em imagem depende da transcrição gravada em `data/visao/`. Confira os quadros críticos contra o PDF.
- A acertividade da aba Avaliação vale para o gabarito usado. Ela é tão boa quanto o gabarito: revise as respostas esperadas antes de apresentar o número.
- A resposta é apoio à consulta. A execução continua sob a responsabilidade do operador e do normativo oficial.
