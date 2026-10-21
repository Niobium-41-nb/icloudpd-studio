# iCloudPD Studio

[![CI](https://github.com/Niobium-41-nb/icloudpd-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/Niobium-41-nb/icloudpd-studio/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> A local, dependency-light desktop GUI for [icloudpd](https://github.com/icloud-photos-downloader/icloud_photos_downloader): native WebView2 window, two-factor login, live logs, local gallery, and any-drive folder picker.

一个为 [icloudpd](https://github.com/icloud-photos-downloader/icloud_photos_downloader) 打造的本机桌面应用。把 iCloud 照片与视频以原始画质增量备份到本地，同时提供实时任务日志、双重认证输入、图库浏览与磁盘统计。

> 非官方项目，与 Apple 及 icloudpd 上游项目无隶属关系。iCloud 是 Apple Inc. 的商标。

桌面版使用原生 WebView2 窗口；也可以继续用浏览器模式运行。界面只在本机运行，不经过任何第三方服务器。

---

## 功能

- **同步配置**：账户、iCloud 区域、全部/最近 N 个/日期范围/指定相册、照片/视频/Live Photo、目录结构、尺寸、XMP、EXIF、去重策略等。
- **实时任务**：后台启动 icloudpd，实时显示 stdout、下载进度、已下载/跳过/错误计数与耗时。
- **双重认证**：自动识别 icloudpd 的验证码/密码提示；如提示未被识别，也可以点「输入」手动提交。
- **本地图库**：扫描下载目录，生成缩略图，支持搜索、照片/视频筛选、排序和灯箱预览。HEIC 由 Pillow + pillow-heif 读取；视频首帧由 ffmpeg 生成。
- **任意磁盘目录**：文件夹选择器会列出 C:/D:/E: 等所有磁盘，可以浏览并选择非系统盘目录。
- **桌面窗口**：原生 WebView2 窗口，不依赖浏览器标签页；关闭窗口会自动停止由桌面版启动的本地服务与任务（连接到已运行的服务时除外）。
- **账户会话**：读取 `~/.pyicloud` 里已有的 cookie/session，展示会话状态；支持单独登录/重验。
- **安全确认**：下载后删除、清空「最近删除」、只保留最近 N 天等破坏性操作会二次确认。
- **深浅色主题**：暖白纸张与暗房两套主题，跟随系统或手动切换。
- **响应式**：桌面、平板与手机均可使用。

## 环境要求

- Windows 10/11（其他平台也可运行，路径与打开文件夹行为会略有不同）
- Python 3.10+
- 已安装 [`icloudpd`](https://icloud-photos-downloader.github.io/icloud_photos_downloader/install.html)，并位于 `PATH` 中
- 桌面版需要 `pywebview` 与 Microsoft Edge WebView2 Runtime（Windows 10/11 通常已自带）
- 可选：`Pillow` 用于图片缩略图，`pillow-heif` 用于 HEIC 预览，`ffmpeg` 用于视频首帧

```powershell
# 桌面窗口（已安装则可跳过）
python -m pip install -r requirements-desktop.txt

# 可选：让 HEIC 与视频缩略图可用
python -m pip install -r requirements-optional.txt
```

## 启动：桌面版（推荐）

双击 `desktop.bat`，或双击项目里的 `iCloudPD Studio.lnk` 快捷方式。可以把该快捷方式拖到桌面或开始菜单，图标已经内嵌。

也可以从终端启动：

```powershell
python desktop.py
```

桌面版会：

1. 在随机本机端口启动后台服务；
2. 使用 WebView2 打开原生窗口；
3. 如果已经有一个本地服务在运行，桌面版会直接连接它；否则关闭窗口时会优雅停止自己启动的服务与任务。

## 启动：浏览器版

双击 `run.bat`，或在当前目录执行：

```powershell
python app.py
```

默认打开：

```text
http://127.0.0.1:8765
```

常用参数：

```powershell
python app.py --port 9000        # 指定端口（被占用时会自动向后寻找）
python app.py --no-browser       # 不自动打开浏览器
python app.py --host 127.0.0.1   # 默认，仅本机可访问
```

## 使用流程

1. 打开「设置」或「同步下载」，确认默认下载目录。点「浏览」后可在文件夹窗口顶部选择 C:/D:/E: 等任意磁盘，再逐级进入目标文件夹。
2. 在「同步下载」填写 Apple ID。若本机已有有效会话，可以留空密码直接开始；否则点「登录 / 重验」。
3. 第一次登录时，icloudpd 可能在任务面板里请求密码或双重认证验证码。提交后会保存 session，后续同步通常不再需要密码。
4. 选择范围与保存位置，建议先勾选「仅预览待下载清单」确认范围。
5. 正式同步后，在「任务记录」查看实时日志；在「照片图库」浏览已经落地的文件。

## 数据与隐私

- 服务默认只监听 `127.0.0.1`，不会向局域网暴露。
- 配置保存在 `data/config.json`；任务日志保存在 `data/jobs/`；缩略图缓存在 `data/thumbs/`。
- **密码不会写入配置或日志**：密码只在启动 icloudpd 时保留在内存中，并作为 `--password` 参数传给子进程。本机其他有权限的进程在任务运行期间可能看到该进程参数。若介意，请先通过「登录 / 重验」保存会话，之后留空密码运行。
- iCloud cookie/session 由 icloudpd 保存在 `~/.pyicloud`（可在设置中修改）。这些文件等同于账户凭据，请勿分享。
- 没有账号系统、遥测或云端中转。

## 目录结构

```text
icloudpd-web/
├─ desktop.py             # 桌面窗口启动器（WebView2 / pywebview）
├─ desktop.bat            # 无控制台启动桌面版
├─ iCloudPD Studio.lnk    # Windows 快捷方式（带应用图标）
├─ app.py                 # 本地 HTTP 服务、任务管理、图库/缩略图 API
├─ static/
│  ├─ index.html          # 单页 UI
│  ├─ styles.css          # 视觉系统与响应式
│  ├─ app.js              # 路由、表单、轮询、图库灯箱
│  ├─ app.ico             # 桌面/任务栏图标
│  └─ favicon.svg
├─ data/                  # 运行时生成（配置、任务、缩略图、桌面日志）
├─ run.bat                # 浏览器版启动
└─ README.md
```

## 高级配置

### 使用自定义 icloudpd

```powershell
$env:ICLOUDPD_BIN = "C:\Tools\icloudpd.exe"
python app.py
```

### 引擎检测

页面加载时会执行 `icloudpd --version`。若侧边栏显示「未检测到 icloudpd」，请检查 `PATH` 或使用上面的 `ICLOUDPD_BIN`。

### 关于缩略图

- JPEG/PNG/WebP/AVIF 等：浏览器直接显示；服务端用 Pillow 生成压缩缩略图。
- HEIC/HEIF：需要 `pillow-heif`。
- 视频：需要 `ffmpeg`；若生成失败会显示格式占位图，不影响原文件。
- 缩略图只在本机生成，不会上传。

### 关于 icloudpd

- 本项目只是 icloudpd 的图形前端；下载、去重、MFA、删除等行为均由 icloudpd 完成。
- ADP（高级数据保护）账户无法通过 icloudpd 登录。
- 中国大陆账户可将区域切换为「中国大陆（.cn）」。
- 当前测试版本：icloudpd `1.32.3`。

## 已知限制

- 任务在服务重启后会标记为「已中断」，不会自动恢复；重新运行同一配置即可增量续传。
- 任务日志最多保留 2500 行（内存）与 700 行（磁盘）。
- 同时只允许一个本 UI 启动的 icloudpd 任务，避免多个进程争用同一个会话。如果终端里已经有 icloudpd 在运行，建议先让它结束，避免同一个账户被两个进程同时访问。
- 不提供定时任务；可以使用系统任务计划程序定时调用 icloudpd，或后续扩展 `--watch-with-interval`。

## 常见问题

**页面提示「引擎缺失」？**
先运行 `icloudpd --version`。如果命令不存在，安装 icloudpd 或设置 `ICLOUDPD_BIN`。

**任务卡在「等待双重认证」？**
在任务面板输入验证码；如果 icloudpd 的提示没有被自动识别，点右上角「输入」手动提交密码或验证码。

**图库不显示 HEIC？**
安装 `pillow-heif` 后重启服务。没有它时，HEIC 会显示为格式占位图。

**图库没有视频封面？**
安装 `ffmpeg` 并确保在 `PATH` 中；否则显示视频占位图。

**下载很慢或中断？**
icloudpd 支持断点续传与按名称+大小跳过已下载文件。重新运行同一配置即可继续。

## License

MIT. See [LICENSE](LICENSE).

---

Made for local archives. 照片留在你的磁盘上。
