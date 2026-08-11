Option Explicit

Dim fileSystem, shell, scriptDirectory, projectRoot
Dim powerShellPath, powerShellScript, command, exitCode, launcherLog
Dim dialogTitle, failureMessage

Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fileSystem.GetParentFolderName(scriptDirectory)
powerShellPath = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
powerShellScript = scriptDirectory & "\open-service-hub.ps1"
launcherLog = projectRoot & "\logs\desktop-launcher.log"
dialogTitle = UnicodeText("672C 5730 670D 52A1 4E2D 5FC3")
failureMessage = dialogTitle & UnicodeText("542F 52A8 5931 8D25 3002") & _
    vbCrLf & vbCrLf & _
    UnicodeText("8BF7 67E5 770B 65E5 5FD7 FF1A") & launcherLog

command = Quote(powerShellPath) & _
    " -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " & Quote(powerShellScript)

' Window style 0 keeps the PowerShell console hidden. Waiting allows this wrapper
' to show a useful message if startup or the health check fails.
exitCode = shell.Run(command, 0, True)

If exitCode <> 0 Then
    shell.Popup _
        failureMessage, _
        0, _
        dialogTitle, _
        16
End If

Function Quote(ByVal value)
    Quote = Chr(34) & value & Chr(34)
End Function

Function UnicodeText(ByVal codePoints)
    Dim parts, index, result
    parts = Split(codePoints, " ")
    result = ""
    For index = 0 To UBound(parts)
        result = result & ChrW(CLng("&H" & parts(index)))
    Next
    UnicodeText = result
End Function
