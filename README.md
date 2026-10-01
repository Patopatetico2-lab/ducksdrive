# DucksDrive 🦆☁️

O **DucksDrive** é uma interface gráfica (GUI) desktop moderna, leve e robusta desenvolvida em **Python (PySide6/Qt)** para o gerenciamento de serviços de nuvem e unidades de rede através do ecossistema **Rclone** no Linux (compatível com KDE Plasma, GNOME, XFCE e outros ambientes).

---

## 🚀 Principais Funcionalidades

1. **Painel Flutuante de Status (Estilo Google Drive Nativo)**:
   * Ao clicar no ícone da bandeja, uma janela flutuante exibe métricas em tempo real.
   * **Gráfico de Velocidade**: Linha do tempo com histórico de picos de upload/download renderizada via `QPainter`.
   * **Progresso e ETA**: Barra de progresso do arquivo atual, velocidade em MB/s e tempo estimado restante formatado em `HH:MM:SS`.
   * **Disparo Automático**: Abre sozinho quando uma transferência (upload ou download) é iniciada.

2. **Painel de Configurações Amigável (`~/.config/ducksdrive/config.json`)**:
   * **Limite de Banda (`--bwlimit`)**: Defina tetos de velocidade (ex: `10M` ou `off`) para evitar saturar a rede ou travar jogos online.
   * **Cache VFS Máximo (`--vfs-cache-max-size`)**: Controle o espaço no SSD reservado para arquivos temporários (ex: `2G`).
   * **Transferências Paralelas (`--transfers`)**: Ajuste quantos arquivos são sincronizados simultaneamente (ex: `4`).
   * **Início com o Sistema (Autostart)**: Ative/desative a inicialização automática direto pelo menu.

3. **Ciclo de Vida FUSE Robusto e Seguro**:
   * **Polling de Montagem**: Verificação ativa (até 10s) para garantir que o drive foi montado corretamente sem travar.
   * **Monitoramento de Saúde (`QTimer`)**: Detecta quedas inesperadas do processo FUSE em background e atualiza o estado do app.
   * **Flush Seguro de Cache**: Timeout estendido de 10 segundos no encerramento para garantir gravação íntegra na nuvem.

4. **Integração Nativa com Gerenciadores de Arquivos**:
   * **KDE (Dolphin) & GTK (Nautilus/Thunar/Nemo)**: Inserção automática de atalhos na barra lateral.
   * **Backup Automático**: Cria uma cópia de segurança (`user-places.xbel.bak`) antes de modificar o arquivo de favoritos do KDE.
   * **Branding Dinâmico**: O ponto de montagem e os marcadores utilizam nomes dinâmicos baseados na nuvem conectada (ex: `~/CloudDrives/{nome_do_remoto}`).

5. **Engenharia e Confiabilidade**:
   * **Instância Única (`QSharedMemory`)**: Impede execuções duplicadas e conflitos na porta RC.
   * **Logs em Disco (`RotatingFileHandler`)**: Gravação automática de eventos em `~/.local/share/ducksdrive/ducksdrive.log` (até 5MB, com rotação).
   * **Desinstalação Segura (`uninstall.sh`)**: Validação estrita com `mountpoint -q` que impede a exclusão do diretório se o drive ainda estiver montado, evitando perda de dados na nuvem.

---

## 📦 Como Instalar

Certifique-se de estar na pasta raiz do repositório clonado e execute o instalador automatizado:

```bash
git clone https://github.com/Patopatetico2-lab/ducksdrive.git
cd ducksdrive
chmod +x install.sh
./install.sh
```

O script detecta automaticamente a sua distribuição (Debian/Ubuntu via `apt` ou Arch Linux via `pacman`), instala as dependências necessárias (`rclone`, `fuse3`), configura o ambiente virtual Python (`venv`), instala o `PySide6` e registra o atalho e o ícone no seu sistema.

---

## 🛠️ Como Desinstalar

Para remover o aplicativo e todos os seus componentes de forma limpa e segura:

```bash
pkill -f "python3.*main.py"
./uninstall.sh
```

---

## ⚙️ Requisitos do Sistema

* **Linux** com ambiente gráfico (X11 ou Wayland).
* **Python 3.8+** com suporte a `venv`.
* **Rclone** (instalado automaticamente pelo script).
* **FUSE3** (`fuse3`).
