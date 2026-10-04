import os

def modify_file(filepath, replacements):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for old, new in replacements:
        content = content.replace(old, new)
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

rclone_settings_reps = [
    (
        'bin_path = shutil.which("ducksdrive") or os.path.expanduser("~/.local/bin/ducksdrive")\n        if not os.path.exists(bin_path):\n            bin_path = os.path.expanduser("~/.local/bin/ducksdrive")',
        'appimage_path = os.environ.get("APPIMAGE")\n        if appimage_path and os.path.exists(appimage_path):\n            bin_path = appimage_path\n        else:\n            bin_path = shutil.which("ducksdrive") or os.path.expanduser("~/.local/bin/ducksdrive")\n            if not os.path.exists(bin_path):\n                bin_path = os.path.expanduser("~/.local/bin/ducksdrive")'
    )
]

modify_file('rclone_settings.py', rclone_settings_reps)

main_reps = [
    (
        'QMessageBox.critical(\n            None,\n            "Already Running",\n            "An instance of DucksDrive is already running in the system tray."\n        )',
        'QMessageBox.critical(\n            None,\n            tr("Já em Execução"),\n            tr("Uma instância do DucksDrive já está em execução na bandeja do sistema.")\n        )'
    ),
    (
        'QMessageBox.critical(\n            None,\n            "System Tray Not Found",\n            "The system tray is not available. Please ensure a desktop environment is running."\n        )',
        'QMessageBox.critical(\n            None,\n            tr("Bandeja do Sistema Não Encontrada"),\n            tr("A bandeja do sistema não está disponível. Verifique se há um ambiente de desktop em execução.")\n        )'
    ),
    (
        'QMessageBox.critical(None, "Rclone Not Found", \n                             "Rclone is not installed or could not be found in PATH.")',
        'QMessageBox.critical(None, tr("Rclone Não Encontrado"), \n                             tr("O Rclone não está instalado ou não foi encontrado no PATH."))'
    ),
    (
        'QMessageBox.warning(None, "Rclone Outdated", \n                            f"Detected Rclone v{v_str}.\\nVersion v{req_str} or higher is recommended.")',
        'QMessageBox.warning(None, tr("Rclone Desatualizado"), \n                            tr("Detectado Rclone v%s.\\nA versão v%s ou superior é recomendada.") % (v_str, req_str))'
    ),
    (
        'from rclone_settings import load_config\nfrom i18n import set_language',
        'from rclone_settings import load_config\nfrom i18n import set_language, tr'
    )
]
modify_file('main.py', main_reps)

