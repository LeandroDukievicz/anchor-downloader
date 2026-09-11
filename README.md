# TG Downloader

Gerenciador de downloads do Telegram no terminal, com navegacao por teclado e
mouse, painel de transferencias e paleta cyberpunk em ciano, magenta e verde.

A partir da versao 7.0.2, o aplicativo faz uma primeira varredura leve dos
metadados para calcular o numero e o tamanho total dos arquivos. Durante essa
etapa, o painel mostra `CALCULANDO VOLUME TOTAL` e atualiza o volume encontrado.
Antes do primeiro download, o total fica confirmado e passa a ser a referencia
fixa para o progresso da fila.

Na segunda passagem, os arquivos entram em uma fila interna limitada e os
downloads acontecem em paralelo, sem carregar todo o historico na RAM. O painel
mostra separadamente o volume total, o volume disponivel e a porcentagem. O
volume disponivel inclui arquivos concluidos, arquivos completos encontrados no
destino e bytes de arquivos `.part` que podem ser retomados. A tabela e
atualizada somente quando uma linha muda, reduzindo o uso de CPU e memoria em
filas grandes ou depois que uma transferencia termina.

## Executar

```sh
tg-downloader
tg-downloader --demo
tg-downloader --offline
tg-downloader --doctor
```

`--demo` abre uma simulacao identificada na interface, sem conectar ao Telegram.
`--offline` abre o painel sem conexao. `--doctor` verifica a instalacao local sem
exibir credenciais nem conectar ao Telegram.

Na janela `Novo download`, cole links usando `Ctrl+Shift+V` ou `Shift+Insert`. O campo mostra
`[OK] Link reconhecido` quando o formato foi aceito e so libera `Iniciar` depois dessa validacao.
Ao concluir a fila inteira, o programa atualiza o único arquivo
`telegram_downloader_log.txt` na pasta de destino informada. Ele lista todos os arquivos
do link e o resultado de cada um, por exemplo `1-arquivo1.mp4 status: ok`, `status: pulado`
ou `status: erro`.

"StringSession ativa" informa apenas que a conta esta autenticada. O estado da
transferencia aparece no cabecalho: `CALCULANDO VOLUME TOTAL`, `ESCANEANDO E
BAIXANDO`, `FINALIZANDO DOWNLOADS`, `DOWNLOAD FINALIZADO` ou `ERRO`. O painel de
instancias mostra o estado, arquivos finalizados/total, velocidade e canal de
cada janela.

Se uma versao anterior ficar aberta sem velocidade e consumindo muita memoria,
feche essa janela e execute `tg-downloader` novamente. Arquivos `.part` e o
manifesto sao preservados para retomada; a sessao da conta nao e apagada. Para
retomar uma fila encerrada, pressione `N` e informe novamente o mesmo link e a
mesma pasta de destino. O manifesto pula os arquivos completos e o `.part`
continua do ponto salvo.

## Instalar

Requer Linux e Python 3.11 ou superior, com suporte a `venv`.

```sh
./install.sh
```

O instalador cria um ambiente Python isolado no projeto, o comando
`~/.local/bin/tg-downloader`, a entrada no menu de aplicativos e o atalho na area
de trabalho. O comando funciona de qualquer pasta. `~/.local/bin` deve estar no
`PATH` do usuario; a instalacao nao usa `sudo`.

O projeto deve permanecer nesta pasta depois da instalacao, pois o comando aponta
para seu ambiente virtual. Para atualizar os atalhos apos mover a pasta, execute
`./install.sh` novamente.

## Dados Existentes

A configuracao e a sessao ja existentes sao reutilizadas:

- `~/.config/telegram-downloader/config.json`
- `~/.config/telegram-downloader/sessions/session_string`

O arquivo original `~/Downloads/telegram_downloader_v6_merged.py` foi preservado.
Os destinos de download existentes continuam disponiveis pela configuracao.

## Desenvolvimento

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
```
