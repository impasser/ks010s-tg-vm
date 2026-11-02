import secrets
import string
import logging
from datetime import datetime, timezone
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

    def create_temporary_vm(self, user_id: int, user_display_name: str, allowed_ip: str = None) -> dict:
        """
        為使用者建立專屬獨立 Resource Group 與 Windows 11 VM。
        allowed_ip: 若有傳入同事的外網 IP，NSG 僅放行該 IP；若無則全網開放。
        """
        suffix = secrets.token_hex(3)
        rg_name = f"rg-tempvm-{user_id}-{suffix}"
        vm_name = f"win11-{suffix}"
        admin_username = "azureuser"
        admin_password = self.generate_secure_password()
        location = config.AZURE_LOCATION

        logger.info(f"正在為 [{user_display_name}] 建立專屬資源群組: {rg_name}...")

        # 1. 建立獨立 Resource Group (帶上 Tag 標籤方便追蹤)
        rg_params = {
            "location": location,
            "tags": {
                "ManagedBy": "TelegramTempVMBot",
                "CreatedBy": str(user_id),
                "CreatedAt": datetime.now(timezone.utc).isoformat(),
            }
        }
        self.resource_client.resource_groups.create_or_update(rg_name, rg_params)

        # 2. 建立虛擬網路 (VNet) 與子網路 (Subnet)
        logger.info(f"建立網路拓撲: {rg_name}...")
        vnet_name = f"vnet-{suffix}"
        subnet_name = "default"
        vnet_poller = self.network_client.virtual_networks.begin_create_or_update(
            rg_name,
            vnet_name,
            {
                "location": location,
                "address_space": {"address_prefixes": ["10.0.0.0/16"]},
                "subnets": [{"name": subnet_name, "address_prefix": "10.0.0.0/24"}]
            }
        )
        vnet_result = vnet_poller.result()
        subnet_ref = vnet_result.subnets[0].id

        # 3. 建立網路安全群組 (NSG) 放行 RDP (3389)
        nsg_name = f"nsg-{suffix}"
        source_address_prefix = allowed_ip if allowed_ip else "*"
        nsg_params = {
            "location": location,
            "security_rules": [
                {
                    "name": "Allow-RDP",
                    "protocol": "Tcp",
                    "source_port_range": "*",
                    "destination_port_range": "3389",
                    "source_address_prefix": source_address_prefix,
                    "destination_address_prefix": "*",
                    "access": "Allow",
                    "priority": 1000,
                    "direction": "Inbound"
                }
            ]
        }
        nsg_poller = self.network_client.network_security_groups.begin_create_or_update(
            rg_name, nsg_name, nsg_params
        )
        nsg_result = nsg_poller.result()

        # 4. 建立公開 IP (Public IP)
        pip_name = f"pip-{suffix}"
        pip_params = {
            "location": location,
            "public_ip_allocation_method": "Static",
            "sku": {"name": "Standard"}
        }
        pip_poller = self.network_client.public_ip_addresses.begin_create_or_update(
            rg_name, pip_name, pip_params
        )
        pip_result = pip_poller.result()

        # 5. 建立網路介面卡 (NIC)
        nic_name = f"nic-{suffix}"
        nic_params = {
            "location": location,
            "ip_configurations": [
                {
                    "name": "ipconfig1",
                    "subnet": {"id": subnet_ref},
                    "public_ip_address": {"id": pip_result.id}
                }
            ],
            "network_security_group": {"id": nsg_result.id}
        }
        nic_poller = self.network_client.network_interfaces.begin_create_or_update(
            rg_name, nic_name, nic_params
        )
        nic_result = nic_poller.result()

        # 6. 建立 Windows 11 VM
        logger.info(f"正在配置 Windows 11 虛擬主機 {vm_name}...")
        vm_params = {
            "location": location,
            "hardware_profile": {"vm_size": config.DEFAULT_VM_SIZE},
            "storage_profile": {
                "image_reference": {
                    "publisher": config.IMAGE_PUBLISHER,
                    "offer": config.IMAGE_OFFER,
                    "sku": config.IMAGE_SKU,
                    "version": config.IMAGE_VERSION
                },
                "os_disk": {
                    "create_option": "FromImage",
                    "managed_disk": {"storage_account_type": config.OS_DISK_TYPE}
                }
            },
            "os_profile": {
                "computer_name": vm_name[:15],  # NetBIOS 名稱最多 15 字元
                "admin_username": admin_username,
                "admin_password": admin_password,
                "windows_configuration": {
                    "provision_vm_agent": True,
                    "enable_automatic_updates": False
                }
            },
            "network_profile": {
                "network_interfaces": [{"id": nic_result.id}]
            }
        }
        vm_poller = self.compute_client.virtual_machines.begin_create_or_update(
            rg_name, vm_name, vm_params
        )
        vm_result = vm_poller.result()

        # 7. 取得最終已綁定的公網 IP 地址
        pip_current = self.network_client.public_ip_addresses.get(rg_name, pip_name)
        public_ip = pip_current.ip_address

        # 8. 設定 Azure 原生「每日晚上 19:00 自動關機」排程
        self._configure_auto_shutdown(rg_name, vm_result.id)

        return {
            "resource_group": rg_name,
            "vm_name": vm_name,
            "public_ip": public_ip,
            "admin_username": admin_username,
            "admin_password": admin_password,
            "allowed_ip": source_address_prefix,
            "vm_size": config.DEFAULT_VM_SIZE
        }

    def _configure_auto_shutdown(self, rg_name: str, vm_resource_id: str):
        """利用 Azure DevTestLab Schedule 建立每日 19:00 自動關機排程"""
        try:
            schedule_name = f"shutdown-computevm-{vm_resource_id.split('/')[-1]}"
            schedule_params = {
                "location": config.AZURE_LOCATION,
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

    def delete_vm_resource_group(self, rg_name: str):
        """直接刪除整個 Resource Group，徹底清空硬碟、網卡、IP 等所有計費資源"""
        logger.info(f"正在刪除資源群組: {rg_name}...")
        try:
            # 非同步發送刪除命令，不阻塞本機進程
            self.resource_client.resource_groups.begin_delete(rg_name)
            return True
        except Exception as e:
            logger.error(f"刪除資源群組 {rg_name} 失敗: {e}")
            return False

    def list_active_temp_rgs(self) -> list:
        """列出當前由機器人建立且仍在運行的資源群組"""
        active_rgs = []
        try:
            for rg in self.resource_client.resource_groups.list():
                if rg.tags and rg.tags.get("ManagedBy") == "TelegramTempVMBot":
                    active_rgs.append({
                        "name": rg.name,
                        "created_by": rg.tags.get("CreatedBy"),
                        "created_at": rg.tags.get("CreatedAt")
                    })
        except Exception as e:
            logger.error(f"查詢資源群組失敗: {e}")
        return active_rgs
