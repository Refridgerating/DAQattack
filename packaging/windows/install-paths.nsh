; Shared path/ownership policy. This include also runs in a non-elevating test harness.
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "TextFunc.nsh"

Var RegisteredDir
Var PathError
Var InventoryHandle
Var OwnedPath
Var Ancestor
Var Candidate

!macro InstallPathFunctions PREFIX
Function ${PREFIX}ValidateInstallPath
  StrCpy $PathError ""
  ${GetRoot} "$INSTDIR" $0
  ${If} $0 == ""
    StrCpy $PathError "Choose an absolute local application folder."
    Return
  ${EndIf}
  ; NSIS GetFullPathName returns empty for missing paths; the Win32 API also
  ; canonicalizes fresh destinations and inventory entries already removed.
  System::Call 'kernel32::GetFullPathNameW(w "$INSTDIR", i ${NSIS_MAX_STRLEN}, w .r1, p 0) i .r2'
  ${If} $2 == 0
  ${OrIf} $2 >= ${NSIS_MAX_STRLEN}
    StrCpy $PathError "Choose a valid application folder with a shorter path."
    Return
  ${EndIf}
  StrCpy $INSTDIR $1
  StrCpy $1 $INSTDIR 1 -1
  StrLen $2 $INSTDIR
  ${If} $1 == "\"
  ${AndIf} $2 > 3
    StrCpy $INSTDIR $INSTDIR -1
  ${EndIf}
  StrCpy $1 "$INSTDIR" 2
  ${If} $1 == "\\"
    StrCpy $PathError "Choose a local application folder."
    Return
  ${EndIf}
  ${If} $INSTDIR == "$0\"
  ${OrIf} $INSTDIR == "$0"
  ${OrIf} $INSTDIR == "$WINDIR"
  ${OrIf} $INSTDIR == "$SYSDIR"
  ${OrIf} $INSTDIR == "$PROGRAMFILES"
  ${OrIf} $INSTDIR == "$PROGRAMFILES64"
  ${OrIf} $INSTDIR == "$COMMONFILES"
  ${OrIf} $INSTDIR == "$COMMONFILES64"
  ${OrIf} $INSTDIR == "$APPDATA"
  ${OrIf} $INSTDIR == "$PROFILE"
    StrCpy $PathError "Choose a dedicated LabDAQ subfolder, not a drive or system folder."
    Return
  ${EndIf}
  ; No component of a managed path may redirect operations through a junction.
  StrCpy $Ancestor "$INSTDIR"
  Call ${PREFIX}CheckAncestors
FunctionEnd

Function ${PREFIX}CheckAncestors
  ${Do}
    System::Call 'kernel32::GetFileAttributesW(w "$Ancestor") i .r0'
    ${If} $0 != -1
      IntOp $0 $0 & 0x400
      ${If} $0 != 0
        StrCpy $PathError "Choose an application folder without filesystem links or junctions."
        Return
      ${EndIf}
    ${EndIf}
    ${GetParent} "$Ancestor" $0
    ${If} $0 == ""
    ${OrIf} $0 == $Ancestor
      Return
    ${EndIf}
    StrCpy $Ancestor $0
  ${Loop}
FunctionEnd

Function ${PREFIX}ValidateOwnedPath
  ${TrimNewLines} "$Candidate" $Candidate
  ; Canonicalize then enforce containment; never trust an inventory line as a command.
  System::Call 'kernel32::GetFullPathNameW(w "$INSTDIR\$Candidate", i ${NSIS_MAX_STRLEN}, w .r1, p 0) i .r2'
  ${If} $2 == 0
  ${OrIf} $2 >= ${NSIS_MAX_STRLEN}
    StrCpy $PathError "The installed file inventory contains an invalid path."
    Return
  ${EndIf}
  StrCpy $OwnedPath $1
  StrCpy $0 "$INSTDIR\"
  StrLen $1 $0
  StrCpy $2 $OwnedPath $1
  ${If} $2 != $0
    StrCpy $PathError "The installed file inventory contains an invalid path."
    Return
  ${EndIf}
  StrLen $1 $Candidate
  StrCpy $2 0
  ${DoWhile} $2 < $1
    StrCpy $0 $Candidate 1 $2
    ${If} $0 == ":"
    ${OrIf} $0 == "*"
    ${OrIf} $0 == "?"
      StrCpy $PathError "The installed file inventory contains an invalid path."
      Return
    ${EndIf}
    IntOp $2 $2 + 1
  ${Loop}
  StrCpy $Ancestor "$OwnedPath"
  Call ${PREFIX}CheckAncestors
FunctionEnd

Function ${PREFIX}ValidateInventory
  ClearErrors
  FileOpen $InventoryHandle "$INSTDIR\installed-files.txt" r
  ${If} ${Errors}
    StrCpy $PathError "The installed file inventory is missing or unreadable."
    Return
  ${EndIf}
  ${Do}
    ClearErrors
    FileReadUTF16LE $InventoryHandle $Candidate
    ${If} ${Errors}
      ${ExitDo}
    ${EndIf}
    Call ${PREFIX}ValidateOwnedPath
    ${If} $PathError != ""
      ${ExitDo}
    ${EndIf}
  ${Loop}
  FileClose $InventoryHandle
FunctionEnd

Function ${PREFIX}RemoveOwnedFiles
  Call ${PREFIX}ValidateInventory
  ${If} $PathError != ""
    Return
  ${EndIf}
  FileOpen $InventoryHandle "$INSTDIR\installed-files.txt" r
  ${Do}
    ClearErrors
    FileReadUTF16LE $InventoryHandle $Candidate
    ${If} ${Errors}
      ${ExitDo}
    ${EndIf}
    Call ${PREFIX}ValidateOwnedPath
    ${If} $PathError != ""
      ${ExitDo}
    ${EndIf}
    IfFileExists "$OwnedPath" 0 prune_empty
    ClearErrors
    Delete "$OwnedPath"
    ${If} ${Errors}
      StrCpy $PathError "Could not remove an application file. Close LabDAQ and retry."
      ${ExitDo}
    ${EndIf}
    prune_empty:
    ${GetParent} "$OwnedPath" $Ancestor
    ${DoWhile} $Ancestor != $INSTDIR
      ; Nonrecursive removal preserves unrecognized files in every subdirectory.
      RMDir "$Ancestor"
      ${GetParent} "$Ancestor" $Ancestor
    ${Loop}
  ${Loop}
  FileClose $InventoryHandle
FunctionEnd
!macroend

!insertmacro InstallPathFunctions ""
!ifndef LABDAQ_PATH_TEST
!insertmacro InstallPathFunctions "un."
!endif

Function ValidateDestination
  Call ValidateInstallPath
  ${If} $PathError != ""
    Return
  ${EndIf}
  ${If} $RegisteredDir != ""
  ${AndIf} $RegisteredDir != $INSTDIR
    StrCpy $PathError "A shared LabDAQ installation already exists. Uninstall it before changing its location."
    Return
  ${EndIf}
  ${If} $RegisteredDir == $INSTDIR
    Call ValidateInventory
    Return
  ${EndIf}
  FindFirst $0 $1 "$INSTDIR\*.*"
  ${DoWhile} $1 != ""
    ${If} $1 != "."
    ${AndIf} $1 != ".."
      StrCpy $PathError "Choose an empty folder or the registered shared LabDAQ installation."
      ${ExitDo}
    ${EndIf}
    FindNext $0 $1
  ${Loop}
  FindClose $0
FunctionEnd
