# Assistente da Operação (COG)

Piloto local de consulta aos normativos do Centro de Operação da Geração. O programa roda neste PC, lê os normativos da pasta `acervo/`, encontra os trechos relevantes com busca local e pede ao GPT só a redação da resposta. Toda resposta cita o documento e a seção. Quando os trechos não sustentam a resposta, o assistente diz que o ponto não está no normativo e manda seguir o fluxo humano do COG.

O uso principal é o ambiente web neste PC, em `http://127.0.0.1:5173`, com o nome **Assistente da Operação**. A integração com o Teams continua no código, mas é opcional e não é o caminho de teste atual.

A tela **Consulta** tem dois chats: **Operacional** ("Consulta aos Documentos Operacionais Energimp"), que responde pelos normativos do acervo, e **ONS** ("Dados abertos do ONS"), que responde com os dados abertos do ONS (carga, geração, constrained-off, CMO, reservatórios) pelo servidor MCP oficial do ONS. As conversas são separadas e não se misturam.

## Como funciona

### Indexação (uma vez por mudança no acervo)

1. `app/extrair.py` lê cada PDF, DOCX, TXT ou MD de `acervo/`. O PDF é lido em modo de layout, e as colunas de tabela viram texto separado por ` | `.
2. Os metadados (código, revisão, título, vigência) vêm do arquivo `.meta.json` ao lado do normativo. Sem ele, o programa procura `Código:`, `Revisão:` e `Status:` no texto.
3. As imagens de conteúdo do PDF (quadros e tabelas em figura) são transcritas pelo GPT **uma única vez**. A transcrição fica gravada em `data/visao/` pelo hash da imagem. O logotipo que se repete em várias páginas é ignorado. Numa nova indexação, imagem que não mudou não gera nova chamada nem custo.
4. A transcrição entra no texto da página no fim da linha que diz "quadro a seguir", "tabela a seguir" ou "figura a seguir", sem partir a frase.
5. `app/chunking.py` corta o documento em trechos por seção numerada (por exemplo `5.1.1` ou `18.`), em janelas de até 2.000 caracteres com 200 de sobreposição. Cabeçalhos que se repetem em todas as páginas saem do texto. Página de sumário (5 ou mais linhas com pontilhado) vira um trecho "Sumário" e não abre seção, para não duplicar os nomes das seções verdadeiras.
6. `app/indice.py` calcula os embeddings no próprio PC com o `google/embeddinggemma-300m` e grava o índice em `data/indice/`. Essa etapa não usa a chave do GPT.
7. A indexação confere o sumário do PDF contra as seções que viraram trecho. Se um número do sumário (por exemplo `17`) não virou seção, a indexação e a tela **Acervo** avisam: o título ficou colado na seção anterior.

### Consulta (a cada pergunta)

1. **Planejador.** O GPT barato (`AUX_MODEL`) entende a pergunta antes da busca e devolve três coisas: a pergunta completa e independente ("e de urgência?" vira a pergunta inteira), o tipo (**geral**, sobre o documento como um todo, ou **específica**) e até 3 reformulações com o vocabulário que o normativo usaria (finalidade → objetivo, quem pode → responsabilidades). Isso ataca a causa mais comum de "não sei" com a resposta no documento: a pessoa e o normativo usam palavras diferentes. O plano fica em `data/reescrita/`, então repetir a pergunta não repete a chamada. Sem chave, com falha ou com `REESCREVER=off`, a busca segue sem plano (soma das perguntas anteriores, glossário e a regra de pergunta geral por palavras).
2. Um glossário local amplia siglas antes da busca. Hoje ele cobre WTG (aerogerador), SU (subestação unitária), seccionamento (desligamento, isolação, seccionadora) e urgências (urgência).
3. `app/buscar.py` combina busca por significado (embeddings) com busca por palavras (BM25) e escolhe os 6 melhores trechos. Depois acrescenta as partes seguintes da mesma seção e, quando a seção tem quadro transcrito, o trecho do quadro, mesmo que a busca tenha acertado outra parte da seção. Com `MODO_CONTEXTO=secao`, manda a seção inteira de cada acerto; com `documento`, o documento inteiro do primeiro acerto. Com `RERANKER_MODEL` preenchido, um reordenador local relê os 12 melhores candidatos antes de escolher os 6. Cada reformulação do planejador também busca, e o que ela traz de novo entra depois (até 4 trechos a mais). Se a pergunta é **geral** e cita um documento, a visão geral e as seções de objetivo, escopo ou introdução desse documento vão na frente do contexto.
4. Se nenhum trecho tem palavra da pergunta (fora artigos, preposições e cumprimentos) e as notas da busca ficam abaixo do mínimo (cosseno 0,28 ou BM25 8), a pergunta é recusada ali mesmo, sem chamar o GPT e sem ir para o cache. Na calibração, as 200 perguntas do gabarito passaram e nenhuma das 18 perguntas fora do assunto passou.
5. Quando o quadro de uma seção está entre os trechos, `app/quadro.py` lê as células `Linha | Coluna | Valor` da transcrição e escolhe a linha e a coluna pelos termos da pergunta (alta, média, baixa, com ou sem desligamento, corretiva, preventiva, urgência…). As células escolhidas vão no pedido como "Consulta exata ao quadro" e saem dos trechos para não irem duas vezes. Se a pergunta cita só a coluna, vai a coluna inteira; se não cita nenhuma das duas, nada é acrescentado. Essa escolha é local, sem chamada extra ao GPT.
6. `app/rag.py` manda ao GPT só esses trechos, numerados `[n]`. O pedido vai nesta ordem: trechos agrupados por documento (um cabeçalho com arquivo, código e revisão por documento, e sem o texto repetido entre partes seguidas da mesma seção), a conversa do chat (as respostas anteriores resumidas em 300 caracteres), a consulta exata ao quadro, a pergunta completa reescrita (se houve) e, no fim, a pergunta atual. A resposta tem no máximo 800 tokens.
7. **Prompt v2 (padrão, `PROMPT_VERSAO=v2`).** O prompt é curto, com três exemplos, e a saída é estruturada (`json_schema`; se o modelo não aceitar, cai para `json_object`). O GPT devolve um de três estados: **coberto**, **parcial** (responde só o que os trechos sustentam e diz o que falta; o chat mostra "Resposta parcial") ou **ausente** (explica o que não achou). Ele cita pelo **número do trecho**: número que não existe vira recusa. As regras do quadro (consulta exata), da visão geral e da linha "Dentro de:" continuam no prompt. `PROMPT_VERSAO=v1` volta ao prompt rígido anterior (coberto ou não, citação por nome de seção). Motivo da mudança: na prova de 400 perguntas de 08/10/2026 (73,5%), 51 dos 106 erros eram recusas com a seção certa em mãos e 38 eram respostas incompletas; só 10 vinham da busca.
   - **Conferência dos valores.** Todo número da resposta (prazo, valor) é procurado nos trechos citados e na consulta exata. Se estiver em outro trecho enviado, esse trecho também é citado. Se não estiver em nenhum, a resposta sai com "Atenção: o valor X não foi encontrado nos trechos". Número de lista ("1.", "2)") não conta.
   - **Resposta ausente.** A explicação do modelo ("os trechos não informam X") é mantida, seguida das seções relacionadas.
8. O resultado vai para o cache em `data/cache/` e para o registro em `data/logs/consultas.jsonl` (sem o texto dos trechos, para o registro não crescer). No chat, o operador marca a resposta como **Útil** ou **Não útil**, com um comentário opcional. O retorno fica em `data/logs/retornos.jsonl` e aparece no **Histórico**, que filtra as respostas marcadas como não úteis.

Medida em 30/09/2026: pergunta nova em 6 s (busca de 24 a 46 ms, o resto é o GPT), 5.730 tokens de entrada e 149 de saída, cerca de US$ 0,0025; a mesma pergunta de novo sai do cache em 0,5 s; pergunta fora do assunto é recusada em 0,4 s.

### Cache

A mesma pergunta, no mesmo acervo e no mesmo contexto de conversa, sai do cache com custo zero. A chave do cache é o modelo mais o pedido exato que iria ao GPT: prompt de sistema, trechos escolhidos, conversa, consulta ao quadro e pergunta. Portanto:

- reindexar com conteúdo diferente invalida as respostas antigas;
- mudar o prompt de sistema em `app/rag.py` (ou `PROMPT_VERSAO`) invalida as respostas antigas;
- a mesma pergunta feita como continuação de outra conversa é uma consulta nova.

