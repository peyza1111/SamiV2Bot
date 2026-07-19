import json
import logging
import time
import uuid
from urllib.parse import quote, urlencode, urljoin

import requests
from src.core.config import settings


class XUIApi:
    def __init__(self, panel_url, username, password):
        self.base_url = panel_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.username = username
        self.password = password
        self.xray_config = None

    def _build_url(self, *parts):
        path = "/".join(map(str, parts))
        return urljoin(self.base_url + "/", path)

    def _make_request(self, method, url, **kwargs):
        try:
            r = self.session.request(method, url, timeout=10, **kwargs)
            r.raise_for_status()
            if not r.text:
                return {"success": True}
            return r.json()
        except requests.exceptions.JSONDecodeError:
            raise ConnectionError(
                f"Failed to decode JSON. Server response (status {r.status_code}):\n{r.text}"
            )
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Request failed: {e}")

    def login(self):
        login_url = self._build_url("login")
        payload = {"username": self.username, "password": self.password}
        response = self._make_request("post", login_url, data=payload)
        if not response.get("success"):
            raise ConnectionError(f"Login failed: {response.get('msg')}")
        return True

    def _get_xray_config(self):
        url = self._build_url("panel/xray/")
        response = self._make_request("post", url)
        if not response.get("success"):
            raise RuntimeError(f"Failed to get Xray config: {response.get('msg')}")
        self.xray_config = json.loads(response["obj"])["xraySetting"]
        return self.xray_config

    def _update_xray_config(self):
        if not self.xray_config:
            raise ValueError("Xray config is not loaded.")

        url = self._build_url("panel/xray/update")
        payload = {"xraySetting": json.dumps(self.xray_config, indent=2)}
        response = self._make_request("post", url, data=payload)
        if not response.get("success"):
            raise RuntimeError(f"Failed to update Xray config: {response.get('msg')}")
        return True

    def is_profile_exists(self, remark, inbound_id):
        client_remark_to_check = f"user-{remark.lower().replace(' ', '-')[:20]}"
        try:
            inbound_data = self.get_inbound(inbound_id)
            clients = json.loads(inbound_data.get("settings", "{}")).get("clients", [])
            return any(
                client.get("email") == client_remark_to_check for client in clients
            )
        except ValueError:
            return False

    def add_outbound(self, tag, address, port, user, password):
        config = self._get_xray_config()
        new_outbound = {
            "tag": tag,
            "protocol": "socks",
            "settings": {
                "servers": [
                    {
                        "address": address,
                        "port": int(port),
                        "users": [{"user": user, "pass": password}],
                    }
                ]
            },
        }
        config["outbounds"].append(new_outbound)
        return self._update_xray_config()

    def get_inbound(self, inbound_id):
        url = self._build_url("panel/api/inbounds/list")
        response = self._make_request("get", url)
        if not response.get("success"):
            raise RuntimeError(f"Failed to get inbounds list: {response.get('msg')}")
        for inbound in response.get("obj", []):
            if inbound.get("id") == inbound_id:
                return inbound
        raise ValueError(f"Inbound with ID {inbound_id} not found.")

    def add_client_to_inbound(
        self, inbound_id, client_remark, total_gb=0, expiry_days=0, flow=""
    ):
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
        response = self._make_request("post", url, data=payload)
        if not response.get("success"):
            raise RuntimeError(f"Failed to add client: {response.get('msg')}")
        return new_uuid

    def add_routing_rule(self, user_remark, outbound_tag, inbound_id):
        config = self._get_xray_config()
        inbound_data = self.get_inbound(inbound_id)
        inbound_tag = inbound_data.get("tag")
        if not inbound_tag:
            raise ValueError(f"Could not find inbound tag for ID '{inbound_id}'")
        new_rule = {
            "type": "field",
            "inboundTag": [inbound_tag],
            "outboundTag": outbound_tag,
            "user": [user_remark],
        }

        if len(config["routing"]["rules"]) > 2:
            config["routing"]["rules"].insert(-2, new_rule)
        else:
            config["routing"]["rules"].append(new_rule)
        return self._update_xray_config()

    def restart_xray(self):
        try:
            url = self._build_url("panel/setting/restartPanel")
            response = self._make_request("post", url)
        except ConnectionError:
            url = self._build_url("xui/setting/restartPanel")
            response = self._make_request("post", url)
        return response.get("success")

    def get_vless_uri(self, inbound_id, client_uuid, remark, inbound_data=None):
        if not inbound_data:
            inbound_data = self.get_inbound(inbound_id)

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

    def get_profiles(self, inbound_id):
        config = self._get_xray_config()
        routing_rules = config.get("routing", {}).get("rules", [])
        rules_map = {
            rule["user"][0]: rule.get("outboundTag")
            for rule in routing_rules
            if rule.get("user") and isinstance(rule.get("user"), list) and rule["user"]
        }

        inbound_data = self.get_inbound(inbound_id)
        clients = json.loads(inbound_data.get("settings", "{}")).get("clients", [])

        profiles = []
        for client in clients:
            client_remark = client.get("email")
            if client_remark and client_remark.startswith("user-"):
                outbound_tag = rules_map.get(client_remark)

                if outbound_tag:
                    profile_id = client_remark.replace("user-", "", 1)
                    remark = profile_id.replace("-", " ")
                    profiles.append(
                        {
                            "remark": remark.capitalize(),
                            "client_remark": client_remark,
                            "outbound_tag": outbound_tag,
                            "profile_id": profile_id,
                        }
                    )
        return profiles

    def delete_profile(
        self, client_remark_to_delete, outbound_tag_to_delete, inbound_id
    ):
        inbound_data = self.get_inbound(inbound_id)
        clients = json.loads(inbound_data.get("settings", "{}")).get("clients", [])

        client_uuid_to_delete = next(
            (c.get("id") for c in clients if c.get("email") == client_remark_to_delete),
            None,
        )

        if client_uuid_to_delete:
            del_client_url = self._build_url(
                "panel/api/inbounds", inbound_id, "delClient", client_uuid_to_delete
            )
            self._make_request("post", del_client_url)
        else:
            logging.warning(
                f"Client with remark '{client_remark_to_delete}' not found in inbound."
            )

        config = self._get_xray_config()
        config["routing"]["rules"] = [
            rule
            for rule in config["routing"]["rules"]
            if not (
                rule.get("user")
                and isinstance(rule.get("user"), list)
                and rule["user"]
                and rule["user"][0] == client_remark_to_delete
            )
        ]

        if outbound_tag_to_delete != "direct":
            config["outbounds"] = [
                outbound
                for outbound in config["outbounds"]
                if outbound.get("tag") != outbound_tag_to_delete
            ]

        self._update_xray_config()
        return True

    def get_stats(self):
        """دریافت آمار کلی پنل (همزمان)"""
        try:
            # دریافت لیست اینباندها برای محاسبه آمار
            url = self._build_url("panel/api/inbounds/list")
            response = self._make_request("get", url)
            
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
                
                # جمع ترافیک کل از تمام اینباندها
                total_traffic_bytes += inbound.get("up", 0) + inbound.get("down", 0)
            
            # تبدیل بایت به گیگابایت
            total_traffic_gb = round(total_traffic_bytes / (1024**3), 2)
            
            return {
                'total_users': total_users,
                'today_traffic': 0,  # در این API جداگانه موجود نیست
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

    def create_vless_user(self, name, limit_gb=0, expiry_date=None, inbound_id=None):
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
            client_uuid = self.add_client_to_inbound(
                inbound_id=inbound_id,
                client_remark=client_remark,
                total_gb=limit_gb,
                expiry_days=expiry_days,
                flow="xtls-rprx-vision-udp443"
            )
            
            # دریافت لینک کانفیگ
            config_link = self.get_vless_uri(
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

    def delete_user(self, user_id):
        """حذف کاربر با شناسه"""
        try:
            # پیدا کردن کاربر در اینباندها
            url = self._build_url("panel/api/inbounds/list")
            response = self._make_request("get", url)
            
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
                        del_response = self._make_request("post", del_url)
                        if del_response.get('success'):
                            return {'success': True, 'message': 'کاربر با موفقیت حذف شد'}
                        else:
                            return {'success': False, 'message': del_response.get('msg', 'خطا در حذف')}
            
            return {'success': False, 'message': 'کاربر یافت نشد'}
        except Exception as e:
            logging.error(f"Error deleting user: {e}")
            return {'success': False, 'message': str(e)}

    def get_users(self):
        """دریافت لیست کامل کاربران"""
        try:
            url = self._build_url("panel/api/inbounds/list")
            response = self._make_request("get", url)
            
            if not response.get("success"):
                return []
            
            users = []
            for inbound in response.get("obj", []):
                settings = json.loads(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                
                for client in clients:
                    # محاسبه حجم مصرفی و باقی‌مانده
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