gui_tray_reps = [
    (
        'self.setWindowTitle("Configurar Nova Nuvem")',
        'self.setWindowTitle(tr("Configurar Nova Nuvem"))'
    ),
    (
        'self.name_input.setPlaceholderText("ex: meu_drive")',
        'self.name_input.setPlaceholderText(tr("ex: meu_drive"))'
    ),
    (
        'form.addRow("Nome da Nuvem:", self.name_input)',
        'form.addRow(tr("Nome da Nuvem:"), self.name_input)'
    ),
    (
        'form.addRow("Provedor:", self.type_combo)',
        'form.addRow(tr("Provedor:"), self.type_combo)'
    ),
    (
        'self.btn_create = QPushButton("Iniciar Autorização no Browser")',
        'self.btn_create = QPushButton(tr("Iniciar Autorização no Browser"))'
    ),
    (
        'self.btn_create.setText("Criar Nuvem WebDAV")',
        'self.btn_create.setText(tr("Criar Nuvem WebDAV"))'
    ),
    (
        'self.btn_create.setText("Criar Nuvem S3")',
        'self.btn_create.setText(tr("Criar Nuvem S3"))'
    ),
    (
        'self.btn_create.setText("Criar Nuvem Mega")',
        'self.btn_create.setText(tr("Criar Nuvem Mega"))'
    ),
    (
        'self.btn_create.setText(f"Criar Nuvem {rtype.upper()}")',
        'self.btn_create.setText(tr("Criar Nuvem %s") % rtype.upper())'
    ),
    (
        'self.btn_create.setText("Iniciar Autorização no Browser")',
        'self.btn_create.setText(tr("Iniciar Autorização no Browser"))'
    ),
    (
        'QMessageBox.warning(self, "Erro de Input", "O nome da nuvem não pode estar vazio.")',
        'QMessageBox.warning(self, tr("Erro de Input"), tr("O nome da nuvem não pode estar vazio."))'
    ),
    (
        'QMessageBox.warning(self, "Erro de Duplicado", f"Já existe uma nuvem com o nome \'{name}\'.")',
        'QMessageBox.warning(self, tr("Erro de Duplicado"), tr("Já existe uma nuvem com o nome \'%s\'.") % name)'
    ),
    (
        'self.action_open = QAction("Abrir Pasta Local", self)',
        'self.action_open = QAction(tr("Abrir Pasta Local"), self)'
    ),
    (
        'self.action_panel = QAction("Abrir Painel de Transferências", self)',
        'self.action_panel = QAction(tr("Abrir Painel de Transferências"), self)'
    ),
    (
        'self.action_connect = QAction("Conectar Nuvem...", self)',
        'self.action_connect = QAction(tr("Selecionar Nuvem para Conectar..."), self)'
    ),
    (
        'self.menu_remotes = self._menu.addMenu("Gerenciar Nuvens")',
        'self.menu_remotes = self._menu.addMenu(tr("Gerenciar Nuvens"))'
    ),
    (
        'self.action_settings = QAction("Configurações...", self)',
        'self.action_settings = QAction(tr("Configurações..."), self)'
    ),
    (
        'self.action_exit = QAction("Sair", self)',
        'self.action_exit = QAction(tr("Sair"), self)'
    ),
    (
        'label = f"✓ {remote} (Ativo)" if is_active else remote',
        'label = f"✓ {remote} (" + tr("Ativo") + ")" if is_active else remote'
    ),
    (
        'empty_action = QAction("Nenhuma nuvem configurada", self)',
        'empty_action = QAction(tr("Nenhuma nuvem configurada"), self)'
    ),
    (
        'self.action_unmount = QAction("Desconectar Atual", self)',
        'self.action_unmount = QAction(tr("Desconectar Atual"), self)'
    ),
    (
        'self.action_new = QAction("Configurar Nova Nuvem...", self)',
        'self.action_new = QAction(tr("Adicionar Nova Nuvem..."), self)'
    ),
    (
        'self.action_delete = QAction("Remover Nuvem...", self)',
        'self.action_delete = QAction(tr("Remover Nuvem..."), self)'
    ),
    (
        '"stopped": "Desconectado",',
        '"stopped": tr("Desconectado"),'
    ),
    (
        '"starting": "Conectando...",',
        '"starting": tr("Conectando..."),'
    ),
    (
        '"mounted": "Conectado",',
        '"mounted": tr("Conectado"),'
    ),
    (
        '"unmounting": "Desconectando...",',
        '"unmounting": tr("Desconectando..."),'
    ),
    (
        '"error": "Erro"',
        '"error": tr("Erro")'
    ),
    (
        'self.showMessage(APP_NAME, "Drive montado com sucesso.", QSystemTrayIcon.Information, 3000)',
        'self.showMessage(APP_NAME, tr("Drive montado com sucesso."), QSystemTrayIcon.Information, 3000)'
    ),
    (
        'QMessageBox.critical(None, "Mount Error", error)',
        'QMessageBox.critical(None, tr("Erro de Montagem"), error)'
    ),
    (
        'text = f"A enviar: {name} ({pct}%) • {stats.speed_str} • ETA: {stats.eta_str}"',
        'text = f"{tr(\'A enviar:\')} {name} ({pct}%) • {stats.speed_str} • {tr(\'ETA:\')} {stats.eta_str}"'
    ),
    (
        'text = f"Speed: {stats.speed_str} • ETA: {stats.eta_str}"',
        'text = f"{tr(\'Velocidade:\')} {stats.speed_str} • {tr(\'ETA:\')} {stats.eta_str}"'
    ),
    (
        'self.showMessage(\n                        APP_NAME,\n                        "Transferências concluídas! Todos os ficheiros foram sincronizados com a nuvem.",\n                        QSystemTrayIcon.Information,\n                        4000\n                    )',
        'self.showMessage(\n                        APP_NAME,\n                        tr("Transferências concluídas! Todos os ficheiros foram sincronizados com a nuvem."),\n                        QSystemTrayIcon.Information,\n                        4000\n                    )'
    ),
    (
        'self.action_status.setText("Status: Connection Lost")',
        'self.action_status.setText(tr("Status: Conexão Perdida"))'
    ),
    (
        'res = QMessageBox.warning(\n                None,\n                "Active Transfers",\n                "File transfers are currently in progress.\\nAre you sure you want to quit and interrupt them?",\n                QMessageBox.Yes | QMessageBox.No,\n                QMessageBox.No\n            )',
        'res = QMessageBox.warning(\n                None,\n                tr("Transferências Ativas"),\n                tr("Transferências de arquivos estão em andamento.\\nTem certeza que deseja sair e interrompê-las?"),\n                QMessageBox.Yes | QMessageBox.No,\n                QMessageBox.No\n            )'
    ),
    (
        'QMessageBox.warning(None, "Folder Missing", f"The directory {path} does not exist.")',
        'QMessageBox.warning(None, tr("Pasta Ausente"), tr("O diretório %s não existe.") % path)'
    ),
    (
        'QMessageBox.information(None, "Nenhuma Nuvem", "Não existem nuvens configuradas para remover.")',
        'QMessageBox.information(None, tr("Nenhuma Nuvem"), tr("Não existem nuvens configuradas para remover."))'
    ),
    (
        'QInputDialog.getItem(\n            None, "Remover Nuvem", "Selecione a nuvem que deseja remover:", remotes, 0, False\n        )',
        'QInputDialog.getItem(\n            None, tr("Remover Nuvem"), tr("Selecione a nuvem que deseja remover:"), remotes, 0, False\n        )'
    ),
    (
        'res = QMessageBox.warning(\n                None,\n                "Confirmar Exclusão",\n                f"Tem certeza que deseja excluir as configurações de \'{remote}\'?\\nEsta ação não pode ser desfeita.",\n                QMessageBox.Yes | QMessageBox.No,\n                QMessageBox.No\n            )',
        'res = QMessageBox.warning(\n                None,\n                tr("Confirmar Exclusão"),\n                tr("Tem certeza que deseja excluir as configurações de \'%s\'?\\nEsta ação não pode ser desfeita.") % remote,\n                QMessageBox.Yes | QMessageBox.No,\n                QMessageBox.No\n            )'
    ),
    (
        'QMessageBox.information(None, "Nuvem Removida", f"A nuvem \'{remote}\' foi removida com sucesso.")',
        'QMessageBox.information(None, tr("Nuvem Removida"), tr("A nuvem \'%s\' foi removida com sucesso.") % remote)'
    ),
    (
        'QMessageBox.critical(None, "Erro", f"Falha ao remover a nuvem \'{remote}\'.")',
        'QMessageBox.critical(None, tr("Erro"), tr("Falha ao remover a nuvem \'%s\'.") % remote)'
    ),
    (
        'msg = "No cloud remotes configured.\\nWould you like to configure one now?"\n            res = QMessageBox.question(None, "No Remotes", msg, QMessageBox.Yes | QMessageBox.No)',
        'msg = tr("Nenhuma nuvem configurada.\\nGostaria de configurar uma agora?")\n            res = QMessageBox.question(None, tr("Nenhuma Nuvem"), msg, QMessageBox.Yes | QMessageBox.No)'
    ),
    (
        'res = QMessageBox.question(\n                None,\n                "Switch Drive",\n                f"A drive is already connected ({self.daemon.current_remote}).\\nWould you like to disconnect it and connect to a new one?",\n                QMessageBox.Yes | QMessageBox.No,\n                QMessageBox.No\n            )',
        'res = QMessageBox.question(\n                None,\n                tr("Substituir Drive Ativo"),\n                tr("Um drive já está conectado (%s). Deseja desconectá-lo e conectar ao novo remoto configurado?") % self.daemon.current_remote,\n                QMessageBox.Yes | QMessageBox.No,\n                QMessageBox.No\n            )'
    ),
    (
        'remote, ok = QInputDialog.getItem(\n            None, "Connect Drive", "Select a cloud remote to mount:", remotes, 0, False\n        )',
        'remote, ok = QInputDialog.getItem(\n            None, tr("Conectar Nuvem"), tr("Selecione uma nuvem para conectar:"), remotes, 0, False\n        )'
    ),
    (
        'msg = f"Please complete OAuth in your browser for \'{name}\'..." if is_oauth else f"Configuring remote \'{name}\'..."',
        'msg = (tr("Por favor, complete a autorização no seu navegador para \'%s\'...") % name) if is_oauth else (tr("Configurando nuvem \'%s\'...") % name)'
    ),
    (
        'progress = QMessageBox(QMessageBox.Information, "Configuring", msg, QMessageBox.Cancel, parent=None)',
        'progress = QMessageBox(QMessageBox.Information, tr("Configurando"), msg, QMessageBox.Cancel, parent=None)'
    ),
    (
        'res = QMessageBox.question(\n                        None,\n                        "Substituir Drive Ativo",\n                        f"Um drive já está conectado ({self.daemon.current_remote}). Deseja desconectá-lo e conectar ao novo remoto configurado?",\n                        QMessageBox.Yes | QMessageBox.No,\n                        QMessageBox.No\n                    )',
        'res = QMessageBox.question(\n                        None,\n                        tr("Substituir Drive Ativo"),\n                        tr("Um drive já está conectado (%s). Deseja desconectá-lo e conectar ao novo remoto configurado?") % self.daemon.current_remote,\n                        QMessageBox.Yes | QMessageBox.No,\n                        QMessageBox.No\n                    )'
    ),
    (
        'QMessageBox.critical(None, "Configuration Failed", message)',
        'QMessageBox.critical(None, tr("Falha na Configuração"), message)'
    )
]
modify_file('gui_tray.py', gui_tray_reps)


