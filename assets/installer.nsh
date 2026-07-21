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
    ; Use -File (not -Command) so PowerShell correctly propagates the script exit code
    nsExec::ExecToLog "powershell -ExecutionPolicy Bypass -File $\"$INSTDIR\resources\install-torch.ps1$\""
    Pop $1
    DetailPrint "install-torch.ps1 exit code: $1"
    ${If} $1 != 0
      MessageBox MB_OK|MB_ICONEXCLAMATION "PyTorch installation failed (exit code $1).$\n$\nCheck the log for details:$\n$TEMP\memo-torch-install.log$\n$\nYou can also install later from the Settings page in Memo."
    ${Else}
      DetailPrint "PyTorch installed successfully."
    ${EndIf}
    skipPyTorch:
  ${EndIf}
!macroend

!macro customUnInstall
  ; Only clean up managed Python during manual uninstall, NOT during silent upgrades
  IfSilent skipPythonCleanup
  ReadEnvStr $0 "LOCALAPPDATA"
  RMDir /r "$0\Memo\python"
  skipPythonCleanup:
!macroend
