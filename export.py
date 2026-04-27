"""
Модуль для экспорта данных в Excel
"""
import io
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter


def export_users_to_excel(users: list) -> bytes:
    """
    Экспортирует список пользователей в Excel-файл
    
    Args:
        users: список словарей с данными пользователей из user_db.get_all_users()
    
    Returns:
        bytes: содержимое Excel-файла
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Пользователи"
    
    # Стили
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Заголовки
    headers = [
        "ID пользователя",
        "Имя",
        "Username",
        "Дата регистрации",
        "Статус подписки",
        "Дата окончания"
    ]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border
    
    # Данные
    for row_idx, user in enumerate(users, 2):
        user_id = user.get("user_id", "")
        first_name = user.get("first_name", "")
        username = f"@{user['username']}" if user.get("username") else ""
        
        # Форматирование даты регистрации (ДД.ММ.ГГГГ)
        created_at = user.get("created_at", "")
        if created_at and len(created_at) >= 10:
            parts = created_at[:10].split('-')
            if len(parts) == 3:
                created_at = f"{parts[2]}.{parts[1]}.{parts[0]}"
        
        # Определяем статус подписки и дату окончания
        status = ""
        end_date = ""
        
        if user.get("is_forever"):
            status = "Бессрочная"
            end_date = "∞"
        elif user.get("paid_until"):
            status = "Оплачена"
            end_date = user["paid_until"]
            if end_date and len(end_date) >= 10:
                # Формат ДД.ММ.ГГГГ
                parts = end_date[:10].split('-')
                if len(parts) == 3:
                    end_date = f"{parts[2]}.{parts[1]}.{parts[0]}"
        elif user.get("trial_end"):
            status = "Триал"
            end_date = user["trial_end"]
            if end_date and len(end_date) >= 10:
                # Формат ДД.ММ.ГГГГ
                parts = end_date[:10].split('-')
                if len(parts) == 3:
                    end_date = f"{parts[2]}.{parts[1]}.{parts[0]}"
        else:
            status = "Не активна"
            end_date = "-"
        
        row_data = [
            user_id,
            first_name,
            username,
            created_at,
            status,
            end_date
        ]
        
        for col, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="left", vertical="center")
    
    # Автоширина колонок
    for col in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 18
    
    # Дополнительный лист со статистикой
    ws_stats = wb.create_sheet("Статистика")
    
    total_users = len(users)
    active_users = sum(1 for u in users if (
        u.get("is_forever") or 
        (u.get("paid_until") and u["paid_until"] >= datetime.now().strftime("%Y-%m-%d")) or
        (u.get("trial_end") and u["trial_end"] >= datetime.now().strftime("%Y-%m-%d"))
    ))
    trial_users = sum(1 for u in users if u.get("trial_end") and not u.get("paid_until") and not u.get("is_forever"))
    paid_users = sum(1 for u in users if u.get("paid_until") or u.get("is_forever"))
    
    stats_data = [
        ["Показатель", "Значение"],
        ["Всего пользователей", total_users],
        ["Активных пользователей", active_users],
        ["На триале", trial_users],
        ["Оплативших", paid_users],
        ["Дата экспорта", datetime.now().strftime("%d.%m.%Y %H:%M")]
    ]
    
    for row_idx, row in enumerate(stats_data, 1):
        for col_idx, value in enumerate(row, 1):
            cell = ws_stats.cell(row=row_idx, column=col_idx, value=value)
            if row_idx == 1:
                cell.font = header_font
                cell.fill = header_fill
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="left" if col_idx == 1 else "center", vertical="center")
    
    ws_stats.column_dimensions['A'].width = 25
    ws_stats.column_dimensions['B'].width = 20
    
    # Сохраняем в bytes
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    return output.getvalue()