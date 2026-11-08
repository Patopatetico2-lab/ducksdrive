# DucksDrive — Cliente de Nuvem e Interface Gráfica Rclone para Linux

<p align="center">
  <img width="237" height="42" alt="DucksDrive Logo" src="https://github.com/user-attachments/assets/6aa5909b-98e9-4117-a825-7c1d09ec7ce2" />
</p>

<p align="center">
  <a href="https://patopatetico2-lab.github.io/ducksdrive/"><img src="https://img.shields.io/badge/Site%20Oficial-patopatetico2--lab.github.io%2Fducksdrive-0078d4?style=flat&logo=googlechrome&logoColor=white" alt="Site Oficial" /></a>
  <a href="https://github.com/Patopatetico2-lab/ducksdrive/releases"><img src="https://img.shields.io/github/v/release/Patopatetico2-lab/ducksdrive?color=2ea44f&label=Releases&logo=github" alt="GitHub Releases" /></a>
  <a href="https://github.com/Patopatetico2-lab/ducksdrive/actions/workflows/ci.yml"><img src="https://github.com/Patopatetico2-lab/ducksdrive/actions/workflows/ci.yml/badge.svg" alt="CI Status" /></a>
  <a href="https://github.com/Patopatetico2-lab/ducksdrive/actions/workflows/ci.yml"><img src="https://img.shields.io/badge/tests-24%20passed-brightgreen?logo=pytest&logoColor=white" alt="Tests Status" /></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Code style: ruff" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=flat&logo=python&logoColor=white" alt="Python 3.10+" /></a>
  <a href="https://pypi.org/project/PySide6/"><img src="https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-41cd52?style=flat&logo=qt&logoColor=white" alt="PySide6 / Qt6" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/Licen%C3%A7a-MIT-yellow.svg" alt="MIT License" /></a>
</p>

