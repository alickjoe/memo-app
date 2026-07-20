!include "LogicLib.nsh"

!macro customInstall
  ReadEnvStr $0 "LOCALAPPDATA"
  ${If} ${FileExists} "$0\Memo\python\python.exe"
    DetailPrint "PyTorch already installed at $0\Memo\python"
  ${Else}
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON1 \
      "Install PyTorch for better voice detection?$\n$\nRequires internet and ~200MB download. You can also install later from Settings." \
      IDNO skipPyTorch
    DetailPrint "Installing PyTorch (this may take several minutes)..."
    DetailPrint "Log file: $TEMP\memo-torch-install.log"
    ; Use Tee-Object to show output AND save to log file
    nsExec::ExecToLog "powershell -ExecutionPolicy Bypass -Command $\"& '$INSTDIR\resources\install-torch.ps1' 2>&1 | Tee-Object -FilePath '$TEMP\memo-torch-install.log'$\""
    Pop $1
    ${If} $1 != 0
      MessageBox MB_OK|MB_ICONEXCLAMATION "PyTorch installation failed (exit code $1).$\n$\nCheck the log for details:$\n$TEMP\memo-torch-install.log$\n$\nYou can also install later from the Settings page in Memo."
    ${Else}
      DetailPrint "PyTorch installed successfully. Log: $TEMP\memo-torch-install.log"
    ${EndIf}
    skipPyTorch:
  ${EndIf}
!macroend

!macro customUnInstall
  ReadEnvStr $0 "LOCALAPPDATA"
  RMDir /r "$0\Memo\python"
!macroend
