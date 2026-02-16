from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import PlainTextResponse, HTMLResponse
from sqlmodel import Session
from models import Product, Order, engine, get_session, create_db_and_tables
from services import event_bus, send_email_notification, verify_ecpay_checksum, create_ecpay_params
import timezone
from datetime import datetime
import asyncio

app = FastAPI(title="Mini E-commerce Backend")

@app.on_event("startup")
def startup():
    create_db_and_tables()
    asyncio.create_task(event_worker())

async def event_worker():
    async for event in event_bus.subscribe(): # 注意 Event Loop Blocked
        if event.get("event") == "PAYMENT_SUCCESS":
            order_id = event.get("order_id")
            
            with Session(engine) as session:
                # 1. 取得訂單資訊
                order = session.get(Order, order_id)
                if not order:
                    continue

                # 2. 執行扣除庫存
                product = session.get(Product, order.product_id)

                # 檢查庫存[下單後]
                if product and product.stock > 0:
                    product.stock -= 1
                    session.add(product)
                    print(f" [更新] name {product.name} 更新庫存 {product.stock}")
                else:
                    print(f" [失敗] order_id {order_id} 庫存不足")

                session.commit()

                # 3. 寄送 Email
                asyncio.create_task(send_email_notification(order.id, order.amount)) 

        await asyncio.sleep(0.1)

# --- API Endpoints ---

@app.post("/products/")
def create_product(product: Product, session: Session = Depends(get_session)):
    session.add(product)
    session.commit()
    session.refresh(product)
    return product

@app.post("/orders/", response_class=HTMLResponse)
def create_order(product_id: int, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)

    # 檢查庫存[下單前] # todo: 寫在服務層/ 排他鎖 Exclusive Lock (.with_for_update())
    if not product or product.stock <= 0:
        raise HTTPException(status_code=400, detail=f" product_id {product_id} 不存在或庫存不足")
    
    # 1. 建立訂單
    new_order = Order(product_id=product_id, amount=product.price)
    session.add(new_order)
    session.commit()
    session.refresh(new_order)
    
    # 2. 產生綠界參數
    ecpay_data = create_ecpay_params(
        order_id=new_order.id, 
        amount=new_order.amount, 
        item_name=product.name
    )
    
    # 3. 建立自動跳轉的 HTML 表單
    payment_url = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"
    
    # 將參數轉成 hidden input
    inputs = "".join([f'<input type="hidden" name="{k}" value="{v}">' for k, v in ecpay_data.items()])
    
    html_content = f"""
    <html>
        <body onload="document.forms[0].submit()">
            <h3>正在導向至綠界支付，請稍候...</h3>
            <form method="POST" action="{payment_url}">
                {inputs}
            </form>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/webhooks/ecpay")
async def ecpay_webhook(request: Request, session: Session = Depends(get_session)): # 注意 Event Loop Blocked
    form_data = await request.form()
    payload = dict(form_data)
    
    # 0. 檢查
    if not verify_ecpay_checksum(payload):
        print(" [失敗] 簽章驗證失敗")
        return "0|CheckMacValue Error"

    # 2. 狀態流轉
    order_id = int(payload.get("CustomField1", 0))
    rtn_code = payload.get("RtnCode")
    is_simulate = payload.get("SimulatePaid") == "1"

    # 模擬付款(不觸發後續動作)
    if is_simulate:
        print(f" [成功] order_id {order_id} 模擬付款成功，不觸發後續動作 ")
        return "1|OK"

    # 真實付款
    if rtn_code == "1":
        order = session.get(Order, order_id)
        # 檢查 避免重複處理 (Idempotency)
        if order and order.status == "pending":
            order.status = "paid"
            session.add(order)
            session.commit()
            
            # 自動化
            await event_bus.publish({"event": "PAYMENT_SUCCESS", "order_id": order.id})
            print(f" [成功] 訂單 {order_id} 付款成功。")
            
    return "1|OK"

@app.get("/orders/{order_id}/status")
def get_order_status(order_id: int, session: Session = Depends(get_session)):
    order = session.get(Order, order_id)
    
    if not order:
        raise HTTPException(status_code=404, detail=f"訂單 ID {order_id} 不存在")
    
    return {
        "order_id": order.id,
        "status": order.status,
        "updated_at": datetime.now(timezone.utc)
    }