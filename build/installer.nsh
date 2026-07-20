!include "nsDialogs.nsh"
!include "LogicLib.nsh"

Var PyTorchCheckbox
Var PyTorchChecked

Page custom PyTorchPageCreate PyTorchPageLeave

Function PyTorchPageCreate
  ; 默认勾选
  StrCpy $PyTorchChecked ${BST_CHECKED}

  ; 修改模式：检测 PyTorch 是否已安装
  ReadEnvStr $0 "LOCALAPPDATA"
  ${If} ${FileExists} "$0\Memo\python\python.exe"
    StrCpy $PyTorchChecked ${BST_CHECKED}
  ${EndIf}

  nsDialogs::Create 1018
  Pop $0

  ${NSD_CreateCheckbox} 0 10u 100% 12u "Install PyTorch for better voice detection (requires internet, ~200MB download)"
  Pop $PyTorchCheckbox
  ${NSD_SetState} $PyTorchCheckbox $PyTorchChecked

  nsDialogs::Show
FunctionEnd

Function PyTorchPageLeave
  ${NSD_GetState} $PyTorchCheckbox $PyTorchChecked
FunctionEnd

!macro customInstall
  ${If} $PyTorchChecked == ${BST_CHECKED}
    DetailPrint "Installing PyTorch (this may take several minutes)..."
    nsExec::ExecToLog 'powershell -ExecutionPolicy Bypass -File "$INSTDIR\resources\install-torch.ps1"'
    Pop $0
    ${If} $0 != 0
      MessageBox MB_OK|MB_ICONWARNING "PyTorch installation failed (exit code $0). You can install it later from the Settings page in Memo."
    ${EndIf}
  ${EndIf}
!macroend

!macro customUnInstall
  ReadEnvStr $0 "LOCALAPPDATA"
  RMDir /r "$0\Memo\python"
!macroend
