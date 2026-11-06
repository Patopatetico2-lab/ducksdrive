# Política de Segurança / Security Policy

## Versões Suportadas / Supported Versions

| Versão / Version | Suportada / Supported |
|---|---|
| 1.0.x | :white_check_mark: |
| < 1.0.0 | :x: |

---

## Modelo de Segurança do DucksDrive

O **DucksDrive** foi projetado seguindo o princípio do menor privilégio e privacidade de dados locais:

1. **Credenciais da API RC em Memória:**
   - Durante a montagem, o aplicativo gera credenciais HTTP Basic Auth aleatórias e criptograficamente seguras (`secrets.token_urlsafe(24)`).
   - Essas credenciais residem exclusivamente na memória do processo e nunca são gravadas no disco.
   - A interface de controle remoto (RC) vincula-se apenas a `127.0.0.1`.

2. **Privacidade e Tokens de Autenticação:**
   - O DucksDrive não possui servidores externos, telemetria de terceiros ou rastreadores.
   - Todos os tokens OAuth e senhas de nuvem permanecem armazenados localmente no seu arquivo de configuração padrão do Rclone (`rclone.conf`).
   - Campos de senhas em diálogos utilizam modo de máscara (`QLineEdit.Password`).

3. **Operações no Sistema de Arquivos:**
   - Montagens e desmontagens utilizam ferramentas oficiais do FUSE (`fusermount3`, `fusermount` ou `umount`).
   - Modificações em arquivos de configuração do usuário (como atalhos de barra lateral no KDE `user-places.xbel`) realizam backups prévios (`.bak`) e escrita atômica via arquivo temporário.

---

## Como relatar uma vulnerabilidade / Reporting a Vulnerability

Se você identificou uma falha ou vulnerabilidade de segurança:

1. **Não** abra uma issue pública detalhando a exploração.
2. Utilize o recurso oficial do GitHub: **[Report a vulnerability](https://github.com/Patopatetico2-lab/ducksdrive/security/advisories/new)** na aba *Security* do repositório.
3. Alternativamente, entre em contato diretamente pelo e-mail do mantenedor listado nos commits do projeto.

Por favor, inclua:
- Descrição detalhada da vulnerabilidade;
- Passos para reproduzir;
- Versão do DucksDrive, do Rclone e ambiente operacional (distribuição Linux, desktop environment).

Agradecemos o relato responsável e nos comprometemos a analisar e corrigir potenciais problemas o mais rápido possível.
