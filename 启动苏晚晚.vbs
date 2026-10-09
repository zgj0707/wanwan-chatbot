' 苏晚晚无控制台启动器: 双击 = 直接弹出应用窗口, 全程无黑色命令行.
' 原理: WScript.Shell Run 第二参 0 = 隐藏 python.exe 的控制台窗口.
' 关闭应用窗口时服务随之退出 (run.py 的窗口模式行为).
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
exe = root & "\.venv\Scripts\python.exe"
script = root & "\run.py"

If Not fso.FileExists(exe) Then
  MsgBox "未找到虚拟环境, 请先运行一次 setup.bat.", 16, "苏晚晚"
  WScript.Quit 1
End If

' 0 = 隐藏控制台; False = 不等待进程结束 (run.py 自己管理生命周期)
shell.Run """" & exe & """ """ & script & """ --mode window", 0, False
