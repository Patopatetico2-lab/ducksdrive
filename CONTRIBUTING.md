# Guia de Contribuição / Contributing Guide

[🇧🇷 Português](#-guia-de-contribuição-português) | [🇺🇸 English](#-contributing-guide-english)

---

## 🇧🇷 Guia de Contribuição (Português)

Agradecemos o seu interesse em contribuir com o **DucksDrive**! Este projeto é desenvolvido em código aberto e mantido com foco em qualidade, estabilidade e usabilidade para a comunidade Linux.

Contribuições de todos os tipos são muito bem-vindas: correção de bugs, novas funcionalidades, melhorias na documentação, internacionalização e testes em diferentes ambientes de desktop (GNOME, KDE Plasma, XFCE, Cinnamon, etc.).

---

### 1. Configurando o Ambiente de Desenvolvimento Local

#### 1.1 Pré-requisitos do Sistema
Para executar e testar o DucksDrive a partir do código-fonte, seu sistema precisa de:
- **Python 3.10 ou superior** com suporte a `venv` e `pip`.
- **Rclone 1.63.0 ou superior** instalado e acessível no `PATH` (`rclone version`).
- **FUSE3** (`fuse3` ou pacote `fuse` compatível).
- Dependências de sistema do Qt6 (OpenGL, XCB, D-Bus).

Exemplos de instalação de dependências nas principais distribuições:

**Ubuntu / Debian / Linux Mint / Pop!_OS:**
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip rclone fuse3 \
    libgl1-mesa-glx libegl1 libxkbcommon-x11-0 libdbus-1-3 libxcb-cursor0
```

**Arch Linux / CachyOS / Manjaro:**
```bash
sudo pacman -S --needed python python-pip rclone fuse3 qt6-base qt6-svg
```

**Fedora / RHEL:**
```bash
sudo dnf install -y python3 python3-pip rclone fuse3 qt6-qtbase-gui qt6-qtsvg
```

**openSUSE:**
```bash
sudo zypper install -y python3 python3-pip rclone fuse3
```

#### 1.2 Clonando o Repositório e Criando o Ambiente Virtual (venv)
Clone o repositório oficial ou seu fork:
```bash
git clone https://github.com/Patopatetico2-lab/ducksdrive.git
cd ducksdrive
```

Crie e ative um ambiente virtual isolado:
```bash
python3 -m venv venv
source venv/bin/activate
```

Instale as dependências de execução e de desenvolvimento:
```bash
pip install --upgrade pip
pip install -r requirements-dev.txt
```

> [!NOTE]
> O arquivo `requirements-dev.txt` inclui as dependências de produção do `requirements.txt` (`PySide6`, `requests`) e adiciona as ferramentas de desenvolvimento (`pytest`, `ruff`).

#### 1.3 Executando o DucksDrive Localmente
Com o ambiente virtual ativado:
```bash
python3 main.py
```

Para acompanhar os logs gerados em tempo real durante o desenvolvimento:
```bash
tail -f ~/.local/share/ducksdrive/ducksdrive.log
```

---

### 2. Testes Automatizados e Verificação de Qualidade

Antes de submeter qualquer código, é obrigatório garantir que a suíte de testes e os linters passem com 100% de sucesso.

#### 2.1 Executando os Testes com Pytest
Para rodar todos os testes unitários e de integração:
```bash
pytest
```

Para saída detalhada e exibição de cada teste executado:
```bash
pytest -v
```

Se você estiver em um ambiente sem servidor gráfico ativo (servidor SSH, contêiner Docker ou pipeline de CI):
```bash
QT_QPA_PLATFORM=offscreen pytest -v
```

#### 2.2 Verificação de Estilo com Ruff
Utilizamos o **Ruff** para análise estática e padronização de código Python (alinhado ao PEP 8, limite de linha 120):
```bash
ruff check .
```

Para aplicar correções automáticas seguras sugeridas pelo linter:
```bash
ruff check --fix .
```

#### 2.3 Escrevendo Novos Testes
- Todos os testes residem no diretório `tests/` (arquivo principal `tests/test_ducksdrive.py`).
- Ao adicionar novas funcionalidades ou corrigir bugs, inclua testes cobrindo o caminho feliz, casos de borda e tratamento de falhas.
- Isole chamadas de rede e comandos externos de sistema utilizando `unittest.mock` (`patch`, `MagicMock`).
- Para testar componentes visuais e sinais Qt, utilize a fixture de sessão `qapp`.

---

### 3. Diretrizes de Código e Arquitetura

O DucksDrive adota uma arquitetura modular, dividindo responsabilidades de forma desacoplada:

| Módulo | Papel no Sistema |
|---|---|
| `main.py` | Ponto de entrada, trava de instância única (`QLockFile`), checagem de bandeja e fallback de modo janela, orquestração geral. |
| `gui_tray.py` | Interface da bandeja do sistema (`QSystemTrayIcon`), menus contextuais, gestão do ciclo de vida das nuvens montadas. |
| `status_popup.py` | Painel flutuante de telemetria (Wayland/X11), seletor de nuvem concorrente, quotas, progresso individual e gráfico `QPainter`. |
| `rclone_daemon.py` | Gerenciamento de processos `rclone mount`, alocação de portas RC dinâmicas, credenciais em memória e desmontagem FUSE segura. |
| `remote_settings_dialog.py` | Diálogo de configurações específicas por nuvem (ponto de montagem customizado, modo somente leitura, flags extras). |
| `settings_dialog.py` | Diálogo de configurações globais do aplicativo (banda, cache VFS, tema, autostart, idioma). |
| `log_viewer.py` | Visualizador de logs diagnósticos em tempo real com filtros por severidade, busca textual e cópia rápida. |
| `notifications.py` | Sistema assíncrono de notificações de desktop (D-Bus `org.freedesktop.Notifications`, `notify-send` e fallback de bandeja). |
| `updater.py` | Verificador de atualizações assíncrono via GitHub Releases com cache de 24 horas e parsing semver. |
| `rclone_stats.py` | Polling dos endpoints RC (`/core/stats`, `/core/transferred`) e cálculo de velocidade/ETA. |
| `rclone_config.py` | Leitura e manipulação do `rclone.conf`, testes de conectividade e fluxo de reautorização OAuth. |
| `rclone_settings.py` | Persistência de configurações em JSON (`~/.config/ducksdrive/config.json`) e ciclo de portas RC. |
| `filemanager_integration.py` | Criação e remoção atômica de atalhos no Dolphin (KDE), Nautilus, Thunar e Nemo. |
| `i18n.py` | Sistema de internacionalização (PT-BR e EN-US). |

#### Regras de Implementação
1. **Thread Safety e Não-Bloqueio da Interface:** Nunca execute chamadas bloqueantes (I/O intensivo, downloads, requisições de rede ou subprocessos síncronos) na thread principal da GUI. Use `QThread`, workers assíncronos ou `QTimer`.
2. **Internacionalização Obrigatória (i18n):** Qualquer string exibida ao usuário deve ser envolvida na função `tr("chave", "Texto padrão em português")`. Certifique-se de registrar a tradução em inglês no dicionário `TRANSLATIONS_EN` em `i18n.py`.
3. **Segurança de Credenciais:** As credenciais da API RC (`rclone rcd`) devem ser geradas aleatoriamente em memória (`secrets.token_urlsafe`) e nunca gravadas no disco.
4. **Prevenção de Perda de Dados no Cache VFS:** Antes de desmontar remotos ou fechar o app, sempre inspecione a fila de uploads pendentes (`fetch_vfs_queue`). Se houver transferências pendentes, o usuário deve ser alertado.

---

### 4. Fluxo de Trabalho Git e Envio de Pull Requests

1. **Faça um Fork** do repositório no GitHub para sua conta.
2. **Crie uma Branch de Tópico** a partir da branch `main`:
   ```bash
   git checkout -b feat/minha-nova-funcionalidade
   # ou para correções:
   git checkout -b fix/correcao-conflito-portas
   ```
3. **Escreva Commits Semânticos** seguindo o padrão Conventional Commits:
   - `feat: adiciona seletor de temas no diálogo de configurações`
   - `fix: resolve falha na desmontagem com fusermount3`
   - `docs: atualiza instruções de dependências para Fedora`
   - `test: adiciona testes para o visualizador de logs`
   - `refactor: simplifica coleta de estatísticas RC`
4. **Valide Localmente:**
   ```bash
   ruff check .
   pytest -v
   ```
5. **Envie a Branch para o seu Fork:**
   ```bash
   git push origin feat/minha-nova-funcionalidade
   ```
6. **Abra um Pull Request (PR)** para a branch `main` do repositório oficial:
   - Forneça um título claro e descritivo.
   - Descreva a motivação da alteração, o que foi alterado e como os revisores podem testar manualmente.
   - Se aplicável, mencione a issue relacionada (ex.: `Fixes #42`).

---

### 5. Reportando Problemas e Sugerindo Ideias

- **Bugs:** Abra uma issue utilizando nosso [Template de Bug Report](https://github.com/Patopatetico2-lab/ducksdrive/issues/new?template=bug_report.yml). Inclua distribuição Linux, versão do Rclone, ambiente desktop e anexe os logs (`~/.local/share/ducksdrive/ducksdrive.log`).
- **Sugestões:** Abra uma issue através do [Template de Feature Request](https://github.com/Patopatetico2-lab/ducksdrive/issues/new?template=feature_request.yml) descrevendo o caso de uso e os benefícios esperados.
- **Vulnerabilidades de Segurança:** Siga as instruções descritas em nosso [SECURITY.md](SECURITY.md) para divulgação privada e responsável.

---

## 🇺🇸 Contributing Guide (English)

Thank you for your interest in contributing to **DucksDrive**! This project is free, open-source software built to deliver the best possible cloud storage mounting and tray management experience on Linux.

We welcome contributions of all levels: bug fixes, new features, UI/UX polish, documentation improvements, internationalization, and testing on diverse Linux desktop environments (GNOME, KDE Plasma, XFCE, Cinnamon, etc.).

---

### 1. Setting Up Your Local Development Environment

#### 1.1 System Prerequisites
To run and develop DucksDrive from source, you need:
- **Python 3.10 or newer** with `venv` and `pip`.
- **Rclone 1.63.0 or newer** in your system `PATH` (`rclone version`).
- **FUSE3** (`fuse3` or compatible FUSE library).
- Qt6 system dependencies (OpenGL, XCB, D-Bus).

Package installation examples:

**Ubuntu / Debian / Linux Mint / Pop!_OS:**
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip rclone fuse3 \
    libgl1-mesa-glx libegl1 libxkbcommon-x11-0 libdbus-1-3 libxcb-cursor0
```

**Arch Linux / CachyOS / Manjaro:**
```bash
sudo pacman -S --needed python python-pip rclone fuse3 qt6-base qt6-svg
```

**Fedora / RHEL:**
```bash
sudo dnf install -y python3 python3-pip rclone fuse3 qt6-qtbase-gui qt6-qtsvg
```

**openSUSE:**
```bash
sudo zypper install -y python3 python3-pip rclone fuse3
```

#### 1.2 Cloning and Creating the Virtual Environment
Clone your fork or the official repository:
```bash
git clone https://github.com/Patopatetico2-lab/ducksdrive.git
cd ducksdrive
```

Create and activate a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

Install production and development dependencies:
```bash
pip install --upgrade pip
pip install -r requirements-dev.txt
```

> [!NOTE]
> `requirements-dev.txt` installs runtime requirements from `requirements.txt` (`PySide6`, `requests`) alongside testing and linting tools (`pytest`, `ruff`).

#### 1.3 Running DucksDrive in Development Mode
With the virtual environment activated:
```bash
python3 main.py
```

To tail real-time debug and application logs:
```bash
tail -f ~/.local/share/ducksdrive/ducksdrive.log
```

---

### 2. Automated Tests & Code Quality

All Pull Requests must pass 100% of the automated test suite and linter checks before being merged.

#### 2.1 Running the Pytest Suite
Run the test suite:
```bash
pytest
```

Run with verbose test reporting:
```bash
pytest -v
```

When running in headless environments (SSH sessions, Docker containers, or CI pipelines):
```bash
QT_QPA_PLATFORM=offscreen pytest -v
```

#### 2.2 Linting and Formatting with Ruff
We use **Ruff** for fast static analysis and PEP 8 enforcement (target Python 3.10, 120 character max line length):
```bash
ruff check .
```

To automatically apply safe fixes:
```bash
ruff check --fix .
```

#### 2.3 Adding New Tests
- Test cases live under `tests/test_ducksdrive.py`.
- New features or bug fixes must include unit or integration tests covering normal flows and edge cases.
- Use `unittest.mock` (`patch`, `MagicMock`) for external network calls and Rclone child processes.
- Use the `qapp` session fixture for any tests that interact with Qt widgets, event loops, or signals.

---

### 3. Architecture & Code Conventions

DucksDrive is modular and cleanly decoupled:

| Module | Core Responsibility |
|---|---|
| `main.py` | Entrypoint, single-instance lock (`QLockFile`), tray detection & window mode fallback, app lifecycle. |
| `gui_tray.py` | System tray icon (`QSystemTrayIcon`), context menus, multi-daemon lifecycle management. |
| `status_popup.py` | Floating telemetry dashboard (Wayland/X11), active remote selector, quota bar, rolling speed chart (`QPainter`). |
| `rclone_daemon.py` | `rclone mount` process manager, dynamic RC port allocation, in-memory basic auth, clean FUSE unmount. |
| `remote_settings_dialog.py` | Per-remote preferences dialog (custom mount paths, read-only defaults, extra flags). |
| `settings_dialog.py` | Global application preferences (bandwidth limit, VFS cache settings, theme, autostart, language). |
| `log_viewer.py` | Integrated real-time diagnostic log viewer with log level filtering, instant text search, and clipboard export. |
| `notifications.py` | Asynchronous desktop notifications (D-Bus `org.freedesktop.Notifications`, `notify-send`, and tray fallback). |
| `updater.py` | Asynchronous GitHub release update checker with 24-hour cache and semver comparisons. |
| `rclone_stats.py` | RC API polling (`/core/stats`, `/core/transferred`) and speed/ETA telemetry calculation. |
| `rclone_config.py` | Direct parser of `rclone.conf`, remote health checks, and OAuth reconnect flows. |
| `rclone_settings.py` | JSON configuration storage (`~/.config/ducksdrive/config.json`) and RC port lifecycle. |
| `filemanager_integration.py` | Atomic sidebar bookmark creation for KDE Dolphin, Nautilus, Thunar, and Nemo. |
| `i18n.py` | Lightweight internationalization engine supporting PT-BR and EN-US. |

#### Key Engineering Guidelines
1. **Never Block the GUI Thread:** Do not perform network calls, long filesystem scans, or synchronous subprocess execution on the main Qt thread. Use `QThread`, worker objects, or `QTimer`.
2. **Mandatory Internationalization (i18n):** All visible UI strings must use `tr("key", "Default PT text")` and have an English counterpart registered in `TRANSLATIONS_EN` in `i18n.py`.
3. **In-Memory RC Credentials:** API credentials (`rclone rcd`) must always be generated randomly in memory (`secrets.token_urlsafe`) and never written to disk.
4. **Prevent VFS Upload Loss:** When unmounting or quitting, inspect the VFS upload queue (`fetch_vfs_queue`). Always prompt and offer to wait for pending uploads before shutting down daemons.

---

### 4. Git Workflow & Submitting Pull Requests

1. **Fork** the repository on GitHub.
2. **Create a topic branch** from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   # or for bug fixes:
   git checkout -b fix/issue-description
   ```
3. **Commit using Conventional Commits**:
   - `feat: add multi-mount support for simultaneous connections`
   - `fix: prevent RC port collisions on reconnect`
   - `docs: add installation instructions for Fedora`
   - `test: add unit tests for log viewer level filtering`
4. **Verify Locally:**
   ```bash
   ruff check .
   pytest -v
   ```
5. **Push your branch:**
   ```bash
   git push origin feat/your-feature-name
   ```
6. **Open a Pull Request:**
   - Provide a clear summary of changes and the problem solved.
   - Outline manual steps to reproduce or verify.
   - Reference open issues (e.g., `Closes #15`).

---

### 5. Reporting Issues & Feedback

- **Bug Reports:** Open an issue via our [Bug Report Template](https://github.com/Patopatetico2-lab/ducksdrive/issues/new?template=bug_report.yml). Attach distribution details and logs from `~/.local/share/ducksdrive/`.
- **Feature Requests:** Submit ideas through our [Feature Request Template](https://github.com/Patopatetico2-lab/ducksdrive/issues/new?template=feature_request.yml).
- **Security Vulnerabilities:** Follow the private reporting instructions in [SECURITY.md](SECURITY.md).
