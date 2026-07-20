!include "LogicLib.nsh"

!macro customInstall
  ; 检查是否已安装 PyTorch（避免重复询问）
  ReadEnvStr $0 "LOCALAPPDATA"
  ${If} ${FileExists} "$0\Memo\python\python.exe"
    DetailPrint "PyTorch already installed at $0\Memo\python"
  ${Else}
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON1 \
      "Install PyTorch for better voice detection?$\n$\nRequires internet and ~200MB download. You can also install later from Settings." \
      IDNO skipPyTorch
    DetailPrint "Installing PyTorch (this may take several minutes)..."
    nsExec::ExecToLog 'powershell -ExecutionPolicy Bypass -File "$INSTDIR\resources\install-torch.ps1"'
    Pop $1
    ${If} $1 != 0
      MessageBox MB_OK|MB_ICONWARNING \
        "PyTorch installation failed (exit code $1). You can install it later from the Settings page in Memo."
    ${EndIf}
    skipPyTorch:
  ${EndIf}
!macroend

!macro customUnInstall
  ReadEnvStr $0 "LOCALAPPDATA"
  RMDir /r "$0\Memo\python"
!macroend
