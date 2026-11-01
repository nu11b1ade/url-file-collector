# 网址文件收集器

递归检查文件的**文本内容**，将包含 URL、域名或 IPv4 地址的文件复制到输出文件夹。完全离线运行，保留原有相对路径。

## Windows：双击运行，无需安装

1. 下载构建产物 `CollectUrlFiles-windows-x64`，解压后得到 `CollectUrlFiles.exe`。
2. **双击 `CollectUrlFiles.exe`** 即可打开中文图形界面，无需安装程序或 Python，也无需额外依赖文件。
3. 点击“浏览…”选择“源文件夹”和“输出文件夹”；输出文件夹也可以直接输入新路径。
4. 可勾选“仅预览(不复制)”检查命中结果；预览不会创建文件或文件夹。
5. 点击“开始”。界面显示活动进度条、扫描 / 命中 / 复制 / 跳过计数，以及可横向、纵向滚动的命中相对路径列表。

扫描在后台线程执行，界面可继续滚动和响应操作；运行期间“开始”和路径选项暂时禁用，结束后恢复。

## 从 Python 源码运行

需要 Python 3.8+ 和 tkinter，运行时仅使用标准库。官方 Windows Python 通常自带 tkinter；Linux 的 tkinter 由发行版提供（例如 Debian / Ubuntu 的 `python3-tk`），图形界面需要桌面显示环境。

将三个 Python 文件放在同一文件夹，在该文件夹执行：

```sh
python collect_url_files_gui.py
```

- `collect_url_files_gui.py`：中文 tkinter 图形界面。
- `collect_url_files_core.py`：CLI / GUI 共用的扫描、匹配、命名和复制逻辑，无界面依赖。
- `collect_url_files.py`：保留的轻量命令行入口。

### 命令行用法

Windows：

```powershell
py collect_url_files.py -s "C:\input" -o "C:\collected"
```

Linux：

```sh
python3 collect_url_files.py -s ./input -o ./collected
```

可选参数：`--dry-run` 仅预览，`--verbose` 输出逐文件详情，`--max-bytes 5000000` 设置大小上限（默认值如示例）。命中路径列表始终输出。退出码：`0` 成功，`2` 源目录或命令行参数无效，`1` 扫描 / 复制出错。

## 匹配与复制规则

GUI 和 CLI 使用同一套规则：

- 识别 HTTP(S)、FTP、file URL；带 2–63 位英文字母顶级域的域名；有效 IPv4 地址（可带端口）。仅检查文本内容，不按文件名匹配，不查询 DNS。
- 默认跳过超过 **5,000,000 字节**的文件、常见二进制扩展名，以及前 **8192 字节**中含 NUL 的文件。
- 解码依次尝试 UTF-8-SIG、UTF-8、GBK、GB18030、Latin-1；复制时保持原始字节不变。
- 不跟随符号链接，跳过非普通文件。输出文件夹即使位于源文件夹内，也不会被重新扫描。
- 输出路径的子目录若是符号链接或 Windows 目录联接，会停止并报错，避免把文件写到其他位置。
- 保留相对路径；同名但内容不同时生成 `name__1.ext`、`name__2.ext` 等，不覆盖已有文件。
- 目标文件大小和 SHA256 均相同则跳过复制，仍计入命中数。预览也会预留计划中的目标名称，保持碰撞决策与实际复制一致。
- 复制写入或关闭文件失败时，会尝试删除本次新建的不完整文件，保留已存在的文件。

计数含义：**扫描**是访问的文件数；**命中**包括重复文件；**复制**只计算实际写入，预览时为零；**跳过**包括二进制、超限、非普通 / 符号链接文件和重复命中，不包括普通的未命中文本。遇到扫描或复制错误会停止并显示错误。

## 构建 Windows 单文件 EXE

构建工作流：`.github/workflows/build-windows-exe.yml`。将本目录内容作为 GitHub 仓库根目录推送后，可在 **Actions → Build Windows EXE → Run workflow** 手动构建；相关 Python 文件、夹具、构建依赖、README 或工作流变更的 push 也会触发构建。

工作流使用 `windows-latest` 和 Python 3.12，先执行夹具检查，再运行：

```sh
python -m pip install -r requirements-build.txt
pyinstaller --noconfirm --clean --onefile --windowed --name CollectUrlFiles collect_url_files_gui.py
```

`requirements-build.txt` 仅包含 CI 打包所需的 `pyinstaller`，运行程序不需要安装它。可执行文件位于 **`dist/CollectUrlFiles.exe`**，工作流会上传为 artifact **`CollectUrlFiles-windows-x64`**。也可在 Windows 源码目录执行上述命令本地构建。

## Linux 冒烟检查

没有 tkinter 或桌面显示环境时，仍可编译检查所有脚本并独立验证核心 / CLI：

```sh
python3 -m py_compile collect_url_files.py collect_url_files_core.py collect_url_files_gui.py
python3 _fixture/verify.py
python3 _fixture/verify_core.py
python3 collect_url_files.py -s _fixture/src -o _fixture/out --dry-run
```

夹具检查使用 `_fixture` 下的临时目录，覆盖 URL / 域名 / IPv4、编码、二进制 / NUL / 大小过滤、相对路径、名称碰撞、SHA256 去重、预览、嵌套输出目录、符号链接、进度事件、Unicode 日志和退出码。
