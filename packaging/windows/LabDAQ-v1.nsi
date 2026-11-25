Unicode True
RequestExecutionLevel admin

!include "MUI2.nsh"
!include "x64.nsh"

!ifndef PAYLOAD_DIR
  !error "PAYLOAD_DIR must point to the PyInstaller onedir payload"
!endif
!ifndef OUTPUT_DIR
  !error "OUTPUT_DIR must point to the release artifact directory"
!endif
!ifndef BUILD_METADATA_DIR
  !error "BUILD_METADATA_DIR must contain generated build metadata"
!endif
!ifndef REPOSITORY_ROOT
  !error "REPOSITORY_ROOT must point to the repository root"
!endif

!define APP_NAME "LabDAQ"
!define APP_VERSION "1.0.0"
!define APP_PUBLISHER "LabDAQ internal laboratory project"
!define APP_REGISTRY_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\LabDAQRelease"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUTPUT_DIR}\LabDAQ-1.0.0-win64-setup.exe"
InstallDir "$PROGRAMFILES64\LabDAQ"
BrandingText "Laboratory release"
ShowInstDetails show
ShowUninstDetails show
SetCompressor /SOLID lzma
SetCompressorDictSize 32
!include "install-paths.nsh"

VIProductVersion "1.0.0.0"
VIAddVersionKey /LANG=1033 "ProductName" "LabDAQ"
VIAddVersionKey /LANG=1033 "FileDescription" "LabDAQ laboratory installer"
VIAddVersionKey /LANG=1033 "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright (c) 2026. All rights reserved."
VIAddVersionKey /LANG=1033 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${APP_VERSION} lab-release"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_TEXT "LabDAQ is installed for all Windows users. Launch LabDAQ from the Start menu after closing setup."
!define MUI_DIRECTORYPAGE_TEXT_TOP "Choose a dedicated application folder. This shared installation is available to every Windows user. Store recordings in a separate writable folder."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${REPOSITORY_ROOT}\docs\releases\LAB-RELEASE-NOTICE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "LabDAQ" SEC_MAIN
  SetShellVarContext all
  SetRegView 64
  Call ValidateDestination
  ${If} $PathError != ""
    MessageBox MB_OK|MB_ICONSTOP "$PathError"
    Abort
  ${EndIf}
  ${If} $RegisteredDir == $INSTDIR
    Call RemoveOwnedFiles
    ${If} $PathError != ""
      MessageBox MB_OK|MB_ICONSTOP "$PathError"
      Abort
    ${EndIf}
  ${EndIf}
  ClearErrors
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_DIR}\*.*"
  File "${REPOSITORY_ROOT}\LICENSE"
  File "${REPOSITORY_ROOT}\docs\releases\LAB-RELEASE-NOTICE.txt"
  File "${BUILD_METADATA_DIR}\build-info.json"
  File "${BUILD_METADATA_DIR}\THIRD-PARTY-NOTICES.txt"
  File "${BUILD_METADATA_DIR}\installed-files.txt"

  IfErrors 0 +3
    MessageBox MB_OK|MB_ICONSTOP "Could not install the application files."
    Abort
  SetOutPath "$INSTDIR\docs"
  File "${REPOSITORY_ROOT}\docs\releases\DAQ-1.0.0-lab-release.md"
  File "${REPOSITORY_ROOT}\docs\gui.md"
  File "${REPOSITORY_ROOT}\docs\ni-hardware.md"
  File "${REPOSITORY_ROOT}\docs\storage\recovery.md"

  SetOutPath "$INSTDIR\examples"
  File "${REPOSITORY_ROOT}\src\labdaq\resources\config\hardware-profiles\cdaq-9171-ni-9239-four-channel-v1.json"
  File "${REPOSITORY_ROOT}\src\labdaq\resources\config\experiments\four-channel-vibration-synthetic-v1.json"
  File "${REPOSITORY_ROOT}\src\labdaq\resources\config\simulator-scenarios\clean-four-channel-v1.json"

  IfErrors 0 +3
    MessageBox MB_OK|MB_ICONSTOP "Could not install the application documentation."
    Abort
  SetOutPath "$INSTDIR"
  ClearErrors
  CreateDirectory "$SMPROGRAMS\LabDAQ"
  CreateShortcut "$SMPROGRAMS\LabDAQ\LabDAQ — Simulated.lnk" "$INSTDIR\LabDAQ.exe" "--daq simulated" "$INSTDIR\LabDAQ.exe"
  CreateShortcut "$SMPROGRAMS\LabDAQ\LabDAQ — NI Hardware.lnk" "$INSTDIR\LabDAQ.exe" "" "$INSTDIR\LabDAQ.exe"
  CreateShortcut "$SMPROGRAMS\LabDAQ\Create LabDAQ Feedback Bundle.lnk" "$INSTDIR\LabDAQ.exe" "--feedback" "$INSTDIR\LabDAQ.exe"

  WriteRegStr HKLM "Software\LabDAQ\Release" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${APP_REGISTRY_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${APP_REGISTRY_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${APP_REGISTRY_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "${APP_REGISTRY_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${APP_REGISTRY_KEY}" "DisplayIcon" "$INSTDIR\LabDAQ.exe"
  WriteRegStr HKLM "${APP_REGISTRY_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKLM "${APP_REGISTRY_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${APP_REGISTRY_KEY}" "NoRepair" 1
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  IfErrors 0 +3
    MessageBox MB_OK|MB_ICONSTOP "Could not register the shared application or its uninstaller."
    Abort
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  SetRegView 64
  Call un.ValidateInstallPath
  ReadRegStr $RegisteredDir HKLM "Software\LabDAQ\Release" "InstallDir"
  ${If} $RegisteredDir != $INSTDIR
    StrCpy $PathError "This folder is not the registered shared LabDAQ installation."
  ${EndIf}
  ${If} $PathError == ""
    Call un.RemoveOwnedFiles
  ${EndIf}
  ${If} $PathError != ""
    MessageBox MB_OK|MB_ICONSTOP "$PathError"
    Abort
  ${EndIf}
  Delete "$SMPROGRAMS\LabDAQ\LabDAQ — Simulated.lnk"
  Delete "$SMPROGRAMS\LabDAQ\LabDAQ — NI Hardware.lnk"
  Delete "$SMPROGRAMS\LabDAQ\Create LabDAQ Feedback Bundle.lnk"
  RMDir "$SMPROGRAMS\LabDAQ"
  DeleteRegKey HKLM "${APP_REGISTRY_KEY}"
  DeleteRegKey HKLM "Software\LabDAQ\Release"
  Delete "$INSTDIR\installed-files.txt"
  Delete "$INSTDIR\Uninstall.exe"
  SetOutPath "$TEMP"
  RMDir "$INSTDIR"
SectionEnd

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "LabDAQ requires 64-bit Windows."
    Abort
  ${EndIf}
  SetShellVarContext all
  SetRegView 64
  ReadRegStr $RegisteredDir HKLM "Software\LabDAQ\Release" "InstallDir"
  ${If} $RegisteredDir != ""
    GetFullPathName $RegisteredDir "$RegisteredDir"
    StrCpy $INSTDIR $RegisteredDir
  ${EndIf}
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} "$0" "/D=" $1
  ${IfNot} ${Errors}
    StrCpy $INSTDIR $1
  ${EndIf}
FunctionEnd

Function .onVerifyInstDir
  Call ValidateDestination
  ${If} $PathError != ""
    Abort
  ${EndIf}
FunctionEnd
