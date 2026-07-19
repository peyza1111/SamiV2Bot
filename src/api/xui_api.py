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
        self.password = password
        self.session = None
        self.cookies = None
        self.logged_in = False

    async def _login_panel(self):
        """ورود به پنل myx با روش ساده و گرفتن کوکی"""
        try:
            # مرحله 1: دریافت صفحه لاگین برای گرفتن کوکی اولیه
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/login") as response:
                    if response.status == 200:
                        # کوکی‌های اولیه را ذخیره کن
                        self.cookies = response.cookies
                        
                        # مرحله 2: ارسال رمز عبور با کوکی
                        payload = {"password": self.password}
                        async with session.post(f"{self.base_url}/login", data=payload, cookies=self.cookies) as login_response:
                            # کوکی جدید را ذخیره کن
                            self.cookies = login_response.cookies
                            
                            if login_response.status in [200, 302]:
                                self.logged_in = True
                                logging.info("✅ Login successful")
                                return True
                            
                            # اگر POST کار نکرد، با GET به داشبورد برو
                            async with session.get(f"{self.base_url}/dashboard", cookies=self.cookies) as dash_response:
                                if dash_response.status == 200:
                                    self.cookies = dash_response.cookies
                                    self.logged_in = True
                                    logging.info("✅ Login successful via dashboard")
                                    return True
            
            raise Exception("❌ Login failed")
        except Exception as e:
            logging.error(f"❌ Login error: {e}")
            raise

    async def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            await self._login_panel()
        return self.session

    async def _make_request(self, method, url, **kwargs):
        try:
            if not self.logged_in:
                await self._login_panel()
            
            if self.cookies:
                kwargs.setdefault('cookies', self.cookies)
            
            async with aiohttp.ClientSession(cookies=self.cookies) as session:
                async with session.request(method, url, timeout=15, **kwargs) as response:
                    if response.status in [401, 403]:
                        logging.warning("⚠️ Session expired, logging in again...")
                        await self._login_panel()
                        if self.cookies:
                            kwargs['cookies'] = self.cookies
                        async with aiohttp.ClientSession(cookies=self.cookies) as retry_session:
                            async with retry_session.request(method, url, timeout=15, **kwargs) as retry_response:
                                return await self._handle_response(retry_response)
                    
                    return await self._handle_response(response)
        except Exception as e:
            logging.error(f"❌ Request error: {e}")
            raise

    async def _handle_response(self, response):
        try:
            text = await response.text()
            
            # اگر پاسخ HTML باشد (معمولاً خطا)
            if "<!DOCTYPE" in text or "<html" in text:
                if "login" in text.lower():
                    self.logged_in = False
                    return {"success": False, "msg": "Please login"}
                return {"success": False, "msg": "Invalid response"}
            
            # اگر پاسخ خالی باشد
            if not text or text.strip() == "":
                return {"success": True}
            
            # تلاش برای parse JSON
            return json.loads(text)
        except json.JSONDecodeError:
            logging.warning(f"⚠️ Invalid JSON response: {text[:100]}")
            return {"success": False, "msg": "Invalid JSON"}
        except Exception as e:
            logging.error(f"❌ Response error: {e}")
            return {"success": False, "msg": str(e)}

    async def create_vless_user(self, name, limit_gb=0, expiry_date=None, inbound_id=None):
        """ساخت کاربر جدید با روش مستقیم"""
        try:
            # لاگین مجدد برای اطمینان از معتبر بودن کوکی
            await self._login_panel()
            
            # ساخت UUID جدید
            new_uuid = str(uuid.uuid4())
            
            # آماده‌سازی payload برای API
            api_url = f"{self.base_url}/api/users"
            
            payload = {
                "username": name,
                "uuid": new_uuid,
                "limit": limit_gb if limit_gb > 0 else 0,
                "days": 2 if expiry_date else 0,
                "expiry": expiry_date if expiry_date else None
            }
            
            logging.info(f"📤 Sending request to {api_url}")
            logging.info(f"📦 Payload: {payload}")
            
            # ارسال درخواست با کوکی
            async with aiohttp.ClientSession(cookies=self.cookies) as session:
                async with session.post(api_url, json=payload, timeout=15) as response:
                    if response.status == 200:
                        try:
                            result = await response.json()
                            logging.info(f"✅ Response: {result}")
                            
                            if result.get('success'):
                                # ساخت لینک کانفیگ
                                server_address = settings.PUBLIC_HOST.replace("https://", "").replace("http://", "")
                                path = "/b6e0f80e8273"  # مسیر پیش‌فرض پنل myx
                                
                                params = {
                                    "type": "ws",
                                    "security": "tls",
                                    "host": server_address,
                                    "sni": server_address,
                                    "fp": "chrome",
                                    "path": path
                                }

                                query_string = urlencode(params, quote_via=quote)
                                config_link = f"vless://{new_uuid}@{server_address}:443?{query_string}#{name}"
                                
                                return {
                                    'success': True,
                                    'uuid': new_uuid,
                                    'link': config_link,
                                    'name': name,
                                    'limit_gb': limit_gb,
                                    'expiry_date': expiry_date
                                }
                            else:
                                return {
                                    'success': False,
                                    'message': result.get('message', 'خطا در ساخت کاربر')
                                }
                        except json.JSONDecodeError:
                            logging.error("❌ Invalid JSON response from panel")
                            return {
                                'success': False,
                                'message': 'پاسخ نامعتبر از پنل'
                            }
                    elif response.status == 401:
                        logging.warning("⚠️ Session expired, retrying...")
                        await self._login_panel()
                        return await self.create_vless_user(name, limit_gb, expiry_date)
                    else:
                        error_text = await response.text()
                        logging.error(f"❌ HTTP Error {response.status}: {error_text}")
                        return {
                            'success': False,
                            'message': f'خطای HTTP {response.status}'
                        }
            
            return {
                'success': False,
                'message': 'خطا در ارتباط با پنل'
            }
        except Exception as e:
            logging.error(f"❌ Error creating user: {e}")
            return {
                'success': False,
                'message': str(e)
            }

    async def get_users(self):
        """دریافت لیست کاربران"""
        try:
            await self._login_panel()
            
            api_url = f"{self.base_url}/api/users"
            async with aiohttp.ClientSession(cookies=self.cookies) as session:
                async with session.get(api_url, timeout=15) as response:
                    if response.status == 200:
                        try:
                            result = await response.json()
                            if result.get('success') and result.get('users'):
                                users = []
                                for user in result.get('users', []):
                                    users.append({
                                        'id': user.get('uuid'),
                                        'name': user.get('username', ''),
                                        'limit': user.get('limit', 'نامحدود'),
                                        'expiry': user.get('expiry', 'نامحدود'),
                                        'enable': user.get('active', True),
                                        'inbound_id': 1
                                    })
                                return users
                        except json.JSONDecodeError:
                            logging.error("❌ Invalid JSON response")
                    elif response.status == 401:
                        logging.warning("⚠️ Session expired, retrying...")
                        await self._login_panel()
                        return await self.get_users()
            
            return []
        except Exception as e:
            logging.error(f"❌ Error getting users: {e}")
            return []

    async def delete_user(self, user_id):
        """حذف کاربر"""
        try:
            await self._login_panel()
            
            api_url = f"{self.base_url}/api/users/{user_id}"
            async with aiohttp.ClientSession(cookies=self.cookies) as session:
                async with session.delete(api_url, timeout=15) as response:
                    if response.status == 200:
                        try:
                            result = await response.json()
                            if result.get('success'):
                                return {'success': True, 'message': 'کاربر با موفقیت حذف شد'}
                        except json.JSONDecodeError:
                            pass
                    elif response.status == 401:
                        logging.warning("⚠️ Session expired, retrying...")
                        await self._login_panel()
                        return await self.delete_user(user_id)
            
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