status_popup_reps = [
    (
        'painter.drawText(rect, Qt.AlignCenter, "No active transfer speed")',
        'painter.drawText(rect, Qt.AlignCenter, tr("Sem velocidade de transferência ativa"))'
    ),
    (
        'self.status_badge = QLabel("● Offline")',
        'self.status_badge = QLabel(tr("● Offline"))'
    ),
    (
        'self.btn_close.setToolTip("Fechar Painel (ou pressione Esc)")',
        'self.btn_close.setToolTip(tr("Fechar Painel (ou pressione Esc)"))'
    ),
    (
        'self.quota_label = QLabel("Calculando espaço...")',
        'self.quota_label = QLabel(tr("Calculando espaço..."))'
    ),
    (
        'self.empty_label = QLabel("Nenhum arquivo sendo transferido")',
        'self.empty_label = QLabel(tr("Nenhum arquivo sendo transferido"))'
    ),
    (
        'metrics_layout.addWidget(QLabel("Restam:"))',
        'metrics_layout.addWidget(QLabel(tr("Restam:")))'
    ),
    (
        'self.btn_open = QPushButton("Abrir Pasta Local")',
        'self.btn_open = QPushButton(tr("Abrir Pasta Local"))'
    ),
    (
        'txt = f"Usando {format_bytes(used)} de {format_bytes(total)} ({percent}%)"',
        'txt = tr("Usando %s de %s (%s%%)") % (format_bytes(used), format_bytes(total), percent)'
    ),
    (
        'self.status_badge.setText("● Online")',
        'self.status_badge.setText(tr("● Online"))'
    ),
    (
        'self.status_badge.setText("● Offline")',
        'self.status_badge.setText(tr("● Offline"))'
    )
]
modify_file('status_popup.py', status_popup_reps)
