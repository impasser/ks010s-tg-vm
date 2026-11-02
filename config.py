import os
from dotenv import load_dotenv

# 載入 .env 檔案 (本地除錯時方便，容器中直接透過環境變數傳入)
load_dotenv()

# Telegram 設定
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Azure 訂用帳戶 ID (Subscription ID)
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")

# 白名單：允許可操作的 Telegram User ID (數字列表，以逗號分隔，例如 "12345678,87654321")
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = set(
    int(uid.strip()) for uid in ALLOWED_USERS_RAW.split(",") if uid.strip().isdigit()
)

# 超級管理員 ID (擁有全域查看所有人的 VM、延長使用時間、強制銷毀的最高權限)
ADMIN_USERS_RAW = os.getenv("ADMIN_USERS", "")
ADMIN_USERS = set(
    int(uid.strip()) for uid in ADMIN_USERS_RAW.split(",") if uid.strip().isdigit()
)

# 允許可操作的群組 Chat ID (留空表示所有群組或私聊均可，只要發起人是白名單用戶即可)
ALLOWED_GROUPS_RAW = os.getenv("ALLOWED_GROUPS", "")
ALLOWED_GROUPS = set(
    int(gid.strip()) for gid in ALLOWED_GROUPS_RAW.split(",") if gid.strip().lstrip("-").isdigit()
)

# ==========================================
# Azure 虛擬主機設定 (區域 / 規格 / 磁碟 / 映像)
# ==========================================
# 1. 部署區域 (Region)
# 例如: "eastasia" (東亞/香港), "japaneast" (日本東部), "southeastasia" (東南亞/新加坡)
AZURE_LOCATION = os.getenv("AZURE_LOCATION", "eastasia")

# 2. 虛擬機規格 (VM Size)
# Windows 11 建議至少 2 核心 4GB~8GB RAM:
# - "Standard_B2s" (2 vCPU, 4GB RAM) - 便宜經濟型
# - "Standard_B2ms" (2 vCPU, 8GB RAM) - 記憶體較充裕
# - "Standard_D2s_v5" (2 vCPU, 8GB RAM) - 運算效能型 (不降頻)
# - "Standard_D4s_v5" (4 vCPU, 16GB RAM) - 高效能型
DEFAULT_VM_SIZE = os.getenv("DEFAULT_VM_SIZE", "Standard_B2s")

# 3. 作業系統硬碟等級 (OS Disk Type)
# - "Standard_LRS" (普通 HDD - 最便宜)
# - "StandardSSD_LRS" (標準 SSD - 性價比最高，推薦)
# - "Premium_LRS" (高級 SSD - 讀寫效能最好，Win11 操作最順暢)
OS_DISK_TYPE = os.getenv("OS_DISK_TYPE", "StandardSSD_LRS")

# 4. 作業系統映像檔 (預設改用 Windows Server 2022，相容性最佳、開機最快速且無授權限制)
IMAGE_PUBLISHER = os.getenv("IMAGE_PUBLISHER", "MicrosoftWindowsServer")
IMAGE_OFFER = os.getenv("IMAGE_OFFER", "WindowsServer")
IMAGE_SKU = os.getenv("IMAGE_SKU", "2022-datacenter-azure-edition")  # 可選: 2022-datacenter-azure-edition, 2025-datacenter-azure-edition
IMAGE_VERSION = os.getenv("IMAGE_VERSION", "latest")

# ==========================================
# 生命週期與配額設定
# ==========================================
# 自動銷毀倒數時間 (預設已改成 2 小時)
DEFAULT_LIFETIME_HOURS = int(os.getenv("DEFAULT_LIFETIME_HOURS", "2"))

# 每日自動關機時間 (格式為 24 小時制的 HHMM，例如 1900 代表 19:00)
AUTO_SHUTDOWN_TIME = os.getenv("AUTO_SHUTDOWN_TIME", "1900")
AUTO_SHUTDOWN_TIMEZONE = os.getenv("AUTO_SHUTDOWN_TIMEZONE", "Taipei Standard Time")

# 配額限制 (每人最多 2 台，全局總開機數上限也是 2 台)
MAX_VMS_PER_USER = int(os.getenv("MAX_VMS_PER_USER", "2"))
MAX_TOTAL_VMS = int(os.getenv("MAX_TOTAL_VMS", "2"))