Não vão para o cache a resposta cortada pelo limite de tokens, a resposta que não veio em JSON e a pergunta recusada antes do GPT. Resposta errada não fica "aprendida": corrigido o índice ou o prompt, a próxima consulta chama o GPT de novo.

### Chat ONS (dados abertos do ONS)

O ONS publica um servidor MCP público, o **TIAGO Dados Abertos** ([dados.ons.org.br/mcp](https://dados.ons.org.br/mcp), código em [github.com/ONSBR/TIAGO-Dados-Abertos](https://github.com/ONSBR/TIAGO-Dados-Abertos)), no endereço `https://mcp.dados.tiago.ons.org.br/`, sem login. O servidor não tem IA: ele entrega o catálogo de cerca de 80 datasets e o contrato de cada um (colunas, conversões, unidades), valida o SQL e executa só leitura sobre os arquivos públicos do ONS. Quem escolhe o dataset, escreve o SQL e redige a resposta é o nosso GPT.

1. `app/ons.py` fala com o servidor por JSON-RPC em HTTP (`initialize` e `tools/call`), aceitando resposta em JSON ou em SSE.
2. O catálogo (`listar_datasets`) fica guardado em `data/ons/catalogo.txt` por 24 horas, enxuto (só nome e descrição curta), e vai fixo no prompt. Isso poupa uma chamada por pergunta. O contrato de cada dataset (`descrever_dataset`) também fica guardado por 24 horas em `data/ons/contratos/` e vai ao GPT compactado: colunas, unidades e instruções repetidas agrupadas (de 16,9 mil para 9 mil caracteres no contrato de constrained-off). Junto com o catálogo, o programa busca no cadastro `capacidade-geracao` as usinas em que o agente do `ONS_AGENTE` (padrão ENERGIMP) é operador ou proprietário, com o conjunto de cada uma, e põe a lista no prompt, também guardada por 24 horas. Assim, "a ENERGIMP", "nossas usinas" e "nossos conjuntos" viram os códigos das usinas (CEG) e dos conjuntos (`id_ons`), porque o nome da empresa não aparece no nome das usinas. Hoje são 13 usinas eólicas em três conjuntos: Água Doce e Bom Jardim, em SC, e Papagaios, no CE.
3. O GPT recebe a pergunta, a data de hoje e 4 ferramentas: `listar_datasets`, `buscar_dataset`, `descrever_dataset` e `executar_sql`. O programa executa as chamadas no servidor do ONS (até 4 ao mesmo tempo quando o GPT pede várias num passo) e devolve os resultados ao GPT, até 6 rodadas (`ONS_MAX_PASSOS`). O `executar_sql` vai com no máximo 50 linhas, e cada retorno é cortado em 12 mil caracteres. Se o servidor perde a sessão (404), o programa abre outra e repete a chamada uma vez.
4. Antes de mandar o SQL, o programa troca a leitura de todos os arquivos parquet do dataset só pelos arquivos do período pedido no `WHERE` (a lista de arquivos do S3 público do ONS fica guardada por 6 horas em `data/ons/arquivos/`). Se o período não é claro ou algum arquivo não tem data no nome, o SQL vai como veio; se o SQL trocado der erro, o original roda em seguida. A consulta registrada e mostrada é sempre a original.
5. O programa corrige dois tropeços do ONS sem depender do GPT:
   - mês ainda não fechado aparece como `0.0` nos datasets mensais. Resultado vazio, nulo ou zerado ganha um aviso para trocar pelo dataset de grão menor (diário, horário), e o programa pede uma nova tentativa antes de aceitar "não encontrado";
   - erro de SQL volta como texto começando com `[ERRO]`. Ele não conta como fonte, e o erro de conversão de texto vazio (recomendado por engano em alguns contratos) ganha a dica de usar só `TRY_CAST`.
6. A resposta mostra o texto, a fonte (dataset com link para o portal), a unidade de cada coluna e o SQL executado num bloco recolhido. Só conta como fonte a consulta que trouxe valor.
7. A resposta vai para o cache pela pergunta, pela conversa e pela **data de hoje**, porque o ONS revisa os períodos recentes; no dia seguinte a mesma pergunta consulta de novo. Resposta sem fonte não vai para o cache. Vai também para o registro com `origem: "ons"`, entra no **Uso** e aparece no **Histórico** agrupada como "ONS Dados Abertos".

Custo e tempo: cada passo reenvia ao GPT o catálogo, os contratos lidos e os resultados anteriores. A maior parte da entrada se repete de um passo para o outro, e a OpenAI cobra essa parte a US$ 0,10 por milhão em vez de US$ 0,40. Antes do catálogo enxuto, dos contratos guardados e do SQL por período, uma pergunta nova somava de 20 mil a 115 mil tokens de entrada, levava de 20 segundos a mais de um minuto e custava de US$ 0,004 a 0,015. Depois, a pergunta de constrained-off de agosto levou 11,9 s em 3 passos, com 18,5 mil tokens de entrada (11,1 mil reaproveitados), cerca de US$ 0,0044; o SQL leu um só arquivo mensal em 1,9 s. A tela **Uso** mostra, para cada pergunta ONS, o tempo total, o tempo no GPT e no ONS, os passos, os tokens reaproveitados e as repetições automáticas da OpenAI. As perguntas do chat ONS saem do PC: o SQL vai para o servidor do ONS e o pedido vai para a OpenAI. Os dados consultados são públicos (CC-BY) e nada do acervo é enviado.

## Preparar o PC

Na pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Abra o `.env` e preencha `OPENAI_API_KEY`. O modelo padrão é `gpt-4.1-mini`. A primeira indexação baixa o modelo de embeddings (cerca de 1,2 GB) para `data/modelos/`; depois tudo roda sem internet, exceto as chamadas ao GPT e o chat ONS. Trocar `EMBED_MODEL` exige reindexar. O chat ONS funciona sem configuração extra; `ONS_MCP_URL` e `ONS_MAX_PASSOS` (padrão 6) só precisam mudar se o ONS trocar o endereço ou se quiser outro limite de rodadas. `ONS_AGENTE` (padrão ENERGIMP) é o agente do cadastro do ONS que o chat entende como "a empresa"; vazio desliga a lista de usinas. `JUIZ_MODEL` escolhe o modelo que julga as respostas na avaliação; vazio, usa o mesmo `OPENAI_MODEL`.

Para o ambiente web, instale o Node.js. A primeira execução de `scripts\web.ps1` roda `npm ci`, que instala exatamente as versões do `package-lock.json`.

Para desenvolver, `requirements-dev.txt` acrescenta o `ruff`. As checagens são `.\.venv\Scripts\python -m ruff check app tests` e, na pasta `web`, `npm run lint` (ESLint com as regras de hooks do React).

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

1. Na tela **Acervo**, solte o arquivo. O envio já dispara a preparação em segundo plano: indexação e treino do documento novo (veja "Treinamento automático").
2. No cartão do documento, clique em **Editar dados**, confira código, revisão, título e vigência e clique em **Salvar dados**. Isso grava o `.meta.json` ao lado do arquivo e dispara a preparação de novo; as perguntas de treino das seções saem do cache, então a segunda passada custa centavos.
3. Acompanhe a etapa na tela **Acervo** e o resultado na aba **Treinamento**. Dá para trocar de tela, e o chat continua respondendo com o índice anterior até o novo ficar pronto. Mudança feita durante uma preparação entra na fila e roda logo depois. Arquivo copiado direto para `acervo/` precisa do botão **Indexar e treinar de novo** ou de `.\.venv\Scripts\python -m app treinar` (só indexar, sem treino: `python -m app indexar`).

A indexação guarda os vetores de cada texto em `data/vetores/`: reindexar só calcula os trechos e perguntas que mudaram.

O envio confere o arquivo antes de colocá-lo na pasta: até 25 MB, PDF começando com `%PDF-`, DOCX começando com `PK`, e o arquivo precisa abrir. Arquivo recusado não entra em `acervo/`. Arquivo ilegível que já estava na pasta aparece no Acervo com o aviso "Não foi possível ler o arquivo", e um `.meta.json` quebrado é ignorado (valem os dados lidos do documento) com aviso no cartão.

Normativo com vigente desmarcado, ou com status substituído, revogado, obsoleto, cancelado ou não vigente, fica no índice mas não entra nas respostas. O `exemplo-normativo.md`, se estiver na pasta, fica de fora enquanto houver um normativo real.

### Corrigir a transcrição de um quadro

A transcrição automática pode errar em tabelas com células mescladas. Foi o caso do quadro de prazos da seção 5.1.1 da ITOM (página 11), corrigido à mão célula a célula. Para corrigir outro quadro:

1. Procure em `data/visao/` o JSON com o `arquivo` e a `pagina` do quadro.
2. Para pedir uma nova leitura ao GPT, apague esse JSON. Para corrigir à mão, edite o campo `texto`, com uma linha por célula no formato `Linha: … | Coluna: … | Valor: …`. Célula mesclada se repete em cada combinação que ela cobre.
3. Rode a indexação e reinicie a API.
4. Confira as seções (próximo tópico).

Mudar o pedido de transcrição em `app/visao.py` não refaz as transcrições já gravadas. Só o JSON apagado é lido de novo.

### Conferir as seções depois de indexar

A citação e a busca dependem do corte por seção. Um corte errado não dá erro na indexação: as respostas continuam saindo, mas citam a seção errada e misturam assuntos. Depois de incluir um normativo novo ou mexer em quadro:

1. Leia a saída de `python -m app indexar`. Para cada documento com sumário, ela diz se todas as seções do sumário viraram trecho ou quais números faltam:

   ```text
   ITOM EGP 01 Rev.02 (1).pdf: as 53 seções do sumário estão no índice.
   ```

   O mesmo aviso aparece no cartão do documento na tela **Acervo**. A conferência compara só os números (`5.1.1`, `17`); documento sem sumário não é conferido.
2. Na tela **Acervo**, o botão **Seções** do cartão lista as seções com a página no PDF. Ela usa as transcrições de quadros já gravadas em `data/visao/` (sem chamar o GPT), então mostra o corte igual ao do índice. Quadro que ainda não foi transcrito só entra depois da indexação.
3. Para ver as seções do índice que a consulta usa de fato, rode no terminal:

   ```powershell
   .\.venv\Scripts\python -c "import json; [print(t['pagina'], t['secao']) for t in json.load(open('data/indice/trechos.json', encoding='utf-8'))['trechos']]"
   ```

4. Desconfie de uma seção com muitas partes (por exemplo "5.1.1 … (parte 30)") ou de muitas partes seguidas com a mesma página. É sinal de que o corte parou de reconhecer os títulos a partir de algum ponto.
5. Rode a aba **Avaliação**: muitos cartões com "A seção citada não é a esperada" apontam para o mesmo problema.

Hoje a ITOM EGP 01 fica com 123 trechos: sumário, itens 1 a 20 com suas subseções e anexos 01 a 08, cada um com a página real. O acervo inteiro soma 143 trechos.

### Treinamento automático (aba Treinamento)

Roda sozinho depois de enviar, editar ou remover documento, e só para o documento cujo texto mudou (o botão **Treinar de novo** do cartão força um documento). Não é ajuste fino do GPT: o que aprende é a busca e o selo de confiança.

1. **Perguntas de treino.** Para cada seção com conteúdo, o GPT escreve até 2 perguntas de operador, com resposta esperada e valores; para o documento, 3 perguntas que ele não responde (com o código do documento, para a recusa ser obrigatória). Metade das perguntas normais leva o código do documento, como o operador costuma perguntar. Pergunta quase igual a uma pergunta de busca da seção (palavras em comum de 70% ou mais) é descartada, porque não testaria nada. Ficam em cache em `data/treino/cache/`.
2. **Conferência da busca, sem GPT.** Cada pergunta passa pela mesma busca do chat; conta acerto quando a seção esperada está entre os trechos que iriam ao GPT e a busca não recusaria antes.
3. **Perguntas aprendidas.** As perguntas que a busca não achou entram como perguntas de busca da seção (`data/aprendizado/perguntas.json`, até 10 por seção) e o índice é refeito, aproveitando os vetores guardados. As aprendidas só são zeradas quando o texto do documento muda.
4. **Paráfrases.** O GPT reescreve cada pergunta que falhou e a busca é conferida de novo. É esse número que diz se o aprendizado vale para outra forma de perguntar, e não só para a frase decorada.
5. **Amostra com resposta completa.** 15 perguntas por documento (12 espalhadas pelas seções e 3 de recusa, sempre as mesmas para o mesmo treino) passam pelo chat e pelo juiz. Cada erro ganha uma causa: a busca não trouxe a seção, recusou com a seção em mãos, errou o conteúdo ou inventou.
6. **Selo de confiança.** Uma regressão logística (em numpy, `app/confianca.py`) aprende com todas as rodadas julgadas (amostras e prova) a partir de sinais da busca e da resposta: notas de cosseno e BM25, posição e nota do trecho citado, número de citações e documentos, documento citado na pergunta, tamanho da pergunta e da resposta e a confiança média dos tokens do GPT (`logprobs`). Só liga com 200 respostas julgadas ou mais e AUC de 0,70 ou mais na validação cruzada; aí o chat mostra "Confiança alta", "média" ou "baixa". Alta exige 95% de acerto na validação. O modelo fica em `data/modelo/confianca.json` e é refeito ao fim de cada rodada.

Custo: as perguntas de treino custam por volta de US$ 0,0005 por seção, uma vez; a amostra, por volta de US$ 0,05 por documento; uma passada sem mudança sai de graça. O custo de cada documento aparece no cartão dele.

### Medir a acertividade (prova na aba Treinamento)

A prova, na aba **Treinamento**, roda um gabarito de perguntas com resposta conhecida pelo mesmo caminho do chat (glossário, busca, quadros, prompt e validação de citação) e compara cada resposta com a esperada. O número mede o sistema inteiro, não só o GPT. Nada é "treinado": quando uma pergunta erra, a correção é no índice, na transcrição de um quadro, no glossário ou no prompt, e a rodada seguinte mostra se melhorou.

Ciclo de uso:

1. Gere o gabarito no NotebookLM com o pedido abaixo e salve a resposta como `.json`.
2. Clique em **Importar bloco do gabarito** e escolha o arquivo. O bloco soma ao gabarito: só as perguntas do mesmo documento são substituídas (o documento é comparado pelo código, como `ITOM AGD 01`, sem a revisão e o título), e as dos outros documentos ficam. Quando um id já existe em outro documento, o novo ganha o código do documento na frente (`ITOM-AGD-01-1`). A tela mostra quantas perguntas entraram, quantas foram substituídas e o total; com mais de um documento, o resumo conta as perguntas por documento. As rodadas antigas continuam no gráfico. Se o arquivo tiver problema, a tela diz qual item corrigir (pergunta sem resposta, id repetido, JSON inválido).
3. Escolha o contexto (**Trechos**, **Seção inteira** ou **Documento inteiro**) e se usa o **reordenador local**, e clique em **Rodar avaliação**. O padrão vem do `.env`. As perguntas rodam 4 por vez e a barra mostra o andamento. A primeira rodada após subir a API demora uns 15 segundos a mais para carregar o modelo de embeddings. Cada barra do gráfico mostra embaixo a configuração usada (`trechos`, `seção`, `doc.`, e `+R` com reordenador).
4. Veja a **acertividade por categoria** para achar o assunto mais fraco (clicar numa categoria filtra os cartões). Depois filtre por **Erro**, **Parcial**, **Recusa indevida** e **Invenção** e leia o diagnóstico de cada cartão.
5. Corrija a causa (veja a tabela de status), reindexe ou reinicie a API se for o caso, e rode de novo. O gráfico mostra a acertividade de cada rodada lado a lado.

O gabarito atual é `perguntas-itom-01.json`, gerado pelo NotebookLM: 200 perguntas da ITOM EGP 01 em 6 categorias (Definições e Conceitos, Prazos e SI-PS, Roteiro de Manobras, Documentos Vinculados, Segurança e LOTO, Responsabilidades). Ele não tem perguntas que devem ser recusadas, então o indicador **Invenções** fica em zero por falta de teste. Use o pedido complementar abaixo para acrescentá-las.

Uma rodada sem mudança no índice nem no prompt sai toda do cache, com custo zero. A rodada guarda a `versao` (assinatura do índice e do prompt): duas rodadas com a mesma versão testaram o mesmo sistema.

#### Revisão do gabarito com o COG

O gabarito do NotebookLM também erra. Antes de apresentar o número, revise uma amostra com alguém do COG na aba **Avaliação > Revisão do gabarito**:

1. A tela sorteia 50 perguntas na proporção de cada categoria. A amostra fica gravada e não muda entre as visitas.
2. Cada cartão mostra a resposta esperada no gabarito e a resposta do sistema na rodada escolhida no gráfico. Confira no normativo e marque:
   - **Confere**: a resposta esperada está certa.
   - **Corrigir resposta**: escreva a resposta certa. Nas próximas rodadas a pergunta é julgada contra ela.
   - **Descartar pergunta**: a pergunta é ruim ou ambígua. Ela sai das próximas rodadas.
3. O campo de observação guarda quem conferiu e onde.

O indicador **Nas perguntas revisadas** mostra a acertividade só nas perguntas conferidas pelo COG. Uma pergunta corrigida conta nas rodadas feitas depois da correção. É esse o número defensável para a apresentação. As revisões ficam em `data/avaliacao/revisao.json` e continuam valendo se o mesmo gabarito for importado de novo; se o texto da pergunta mudar, a revisão dela deixa de valer.

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

Cada pergunta sem cache custa uma resposta e um julgamento, em torno de US$ 0,003 a 0,005 com o `gpt-4.1-mini`. Quando a resposta contém a resposta esperada, ela vale como acerto sem chamar o juiz. A rodada das 200 perguntas atuais sai por volta de US$ 0,60 a 1,00 e leva alguns minutos; uma rodada repetida sem mudança sai de graça. Reindexar ou mudar o prompt invalida o cache, e a rodada seguinte paga de novo. O custo aparece no cartão **Custo da rodada** e também na aba **Uso**, separado do custo do Normativo e do Chat ONS. As perguntas da avaliação ficam em `data/logs/avaliacao.jsonl`, fora do registro das consultas, e não aparecem no Histórico nem na nuvem de perguntas. Enquanto a rodada roda, a tela pergunta só o progresso a cada 2 segundos e para de perguntar quando a aba do navegador fica escondida.

### Medir a busca sem gastar GPT

`scripts/medir_busca.py` roda as perguntas do gabarito só pela busca local e conta em quantas a seção esperada chega aos trechos enviados ao GPT e em quantas ela vem em primeiro. Não chama o GPT, então serve para comparar modelos de embeddings, reordenador e modos antes de pagar uma rodada:

```powershell
.\.venv\Scripts\python scripts\medir_busca.py --modelos google/embeddinggemma-300m intfloat/multilingual-e5-large
.\.venv\Scripts\python scripts\medir_busca.py --reranker jinaai/jina-reranker-v2-base-multilingual --candidatos 12
.\.venv\Scripts\python scripts\medir_busca.py --modo secao --faltas
```

Os vetores de cada modelo ficam em `data/medicao/`. Um modelo novo é baixado para `data/modelos/` na primeira vez.

### Resultados das medições

Gabarito de 200 perguntas do NotebookLM sobre a ITOM EGP 01, ainda sem revisão do COG.

**Busca local** (`medir_busca.py`, 196 perguntas com seção esperada):

| Configuração | Seção esperada no contexto | Seção esperada em primeiro | Tempo por pergunta |
| --- | --- | --- | --- |
| MiniLM (modelo antigo) | 170 (86,7%) | 93 (47,4%) | menos de 0,1 s |
| multilingual-e5-large | 174 (88,8%) | 134 (68,4%) | menos de 0,1 s |
| **embeddinggemma-300m (atual)** | **184 (93,9%)** | **139 (70,9%)** | menos de 0,1 s |
| embeddinggemma + seção inteira | 185 (94,4%) | 139 (70,9%) | menos de 0,1 s |
| embeddinggemma + reordenador jina sobre 12 candidatos | 184 (93,9%) | 159 (81,1%) | cerca de 7 s |

O reordenador põe a seção certa em primeiro com mais frequência, mas não traz seção que a busca não achou. Como os 6 trechos vão todos ao GPT, a ordem pesa pouco na resposta e o reordenador custa 7 segundos por pergunta neste PC. Por isso ele fica desligado por padrão e disponível na aba Avaliação.

**Rodadas com o GPT** (aba Avaliação):

| Rodada | Configuração | Acertividade | Parciais | Recusas indevidas | Custo |
| --- | --- | --- | --- | --- | --- |
| Linha de base | MiniLM, trechos | 60,0% | 47 | 31 | US$ 0,54 |
| Atual | embeddinggemma, trechos, consulta exata ao quadro, item 17 corrigido | 65,5% | 57 | 10 | US$ 0,56 |
| Seção inteira | igual à atual, com `MODO_CONTEXTO=secao` | 68,5% | 52 | 9 | US$ 0,80 |
| Documento inteiro | igual à atual, com `MODO_CONTEXTO=documento` | 67,0% | 58 | 5 | cerca de US$ 2,20 |

Por categoria, a consulta exata ao quadro levou **Prazos e SI-PS** de 60,0% para 85,7% (82,9% com seção inteira, 74,3% com documento inteiro). **Responsabilidades** segue a pior (37% nos trechos e na seção, 43% no documento inteiro): as respostas esperadas juntam atribuições espalhadas por vários itens.

Seção inteira e documento inteiro ficaram dentro da margem de variação um do outro. O documento inteiro custa quase 3 vezes mais, é mais lento e só cabe porque hoje há um documento por pergunta; com os 40 normativos, a seção inteira é o caminho viável. Na rodada com documento inteiro, as perguntas vão uma por vez: cada pedido tem uns 43 mil tokens e 4 em paralelo estouram o limite de 200 mil tokens por minuto da conta OpenAI.

Como ler esses números:

- Com 200 perguntas, a margem de uma rodada é de uns 3 pontos para cima ou para baixo. Diferença menor que isso pode ser variação do GPT.
- A maior parte das parciais é o juiz punindo detalhe a mais que a resposta esperada não confirma. O juiz não vê o normativo, então não sabe se o detalhe está certo. A revisão do COG é que separa gabarito incompleto de resposta errada.
- Este gabarito não tem perguntas que devem ser recusadas, então a invenção (responder o que o normativo não diz) ainda não foi medida. Use o pedido complementar ao NotebookLM para incluir essas perguntas.

### Segunda tentativa e o que impede o conserto "pergunta a pergunta"

Três mecanismos tratam classes de falha, e não perguntas soltas:

- **Segunda tentativa.** Se a primeira resposta é "ausente" e as seções inteiras dos trechos achados trazem texto que o GPT não viu, o assistente tenta uma vez mais com esse contexto. Só vale se a nova resposta tiver fonte válida; senão fica a primeira. Custa uma chamada a mais, só quando ele recusou. A resposta marca `ampliado` no registro.
- **Treino com perguntas gerais.** O treino automático (`python -m app treinar`, ou a aba Treinamento) agora gera por documento 3 perguntas gerais (finalidade, assunto, a quem se aplica) e confere se a visão geral ou o objetivo chegam ao contexto. Antes só testava perguntas de uma seção, e foi assim que "qual a finalidade da ITOM EGP 05?" passou despercebido. Falha vira pergunta de busca aprendida.
- **Perguntas fora do escopo em toda prova.** A prova inclui 10 perguntas que nenhum normativo responde (`recusa-padrao-NN`). Se o assistente passar a respondê-las, aparece como **invenção** e o `avaliar` reprova. É a trava contra a segunda tentativa e o prompt v2 ficarem soltos demais. A comparação com rodadas anteriores usa só os ids em comum, então a prova de antes continua comparável.

Uma bateria de 18 perguntas variadas (gerais, com palavras diferentes das do normativo, coloquiais, continuações e fora do assunto) custou US$ 0,06: 13 respondidas com fonte, 1 parcial, 4 recusadas, nenhuma inventada.

### Porta de regressão e métricas de busca

- **Busca separada da resposta.** Cada pergunta da prova guarda a posição em que a seção esperada chegou ao GPT (`posicao_recuperada`). O resumo da rodada traz o *recall da busca* (a seção chegou) e o *MRR* (em que posição). Recall baixo aponta busca ou extração; recall alto com acertividade baixa aponta prompt ou modelo.
- **Comparação com a rodada anterior.** Cada rodada é comparada, pelos mesmos ids, com a anterior da mesma origem e documento: variação da acertividade, perguntas que melhoraram e **regressões** (acertavam e deixaram de acertar). A aba Avaliação mostra isso.
- **Porta pelo terminal.** O comando abaixo roda a prova e sai com código 1 (reprovada) se a acertividade ficar abaixo do mínimo, se houver invenção ou se houver regressão. `--permitir-regressao` aceita uma troca consciente:

```powershell
.\.venv\Scripts\python -m app avaliar --minimo 85
```

- **Comparar o prompt.** Rode a prova com `PROMPT_VERSAO=v1` e com `v2` (reinicie a API entre as duas). A rodada guarda a `versao` do sistema, e a comparação lista as perguntas que mudaram.

### Revisar transcrições de quadros

Quadro lido por visão deve ser conferido contra o PDF. `python -m app transcricoes` lista as transcrições de `data/visao/` e marca as que ainda não foram revisadas. Corrija o campo `texto` (ou use `PUT /api/visao/<hash>` com `{"texto": "..."}`) e marque com `python -m app transcricoes --revisar <hash>`. Reindexe depois. Página de PDF sem texto (digitalizada) é lida pelo GPT como página inteira (OCR) na indexação, com o mesmo cache por hash.

### Perguntar pelo terminal

```powershell
.\.venv\Scripts\python -m app perguntar "qual o prazo de solicitação para alta complexidade com desligamento?"
```

### Rodar os testes

```powershell
.\.venv\Scripts\python -m unittest discover -s tests
```

São 123 testes. Os do chat ONS usam servidor e GPT falsos e não precisam de internet. Os que precisam de um normativo copiam o `tests/dados/exemplo-normativo.md` para uma pasta temporária, então passam com qualquer conteúdo em `acervo/` e não mexem em `data/`.

## Registro de correções

Problemas achados no uso do piloto e como foram resolvidos. Serve de histórico para a coordenação e de checklist quando entrar um normativo novo.

| Problema | Causa | Correção |
| --- | --- | --- |
| Pergunta de natureza da SI (alta complexidade, com desligamento, programada) recusada. | O quadro de prazos da 5.1.1 estava numa parte da seção que a busca não trazia, e a transcrição automática desalinhou as células mescladas. | A busca passou a incluir o trecho do quadro da mesma seção. O quadro da página 11 foi transcrito à mão, célula a célula. |
| Prazo de urgência respondido como 15 dias. | Transcrição do quadro e pergunta com "urgências" no plural. | Coluna de urgência corrigida para "Inferior a 2 dias úteis"; glossário com urgências; regra no prompt para responder só a célula da coluna pedida. |
| Quase toda a ITOM citada como 5.1.1. | O quadro da página 11 era inserido no meio da frase "quadro a seguir, de acordo com a…". O marcador de fim ficou colado no resto da frase, e o corte achou que o quadro não terminava, ignorando todos os títulos seguintes (5.2 a 20 e anexos). | O quadro passou a entrar no fim da linha, e o corte reconhece o fim do quadro mesmo com texto colado. A 5.1.1 caiu de 44 para 11 partes. |
| Seções duplicadas com pontilhado ("5.1 UTILIZAÇÃO DA SI-ps ......"). | As linhas do sumário eram lidas como títulos. | Página de sumário vira um trecho "Sumário" e não abre seção. |
| Gabarito do NotebookLM recusado na importação. | O arquivo usa `perguntas_e_respostas`, `resposta` e `referencia` em texto livre. | A importação aceita esse formato como vem. |
| Item 17 REGISTROS da ITOM colado no fim da 16.2. | No PDF o título vem como `17. \| REGISTROS`, com a barra de coluna da tabela, e o corte não reconhecia. | O corte aceita a barra depois do número. A conferência com o sumário passou a avisar esse tipo de falha na indexação e no Acervo. |
| Busca trazia a seção certa em primeiro em só 47% das perguntas. | O modelo de embeddings antigo (MiniLM) lê só o começo de cada trecho de 2.000 caracteres. | Troca para o `google/embeddinggemma-300m`: 71% em primeiro e 94% com a seção no contexto (veja "Resultados das medições"). |
| Duas rodadas de avaliação no mesmo segundo viravam uma. | O nome do arquivo da rodada tinha precisão de segundo. | Rodada com nome repetido ganha sufixo `-2`, `-3`. |
| Chat ONS respondeu carga de 0,0 MWmed para agosto de 2026. | O dataset mensal do ONS publica `0.0` para mês ainda não fechado, e o GPT leu como valor. | Resultado zerado ganha aviso de dado não publicado e o programa exige tentar o dataset diário; a resposta passou a 13.588,3 MWmed, igual à consulta direta. |
| Chat ONS esgotava as rodadas com o mesmo SQL errado. | O contrato do `carga-energia` recomenda comparar a coluna com `''`, e a coluna já é numérica; o erro volta como texto `[ERRO]`, sem marca de falha. | `[ERRO]` passou a contar como falha, e o erro de conversão ganha a dica de usar só `TRY_CAST`. |
| Rodada com documento inteiro falhou em 163 de 200 perguntas. | Limite de 200 mil tokens por minuto da OpenAI, com 4 pedidos de 43 mil tokens em paralelo. | A chamada ao GPT tenta de novo até 5 vezes, esperando o tempo que a OpenAI indica, e a rodada com documento inteiro vai uma pergunta por vez. |
| Registro de consultas com 8,3 MB e tela Uso pesada. | Cada consulta gravava o texto de todos os trechos, e a tela baixava o registro inteiro a cada abertura. | O registro não guarda mais os trechos, a avaliação foi para `avaliacao.jsonl` e o servidor devolve só os totais e recortes (de 86 KB para 2 KB por abertura). Na primeira subida, o registro antigo foi enxugado e a cópia ficou em `consultas.jsonl.bak`. |
| Tela Acervo levava de 8 a 10 segundos para abrir. | Cada abertura relia e refatiava todos os PDFs. | A análise de cada arquivo fica em memória até o arquivo ou o `.meta.json` mudar; a API analisa o acervo ao subir, e a tela abre em cerca de 30 ms. |

## Estrutura de pastas e arquivos

### Raiz

| Arquivo | Função |
| --- | --- |
| `README.md` | Este documento. |
| `requirements.txt` | Dependências Python da API, da leitura de documentos, da busca local e do cliente OpenAI, com teto de versão maior para uma atualização não quebrar a instalação. |
| `requirements-dev.txt` | As mesmas dependências mais o `ruff`, para checar o código. |
| `ruff.toml` | Regras do `ruff`: nomes indefinidos, imports e variáveis sem uso e erros de sintaxe. |
| `.env.example` | Modelo do `.env`, com todas as variáveis comentadas. |
| `.env` | Chave do GPT, modelo, pastas, porta, busca, chat ONS e segredos do Teams. Fica fora do git. |
| `.gitignore` | Deixa fora do git o `.env`, o ambiente `.venv`, a pasta `data/`, os normativos reais e os artefatos do front. |

### `acervo/` — normativos

Pasta lida pela indexação. Os normativos reais ficam fora do git. O normativo fictício de exemplo (IT-EXEMPLO-001) não fica mais aqui; os testes usam a cópia em `tests/dados/`.

| Arquivo | Função |
| --- | --- |
| `ITOM EGP 01 Rev.02 (1).pdf` | ITOM 01, revisão 02: solicitação, emissão e liberação de SI-PS, prazos por complexidade e natureza, MO, AI, ATEIE, bloqueio de energias, roteiro de manobras, liberação e retorno à operação, responsabilidades e anexos 01 a 08. Tem 9 imagens de conteúdo transcritas em `data/visao/` (o quadro de prazos da página 11 e os formulários dos anexos). |
| `NO 01 - Pós Operação.pdf` | NO-01, revisão 00: pós-operação, triagem de ocorrências e critérios de RAO/RAI. |
| `*.pdf.meta.json` | Metadados de cada normativo: `codigo`, `revisao`, `titulo` e `vigente`. Gravados pela tela Acervo. |
### `app/` — núcleo em Python

| Arquivo | Função |
| --- | --- |
| `__init__.py` | Marca o pacote e guarda a versão. |
| `__main__.py` | Linha de comando: `python -m app indexar`, `treinar [arquivo…]`, `perguntar "…"` e `servir`. |
| `treino.py` | Treinamento automático por documento: perguntas de treino por seção e de recusa (com cache), descarte das parecidas com as perguntas de busca, conferência da busca sem GPT, perguntas aprendidas, paráfrases das falhas, amostra de 15 pela avaliação e o ciclo completo `preparar` (indexa, treina só o que mudou, reindexa, confere e amostra). |
| `confianca.py` | Selo de confiança: regressão logística com regularização, validação cruzada em 5 partes, AUC, cortes de alta e baixa e aplicação nas respostas cobertas. |
| `config.py` | Lê o `.env` e define chave, modelo, pastas, host, porta, modelo de embeddings (`EMBED_MODEL`), quantidade de trechos (`TOP_K`), modo de contexto (`MODO_CONTEXTO`), reordenador (`RERANKER_MODEL`, vazio desliga) e quantos candidatos ele lê (`CANDIDATOS`), o endereço do MCP do ONS (`ONS_MCP_URL`), o limite de rodadas do chat ONS (`ONS_MAX_PASSOS`, padrão 6), o agente que é "a empresa" no chat ONS (`ONS_AGENTE`, padrão ENERGIMP), o modelo do juiz da avaliação (`JUIZ_MODEL`, vazio usa o `OPENAI_MODEL`) e o aviso ao operador. Remove `OPENAI_BASE_URL` vazio para o SDK usar o endereço padrão. |
| `gpt.py` | Cliente OpenAI único para todo o programa (60 s de tempo limite e até 5 novas tentativas), o formato da saída do GPT (texto, tokens de entrada, reaproveitados e de saída, motivo do fim) e a conversão do histórico do chat em turnos. |
| `arquivos.py` | Gravação atômica (arquivo temporário e troca) e leitura tolerante de JSON. Usada no cache, nas transcrições, na avaliação, nos metadados, no índice e nos arquivos do ONS, para um arquivo cortado no meio não quebrar a leitura seguinte. |
| `extrair.py` | Lê PDF (modo layout), DOCX, TXT e MD, página a página. Resolve os metadados; `.meta.json` ilegível é ignorado. Lista as imagens de conteúdo do PDF, ignorando o logotipo que aparece em 3 ou mais páginas, e insere a transcrição do quadro no fim da linha que o anuncia ("quadro a seguir"), entre os marcadores de início e fim da figura. |
| `visao.py` | Transcreve cada imagem de conteúdo com o GPT, uma vez, célula a célula. Grava em `data/visao/{hash}.json` e reaproveita nas indexações seguintes. Imagem sem texto útil fica gravada como `SEM_CONTEUDO` e não entra no índice. |
| `chunking.py` | Corta o documento em trechos por seção. Item numerado com título em maiúscula abre seção; linha que começa com número mas é conteúdo (por exemplo "7 dias úteis") não abre. O quadro transcrito fica dentro da seção que o anuncia, entre "Transcrição da figura da página N" e "Fim da transcrição da figura"; dentro do quadro nenhuma linha abre seção. Página de sumário vira um trecho só. Também confere os números do sumário contra as seções que viraram trecho. |
| `indice.py` | Monta o índice: extrai, transcreve os quadros, fatia, calcula embeddings locais e grava `data/indice/`. Aplica o prefixo que cada modelo de embeddings espera na pergunta e no trecho (e5, embeddinggemma, Qwen3). Também calcula a impressão digital do acervo pelo tamanho e data de cada arquivo e do seu `.meta.json` (para avisar que o índice ficou desatualizado) e a assinatura do conteúdo. Grava os vetores num arquivo de nome novo antes de o `trechos.json` passar a apontar para ele, para uma pergunta no meio da indexação ler o par antigo ou o novo inteiro, e guarda o índice lido em memória até ele mudar. |
| `buscar.py` | Busca híbrida (embeddings + BM25, fundidos por RRF), reordenação opcional, glossário de siglas e expansão conforme o modo: partes vizinhas da seção e trecho do quadro (`trechos`), seção inteira (`secao`, até 40 mil caracteres) ou documento inteiro (`documento`, até 200 mil). Decide se há base mínima para chamar o GPT: alguma palavra da pergunta num trecho e nota mínima de cosseno ou de BM25. |
| `reranker.py` | Reordenador local (cross-encoder do fastembed). Relê a pergunta junto com cada candidato e dá uma nota. Carregado só quando `RERANKER_MODEL` está preenchido ou a rodada pede. |
| `quadro.py` | Lê as células `Linha \| Coluna \| Valor` das transcrições de quadros no índice e escolhe as que a pergunta pede, pelos termos que diferenciam cada linha e cada coluna. |
| `rag.py` | Prompt de sistema, montagem do pedido (trechos agrupados por documento, conversa resumida, consulta exata ao quadro e a pergunta no fim), chamada ao GPT (temperatura 0, resposta em JSON, até 800 tokens), validação das citações, cache pelo pedido exato e registro de cada consulta. |
| `ons.py` | Chat ONS: cliente MCP compartilhado (JSON-RPC em HTTP, JSON ou SSE, nova sessão quando o servidor responde 404), catálogo enxuto, contratos compactados e usinas da empresa guardados por 24 horas, regras de busca (empresa pelo agente do cadastro, nome sem acento, data de fim vazia como vigente), laço de ferramentas com o GPT (chamadas do mesmo passo em paralelo, limite de rodadas, de linhas e de tamanho do retorno), troca do SQL para ler só os arquivos do período, leitura da unidade e da fonte que o ONS devolve, avisos de resultado zerado e de erro de conversão, cache do dia (só com fonte) e registro com `origem: "ons"`. |
| `cache.py` | Chave e leitura/gravação das respostas em `data/cache/`. |
| `registro.py` | Acrescenta cada consulta em `data/logs/consultas.jsonl` (as da avaliação em `data/logs/avaliacao.jsonl`), com identificador, data, autor, origem, pergunta, resposta, citações, tokens, tempos e `continuacao` (se a pergunta veio com conversa anterior), e cada retorno do operador em `data/logs/retornos.jsonl`. A gravação tem trava para as perguntas paralelas da avaliação. |
| `uso.py` | Lê os registros aos poucos (só o que foi acrescentado desde a última leitura), junta a cada consulta o último retorno do operador e calcula no servidor consultas, úteis e não úteis, chamadas cobradas, cache, tokens e custo estimado em dólar pela tabela pública do `gpt-4.1-mini` (US$ 0,40 por milhão de tokens de entrada, US$ 0,10 na parte que a OpenAI reaproveita e US$ 1,60 na saída), com a divisão por origem (Normativo, Chat ONS, Avaliação), por dia e por documento. Também monta a nuvem de perguntas, a lista do Histórico com busca e as medidas do chat ONS. Na subida, enxuga o registro antigo (tira os trechos e leva a avaliação para o arquivo próprio), guardando a cópia em `consultas.jsonl.bak`. Marca cada consulta como continuação ou não: vale o campo `continuacao`; nas consultas antigas, sem o campo, conta como continuação a pergunta do mesmo autor e chat feita até 10 minutos depois da anterior e que remete a ela ("e…", "só…", "deste", "disso", "montante"). Também repassa as medidas do chat ONS (tempo total, tempo no GPT e no ONS, passos e repetições). A função `custo_usd` também é usada pela avaliação. |
| `avaliacao.py` | Avaliação da acertividade: importa e valida cada bloco do gabarito (no formato próprio ou no do NotebookLM, convertendo a `referencia` em seções) e soma ao gabarito, substituindo só as perguntas do mesmo documento, roda as perguntas 4 por vez pelo mesmo `responder` do chat, aplica as regras objetivas e o juiz GPT (com cache; resposta que contém a esperada dispensa o juiz), classifica cada pergunta, calcula a acertividade geral, por categoria e nas perguntas revisadas, e grava a rodada em `data/avaliacao/rodadas/` com o modo e o reordenador usados. Sorteia a amostra de revisão, grava as revisões e aplica correções e descartes nas rodadas seguintes. Guarda o andamento da rodada em curso para a tela. |
| `painel.py` | Regras do ambiente web: pré-análise dos arquivos (páginas, seções, trechos, avisos e conferência do sumário, com os quadros já transcritos), guardada em memória por arquivo; envio com nome seguro, limite de 25 MB, conferência do tipo e leitura antes de entrar na pasta; gravação de metadados; remoção; preparação em segundo plano (indexação e treino, uma por vez, com fila para o pedido feito durante outra e a etapa em andamento) e consulta pelo chat. |
| `server.py` | API FastAPI em `127.0.0.1:8080`, com respostas compactadas (gzip). Rotas `/api/acervo` (listar, enviar, remover; enviar, editar e remover disparam a preparação), `/api/acervo/meta`, `/api/indexar` (POST inicia ou põe na fila, GET diz a etapa), `/api/treino` (GET resultado por documento e selo de confiança, POST força o treino de um arquivo), `/api/chat` (aceita até 8 turnos de conversa; 502 quando o GPT não responde, 409 quando o índice falta ou é inválido), `/api/ons/chat` (chat ONS; responde 502 quando o servidor do ONS não responde), `/api/retorno`, `/api/uso` (totais), `/api/uso/perguntas?canal=`, `/api/uso/consultas?limite=&busca=&nao_uteis=`, `/api/uso/ons`, `/api/avaliacao` (estado, `progresso`, `gabarito`, `rodar` com modo e reordenador permitido, `revisao` e `rodadas/{id}`), `/saude` e `/teams/outgoing`. Ao subir, enxuga o registro antigo, analisa o acervo em segundo plano e carrega o modelo de embeddings (e o reordenador, se configurado). |
| `teams.py` | Integração opcional com o Teams: confere a assinatura HMAC do outgoing webhook, limpa a menção e publica a resposta no incoming webhook. |

### `web/` — ambiente web (React + Vite)

| Arquivo | Função |
| --- | --- |
| `index.html` | Página base que carrega o React, com o título "Assistente da Operação" na aba do navegador. Aplica o tema salvo antes de a tela desenhar, para não piscar branco no tema escuro. |
| `public/aerogerador.svg` | Ícone da aba do navegador. |
| `package.json` / `package-lock.json` | Dependências do front (React 19, Vite 6, ESLint) e comandos `dev`, `build`, `lint` e `preview`. |
| `eslint.config.js` | Regras do ESLint, com as regras de hooks do React (`rules-of-hooks` e `exhaustive-deps`). |
| `vite.config.js` | Servidor de desenvolvimento em `127.0.0.1:5173`, com as chamadas `/api` repassadas para a API na 8080. |
| `src/main.jsx` | Ponto de entrada do React. |
| `src/App.jsx` | Topo com a marca (aerogerador), o selo "COG · Centro de Operação da Geração", o nome "Assistente da Operação", as abas **Acervo**, **Consulta**, **Histórico**, **Uso** e **Treinamento** e o botão de tema. A Consulta fica sempre montada (só escondida nas outras abas), para a conversa não se perder. Na Consulta, a página ocupa a altura da tela para a conversa rolar por dentro. |
| `src/tema.js` | Tema claro, escuro ou do sistema. Guarda a escolha no navegador e acompanha a troca de tema do Windows quando está em "sistema". |
| `src/api.js` | Funções que chamam a API: acervo, metadados, remoção, indexação e andamento da indexação, chat e chat ONS (com o histórico da conversa), retorno do operador e avaliação (estado, progresso, envio do gabarito, rodar com modo e reordenador, ler rodada e revisão). Cada chamada leva um rótulo que aparece na barra de carregamento; as consultas periódicas de andamento são silenciosas. |
| `src/carga.js` | Guarda as chamadas em andamento para a barra de carregamento do topo. |
| `src/Carregando.jsx` | Barra fina no topo da página, ativa enquanto houver chamada em andamento (com o rótulo no canto quando a tela não mostra barra própria), e a barra com texto usada dentro das telas: ler o acervo, indexar, enviar arquivo, consultar, ler registro, avaliação, rodada e amostra da revisão. |
| `src/registro.js` | Guarda compartilhado dos dados do registro (totais do Uso, nuvem de perguntas de cada chat, lista do Histórico, medidas do ONS). Cada tela pede só o recorte que mostra; depois de uma resposta ou de um voto, os recortes em uso são pedidos de novo. |
| `src/Acervo.jsx` | Situação do acervo e do índice, passos de uso, envio de arquivos, busca e filtros, e um cartão compacto por documento (páginas, seções, trechos, conferência do sumário, avisos). As seções e a edição dos dados abrem só ao clicar; remover pede confirmação. A indexação roda em segundo plano, e a tela acompanha o andamento mesmo depois de trocar de aba e voltar. |
| `src/Chat.jsx` | Tela Consulta com o seletor **Operacional \| ONS**. O título muda com o chat: "Consulta aos Documentos Operacionais Energimp" ou "Dados abertos do ONS", cada um com as próprias sugestões. Cada chat guarda a própria conversa, que continua ao trocar de chat ou de aba e ao recarregar a página (fica no `sessionStorage` da aba do navegador); o botão **Nova conversa** limpa o chat. Se a consulta falhar, a pergunta volta para a caixa de texto. Envia as 4 últimas trocas como contexto, rola até a resposta mais nova e, com o chat vazio, mostra a nuvem das perguntas já respondidas naquele chat (sem as da avaliação e sem as de continuação, que dependem da conversa anterior). A resposta do ONS mostra fonte com link para o portal, unidade e o SQL executado recolhido. Cada resposta tem os botões **Útil** e **Não útil**, este com comentário opcional. |
| `src/Nuvem.jsx` | Nuvem de até 16 perguntas anteriores que tiveram resposta coberta. Cada pergunta flutua devagar e aparece inteira. A nuvem não rola: mostra só as perguntas que cabem na tela e recalcula quando a janela muda de tamanho. Clicar faz a pergunta de novo, num chat vazio. Recusas não entram, nem perguntas feitas no meio de uma conversa ("e pra RAO?", "quanto deste montante é de Papagaios?"), porque sem o contexto elas não teriam a mesma resposta. |
| `src/Leitura.jsx` | Mostra a resposta com listas `a)`, `b)`, `i)`, `1)` separadas em linhas. |
| `src/Historico.jsx` | Últimas 10 perguntas agrupadas pelo documento citado, com busca no servidor por texto da pergunta ou da resposta, selo do retorno do operador, comentário e filtro das marcadas como não úteis. Ignora as perguntas da avaliação. |
| `src/Uso.jsx` | Números de uso e custo, barras de custo por origem (Normativo, Chat ONS, Avaliação), gráfico de custo por dia (com a divisão por origem ao passar o mouse), barras de custo por documento e a tabela "Chat ONS: tempo e passos" com as últimas 15 perguntas ONS medidas. Tudo já vem calculado da API. |
| `src/Treinamento.jsx` | Topo da aba Treinamento: etapa da preparação em andamento, avisos, situação do selo de confiança e um cartão por documento (busca, aprendidas, paráfrases, amostra com as causas dos erros, custo, perguntas que a busca ainda não acha e o botão **Treinar de novo**). |
| `src/Avaliacao.jsx` | Aba Treinamento, parte da prova: importar bloco do gabarito (com o resumo do que entrou e a contagem por documento), escolher modo e reordenador, rodar, barra de andamento (pergunta só o progresso a cada 2 segundos e pausa com a aba do navegador escondida), gráfico de acertividade por rodada com a configuração de cada uma (clicar na barra abre a rodada), indicadores (inclusive nas revisadas), acertividade por categoria (clicar filtra), filtro por status e cartões com esperada, obtida, citação, motivo do juiz e diagnóstico. |
| `src/Revisao.jsx` | Revisão do gabarito: amostra de 50 perguntas, esperada ao lado da resposta da rodada escolhida, botões Confere, Corrigir resposta e Descartar pergunta, observação e desfazer. |
| `src/formato.js` | Funções de apoio: quebra da resposta em blocos de leitura, formatação de dólar, número e data, agrupamento por documento e os rótulos de status da avaliação. |
| `src/styles.css` | Identidade visual de operação eólica (azul-petróleo, verde-vento), com as cores em variáveis para os temas claro e escuro. Estilo das telas, da conversa com rolagem interna e da nuvem flutuante. O movimento para quem pede menos animação no sistema. |

### `scripts/` — atalhos do Windows

| Arquivo | Função |
| --- | --- |
| `subir.ps1` | Sobe a API com o Python do `.venv`. |
| `medir_busca.py` | Mede a busca contra o gabarito sem chamar o GPT: seção esperada no contexto e em primeiro, por modelo de embeddings, reordenador e modo. |
| `web.ps1` | Instala as dependências do front na primeira vez (`npm ci`) e sobe a tela. |
| `tunel-cloudflare.ps1` | Abre um túnel Cloudflare até a API. Só serve para o Teams. |
| `cloudflared.config.example.yml` | Modelo da configuração do túnel. Só deixa passar `/teams/outgoing`; qualquer outro caminho recebe 404, para painel, chat e acervo não ficarem expostos na internet. A cópia preenchida (`cloudflared.yml`) fica fora do git. |

### `tests/` — testes automáticos

| Arquivo | O que confere |
| --- | --- |
| `test_acervo.py` | Leitura, metadados e troca do exemplo pelo normativo real; corte por item numerado sem cabeçalho repetido; colunas do layout; linha de prazo que não vira seção; quadro dentro da seção; filtro do logotipo; transcrição que não é pedida duas vezes; glossário; inclusão do trecho do quadro quando a busca acerta outra parte da seção; quadro anunciado no meio da frase que não engole as seções seguintes; página de sumário num trecho só; aviso de seção do sumário que não virou título; pré-análise que usa a transcrição gravada sem chamar o GPT. |
| `test_quadro.py` | Consulta exata ao quadro (linha e coluna certas, nada quando a pergunta não aponta célula); modos seção e documento; ordem dada pelo reordenador; assinatura do cache que muda com modo e reordenador; reordenador fora da lista recusado; prefixos dos modelos de embeddings. |
| `test_ons.py` | Chat ONS com servidor e GPT falsos: laço que lê o contrato, executa o SQL (limitado a 50 linhas) e devolve fonte, unidade e SQL; limite de rodadas; recusa de ferramenta fora da lista; `[ERRO]` que não vira fonte; resultado zerado que gera aviso e nova tentativa; cache do dia, e sem cache quando não há fonte; ferramentas em paralelo e contrato guardado; compactação do contrato; troca do SQL pelo período e volta ao original quando dá erro; usinas da empresa no prompt, guardadas, e prompt sem mudança quando o agente não tem usina; cliente MCP com resposta em JSON e em SSE, sessão reaproveitada, nova sessão depois de 404 e servidor fora do ar. |
| `test_prompt_v2.py` | Prompt v2: número do trecho segue a ordem do pedido, estados parcial e ausente, conferência de valores, reescrita da pergunta (e o cache dela), cadeia de formatos da API com cliente falso, posição e recall da busca, comparação e porta de regressão, OCR e revisão de transcrições. |
| `test_rag.py` | Resposta com citação válida, recusa de citação inventada, continuação de conversa que não reaproveita o cache da pergunta isolada, falha do GPT fora do cache, pergunta fora do assunto recusada sem chamar o GPT, montagem do pedido por documento e troca do par do índice. |
| `test_painel.py` | Envio, pré-análise, metadados, remoção, recusa de nome fora do acervo, recusa de arquivo que não é o que diz ser, aviso de arquivo ilegível e de `.meta.json` quebrado. |
| `test_uso.py` | Consulta em cache fora do custo, entrada reaproveitada pela OpenAI com preço menor, lista da mais nova para a mais antiga, retorno do operador ligado à consulta (vale o último), avaliação em arquivo próprio com a divisão por origem, enxugamento do registro antigo e leitura só do que foi acrescentado. |
| `test_avaliacao.py` | Rodada com gerador e juiz falsos que produz todos os status; segunda rodada inteira do cache, sem chamada e sem custo; validação do gabarito importado; blocos somados por documento, reimportação que substitui o mesmo documento, prefixo no id repetido e revisão mantida; formato do NotebookLM (lista dentro de outra chave, `resposta`, `referencia`, `categoria`); conferência da seção citada, inclusive com várias seções e anexos; revisão que corrige, descarta e mede as revisadas. |
| `test_treino.py` | Ciclo completo com GPT e juiz falsos (perguntas por seção, metade com código, recusas, amostra fora da lista da prova, sinais gravados, segunda passada sem chamada); falha de busca que vira pergunta aprendida, entra no índice e é conferida por paráfrase, sem se perder num treino forçado; descarte por semelhança; amostra estável e espalhada; AUC e selo de confiança que só liga com exemplos suficientes. |
| `test_teams.py` / `test_webhook.py` | Assinatura HMAC, limpeza da menção, formatos de cartão e recusa de chamada sem assinatura. |

### `data/` — gerado neste PC (fora do git)

| Pasta | Conteúdo |
| --- | --- |
| `data/indice/` | `trechos.json` (texto e metadados de cada trecho: arquivo, código, revisão, vigência, seção e página, e o nome do arquivo de vetores) e `vetores-….npy` (embeddings). Refeito a cada indexação; o par anterior fica até a indexação seguinte. |
| `data/visao/` | Uma transcrição por imagem de conteúdo, pelo hash. É o que evita pagar a leitura da imagem de novo. |
| `data/cache/` | Respostas já geradas, uma por arquivo. Pode ser apagada; a próxima pergunta chama o GPT de novo. |
| `data/logs/consultas.jsonl` | Registro das consultas do chat, do terminal e do Teams. Alimenta Histórico, Uso e a nuvem de perguntas. As do chat ONS têm `origem: "ons"` e o SQL executado. |
| `data/logs/avaliacao.jsonl` | Registro das perguntas feitas pelas rodadas de avaliação. Entra só no Uso. |
| `data/logs/consultas.jsonl.bak` | Cópia do registro antigo, de antes de tirar os trechos e separar a avaliação. Pode ser apagada depois de conferir o Uso. |
| `data/ons/` | `catalogo.txt` (catálogo de datasets), `empresa-energimp.txt` (usinas e conjuntos do agente, por 24 horas), `contratos/` (contrato de cada dataset lido, por 24 horas) e `arquivos/` (lista dos arquivos parquet de cada dataset no S3, por 6 horas). Pode ser apagada; a próxima pergunta baixa de novo. As respostas do chat ONS ficam em `data/cache/` com nome começando por `ons-`. |
| `data/logs/retornos.jsonl` | Um registro por clique em Útil ou Não útil, com o identificador da consulta e o comentário. Vale o último de cada consulta. |
| `data/avaliacao/` | `gabarito.json` (os blocos importados, somados por documento), `gabarito.json.bak` (cópia de antes de recuperar a ITOM EGP 01; pode ser apagada), `revisao.json` (amostra e revisões do COG), `rodadas/` (uma rodada por arquivo, com o resultado de cada pergunta) e `juiz/` (vereditos do juiz em cache). Apagar `rodadas/` zera o gráfico. |
| `data/vetores/` | Vetores já calculados de cada texto, por modelo de embeddings. Reindexar só calcula o que mudou. Pode ser apagada; a próxima indexação calcula tudo de novo. |
| `data/treino/` | Um arquivo por documento com as perguntas de treino e os resultados, e `cache/` com as respostas do GPT do treino. |
| `data/aprendizado/perguntas.json` | Perguntas aprendidas por seção. Apagar faz a busca esquecer o que o treino ensinou. |
| `data/modelo/confianca.json` | Modelo do selo de confiança e a validação dele. |
| `data/modelos/` | Modelos locais, baixados no primeiro uso: embeddings `google/embeddinggemma-300m` (cerca de 1,2 GB) e, se usado, o reordenador `jinaai/jina-reranker-v2-base-multilingual` (cerca de 1,1 GB). |
| `data/medicao/` | Vetores de cada modelo testado pelo `medir_busca.py`. Pode ser apagada. |

## Canal do Teams (opcional)

O Teams exige um endereço HTTPS público. O outgoing webhook não existe em canal privado. Para usar mesmo assim:

1. Suba a API com `.\scripts\subir.ps1`.
2. Abra um túnel com hostname fixo até `http://127.0.0.1:8080`: copie `scripts\cloudflared.config.example.yml` para `scripts\cloudflared.yml`, preencha id do túnel, credenciais e hostname, e rode `.\scripts\tunel-cloudflare.ps1`. O modelo só deixa passar `/teams/outgoing`; mantenha a regra `path` ao preencher, senão a tela inteira fica aberta na internet.
3. No canal, crie um outgoing webhook apontando para `https://seu-hostname/teams/outgoing` e copie o token para `TEAMS_OUTGOING_HMAC_SECRET`.
4. Crie um incoming webhook (ou o fluxo "Post to a channel when a webhook request is received") e copie a URL para `TEAMS_INCOMING_WEBHOOK_URL`. `TEAMS_POST_FORMAT` aceita `adaptive`, `messagecard` ou `texto`.
5. Reinicie a API. No canal, mencione o webhook com a pergunta.

## Limites deste piloto

- Só responde com este PC ligado e a API no ar. Tudo escuta em `127.0.0.1`.
- O chat ONS depende da internet e do servidor do ONS no ar. Os números vêm dos dados abertos, que o ONS revisa; para decisão operacional, confirme nos canais oficiais. O `gpt-4.1-mini` às vezes escolhe o dataset errado ou desiste cedo: confira a fonte e o SQL da resposta.
- A citação aponta o arquivo local e a seção, não um link do SharePoint.
- Quadro em imagem depende da transcrição gravada em `data/visao/`. Confira os quadros críticos contra o PDF.
- A acertividade da aba Avaliação vale para o gabarito usado. Ela é tão boa quanto o gabarito: revise a amostra na aba Revisão do gabarito antes de apresentar o número.
- O texto sai do PDF pelo pypdf com layout, e o corte em seções depende dos títulos numerados. Parsers estruturais (Docling, unstructured) ficaram de fora: pedem PyTorch e modelos de vários GB e são lentos sem GPU. A conferência com o sumário avisa quando o corte falha; vale reavaliar se aparecerem documentos que o corte atual não resolve.
- A resposta é apoio à consulta. A execução continua sob a responsabilidade do operador e do normativo oficial.