> 🌐 **Site Oficial e Demonstração:** [https://patopatetico2-lab.github.io/ducksdrive/](https://patopatetico2-lab.github.io/ducksdrive/)  
> 📦 **Downloads e Pacotes Prontos (AppImage, Flatpak, .deb, Pacman):** [GitHub Releases](https://github.com/Patopatetico2-lab/ducksdrive/releases)

Interface gráfica em Python (PySide6/Qt6) para montar e gerenciar nuvens e unidades de rede pelo `rclone` e `FUSE3` no Linux. A experiência do cliente do Google Drive do Windows e do RaiDrive no Linux: ícone na bandeja do sistema, painel flutuante com telemetria em tempo real, controle de cache VFS e a nuvem integrada diretamente como uma pasta nativa no seu gerenciador de arquivos favorito.

Compatível com KDE Plasma, GNOME, XFCE, Cinnamon e outros ambientes de desktop Linux.

---

### Conteúdo
- [O que ele faz](#o-que-ele-faz)
- [Novidades da Versão 1.0.9](#novidades-da-versão-109)
- [Como configurar uma nuvem](#como-configurar-uma-nuvem)
- [Configurações](#configurações)
- [Arquitetura e Módulos do Projeto](#arquitetura-e-módulos-do-projeto)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Desinstalação](#desinstalação)
- [Solução de problemas](#solução-de-problemas)
- [Como Contribuir](#como-contribuir)
- [English Summary](#english-summary)

---

<img width="780" alt="Demonstração do DucksDrive" src="docs/assets/demo.gif" />

## O que ele faz

Clicando no ícone da bandeja, abre um painel flutuante com o uso de espaço da nuvem (quota), um botão para pausar ou retomar os envios na hora e a lista de transferências ativas: barra de progresso por arquivo, velocidade em MB/s, tempo restante (HH:MM:SS) e um gráfico com o histórico de velocidade de transferência, desenhado com `QPainter`.

A nuvem é montada via FUSE em `~/CloudDrives/{nome_do_remoto}` (ou em qualquer pasta customizada). Depois de pedir a montagem, o app confere por até 30 segundos se ela realmente aconteceu, sem travar. Um `QTimer` fica de olho no processo do FUSE em segundo plano, e se ele cair do nada o estado do app é atualizado. Ao fechar, o app espera até 15 segundos para o cache terminar de ser gravado na nuvem.

Na barra lateral do Dolphin (KDE) e dos gerenciadores GTK (Nautilus, Thunar, Nemo) ele cria um atalho com o nome do remoto. No KDE, antes de mexer no arquivo de favoritos, o app salva uma cópia em `user-places.xbel.bak`.

Quando não tem nada sendo transferido, o painel mostra os **arquivos enviados recentemente**. Se o drive estiver desconectado, o mesmo botão de pausar vira **Conectar / Reconectar**. O painel pode ser arrastado para qualquer lugar da tela (funciona no Wayland e no X11).

Antes de desconectar, trocar de nuvem ou sair, o app verifica se ainda tem arquivo na fila de envio do cache VFS. Se tiver, ele avisa e oferece a opção **Aguardar e sair**, que só fecha depois que tudo subiu.

No submenu de cada nuvem também dá para:
- **Montar (Somente Leitura)**, para não correr o risco de apagar ou alterar nada sem querer;
- **Testar Conexão**, que diz se a nuvem está respondendo e, se o token OAuth tiver expirado, oferece **reautorizar pelo navegador** na hora;
- **Configurações da Nuvem...**, para personalizar ponto de montagem, limites de banda e flags exclusivas do Rclone;
- **Remover** a nuvem (o `rclone.conf` ganha um backup `.bak` antes).

E no menu principal tem **Atualizar Pastas**, que recarrega a listagem da nuvem (`vfs/refresh`) sem precisar desmontar.

Os logs ficam em `~/.local/share/ducksdrive/ducksdrive.log`, com rotação automática de até 5 MB (`RotatingFileHandler`). Cada montagem também gera um log do rclone (`rclone-{remoto}-{data}.log`) na mesma pasta, e só os 5 mais recentes são mantidos.

<img width="360" alt="Painel em transferência" src="docs/assets/status-popup-active.png" /> <img width="360" alt="Painel conectado" src="docs/assets/status-popup-idle.png" />

---

## Novidades da Versão 1.0.9

A versão **1.0.9** traz a maior atualização de funcionalidades da história do DucksDrive:

```
               ┌────────────────────────────────────────────────────────┐
               │                 DucksDrive GUI / Tray                  │
               │   (Status Popup • Multi-Mount Selector • Log Viewer)   │
               └───────────────┬────────────────────────┬───────────────┘
                               │                        │
               ┌───────────────▼────────┐      ┌────────▼──────────────┐
               │    RcloneDaemon #1     │      │    RcloneDaemon #2    │
               │  Google Drive (RC 5572)│      │   OneDrive (RC 5573)  │
               └───────────────┬────────┘      └────────┬──────────────┘
                               │                        │
                 FUSE: ~/CloudDrives/gdrive     FUSE: ~/CloudDrives/onedrive
```

### 1. 🚀 Montagem Simultânea (Multi-Mount)
- **Conecte múltiplas nuvens simultaneamente**: acesse seu Google Drive de trabalho, seu OneDrive pessoal e seu servidor S3 ou Nextcloud ao mesmo tempo.
- **Isolamento de daemons e portas RC dinâmicas**: cada nuvem conectada roda em um processo `rclone mount` independente, com credenciais em memória exclusivas e portas RC alocadas dinamicamente (5572, 5573...) com ciclo de liberação e reciclagem livre de colisões.
- **Seletor rápido no painel flutuante**: dropdown no topo do painel para alternar instantaneamente as estatísticas, quota e gráfico de velocidade entre as nuvens ativas.
- **Ações individuais e Desconexão Geral Segura**: a bandeja oferece controle individual de montagem/desmontagem por nuvem e a opção **"Desconectar Todas as Nuvens"**, que inspeciona a fila VFS de cada daemon ativo para garantir que nenhum upload seja interrompido.

### 2. 📋 Visualizador de Logs Integrado (Log Viewer)
- **Janela diagnóstica em tempo real**: acesse os logs diretamente pelo menu da bandeja em **Visualizador de Logs...**.
- **Filtro de severidade e busca ao vivo**: filtre por `Todos`, `Avisos e Erros (WARN + ERR)` ou `Somente Erros (ERROR)`, com barra de pesquisa instantânea e auto-scroll inteligente.
- **Alternância entre fontes de log**: selecione entre o log geral do aplicativo (`ducksdrive.log`) ou os logs individuais gerados por cada remoto montado (`rclone-{remoto}-{timestamp}.log`).
- **Botão de cópia rápida**: copie com um clique todo o conteúdo formatado para a área de transferência para agilizar a abertura de chamados e suporte.

### 3. ⚙️ Configurações por Remoto (Per-Remote Settings)
- **Diálogo exclusivo por unidade de nuvem**: acessível via **Gerenciar Nuvens → [Sua Nuvem] → Configurações da Nuvem...**.
- **Ponto de montagem personalizado**: monte qualquer nuvem em diretórios customizados (ex.: `/mnt/dados/backup`, `~/Projetos/drive`).
- **Somente leitura por padrão**: force a montagem em modo read-only de drives sensíveis para proteção contra modificações ou exclusões acidentais.
- **Limites de banda dedicados**: configure velocidades específicas de download/upload por remoto (ex.: `5M`), sobrescrevendo a política global.
- **Flags adicionais do Rclone**: injete argumentos avançados do rclone exclusivos para aquela nuvem (ex.: `--drive-shared-with-me`, `--fast-list`, `--vfs-read-chunk-size=64M`).

### 4. 🔔 Notificações Nativas do Desktop
- **Despacho assíncrono via D-Bus**: integração nativa com o subsistema de notificações do Linux (`org.freedesktop.Notifications`) e `notify-send`.
- **Fallback resiliente**: se os serviços D-Bus não responderem, as mensagens são automaticamente encaminhadas via `QSystemTrayIcon.showMessage`.
- **Eventos informativos**: alertas nativos ao concluir lotes de sincronização em segundo plano, conectar/desconectar unidades com sucesso ou registrar falhas de rede.

### 5. 🪟 Suporte a GNOME Vanilla / Modo Janela Fallback
- **Inicialização sem travamento**: elimina o atraso de 30 segundos ao iniciar em distribuições GNOME puro (Fedora Workstation, Debian GNOME) onde extensões de bandeja (AppIndicator) não estão ativas.
- **Modo Janela / Dashboard**: quando nenhuma bandeja do sistema for detectada, o DucksDrive inicia automaticamente como janela padrão com todas as opções de gerenciamento, acompanhado de orientações instrutivas sobre como ativar a extensão AppIndicator caso prefira o ícone de bandeja.

### 6. 🔄 Verificador de Atualizações Integrado (Update Checker)
- **Checagem assíncrona não-bloqueante**: consulta periódica em segundo plano na API oficial de releases do GitHub, utilizando cache local de 24 horas para respeitar rate-limits de rede.
- **Botão de verificação manual**: botão **Verificar Atualizações** disponível no diálogo de Configurações para checagem sob demanda.
- **Notificação de novos pacotes**: emite aviso imediato com atalho direto para download de novas versões (AppImage, deb, pacman, flatpak).

---

## Como configurar uma nuvem

Pelo menu do ícone na bandeja, vá em **Gerenciar Nuvens → Adicionar Nova Nuvem...**. Dá para adicionar tanto serviços que fazem login pelo navegador (Google Drive, OneDrive, Dropbox, Box e pCloud) quanto servidores com credenciais diretas na janela (Amazon S3 / compatíveis como MinIO e Wasabi, Nextcloud / WebDAV, Mega, FTP e SFTP).

Depois de criada, é só escolher a nuvem no menu para montar.

<img width="400" alt="Nova nuvem OAuth" src="docs/assets/new-remote-oauth.png" /> <img width="400" alt="Nova nuvem S3" src="docs/assets/new-remote-s3.png" />

## Configurações

Ficam em `~/.config/ducksdrive/config.json` e dá para mexer pela tela de configurações do app:

- `--bwlimit`: limite de banda, tipo `10M`, ou `off` para sem limite (aplicado em tempo real via API RC sem precisar desmontar). Ajuda a não saturar a rede nem travar jogo online.
- `--vfs-cache-mode` e `--vfs-cache-max-size`: modo do cache (`full`, `writes`, `minimal` ou `off`), quanto espaço do SSD o cache pode usar (ex: `10G`), medidor do uso atual no SSD e botão **Limpar Cache Agora**.
- `--transfers`: quantos arquivos são sincronizados ao mesmo tempo, tipo `4`.
- Idioma: Português (Brasil) ou Inglês (EUA), aplicado na hora sem precisar reiniciar.
- Tema do Painel: Automático (segue o modo escuro/claro do sistema), Escuro (`#202326`) ou Claro (`#ffffff`).
- Reconexão inteligente (`last_remote`): lembra a última nuvem conectada e monta ela automaticamente ao iniciar.
- Iniciar com o sistema (autostart): liga e desliga pelo mesmo menu.
- Verificação de atualizações: botão manual para checar novas versões do DucksDrive no GitHub.

<img width="540" alt="Tela de configurações" src="docs/assets/settings-dialog.png" />

## Arquitetura e Módulos do Projeto

O DucksDrive é modular e orquestrado em Python puro com PySide6 (Qt6), dividindo as responsabilidades de forma desacoplada:

| Módulo / Arquivo | Responsabilidade Técnica |
|---|---|
| [`main.py`](main.py) | **Ponto de Entrada e Ciclo de Vida**: trava de instância única com `QLockFile`, checagem de ambiente desktop e bandeja do sistema com fallback para Modo Janela no GNOME, verificação de versão mínima do Rclone (`>= 1.63.0`) e captura de sinais (`SIGINT`/`SIGTERM`). |
| [`rclone_daemon.py`](rclone_daemon.py) | **Daemon de Montagem FUSE e Ciclo de Vida do Drive Virtual**: gerencia subprocessos concorrentes de montagem via `rclone mount`, aloca portas RC dinâmicas sem colisões, gera credenciais aleatórias em memória, orquestra flags VFS e executa desmontagem segura com fallback dinâmico para `fusermount3` ou `fusermount`. |
| [`gui_tray.py`](gui_tray.py) | **Interface da Bandeja (System Tray)**: ícone da área de notificação (`QSystemTrayIcon`), menus contextuais dinâmicos, suporte a Multi-Mount, checagem de uploads pendentes em todos os daemons e diálogos de autenticação OAuth no navegador. |
| [`status_popup.py`](status_popup.py) | **Painel Flutuante de Telemetria**: janela frameless (`Qt.Tool`) compatível com Wayland e X11, seletor de nuvem ativa, medidor de quota de armazenamento, progresso individual de arquivos, botão pausar/retomar em 1 clique e gráfico histórico de velocidade desenhado com `QPainter`. |
| [`log_viewer.py`](log_viewer.py) | **Visualizador de Logs Integrado**: janela diagnóstica em tempo real para `ducksdrive.log` e logs individuais de cada remoto, com filtros por severidade (`ALL`, `WARN+ERR`, `ERROR`), busca textual ao vivo e botão de cópia rápida para a área de transferência. |
| [`remote_settings_dialog.py`](remote_settings_dialog.py) | **Configurações por Remoto**: diálogo para personalização individual por drive (ponto de montagem personalizado, modo somente leitura padrão, limites de banda e flags extras como `--drive-shared-with-me`). |
| [`settings_dialog.py`](settings_dialog.py) | **Janela de Configurações Globais**: controle visual de limites de banda (`--bwlimit`), parâmetros de cache VFS em SSD, seleção de idioma em tempo de execução, botão de checagem de atualizações e rotina de desinstalação segura. |
| [`notifications.py`](notifications.py) | **Notificações Nativas do Desktop**: despacho assíncrono via D-Bus (`org.freedesktop.Notifications`) e `notify-send` com fallback para `QSystemTrayIcon` para avisos de conclusão de uploads, conexões e erros. |
| [`updater.py`](updater.py) | **Verificador de Atualizações**: checagem assíncrona não-bloqueante de novas versões via API do GitHub Releases com cache em disco de 24 horas e parsing semver. |
| [`rclone_stats.py`](rclone_stats.py) | **Coleta de Estatísticas RC**: polling periódico dos endpoints `/core/stats` e `/core/transferred` da API RC, calculando taxas de transferência (MB/s), ETA e histórico recente de uploads. |
| [`rclone_config.py`](rclone_config.py) | **Gerenciador de Configuração do Rclone**: leitura direta do arquivo `rclone.conf`, criação assíncrona de remotos em background, teste de conectividade (`rclone about`) e reconexão automática de tokens OAuth expirados. |
| [`rclone_settings.py`](rclone_settings.py) | **Persistência de Preferências**: carregamento e gravação em `~/.config/ducksdrive/config.json`, detecção de paleta e modo escuro/claro do sistema (KDE Plasma 6 e GNOME), persistência de `remotes_config` e ciclo de portas RC. |
| [`filemanager_integration.py`](filemanager_integration.py) | **Integração com Gerenciadores de Arquivos**: injeção atômica de atalhos na barra lateral do Dolphin (KDE `user-places.xbel` com backup de segurança `.bak`) e gerenciadores GTK (GNOME Nautilus, XFCE Thunar, Nemo). |
| [`i18n.py`](i18n.py) | **Internacionalização**: dicionário e função `tr()` para alternância instantânea entre Português (Brasil) e Inglês (EUA). |

## Requisitos

- Linux com **FUSE3** (`fuse3`)
- **rclone 1.63 ou mais novo** (o AppImage e o Flatpak já trazem o rclone embutido)
- Para os pacotes nativos e o script: **Python 3.10+** e **PySide6 6.6+**

## Instalação

### Arch Linux / CachyOS / Manjaro (Nativo via Pacman)
Instalação direta em 1 comando via Pacman:
```bash
curl -Lo /tmp/ducksdrive-1.0.9-1-any.pkg.tar.zst https://github.com/Patopatetico2-lab/ducksdrive/releases/download/v1.0.9/ducksdrive-1.0.9-1-any.pkg.tar.zst && sudo pacman -U /tmp/ducksdrive-1.0.9-1-any.pkg.tar.zst && rm -f /tmp/ducksdrive-1.0.9-1-any.pkg.tar.zst
```
Ou compilando direto do repositório com `makepkg`:
```bash
git clone https://github.com/Patopatetico2-lab/ducksdrive.git && cd ducksdrive && makepkg -si
```

### Debian / Ubuntu / Linux Mint / Zorin OS (Nativo via .deb)
Instalação direta em 1 comando via `apt`:
```bash
curl -Lo /tmp/ducksdrive_1.0.9-1_all.deb https://github.com/Patopatetico2-lab/ducksdrive/releases/download/v1.0.9/ducksdrive_1.0.9-1_all.deb && sudo apt install -y /tmp/ducksdrive_1.0.9-1_all.deb && rm -f /tmp/ducksdrive_1.0.9-1_all.deb
```

### Pelo script (instala no sistema)
```bash
git clone https://github.com/Patopatetico2-lab/ducksdrive.git
cd ducksdrive
chmod +x install.sh
./install.sh
```
O script detecta se a distro usa `apt` (Debian/Ubuntu), `pacman` (Arch), `dnf` (Fedora/RHEL) ou `zypper` (openSUSE), instala o `rclone` e o `fuse3`, cria um venv, instala o PySide6 e registra o atalho e o ícone do app.

### Pelo Flatpak
Baixe o `ducksdrive.flatpak` da aba de [Releases](https://github.com/Patopatetico2-lab/ducksdrive/releases) e instale (o rclone já vem junto; o runtime `org.kde.Platform 6.8` é baixado do Flathub automaticamente):
```bash
curl -Lo /tmp/ducksdrive.flatpak https://github.com/Patopatetico2-lab/ducksdrive/releases/download/v1.0.9/ducksdrive.flatpak && flatpak install --user -y /tmp/ducksdrive.flatpak && rm -f /tmp/ducksdrive.flatpak
```
> Para montar via FUSE e criar os atalhos no gerenciador de arquivos, o Flatpak precisa de acesso amplo ao sistema (`--filesystem=host` e `--device=all`).

### Pelo AppImage (portátil)
Baixe o `.AppImage` direto na aba de [Releases](https://github.com/Patopatetico2-lab/ducksdrive/releases) (ele já traz o binário do `rclone` junto), dê permissão de execução e rode:
```bash
curl -Lo DucksDrive-x86_64.AppImage https://github.com/Patopatetico2-lab/ducksdrive/releases/latest/download/DucksDrive-x86_64.AppImage
chmod +x DucksDrive-x86_64.AppImage
./DucksDrive-x86_64.AppImage
```
Ou gere um localmente rodando `./build_appimage.sh`.

## Desinstalação

O botão **Desinstalar** na tela de configurações do app desmonta as nuvens, limpa os atalhos e apaga as configurações. Ele pergunta antes se você quer apagar também as nuvens do rclone. Depois disso, remova o pacote de acordo com o jeito que você instalou:

| Instalado via | Como remover |
|---|---|
| pacman / makepkg | `sudo pacman -R ducksdrive` |
| .deb | `sudo apt remove ducksdrive` |
| Flatpak | `flatpak uninstall --delete-data com.github.patopatetico2.ducksdrive` |
| Script `install.sh` | `./uninstall.sh` na pasta do projeto |
| AppImage | o próprio botão **Desinstalar** já apaga o arquivo `.AppImage` |

## Solução de problemas

- **Logs:** Acesse pelo menu **Visualizador de Logs...** ou inspecione diretamente `~/.local/share/ducksdrive/ducksdrive.log` (app) e `~/.local/share/ducksdrive/rclone-*.log` (rclone). Anexe esses arquivos ao abrir uma [issue](https://github.com/Patopatetico2-lab/ducksdrive/issues).
- **Pasta travada / "Transport endpoint is not connected":** feche o app e rode `fusermount3 -uz ~/CloudDrives/NOME_DA_NUVEM`.
- **Nuvem parou de conectar do nada:** use **Gerenciar Nuvens → (sua nuvem) → Testar Conexão**. Se o token OAuth tiver expirado, o app oferece reautorizar pelo navegador.
- **Ambiente GNOME sem ícone na bandeja:** se você utiliza GNOME puro no Fedora, Debian ou Ubuntu sem bandeja, instale a extensão [AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/). O DucksDrive funcionará perfeitamente em Modo Janela enquanto a extensão não estiver presente.

## Como Contribuir

Adoramos receber contribuições da comunidade! Para instruções detalhadas sobre como configurar o ambiente de desenvolvimento local, rodar a suíte de testes com `pytest`, validar a formatação com `ruff` e enviar Pull Requests, consulte nosso **[Guia de Contribuição (CONTRIBUTING.md)](CONTRIBUTING.md)**.

Relatos de vulnerabilidades de segurança devem seguir as orientações em **[SECURITY.md](SECURITY.md)**.

---

<details>
<summary>English summary</summary>

### About
**DucksDrive** is a free, open-source Python (PySide6/Qt6) desktop client and GUI for mounting and managing cloud drives via `rclone` and `FUSE3` on Linux — bringing the Windows Google Drive for Desktop and RaiDrive experience to Linux. Works on KDE Plasma, GNOME, XFCE, Cinnamon, and other desktop environments.

- 🌐 **Official Website:** [https://patopatetico2-lab.github.io/ducksdrive/](https://patopatetico2-lab.github.io/ducksdrive/)
- 📦 **Download Releases:** [GitHub Releases](https://github.com/Patopatetico2-lab/ducksdrive/releases)

Designed as a modern, lightweight alternative to **Insync**, **RaiDrive**, **Rclone Browser**, and **RcloneTray** on Linux.

### Key Features (v1.0.9)
- **Simultaneous Multi-Mount**: Mount multiple cloud remotes concurrently (Google Drive, OneDrive, S3, Nextcloud) with independent `RcloneDaemon` processes, collision-free dynamic RC port cycling (5572+), separate mount paths, and a quick remote selector dropdown in the telemetry panel.
- **Integrated Log Viewer**: Live diagnostic window for `ducksdrive.log` and per-remote Rclone logs with severity filters (`ALL`, `WARN+ERR`, `ERROR`), real-time search, and 1-click clipboard export.
- **Per-Remote Settings**: Dedicated dialog to customize individual mount points, default read-only mode, per-cloud bandwidth limits, and extra Rclone flags (e.g. `--drive-shared-with-me`, `--fast-list`).
- **Native Desktop Notifications**: Asynchronous alerts via D-Bus (`org.freedesktop.Notifications`) and `notify-send` with graceful fallback to `QSystemTrayIcon.showMessage` on upload completions, mount state transitions, and connection drops.
- **GNOME Vanilla & Wayland Window Mode Fallback**: Zero-hang instant startup on tray-less desktop environments (such as vanilla GNOME on Fedora/Debian) with an automatic floating Dashboard and guidance on the AppIndicator extension.
- **Built-in Update Checker**: Background GitHub API release polling with a 24-hour disk cache and manual "Check for Updates" trigger in Settings.
- **Floating Telemetry Dashboard**: Storage quota meter, 1-click pause/resume or reconnect button, per-file progress bars, transfer speed (MB/s), ETA, and a rolling speed chart drawn with `QPainter`.
- **Sidebar Bookmarks**: Automatic sidebar shortcuts in **Dolphin (KDE)** (with `user-places.xbel.bak` backup), **Nautilus (GNOME)**, **Thunar (XFCE)**, and **Nemo (Cinnamon)**.
- **OAuth & Direct Cloud Setup**: Built-in wizard for OAuth providers (**Google Drive**, **OneDrive**, **Dropbox**, **Box**, **pCloud**) and direct credentials (**Amazon S3 / MinIO / Wasabi**, **Nextcloud / WebDAV**, **Mega**, **FTP**, **SFTP**).
- **VFS Cache & Data Safety**: Bandwidth limiting (`--bwlimit`, applied live via RC API), SSD VFS cache control with cleaner button, and a safety check offering to **wait for uploads to finish** before unmounting or exiting.

### Architecture & Project Structure
- [`main.py`](main.py): Application entrypoint, single-instance lock (`QLockFile`), tray detection & GNOME window mode fallback, signal handling (`SIGINT`/`SIGTERM`).
- [`rclone_daemon.py`](rclone_daemon.py): Multi-daemon FUSE manager, dynamic RC port allocation, in-memory basic auth, VFS cache flags, and clean unmount.
- [`gui_tray.py`](gui_tray.py): System tray icon (`QSystemTrayIcon`), dynamic menus, multi-mount orchestration, and OAuth browser flow.
- [`status_popup.py`](status_popup.py): Frameless telemetry panel with active remote switcher, live speeds, quota bar, and rolling transfer chart (`QPainter`).
- [`log_viewer.py`](log_viewer.py): Real-time diagnostic log viewer with log level filtering, instant text search, and clipboard export.
- [`remote_settings_dialog.py`](remote_settings_dialog.py): Per-remote preferences dialog (custom mount paths, read-only defaults, extra flags).
- [`settings_dialog.py`](settings_dialog.py): Global preferences dialog for bandwidth limits, VFS SSD cache, themes, language, and update checks.
- [`notifications.py`](notifications.py): Asynchronous desktop notifications (D-Bus `org.freedesktop.Notifications`, `notify-send`, and tray fallback).
- [`updater.py`](updater.py): Asynchronous GitHub release update checker with 24-hour cache and semver comparisons.
- [`rclone_stats.py`](rclone_stats.py): RC API polling (`/core/stats`, `/core/transferred`), transfer telemetry parser, and ETA calculator.
- [`rclone_config.py`](rclone_config.py): Remote listing, `rclone.conf` parser, connection tests, and OAuth reconnect.
- [`rclone_settings.py`](rclone_settings.py): JSON preference manager, system dark/light mode detection, and RC port lifecycle.
- [`filemanager_integration.py`](filemanager_integration.py): Sidebar bookmarks for KDE Dolphin and GTK file managers.
- [`i18n.py`](i18n.py): Lightweight internationalization engine (PT-BR / EN-US).

### Requirements & Installation
Requires Linux with FUSE3 and rclone 1.63+. Native packages need Python 3.10+ and PySide6 6.6+.
Download `.pkg.tar.zst` (Arch), `.deb` (Debian/Ubuntu), `.flatpak`, or portable `.AppImage` from [GitHub Releases](https://github.com/Patopatetico2-lab/ducksdrive/releases), or run `./install.sh`.

### Contributing
We welcome contributions! Please read our **[Contributing Guide (CONTRIBUTING.md)](CONTRIBUTING.md)** for local environment setup, running tests with `pytest`, linting with `ruff`, and submitting Pull Requests.
For security concerns, please review **[SECURITY.md](SECURITY.md)**.
</details>
