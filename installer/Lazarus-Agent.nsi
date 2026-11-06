Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma
Icon "..\web\assets\lazarus.ico"
UninstallIcon "..\web\assets\lazarus.ico"

!ifndef SOURCE_EXE
  !error "Pass /DSOURCE_EXE=path-to-Lazarus-Agent.exe"
!endif
!ifndef OUTPUT
  !define OUTPUT "Lazarus-Agent-Setup.exe"
!endif
!ifndef VERSION
  !define VERSION "0.0.0"
!endif

!define APP_NAME "Lazarus Agent"
!define APP_EXE "Lazarus-Agent.exe"
!ifndef APP_REG_KEY
  !define APP_REG_KEY "Software\Lazarus Agent"
!endif
!ifndef UNINSTALL_REG_KEY
  !define UNINSTALL_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lazarus Agent"
!endif
!ifndef DATA_ROOT
  !define DATA_ROOT "$LOCALAPPDATA\Lazarus-Agent"
!endif
!include "FileFunc.nsh"
Var ClearCache
Var DeleteOutput
Var Parameters
Var OptionValue

Name "${APP_NAME} ${VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Lazarus Agent"
InstallDirRegKey HKCU "${APP_REG_KEY}" "InstallDir"
ShowInstDetails show
ShowUninstDetails show
SilentInstall normal
SilentUnInstall normal

Section "Lazarus Agent (required)" SecMain
  SetShellVarContext current
  SetAutoClose true
  SetOutPath "$INSTDIR"
  File /oname=${APP_EXE} "${SOURCE_EXE}"
  FileOpen $0 "$INSTDIR\.lazarus-install-complete" w
  FileWrite $0 "${VERSION}"
  FileClose $0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${APP_REG_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayIcon" '"$INSTDIR\${APP_EXE}",0'
  WriteRegDWORD HKCU "${UNINSTALL_REG_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_REG_KEY}" "NoRepair" 1
!ifndef NO_SHORTCUTS
  CreateDirectory "$SMPROGRAMS\Lazarus Agent"
  CreateShortcut "$SMPROGRAMS\Lazarus Agent\Lazarus Agent.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$DESKTOP\Lazarus Agent.lnk" "$INSTDIR\${APP_EXE}"
!endif
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  SetAutoClose true
!ifndef NO_SHORTCUTS
  Delete "$DESKTOP\Lazarus Agent.lnk"
  Delete "$SMPROGRAMS\Lazarus Agent\Lazarus Agent.lnk"
  RMDir "$SMPROGRAMS\Lazarus Agent"
!endif
  DeleteRegKey HKCU "${UNINSTALL_REG_KEY}"
  DeleteRegKey HKCU "${APP_REG_KEY}"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\.lazarus-install-complete"
  ; The default NSIS launcher can still hold its original image while the
  ; temporary uninstaller is cleaning up. Retry only this installed file.
  StrCpy $0 0
un_remove_launcher:
  ClearErrors
  Delete "$INSTDIR\Uninstall.exe"
  IfFileExists "$INSTDIR\Uninstall.exe" 0 un_launcher_removed
  IntOp $0 $0 + 1
  IntCmp $0 16 un_launcher_cleanup_failed un_wait_for_launcher un_launcher_cleanup_failed
un_wait_for_launcher:
  Sleep 200
  Goto un_remove_launcher
un_launcher_cleanup_failed:
  SetErrorLevel 2
un_launcher_removed:
  ; Default uninstall never touches local jobs, models or exported APKs.
  ; `/CLEARCACHE` only removes regenerable runtime cache. `/DELOUTPUT` is a
  ; separate explicit request because output may be user-deliverable evidence.
  StrCmp $ClearCache 1 un_clear_cache
  Goto un_after_clear_cache
un_clear_cache:
    RMDir /r "${DATA_ROOT}\temp"
    RMDir /r "${DATA_ROOT}\runtimes"
un_after_clear_cache:
  StrCmp $DeleteOutput 1 un_delete_output
  Goto un_after_delete_output
un_delete_output:
    RMDir /r "${DATA_ROOT}\output"
un_after_delete_output:
  ; Never recursively delete $INSTDIR: unexpected files are retained.
  RMDir "$INSTDIR"
SectionEnd

Function un.onInit
  SetShellVarContext current
  StrCpy $ClearCache 0
  StrCpy $DeleteOutput 0
  ${GetParameters} $Parameters
  ClearErrors
  ${GetOptions} $Parameters "/CLEARCACHE" $OptionValue
  IfErrors +2
    StrCpy $ClearCache 1
  ClearErrors
  ${GetOptions} $Parameters "/DELOUTPUT" $OptionValue
  IfErrors +2
    StrCpy $DeleteOutput 1
FunctionEnd
