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
的 MusicUnlock-web 中，重要结果请通过保存对话框主动保存。

若无法打开窗口，程序会显示错误对话框；可查看
MusicUnlock-data 中最近的 MusicUnlock-数字.log 文件，每个实例独立记录日志。
启动失败时也会停止已启动的本机服务，错误对话框会显示对应日志的完整路径。

格式支持沿用原仓库：NCM、部分 QQ 音乐格式、KGM/KGMA/VPR 和老版 KWM。
部分新版 QQ 文件需要在 EKey 中提供相应密钥；KGG（酷狗 v5）暂不支持。
仅处理自己有权使用的音乐文件。

构建来源、Python/依赖版本、界面模式和 FFmpeg 身份见 build-info.json；
EXE 的 SHA256 见 SHA256SUMS.txt。许可证见 THIRD_PARTY_NOTICES.txt 和 licenses。
这两个文件记录手动构建时的快照，软件内更新不会刷新旁边的快照文件。
内置更新后的当前构建和上游提交以“设置”显示的信息为准；如需校验当前 EXE，
可用 PowerShell 的 Get-FileHash -Algorithm SHA256 重新计算实际文件摘要。

点击右上角“设置”，再点击“检测更新”，会查询原 GitHub 仓库 main 分支的
源码提交。仅打开软件或设置页不会联网检测；当前构建和上游提交分开记录，
本地 MusicUnlock 桌面修改不会被误认成上游新版本。

发现更新后点击“更新软件”。软件会创建独立源码目录，保留本地桌面和更新
功能，合并上游源码并重新应用 MusicUnlock 名称，运行测试、构建窗口版 EXE，
再验证真实转换功能。进度和错误显示在设置中，“版本与源码详情”可查看目录。
测试或构建失败会保留当前软件；源码冲突、有未提交修改或分支发生变化时会
停止操作，请先自行保存或提交源码修改，再重新检测。

构建验证完成后，先保存转换结果并等待队列完成，再点击“重启生效”。
软件会关闭窗口、备份并替换 EXE，再打开新版本。新窗口启动失败时会恢复并
重新打开旧 EXE。源码已完成快进但后续安装失败时，设置会说明源码和当前
EXE 的状态，并保留候选供重试，不会强制重置用户源码。

日常运行不需要 Python 或 FFmpeg；源码更新需要保留原始 Git 仓库、Git、
Python 3.12 x64 和仓库内 .venv，以及 vendor/ffmpeg 中的程序和许可证。
本机已具备这些构建条件。移动 EXE 后，可在设置的“版本与源码详情”中重新选择
原始源码目录。更新需要联网获取 GitHub 源码和构建依赖，首次构建会较慢；
依赖只安装到候选目录内的虚拟环境。失败记录及构建缓存保留以便排查，位于
源码 .musicunlock-updates 和 EXE 旁 MusicUnlock-data 中，可在不更新时
按需备份处理。请保留许可证及第三方署名，真实仓库链接不更改。

从源码构建需要 Python 3.12 x64、Git 和 Windows FFmpeg。依赖只安装在仓库
.venv；构建先运行测试，再生成窗口版 dist\MusicUnlock.exe：

```powershell
.\build_windows.ps1 -FFmpegPath '你的FFmpeg目录\bin\ffmpeg.exe' -FFmpegLicensePath '你的FFmpeg目录\LICENSE'
.\.venv\Scripts\python.exe scripts\smoke_windows_exe.py dist\MusicUnlock.exe
.\.venv\Scripts\python.exe scripts\check_desktop_exe.py dist\MusicUnlock.exe
```

--port 可指定本机服务端口；不指定则自动选择空闲端口。保留的
--no-browser 是高级 API 兼容模式，只启动服务，普通使用不需要这个参数。
