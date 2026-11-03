Unicode true
RequestExecutionLevel user
SetShellVarContext current
SetCompressor /SOLID lzma

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

Name "${APP_NAME} ${VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Lazarus Agent"
InstallDirRegKey HKCU "Software\Lazarus Agent" "InstallDir"
ShowInstDetails show
ShowUninstDetails show
SilentInstall normal
SilentUnInstall normal
SetAutoClose true

Section "Lazarus Agent (required)" SecMain
  SetOutPath "$INSTDIR"
  File /oname=${APP_EXE} "${SOURCE_EXE}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Lazarus Agent" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lazarus Agent" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lazarus Agent" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lazarus Agent" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lazarus Agent" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lazarus Agent" "NoRepair" 1
  CreateDirectory "$SMPROGRAMS\Lazarus Agent"
  CreateShortcut "$SMPROGRAMS\Lazarus Agent\Lazarus Agent.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$DESKTOP\Lazarus Agent.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\Lazarus Agent.lnk"
  Delete "$SMPROGRAMS\Lazarus Agent\Lazarus Agent.lnk"
  RMDir "$SMPROGRAMS\Lazarus Agent"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lazarus Agent"
  DeleteRegKey HKCU "Software\Lazarus Agent"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\Uninstall.exe"
  ; Never recursively delete this folder: unexpected files are retained, and
  ; all task data lives outside $INSTDIR in the user's Lazarus-Agent data root.
  RMDir "$INSTDIR"
SectionEnd
