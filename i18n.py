
import os
from PySide6.QtCore import QObject

_TRANSLATIONS = {
    "en_US": {
        "Configurar Novo Drive": "Configure New Drive",
        "Configurações": "Settings",
        "Sair": "Exit",
        "Montar": "Mount",
        "Desmontar": "Unmount",
        "Drive não configurado.": "Drive not configured.",
        "Pronto para montar": "Ready to mount",
        "Iniciando montagem...": "Starting mount...",
        "DucksDrive - Conectado": "DucksDrive - Connected",
        "Desmontando...": "Unmounting...",
        "Atenção": "Warning",
        "O nome do drive não pode estar vazio.": "Drive name cannot be empty.",
        "DucksDrive - Escolha o Provedor": "DucksDrive - Choose Provider",
        "Selecione o Provedor:": "Select Provider:",
        "Nome do Drive (ex: meu_drive):": "Drive Name (e.g. my_drive):",
        "Continuar": "Continue",
        "Cancelar": "Cancel",
        "Erro ao configurar:": "Error configuring:",
        "Sucesso": "Success",
        "Drive '%s' configurado com sucesso!": "Drive '%s' configured successfully!",
        "Conectando ao navegador...": "Connecting to browser...",
        "Por favor, autorize no navegador (Google Drive)...": "Please authorize in the browser (Google Drive)...",
        "Velocidade:": "Speed:",
        "Baixado:": "Downloaded:",
        "Enviado:": "Uploaded:",
        "Transferências Ativas:": "Active Transfers:",
        "Quota indisponível": "Quota unavailable",
        "Nenhuma transferência": "No transfers",
        "Configurações do DucksDrive": "DucksDrive Settings",
        "Modo do Cache VFS:": "VFS Cache Mode:",
        "Tamanho Máximo do Cache VFS:": "Max VFS Cache Size:",
        "Largura de Banda (Upload/Download):": "Bandwidth Limit:",
        "Transferências Paralelas:": "Parallel Transfers:",
        "Iniciar DucksDrive automaticamente com o sistema operativo": "Start DucksDrive automatically on boot",
        "Salvar": "Save",
        "Cancelar": "Cancel",
        "Guardar Configurações": "Save Settings",
        "Aviso": "Warning",
        "Já existe uma instância do DucksDrive em execução.": "An instance of DucksDrive is already running.",
        "O DucksDrive ainda está encerrando suas atividades...": "DucksDrive is still shutting down...",
        "Erro": "Error",
        "Idioma / Language:": "Language:",
        "Idioma": "Language",
        "Português (Brasil)": "Portuguese (Brazil)",
        "Inglês (EUA)": "English (US)",
        "Preferências de Desempenho e Sistema": "Performance and System Preferences",
        "Rede e Transferência": "Network and Transfer",
        "Armazenamento e Cache SSD": "Storage and SSD Cache",
        "Integração com o Sistema": "System Integration"
    }
}

_current_lang = "pt_BR"

def set_language(lang: str):
    global _current_lang
    _current_lang = lang

def tr(text: str) -> str:
    if _current_lang == "pt_BR":
        return text
    return _TRANSLATIONS.get(_current_lang, {}).get(text, text)
