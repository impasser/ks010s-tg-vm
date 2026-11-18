import os
from dotenv import load_dotenv

# 載入 .env 檔案 (本地除錯時方便，容器中直接透過環境變數傳入)
load_dotenv()


def _env(key: str, default: str = "") -> str:
    """讀取環境變數並自動去除首尾空白與多餘引號 (避免 Portainer Stack 貼上引號導致錯誤)"""
    return os.getenv(key, default).strip().strip('"').strip("'")


# Telegram 設定
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")

# Azure 訂用帳戶 ID (Subscription ID)
AZURE_SUBSCRIPTION_ID = _env("AZURE_SUBSCRIPTION_ID", "")

# 白名單：允許可操作的 Telegram User ID (數字列表，以逗號分隔，例如 "12345678,87654321")
ALLOWED_USERS_RAW = _env("ALLOWED_USERS", "")
ALLOWED_USERS = set(
    int(uid.strip()) for uid in ALLOWED_USERS_RAW.split(",") if uid.strip().isdigit()
)

# 超級管理員 ID (擁有全域查看所有人的 VM、延長使用時間、強制銷毀的最高權限)
ADMIN_USERS_RAW = _env("ADMIN_USERS", "")
ADMIN_USERS = set(
    int(uid.strip()) for uid in ADMIN_USERS_RAW.split(",") if uid.strip().isdigit()
)

# 允許可操作的群組 Chat ID (留空表示所有群組或私聊均可，只要發起人是白名單用戶即可)
ALLOWED_GROUPS_RAW = _env("ALLOWED_GROUPS", "")
ALLOWED_GROUPS = set(
    int(gid.strip()) for gid in ALLOWED_GROUPS_RAW.split(",") if gid.strip().lstrip("-").isdigit()
)

# ==========================================
# Azure 虛擬主機設定 (區域 / 規格 / 磁碟 / 映像)
# ==========================================
# 1. 部署區域 (Region)
# 預設: "taiwannorth" (台灣北部)
AZURE_LOCATION = _env("AZURE_LOCATION", "taiwannorth")

# 可供建立時選擇的區域清單 (顯示名稱, Azure Region 代碼) - 亞洲常用區域優先排序
AVAILABLE_REGIONS = [
    ("台灣北部(台北)", "taiwannorth"),
    ("東亞(香港)", "eastasia"),
    ("日本東部(東京)", "japaneast"),
    ("東南亞(新加坡)", "southeastasia"),
    ("韓國中部(首爾)", "koreacentral"),
    ("馬來西亞西部(吉隆坡)", "malaysiawest"),
    ("印尼中部(雅加達)", "indonesiacentral"),
    ("印度中部(浦那)", "centralindia"),
    ("印度中南部(海得拉巴)", "indiasouthcentral"),
    ("澳洲東部(雪梨)", "australiaeast"),
    ("紐西蘭北部(奧克蘭)", "newzealandnorth"),
    ("美國西部2(華盛頓州)", "westus2"),
    ("加拿大中部(多倫多)", "canadacentral"),
    ("墨西哥中部(克雷塔羅)", "mexicocentral"),
    ("巴西南部(聖保羅)", "brazilsouth"),
    ("智利中部(聖地亞哥)", "chilecentral"),
    ("英國南部(倫敦)", "uksouth"),
    ("法國中部(巴黎)", "francecentral"),
    ("德國中西部(法蘭克福)", "germanywestcentral"),
    ("北歐(愛爾蘭都柏林)", "northeurope"),
    ("瑞士北部(蘇黎世)", "switzerlandnorth"),
    ("瑞典中部(耶夫勒)", "swedencentral"),
    ("義大利北部(米蘭)", "italynorth"),
    ("西班牙中部(馬德里)", "spaincentral"),
    ("奧地利東部(維也納)", "austriaeast"),
    ("比利時中部(布魯塞爾)", "belgiumcentral"),
    ("丹麥東部(哥本哈根)", "denmarkeast"),
    ("挪威東部(奧斯陸)", "norwayeast"),
    ("波蘭中部(華沙)", "polandcentral"),
    ("阿聯酋北部(杜拜)", "uaenorth"),
    ("卡達中部(杜哈)", "qatarcentral"),
    ("以色列中部(特拉維夫)", "israelcentral"),
    ("南非北部(約翰尼斯堡)", "southafricanorth"),
]

