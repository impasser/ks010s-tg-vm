import logging
import asyncio
from datetime import datetime, timedelta, timezone
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CopyTextButton,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import config
from azure_manager import AzureVMManager

# 設定日誌
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 初始化 Azure 管理器
azure_mgr = AzureVMManager()

# 記憶體狀態追蹤：{rg_name: {"user_id": int, "chat_id": int, "username": str, "expire_at": datetime, "ip": str}}
ACTIVE_VMS = {}


def is_user_authorized(user_id: int) -> bool:
    """檢查使用者是否在白名單內（若未配置白名單則允許所有人）"""
    if not config.ALLOWED_USERS:
        return True
    return user_id in config.ALLOWED_USERS or user_id in config.ADMIN_USERS


def is_admin(user_id: int) -> bool:
    """檢查是否為超級管理員"""
    return user_id in config.ADMIN_USERS


def is_chat_authorized(chat_id: int) -> bool:
    """檢查群組是否在白名單內（若未配置白名單則允許任何群組與私聊）"""
    if not config.ALLOWED_GROUPS:
        return True
    return chat_id in config.ALLOWED_GROUPS or chat_id > 0  # chat_id > 0 代表私聊


def build_main_menu(user_id: int, is_private: bool = False) -> InlineKeyboardMarkup:
    """建置使用者/管理員的主功能互動按鈕選單 (管理員按鈕僅限私聊顯示)"""
    keyboard = [
        [
            InlineKeyboardButton("🚀 建立 Windows VM", callback_data="btn_create_vm"),
            InlineKeyboardButton("🔍 查詢我的 VM", callback_data="btn_my_vm"),
        ],
        [
            InlineKeyboardButton("🗑 釋放/刪除 VM", callback_data="btn_delete_vm"),
        ]
    ]
    # 只有在私聊且是超級管理員時，才顯示管理員控制台按鈕，群組內完全隱藏
    if is_admin(user_id) and is_private:
        keyboard.append([
            InlineKeyboardButton("👑 管理員主控台 (延長/強制刪除/全域)", callback_data="btn_admin")
        ])
    return InlineKeyboardMarkup(keyboard)


