import json
import logging
import time
import uuid
from datetime import datetime
from urllib.parse import quote, urlencode, urljoin

import aiohttp
import requests
from src.core.config import settings


class XUIApi:
    def __init__(self, panel_url, username, password):
        self.base_url = panel_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = None
        self.xray_config = None

    async def _ensure_session(self):
        """اطمینان از وجود session و لاگین"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
            await self.login()
        return self.session

    async def _make_request(self, method, url, **kwargs):
        """ارسال درخواست به صورت غیرهمزمان"""
        session = await self._ensure_session()
        try:
            async with session.request(method, url, timeout=10, **kwargs) as response:
                if response.status == 401:
                    # اگر لاگین منقضی شده، دوباره لاگین کن
                    await self.login()
                    async with session.request(method, url, timeout=10, **kwargs) as retry_response:
                        return await self._handle_response(retry_response)
                
                return await self._handle_response(response)
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Request failed: {e}")

    async def _handle_response(self, response):
        """پردازش پاسخ دریافتی"""
        try:
            if response.status >= 400:
                error_text = await response.text()
                raise ConnectionError(f"HTTP Error {response.status}: {error_text}")
            
            text = await response.text()
            if not text:
                return {"success": True}
            return await response.json()
        except aiohttp.ContentTypeError:
            text = await response.text()
            raise ConnectionError(f"Invalid JSON response: {text[:100]}...")
        except Exception as e:
            raise ConnectionError(f"Request failed: {e}")

    async def login(self):
        """ورود به پنل"""
        login_url = self._build_url("login")
        payload = {"username": self.username, "password": self.password}
        response = await self._make_request("post", login_url, data=payload)
        if not response.get("success"):
            raise ConnectionError(f"Login failed: {response.get('msg')}")
        return True

    def _build_url(self, *parts):
        path = "/".join(map(str, parts))
        return urljoin(self.base_url + "/", path)

    async def _get_xray_config(self):
        """دریافت کانفیگ Xray"""
        url = self._build_url("panel/xray/")
        response = await self._make_request("post", url)
        if not response.get("success"):
            raise RuntimeError(f"Failed to get Xray config: {response.get('msg')}")
        self.xray_config = json.loads(response["obj"])["xraySetting"]
        return self.xray_config

    async def _update_xray_config(self):
        """به‌روزرسانی کانفیگ Xray"""
        if not self.xray_config:
            raise ValueError("Xray config is not loaded.")

        url = self._build_url("panel/xray/update")
        payload = {"xraySetting": json.dumps(self.xray_config, indent=2)}
        response = await self._make_request("post", url, data=payload)
        if not response.get("success"):
            raise RuntimeError(f"Failed to update Xray config: {response.get('msg')}")
        return True

    async def get_inbound(self, inbound_id):
        """دریافت اطلاعات یک اینباند"""
        url = self._build_url("panel/api/inbounds/list")
        response = await self._make_request("get", url)
        if not response.get("success"):
            raise RuntimeError(f"Failed to get inbounds list: {response.get('msg')}")
        for inbound in response.get("obj", []):
            if inbound.get("id") == inbound_id:
                return inbound
        raise ValueError(f"Inbound with ID {inbound_id} not found.")

    async def add_client_to_inbound(
        self, inbound_id, client_remark, total_gb=0, expiry_days=0, flow=""
    ):
        """اضافه کردن کاربر جدید به اینباند"""
        url = self._build_url("panel/api/inbounds/addClient")
        new_uuid = str(uuid.uuid4())

        total_bytes = int(total_gb * 1024**3) if total_gb > 0 else 0
        expiry_timestamp = (
            int((time.time() + expiry_days * 24 * 60 * 60) * 1000)
            if expiry_days > 0
            else 0
        )

        client_object = {
            "id": new_uuid,
            "email": client_remark,
            "enable": True,
            "flow": flow,
            "limitIp": 0,
            "totalGB": total_bytes,
            "expiryTime": expiry_timestamp,
            "tgId": "",
            "subId": "",
        }
        settings_payload = {"clients": [client_object]}
        payload = {"id": inbound_id, "settings": json.dumps(settings_payload)}
        response = await self._make_request("post", url, data=payload)
        if not response.get("success"):
            raise RuntimeError(f"Failed to add client: {response.get('msg')}")
        return new_uuid

    async def get_vless_uri(self, inbound_id, client_uuid, remark, inbound_data=None):
        """ساخت لینک کانفیگ VLESS"""
        if not inbound_data:
            inbound_data = await self.get_inbound(inbound_id)

        stream_settings = json.loads(inbound_data["streamSettings"])
        reality_settings = stream_settings.get("realitySettings", {})
        reality_advanced_settings = reality_settings.get("settings", reality_settings)

        server_address = settings.PUBLIC_HOST
        port = inbound_data["port"]
        network_type = stream_settings.get("network", "tcp")
        security = stream_settings.get("security")

        public_key = reality_advanced_settings.get("publicKey", "")
        fingerprint = reality_advanced_settings.get("fingerprint", "chrome")
        spider_x = reality_advanced_settings.get("spiderX", "")

        server_names = reality_settings.get("serverNames", [""])
        sni = server_names[0] if server_names else ""
        short_ids = reality_settings.get("shortIds", [])
        short_id = short_ids[0] if short_ids else ""

        params = {
            "type": network_type,
            "security": security,
            "flow": "xtls-rprx-vision-udp443",
            "pbk": public_key,
            "fp": fingerprint,
            "sni": sni,
        }
        if short_id:
            params["sid"] = short_id
        if spider_x:
            params["spx"] = spider_x

        query_string = urlencode(params, quote_via=quote)

        inbound_remark = inbound_data.get("remark") or "VLESS"
        encoded_remark = quote(remark)
        uri_remark = f"{inbound_remark}-user-{encoded_remark}"

        uri = (
            f"vless://{client_uuid}@{server_address}:{port}?{query_string}#{uri_remark}"
        )
        return uri

    async def create_vless_user(self, name, limit_gb=0, expiry_date=None, inbound_id=None):
        """ساخت کاربر VLESS جدید با امکانات کامل"""
        try:
            # اگر inbound_id داده نشده، از تنظیمات استفاده کن
            if inbound_id is None:
                inbound_id = settings.VLESS_INBOUND_ID
            
            # ساخت remark برای کاربر
            client_remark = f"user-{name.lower().replace(' ', '-')[:20]}"
            
            # محاسبه روزهای باقی‌مانده تا تاریخ انقضا
            expiry_days = 0
            if expiry_date:
                expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
                delta = expiry_dt - datetime.now()
                expiry_days = max(0, delta.days)
            
            # اضافه کردن کاربر به اینباند
            client_uuid = await self.add_client_to_inbound(
                inbound_id=inbound_id,
                client_remark=client_remark,
                total_gb=limit_gb,
                expiry_days=expiry_days,
                flow="xtls-rprx-vision-udp443"
            )
            
            # دریافت لینک کانفیگ
            config_link = await self.get_vless_uri(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                remark=name
            )
            
            return {
                'success': True,
                'uuid': client_uuid,
                'link': config_link,
                'name': name,
                'limit_gb': limit_gb,
                'expiry_date': expiry_date
            }
        except Exception as e:
            logging.error(f"Error creating VLESS user: {e}")
            return {
                'success': False,
                'message': str(e)
            }

    async def get_users(self):
        """دریافت لیست کامل کاربران"""
        try:
            url = self._build_url("panel/api/inbounds/list")
            response = await self._make_request("get", url)
            
            if not response.get("success"):
                return []
            
            users = []
            for inbound in response.get("obj", []):
                settings = json.loads(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                
                for client in clients:
                    total_gb = client.get('totalGB', 0) / (1024**3)
                    expiry_time = client.get('expiryTime', 0)
                    expiry_date = None
                    if expiry_time > 0:
                        expiry_date = datetime.fromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d")
                    
                    users.append({
                        'id': client.get('id'),
                        'name': client.get('email', '').replace('user-', ''),
                        'limit': round(total_gb, 2) if total_gb > 0 else 'نامحدود',
                        'expiry': expiry_date if expiry_date else 'نامحدود',
                        'enable': client.get('enable', True),
                        'inbound_id': inbound.get('id')
                    })
            
            return users
        except Exception as e:
            logging.error(f"Error getting users: {e}")
            return []

    async def delete_user(self, user_id):
        """حذف کاربر با شناسه"""
        try:
            # پیدا کردن کاربر در اینباندها
            url = self._build_url("panel/api/inbounds/list")
            response = await self._make_request("get", url)
            
            if not response.get("success"):
                return {'success': False, 'message': 'دریافت لیست اینباندها失敗'}
            
            for inbound in response.get("obj", []):
                settings = json.loads(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                
                for client in clients:
                    if str(client.get('id')) == str(user_id):
                        # حذف کاربر از اینباند
                        del_url = self._build_url(
                            "panel/api/inbounds", inbound['id'], "delClient", user_id
                        )
                        del_response = await self._make_request("post", del_url)
                        if del_response.get('success'):
                            return {'success': True, 'message': 'کاربر با موفقیت حذف شد'}
                        else:
                            return {'success': False, 'message': del_response.get('msg', 'خطا در حذف')}
            
            return {'success': False, 'message': 'کاربر یافت نشد'}
        except Exception as e:
            logging.error(f"Error deleting user: {e}")
            return {'success': False, 'message': str(e)}

    async def get_stats(self):
        """دریافت آمار کلی پنل"""
        try:
            url = self._build_url("panel/api/inbounds/list")
            response = await self._make_request("get", url)
            
            if not response.get("success"):
                return {
                    'total_users': 0,
                    'today_traffic': 0,
                    'total_traffic': 0,
                    'server_status': 'خطا در دریافت آمار'
                }
            
            inbounds = response.get("obj", [])
            total_users = 0
            total_traffic_bytes = 0
            
            for inbound in inbounds:
                settings = json.loads(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                total_users += len(clients)
                total_traffic_bytes += inbound.get("up", 0) + inbound.get("down", 0)
            
            total_traffic_gb = round(total_traffic_bytes / (1024**3), 2)
            
            return {
                'total_users': total_users,
                'today_traffic': 0,
                'total_traffic': total_traffic_gb,
                'server_status': 'فعال',
                'total_inbounds': len(inbounds)
            }
        except Exception as e:
            logging.error(f"Error getting stats: {e}")
            return {
                'total_users': 0,
                'today_traffic': 0,
                'total_traffic': 0,
                'server_status': 'خطا در دریافت',
                'error': str(e)
            }

    async def close(self):
        """بستن session"""
        if self.session:
            await self.session.close()
            self.session = None
