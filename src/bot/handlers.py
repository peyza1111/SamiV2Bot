import re
from datetime import datetime, timedelta
from aiogram import Router, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from src.api.xui_api import XUIApi
from src.core.config import settings

router = Router()

# ========== تعریف وضعیت‌ها ==========
class LoginState(StatesGroup):
    waiting_for_password = State()

class UserCreation(StatesGroup):
    waiting_for_name = State()
    waiting_for_limit = State()
    waiting_for_days = State()

class UserDeletion(StatesGroup):
    waiting_for_user_id = State()

# ========== ذخیره وضعیت لاگین کاربران ==========
user_sessions = {}  # {user_id: logged_in}

# ========== منوهای مختلف ==========
def get_login_menu():
    """منوی لاگین"""
    keyboard = [
        [KeyboardButton(text="🔑 ورود به پنل")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_main_menu():
    """منوی اصلی بعد از لاگین"""
    keyboard = [
        [KeyboardButton(text="➕ ساخت کانفیگ جدید")],
        [KeyboardButton(text="📋 لیست کاربران")],
        [KeyboardButton(text="❌ حذف کاربر")],
        [KeyboardButton(text="📊 آمار پنل")],
        [KeyboardButton(text="🔐 خروج از پنل")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_users_inline_menu(users):
    """منوی اینلاین برای لیست کاربران"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    for user in users:
        user_name = user.get('name', 'بی‌نام')
        user_id = user.get('id')
        if user_id:
            keyboard.add(
                InlineKeyboardButton(
                    text=f"🗑️ حذف {user_name}",
                    callback_data=f"delete_user_{user_id}"
                )
            )
    keyboard.add(InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="back_to_menu"))
    return keyboard

# ========== دستور /start ==========
@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    if user_sessions.get(user_id):
        await message.answer(
            "👋 به ربات مدیریت پنل خوش آمدید!\n\n"
            "شما قبلاً وارد شده‌اید. از منوی زیر استفاده کنید:",
            reply_markup=get_main_menu()
        )
    else:
        await message.answer(
            "👋 به ربات مدیریت پنل خوش آمدید!\n\n"
            "لطفاً برای دسترسی به پنل، روی دکمه زیر کلیک کنید:",
            reply_markup=get_login_menu()
        )

# ========== ورود به پنل ==========
@router.message(F.text == "🔑 ورود به پنل")
async def start_login(message: types.Message, state: FSMContext):
    await state.set_state(LoginState.waiting_for_password)
    await message.answer(
        "🔑 **لطفاً رمز عبور پنل را وارد کنید:**\n\n"
        "⚠️ این پنل فقط با رمز عبور کار می‌کند.",
        parse_mode="Markdown"
    )

@router.message(LoginState.waiting_for_password, F.text)
async def process_login(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    entered_password = message.text.strip()
    
    # بررسی رمز عبور
    if entered_password == settings.PANEL_PASSWORD:
        user_sessions[user_id] = True
        await message.answer(
            "✅ **ورود موفق!**\n\n"
            "شما اکنون به پنل دسترسی دارید. از منوی زیر استفاده کنید:",
            parse_mode="Markdown",
            reply_markup=get_main_menu()
        )
    else:
        await message.answer(
            "❌ **رمز عبور اشتباه است!**\n\n"
            "لطفاً دوباره تلاش کنید یا /cancel را بزنید.",
            parse_mode="Markdown"
        )
    await state.clear()

# ========== خروج از پنل ==========
@router.message(F.text == "🔐 خروج از پنل")
async def cmd_logout(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
    await message.answer(
        "👋 **شما از پنل خارج شدید.**\n\n"
        "برای ورود مجدد، روی دکمه «🔑 ورود به پنل» کلیک کنید.",
        parse_mode="Markdown",
        reply_markup=get_login_menu()
    )

# ========== بررسی لاگین بودن کاربر ==========
async def is_logged_in(message: types.Message):
    user_id = message.from_user.id
    if not user_sessions.get(user_id):
        await message.answer(
            "❌ **شما وارد نشده‌اید!**\n\n"
            "لطفاً ابتدا روی دکمه «🔑 ورود به پنل» کلیک کرده و رمز عبور را وارد کنید.",
            parse_mode="Markdown",
            reply_markup=get_login_menu()
        )
        return False
    return True

# ========== دستور /help ==========
@router.message(Command("help"))
async def cmd_help(message: types.Message):
    if not await is_logged_in(message):
        return
    
    help_text = (
        "📖 **راهنمای کامل ربات**\n\n"
        "✅ **ساخت کانفیگ جدید:**\n"
        "روی دکمه «➕ ساخت کانفیگ جدید» کلیک کنید و سپس مراحل را دنبال کنید.\n\n"
        "✅ **مشاهده لیست کاربران:**\n"
        "روی دکمه «📋 لیست کاربران» کلیک کنید.\n\n"
        "✅ **حذف کاربر:**\n"
        "روی دکمه «❌ حذف کاربر» کلیک کنید و شناسه کاربر را وارد کنید.\n\n"
        "✅ **آمار پنل:**\n"
        "برای مشاهده وضعیت کلی پنل، روی دکمه «📊 آمار پنل» کلیک کنید.\n\n"
        "✅ **خروج از پنل:**\n"
        "روی دکمه «🔐 خروج از پنل» کلیک کنید."
    )
    await message.answer(help_text, parse_mode="Markdown", reply_markup=get_main_menu())

# ========== ساخت کانفیگ جدید ==========
@router.message(F.text == "➕ ساخت کانفیگ جدید")
async def start_vless_creation(message: types.Message, state: FSMContext):
    if not await is_logged_in(message):
        return
    await state.set_state(UserCreation.waiting_for_name)
    await message.answer(
        "📝 **لطفاً نام کاربر را وارد کنید:**\n\n"
        "نام باید فقط شامل حروف، اعداد و خط تیره باشد.",
        parse_mode="Markdown"
    )

@router.message(UserCreation.waiting_for_name, F.text)
async def process_vless_name(message: types.Message, state: FSMContext):
    if not await is_logged_in(message):
        await state.clear()
        return
    
    name = message.text.strip()
    if not re.match(r'^[a-zA-Z0-9\-_]+$', name):
        await message.answer("❌ نام نامعتبر است! فقط از حروف، اعداد و خط تیره استفاده کنید.")
        return
    await state.update_data(name=name)
    await state.set_state(UserCreation.waiting_for_limit)
    await message.answer(
        "📊 **محدودیت حجم (به گیگابایت) را وارد کنید:**\n\n"
        "مثال: `50` (برای ۵۰ گیگ) یا `0` (برای بدون محدودیت)",
        parse_mode="Markdown"
    )

@router.message(UserCreation.waiting_for_limit, F.text)
async def process_vless_limit(message: types.Message, state: FSMContext):
    if not await is_logged_in(message):
        await state.clear()
        return
    
    try:
        limit = int(message.text)
        if limit < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد معتبر (۰ یا بیشتر) وارد کنید.")
        return
    await state.update_data(limit=limit)
    await state.set_state(UserCreation.waiting_for_days)
    await message.answer(
        "📅 **مدت زمان (به روز) را وارد کنید:**\n\n"
        "مثال: `30` (برای ۳۰ روز) یا `0` (برای بدون محدودیت زمانی)",
        parse_mode="Markdown"
    )

@router.message(UserCreation.waiting_for_days, F.text)
async def process_vless_days(message: types.Message, state: FSMContext):
    if not await is_logged_in(message):
        await state.clear()
        return
    
    try:
        days = int(message.text)
        if days < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد معتبر (۰ یا بیشتر) وارد کنید.")
        return
    
    data = await state.get_data()
    name = data['name']
    limit_gb = data['limit']
    
    expiry_date = None
    if days > 0:
        expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    
    try:
        api = XUIApi(settings.PANEL_URL, settings.PANEL_LOGIN, settings.PANEL_PASSWORD)
        result = await api.create_vless_user(
            name=name,
            limit_gb=limit_gb,
            expiry_date=expiry_date
        )
        if result.get('success'):
            config_link = result.get('link', 'لینک تولید نشد')
            await message.answer(
                f"✅ **کانفیگ با موفقیت ساخته شد!**\n\n"
                f"👤 **نام:** {name}\n"
                f"📊 **حجم:** {limit_gb} گیگ\n"
                f"📅 **انقضا:** {expiry_date if expiry_date else 'نامحدود'}\n\n"
                f"🔗 **لینک کانفیگ:**\n`{config_link}`",
                parse_mode="Markdown",
                reply_markup=get_main_menu()
            )
        else:
            await message.answer(
                f"❌ **خطا در ساخت کانفیگ:**\n{result.get('message', 'خطای ناشناخته')}",
                reply_markup=get_main_menu()
            )
    except Exception as e:
        await message.answer(
            f"❌ **خطا در ارتباط با پنل:**\n{str(e)}",
            reply_markup=get_main_menu()
        )
    
    await state.clear()

# ========== لیست کاربران ==========
@router.message(F.text == "📋 لیست کاربران")
async def cmd_list(message: types.Message):
    if not await is_logged_in(message):
        return
    
    try:
        api = XUIApi(settings.PANEL_URL, settings.PANEL_LOGIN, settings.PANEL_PASSWORD)
        users = await api.get_users()
        if not users:
            await message.answer("❌ هیچ کاربری یافت نشد.", reply_markup=get_main_menu())
            return
        
        user_list = "📋 **لیست کاربران:**\n\n"
        for i, user in enumerate(users, 1):
            user_list += (
                f"{i}. 👤 **{user.get('name', 'بی‌نام')}**\n"
                f"   🆔 شناسه: `{user.get('id')}`\n"
                f"   📊 حجم: {user.get('limit', 'نامحدود')} گیگ\n"
                f"   📅 انقضا: {user.get('expiry', 'نامحدود')}\n\n"
            )
        
        await message.answer(
            user_list,
            parse_mode="Markdown",
            reply_markup=get_users_inline_menu(users)
        )
    except Exception as e:
        await message.answer(
            f"❌ **خطا در دریافت لیست کاربران:**\n{str(e)}",
            reply_markup=get_main_menu()
        )

# ========== حذف کاربر از دکمه اینلاین ==========
@router.callback_query(F.data.startswith("delete_user_"))
async def delete_user_from_button(callback: types.CallbackQuery):
    if not await is_logged_in(callback.message):
        return
    
    user_id = callback.data.split("_")[2]
    try:
        api = XUIApi(settings.PANEL_URL, settings.PANEL_LOGIN, settings.PANEL_PASSWORD)
        result = await api.delete_user(user_id)
        if result.get('success'):
            await callback.message.edit_text(
                f"✅ کاربر با شناسه `{user_id}` با موفقیت حذف شد.",
                parse_mode="Markdown"
            )
        else:
            await callback.message.edit_text(
                f"❌ **خطا در حذف کاربر:**\n{result.get('message', 'خطای ناشناخته')}",
                parse_mode="Markdown"
            )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ **خطا در ارتباط با پنل:**\n{str(e)}"
        )
    await callback.answer()
    await callback.message.answer("🔙 به منوی اصلی بازگشتید.", reply_markup=get_main_menu())

# ========== حذف کاربر (دستور متنی) ==========
@router.message(F.text == "❌ حذف کاربر")
async def start_delete_user(message: types.Message, state: FSMContext):
    if not await is_logged_in(message):
        return
    
    await state.set_state(UserDeletion.waiting_for_user_id)
    await message.answer(
        "🗑️ **شناسه کاربر مورد نظر را وارد کنید:**\n\n"
        "شناسه را می‌توانید از لیست کاربران (دکمه 📋 لیست کاربران) مشاهده کنید.",
        parse_mode="Markdown"
    )

@router.message(UserDeletion.waiting_for_user_id, F.text)
async def process_delete_user(message: types.Message, state: FSMContext):
    if not await is_logged_in(message):
        await state.clear()
        return
    
    user_id = message.text.strip()
    try:
        api = XUIApi(settings.PANEL_URL, settings.PANEL_LOGIN, settings.PANEL_PASSWORD)
        result = await api.delete_user(user_id)
        if result.get('success'):
            await message.answer(
                f"✅ کاربر با شناسه `{user_id}` با موفقیت حذف شد.",
                parse_mode="Markdown",
                reply_markup=get_main_menu()
            )
        else:
            await message.answer(
                f"❌ **خطا در حذف کاربر:**\n{result.get('message', 'خطای ناشناخته')}",
                reply_markup=get_main_menu()
            )
    except Exception as e:
        await message.answer(
            f"❌ **خطا در ارتباط با پنل:**\n{str(e)}",
            reply_markup=get_main_menu()
        )
    await state.clear()

# ========== آمار پنل ==========
@router.message(F.text == "📊 آمار پنل")
async def cmd_stats(message: types.Message):
    if not await is_logged_in(message):
        return
    
    try:
        api = XUIApi(settings.PANEL_URL, settings.PANEL_LOGIN, settings.PANEL_PASSWORD)
        stats = await api.get_stats()
        stats_text = (
            "📊 **آمار کلی پنل**\n\n"
            f"👥 **تعداد کل کاربران:** {stats.get('total_users', 0)}\n"
            f"🟢 **کاربران فعال:** {stats.get('active_users', 0)}\n"
            f"🔴 **کاربران غیرفعال:** {stats.get('inactive_users', 0)}\n"
            f"📊 **ترافیک کل مصرفی:** {stats.get('total_traffic', 0)} GB\n"
            f"💾 **وضعیت سرور:** {stats.get('server_status', 'نامشخص')}"
        )
        await message.answer(stats_text, parse_mode="Markdown", reply_markup=get_main_menu())
    except Exception as e:
        await message.answer(
            f"❌ **خطا در دریافت آمار:**\n{str(e)}",
            reply_markup=get_main_menu()
        )

# ========== بازگشت به منو ==========
@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("🔙 به منوی اصلی بازگشتید.", reply_markup=get_main_menu())
    await callback.answer()

# ========== لغو عملیات ==========
@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu())

# ========== پاسخ به پیام‌های ناشناخته ==========
@router.message()
async def handle_unknown(message: types.Message):
    if message.text in ["🔑 ورود به پنل", "➕ ساخت کانفیگ جدید", "📋 لیست کاربران", "❌ حذف کاربر", "📊 آمار پنل", "🔐 خروج از پنل"]:
        return
    
    await message.answer(
        "❓ دستور یا پیام ناشناخته.\n"
        "لطفاً از دکمه‌های منو استفاده کنید یا /help را بزنید.",
        reply_markup=get_main_menu() if user_sessions.get(message.from_user.id) else get_login_menu()
    )
