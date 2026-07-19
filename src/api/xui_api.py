import json
import logging
import time
import uuid
from datetime import datetime, timedelta
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

    def _build_url(self, *parts):
        """ساخت URL کامل"""
        path = "/".join(map(str, parts))
        return urljoin(self.base_url + "/", path)

    async def _ensure_session(self):
        """اطمینان از وجود session و لاگین"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
            await self.login()
        return self.session

    async def _make_request(self, method, url, **kwargs):
        """ارسال درخواست با مدیریت خودکار لاگین"""
        session = await self._ensure_session()
        
        # اضافه کردن کوکی‌ها در صورت وجود
        if self.cookies:
            kwargs.setdefault('cookies', self.cookies)
        
        try:
            async with session.request(method, url, timeout=15, **kwargs) as response:
                # اگر لاگین منقضی شده، دوباره لاگین کن
                if response.status == 401 or "login" in str(response.url):
                    await self.login()
                    # ارسال مجدد درخواست با کوکی جدید
                    if self.cookies:
                        kwargs['cookies'] = self.cookies
                    async with session.request(method, url, timeout=15, **kwargs) as retry_response:
                        return await self._handle_response(retry_response)
                
                return await self._handle_response(response)
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Request failed: {e}")

    async def _handle_response(self, response):
        """پردازش پاسخ دریافتی با تشخیص JSON یا HTML"""
        try:
            text = await response.text()
            
            # اگر پاسخ HTML است (خطا یا صفحه لاگین)
            if "<!DOCTYPE" in text or "<html" in text:
                logging.warning(f"Received HTML response instead of JSON. Status: {response.status}")
                if "login" in text.lower():
                    self.logged_in = False
                    raise ConnectionError("Session expired. Please login again.")
                return {"success": False, "msg": "Invalid response from panel"}
            
            # تلاش برای parse JSON
            if not text or text.strip() == "":
                return {"success": True}
            
            return json.loads(text)
        except json.JSONDecodeError:
            # اگر JSON نبود، خطا برگردان
            logging.error(f"Invalid JSON response: {text[:200]}")
            raise ConnectionError(f"Invalid JSON response: {text[:100]}...")

    async def login(self):
        """ورود به پنل myx"""
        try:
            login_url = self._build_url("login")
            payload = {"username": self.username, "password": self.password}
            
            if self.session is None:
                self.session = aiohttp.ClientSession()
            
            async with self.session.post(login_url, data=payload, timeout=10) as response:
                # دریافت کوکی‌ها
                self.cookies = response.cookies
                text = await response.text()
                
                # بررسی موفقیت لاگین
                if "dashboard" in text.lower() or response.status == 200:
                    self.logged_in = True
                    logging.info("Login successful")
                    return True
                else:
                    self.logged_in = False
                    raise ConnectionError(f"Login failed. Status: {response.status}")
        except Exception as e:
            self.logged_in = False
            raise ConnectionError(f"Login failed: {e}")

    async def get_inbound(self, inbound_id):
        """دریافت اطلاعات یک اینباند"""
        try:
            url = self._build_url("panel/api/inbounds/list")
            response = await self._make_request("get", url)
            
            if not response.get("success"):
                # اگر خطا داشت، سعی کنید با مسیر جایگزین
                url = self._build_url("xui/API/inbounds/list")
                response = await self._make_request("get", url)
            
            if response.get("success") and response.get("obj"):
                for inbound in response.get("obj", []):
                    if inbound.get("id") == inbound_id:
                        return inbound
                raise ValueError(f"Inbound with ID {inbound_id} not found.")
            else:
                raise RuntimeError(f"Failed to get inbounds: {response.get('msg', 'Unknown error')}")
        except Exception as e:
            logging.error(f"Error getting inbound: {e}")
            raise

    async def add_client_to_inbound(self, inbound_id, client_remark, total_gb=0, expiry_days=0, flow=""):
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
            # اگر مسیر اول کار نکرد، مسیر جایگزین را امتحان کن
            alt_url = self._build_url("xui/API/inbounds/addClient")
            response = await self._make_request("post", alt_url, data=payload)
            
        if response.get("success"):
            return new_uuid
        else:
            raise RuntimeError(f"Failed to add client: {response.get('msg', 'Unknown error')}")

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

        uri = f"vless://{client_uuid}@{server_address}:{port}?{query_string}#{uri_remark}"
        return uri

    async def create_vless_user(self, name, limit_gb=0, expiry_date=None, inbound_id=None):
        """ساخت کاربر VLESS جدید"""
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
            
            client_uuid = await self.add_client_to_inbound(
                inbound_id=inbound_id,
                client_remark=client_remark,
                total_gb=limit_gb,
                expiry_days=expiry_days,
                flow="xtls-rprx-vision-udp443"
            )
            
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
                # اگر مسیر اول کار نکرد، مسیر جایگزین را امتحان کن
                url = self._build_url("xui/API/inbounds/list")
                response = await self._make_request("get", url)
            
            if not response.get("success") or not response.get("obj"):
                logging.warning("No users found or failed to fetch")
                return []
            
            users = []
            for inbound in response.get("obj", []):
                try:
                    settings = json.loads(inbound.get("settings", "{}"))
                    clients = settings.get("clients", [])
                    
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
            # ابتدا کاربر را پیدا کن
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
            
            # حذف کاربر از اینباند
            del_url = self._build_url("panel/api/inbounds", inbound_id, "delClient", user_id)
            response = await self._make_request("post", del_url)
            
            if not response.get("success"):
                # مسیر جایگزین
                alt_url = self._build_url("xui/API/inbounds", inbound_id, "delClient", user_id)
                response = await self._make_request("post", alt_url)
            
            if response.get("success"):
                return {'success': True, 'message': 'کاربر با موفقیت حذف شد'}
            else:
                return {'success': False, 'message': response.get('msg', 'خطا در حذف')}
        except Exception as e:
            logging.error(f"Error deleting user: {e}")
            return {'success': False, 'message': str(e)}

    async def get_stats(self):
        """دریافت آمار کلی پنل"""
        try:
            users = await self.get_users()
            
            # محاسبه آمار از لیست کاربران
            total_users = len(users)
            active_users = len([u for u in users if u.get('enable', True)])
            
            # دریافت اطلاعات بیشتر از اینباندها
            url = self._build_url("panel/api/inbounds/list")
            response = await self._make_request("get", url)
            
            if not response.get("success"):
                url = self._build_url("xui/API/inbounds/list")
                response = await self._make_request("get", url)
            
            total_traffic_bytes = 0
            inbound_count = 0
            
            if response.get("success") and response.get("obj"):
                for inbound in response.get("obj", []):
                    total_traffic_bytes += inbound.get("up", 0) + inbound.get("down", 0)
                    inbound_count += 1
            
            total_traffic_gb = round(total_traffic_bytes / (1024**3), 2)
            
            return {
                'total_users': total_users,
                'active_users': active_users,
                'inactive_users': total_users - active_users,
                'today_traffic': 0,  # محاسبه دقیق نیاز به API جداگانه دارد
                'total_traffic': total_traffic_gb,
                'server_status': 'فعال',
                'total_inbounds': inbound_count
            }
        except Exception as e:
            logging.error(f"Error getting stats: {e}")
            return {
                'total_users': 0,
                'active_users': 0,
                'inactive_users': 0,
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
