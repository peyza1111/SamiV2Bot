import json
import logging
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

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

    async def _login_panel(self):
        """ورود به پنل myx"""
        try:
            login_url = f"{self.base_url}/login"
            
            async with aiohttp.ClientSession() as session:
                # ابتدا صفحه لاگین را دریافت کن
                async with session.get(login_url) as response:
                    if response.status == 200:
                        # حالا لاگین را انجام بده
                        payload = {
                            "username": self.username,
                            "password": self.password
                        }
                        
                        async with session.post(login_url, data=payload) as login_response:
                            self.cookies = login_response.cookies
                            
                            if login_response.status == 200 or login_response.status == 302:
                                self.logged_in = True
                                logging.info("Login successful")
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
            if not self.logged_in:
                await self._login_panel()
            
            if self.cookies:
                kwargs.setdefault('cookies', self.cookies)
            
            async with aiohttp.ClientSession(cookies=self.cookies) as session:
                async with session.request(method, url, timeout=15, **kwargs) as response:
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
        """دریافت اطلاعات اینباند (در پنل myx همیشه 1 است)"""
        try:
            # در پنل myx، اینباند پیش‌فرض همیشه ID=1 دارد
            if inbound_id == 1:
                # یک اینباند مجازی بساز
                return {
                    "id": 1,
                    "port": 443,
                    "remark": "VLESS",
                    "streamSettings": json.dumps({
                        "network": "ws",
                        "security": "tls",
                        "wsSettings": {
                            "path": "/"
                        }
                    }),
                    "settings": json.dumps({"clients": []})
                }
            
            raise ValueError(f"Inbound with ID {inbound_id} not found")
        except Exception as e:
            logging.error(f"Error getting inbound: {e}")
            raise

    async def add_client_to_inbound(self, inbound_id, client_remark, total_gb=0, expiry_days=0, flow=""):
        """اضافه کردن کاربر (روش مستقیم با API پنل myx)"""
        try:
            new_uuid = str(uuid.uuid4())
            
            # استفاده از API مستقیم پنل myx
            api_url = f"{self.base_url}/api/users"
            
            # محاسبه تاریخ انقضا
            expiry_date = None
            if expiry_days > 0:
                expiry_date = (datetime.now() + timedelta(days=expiry_days)).strftime("%Y-%m-%d")
            
            payload = {
                "username": client_remark.replace("user-", ""),
                "uuid": new_uuid,
                "limit": total_gb if total_gb > 0 else 0,
                "days": expiry_days if expiry_days > 0 else 0,
                "expiry": expiry_date
            }
            
            response = await self._make_request("post", api_url, json=payload)
            
            if response.get("success"):
                return new_uuid
            else:
                raise Exception("Failed to add user")
        except Exception as e:
            logging.error(f"Error adding client: {e}")
            raise

    async def get_vless_uri(self, inbound_id, client_uuid, remark, inbound_data=None):
        """ساخت لینک کانفیگ برای پنل myx"""
        # تنظیمات پنل myx
        server_address = settings.PUBLIC_HOST.replace("https://", "").replace("http://", "")
        port = 443
        path = "/b6e0f80e8273"  # مسیر پیش‌فرض پنل myx
        
        # پارامترهای لینک
        params = {
            "type": "ws",
            "security": "tls",
            "host": server_address,
            "sni": server_address,
            "fp": "chrome",
            "path": path
        }

        query_string = urlencode(params, quote_via=quote)
        
        uri = f"vless://{client_uuid}@{server_address}:{port}?{query_string}#{remark}"
        return uri

    async def create_vless_user(self, name, limit_gb=0, expiry_date=None, inbound_id=None):
        """ساخت کاربر جدید در پنل myx"""
        try:
            client_remark = f"user-{name.lower().replace(' ', '-')[:20]}"
            
            expiry_days = 0
            if expiry_date:
                try:
                    expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
                    delta = expiry_dt - datetime.now()
                    expiry_days = max(0, delta.days)
                except:
                    expiry_days = 0
            
            # اضافه کردن کاربر
            client_uuid = await self.add_client_to_inbound(
                inbound_id=1,
                client_remark=client_remark,
                total_gb=limit_gb,
                expiry_days=expiry_days,
                flow=""
            )
            
            # ساخت لینک
            config_link = await self.get_vless_uri(
                inbound_id=1,
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
            logging.error(f"Error creating user: {e}")
            return {
                'success': False,
                'message': str(e)
            }

    async def get_users(self):
        """دریافت لیست کاربران از پنل myx"""
        try:
            # استفاده از API مستقیم پنل myx
            api_url = f"{self.base_url}/api/users"
            response = await self._make_request("get", api_url)
            
            if response.get("success") and response.get("users"):
                users = []
                for user in response.get("users", []):
                    users.append({
                        'id': user.get('uuid'),
                        'name': user.get('username', ''),
                        'limit': user.get('limit', 'نامحدود'),
                        'expiry': user.get('expiry', 'نامحدود'),
                        'enable': user.get('active', True),
                        'inbound_id': 1
                    })
                return users
            
            # اگر API کار نکرد، از روش جایگزین استفاده کن
            return await self._get_users_from_web()
        except Exception as e:
            logging.error(f"Error getting users: {e}")
            return []

    async def _get_users_from_web(self):
        """دریافت لیست کاربران از صفحه وب پنل myx"""
        try:
            # این روش به HTML scraping نیاز دارد
            # فعلاً لیست خالی برگردان
            return []
        except:
            return []

    async def delete_user(self, user_id):
        """حذف کاربر از پنل myx"""
        try:
            api_url = f"{self.base_url}/api/users/{user_id}"
            response = await self._make_request("delete", api_url)
            
            if response.get("success"):
                return {'success': True, 'message': 'کاربر با موفقیت حذف شد'}
            else:
                return {'success': False, 'message': 'خطا در حذف کاربر'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    async def get_stats(self):
        """دریافت آمار از پنل myx"""
        try:
            users = await self.get_users()
            total_users = len(users)
            active_users = len([u for u in users if u.get('enable', True)])
            
            return {
                'total_users': total_users,
                'active_users': active_users,
                'inactive_users': total_users - active_users,
                'total_traffic': 0,
                'total_inbounds': 1,
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
