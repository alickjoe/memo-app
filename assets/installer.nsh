!include "LogicLib.nsh"

!macro customInstall
  ReadEnvStr $0 "LOCALAPPDATA"
  ${If} ${FileExists} "$0\Memo\python\python.exe"
    DetailPrint "PyTorch already installed"
  ${Else}
    MessageBox MB_YESNO|MB_ICONQUESTION "Install PyTorch? You can also install later from Settings." IDNO skipPyTorch
    DetailPrint "User chose to install"
    skipPyTorch:
  ${EndIf}
!macroend

!macro customUnInstall
  ReadEnvStr $0 "LOCALAPPDATA"
  RMDir /r "$0\Memo\python"
!macroend
