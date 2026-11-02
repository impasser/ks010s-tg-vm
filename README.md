# Azure 臨時 Windows 測試機 Telegram 管理機器人

專為團隊臨時測試需求設計的自動化管理機器人（預設採用 Windows Server 2022，開機只需 1~2 分鐘且無授權限制）。提供隨機高強度密碼、晚上 19:00 強制關機，以及 **2 小時後自動徹底銷毀資源群組**（每人最多 2 台，全局上限 2 台）。

---

## 📋 目錄
1. [核心架構與互動規則](#-核心架構與互動規則)
2. [事前準備：Telegram 機器人與群組完整設定](#-事前準備telegram-機器人與群組完整設定)
3. [方案一：部署到 Azure Container Apps (最推薦 - 免密碼受控識別)](#-方案一部署到-azure-container-apps-最推薦---免密碼受控識別)
4. [方案二：部署到一般容器環境 (Docker / NAS / VPS / 本地)](#-方案二部署到一般容器環境-docker--nas--vps--本地)
5. [方案三：推送到 GitHub 私有庫 + Portainer CE Web GUI 部署 (圖形化推薦)](#-方案三推送到-github-私有庫--portainer-ce-web-gui-部署-圖形化推薦)
6. [自訂參數速查 (區域 / 規格 / 硬碟 / 映像)](#-自訂參數速查-區域--規格--硬碟--映像)
7. [常見問題與排錯指南](#-常見問題與排錯指南)

---

## 🌟 核心架構與互動規則

- **建立 VM 限群組（透明公開）**：
  為了讓大家清楚 2 台總額度的佔用情況，`/create_vm` **強制只能在工作群組發起**。若同事私聊發送指令，機器人會提示：
  > `⛔ 此指令只在群組模式有效。請至工作群組內發送 /create_vm 指令，建立完成後我會在這裡把密碼私發給您。`
- **敏感帳密私信交付（安全保密）**：
  VM 建立完成後，群組只會顯示「已就緒」，**連線 IP、帳號與隨機密碼一律透過「私信」單獨發給發起者**，群組絕不洩漏登入憑證。
- **單一映像，雙模認證自適應**：
  - 跑在 Azure：不填任何 Key，自動走 **受控識別 (Managed Identity)**，零密碼。
  - 跑在其他地方：填入 3 組 Service Principal Key，自動切換至 API 憑證認證。
- **乾淨銷毀與防呆關機**：
  - 每次建立獨立資源群組 (`rg-tempvm-{userid}-{id}`)，2 小時後直接刪除整個資源群組，VM、硬碟、IP 徹底釋放，絕無殘留扣費。
  - 內建每天晚上 19:00 強制自動關機。

---

## 🛠 事前準備：Telegram 機器人與群組完整設定

回家後請依照以下步驟完成 Telegram 端設定：

### 1. 建立機器人並獲取 Token
1. 在 Telegram 搜尋 `@BotFather` 並開啟對話。
2. 發送 `/newbot`，依序輸入機器人顯示名稱與 username（結尾必須是 `bot`，例如 `MyAzureVMBot`）。
3. 建立成功後，複製畫面上的 **HTTP API Token**（格式如 `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`）。

### 2. 關閉群組隱私模式 (極度重要 ⚠️)
Telegram 預設會阻擋群組訊息，**必須關閉隱私模式機器人才能在群裡接收指令**：
1. 在與 `@BotFather` 的對話中發送指令：`/setprivacy`
2. 點選您的機器人。
3. 點選 **`Disable`**（看到回傳 `Success! Privacy mode disabled` 即完成）。

### 3. 設定快捷指令選單 (選填，體驗更加分 ✨)
向 `@BotFather` 發送 `/setcommands`，選擇您的機器人，並直接貼上以下文字，這樣同事只要在對話框打 `/` 就會自動彈出選單：
```text
create_vm - 建立臨時 Windows (限群組)
my_vm - 查詢我的 VM 狀態與剩餘時間
delete_vm - 提前手動釋放 VM (支援按鈕選擇)
help - 顯示指令說明與規則
```

### 4. 取得同事與自己的 Telegram User ID (用於白名單)
1. 讓自己與同事在 Telegram 搜尋 `@userinfobot` 並點擊 Start。
2. 畫面會回傳各自的數字 `Id`（例如 `123456789`）。
3. 多個 ID 以逗號分隔，填入配置中的 `ALLOWED_USERS`。

### 4. 建立工作群組並取得 Group Chat ID (用於群組鎖定)
1. 在 Telegram 建立一個新的工作私人群組，把**同事**和**機器人**加進群組。
2. 在群組裡隨便發送一則訊息（例如輸入 `/start`）。
3. 打開瀏覽器訪問以下網址（將 `<你的TOKEN>` 替換為步驟 1 的 Bot Token）：
   ```text
   https://api.telegram.org/bot<你的TOKEN>/getUpdates
   ```
4. 在網頁文字中找到 `"chat":{"id": -100xxxxxxxxxx`，這個以 `-100` 開頭的負數就是群組 ID。
5. 將此 ID 填入配置中的 `ALLOWED_GROUPS`（機器人將只會響應此群組，防止被加到其他群盜用）。

### 5. 提醒同事按一次「開始」 (關鍵 ⚠️)
> **Telegram 防騷擾機制**：如果使用者從未私聊過該機器人，機器人是無法主動私發訊息給他的！
> 
> **操作**：請同事加入群組後，**先點擊機器人名稱私聊發送 `/start` 一次**。之後他在群裡建立 VM，機器人就能順利把帳號密碼私發給他。

---

## 🚀 方案一：部署到 Azure Container Apps (最推薦 - 免密碼受控識別)

託管在 Azure 容器上，每月成本約 0~1 美金，且**容器內不需要儲存任何 Azure 帳號密碼**。
可在本機終端機（安裝 [Azure CLI](https://learn.microsoft.com/zh-tw/cli/azure/install-azure-cli)）或直接使用瀏覽器打開 [Azure Cloud Shell](https://shell.azure.com/) 執行：

```bash
# 1. 登入 Azure
az login

# 2. 建立放置 Bot 的資源群組
az group create --name rg-tg-bot --location eastasia

# 3. 建立 ACR 容器註冊表，並直接在雲端打包程式碼 (電腦免裝 Docker)
ACR_NAME="acrbot$RANDOM"
az acr create --resource-group rg-tg-bot --name $ACR_NAME --sku Basic --admin-enabled true

# 在專案目錄下執行雲端建置：
az acr build --registry $ACR_NAME --image tg-azure-vm-bot:latest .

# 4. 建立 Container Apps 執行環境
az containerapp env create \
  --name bot-env \
  --resource-group rg-tg-bot \
  --location eastasia

# 5. 建立並運行 Container App (開啟 --system-assigned 系統受控識別)
az containerapp create \
  --name azure-vm-bot \
  --resource-group rg-tg-bot \
  --environment bot-env \
  --image $ACR_NAME.azurecr.io/tg-azure-vm-bot:latest \
  --registry-server $ACR_NAME.azurecr.io \
  --system-assigned \
  --cpu 0.25 --memory 0.5Gi \
  --env-vars \
    TELEGRAM_BOT_TOKEN="你的TelegramBotToken" \
    AZURE_SUBSCRIPTION_ID="你的Azure訂用帳戶ID" \
    ALLOWED_USERS="你的TG_ID,同事的TG_ID" \
    ADMIN_USERS="你的TG_ID" \
    ALLOWED_GROUPS="-1001234567890" \
    DEFAULT_LIFETIME_HOURS="2" \
    MAX_VMS_PER_USER="2" \
    MAX_TOTAL_VMS="2"

# 6. 為容器賦予 Azure 訂閱權限 (關鍵步驟)
# 取得容器自動產生的 Principal ID
PRINCIPAL_ID=$(az containerapp identity show --name azure-vm-bot --resource-group rg-tg-bot --query principalId -o tsv)

# 授權 Contributor (參與者) 角色給此容器
az role assignment create \
  --assignee $PRINCIPAL_ID \
  --role Contributor \
  --scope /subscriptions/<你的Azure訂用帳戶ID>
```

#### 檢查運行日誌：
```bash
az containerapp logs show --name azure-vm-bot --resource-group rg-tg-bot --follow
```
看到 `啟動 Telegram Azure 臨時 VM 機器人...` 即代表部署成功！

---

## 🐳 方案二：部署到一般容器環境 (Docker / NAS / VPS / 本地)

如果打算把容器放在家裡的 NAS (Synology)、Linux 伺服器或本地 Docker 運行：

### 1. 建立 Azure 服務主體 (取得 3 組 API Key)
在 Azure CLI 執行：
```bash
az ad sp create-for-rbac \
  --name "sp-tg-vm-bot" \
  --role Contributor \
  --scopes /subscriptions/<你的Azure訂用帳戶ID>
```
終端機會輸出 JSON：
```json
{
  "appId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "displayName": "sp-tg-vm-bot",
  "password": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "tenant": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
> - `appId` = `AZURE_CLIENT_ID`
> - `password` = `AZURE_CLIENT_SECRET`
> - `tenant` = `AZURE_TENANT_ID`

### 2. 使用 Docker Compose 一鍵啟動 (推薦)
編輯專案中的 `docker-compose.yml`，填入參數後啟動：
```bash
docker compose up -d
```

### 3. 或使用標準 Docker 命令啟動
```bash
# 建置映像檔
docker build -t tg-azure-vm-bot:latest .

# 運行容器
docker run -d \
  --name tg-azure-vm-bot \
  --restart always \
  -e TELEGRAM_BOT_TOKEN="你的TelegramBotToken" \
  -e AZURE_SUBSCRIPTION_ID="你的Azure訂用帳戶ID" \
  -e AZURE_TENANT_ID="上面取得的tenant" \
  -e AZURE_CLIENT_ID="上面取得的appId" \
  -e AZURE_CLIENT_SECRET="上面取得的password" \
  -e ALLOWED_USERS="你的TG_ID,同事的TG_ID" \
  -e ALLOWED_GROUPS="-1001234567890" \
  -e DEFAULT_LIFETIME_HOURS="2" \
  -e MAX_VMS_PER_USER="2" \
  -e MAX_TOTAL_VMS="2" \
  tg-azure-vm-bot:latest
```

---

## 🐙 方案三：推送到 GitHub 私有庫 + Portainer CE Web GUI 部署 (圖形化推薦)

此方式結合 **GitHub 免費自動構建 (GHCR)** 與 **Portainer CE 網頁圖形化介面**，全程點擊滑鼠操作，無需進終端機打指令：

### 步驟 1：建立 GitHub 私有保存庫並推送代碼
在本地專案目錄執行：
```bash
git init
git add .
git commit -m "feat: initial commit for azure temp vm bot"
git branch -M main

# 換成您的 GitHub 私有倉庫網址
git remote add origin https://github.com/<你的GitHub帳號>/<你的倉庫名稱>.git
git push -u origin main
```
推送完成後，GitHub Actions 會自動在雲端打包，並發布到：  
`ghcr.io/<你的GitHub帳號>/<你的倉庫名稱>:latest`

---

### 步驟 2：在 Portainer 新增 GitHub 映像庫憑證 (只需做一次)
1. 登入 Portainer，點擊左側最下方的 **`Registries`**。
2. 點擊右上角 **`+ Add registry`**。
3. 選擇 **`Custom registry`**：
   - **Name**：輸入 `GitHub-GHCR`
   - **Registry URL**：輸入 `ghcr.io`
   - **Authentication**：開啟 (Toggle 打開)
   - **Username**：您的 GitHub 帳號
   - **Password / Token**：貼上您的 **GitHub Personal Access Token**（需勾選 `read:packages` 權限）
4. 點擊 **`Add registry`** 儲存。

---

### 步驟 3：在 Portainer 使用「Stacks」一鍵部署
1. 點擊 Portainer 左側選單的 **`Stacks`**。
2. 點擊右上角 **`+ Add stack`**。
3. **Name**：輸入 `azure-vm-bot`。
4. **Build method**：選擇預設的 **`Web editor`**。
5. 在下方文字編輯框中貼入設定（替換成您的實際資料）：

```yaml
version: '3.8'

services:
  azure-vm-bot:
    image: ghcr.io/<你的GitHub帳號>/<你的倉庫名稱>:latest
    container_name: tg-azure-vm-bot
    restart: always
    environment:
      # Telegram 設定
      - TELEGRAM_BOT_TOKEN=你的TelegramBotToken
      - ALLOWED_USERS=你的TG數字ID,同事的TG數字ID
      - ADMIN_USERS=你的TG數字ID
      - ALLOWED_GROUPS=-1001234567890
      
      # Azure 服務主體認證 (3組Key)
      - AZURE_SUBSCRIPTION_ID=你的Azure訂閱ID
      - AZURE_TENANT_ID=你的TenantID
      - AZURE_CLIENT_ID=你的ClientID
      - AZURE_CLIENT_SECRET=你的ClientSecret

      # VM 自訂設定 (選填)
      - AZURE_LOCATION=eastasia
      - DEFAULT_VM_SIZE=Standard_B2s
      - OS_DISK_TYPE=StandardSSD_LRS
      - DEFAULT_LIFETIME_HOURS=2
      - MAX_VMS_PER_USER=2
      - MAX_TOTAL_VMS=2
```

6. 滾動到最下方，點擊藍色按鈕 **`Deploy the stack`**！

---

### 步驟 4：查看日誌確認與後續更新
- **查看日誌**：點進 `azure-vm-bot` Stack -> 點擊容器 -> 點擊上方 **`Logs`** 按鈕，看見 `啟動 Telegram Azure 臨時 VM 機器人...` 即代表成功。
- **後續更新**：代碼推送到 GitHub 自動打包後，回到 Portainer 的 Stack 編輯器，勾選 **`Re-pull image and redeploy`** 並點擊 **`Update the stack`** 即可無縫升級！

---

## ⚙️ 自訂參數速查 (區域 / 規格 / 硬碟 / 映像)

可直接在 [config.py](file:///e:/04-soft/00-me/0-sync-data/azure臨時vm快速建立刪除/config.py) 中調整，或在啟動容器時透過環境變數傳入覆蓋：

| 環境變數名稱 | 預設值 | 說明與可選值 |
| :--- | :--- | :--- |
| `AZURE_LOCATION` | `eastasia` | 區域：`eastasia` (香港), `japaneast` (東京), `southeastasia` (新加坡) |
| `DEFAULT_VM_SIZE` | `Standard_B2s` | 規格：`Standard_B2s` (2核4G), `Standard_B2ms` (2核8G), `Standard_D2s_v5` (2核8G穩定型) |
| `OS_DISK_TYPE` | `StandardSSD_LRS` | 磁碟：`StandardSSD_LRS` (標準SSD，推薦), `Premium_LRS` (高級SSD，速度最快), `Standard_LRS` (普通HDD) |
| `IMAGE_SKU` | `win11-23h2-pro` | 映像：`win11-23h2-pro`, `win11-24h2-pro` |
| `DEFAULT_LIFETIME_HOURS` | `2` | 自動銷毀倒數時間（小時） |
| `AUTO_SHUTDOWN_TIME` | `1900` | 每天自動關機時間 (24小時制，1900 即 19:00) |
| `MAX_VMS_PER_USER` | `2` | 每位同事最多同時持有數 |
| `MAX_TOTAL_VMS` | `2` | 全局所有人加總最大開機數 |

---

## 📱 Telegram 常用指令速查

### 一般成員指令
| 指令 | 適用場景 | 功能說明 |
| :--- | :--- | :--- |
| `/create_vm` | **限群組** | 建立臨時 Windows，密碼將私信給發起人 |
| `/create_vm <IP>` | **限群組** | 建立 VM 並限制防火牆僅允許該 IP 連線 (最安全) |
| `/my_vm` | 群組/私聊 | 查詢自己名下的 VM 狀態、公網 IP 與剩餘倒數分鐘（**看不到別人的**） |
| `/delete_vm` | 群組/私聊 | 提前手動釋放 VM（支援按鈕選擇，或輸入 `/delete_vm 1`） |
| `/help` | 群組/私聊 | 檢視說明與規則 |

### 👑 超級管理員專屬指令 (`ADMIN_USERS`)
只有您（配置在 `ADMIN_USERS` 中的 ID）可以使用以下指令：

| 指令 | 功能說明 |
| :--- | :--- |
| `/admin` 或 `/all_vms` | **開啟全域總控面板**：<br>• 查看全團隊所有人正在運行的 VM（包含建立者、IP、剩餘分鐘數）<br>• 為每台機器提供專屬按鈕：`[ ⏱ 延長 +1小時 ]`、`[ ⏱ 延長 +2小時 ]`、`[ 🗑 強制刪除 ]` |
| `/extend <1\|2\|IP> [小時]` | 為指定機器延長使用時間（例如 `/extend 1 2` 為 1 號機延長 2 小時，系統會自動重新排程並發通知給該同事） |

---

## ❓ 常見問題與排錯指南

1. **為什麼同事在群裡打了 `/create_vm` 機器人沒反應？**
   - 檢查 `@BotFather` 是否有執行 `/setprivacy` 並設為 **`Disable`**。
   - 檢查該同事的 Telegram ID 是否有加入到 `ALLOWED_USERS` 白名單。
2. **為什麼機器人在群裡說建好，但同事沒收到私信密碼？**
   - 該同事從未點進機器人的私聊對話按過 **「開始 (Start)」**。請提醒他先私聊機器人隨便說句話即可解鎖。
3. **VM 刪除會不會留下沒刪乾淨的硬碟或 IP 繼續扣錢？**
   - 不會。程式設計為每次建立獨立資源群組 (`rg-tempvm-xxx`)，刪除時直接向 Azure 發出「刪除整個資源群組」指令，所屬硬碟、IP、網卡 100% 同步銷毀。
