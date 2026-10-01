# DucksDrive
<img width="237" height="42" alt="Image" src="https://github.com/user-attachments/assets/6aa5909b-98e9-4117-a825-7c1d09ec7ce2" />

Interface gráfica em Python (PySide6/Qt) para montar e gerenciar nuvens e unidades de rede pelo rclone no Linux. A ideia é ter algo parecido com o cliente do Google Drive do Windows: ícone na bandeja, um painel com o andamento das transferências e a nuvem aparecendo como uma pasta normal no gerenciador de arquivos.

Compatível com KDE Plasma, GNOME, XFCE e outros ambientes.

## O que ele faz

Clicando no ícone da bandeja, abre um painel flutuante com a transferência atual: barra de progresso do arquivo, velocidade em MB/s, tempo restante (HH:MM:SS) e um gráfico com o histórico de velocidade de upload e download, desenhado com `QPainter`. O painel também abre sozinho quando uma transferência começa.

A nuvem é montada via FUSE em `~/CloudDrives/{nome_do_remoto}`. Depois de pedir a montagem, o app confere por até 10 segundos se ela realmente aconteceu, sem travar. Um `QTimer` fica de olho no processo do FUSE em segundo plano, e se ele cair do nada o estado do app é atualizado. Ao fechar, o app espera até 10 segundos para o cache terminar de ser gravado na nuvem.

Na barra lateral do Dolphin (KDE) e dos gerenciadores GTK (Nautilus, Thunar, Nemo) ele cria um atalho com o nome do remoto. No KDE, antes de mexer no arquivo de favoritos, o app salva uma cópia em `user-places.xbel.bak`.

Só uma instância roda por vez (`QSharedMemory`), para não dar conflito na porta do RC. Os logs ficam em `~/.local/share/ducksdrive/ducksdrive.log`, com rotação automática de até 5 MB (`RotatingFileHandler`).
<img width="338" height="324" alt="b7031b58-5d46-4966-9a31-7f35d97a8aa1" src="https://github.com/user-attachments/assets/f59de122-61bb-4955-96d7-ece7503d27b0" />
<img width="434" height="200" alt="Image" src="https://github.com/user-attachments/assets/ce46bba7-628a-41cd-b313-b273a151adce" />
## Configurações

Ficam em `~/.config/ducksdrive/config.json` e dá para mexer pela tela de configurações do app:

- `--bwlimit`: limite de banda, tipo `10M`, ou `off` para sem limite. Ajuda a não saturar a rede nem travar jogo online.
- `--vfs-cache-max-size`: quanto espaço do SSD o cache pode usar, tipo `2G`.
- `--transfers`: quantos arquivos são sincronizados ao mesmo tempo, tipo `4`.
- Iniciar com o sistema (autostart): liga e desliga pelo mesmo menu.
<img width="658" height="511" alt="Image" src="https://github.com/user-attachments/assets/2f297300-14e3-42ca-aa62-cba54b874846" />

## Instalação
```bash
git clone https://github.com/Patopatetico2-lab/ducksdrive.git
cd ducksdrive
chmod +x install.sh
./install.sh
```
O script detecta se a distro usa `apt` (Debian/Ubuntu) ou `pacman` (Arch), instala o `rclone` e o `fuse3`, cria um venv, instala o PySide6 e registra o atalho e o ícone do app.
## Desinstalação

```bash
pkill -f "python3.*main.py"
./uninstall.sh
```
O primeiro comando fecha o app caso esteja aberto. O `uninstall.sh` não apaga o diretório de montagem enquanto o drive ainda estiver montado (ele checa com `mountpoint -q`), para não apagar arquivos da nuvem sem querer.
