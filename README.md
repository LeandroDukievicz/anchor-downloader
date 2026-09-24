# Anchor Download

Gerenciador de downloads do Telegram com interface TUI moderna, responsiva e
orientada por teclado. Construído com Python, Textual e Telethon para baixar e
organizar grandes coleções sem carregar todo o histórico do canal na memória.

![Dashboard do Anchor Download](docs/screenshots/dashboard.png)

> Captura do modo de demonstração. Nenhuma conta ou transferência real foi
> utilizada na imagem. Cada janela do programa está documentada em
> [Telas do aplicativo](#telas-do-aplicativo).

## Recursos

- Dashboard cyberpunk para acompanhar arquivos, velocidade, ETA e progresso.
- Cálculo do tamanho total da coleção antes do primeiro download.
- Volume total, volume já disponível, porcentagem e espaço restante em tempo real.
- De 1 a 4 downloads paralelos por fila, configuráveis pela interface. O limite
  evita abrir fluxos que o serviço global não conseguiria atender e reduz a
  incidência de bloqueios temporários do Telegram.
- Dez ou mais janelas podem compartilhar uma única conexão principal com o
  Telegram; as transferências usam um limite global para não sobrecarregar a
  conta, a rede ou o computador.
- Retomada de arquivos `.part` depois de interrupções.
- Renovação automática de referências de mídia expiradas, sem perder o parcial.
- Watchdog de inatividade e retentativas progressivas para conexões que param de
  entregar dados.
- Renovação automática do transporte MTProto quando várias requisições seguidas
  ficam sem resposta, coordenada para não criar uma tempestade de reconexões.
- Retomada da varredura exatamente após a última mensagem se o serviço local for
  reiniciado ou o socket for interrompido durante uma fila longa.
- Espera automática para limites comuns e `FloodPremiumWait` de contas não
  Premium, coordenada entre todas as janelas.
- Detecção e salto de arquivos completos já presentes no destino.
- Fila limitada para manter o consumo de memória estável em canais grandes.
- Criptografia nativa com `cryptg`, evitando que a descriptografia em Python se
  torne um gargalo de transferência.
- Liberação periódica de buffers e tracebacks do serviço compartilhado para
  manter a memória estável durante execuções de vários dias.
- Organização automática em Fotos, Vídeos, Músicas, Áudios, Documentos,
  Legendas, GIFs, Stickers e Outros.
- Manifesto por destino e relatório final em texto.
- Atalho `Shift+O` no rodapé para abrir a pasta de destino no
  gerenciador de arquivos do sistema.
- Filtros, busca, detalhes do arquivo e controle individual da fila.
- Suporte a várias instâncias, com serviço local compartilhado e bloqueio contra
  gravações simultâneas no mesmo destino.
- Autenticação local por `StringSession`; não existe servidor intermediário.
- Uso completo pelo teclado e adaptação a terminais menores.

## Requisitos

- Ubuntu ou outra distribuição Linux. O serviço compartilhado usa sockets
  `AF_UNIX` e trava de arquivo por `fcntl`: não roda no Windows.
- Python 3.11 ou mais recente.
- `pipx` para instalar, ou `python3-venv` para rodar a partir do código.
- Conta do Telegram.
- `API ID` e `API Hash` obtidos em [my.telegram.org](https://my.telegram.org).

## Instalação

### Com pipx (recomendado)

O pipx instala o programa num ambiente isolado e deixa o comando disponível de
qualquer pasta, sem mexer no Python do sistema:

```bash
sudo apt update
sudo apt install -y pipx
pipx install anchor-downloader
pipx ensurepath
```

Abra um terminal novo e execute `anchor-downloader`.

Para atualizar depois, `pipx upgrade anchor-downloader`.

> Se você já tinha instalado pelo `install.sh`, remova o lançador antigo antes
> — os dois disputam o mesmo caminho `~/.local/bin/anchor-downloader`:
>
> ```bash
> rm -f ~/.local/bin/anchor-downloader
> ```

### Com snap

Disponível na Snap Store, com atualização automática:

```bash
sudo snap install anchor-downloader
```

O destino padrão `~/Downloads/Telegram` funciona assim que instala. Para baixar
num HD secundário ou externo, libere o acesso uma vez:

```bash
sudo snap connect anchor-downloader:removable-media
```

O aplicativo mostra esse comando pronto se você escolher um destino que ainda
não foi liberado.

### A partir do código-fonte

Para desenvolver, ou para rodar uma versão ainda não publicada:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

Clone o projeto e execute o instalador:

```bash
git clone https://github.com/LeandroDukievicz/anchor-downloader.git
cd anchor-downloader
./install.sh
```

O instalador cria:

- um ambiente virtual em `.venv`;
- o comando global `~/.local/bin/anchor-downloader`;
- uma entrada no menu de aplicativos;
- um atalho na área de trabalho.

Se o terminal ainda não encontrar o comando, adicione o diretório local ao
`PATH` e abra um novo terminal:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

O projeto deve permanecer no local em que foi instalado. Caso a pasta seja
movida, execute `./install.sh` novamente para atualizar os atalhos.

## Primeira configuração

1. Execute `anchor-downloader`.
2. Pressione `F3` para abrir as configurações.
3. Informe o `API ID`, o `API Hash` e o número de downloads simultâneos.
4. Selecione **Conectar**.
5. Informe o telefone com código do país, o código recebido pelo Telegram e,
   se solicitado, a senha de verificação em duas etapas.
6. Quando o cabeçalho mostrar `PRONTO`, pressione `N` para criar uma fila.
7. Informe o link do canal ou grupo, uma pasta absoluta de destino e confirme.

Cada uma dessas janelas aparece, com explicação, em
[Telas do aplicativo](#telas-do-aplicativo).

As credenciais de API e a sessão ficam somente no computador local, com
permissões restritas ao usuário.

## Telas do aplicativo

Todas as imagens desta seção saem do modo de demonstração
(`anchor-downloader --demo`): os arquivos, os tamanhos e as velocidades são
simulados, a configuração vem de uma pasta temporária e o destino é fixado em
`/home/usuario/...`. Nenhuma conta, canal, credencial ou caminho pessoal
aparece nelas. Para refazê-las depois de mexer na interface, veja
[Desenvolvimento e testes](#desenvolvimento-e-testes).

### Painel principal

![Painel principal do Anchor Download](docs/screenshots/dashboard.png)

É a tela que abre com o programa e onde se passa quase todo o uso. De cima
para baixo:

- **Cabeçalho:** nome, versão, conta conectada, estado da sessão
  (`PRONTO`, `CONECTANDO`, `DESCONECTADO`, `DEMO / DADOS SIMULADOS`) e relógio.
- **CONTA / SESSAO:** nome e `@usuário` da conta, tipo de sessão, canal da fila
  atual, número da instância e quantos downloads simultâneos ela usa.
- **TOTAL DA FILA:** volume total do canal, quanto já está disponível no
  destino e a barra de progresso da coleção inteira. O rótulo `confirmado`
  indica que a varredura de metadados terminou.
- **TRANSFERENCIA TOTAL:** histórico de velocidade somando todas as janelas
  abertas, com o número de instâncias ativas.
- **ATIVIDADE ATUAL:** velocidade instantânea, pico, volume disponível, total,
  ETA, arquivos concluídos, pulados e o que falta baixar.
- **ARQUIVOS:** a fila em si, com abas de filtro, posição, nome, progresso,
  velocidade e status de cada arquivo. O título do painel mostra quantos itens
  o filtro atual seleciona.
- **DETALHES DO ARQUIVO:** o item sob o cursor, com categoria e caminho de
  destino completo.
- **INSTANCIAS ATIVAS:** as outras janelas do programa que estão baixando.
- **Rodapé:** contagem por status e as teclas disponíveis.

### Configurações e conta (`F3`)

![Tela de configurações](docs/screenshots/configuracoes.png)

Guarda o `API ID` e o `API Hash` obtidos em
[my.telegram.org](https://my.telegram.org) e o número padrão de downloads
simultâneos. O campo do hash é mascarado e o `config.json` gravado fica com
permissão restrita ao usuário. **Salvar** apenas grava; **Conectar** grava e já
começa o login. Enquanto nada tiver sido configurado os campos aparecem
vazios, como na imagem.

### Entrar na conta

O login só acontece quando não existe sessão salva. São até três janelas em
sequência, e nenhuma delas escreve o que você digita no log.

![Pedido do telefone](docs/screenshots/login-telefone.png)

O telefone precisa do código do país, no formato `+5511999999999`. É por ele
que o Telegram decide para onde mandar o código de verificação.

![Pedido do código de verificação](docs/screenshots/login-codigo.png)

O código chega no aplicativo do Telegram ou por SMS. Espaços são ignorados.

![Pedido da senha de duas etapas](docs/screenshots/login-duas-etapas.png)

Esta janela só aparece se a conta tiver verificação em duas etapas ligada. O
campo é mascarado e a senha não vai para o disco: o que fica gravado é a
`StringSession` devolvida pelo Telegram.

### Novo download (`N`)

![Janela de novo download](docs/screenshots/novo-download.png)

Abre uma fila. São três campos: o canal ou link do Telegram (`@canal`, ID
numérico ou um endereço `t.me`), a pasta absoluta de destino e quantos
arquivos baixar ao mesmo tempo. Cada campo responde enquanto você digita — na
imagem o link foi reconhecido e a pasta ainda não existe, mas será criada.

![Novo download com link inválido](docs/screenshots/novo-download-link-invalido.png)

Quando o texto não tem forma de canal, a mensagem explica o que é aceito e o
botão **Iniciar** fica desabilitado. Essa verificação é local: o link só é
resolvido no Telegram depois da confirmação.

### Filtros da fila

A lista de arquivos tem cinco abas, percorridas com as setas ou com o mouse. O
contador no título do painel acompanha o filtro escolhido.

![Aba Ativos](docs/screenshots/aba-ativos.png)

**Ativos** reúne o que está em transferência ou pausado — é a aba para
acompanhar uma fila longa sem o ruído do que ainda nem começou.

![Aba Fila](docs/screenshots/aba-fila.png)

**Fila** mostra o oposto: o que ainda está esperando vez. Os arquivos entram
em transferência conforme os slots simultâneos vão sendo liberados.

![Aba Concluídos](docs/screenshots/aba-concluidos.png)

**Concluídos** junta o que terminou nesta execução e o que já estava completo
no destino e foi pulado.

![Aba Erros](docs/screenshots/aba-erros.png)

**Erros** fica vazia enquanto nada falhar, como na imagem. É o primeiro lugar
a olhar quando o relatório final acusa arquivos faltando. Sem nada
selecionado, o painel de detalhes também fica vazio.

### Busca (`/`)

![Busca por nome de arquivo](docs/screenshots/busca.png)

Abre um campo acima da tabela e filtra por parte do nome, somando-se à aba
selecionada. `Esc` limpa a busca e devolve o foco para a lista.

### Pausar e retomar (`Espaço` e `P`)

![Fila inteira pausada](docs/screenshots/fila-pausada.png)

`Espaço` pausa ou retoma o arquivo sob o cursor; `P` faz o mesmo com a fila
inteira. O status vira `Pausado`, a velocidade e o ETA zeram e nada é
descartado: os arquivos `.part` continuam no destino e a transferência segue
do ponto em que parou.

### Detalhes do arquivo (`Enter` ou `2`)

![Detalhes do arquivo em janela](docs/screenshots/detalhes-do-arquivo.png)

Mostra o item selecionado em uma janela: progresso, estado, bytes recebidos,
tamanho, velocidade, ETA, categoria, caminho completo de destino e o canal de
origem. É como ler o caminho inteiro quando o terminal é estreito demais para
o painel lateral.

### Instâncias ativas (`4`)

![Instâncias ativas](docs/screenshots/instancias.png)

Várias janelas do programa podem baixar ao mesmo tempo, compartilhando uma só
conexão com o Telegram. Esta janela lista uma linha por instância: o número do
slot, a fase em que ela está, quantos arquivos já terminaram sobre o total, a
velocidade e o canal que ela está baixando.

### Ajuda (`F1` ou `?`)

![Ajuda com a lista de teclas](docs/screenshots/ajuda.png)

A lista de teclas, à mão, sem sair do programa. A mesma tabela está em
[Atalhos de teclado](#atalhos-de-teclado).

### Encerrar (`Q` ou `Ctrl+C`)

![Confirmação de saída](docs/screenshots/sair.png)

Com uma fila em andamento, a saída pede confirmação. Os arquivos parciais são
preservados: abrir a mesma fila depois retoma de onde parou, sem baixar de
novo o que já está no disco. Sem fila em andamento, o programa fecha direto.

### A interface se adapta ao terminal

![Tabela completa em um terminal largo](docs/screenshots/tabela-completa.png)

Quando sobra espaço para a tabela, ela ganha duas colunas a mais: **Tamanho** e
**ETA** por arquivo, além da velocidade.

![Painel em um terminal estreito](docs/screenshots/layout-estreito.png)

No sentido contrário, abaixo de 145 colunas os painéis são compactados e,
abaixo de 90, os blocos de transferência, atividade e detalhes saem de cena e a
tabela fica no essencial: posição, nome, progresso e status. O `2` e o `4`
continuam abrindo detalhes e instâncias em janela, então nenhuma informação
fica inacessível.

## Como o download funciona

Cada fila passa por duas etapas:

1. **Cálculo do volume:** o programa percorre apenas os metadados, sem reter as
   mensagens, e mostra `CALCULANDO VOLUME TOTAL`.
2. **Transferência:** com o total confirmado, uma segunda passagem alimenta a
   fila limitada enquanto os arquivos são baixados em paralelo.

O campo **Dispon.** representa tudo que já está disponível no destino: arquivos
concluídos nesta execução, arquivos completos encontrados anteriormente e bytes
de partes retomáveis. O progresso usa o volume total confirmado como referência.

Ao finalizar, o destino contém:

- pastas organizadas por categoria;
- `.anchor_downloader_manifest.json`, usado para verificação e retomada;
- `anchor_downloader_log.txt`, com o resultado de cada arquivo.

## Atalhos de teclado

| Tecla | Ação |
| --- | --- |
| `↑` / `↓` ou `J` / `K` | Selecionar arquivo |
| `Tab` / `Shift+Tab` | Alternar entre painéis e campos |
| `Page Up` / `Page Down` | Percorrer listas longas |
| `Enter` | Abrir detalhes ou confirmar um campo |
| `N` | Criar um novo download |
| `Espaço` | Pausar ou retomar o arquivo selecionado |
| `P` | Pausar ou retomar toda a fila |
| `Delete` | Remover o arquivo selecionado da fila |
| `/` | Buscar um arquivo pelo nome |
| `1` | Focar a lista de arquivos |
| `2` | Focar os detalhes do arquivo |
| `4` | Focar o painel de instâncias |
| `Shift+O` | Abrir a pasta de destino |
| `R` | Reconectar usando a sessão salva |
| `F3` | Configurações e conta |
| `F1` ou `?` | Mostrar a ajuda integrada |
| `Esc` | Fechar modal ou voltar |
| `Q` ou `Ctrl+C` | Encerrar o programa |

Durante uma transferência, a saída pede confirmação e mantém os arquivos
parciais. Para continuar depois, abra uma nova fila com o mesmo link e a mesma
pasta de destino.

## Comandos disponíveis

```bash
anchor-downloader             # inicia normalmente
anchor-downloader --demo      # dashboard com dados simulados
anchor-downloader --offline   # abre sem conectar ao Telegram
anchor-downloader --doctor    # verifica a instalação sem conectar
anchor-downloader --version   # mostra a versão instalada
```

O modo `--demo` é a forma mais rápida de conhecer a interface sem configurar
uma conta.

## Arquivos locais

| Caminho | Finalidade |
| --- | --- |
| `~/.config/anchor-downloader/config.json` | API e concorrência |
| `~/.config/anchor-downloader/sessions/session_string` | Sessão autenticada |
| `~/.config/anchor-downloader/instances/` | Estado temporário das instâncias |
| `~/.config/anchor-downloader/broker.sock` | Canal privado do serviço compartilhado |
| `~/.config/anchor-downloader/diagnostic.log` | Diagnóstico persistente e rotativo |
| `<destino>/.anchor_downloader_manifest.json` | Controle dos arquivos do destino |
| `<destino>/anchor_downloader_log.txt` | Relatório da última fila concluída |

Esses arquivos estão ignorados pelo Git. Nunca publique `config.json` nem
`session_string`.

Instalado como snap, tudo o que está em `~/.config/anchor-downloader/` passa
para `~/snap/anchor-downloader/common/`: o confinamento não dá acesso a pastas
ocultas do primeiro nível da sua pasta pessoal. Os dois arquivos gravados no
destino não mudam de lugar. Use `anchor-downloader --doctor` para ver os
caminhos em uso na sua instalação.

## Diagnóstico

Execute:

```bash
anchor-downloader --doctor
```

O diagnóstico informa as versões, o interpretador utilizado e se a configuração
e a sessão existem. Ele não mostra credenciais e não se conecta ao Telegram.

Observações úteis:

- `StringSession ativa` significa que a conta está autenticada; o estado real da
  fila aparece no cabeçalho e nos campos de velocidade.
- Em canais extensos, o cálculo inicial pode demorar, mas o volume encontrado é
  atualizado durante a varredura.
- Em discos externos, confirme que a unidade continua montada e permite escrita.
- Se uma execução for interrompida, não apague o manifesto nem os arquivos
  `.part`; eles são necessários para uma retomada eficiente.
- Se a velocidade ficar zerada por falha de rede, o programa encerra a espera do
  bloco após 180 segundos, preserva o `.part` e tenta retomar com espera
  progressiva. O motivo e cada tentativa ficam em `diagnostic.log`.
- Erros remotos desconhecidos também preservam o `.part`; somente uma falha de
  integridade comprovada reinicia um arquivo desde o primeiro byte.
- O serviço compartilhado permanece ativo enquanto houver janelas abertas e é
  reiniciado automaticamente se o processo local cair.
- O serviço executa uma coleta preventiva a cada 30 segundos. Blocos já gravados
  e ciclos de exceções transitórias são liberados sem interromper a fila.

## Atualização

Se instalou pelo pipx:

```bash
pipx upgrade anchor-downloader
```

Se instalou a partir do código-fonte, dentro do diretório do projeto:

```bash
git pull --ff-only
./install.sh
```

## Desenvolvimento e testes

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m anchor_downloader --demo
```

As capturas de [Telas do aplicativo](#telas-do-aplicativo) são geradas pela
própria interface, num terminal virtual, e não à mão:

```bash
.venv/bin/python tools/screenshots.py
```

O script abre o modo de demonstração, percorre cada tela e grava os PNG em
`docs/screenshots/`. Ele lê a configuração de uma pasta temporária e fixa o
destino em `/home/usuario/...`, então as imagens nunca carregam credenciais nem
o caminho pessoal de quem as gerou. A conversão de SVG para PNG usa o Chrome ou
o Chromium instalado — é o que respeita a fonte monoespacada do terminal.

Estrutura principal:

```text
anchor-downloader/
├── docs/screenshots/       # imagens da documentação
├── src/anchor_downloader/
│   ├── app.py              # aplicação Textual e navegação
│   ├── auth.py             # autenticação segura no Telegram
│   ├── dialogs.py          # modais e formulários
│   ├── engine.py           # descoberta, fila, retomada e persistência
│   └── widgets.py          # componentes visuais
├── tests/                  # testes automatizados
├── tools/screenshots.py    # gera as capturas do README
├── install.sh              # instalação local e atalhos
└── pyproject.toml          # pacote e dependências
```
