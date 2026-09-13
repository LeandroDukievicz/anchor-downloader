# TG Downloader

Gerenciador de downloads do Telegram com interface TUI moderna, responsiva e
orientada por teclado. Construído com Python, Textual e Telethon para baixar e
organizar grandes coleções sem carregar todo o histórico do canal na memória.

![Dashboard do TG Downloader](docs/screenshots/dashboard.png)

> Captura do modo de demonstração. Nenhuma conta ou transferência real foi
> utilizada na imagem.

## Recursos

- Dashboard cyberpunk para acompanhar arquivos, velocidade, ETA e progresso.
- Cálculo do tamanho total da coleção antes do primeiro download.
- Volume total, volume já disponível, porcentagem e espaço restante em tempo real.
- De 1 a 10 downloads paralelos por fila, configuráveis pela interface.
- Dez ou mais janelas podem compartilhar uma única conexão principal com o
  Telegram; as transferências usam um limite global para não sobrecarregar a
  conta, a rede ou o computador.
- Retomada de arquivos `.part` depois de interrupções.
- Renovação automática de referências de mídia expiradas, sem perder o parcial.
- Watchdog de inatividade e retentativas progressivas para conexões que param de
  entregar dados.
- Espera automática para limites comuns e `FloodPremiumWait` de contas não
  Premium, coordenada entre todas as janelas.
- Detecção e salto de arquivos completos já presentes no destino.
- Fila limitada para manter o consumo de memória estável em canais grandes.
- Organização automática em Fotos, Vídeos, Músicas, Áudios, Documentos,
  Legendas, GIFs, Stickers e Outros.
- Manifesto por destino e relatório final em texto.
- Filtros, busca, detalhes do arquivo e controle individual da fila.
- Suporte a várias instâncias, com serviço local compartilhado e bloqueio contra
  gravações simultâneas no mesmo destino.
- Autenticação local por `StringSession`; não existe servidor intermediário.
- Uso completo pelo teclado e adaptação a terminais menores.

## Requisitos

- Ubuntu ou outra distribuição Linux.
- Python 3.11 ou mais recente.
- Pacote `python3-venv`.
- Conta do Telegram.
- `API ID` e `API Hash` obtidos em [my.telegram.org](https://my.telegram.org).

## Instalação no Ubuntu

Instale os requisitos do sistema:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

Clone o projeto e execute o instalador:

```bash
git clone https://github.com/LeandroDukievicz/tg-downloader.git
cd tg-downloader
./install.sh
```

O instalador cria:

- um ambiente virtual em `.venv`;
- o comando global `~/.local/bin/tg-downloader`;
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

1. Execute `tg-downloader`.
2. Pressione `F3` para abrir as configurações.
3. Informe o `API ID`, o `API Hash` e o número de downloads simultâneos.
4. Selecione **Conectar**.
5. Informe o telefone com código do país, o código recebido pelo Telegram e,
   se solicitado, a senha de verificação em duas etapas.
6. Quando o cabeçalho mostrar `PRONTO`, pressione `N` para criar uma fila.
7. Informe o link do canal ou grupo, uma pasta absoluta de destino e confirme.

As credenciais de API e a sessão ficam somente no computador local, com
permissões restritas ao usuário.

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
- `.telegram_downloader_manifest.json`, usado para verificação e retomada;
- `telegram_downloader_log.txt`, com o resultado de cada arquivo.

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
| `O` | Abrir a pasta de destino |
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
tg-downloader             # inicia normalmente
tg-downloader --demo      # dashboard com dados simulados
tg-downloader --offline   # abre sem conectar ao Telegram
tg-downloader --doctor    # verifica a instalação sem conectar
tg-downloader --version   # mostra a versão instalada
```

O modo `--demo` é a forma mais rápida de conhecer a interface sem configurar
uma conta.

## Arquivos locais

| Caminho | Finalidade |
| --- | --- |
| `~/.config/telegram-downloader/config.json` | API e concorrência |
| `~/.config/telegram-downloader/sessions/session_string` | Sessão autenticada |
| `~/.config/telegram-downloader/instances/` | Estado temporário das instâncias |
| `~/.config/telegram-downloader/broker.sock` | Canal privado do serviço compartilhado |
| `~/.config/telegram-downloader/diagnostic.log` | Diagnóstico persistente e rotativo |
| `<destino>/.telegram_downloader_manifest.json` | Controle dos arquivos do destino |
| `<destino>/telegram_downloader_log.txt` | Relatório da última fila concluída |

Esses arquivos estão ignorados pelo Git. Nunca publique `config.json` nem
`session_string`.

## Diagnóstico

Execute:

```bash
tg-downloader --doctor
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
  bloco após 90 segundos, preserva o `.part` e tenta retomar com espera
  progressiva. O motivo e cada tentativa ficam em `diagnostic.log`.
- Erros remotos desconhecidos também preservam o `.part`; somente uma falha de
  integridade comprovada reinicia um arquivo desde o primeiro byte.
- O serviço compartilhado permanece ativo enquanto houver janelas abertas e é
  reiniciado automaticamente se o processo local cair.

## Atualização

Dentro do diretório do projeto:

```bash
git pull --ff-only
./install.sh
```

## Desenvolvimento e testes

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m tg_downloader --demo
```

Estrutura principal:

```text
tg-downloader/
├── docs/screenshots/       # imagens da documentação
├── src/tg_downloader/
│   ├── app.py              # aplicação Textual e navegação
│   ├── auth.py             # autenticação segura no Telegram
│   ├── dialogs.py          # modais e formulários
│   ├── engine.py           # descoberta, fila, retomada e persistência
│   └── widgets.py          # componentes visuais
├── tests/                  # testes automatizados
├── install.sh              # instalação local e atalhos
└── pyproject.toml          # pacote e dependências
```
