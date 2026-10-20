Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = ScriptDir
cmd = """" & ScriptDir & "\.venv\Scripts\python.exe"" """ & ScriptDir & "\main.py"""
For i = 0 To WScript.Arguments.Count - 1
    cmd = cmd & " " & WScript.Arguments(i)
Next
WshShell.Run cmd, 0, False
