import json
import logging
import time
import uuid
from datetime import datetime
from urllib.parse import quote, urlencode, urljoin

import aiohttp
from src.core.config import settings


class XUIApi:
    def __init__(self, panel_url, username, password):
        self.base_url = panel_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = None
        self.cookies = None
        self.logged_in = False
        self.inbounds_cache = None

    def _build_url(self, *parts):
        path = "/".join(map(str, parts))
        return urljoin(self.base_url + "/", path)

    async def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            await self.login()
        return self.session

    async def _make_request(self, method, url, **kwargs):
        session = await self._ensure_session()
        
        if self.cookies:
            kwargs.setdefault('cookies', self.cookies)
        
        try:
            async with session.request(method, url, timeout=15, **kwargs) as response:
                if response.status in [401, 403] or "login" in str(response.url).lower():
                    await self.login()
                    if self.cookies:
                        kwargs['cookies'] = self.cookies
                    async with session.request(method, url, timeout=15, **kwargs) as retry_response:
                        return await self._handle_response(retry_response)
                
                return await self._handle_response(response)
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Request failed: {e}")

    async def _handle_response(self, response):
        try:
            text = await response.text()
            
            if "<!DOCTYPE" in text or "<html" in text:
                logging.warning(f"Received HTML instead of JSON. Status: {response.status}")
                if "login" in text.lower():
                    self.logged_in = False
                    return {"success": False, "msg": "Session expired, please login again"}
                return {"success": False, "msg": "Invalid response from panel"}
            
            if not text or text.strip() == "":
                return {"success": True}
            
            return json.loads(text)
        except json.JSONDecodeError:
            logging.error(f"Invalid JSON: {text[:200]}")
            return {"success": False, "msg": "Invalid JSON response"}

    async def login(self):
        """ورود به پنل myx با پشتیبانی از مسیرهای مختلف"""
        login_paths = ["login", "api/login", "panel/login", "xui/login"]
        
        if self.session is None:
            self.session = aiohttp.ClientSession()
        
        for path in login_paths:
            try:
                login_url = self._build_url(path)
                payload = {"username": self.username, "password": self.password}
                
                async with self.session.post(login_url, data=payload, timeout=10) as response:
                    if response.status == 200:
                        self.cookies = response.cookies
                        text = await response.text()
                        
                        if "dashboard" in text.lower() or "success" in text.lower() or "panel" in text.lower():
                            self.logged_in = True
                            logging.info(f"Login successful via {path}")
                            return True
            except:
                continue
        
        self.logged_in = False
        raise ConnectionError("Login failed with all paths")

    async def get_inbounds(self, force_refresh=False):
        """دریافت لیست تمام اینباندها با پشتیبانی از مسیرهای مختلف"""
        if self.inbounds_cache and not force_refresh:
            return self.inbounds_cache
        
        # مسیرهای مختلف API برای دریافت اینباندها
        api_paths = [
            "panel/api/inbounds/list",
            "xui/API/inbounds/list", 
            "api/inbounds/list",
            "panel/inbounds/list",
            "xui/inbounds/list"
        ]
        
        for path in api_paths:
            try:
                url = self._build_url(path)
                response = await self._make_request("get", url)
                
                if response.get("success") and response.get("obj"):
                    self.inbounds_cache = response.get("obj", [])
                    return self.inbounds_cache
            except:
                continue
        
        return []

    async def get_inbound(self, inbound_id):
        """دریافت اطلاعات یک اینباند خاص"""
        inbounds = await self.get_inbounds()
        for inbound in inbounds:
            if inbound.get("id") == inbound_id:
                return inbound
        
        # اگر اینباند پیدا نشد، با refresh دوباره تلاش کن
        inbounds = await self.get_inbounds(force_refresh=True)
        for inbound in inbounds:
            if inbound.get("id") == inbound_id:
                return inbound
        
        raise ValueError(f"Inbound with ID {inbound_id} not found")

    async def add_client_to_inbound(self, inbound_id, client_remark, total_gb=0, expiry_days=0, flow=""):
        """اضافه کردن کاربر جدید به اینباند"""
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
        
        # مسیرهای مختلف برای اضافه کردن کاربر
        api_paths = [
            "panel/api/inbounds/addClient",
            "xui/API/inbounds/addClient",
            "api/inbounds/addClient",
            "panel/inbounds/addClient",
            "xui/inbounds/addClient"
        ]
        
        for path in api_paths:
            try:
                url = self._build_url(path)
                response = await self._make_request("post", url, data=payload)
                
                if response.get("success"):
                    return new_uuid
            except:
                continue
        
        raise RuntimeError("Failed to add client to inbound")

    async def get_vless_uri(self, inbound_id, client_uuid, remark, inbound_data=None):
        """ساخت لینک کانفیگ VLESS"""
        if not inbound_data:
            inbound_data = await self.get_inbound(inbound_id)

        # دریافت تنظیمات استریم
        stream_settings = json.loads(inbound_data.get("streamSettings", "{}"))
        reality_settings = stream_settings.get("realitySettings", {})
        reality_advanced_settings = reality_settings.get("settings", reality_settings)
        ws_settings = stream_settings.get("wsSettings", {})

        server_address = settings.PUBLIC_HOST.replace("https://", "").replace("http://", "")
        port = inbound_data.get("port", 443)
        network_type = stream_settings.get("network", "ws")
        security = stream_settings.get("security", "tls")

        # پارامترهای Reality
        public_key = reality_advanced_settings.get("publicKey", "")
        fingerprint = reality_advanced_settings.get("fingerprint", "chrome")
        spider_x = reality_advanced_settings.get("spiderX", "")
        server_names = reality_settings.get("serverNames", [""])
        sni = server_names[0] if server_names else server_address
        short_ids = reality_settings.get("shortIds", [])
        short_id = short_ids[0] if short_ids else ""

        # ساخت پارامترها
        params = {
            "type": network_type,
            "security": security,
            "flow": "xtls-rprx-vision-udp443",
            "fp": fingerprint,
            "sni": sni,
        }
        
        if public_key:
            params["pbk"] = public_key
        if short_id:
            params["sid"] = short_id
        if spider_x:
            params["spx"] = spider_x
        
        # تنظیمات WebSocket
        if network_type == "ws":
            ws_path = ws_settings.get("path", "/")
            params["path"] = ws_path

        query_string = urlencode(params, quote_via=quote)

        inbound_remark = inbound_data.get("remark") or "VLESS"
        encoded_remark = quote(remark)
        uri_remark = f"{inbound_remark}-user-{encoded_remark}"

        uri = f"vless://{client_uuid}@{server_address}:{port}?{query_string}#{uri_remark}"
        return uri

    async def create_vless_user(self, name, limit_gb=0, expiry_date=None, inbound_id=None):
        """ساخت کاربر VLESS جدید با امکانات کامل"""
        try:
            if inbound_id is None:
                inbound_id = settings.VLESS_INBOUND_ID
            
            client_remark = f"user-{name.lower().replace(' ', '-')[:20]}"
            
            expiry_days = 0
            if expiry_date:
                try:
                    expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
                    delta = expiry_dt - datetime.now()
                    expiry_days = max(0, delta.days)
                except:
                    expiry_days = 0
            
            # دریافت اطلاعات اینباند
            inbound_data = await self.get_inbound(inbound_id)
            
            # اضافه کردن کاربر
            client_uuid = await self.add_client_to_inbound(
                inbound_id=inbound_id,
                client_remark=client_remark,
                total_gb=limit_gb,
                expiry_days=expiry_days,
                flow="xtls-rprx-vision-udp443"
            )
            
            # ساخت لینک کانفیگ
            config_link = await self.get_vless_uri(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                remark=name,
                inbound_data=inbound_data
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
            inbounds = await self.get_inbounds()
            users = []
            
            for inbound in inbounds:
                try:
                    settings_data = json.loads(inbound.get("settings", "{}"))
                    clients = settings_data.get("clients", [])
                    
                    for client in clients:
                        total_gb = client.get('totalGB', 0) / (1024**3) if client.get('totalGB', 0) > 0 else 0
                        expiry_time = client.get('expiryTime', 0)
                        expiry_date = None
                        if expiry_time > 0:
                            try:
                                expiry_date = datetime.fromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d")
                            except:
                                expiry_date = "نامحدود"
                        
                        users.append({
                            'id': client.get('id'),
                            'name': client.get('email', '').replace('user-', ''),
                            'limit': round(total_gb, 2) if total_gb > 0 else 'نامحدود',
                            'expiry': expiry_date if expiry_date else 'نامحدود',
                            'enable': client.get('enable', True),
                            'inbound_id': inbound.get('id')
                        })
                except Exception as e:
                    logging.warning(f"Error parsing inbound: {e}")
                    continue
            
            return users
        except Exception as e:
            logging.error(f"Error getting users: {e}")
            return []

    async def delete_user(self, user_id):
        """حذف کاربر با شناسه"""
        try:
            # پیدا کردن کاربر
            users = await self.get_users()
            target_user = None
            inbound_id = None
            
            for user in users:
                if str(user.get('id')) == str(user_id):
                    target_user = user
                    inbound_id = user.get('inbound_id')
                    break
            
            if not target_user:
                return {'success': False, 'message': 'کاربر یافت نشد'}
            
            # مسیرهای مختلف برای حذف کاربر
            api_paths = [
                f"panel/api/inbounds/{inbound_id}/delClient/{user_id}",
                f"xui/API/inbounds/{inbound_id}/delClient/{user_id}",
                f"api/inbounds/{inbound_id}/delClient/{user_id}",
                f"panel/inbounds/{inbound_id}/delClient/{user_id}"
            ]
            
            for path in api_paths:
                try:
                    url = self._build_url(path)
                    response = await self._make_request("post", url)
                    if response.get("success"):
                        self.inbounds_cache = None  # Clear cache
                        return {'success': True, 'message': 'کاربر با موفقیت حذف شد'}
                except:
                    continue
            
            return {'success': False, 'message': 'خطا در حذف کاربر'}
        except Exception as e:
            logging.error(f"Error deleting user: {e}")
            return {'success': False, 'message': str(e)}

    async def get_stats(self):
        """دریافت آمار کلی پنل"""
        try:
            users = await self.get_users()
            inbounds = await self.get_inbounds()
            
            total_users = len(users)
            active_users = len([u for u in users if u.get('enable', True)])
            
            total_traffic_bytes = 0
            for inbound in inbounds:
                total_traffic_bytes += inbound.get("up", 0) + inbound.get("down", 0)
            
            total_traffic_gb = round(total_traffic_bytes / (1024**3), 2)
            
            return {
                'total_users': total_users,
                'active_users': active_users,
                'inactive_users': total_users - active_users,
                'total_traffic': total_traffic_gb,
                'total_inbounds': len(inbounds),
                'server_status': 'فعال'
            }
        except Exception as e:
            logging.error(f"Error getting stats: {e}")
            return {
                'total_users': 0,
                'active_users': 0,
                'inactive_users': 0,
                'total_traffic': 0,
                'total_inbounds': 0,
                'server_status': 'خطا در دریافت',
                'error': str(e)
            }

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
