# DucksDrive
<img width="237" height="42" alt="Image" src="https://github.com/user-attachments/assets/6aa5909b-98e9-4117-a825-7c1d09ec7ce2" />

Interface gráfica em Python (PySide6/Qt) para montar e gerenciar nuvens e unidades de rede pelo rclone no Linux. A ideia é ter algo parecido com o cliente do Google Drive do Windows: ícone na bandeja, um painel com o andamento das transferências e a nuvem aparecendo como uma pasta normal no gerenciador de arquivos.

Compatível com KDE Plasma, GNOME, XFCE e outros ambientes.

## O que ele faz

Clicando no ícone da bandeja, abre um painel flutuante com:
- **Quota de armazenamento da nuvem** em tempo real (`Usando X GB de Y GB`) com barra de ocupação;
- **Botão para Pausar / Retomar transferências** com 1 clique via API RC do rclone;
- **Lista rolável de transferências ativas**, com barra de progresso individual por arquivo, velocidade em MB/s, tempo restante (HH:MM:SS) e um gráfico com o histórico de velocidade de transferência desenhado com `QPainter`.

A nuvem é montada via FUSE em `~/CloudDrives/{nome_do_remoto}`. Depois de pedir a montagem, o app confere por até 10 segundos se ela realmente aconteceu, sem travar. Um `QTimer` fica de olho no processo do FUSE em segundo plano, e se ele cair do nada o estado do app é atualizado. Ao fechar, o app espera até 10 segundos para o cache terminar de ser gravado na nuvem.

Na barra lateral do Dolphin (KDE) e dos gerenciadores GTK (Nautilus, Thunar, Nemo) ele cria um atalho com o nome do remoto. No KDE, antes de mexer no arquivo de favoritos, o app salva uma cópia em `user-places.xbel.bak`.

Só uma instância roda por vez (`QLockFile`), para não dar conflito na porta do RC. Os logs ficam em `~/.local/share/ducksdrive/ducksdrive.log`, com rotação automática de até 5 MB (`RotatingFileHandler`).

<p align="center">
  <img width="380" alt="Painel Flutuante com Transferências Ativas" src="docs/assets/status-popup-active.png" />
  <img width="380" alt="Painel Flutuante Conectado" src="docs/assets/status-popup-idle.png" />
</p>

## Provedores Suportados

Pelo menu **Gerenciar Nuvens → Adicionar Nova Nuvem...**, você pode configurar tanto provedores com login no navegador (OAuth) quanto servidores próprios:
- **OAuth (1 clique no navegador):** Google Drive, Microsoft OneDrive, Dropbox, Box, pCloud
- **Credenciais diretas na interface:** Amazon S3 / S3-Compatible (MinIO, Wasabi, Cloudflare R2), Nextcloud / WebDAV, Mega, FTP e SFTP

<p align="center">
  <img width="410" alt="Configurar Nuvem OAuth" src="docs/assets/new-remote-oauth.png" />
  <img width="410" alt="Configurar Nuvem S3 / WebDAV / SFTP" src="docs/assets/new-remote-s3.png" />
</p>

## Configurações

Ficam em `~/.config/ducksdrive/config.json` e dá para mexer pela tela de configurações do app:

- `--bwlimit`: limite de banda (ex: `10 MB/s`) ou ilimitado (`off`). Ajuda a não saturar a rede nem travar jogo online.
- `--vfs-cache-mode` e `--vfs-cache-max-size`: modo do cache (`Full`, `Writes`, `Minimal`, `Off`) e quanto espaço do SSD o cache pode usar (ex: `10 GB`).
- `--transfers`: quantos arquivos são sincronizados ao mesmo tempo (1 a 16).
- **Idioma / Language:** Português (Brasil) e Inglês (EUA).
- **Iniciar com o sistema (autostart)** e botão de **Desinstalação completa** direto na interface.

<p align="center">
  <img width="560" alt="Configurações do DucksDrive" src="docs/assets/settings-dialog.png" />
</p>

## Instalação
```bash
git clone https://github.com/Patopatetico2-lab/ducksdrive.git
cd ducksdrive
chmod +x install.sh
./install.sh
```
O script detecta se a distro usa `apt` (Debian/Ubuntu), `pacman` (Arch), `dnf` (Fedora/RHEL) ou `zypper` (openSUSE), instala o `rclone` e o `fuse3`, cria um venv, instala o PySide6 e registra o atalho e o ícone do app.
## Desinstalação

```bash
./uninstall.sh
```
