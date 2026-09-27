# DucksDrive (Rclone Drive GUI)

Uma interface gráfica leve e nativa (PySide6/Qt) para gerenciar e montar nuvens via Rclone no Linux. O objetivo do projeto é oferecer uma experiência "it just works", focada em integração com o sistema operacional e facilidade de uso (no estilo Google Drive para Windows).

## Recursos Principais

**Montagem FUSE Segura:** Gerencia o processo `rclone mount` em background, capturando sinais do sistema operacional (SIGINT/SIGTERM) para garantir a desmontagem limpa. Conta com trava de segurança crítica no desinstalador (`uninstall.sh`) para impedir a remoção forçada do diretório caso o FUSE ainda esteja acoplado, evitando corromper dados na nuvem.
**Painel de Configurações Dinâmico:** Personalização de parâmetros de desempenho através de um arquivo de configuração local (`~/.config/ducksdrive/config.json`), permitindo ao usuário ajustar limites de largura de banda (`--bwlimit`), cache VFS (`--vfs-cache-max-size`), transferências paralelas (`--transfers`) e inicialização automática sem mexer no código-fonte.
**Integração Nativa com File Managers:** Cria e remove atalhos dinamicamente no painel lateral dos gerenciadores de arquivos mais populares (Dolphin, Nautilus, Thunar, Nemo).
**System Tray Inteligente:** Ícone na bandeja do sistema com telemetria em tempo real via API RC (MB/s, ETA e status), além de menus de contexto para acesso rápido e painel de preferências.
**Auto-Mount e First-Run:** Abre a tela de configuração via OAuth automaticamente na primeira execução e monta a nuvem principal de forma silenciosa nas execuções seguintes.

## Casos de Uso e Limitações Técnicas

O aplicativo utiliza o FUSE com a flag `--vfs-cache-mode full` por padrão para garantir estabilidade e segurança na edição de arquivos. Compreenda onde isso brilha e onde falha:

✅ **Cold Storage:** Excelente para arquivamento de instaladores de jogos, ISOs e backups pesados.
✅ **Sincronização de Saves:** Funciona perfeitamente criando links simbólicos dos seus *saves* locais para a pasta montada.
✅ **Emulação (Retro Gaming):** ROMs leves (SNES, PS1, GBA) carregam rapidamente para a RAM e rodam sem problemas.
❌ **Jogos Nativos Pesados (Steam/Proton/AAA):** Não tente instalar e rodar jogos modernos direto da nuvem. O VFS Cache vai baixar os arquivos para o seu SSD local enquanto você joga e a latência causará *stuttering* severo ou travamentos no Proton.

## Pré-requisitos

`rclone` (versão recente compatível com `rclone config dump`)
`fuse3`
`python3` e `python3-venv`
git`

## Instalação

Abra o seu terminal e execute os comandos abaixo. O script cuidará de criar o ambiente virtual, instalar as dependências Python e registrar os atalhos no menu do sistema[cite: 2].

```bash
git clone https://github.com/Patopatetico2-lab/ducksdrive.git
cd ducksdrive
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
./uninstall.sh
