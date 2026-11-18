import base64
import re
import secrets
import string
import time
import logging
from datetime import datetime, timezone
from azure.core.exceptions import HttpResponseError
from azure.core.rest import HttpRequest
from azure.identity import DefaultAzureCredential
try:
    from azure.mgmt.resource.resources import ResourceManagementClient
except (ImportError, AttributeError):
    from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
import config

logger = logging.getLogger(__name__)

class AzureVMManager:
    def __init__(self):
        # DefaultAzureCredential 會自動按順序檢測：
        # 1. 環境變數中的 AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID (第三方容器/本地)
        # 2. Azure Container Apps 的受控識別 Managed Identity (Azure 原生)
        self.credential = DefaultAzureCredential()
        self.subscription_id = config.AZURE_SUBSCRIPTION_ID
        
        self.resource_client = ResourceManagementClient(self.credential, self.subscription_id)
        self.compute_client = ComputeManagementClient(self.credential, self.subscription_id)
        self.network_client = NetworkManagementClient(self.credential, self.subscription_id)

    @staticmethod
    def generate_secure_password(length: int = 16) -> str:
        """生成符合 Azure 複雜度要求的隨機密碼"""
        upper = secrets.choice(string.ascii_uppercase)
        lower = secrets.choice(string.ascii_lowercase)
        digit = secrets.choice(string.digits)
        special = secrets.choice("!@#$%&*_-+=")
        all_chars = string.ascii_letters + string.digits + "!@#$%&*_-+="
        remaining = [secrets.choice(all_chars) for _ in range(length - 4)]
        password_list = [upper, lower, digit, special] + remaining
        secrets.SystemRandom().shuffle(password_list)
        return "".join(password_list)

    def _deploy_arm_template(self, rg_name: str, deployment_name: str, template: dict, timeout: int = 600) -> dict:
        """透過 Microsoft.Resources/deployments 部署 ARM 範本並輪詢結果（與 Azure Portal 相同機制）。"""
        url = (
            f"https://management.azure.com/subscriptions/{self.subscription_id}"
            f"/resourcegroups/{rg_name}/providers/Microsoft.Resources/deployments/{deployment_name}"
            f"?api-version=2021-04-01"
        )
        req_body = {"properties": {"mode": "Incremental", "template": template}}
        resp = None
        for attempt in range(3):
            resp = self.resource_client.send_request(HttpRequest("PUT", url, json=req_body))
            if resp.status_code in (200, 201):
                break
            if resp.status_code == 400 and "LocationNotAvailableForResourceType" in resp.text() and attempt < 2:
                logger.warning(f"ARM 部署節點暫時回報 LocationNotAvailableForResourceType，2 秒後重試 ({attempt + 1}/3)...")
                time.sleep(2)
                continue
            raise RuntimeError(f"ARM 部署啟動失敗 ({resp.status_code}): {resp.text()}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(4)
            poll_resp = self.resource_client.send_request(HttpRequest("GET", url))
            if poll_resp.status_code != 200:
                raise RuntimeError(f"ARM 部署查詢失敗 ({poll_resp.status_code}): {poll_resp.text()}")
            data = poll_resp.json()
            props = data.get("properties", {})
            state = props.get("provisioningState")
            if state == "Succeeded":
                return props.get("outputs", {})
            if state in ("Failed", "Canceled"):
                err_info = props.get("error", {})
                raise RuntimeError(f"ARM 部署失敗 ({state}): {err_info}")
        raise TimeoutError(f"ARM 部署逾時 ({timeout}s)")

    def create_temporary_vm(
        self,
        user_id: int,
        user_display_name: str,
        allowed_ip: str = None,
        location: str = None,
        software_keys: list = None,
        os_type: str = "windows",
    ) -> dict:
        """
        為使用者建立專屬獨立 Resource Group 與臨時 VM (支援 Windows / Linux)。
        allowed_ip: 若有傳入同事的外網 IP，NSG 僅放行該 IP；若無則全網開放。
        location: 部署區域，預設使用 config.AZURE_LOCATION。
        software_keys: 欲預裝的軟體代碼清單 (僅 Windows 適用，對應 config.AVAILABLE_SOFTWARE)。
        os_type: 作業系統類型 ('windows' 或 'linux')，預設 'windows'。
        """
        is_linux = (os_type == "linux")
        suffix = secrets.token_hex(3)
        rg_name = f"rg-tempvm-{user_id}-{suffix}"
        vm_name = f"lin-{suffix}" if is_linux else f"win-{suffix}"
        admin_username = "azureuser"
        admin_password = self.generate_secure_password()
        location = location or config.AZURE_LOCATION

        vm_size = config.LINUX_VM_SIZE if is_linux else config.DEFAULT_VM_SIZE
        os_disk_type = config.LINUX_OS_DISK_TYPE if is_linux else config.OS_DISK_TYPE
        image_ref = (
            {
                "publisher": config.LINUX_IMAGE_PUBLISHER,
                "offer": config.LINUX_IMAGE_OFFER,
                "sku": config.LINUX_IMAGE_SKU,
                "version": config.LINUX_IMAGE_VERSION,
            }
            if is_linux
            else {
                "publisher": config.IMAGE_PUBLISHER,
                "offer": config.IMAGE_OFFER,
                "sku": config.IMAGE_SKU,
                "version": config.IMAGE_VERSION,
            }
        )
        nsg_rule_name = "Allow-SSH" if is_linux else "Allow-RDP"
        nsg_port = "22" if is_linux else "3389"
        os_label = "Linux (Debian 13)" if is_linux else "Windows"

        logger.info(f"正在為 [{user_display_name}] 建立專屬資源群組: {rg_name}...")

        # 1. 建立獨立 Resource Group (帶上 Tag 標籤方便追蹤)
        rg_tags = {
            "ManagedBy": "TelegramTempVMBot",
            "CreatedBy": str(user_id),
            "CreatedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.resource_client.resource_groups.create_or_update(
                rg_name, {"location": location, "tags": rg_tags}
            )
        except HttpResponseError as rg_err:
            if "LocationNotAvailableForResourceGroup" in str(rg_err) and location != "eastasia":
                logger.warning(
                    f"區域 {location} 不支援建立 Resource Group，改用 eastasia 存放 RG 中繼資料（VM 仍建在 {location}）。"
                )
                self.resource_client.resource_groups.create_or_update(
                    rg_name, {"location": "eastasia", "tags": rg_tags}
                )
            else:
                raise

        # 2. 透過 ARM 範本一次部署 VNet、NSG、Public IP、NIC 與虛擬主機
        logger.info(f"正在透過 ARM 範本部署網路與 {os_label} 虛擬主機 ({location}): {rg_name}/{vm_name}...")
        vnet_name = f"vnet-{suffix}"
        subnet_name = "default"
        nsg_name = f"nsg-{suffix}"
        pip_name = f"pip-{suffix}"
        nic_name = f"nic-{suffix}"
        source_address_prefix = allowed_ip if allowed_ip else "*"

        template = {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
            "contentVersion": "1.0.0.0",
            "resources": [
                {
                    "type": "Microsoft.Network/virtualNetworks",
                    "apiVersion": "2024-01-01",
                    "name": vnet_name,
                    "location": location,
                    "properties": {
                        "addressSpace": {"addressPrefixes": ["10.0.0.0/16"]},
                        "subnets": [
                            {
                                "name": subnet_name,
                                "properties": {"addressPrefix": "10.0.0.0/24"},
                            }
                        ],
                    },
                },
                {
                    "type": "Microsoft.Network/networkSecurityGroups",
                    "apiVersion": "2020-05-01",
                    "name": nsg_name,
                    "location": location,
                    "properties": {
                        "securityRules": [
                            {
                                "name": nsg_rule_name,
                                "properties": {
                                    "protocol": "Tcp",
                                    "sourcePortRange": "*",
                                    "destinationPortRange": nsg_port,
                                    "sourceAddressPrefix": source_address_prefix,
                                    "destinationAddressPrefix": "*",
                                    "access": "Allow",
                                    "priority": 1000,
                                    "direction": "Inbound",
                                },
                            }
                        ]
                    },
                },
                {
                    "type": "Microsoft.Network/publicIPAddresses",
                    "apiVersion": "2023-06-01",
                    "name": pip_name,
                    "location": location,
                    "sku": {"name": "Standard"},
                    "properties": {"publicIPAllocationMethod": "Static"},
                },
                {
                    "type": "Microsoft.Network/networkInterfaces",
                    "apiVersion": "2022-11-01",
                    "name": nic_name,
                    "location": location,
                    "dependsOn": [
                        f"[resourceId('Microsoft.Network/virtualNetworks', '{vnet_name}')]",
                        f"[resourceId('Microsoft.Network/networkSecurityGroups', '{nsg_name}')]",
                        f"[resourceId('Microsoft.Network/publicIPAddresses', '{pip_name}')]",
                    ],
                    "properties": {
                        "ipConfigurations": [
                            {
                                "name": "ipconfig1",
                                "properties": {
                                    "subnet": {
                                        "id": f"[resourceId('Microsoft.Network/virtualNetworks/subnets', '{vnet_name}', '{subnet_name}')]"
                                    },
                                    "publicIPAddress": {
                                        "id": f"[resourceId('Microsoft.Network/publicIPAddresses', '{pip_name}')]"
                                    },
                                },
                            }
                        ],
                        "networkSecurityGroup": {
                            "id": f"[resourceId('Microsoft.Network/networkSecurityGroups', '{nsg_name}')]"
                        },
                    },
                },
                {
                    "type": "Microsoft.Compute/virtualMachines",
                    "apiVersion": "2024-07-01",
                    "name": vm_name,
                    "location": location,
                    "dependsOn": [
                        f"[resourceId('Microsoft.Network/networkInterfaces', '{nic_name}')]",
                    ],
                    "properties": {
                        "hardwareProfile": {"vmSize": vm_size},
                        "storageProfile": {
                            "imageReference": image_ref,
                            "osDisk": {
                                "createOption": "FromImage",
                                "managedDisk": {"storageAccountType": os_disk_type},
                            },
                        },
                        "osProfile": {
                            "computerName": vm_name[:15],
                            "adminUsername": admin_username,
                            "adminPassword": admin_password,
                            **(
                                {"linuxConfiguration": {"disablePasswordAuthentication": False}}
                                if is_linux
                                else {
                                    "windowsConfiguration": {
                                        "provisionVMAgent": True,
                                        "enableAutomaticUpdates": False,
                                    }
                                }
                            ),
                        },
                        "networkProfile": {
                            "networkInterfaces": [
                                {"id": f"[resourceId('Microsoft.Network/networkInterfaces', '{nic_name}')]"}
                            ]
                        },
                    },
                },
            ],
            "outputs": {
                "publicIp": {
                    "type": "string",
                    "value": f"[reference(resourceId('Microsoft.Network/publicIPAddresses', '{pip_name}')).ipAddress]",
                },
                "vmId": {
                    "type": "string",
                    "value": f"[resourceId('Microsoft.Compute/virtualMachines', '{vm_name}')]",
                },
            },
        }

        try:
            outputs = self._deploy_arm_template(rg_name, f"deploy-{suffix}", template, timeout=600)
            public_ip = outputs.get("publicIp", {}).get("value")
            vm_id = outputs.get("vmId", {}).get("value")
            if not public_ip:
                pip_current = self.network_client.public_ip_addresses.get(rg_name, pip_name)
                public_ip = pip_current.ip_address
        except Exception:
            logger.warning(f"建立 VM 失敗，正在自動清理殘留資源群組: {rg_name}...")
            try:
                self.delete_vm_resource_group(rg_name)
            except Exception as cleanup_err:
                logger.error(f"自動清理失敗的資源群組 {rg_name} 時發生錯誤: {cleanup_err}")
            raise

        # 3. 若有勾選預裝軟體 (僅 Windows)，透過 CustomScriptExtension 在 OS 內靜默安裝
        installed_software = []
        rustdesk_id = None
        rustdesk_password = None
        if not is_linux and software_keys:
            sw_res = self._install_software(rg_name, vm_name, location, software_keys)
            installed_software = sw_res.get("names", [])
            rustdesk_id = sw_res.get("rustdesk_id")
            rustdesk_password = sw_res.get("rustdesk_password")

        # 4. 設定 Azure 原生「每日晚上 19:00 自動關機」排程
        self._configure_auto_shutdown(rg_name, vm_id, location=location)

        return {
            "resource_group": rg_name,
            "vm_name": vm_name,
            "public_ip": public_ip,
            "admin_username": admin_username,
            "admin_password": admin_password,
            "allowed_ip": source_address_prefix,
            "vm_size": vm_size,
            "os_type": os_type,
            "location": location,
            "installed_software": installed_software,
            "rustdesk_id": rustdesk_id,
            "rustdesk_password": rustdesk_password,
        }

    def _get_extension_output(self, rg_name: str, vm_name: str, ext_name: str = "InstallSoftware") -> str:
        """從 VM Extension 的 instanceView 讀取腳本標準輸出訊息"""
        url = (
            f"https://management.azure.com/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{rg_name}/providers/Microsoft.Compute/virtualMachines/{vm_name}"
            f"/extensions/{ext_name}?$expand=instanceView&api-version=2024-07-01"
        )
        resp = self.resource_client.send_request(HttpRequest("GET", url))
        if resp.status_code != 200:
            return ""
        iv = resp.json().get("properties", {}).get("instanceView", {})
        messages = []
        for s in iv.get("statuses", []) + iv.get("substatuses", []):
            msg = s.get("message")
            if msg:
                messages.append(msg)
        return "\n".join(messages)

    def _install_software(self, rg_name: str, vm_name: str, location: str, software_keys: list) -> dict:
        """利用 Azure CustomScriptExtension 在 Windows VM 內自動背景靜默安裝預選軟體"""
        scripts = []
        names = []
        rustdesk_password = None
        if "rustdesk" in software_keys:
            safe_chars = string.ascii_letters + string.digits + "@#%+=-_"
            rustdesk_password = "".join(secrets.choice(safe_chars) for _ in range(12))

        for key in software_keys:
            item = config.AVAILABLE_SOFTWARE.get(key)
            if item:
                ps = item["ps_script"]
                if rustdesk_password and "__RUSTDESK_PASSWORD__" in ps:
                    ps = ps.replace("__RUSTDESK_PASSWORD__", rustdesk_password)
                scripts.append(ps)
                names.append(item["name"])

        if not scripts:
            return {"names": [], "rustdesk_id": None, "rustdesk_password": None}

        combined_ps = "; ".join(scripts)
        encoded_ps = base64.b64encode(combined_ps.encode("utf-16-le")).decode("ascii")
        command_to_execute = f"powershell -ExecutionPolicy Bypass -EncodedCommand {encoded_ps}"

        logger.info(f"正在為 {vm_name} 安裝預選軟體 ({', '.join(names)})...")
        try:
            ext_template = {
                "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
                "contentVersion": "1.0.0.0",
                "resources": [
                    {
                        "type": "Microsoft.Compute/virtualMachines/extensions",
                        "apiVersion": "2024-07-01",
                        "name": f"{vm_name}/InstallSoftware",
                        "location": location,
                        "properties": {
                            "publisher": "Microsoft.Compute",
                            "type": "CustomScriptExtension",
                            "typeHandlerVersion": "1.10",
                            "autoUpgradeMinorVersion": True,
                            "settings": {},
                            "protectedSettings": {
                                "commandToExecute": command_to_execute
                            },
                        },
                    }
                ],
            }
            self._deploy_arm_template(rg_name, f"deploy-sw-{vm_name}", ext_template, timeout=360)

            rustdesk_id = None
            if "rustdesk" in software_keys:
                out_text = self._get_extension_output(rg_name, vm_name, "InstallSoftware")
                m = re.search(r"RUSTDESK_ID=([0-9]{6,})", out_text)
                if m:
                    rustdesk_id = m.group(1)
                else:
                    logger.warning(f"未能從 {vm_name} 取得 RustDesk ID，輸出內容: {out_text[:200]}")

            logger.info(f"已成功為 {vm_name} 完成軟體安裝: {', '.join(names)}")
            return {
                "names": names,
                "rustdesk_id": rustdesk_id,
                "rustdesk_password": rustdesk_password if rustdesk_id else None,
            }
        except Exception as e:
            logger.warning(f"背景安裝軟體時出現警告 (不影響 VM 開機使用): {e}")
            return {"names": [], "rustdesk_id": None, "rustdesk_password": None}

    def _configure_auto_shutdown(self, rg_name: str, vm_resource_id: str, location: str = None):
        """利用 Azure DevTestLab Schedule 建立每日 19:00 自動關機排程"""
        try:
            schedule_name = f"shutdown-computevm-{vm_resource_id.split('/')[-1]}"
            schedule_params = {
                "location": location or config.AZURE_LOCATION,
                "properties": {
                    "status": "Enabled",
                    "taskType": "ComputeVmShutdownTask",
                    "dailyRecurrence": {"time": config.AUTO_SHUTDOWN_TIME.replace(":", "")},
                    "timeZoneId": config.AUTO_SHUTDOWN_TIMEZONE,
                    "targetResourceId": vm_resource_id,
                    "notificationSettings": {
                        "status": "Disabled"
                    }
                }
            }
            # 透過 Generic ARM Resource 建立 DevTestLab schedule
            self.resource_client.resources.begin_create_or_update(
                resource_group_name=rg_name,
                resource_provider_namespace="Microsoft.DevTestLab",
                parent_resource_path="",
                resource_type="schedules",
                resource_name=schedule_name,
                api_version="2018-09-15",
                parameters=schedule_params
            )
            logger.info(f"已成功為 {rg_name} 配置晚上自動關機排程 ({config.AUTO_SHUTDOWN_TIME})")
        except Exception as e:
            logger.warning(f"配置自動關機排程時出現警告 (非致命): {e}")

    def delete_vm_resource_group(self, rg_name: str) -> tuple[bool, str]:
        """直接刪除整個 Resource Group，徹底清空硬碟、網卡、IP 等所有計費資源"""
        logger.info(f"正在向 Azure 發送刪除命令: {rg_name}...")
        try:
            # 非同步發送刪除命令，不阻塞本機進程
            self.resource_client.resource_groups.begin_delete(rg_name)
            return True, "Success"
        except Exception as e:
            err_msg = str(e)
            if "ResourceGroupNotFound" in err_msg or "not found" in err_msg.lower():
                logger.info(f"資源群組 {rg_name} 在 Azure 上已不存在 (先前已完成銷毀)。")
                return True, "AlreadyDeleted"
            logger.error(f"刪除資源群組 {rg_name} 失敗: {e}")
            return False, err_msg

    def check_rg_status(self, rg_name: str) -> dict:
        """檢查特定資源群組在 Azure 上的即時存在性與佈署狀態"""
        try:
            if not self.resource_client.resource_groups.check_existence(rg_name):
                return {"exists": False, "state": "Deleted"}
            rg = self.resource_client.resource_groups.get(rg_name)
            state = rg.properties.provisioning_state if rg.properties else "Unknown"
            return {"exists": True, "state": state}
        except Exception as e:
            err_msg = str(e)
            if "ResourceGroupNotFound" in err_msg or "not found" in err_msg.lower():
                return {"exists": False, "state": "Deleted"}
            logger.warning(f"檢查資源群組 {rg_name} 狀態失敗: {e}")
            return {"exists": True, "state": "Error"}

    def list_all_bot_rgs_in_azure(self) -> list:
        """直接向 Azure 訂閱查詢所有由本機器人建立的資源群組 (以 rg-tempvm- 開頭或帶 Tag)"""
        found = []
        try:
            for rg in self.resource_client.resource_groups.list():
                is_bot_rg = (
                    rg.name.startswith("rg-tempvm-") or
                    (rg.tags and rg.tags.get("ManagedBy") == "TelegramTempVMBot")
                )
                if is_bot_rg:
                    state = rg.properties.provisioning_state if rg.properties else "Active"
                    found.append({
                        "name": rg.name,
                        "created_by": rg.tags.get("CreatedBy", "未知") if rg.tags else "未知",
                        "created_at": rg.tags.get("CreatedAt", "") if rg.tags else "",
                        "state": state
                    })
        except Exception as e:
            logger.error(f"對帳查詢 Azure 資源群組清單失敗: {e}")
        return found