def build_persistent_reply_keyboard(user_id: int, is_private: bool = False) -> ReplyKeyboardMarkup:
    """建置輸入框下方的常駐底部鍵盤 (永久釘選在輸入框下方)"""
    keyboard = [
        ["🚀 建立 Windows VM", "🔍 查詢我的 VM"],
        ["🗑 釋放/刪除 VM", "❓ 使用說明"],
    ]
    if is_admin(user_id) and is_private:
        keyboard.append(["👑 管理員主控台"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start 或 /help 指令"""
    user = update.effective_user
    chat = update.effective_chat
    is_private = (chat.type == "private" or chat.id > 0)

    welcome_text = (
        f"👋 你好，{user.first_name}！\n\n"
        "我是 **Azure 臨時 Windows VM 管理機器人**。\n\n"
        "💡 **您可以直接點擊下方按鈕操作，亦可使用文字指令：**\n\n"
        "🛠 **可用指令：**\n"
        "• `/create_vm` 或 `/create_vm <你的外網IP>`\n"
        "  └ 建立臨時 Windows (⚠️ **僅限群組內使用**，密碼私聊發送)\n\n"
        "• `/my_vm`\n"
        "  └ 查看自己擁有的 VM、IP 與剩餘倒數分鐘\n\n"
        "• `/delete_vm`\n"
        "  └ 提早手動釋放 VM (彈出按鈕一鍵點選，或輸入 `/delete_vm 1`、`/delete_vm 2`、`/delete_vm <IP>`)\n\n"
        "• `/help`\n"
        "  └ 顯示此說明選單\n\n"
        "📌 **管理與安全規則：**\n"
        "1. **建立限群組**：`/create_vm` 只能在工作群組發起。\n"
        "2. **密碼私發**：建立完成後，IP、帳號與隨機密碼將透過「此私聊視窗」單獨發送給您。\n"
        f"3. **自動銷毀**：建立後 **{config.DEFAULT_LIFETIME_HOURS} 小時** 將自動刪除並推播通知。\n"
        f"4. **自動關機**：每天 **{config.AUTO_SHUTDOWN_TIME[:2]}:{config.AUTO_SHUTDOWN_TIME[2:]}** 強制自動關機以節省開銷。"
    )
    # 只有在「私聊視窗」且「是超級管理員」時，才追加管理員專屬功能說明
    if is_admin(user.id) and is_private:
        welcome_text += (
            "\n\n👑 **超級管理員專屬功能：**\n"
            "• `/admin` 或 `/all_vms` - 開啟全域主控台 (查閱所有同事的 VM、一鍵延長、強制刪除)\n"
            "• `/extend <1|2|IP> [小時]` - 為指定機器延長使用時間 (例如 `/extend 1 2`)"
        )
    reply_markup = build_main_menu(user.id, is_private=is_private)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

    # 若在私聊視窗，同時啟用底部常駐鍵盤 (永久釘選在輸入框下方)
    if is_private:
        await update.message.reply_text(
            "⌨️ **底部常駐按鈕已為您啟用！**\n隨時點選下方大按鈕即可操作，無需手動輸入指令：",
            reply_markup=build_persistent_reply_keyboard(user.id, is_private=True),
            parse_mode="Markdown"
        )


async def create_vm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/create_vm [optional_ip] 指令處理器"""
    allowed_ip = context.args[0] if context.args else None
    await process_create_vm(
        message=update.message,
        user=update.effective_user,
        chat=update.effective_chat,
        context=context,
        allowed_ip=allowed_ip
    )


async def process_create_vm(message, user, chat, context: ContextTypes.DEFAULT_TYPE, allowed_ip: str = None):
    """核心建立 VM 邏輯 (支援指令發起與按鈕點擊發起)"""
    # 1. 強制檢查：此指令僅限於群組內執行
    if chat.type == "private" or chat.id > 0:
        await message.reply_text(
            "⛔ **此功能只在工作群組內有效。**\n\n"
            "請至工作群組內發送 `/create_vm` 或點擊按鈕。\n"
            "主機建立完成後，我會在此私聊視窗把 IP、帳號與密碼私發給您！",
            parse_mode="Markdown"
        )
        return

    # 2. 檢查群組權限
    if not is_chat_authorized(chat.id):
        await message.reply_text("⛔ 本群組未被授權使用此機器人。")
        return

    # 3. 檢查使用者白名單
    if not is_user_authorized(user.id):
        await message.reply_text(f"⛔ 抱歉，使用者 `{user.id}` 不在操作白名單內，請聯繫管理員新增。", parse_mode="Markdown")
        return

    # 4. 檢查配額限制
    user_vms = [rg for rg, info in ACTIVE_VMS.items() if info["user_id"] == user.id]
    if len(user_vms) >= config.MAX_VMS_PER_USER:
        await message.reply_text(
            f"⚠️ 您當前已有正在運行的 VM (`{user_vms[0]}`)，每人同時上限 {config.MAX_VMS_PER_USER} 台。\n"
            "若已測試完畢，請先使用 `/delete_vm` 提前釋放資源。",
            parse_mode="Markdown"
        )
        return

    if len(ACTIVE_VMS) >= config.MAX_TOTAL_VMS:
        await message.reply_text(
            f"⚠️ 目前系統同時運行的 VM 已達全域上限 ({config.MAX_TOTAL_VMS} 台)，請稍候其他同事測試完成。",
            parse_mode="Markdown"
        )
        return

    # 發送排隊確認訊息
    group_msg = (
        f"🚀 收到 **@{user.username or user.first_name}** 的建立請求！\n"
        f"系統正在 Azure 配置全新的臨時 Windows 主機...\n"
        "⏳ 預計需時 1~2 分鐘，建立完成後**連線 IP 與帳號密碼將透過「私聊」直接發送給您**。\n\n"
        "⚠️ *請確保您曾私聊點擊過機器人的 /start，否則機器人可能無法私聊發送帳密給您。*"
    )
    status_message = await message.reply_text(group_msg, parse_mode="Markdown")

    # 非同步執行 Azure 部署，避免阻塞 Telegram 事件迴圈
    try:
        vm_data = await asyncio.to_thread(
            azure_mgr.create_temporary_vm,
            user_id=user.id,
            user_display_name=user.username or user.first_name,
            allowed_ip=allowed_ip
        )
    except Exception as e:
        logger.error(f"建立 VM 失敗: {e}", exc_info=True)
        await status_message.edit_text(f"❌ 建立失敗，錯誤原因：`{str(e)}`", parse_mode="Markdown")
        return

    rg_name = vm_data["resource_group"]
    public_ip = vm_data["public_ip"]
    expire_time = datetime.now(timezone.utc) + timedelta(hours=config.DEFAULT_LIFETIME_HOURS)

    # 登記至運行列表
    ACTIVE_VMS[rg_name] = {
        "user_id": user.id,
        "chat_id": chat.id,
        "username": user.username or user.first_name,
        "expire_at": expire_time,
        "ip": public_ip,
        "vm_name": vm_data["vm_name"]
    }

    # 1. 私聊發送敏感帳密給該同事 (包含 3 個一鍵複製到剪貼簿按鈕)
    private_text = (
        "🎉 **您的臨時 Windows 測試機已就緒！**\n\n"
        f"🌐 **公網 IP：** `{public_ip}`\n"
        f"👤 **使用者名稱：** `{vm_data['admin_username']}`\n"
        f"🔑 **登入密碼：** `{vm_data['admin_password']}`\n"
        f"🛡 **防火牆限制：** `{vm_data['allowed_ip']}`\n"
        f"💻 **主機規格：** `{vm_data['vm_size']}`\n\n"
        f"⏱ **自動銷毀倒數：** {config.DEFAULT_LIFETIME_HOURS} 小時\n"
        f"🌙 **自動關機排程：** 每天 {config.AUTO_SHUTDOWN_TIME[:2]}:{config.AUTO_SHUTDOWN_TIME[2:]}\n\n"
        "💡 **連線提示：**\n"
        "• 點擊下方按鈕可**一鍵複製** IP、帳號與密碼到剪貼簿。\n"
        "• 使用 Windows 自帶「遠端桌面連線 (mstsc.exe)」輸入 IP 即可連線。\n"
        "• 測試完畢可點擊下方按鈕或輸入 `/delete_vm` 提早釋放以節省額度。"
    )

    private_buttons = [
        [
            InlineKeyboardButton(f"📋 複製 IP ({public_ip})", copy_text=CopyTextButton(public_ip)),
        ],
        [
            InlineKeyboardButton(f"📋 複製帳號 ({vm_data['admin_username']})", copy_text=CopyTextButton(vm_data["admin_username"])),
            InlineKeyboardButton("📋 複製密碼", copy_text=CopyTextButton(vm_data["admin_password"])),
        ],
        [
            InlineKeyboardButton("🔍 查詢剩餘時間", callback_data="btn_my_vm"),
            InlineKeyboardButton("🗑 立即提早釋放", callback_data=f"del:{rg_name}"),
        ]
    ]

    private_sent = False
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=private_text,
            reply_markup=InlineKeyboardMarkup(private_buttons),
            parse_mode="Markdown"
        )
        private_sent = True
    except Exception as e:
        logger.warning(f"無法向用戶 {user.id} 發送私聊訊息 (可能尚未私聊過 Bot): {e}")

    # 2. 群組通知已就緒
    group_success_text = (
        f"✅ **@{user.username or user.first_name}** 的臨時 Windows 建立完成！\n"
        f"🌐 IP：`{public_ip}`\n"
        f"⏳ 本機預計在 {config.DEFAULT_LIFETIME_HOURS} 小時後自動刪除。\n"
    )
    if private_sent:
        group_success_text += "📩 **帳號密碼已透過私信安全發送給您，請檢查私聊訊息！**"
    else:
        group_success_text += (
            "⚠️ **警告：機器人無法私信給您！**\n"
            "請先點擊機器人名稱並點擊「開始 (Start)」，然後輸入 `/my_vm` 獲取密碼。"
        )

    await status_message.edit_text(group_success_text, parse_mode="Markdown")

    # 3. 註冊自動銷毀定時任務
    context.job_queue.run_once(
        auto_delete_job_callback,
        when=config.DEFAULT_LIFETIME_HOURS * 3600,
        data={"rg_name": rg_name, "user_id": user.id, "chat_id": chat.id, "username": user.username or user.first_name},
        name=rg_name
    )


async def auto_delete_job_callback(context: ContextTypes.DEFAULT_TYPE):
    """時間期滿自動刪除回呼"""
    job_data = context.job.data
    rg_name = job_data["rg_name"]
    chat_id = job_data["chat_id"]
    username = job_data["username"]

    # 防呆檢查：若該主機已提前手動釋放 (已不在 ACTIVE_VMS 中)，直接返回，不重複調用 Azure 也不重複發通知
    if rg_name not in ACTIVE_VMS:
        logger.info(f"主機 {rg_name} 先前已提前手動釋放，跳過逾期定時銷毀。")
        return

    logger.info(f"借用期滿，正在向 Azure 發送銷毀資源群組命令 {rg_name}...")
    ip = ACTIVE_VMS[rg_name].get("ip", "")
    ACTIVE_VMS.pop(rg_name, None)

    success, err_msg = await asyncio.to_thread(azure_mgr.delete_vm_resource_group, rg_name)

    if success:
        msg = (
            f"⏳ **【逾期自動銷毀通知】**\n"
            f"由 @{username} 申請的臨時 Windows 測試機 (`{rg_name}`) 借用期滿，已向 Azure 發送銷毀命令。\n"
            f"🔍 *系統將在後台自動覆核雲端釋放狀態。*"
        )
        # 排定 70 秒後後台自動二次覆核 Azure 是否完全清空
        context.job_queue.run_once(
            verify_deletion_callback,
            when=70,
            data={"rg_name": rg_name, "chat_id": chat_id, "username": username, "ip": ip, "retry_count": 0},
            name=f"verify_{rg_name}"
        )
    else:
        msg = (
            f"🚨 **【自動銷毀異常警報】**\n"
            f"主機 `{rg_name}` 到期嘗試銷毀時，向 Azure 發送刪除命令失敗！\n"
            f"錯誤原因：`{err_msg}`\n"
            f"⚠️ 資源可能未被釋放，請管理員手動前往 Azure Portal 處理！"
        )

    try:
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"發送銷毀通知失敗: {e}")