# 可供建立時選擇的預裝軟體清單 (透過 Azure CustomScriptExtension 在背景靜默安裝)
AVAILABLE_SOFTWARE = {
    "chrome": {
        "name": "🌐 Google Chrome",
        "ps_script": (
            "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
            "$msi = \"$env:TEMP\\chrome_enterprise.msi\"; "
            "Invoke-WebRequest -Uri 'https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi' -OutFile $msi -UseBasicParsing; "
            "Start-Process msiexec.exe -ArgumentList \"/i `\"$msi`\" /qn /norestart\" -Wait -NoNewWindow; "
            "Remove-Item $msi -Force -ErrorAction SilentlyContinue"
        ),
    },
    "rustdesk": {
        "name": "🖥 RustDesk 遠端桌面",
        "ps_script": (
            "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
            "$rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/rustdesk/rustdesk/releases/latest' -Headers @{'User-Agent'='PowerShell'}; "
            "$asset = $rel.assets | Where-Object { $_.name -match 'x86_64\\.msi$' } | Select-Object -First 1; "
            "$msi = \"$env:TEMP\\rustdesk_setup.msi\"; "
            "Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $msi -UseBasicParsing; "
            "Start-Process msiexec.exe -ArgumentList \"/i `\"$msi`\" /qn /norestart\" -Wait -NoNewWindow; "
            "$rdExe = 'C:\\Program Files\\RustDesk\\rustdesk.exe'; "
            "for ($i = 0; $i -lt 15; $i++) { if (Test-Path $rdExe) { break }; Start-Sleep -Seconds 2 }; "
            "$svc = Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue; "
            "if (-not $svc) { Start-Process $rdExe -ArgumentList '--install-service' -Wait -NoNewWindow }; "
            "$svc = Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue; "
            "if ($svc -and $svc.Status -ne 'Running') { Start-Service -Name 'RustDesk' -ErrorAction SilentlyContinue }; "
            "Start-Sleep -Seconds 3; "
            "& $rdExe --password '__RUSTDESK_PASSWORD__' 2>&1 | Out-Null; "
            "$rdId = ''; "
            "for ($i = 0; $i -lt 15; $i++) { "
            "  $out = (& $rdExe --get-id 2>$null | Out-String).Trim(); "
            "  if ($out -match '^[0-9]{6,}$') { $rdId = $out; break }; "
            "  Start-Sleep -Seconds 2 "
            "}; "
            "Write-Output \"RUSTDESK_ID=$rdId\"; "
            "Remove-Item $msi -Force -ErrorAction SilentlyContinue"
        ),
    },
}

# 2. 虛擬機規格 (VM Size)
# Windows 11 建議至少 2 核心 4GB~8GB RAM:
# - "Standard_B2s" (2 vCPU, 4GB RAM) - 便宜經濟型
# - "Standard_B2ms" (2 vCPU, 8GB RAM) - 記憶體較充裕
# - "Standard_D2s_v5" (2 vCPU, 8GB RAM) - 運算效能型 (不降頻)
# - "Standard_D4s_v5" (4 vCPU, 16GB RAM) - 高效能型
DEFAULT_VM_SIZE = _env("DEFAULT_VM_SIZE", "Standard_B2s")

# 3. 作業系統硬碟等級 (OS Disk Type)
# - "Standard_LRS" (普通 HDD - 最便宜)
# - "StandardSSD_LRS" (標準 SSD - 性價比最高，推薦)
# - "Premium_LRS" (高級 SSD - 讀寫效能最好，Win11 操作最順暢)
OS_DISK_TYPE = _env("OS_DISK_TYPE", "StandardSSD_LRS")

# 4. 作業系統映像檔 (預設改用 Windows Server 2022，相容性最佳、開機最快速且無授權限制)
IMAGE_PUBLISHER = _env("IMAGE_PUBLISHER", "MicrosoftWindowsServer")
IMAGE_OFFER = _env("IMAGE_OFFER", "WindowsServer")
IMAGE_SKU = _env("IMAGE_SKU", "2022-datacenter-azure-edition")  # 可選: 2022-datacenter-azure-edition, 2025-datacenter-azure-edition
IMAGE_VERSION = _env("IMAGE_VERSION", "latest")

# 5. Linux 虛擬機規格與映像檔 (預設 Debian 13 "Trixie" - x64 Gen2，可透過環境變數覆寫)
LINUX_VM_SIZE = _env("LINUX_VM_SIZE", "Standard_B2s")
LINUX_OS_DISK_TYPE = _env("LINUX_OS_DISK_TYPE", "StandardSSD_LRS")
LINUX_IMAGE_PUBLISHER = _env("LINUX_IMAGE_PUBLISHER", "Debian")
LINUX_IMAGE_OFFER = _env("LINUX_IMAGE_OFFER", "debian-13")
LINUX_IMAGE_SKU = _env("LINUX_IMAGE_SKU", "13-gen2")
LINUX_IMAGE_VERSION = _env("LINUX_IMAGE_VERSION", "latest")

# ==========================================
# 生命週期與配額設定
# ==========================================
# 自動銷毀倒數時間 (預設已改成 2 小時)
DEFAULT_LIFETIME_HOURS = int(_env("DEFAULT_LIFETIME_HOURS", "2"))

# 每日自動關機時間 (格式為 24 小時制的 HHMM，例如 1900 代表 19:00)
AUTO_SHUTDOWN_TIME = _env("AUTO_SHUTDOWN_TIME", "1900")
AUTO_SHUTDOWN_TIMEZONE = _env("AUTO_SHUTDOWN_TIMEZONE", "Taipei Standard Time")

# 配額限制 (每人最多 2 台，全局總開機數上限也是 2 台)
MAX_VMS_PER_USER = int(_env("MAX_VMS_PER_USER", "2"))
MAX_TOTAL_VMS = int(_env("MAX_TOTAL_VMS", "2"))
