# MusicUnlock Windows 桌面版使用说明

1. 双击 MusicUnlock.exe，稍候数秒，直接进入 MusicUnlock 软件窗口。
2. 先选择“保持原样”或目标格式，并按需设置标签、封面及 EKey。
3. 再拖入音乐文件或文件夹，也可点击拖放区域选择文件，等待处理完成。
4. 点击单个下载或 ZIP 打包下载，在“另存为”对话框中选择保存位置。
5. 完成后关闭软件窗口，程序、本机服务及其运行组件一并退出。

桌面版不会打开 CMD/终端，也不会启动 Chrome、Edge 等外部浏览器。
窗口内部显示原有的转换界面。文件全程在本机处理，转换不会修改输入音乐。

这是 Windows x64 单文件 EXE，已包含 Python、pywebview/Python.NET 互操作组件、
转换依赖、页面资源、酷狗公钥及 FFmpeg；无需另装 Python 或 FFmpeg。
界面使用 Windows 的 WebView2 Runtime，本机已具备并经验证；没有安装或修改
任何系统组件。其他电脑也需要具备 WebView2 Runtime，尚未做他机验证。

单文件启动时需解包到系统临时目录，首次启动请等待几秒。EXE 所在目录需
可写，运行数据和滚动日志位于旁边的 MusicUnlock-data 文件夹；每次界面
使用独立私密缓存，退出时清理。临时转换结果仍由原引擎存放在系统临时目录
的 music-geshizhuanhuan-web 中，重要结果请通过保存对话框主动保存。

若无法打开窗口，程序会显示错误对话框；可查看
MusicUnlock-data 中最近的 MusicUnlock-数字.log 文件，每个实例独立记录日志。
启动失败时也会停止已启动的本机服务，错误对话框会显示对应日志的完整路径。

格式支持沿用原仓库：NCM、部分 QQ 音乐格式、KGM/KGMA/VPR 和老版 KWM。
部分新版 QQ 文件需要在 EKey 中提供相应密钥；KGG（酷狗 v5）暂不支持。
仅处理自己有权使用的音乐文件。

构建来源、Python/依赖版本、界面模式和 FFmpeg 身份见 build-info.json；
EXE 的 SHA256 见 SHA256SUMS.txt。许可证见 THIRD_PARTY_NOTICES.txt 和 licenses。

从源码构建需要 Python 3.12 x64、Git 和 Windows FFmpeg。依赖只安装在仓库
.venv；构建先运行测试，再生成窗口版 dist\MusicUnlock.exe：

```powershell
.\build_windows.ps1 -FFmpegPath '你的FFmpeg目录\bin\ffmpeg.exe' -FFmpegLicensePath '你的FFmpeg目录\LICENSE'
.\.venv\Scripts\python.exe scripts\smoke_windows_exe.py dist\MusicUnlock.exe
.\.venv\Scripts\python.exe scripts\check_desktop_exe.py dist\MusicUnlock.exe
```

--port 可指定本机服务端口；不指定则自动选择空闲端口。保留的
--no-browser 是高级 API 兼容模式，只启动服务，普通使用不需要这个参数。