async def verify_deletion_callback(context: ContextTypes.DEFAULT_TYPE):
    """刪除後二次覆核回呼 (確認 Azure 實體是否真正清空，有異常主動警報)"""
    data = context.job.data
    rg_name = data["rg_name"]
    chat_id = data["chat_id"]
    username = data["username"]
    ip = data.get("ip", "")
    retry_count = data.get("retry_count", 0)

    status = await asyncio.to_thread(azure_mgr.check_rg_status, rg_name)
    logger.info(f"後台覆核資源群組 {rg_name} 狀態: {status}")

    # 若已經不存在，或狀態為 Deleted，代表已經徹底清空！
    if not status["exists"] or status["state"] == "Deleted":
        logger.info(f"✅ Azure 雲端二次覆核確認：資源群組 {rg_name} 已徹底銷毀並釋放！")
        return

    # 若狀態仍為 Deleting (大硬碟正在釋放)，且複查次數小於 3 次，再等 60 秒複查
    if status["state"] == "Deleting" and retry_count < 3:
        logger.info(f"資源群組 {rg_name} 正在進行 Azure 雲端資源擦除 (Deleting)，60 秒後進行第 {retry_count + 1} 次覆核...")
        data["retry_count"] = retry_count + 1
        context.job_queue.run_once(
            verify_deletion_callback,
            when=60,
            data=data,
            name=f"verify_{rg_name}"
        )
        return

    # 若超過 3 次重試依然存在，或狀態異常，發送警報
    alert_msg = (
        f"⚠️ **【主機銷毀二次覆核警報】**\n"
        f"• 主機：@{username} 的 VM (`{ip}`)\n"
        f"• 資源群組：`{rg_name}`\n"
        f"• 目前 Azure 狀態：`{status['state']}`\n\n"
        "系統在發送銷毀命令後進行實體覆核，發現該資源在 Azure 上仍未完全消失！\n"
        "👉 請管理員私聊輸入 `/admin` 點擊「🧹 雲端對帳檢查」，或登入 Azure Portal 進行確認。"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"發送覆核警報失敗: {e}")


