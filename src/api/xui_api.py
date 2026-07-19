import json
import logging
import time
import uuid
from datetime import datetime
from urllib.parse import quote, urlencode

import aiohttp
import requests
from src.core.config import settings


class XUIApi:
    def __init__(self, panel_url, username, password):
        self.base_url = panel_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = None
        self.cookies = None
        self.logged_in = False

    async def _login_panel(self):
        """ورود به پنل myx با روش ساده"""
        try:
            # روش ساده: ارسال درخواست به صفحه لاگین
            login_url = f"{self.base_url}/login"
            
            async with aiohttp.ClientSession() as session:
                # ابتدا صفحه لاگین رو دریافت کن
                async with session.get(login_url) as response:
                    if response.status == 200:
                        # حالا لاگین رو انجام بده
                        payload = {
                            "username": self.username,
                            "password": self.password,
                            "login": "login"  # بعضی پنل‌ها نیاز دارن
                        }
                        
                        async with session.post(login_url, data=payload) as login_response:
                            self.cookies = login_response.cookies
                            
                            if login_response.status == 200 or login_response.status == 302:
                                self.logged_in = True
                                logging.info("Login successful")
                                return True
                            
            # اگر روش اول کار نکرد، روش دوم
            async with aiohttp.ClientSession() as session:
                payload = {"username": self.username, "password": self.password}
                async with session.post(login_url, json=payload) as response:
                    if response.status == 200:
                        self.cookies = response.cookies
                        self.logged_in = True
                        logging.info("Login successful (JSON)")
                        return True
            
            raise Exception("Login failed")
        except Exception as e:
            logging.error(f"Login error: {e}")
            raise

    async def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            await self._login_panel()
        return self.session

    async def _make_request(self, method, url, **kwargs):
        """ارسال درخواست با مدیریت خودکار لاگین"""
        try:
            # اگر لاگین نکردیم، لاگین کن
            if not self.logged_in:
                await self._login_panel()
            
            if self.cookies:
                kwargs.setdefault('cookies', self.cookies)
            
            async with aiohttp.ClientSession(cookies=self.cookies) as session:
                async with session.request(method, url, timeout=15, **kwargs) as response:
                    # اگر لاگین منقضی شده بود
                    if response.status == 401:
                        await self._login_panel()
                        if self.cookies:
                            kwargs['cookies'] = self.cookies
                        async with aiohttp.ClientSession(cookies=self.cookies) as retry_session:
                            async with retry_session.request(method, url, timeout=15, **kwargs) as retry_response:
                                return await self._handle_response(retry_response)
                    
                    return await self._handle_response(response)
        except Exception as e:
            logging.error(f"Request error: {e}")
            raise

    async def _handle_response(self, response):
        try:
            text = await response.text()
            
            if "<!DOCTYPE" in text or "<html" in text:
                # اگر HTML برگشت، یعنی خطا یا لاگین
                if "login" in text.lower():
                    self.logged_in = False
                    return {"success": False, "msg": "Please login"}
                return {"success": False, "msg": "Invalid response"}
            
            if not text or text.strip() == "":
                return {"success": True}
            
            return json.loads(text)
        except:
            return {"success": False, "msg": "Invalid JSON"}

    async def get_inbound(self, inbound_id):
        """دریافت اطلاعات اینباند"""
        try:
            # مسیرهای مختلف API
            api_paths = [
                f"{self.base_url}/api/inbounds/list",
                f"{self.base_url}/panel/api/inbounds/list",
                f"{self.base_url}/xui/API/inbounds/list"
            ]
            
            for url in api_paths:
                try:
                    response = await self._make_request("get", url)
                    if response.get("success") and response.get("obj"):
                        for inbound in response.get("obj", []):
                            if inbound.get("id") == inbound_id:
                                return inbound
                except:
                    continue
            
            raise ValueError(f"Inbound with ID {inbound_id} not found")
        except Exception as e:
            logging.error(f"Error getting inbound: {e}")
            raise

    async def add_client_to_inbound(self, inbound_id, client_remark, total_gb=0, expiry_days=0, flow=""):
        """اضافه کردن کاربر"""
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
        
        # مسیرهای مختلف
        api_paths = [
            f"{self.base_url}/api/inbounds/addClient",
            f"{self.base_url}/panel/api/inbounds/addClient",
            f"{self.base_url}/xui/API/inbounds/addClient"
        ]
        
        for url in api_paths:
            try:
                response = await self._make_request("post", url, data=payload)
                if response.get("success"):
                    return new_uuid
            except:
                continue
        
        raise Exception("Failed to add client")

    async def get_vless_uri(self, inbound_id, client_uuid, remark, inbound_data=None):
        """ساخت لینک کانفیگ"""
        if not inbound_data:
            inbound_data = await self.get_inbound(inbound_id)

        server_address = settings.PUBLIC_HOST.replace("https://", "").replace("http://", "")
        port = inbound_data.get("port", 443)
        
        stream_settings = json.loads(inbound_data.get("streamSettings", "{}"))
        network_type = stream_settings.get("network", "ws")
        security = stream_settings.get("security", "tls")
        ws_settings = stream_settings.get("wsSettings", {})
        path = ws_settings.get("path", "/")
        
        # پارامترها
        params = {
            "type": network_type,
            "security": security,
            "sni": server_address,
            "flow": "xtls-rprx-vision-udp443",
            "fp": "chrome",
        }
        
        if network_type == "ws":
            params["path"] = path

        query_string = urlencode(params, quote_via=quote)
        
        uri = f"vless://{client_uuid}@{server_address}:{port}?{query_string}#{remark}"
        return uri

    async def create_vless_user(self, name, limit_gb=0, expiry_date=None, inbound_id=None):
        """ساخت کاربر جدید"""
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
            
            # ساخت لینک
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
            logging.error(f"Error creating user: {e}")
            return {
                'success': False,
                'message': str(e)
            }

    async def get_users(self):
        """دریافت لیست کاربران"""
        try:
            api_paths = [
                f"{self.base_url}/api/inbounds/list",
                f"{self.base_url}/panel/api/inbounds/list",
                f"{self.base_url}/xui/API/inbounds/list"
            ]
            
            users = []
            for url in api_paths:
                try:
                    response = await self._make_request("get", url)
                    if response.get("success") and response.get("obj"):
                        for inbound in response.get("obj", []):
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
                        if users:
                            return users
                except:
                    continue
            
            return users
        except Exception as e:
            logging.error(f"Error getting users: {e}")
            return []

    async def delete_user(self, user_id):
        """حذف کاربر"""
        try:
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
            
            api_paths = [
                f"{self.base_url}/api/inbounds/{inbound_id}/delClient/{user_id}",
                f"{self.base_url}/panel/api/inbounds/{inbound_id}/delClient/{user_id}",
                f"{self.base_url}/xui/API/inbounds/{inbound_id}/delClient/{user_id}"
            ]
            
            for url in api_paths:
                try:
                    response = await self._make_request("post", url)
                    if response.get("success"):
                        return {'success': True, 'message': 'کاربر با موفقیت حذف شد'}
                except:
                    continue
            
            return {'success': False, 'message': 'خطا در حذف کاربر'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    async def get_stats(self):
        """دریافت آمار"""
        try:
            users = await self.get_users()
            total_users = len(users)
            active_users = len([u for u in users if u.get('enable', True)])
            
            return {
                'total_users': total_users,
                'active_users': active_users,
                'inactive_users': total_users - active_users,
                'total_traffic': 0,
                'total_inbounds': 0,
                'server_status': 'فعال'
            }
        except Exception as e:
            return {
                'total_users': 0,
                'active_users': 0,
                'inactive_users': 0,
                'total_traffic': 0,
                'total_inbounds': 0,
                'server_status': 'خطا در دریافت'
            }

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
