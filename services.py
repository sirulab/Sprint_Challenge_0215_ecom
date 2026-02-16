import asyncio
import os
from email.message import EmailMessage
import aiosmtplib
from dotenv import load_dotenv
import hashlib
import urllib.parse
import datetime

# 載入 .env 檔案中的環境變數
load_dotenv()

class EventBus:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def publish(self, event_data: dict):
        await self.queue.put(event_data)

    async def subscribe(self):
        while True:
            yield await self.queue.get()
            self.queue.task_done()

async def send_email_notification(order_id: int, amount: int):

    # 1. 讀取配置
    smtp_host = os.getenv("MAIL_HOST")
    smtp_port = int(os.getenv("MAIL_PORT", 587))
    smtp_user = os.getenv("MAIL_USER")
    smtp_password = os.getenv("MAIL_PASSWORD")
    mail_from = os.getenv("MAIL_FROM")
    mail_to = os.getenv("MAIL_TO")

    # 2. 建立郵件訊息內容
    message = EmailMessage()
    message["From"] = mail_from
    message["To"] = mail_to
    message["Subject"] = f" 訂單確認：您的訂單 #{order_id} 已經成功付款！"
    
    html_content = f"""
    <html>
        <body>
            <h2>感謝您的購買！</h2>
            <p>您的訂單 <strong>#{order_id}</strong> 處理完成。</p>
            <p>付款金額：<strong>${amount} TWD</strong></p>
            <br>
            <p>祝您有愉快的一天！</p>
            <p><i>Mini E-commerce Backend 團隊敬上</i></p>
        </body>
    </html>
    """
    message.add_alternative(html_content, subtype="html")

    # 3. 執行非同步發送
    try:
        await aiosmtplib.send(
            message,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_password,
            start_tls=True,
        )
        print(f" [郵件系統] 成功寄出付款確認信 訂單編號: #{order_id}")
    except Exception as e:
        print(f" [郵件系統] 寄出失敗: {str(e)}")

def create_ecpay_params(order_id: int, amount: int, item_name: str):

    host = os.getenv("HOST_URL")
    
    params = {
        "MerchantID": os.getenv("ECPAY_MERCHANT_ID"),
        "MerchantTradeNo": f"ORDER{order_id}T{int(datetime.datetime.now().timestamp())}", # 需為唯一值
        "MerchantTradeDate": datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
        "PaymentType": "aio",
        "TotalAmount": amount,
        "TradeDesc": "Mini Ecommerce Order",
        "ItemName": item_name,
        "ReturnURL": f"{host}/webhooks/ecpay", 
        "ChoosePayment": "ALL",
        "EncryptType": 1,
        "CustomField1": str(order_id), 
    }


    params["CheckMacValue"] = generate_check_mac_value(params)
    return params

def generate_check_mac_value(params: dict) -> str:
    sorted_params = sorted(params.items())
    raw_string = "&".join([f"{k}={v}" for k, v in sorted_params])
    
    hash_key = os.getenv("ECPAY_HASH_KEY")
    hash_iv = os.getenv("ECPAY_HASH_IV")
    full_string = f"HashKey={hash_key}&{raw_string}&HashIV={hash_iv}"
    
    encoded_string = urllib.parse.quote_plus(full_string).lower()
    
    fixed_string = (
        encoded_string
        .replace("%2d", "-")
        .replace("%5f", "_")
        .replace("%2e", ".")
        .replace("%21", "!")
        .replace("%2a", "*")
        .replace("%28", "(")
        .replace("%29", ")")
    )
    
    # SHA256 加密
    return hashlib.sha256(fixed_string.encode('utf-8')).hexdigest().upper()

def verify_ecpay_checksum(params: dict) -> bool:
    test_params = params.copy()
    received_mac = test_params.pop("CheckMacValue", None)
    
    if not received_mac:
        return False

    calculated_mac = generate_check_mac_value(test_params)
    
    if calculated_mac != received_mac:
        print(f"DEBUG: 收到 MAC: {received_mac}")
        print(f"DEBUG: 計算 MAC: {calculated_mac}")

    return calculated_mac == received_mac

# 實例化全域變數
event_bus = EventBus()