async def my_vm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/my_vm 查詢自己的 VM 狀態 (包含一鍵複製 IP 與操作按鈕)"""
    user = update.effective_user
    chat = update.effective_chat
    is_private = (chat.type == "private" or chat.id > 0)
    user_vms = [(rg, info) for rg, info in ACTIVE_VMS.items() if info["user_id"] == user.id]

    if not user_vms:
        keyboard = [[InlineKeyboardButton("🚀 立即建立 Windows", callback_data="btn_create_vm")]]
        await update.message.reply_text(
            "您目前沒有任何運行的臨時 VM。可點擊下方按鈕或使用 `/create_vm` 進行建立。",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    # 按建立順序排序
    user_vms.sort(key=lambda x: x[1]["expire_at"])

    text = f"🖥 **@{user.username or user.first_name} 目前擁有的臨時 VM ({len(user_vms)}/2 台)：**\n\n"
    keyboard = []
    for idx, (rg, info) in enumerate(user_vms, 1):
        remain_min = max(0, int((info["expire_at"] - datetime.now(timezone.utc)).total_seconds() // 60))
        text += (
            f"🔹 **主機 #{idx}**\n"
            f"  • 公網 IP：`{info['ip']}`\n"
            f"  • 剩餘時間：約 `{remain_min}` 分鐘\n"
            f"  • 資源名稱：`{rg}`\n\n"
        )
        row = [
            InlineKeyboardButton(f"📋 複製 #{idx} IP", copy_text=CopyTextButton(info["ip"])),
            InlineKeyboardButton(f"🗑 刪除 #{idx}", callback_data=f"del:{rg}")
        ]
        # 延長按鈕只有在私聊時才顯示給管理員
        if is_admin(user.id) and is_private:
            row.append(InlineKeyboardButton(f"⏱ 延長 +1h", callback_data=f"adm_ext:{rg}:1"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔄 刷新狀態", callback_data="btn_my_vm")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def delete_vm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/delete_vm [1|2|IP|名稱] 提前手動釋放資源"""
    user = update.effective_user
    user_vms = [(rg, info) for rg, info in ACTIVE_VMS.items() if info["user_id"] == user.id]

    if not user_vms:
        await update.message.reply_text("您目前沒有任何正在運行的 VM 可供刪除。")
        return

    # 按建立時間先後排序
    user_vms.sort(key=lambda x: x[1]["expire_at"])

    # 1. 如果有帶參數
    if context.args:
        arg = context.args[0].strip()
        target_rg = None
        target_info = None

        if arg in ["1", "2"]:
            idx = int(arg) - 1
            if idx < len(user_vms):
                target_rg, target_info = user_vms[idx]
            else:
                await update.message.reply_text(f"⚠️ 您目前只有 {len(user_vms)} 台 VM，不存在 #{arg} 主機。")
                return
        elif any(arg == info["ip"] for rg, info in user_vms):
            target_rg, target_info = next((rg, info) for rg, info in user_vms if info["ip"] == arg)
        elif any(arg == rg for rg, info in user_vms):
            target_rg, target_info = next((rg, info) for rg, info in user_vms if rg == arg)
        else:
            await update.message.reply_text(
                f"⚠️ 找不到主機 `{arg}`。\n"
                "可輸入 `/delete_vm 1` 刪除第一台，或輸入 `/my_vm` 查看清單。",
                parse_mode="Markdown"
            )
            return

        await execute_deletion(update.message, target_rg, target_info["ip"], user, context=context)
        return

    # 2. 如果沒帶參數，且只有 1 台
    if len(user_vms) == 1:
        rg, info = user_vms[0]
        keyboard = [
            [InlineKeyboardButton(f"🗑 確認刪除唯一主機 (IP: {info['ip']})", callback_data=f"del:{rg}")],
            [InlineKeyboardButton("❌ 取消", callback_data="del:cancel")]
        ]
        await update.message.reply_text(
            "您當前有 1 台正在運行的 VM，請點擊下方按鈕確認釋放：",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # 3. 如果擁有多台：彈出互動按鈕
    keyboard = []
    for idx, (rg, info) in enumerate(user_vms, 1):
        remain_min = max(0, int((info["expire_at"] - datetime.now(timezone.utc)).total_seconds() // 60))
        btn_text = f"🗑 刪除 #{idx} (IP: {info['ip']}，剩 {remain_min} 分鐘)"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"del:{rg}")])
    keyboard.append([InlineKeyboardButton("❌ 取消操作", callback_data="del:cancel")])

    await update.message.reply_text(
        "ℹ️ 請點擊按鈕選擇要刪除哪一台主機：\n*(亦可輸入指令 `/delete_vm 1` 或 `/delete_vm 2`)*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理主選單快捷按鈕點擊事件 (btn_*)"""
    query = update.callback_query
    data = query.data
    user = update.effective_user
    chat = update.effective_chat

    if data == "btn_create_vm":
        # 私聊檢查
        if chat.type == "private" or chat.id > 0:
            await query.answer("⚠️ 建立 VM 僅限在工作群組內發起，請至工作群組內點擊或發送 /create_vm！", show_alert=True)
            return
        await query.answer("正在處理建立請求...")
        await process_create_vm(query.message, user, chat, context)
        return

    if data == "btn_my_vm":
        await query.answer()
        is_private = (chat.type == "private" or chat.id > 0)
        user_vms = [(rg, info) for rg, info in ACTIVE_VMS.items() if info["user_id"] == user.id]
        if not user_vms:
            keyboard = [[InlineKeyboardButton("🚀 立即建立 Windows", callback_data="btn_create_vm")]]
            await query.message.reply_text(
                "您目前沒有任何運行的臨時 VM。",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        user_vms.sort(key=lambda x: x[1]["expire_at"])
        text = f"🖥 **@{user.username or user.first_name} 目前擁有的臨時 VM ({len(user_vms)}/2 台)：**\n\n"
        keyboard = []
        for idx, (rg, info) in enumerate(user_vms, 1):
            remain_min = max(0, int((info["expire_at"] - datetime.now(timezone.utc)).total_seconds() // 60))
            text += (
                f"🔹 **主機 #{idx}**\n"
                f"  • 公網 IP：`{info['ip']}`\n"
                f"  • 剩餘時間：約 `{remain_min}` 分鐘\n"
                f"  • 資源名稱：`{rg}`\n\n"
            )
            row = [
                InlineKeyboardButton(f"📋 複製 #{idx} IP", copy_text=CopyTextButton(info["ip"])),
                InlineKeyboardButton(f"🗑 刪除 #{idx}", callback_data=f"del:{rg}")
            ]
            if is_admin(user.id) and is_private:
                row.append(InlineKeyboardButton(f"⏱ 延長 +1h", callback_data=f"adm_ext:{rg}:1"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔄 刷新狀態", callback_data="btn_my_vm")])
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if data == "btn_delete_vm":
        await query.answer()
        user_vms = [(rg, info) for rg, info in ACTIVE_VMS.items() if info["user_id"] == user.id]
        if not user_vms:
            await query.message.reply_text("您目前沒有任何正在運行的 VM 可供刪除。")
            return
        user_vms.sort(key=lambda x: x[1]["expire_at"])
        keyboard = []
        for idx, (rg, info) in enumerate(user_vms, 1):
            remain_min = max(0, int((info["expire_at"] - datetime.now(timezone.utc)).total_seconds() // 60))
            btn_text = f"🗑 刪除 #{idx} (IP: {info['ip']}，剩 {remain_min} 分鐘)"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"del:{rg}")])
        keyboard.append([InlineKeyboardButton("❌ 取消操作", callback_data="del:cancel")])
        await query.message.reply_text(
            "請點擊按鈕選擇要釋放的主機：",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "btn_admin":
        if not is_admin(user.id):
            await query.answer("⛔ 您沒有管理員權限。", show_alert=True)
            return
        await query.answer()
        text, reply_markup = build_admin_panel()
        await query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        return


async def text_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理底部常駐鍵盤發送的文字按鈕"""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    user = update.effective_user
    chat = update.effective_chat

    if text == "🚀 建立 Windows VM":
        await process_create_vm(update.message, user, chat, context)
    elif text == "🔍 查詢我的 VM":
        await my_vm_handler(update, context)
    elif text == "🗑 釋放/刪除 VM":
        await delete_vm_handler(update, context)
    elif text == "❓ 使用說明":
        await start_handler(update, context)
    elif text == "👑 管理員主控台":
        await admin_handler(update, context)


async def new_chat_members_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """當機器人被加入新群組時，自動在群內發送歡迎訊息與操作指南"""
    if not update.message or not update.message.new_chat_members:
        return
    bot_user = await context.bot.get_me()
    for member in update.message.new_chat_members:
        if member.id == bot_user.id:
            welcome_text = (
                "👋 **大家好！我是 Azure 臨時 Windows VM 管理機器人。**\n\n"
                "專為團隊提供臨時 Windows 測試主機（附隨機密碼、2 小時自動銷毀與定時防呆關機）。\n\n"
                "💡 **點擊下方按鈕或對話框左下角的「Menu」選單即可立即操作：**"
            )
            reply_markup = build_main_menu(member.id, is_private=False)
            await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
            break


async def delete_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理使用者點擊刪除按鈕的事件 (del:*)"""
    query = update.callback_query
    await query.answer()

    data = query.data
    user = update.effective_user

    if data == "del:cancel":
        await query.edit_message_text("❌ 操作已取消。")
        return

    rg_name = data.replace("del:", "")
    if rg_name not in ACTIVE_VMS:
        await query.edit_message_text("⚠️ 該虛擬主機已不存在或已被刪除。")
        return

    info = ACTIVE_VMS[rg_name]
    # 檢查權限：只能刪除自己的主機，除非是管理員
    if info["user_id"] != user.id and not is_admin(user.id):
        await query.edit_message_text("⛔ 您無權刪除其他同事的主機。")
        return

    ip = info["ip"]
    await execute_deletion(query.message, rg_name, ip, user, is_edit=True, context=context)


async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/admin 或 /all_vms 管理員總覽控制面板"""
    user = update.effective_user
    chat = update.effective_chat
    if not is_admin(user.id):
        await update.message.reply_text("⛔ 您沒有管理員權限。")
        return

    # 若在群組發送，提示轉移至私聊，防止在公開群組洩漏同事的主機 IP 與資源
    if chat.type != "private" and chat.id < 0:
        await update.message.reply_text(
            "🔒 **為保護伺服器資訊安全，管理員主控台僅限私聊使用。**\n請點擊機器人名稱進入私聊視窗，並輸入 `/admin` 開啟主控台。",
            parse_mode="Markdown"
        )
        return

    text, reply_markup = build_admin_panel()
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


def build_admin_panel():
    """生成管理員面板的訊息與操作按鈕"""
    if not ACTIVE_VMS:
        keyboard = [
            [InlineKeyboardButton("🔄 重新整理", callback_data="adm_refresh")],
            [InlineKeyboardButton("🔍 雲端實體對帳與殘留檢查", callback_data="adm_audit")]
        ]
        return (
            f"📊 **【管理員總覽主控台】**\n\n"
            f"• 全域使用量：`0 / {config.MAX_TOTAL_VMS}` 台\n"
            f"• 狀態：目前本地追蹤沒有任何正在運行的臨時 VM。\n\n"
            f"💡 *可點擊下方「雲端實體對帳」直接掃描 Azure 訂閱是否有遺留殘留。*",
            InlineKeyboardMarkup(keyboard)
        )

    text = (
        f"📊 **【管理員總覽主控台】**\n"
        f"全域使用量：`{len(ACTIVE_VMS)} / {config.MAX_TOTAL_VMS}` 台\n\n"
    )

    keyboard = []
    for idx, (rg, info) in enumerate(ACTIVE_VMS.items(), 1):
        remain_min = max(0, int((info["expire_at"] - datetime.now(timezone.utc)).total_seconds() // 60))
        text += (
            f"🔹 **主機 #{idx}**\n"
            f"  • 使用者：@{info['username']} (ID: `{info['user_id']}`)\n"
            f"  • 公網 IP：`{info['ip']}`\n"
            f"  • 剩餘時間：約 `{remain_min}` 分鐘\n"
            f"  • 資源名稱：`{rg}`\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(f"⏱ #{idx} 延長 +1小時", callback_data=f"adm_ext:{rg}:1"),
            InlineKeyboardButton(f"⏱ #{idx} 延長 +2小時", callback_data=f"adm_ext:{rg}:2"),
            InlineKeyboardButton(f"🗑 刪除 #{idx}", callback_data=f"adm_del:{rg}")
        ])

    keyboard.append([
        InlineKeyboardButton("🔄 重新整理", callback_data="adm_refresh"),
        InlineKeyboardButton("🔍 雲端實體對帳", callback_data="adm_audit")
    ])
    return text, InlineKeyboardMarkup(keyboard)


async def extend_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/extend <1|2|IP|名稱> [小時數，預設1] 管理員命令列延長時間"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ 您沒有管理員權限。")
        return

    if not context.args:
        await update.message.reply_text(
            "用法：`/extend <1|2|IP|名稱> [小時數]`\n\n"
            "例如：\n"
            "• `/extend 1 2` (將 1 號機延長 2 小時)\n"
            "• `/extend 20.1.2.3 1` (延長指定 IP 1 小時)\n"
            "💡 *亦可直接點擊「👑 管理員主控台」按鈕一鍵延長！*",
            parse_mode="Markdown"
        )
        return

    target = context.args[0].strip()
    hours = 1
    if len(context.args) > 1 and context.args[1].isdigit():
        hours = int(context.args[1])

    # 尋找目標機器
    target_rg = None
    sorted_vms = sorted(ACTIVE_VMS.items(), key=lambda x: x[1]["expire_at"])

    if target in ["1", "2"]:
        idx = int(target) - 1
        if idx < len(sorted_vms):
            target_rg = sorted_vms[idx][0]
    elif any(target == info["ip"] for rg, info in ACTIVE_VMS.items()):
        target_rg = next(rg for rg, info in ACTIVE_VMS.items() if info["ip"] == target)
    elif target in ACTIVE_VMS:
        target_rg = target

    if not target_rg:
        await update.message.reply_text(f"⚠️ 找不到主機 `{target}`。可輸入 `/admin` 查看當前運行列表。")
        return

    await apply_extension(context, target_rg, hours, update.message.reply_text)


async def apply_extension(context, rg_name: str, hours: int, send_fn):
    """執行延長邏輯並重設 JobQueue"""
    info = ACTIVE_VMS[rg_name]
    info["expire_at"] = info["expire_at"] + timedelta(hours=hours)

    current_jobs = context.job_queue.get_jobs_by_name(rg_name)
    for job in current_jobs:
        job.schedule_removal()

    new_seconds = max(60, int((info["expire_at"] - datetime.now(timezone.utc)).total_seconds()))
    context.job_queue.run_once(
        auto_delete_job_callback,
        when=new_seconds,
        data={"rg_name": rg_name, "user_id": info["user_id"], "chat_id": info["chat_id"], "username": info["username"]},
        name=rg_name
    )

    remain_min = max(0, int((info["expire_at"] - datetime.now(timezone.utc)).total_seconds() // 60))
    await send_fn(
        f"⏱ **主機延長成功！**\n"
        f"• 主機：@{info['username']} 的 VM (`{info['ip']}`)\n"
        f"• 延長時數：+{hours} 小時\n"
        f"• 新剩餘時間：約 `{remain_min}` 分鐘"
    )


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理管理員按鈕點擊事件 (adm_*)"""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not is_admin(user.id):
        await query.answer("⛔ 您沒有管理員權限。", show_alert=True)
        return

    data = query.data

    if data == "adm_refresh":
        text, reply_markup = build_admin_panel()
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        return

    if data.startswith("adm_ext:"):
        parts = data.split(":")
        rg_name = parts[1]
        hours = int(parts[2])
        if rg_name not in ACTIVE_VMS:
            await query.answer("該主機已不存在。", show_alert=True)
            return
        await apply_extension(context, rg_name, hours, query.message.reply_text)
        # 刷新面板
        text, reply_markup = build_admin_panel()
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        return

    if data == "adm_audit":
        await query.answer("正在向 Azure 實體掃描所有資源群組...")
        azure_rgs = await asyncio.to_thread(azure_mgr.list_all_bot_rgs_in_azure)

        active_rg_names = set(ACTIVE_VMS.keys())
        orphans = [rg for rg in azure_rgs if rg["name"] not in active_rg_names]

        audit_text = (
            f"🔍 **【Azure 雲端實體對帳報告】**\n\n"
            f"• 機器人追蹤中：`{len(active_rg_names)}` 台\n"
            f"• Azure 實體資源群組總數：`{len(azure_rgs)}` 個\n\n"
        )

        keyboard = []
        if not orphans:
            audit_text += "🟢 **對帳完畢，狀態完美！**\nAzure 上無任何未追蹤的殘留資源群組，所有歷史主機均已徹底釋放乾淨。"
        else:
            audit_text += f"⚠️ **發現 {len(orphans)} 個殘留/未受控的資源群組：**\n"
            for orphan in orphans:
                audit_text += (
                    f"  • `{orphan['name']}`\n"
                    f"    狀態: `{orphan['state']}` | 建立者 ID: `{orphan['created_by']}`\n"
                )
                keyboard.append([
                    InlineKeyboardButton(f"🧹 一鍵清理 {orphan['name'][:22]}...", callback_data=f"adm_clean:{orphan['name']}")
                ])

        keyboard.append([InlineKeyboardButton("🔙 返回管理面板", callback_data="adm_refresh")])
        await query.edit_message_text(audit_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if data.startswith("adm_clean:"):
        target_rg = data.replace("adm_clean:", "")
        await query.answer(f"正在清理殘留資源群組 {target_rg}...")
        success, err = await asyncio.to_thread(azure_mgr.delete_vm_resource_group, target_rg)
        if success:
            await query.message.reply_text(f"✅ 已成功向 Azure 發送清理殘留群組 `{target_rg}` 的命令！")
        else:
            await query.message.reply_text(f"❌ 清理 `{target_rg}` 失敗：`{err}`")
        # 重新執行對帳
        azure_rgs = await asyncio.to_thread(azure_mgr.list_all_bot_rgs_in_azure)
        active_rg_names = set(ACTIVE_VMS.keys())
        orphans = [rg for rg in azure_rgs if rg["name"] not in active_rg_names]
        audit_text = f"🔍 **【Azure 雲端實體對帳報告】**\n\n• 殘留群組數：`{len(orphans)}` 個\n"
        keyboard = [[InlineKeyboardButton("🔙 返回管理面板", callback_data="adm_refresh")]]
        await query.edit_message_text(audit_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if data.startswith("adm_del:"):
        rg_name = data.split(":")[1]
        if rg_name not in ACTIVE_VMS:
            await query.answer("該主機已不存在。", show_alert=True)
            return
        info = ACTIVE_VMS[rg_name]
        ip = info["ip"]
        creator = info["username"]
        ACTIVE_VMS.pop(rg_name, None)

        # 取消定時銷毀任務
        if context and context.job_queue:
            for job in context.job_queue.get_jobs_by_name(rg_name):
                job.schedule_removal()
                logger.info(f"已取消主機 {rg_name} 的到期定時銷毀任務")

        success, err = await asyncio.to_thread(azure_mgr.delete_vm_resource_group, rg_name)
        if success:
            await query.message.reply_text(
                f"🗑 管理員已成功向 Azure 發送銷毀 @{creator} 的主機 (IP: `{ip}`) 命令！\n"
                f"🔍 *系統將在 70 秒後自動在後台進行二次覆核。*"
            )
            if context and context.job_queue:
                context.job_queue.run_once(
                    verify_deletion_callback,
                    when=70,
                    data={"rg_name": rg_name, "chat_id": query.message.chat.id, "username": creator, "ip": ip, "retry_count": 0},
                    name=f"verify_{rg_name}"
                )
        else:
            await query.message.reply_text(f"❌ 強制銷毀主機 {rg_name} 失敗：`{err}`")

        # 刷新面板
        text, reply_markup = build_admin_panel()
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        return


async def execute_deletion(message, rg_name: str, ip: str, user, is_edit: bool = False, context: ContextTypes.DEFAULT_TYPE = None):
    """執行後台刪除並回報結果 (自動取消 JobQueue 定時銷毀排程並排定二次覆核)"""
    ACTIVE_VMS.pop(rg_name, None)

    # 取消 JobQueue 中的自動銷毀計時器
    if context and context.job_queue:
        current_jobs = context.job_queue.get_jobs_by_name(rg_name)
        for job in current_jobs:
            job.schedule_removal()
            logger.info(f"已取消主機 {rg_name} 的到期定時銷毀任務")

    success, err_msg = await asyncio.to_thread(azure_mgr.delete_vm_resource_group, rg_name)

    if success:
        success_text = (
            f"✅ 已成功銷毀 @{user.username or user.first_name} 的主機 (IP: `{ip}`)！\n"
            f"• 資源群組：`{rg_name}`\n"
            f"• 狀態：Azure 已接收銷毀指令，正在釋放所有計費資源。\n"
            f"🔍 *系統將在 70 秒後自動向 Azure 雲端二次覆核，確認是否徹底釋放乾淨。*"
        )
        if context and context.job_queue:
            context.job_queue.run_once(
                verify_deletion_callback,
                when=70,
                data={
                    "rg_name": rg_name,
                    "chat_id": message.chat.id,
                    "username": user.username or user.first_name,
                    "ip": ip,
                    "retry_count": 0
                },
                name=f"verify_{rg_name}"
            )
    else:
        success_text = (
            f"❌ **銷毀失敗！**\n"
            f"無法向 Azure 發送刪除命令，錯誤原因：`{err_msg}`\n"
            f"⚠️ 資源可能未被釋放，請聯繫管理員！"
        )

    if is_edit:
        await message.edit_text(success_text, parse_mode="Markdown")
    else:
        await message.reply_text(success_text, parse_mode="Markdown")


async def post_init(application):
    """程式啟動後自動向 Telegram 註冊官方指令選單 (左下角 Menu 快捷鍵)"""
    commands = [
        BotCommand("create_vm", "建立臨時 Windows (限群組)"),
        BotCommand("my_vm", "查詢我的 VM 狀態與剩餘時間"),
        BotCommand("delete_vm", "提前手動釋放 VM (按鈕選單)"),
        BotCommand("help", "顯示說明選單與功能按鈕"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("已成功向 Telegram 官方註冊快捷指令選單 (Menu 按鈕)")
    except Exception as e:
        logger.warning(f"向 Telegram 註冊指令選單時出現警告: {e}")


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise ValueError("錯誤：未配置 TELEGRAM_BOT_TOKEN 環境變數！")
    if not config.AZURE_SUBSCRIPTION_ID:
        raise ValueError("錯誤：未配置 AZURE_SUBSCRIPTION_ID 環境變數！")

    logger.info("啟動 Telegram Azure 臨時 VM 機器人 (v1.5 - 常駐底部鍵盤與官方 Menu 選單版)...")
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # 註冊指令處理器
    app.add_handler(CommandHandler(["start", "help"], start_handler))
    app.add_handler(CommandHandler("create_vm", create_vm_handler))
    app.add_handler(CommandHandler("my_vm", my_vm_handler))
    app.add_handler(CommandHandler("delete_vm", delete_vm_handler))
    app.add_handler(CommandHandler(["admin", "all_vms"], admin_handler))
    app.add_handler(CommandHandler("extend", extend_handler))

    # 註冊底部常駐鍵盤的文字按鈕處理器
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^(🚀 建立 Windows VM|🔍 查詢我的 VM|🗑 釋放/刪除 VM|❓ 使用說明|👑 管理員主控台)$"),
            text_button_handler
        )
    )

    # 註冊機器人加入群組時的自動打招呼歡迎處理器
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members_handler)
    )

    # 註冊互動按鈕回呼處理器
    app.add_handler(CallbackQueryHandler(menu_callback_handler, pattern=r"^btn_"))
    app.add_handler(CallbackQueryHandler(delete_callback_handler, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern=r"^adm_"))

    # 啟動輪詢 (Long Polling)
    app.run_polling()


if __name__ == "__main__":
    main()
