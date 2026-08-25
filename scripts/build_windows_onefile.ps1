# 一键打包：Windows 单文件(onefile) 免 Python 运行 exe
# 用法：powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_onefile.ps1 [-Python C:\Python312\python.exe]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Spec         = Join-Path $ProjectRoot "packaging\windows_onefile.spec"
$Dist         = Join-Path $ProjectRoot "build\windows-onefile\dist"
$Work         = Join-Path $ProjectRoot "build\windows-onefile\work"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$VenvDir      = Join-Path $ProjectRoot ".venv-build"
$VenvPython   = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Spec))         { throw "找不到打包配置：$Spec" }
if (-not (Test-Path -LiteralPath $Requirements)) { throw "找不到依赖清单：$Requirements" }

# ---- 1) 定位基础 Python ----
$baseCmd  = $null
$baseArgs = @()
if ($Python) {
    if (Test-Path -LiteralPath $Python) {
        $baseCmd  = $Python
        $baseArgs = @()
    } else {
        throw "指定的 Python 不存在：$Python"
    }
} else {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $baseCmd  = "py"
        $baseArgs = @("-3")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $baseCmd  = "python"
        $baseArgs = @()
    } else {
        throw "未找到 Python。请安装 64 位 Python 3.10+（推荐 3.11/3.12）并勾选 Add to PATH，或用 -Python 指定解释器路径。"
    }
}

# ---- 2) 创建专用构建虚拟环境（不污染系统 Python）----
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "[1/4] 创建构建环境 .venv-build ..."
    & $baseCmd @baseArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }
}

$pyVer = (& $VenvPython -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ([version]$pyVer -lt [version]"3.10") {
    throw "构建需要 Python 3.10+，当前为 $pyVer。请安装 64 位 Python 3.11/3.12 后用 -Python 指定。"
}

# ---- 3) 安装依赖与 PyInstaller ----
Write-Host "[2/4] 安装依赖与 PyInstaller（首次较慢，需联网）..."
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "升级 pip 失败" }
& $VenvPython -m pip install -r $Requirements pyinstaller
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败" }

# ---- 4) 打包 ----
Write-Host "[3/4] 开始打包（单文件 onefile）..."
# 在项目根目录收集动态导入的 app 模块。
Set-Location -LiteralPath $ProjectRoot
& $VenvPython -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Work $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$Exe = Join-Path $Dist "ServiceManager.exe"
if (-not (Test-Path -LiteralPath $Exe)) { throw "未生成目标 exe：$Exe" }

Write-Host ""
Write-Host "[4/4] 打包完成"
Write-Host "单文件 exe：$Exe"
Write-Host ("大小：{0:N1} MB" -f ((Get-Item -LiteralPath $Exe).Length / 1MB))
Write-Host ""
Write-Host "该 exe 已内嵌源码与静态资源，不含 .db/.env/uploads/backups 等运营数据。"
Write-Host "将 ServiceManager.exe 单独放到任意目录双击即可运行；数据默认保存在 %LOCALAPPDATA%\ServiceManager-ERP。"
