import os
import sys
import win32com.client

sys.stdout.reconfigure(encoding='utf-8')

def create_shortcuts():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable

    app_pyw = os.path.join(project_dir, "app.pyw")
    icon_path = os.path.join(project_dir, "app_icon.ico")
    desktop_dir = os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\leona"), "Desktop")

    shell = win32com.client.Dispatch("WScript.Shell")

    # 1. Atalho na Área de Trabalho (Desktop)
    if os.path.exists(desktop_dir):
        desktop_lnk = os.path.join(desktop_dir, "RNP to OBS - Painel de Controle.lnk")
        s = shell.CreateShortCut(desktop_lnk)
        s.TargetPath = pythonw
        s.Arguments = f'"{app_pyw}"'
        s.WorkingDirectory = project_dir
        if os.path.exists(icon_path):
            s.IconLocation = f"{icon_path},0"
        s.Description = "Painel de Controle da Transmissão RNP / BBB para o OBS Studio"
        s.Save()
        print(f"✅ Atalho criado na Área de Trabalho: {desktop_lnk}")

    # 2. Atalho dentro da pasta do projeto
    folder_lnk = os.path.join(project_dir, "RNP to OBS - Painel.lnk")
    s2 = shell.CreateShortCut(folder_lnk)
    s2.TargetPath = pythonw
    s2.Arguments = f'"{app_pyw}"'
    s2.WorkingDirectory = project_dir
    if os.path.exists(icon_path):
        s2.IconLocation = f"{icon_path},0"
    s2.Description = "Painel de Controle da Transmissão RNP / BBB para o OBS Studio"
    s2.Save()
    print(f"✅ Atalho criado na pasta do projeto: {folder_lnk}")

if __name__ == "__main__":
    create_shortcuts()
