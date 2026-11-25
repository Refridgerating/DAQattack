Unicode True
RequestExecutionLevel user
SilentInstall silent
!define LABDAQ_PATH_TEST
!include "${REPOSITORY_ROOT}\packaging\windows\install-paths.nsh"
OutFile "${HARNESS_EXE}"

Section
  ${GetParameters} $0
  ${GetOptions} "$0" "/PATH=" $INSTDIR
  ${GetOptions} "$0" "/MODE=" $1
  StrCpy $RegisteredDir ""
  ${If} $1 == "shared"
    StrCpy $RegisteredDir "$INSTDIR"
  ${ElseIf} $1 == "relocate"
    StrCpy $RegisteredDir "$EXEDIR\existing-shared"
  ${EndIf}
  ${If} $1 == "remove"
    Call ValidateInstallPath
    ${If} $PathError == ""
      Call RemoveOwnedFiles
    ${EndIf}
  ${Else}
    Call ValidateDestination
  ${EndIf}
  FileOpen $0 "$EXEDIR\result.txt" w
  FileWrite $0 "$PathError"
  FileClose $0
SectionEnd
