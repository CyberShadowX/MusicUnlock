# MusicUnlock Windows 使用说明

1. 双击 `MusicUnlock.exe`，稍候数秒，浏览器会自动打开本机操作页面。
2. 将音乐文件或文件夹拖入页面，选择“保持原样”或目标格式，等待处理。
3. 点击单个下载或 ZIP 打包下载，将结果保存到自己选择的位置。
4. 使用期间保留启动时的控制台窗口；完成后关闭窗口，或按 `Ctrl+C` 退出。
   只关闭浏览器页面不会停止后台服务。

这是 Windows x64 单文件便携版，已包含 Python、页面资源、酷狗公钥和
FFmpeg。运行时无需另装 Python、pip 或 FFmpeg，也无需联网安装依赖。
可将 EXE 单独复制到其他文件夹使用。服务只绑定 `127.0.0.1`，自动选择
空闲端口；文件在本机处理，转换不会修改输入音乐文件。

若浏览器没有自动打开，请将控制台中 `MusicUnlock URL:` 后的地址复制到
浏览器。单文件版启动时会在系统临时目录解包运行组件，因此首次启动需要
等待几秒，并需要临时磁盘空间。网页中的临时结果位于系统临时目录内的
`music-geshizhuanhuan-web` 文件夹；重要结果请主动下载保存。

高级启动示例（在 EXE 所在目录运行）：

```powershell
.\MusicUnlock.exe --port 8686
.\MusicUnlock.exe --no-browser
```

指定端口已被占用时会提示并退出；不指定端口则自动选择可用端口。

格式支持遵循原仓库：NCM、部分 QQ 音乐格式、KGM/KGMA/VPR 和老版 KWM。
新版无内嵌密钥的 QQ 文件需要在页面提供相应 EKey；KGG（酷狗 v5）暂不支持。
打包不会扩展上游解码能力。仅处理自己有权使用的音乐文件。

构建来源、Python/依赖版本和 FFmpeg 身份记录在 `build-info.json`；
EXE 的 SHA256 在 `SHA256SUMS.txt`。许可证见 `THIRD_PARTY_NOTICES.txt`
及 `licenses` 文件夹。

从源码重新构建需要 Python 3.12 x64、Git 和 Windows FFmpeg。构建依赖安装
在仓库 `.venv`，不会修改系统 Python：

```powershell
.\build_windows.ps1 -FFmpegPath '你的FFmpeg目录\bin\ffmpeg.exe' -FFmpegLicensePath '你的FFmpeg目录\LICENSE'
.\.venv\Scripts\python.exe scripts\smoke_windows_exe.py dist\MusicUnlock.exe
```

FFmpeg 已在 PATH 且安装目录包含 `LICENSE` 时，可直接运行
`.\build_windows.ps1`。构建会先运行测试，再生成 `dist\MusicUnlock.exe`。
