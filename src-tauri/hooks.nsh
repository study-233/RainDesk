!macro NSIS_HOOK_PREINSTALL
  IfFileExists "$INSTDIR\agent\RainDeskAgent.exe" 0 done_preinstall
  nsExec::ExecToStack '"$INSTDIR\agent\RainDeskAgent.exe" --stop'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "RainDesk service stop failed ($0)."
    Abort
  ${EndIf}
  done_preinstall:
!macroend
!macro NSIS_HOOK_POSTINSTALL
  nsExec::ExecToStack '"$INSTDIR\agent\RainDeskAgent.exe" --install'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "RainDesk service setup failed ($0). $1"
    Abort
  ${EndIf}
!macroend
!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToStack '"$INSTDIR\agent\RainDeskAgent.exe" --uninstall'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "RainDesk service removal failed ($0). $1"
    Abort
  ${EndIf}
!macroend
