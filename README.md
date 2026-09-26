# Rclone Drive GUI

Uma interface gráfica leve e nativa (PySide6/Qt) para gerenciar e montar nuvens via Rclone no Linux. O objetivo do projeto é oferecer uma experiência "it just works", focada em integração com o sistema operacional e facilidade de uso (no estilo Google Drive para Windows).

## Recursos Principais

* **Montagem FUSE Segura:** Gerencia o processo `rclone mount` em background, capturando sinais do sistema operacional (SIGINT/SIGTERM) para garantir a desmontagem limpa e evitar pontos de montagem travados.
* **Integração Nativa com File Managers:** Cria e remove atalhos dinamicamente no painel lateral do Dolphin (KDE) e Nautilus/Thunar/Nemo (GTK).
* **System Tray Inteligente:** Ícone na bandeja do sistema com telemetria em tempo real via API RC (MB/s, ETA e status), além de menus de contexto para acesso rápido.
* **Auto-Mount e First-Run:** Abre a tela de configuração via OAuth automaticamente na primeira execução e monta a nuvem principal de forma silenciosa nas execuções seguintes.

## Casos de Uso e Limitações Técnicas

O aplicativo utiliza o FUSE com a flag `--vfs-cache-mode full` para garantir estabilidade. Compreenda onde isso brilha e onde falha:

* ✅ **Cold Storage:** Excelente para arquivamento de instaladores de jogos, ISOs e backups pesados.
* ✅ **Sincronização de Saves:** Funciona perfeitamente criando links simbólicos dos seus *saves* locais para a pasta montada.
* ✅ **Emulação (Retro Gaming):** ROMs leves (SNES, PS1, GBA) carregam rapidamente para a RAM e rodam sem problemas.
* ❌ **Jogos Nativos Pesados (Steam/Proton/AAA):** Não tente instalar e rodar jogos modernos direto da nuvem. O VFS Cache vai baixar os arquivos para o seu SSD local enquanto você joga (não economizando espaço) e a latência causará *stuttering* severo ou travamentos no Proton.

## Pré-requisitos

* `rclone` (versão >= 1.73.5)
* `fuse3`
* `python3` e `python3-venv`
* `git`

## Instalação

Abra o seu terminal e execute os comandos abaixo. O script cuidará de criar o ambiente virtual, instalar as dependências Python e registrar os atalhos no menu do sistema.

```bash
git clone [https://github.com/Patopatetico2-lab/rclone-drive-gui.git](https://github.com/Patopatetico2-lab/rclone-drive-gui.git)
cd rclone-drive-gui
chmod +x install.sh
./install.sh
```
## Como Usar
Após a instalação, busque por Rclone Drive GUI no menu de aplicativos do seu sistema.
Se for o seu primeiro acesso, a tela de configuração saltará automaticamente pedindo os dados da nuvem. Caso contrário, o aplicativo iniciará discretamente na bandeja do sistema (perto do relógio) e fará a montagem da sua nuvem.

## Desinstalação
Para remover completamente o aplicativo, interromper os processos e limpar os atalhos do sistema, execute:
```bash 
pkill -f "python3.*main.py"
fusermount3 -uz ~/GoogleDrive || umount -l ~/GoogleDrive
rm -rf ~/.local/share/rclone-drive-gui
rm -f ~/.local/bin/rclone-drive-gui
rm -f ~/.local/share/applications/rclone-drive-gui.desktop
