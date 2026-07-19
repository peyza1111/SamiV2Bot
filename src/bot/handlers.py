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
api = XUIApi(settings.PANEL_URL, settings.PANEL_LOGIN, settings.PANEL_PASSWORD)

# ========== تعریف وضعیت‌های مختلف برای عملیات ==========
class UserCreation(StatesGroup):
    waiting_for_name = State()
    waiting_for_limit = State()
    waiting_for_days = State()

class UserDeletion(StatesGroup):
    waiting_for_user_id = State()

# ========== منوی اصلی (دکمه‌های شیشه‌ای در پایین صفحه) ==========
def get_main_menu():
    """ساخت منوی اصلی با دکمه‌های شیشه‌ای"""
    keyboard = [
        [KeyboardButton(text="➕ ساخت کانفیگ جدید")],
        [KeyboardButton(text="📋 لیست کاربران")],
        [KeyboardButton(text="❌ حذف کاربر")],
        [KeyboardButton(text="📊 آمار پنل"), KeyboardButton(text="ℹ️ راهنما")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# ========== منوی اینلاین برای لیست کاربران ==========
def get_users_inline_menu(users):
    """ساخت منوی اینلاین برای لیست کاربران با دکمه حذف"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    for user in users:
        keyboard.add(
            InlineKeyboardButton(
                text=f"🗑️ حذف {user.get('name', 'بی‌نام')}",
                callback_data=f"delete_user_{user.get('id')}"
            )
        )
    keyboard.add(InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="back_to_menu"))
    return keyboard

# ========== دستور /start ==========
@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 به ربات مدیریت پنل خوش آمدید!\n\n"
        "از طریق دکمه‌های زیر می‌توانید تمام عملیات مدیریتی را انجام دهید:",
        reply_markup=get_main_menu()
    )

# ========== دستور /help ==========
@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 **راهنمای کامل ربات**\n\n"
        "✅ **ساخت کانفیگ جدید:**\n"
        "روی دکمه «➕ ساخت کانفیگ جدید» کلیک کنید و سپس مراحل را دنبال کنید.\n\n"
        "✅ **مشاهده لیست کاربران:**\n"
        "روی دکمه «📋 لیست کاربران» کلیک کنید.\n\n"
        "✅ **حذف کاربر:**\n"
        "روی دکمه «❌ حذف کاربر» کلیک کنید و شناسه کاربر را وارد کنید.\n\n"
        "✅ **آمار پنل:**\n"
        "برای مشاهده وضعیت کلی پنل، روی دکمه «📊 آمار پنل» کلیک کنید."
    )
    await message.answer(help_text, parse_mode="Markdown")

# ========== ساخت کانفیگ جدید (گام اول: دریافت نام) ==========
@router.message(F.text == "➕ ساخت کانفیگ جدید")
async def start_vless_creation(message: types.Message, state: FSMContext):
    await state.set_state(UserCreation.waiting_for_name)
    await message.answer(
        "📝 **لطفاً نام کاربر را وارد کنید:**\n\n"
        "نام باید فقط شامل حروف، اعداد و خط تیره باشد.",
        parse_mode="Markdown"
    )

@router.message(UserCreation.waiting_for_name, F.text)
async def process_vless_name(message: types.Message, state: FSMContext):
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

# ========== ساخت کانفیگ جدید (گام دوم: دریافت حجم) ==========
@router.message(UserCreation.waiting_for_limit, F.text)
async def process_vless_limit(message: types.Message, state: FSMContext):
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

# ========== ساخت کانفیگ جدید (گام سوم: دریافت مدت و ایجاد نهایی) ==========
@router.message(UserCreation.waiting_for_days, F.text)
async def process_vless_days(message: types.Message, state: FSMContext):
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
    
    # محاسبه تاریخ انقضا
    expiry_date = None
    if days > 0:
        expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    
    # ارسال به پنل برای ساخت
    try:
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
    try:
        users = await api.get_users()
        if not users:
            await message.answer("❌ هیچ کاربری یافت نشد.", reply_markup=get_main_menu())
            return
        
        # نمایش لیست به صورت متن
        user_list = "📋 **لیست کاربران:**\n\n"
        for i, user in enumerate(users, 1):
            user_list += (
                f"{i}. 👤 **{user.get('name', 'بی‌نام')}**\n"
                f"   🆔 شناسه: `{user.get('id')}`\n"
                f"   📊 حجم: {user.get('limit', 'نامحدود')} گیگ\n"
                f"   📅 انقضا: {user.get('expiry', 'نامحدود')}\n\n"
            )
        
        # ارسال لیست به همراه منوی اینلاین برای حذف سریع
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

# ========== حذف کاربر از طریق دکمه اینلاین ==========
@router.callback_query(F.data.startswith("delete_user_"))
async def delete_user_from_button(callback: types.CallbackQuery):
    user_id = callback.data.split("_")[2]
    try:
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
    await state.set_state(UserDeletion.waiting_for_user_id)
    await message.answer(
        "🗑️ **شناسه کاربر مورد نظر را وارد کنید:**\n\n"
        "شناسه را می‌توانید از لیست کاربران (دکمه 📋 لیست کاربران) مشاهده کنید.",
        parse_mode="Markdown"
    )

@router.message(UserDeletion.waiting_for_user_id, F.text)
async def process_delete_user(message: types.Message, state: FSMContext):
    user_id = message.text.strip()
    try:
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
    try:
        stats = await api.get_stats()  # تابع دریافت آمار را باید در xui_api.py بسازید
        stats_text = (
            "📊 **آمار کلی پنل**\n\n"
            f"👥 **تعداد کل کاربران:** {stats.get('total_users', 0)}\n"
            f"📈 **ترافیک مصرفی امروز:** {stats.get('today_traffic', 0)} GB\n"
            f"📊 **ترافیک کل مصرفی:** {stats.get('total_traffic', 0)} GB\n"
            f"💾 **وضعیت سرور:** {stats.get('server_status', 'نامشخص')}"
        )
        await message.answer(stats_text, parse_mode="Markdown", reply_markup=get_main_menu())
    except Exception as e:
        await message.answer(
            f"❌ **خطا در دریافت آمار:**\n{str(e)}",
            reply_markup=get_main_menu()
        )

# ========== بازگشت به منو از دکمه اینلاین ==========
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
    await message.answer(
        "❓ دستور یا پیام ناشناخته.\n"
        "لطفاً از دکمه‌های منو استفاده کنید یا /help را بزنید.",
        reply_markup=get_main_menu()
    )
