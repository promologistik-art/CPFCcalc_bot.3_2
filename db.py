import sqlite3
import os
import random
import string
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional
from config import USER_DB_PATH, TRIAL_DAYS, ACTIVITY_LEVELS, REFERRAL_BONUS_DAYS, SUBSCRIPTION_PRICE

class UserDB:
    def __init__(self):
        os.makedirs(os.path.dirname(USER_DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(USER_DB_PATH)
        self.create_tables()
        self._migrate()
        print(f"База данных подключена: {USER_DB_PATH}")
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                short_id INTEGER UNIQUE,
                username TEXT,
                first_name TEXT,
                is_blocked BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                weight REAL,
                height REAL,
                age INTEGER,
                activity_level TEXT,
                gender TEXT,
                goal TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                is_active BOOLEAN DEFAULT 1,
                is_forever BOOLEAN DEFAULT 0,
                trial_end DATE,
                paid_until DATE,
                report_enabled BOOLEAN DEFAULT 0,
                report_time TEXT DEFAULT '07:00',
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS meals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_name TEXT,
                protein REAL,
                fat REAL,
                carbohydrates REAL,
                calories REAL,
                weight_grams REAL DEFAULT 100,
                meal_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_stats (
                user_id INTEGER,
                date DATE,
                total_protein REAL DEFAULT 0,
                total_fat REAL DEFAULT 0,
                total_carbs REAL DEFAULT 0,
                total_calories REAL DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_name TEXT,
                protein REAL,
                fat REAL,
                carbohydrates REAL,
                calories REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                UNIQUE(user_id, product_name)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referral_links (
                code TEXT PRIMARY KEY,
                referrer_id INTEGER,
                referrer_username TEXT,
                commission_percent INTEGER,
                bonus_months INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referee_id INTEGER,
                link_code TEXT,
                is_paid BOOLEAN DEFAULT 0,
                commission_earned REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP,
                FOREIGN KEY (referrer_id) REFERENCES users (user_id),
                FOREIGN KEY (referee_id) REFERENCES users (user_id),
                FOREIGN KEY (link_code) REFERENCES referral_links (code)
            )
        ''')
        
        self.conn.commit()
    
    def _migrate(self):
        """Добавляет недостающие колонки и присваивает short_id"""
        cursor = self.conn.cursor()
        
        # profiles.goal
        cursor.execute("PRAGMA table_info(profiles)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'goal' not in columns:
            cursor.execute("ALTER TABLE profiles ADD COLUMN goal TEXT")
            self.conn.commit()
            print("✅ Добавлена колонка goal в profiles")
        
        # subscriptions.report_enabled / report_time
        cursor.execute("PRAGMA table_info(subscriptions)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'report_enabled' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN report_enabled BOOLEAN DEFAULT 0")
            self.conn.commit()
            print("✅ Добавлена report_enabled в subscriptions")
        if 'report_time' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN report_time TEXT DEFAULT '07:00'")
            self.conn.commit()
            print("✅ Добавлена report_time в subscriptions")
        
        # users.short_id
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'short_id' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN short_id INTEGER")
            self.conn.commit()
            print("✅ Добавлена колонка short_id в users")
        
        # users.is_blocked
        if 'is_blocked' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_blocked BOOLEAN DEFAULT 0")
            self.conn.commit()
            print("✅ Добавлена колонка is_blocked в users")
        
        # Присваиваем short_id всем, у кого его нет
        cursor.execute("SELECT user_id FROM users WHERE short_id IS NULL ORDER BY created_at")
        users_without_short_id = cursor.fetchall()
        
        if users_without_short_id:
            # Находим максимальный существующий short_id
            cursor.execute("SELECT COALESCE(MAX(short_id), 0) FROM users")
            max_short_id = cursor.fetchone()[0]
            
            for idx, (user_id,) in enumerate(users_without_short_id, max_short_id + 1):
                cursor.execute("UPDATE users SET short_id = ? WHERE user_id = ?", (idx, user_id))
            self.conn.commit()
            print(f"✅ Присвоены short_id для {len(users_without_short_id)} пользователей")
    
    def get_or_create_user(self, user_id: int, username: str = None, first_name: str = None, referral_code: str = None) -> tuple:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        
        is_new = False
        if not user:
            # Получаем следующий short_id
            cursor.execute("SELECT COALESCE(MAX(short_id), 0) + 1 FROM users")
            next_short_id = cursor.fetchone()[0]
            
            cursor.execute(
                "INSERT INTO users (user_id, short_id, username, first_name) VALUES (?, ?, ?, ?)",
                (user_id, next_short_id, username, first_name)
            )
            
            extra_days = 0
            referrer_id = None
            link_code = None
            
            if referral_code:
                cursor.execute(
                    "SELECT referrer_id, referrer_username, commission_percent, bonus_months FROM referral_links WHERE code = ?",
                    (referral_code,)
                )
                link_info = cursor.fetchone()
                if link_info:
                    referrer_id = link_info[0]
                    referrer_username = link_info[1]
                    commission_percent = link_info[2]
                    bonus_months = link_info[3]
                    link_code = referral_code
                    extra_days = REFERRAL_BONUS_DAYS
                    
                    if referrer_id and referrer_id < 0 and referrer_username:
                        cursor.execute("SELECT user_id FROM users WHERE LOWER(username) = ?", (referrer_username.lower(),))
                        real_user = cursor.fetchone()
                        if real_user:
                            referrer_id = real_user[0]
                            cursor.execute("UPDATE referral_links SET referrer_id = ? WHERE code = ?", (referrer_id, referral_code))
                    
                    if referrer_id and referrer_id > 0:
                        cursor.execute('''
                            INSERT INTO referrals (referrer_id, referee_id, link_code)
                            VALUES (?, ?, ?)
                        ''', (referrer_id, user_id, link_code))
                        
                        if bonus_months and bonus_months > 0:
                            self._add_bonus_months_to_user(referrer_id, bonus_months)
            
            trial_end = (datetime.now() + timedelta(days=TRIAL_DAYS + extra_days)).date().isoformat()
            cursor.execute(
                "INSERT INTO subscriptions (user_id, trial_end) VALUES (?, ?)",
                (user_id, trial_end)
            )
            self.conn.commit()
            is_new = True
        
        return user, is_new
    
    def _add_bonus_months_to_user(self, user_id: int, months: int):
        cursor = self.conn.cursor()
        cursor.execute("SELECT paid_until, trial_end FROM subscriptions WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
        if row:
            paid_until = row[0]
            trial_end = row[1]
            
            current_end = None
            if paid_until:
                current_end = date.fromisoformat(paid_until)
            elif trial_end:
                current_end = date.fromisoformat(trial_end)
            else:
                current_end = date.today()
            
            new_end = current_end + timedelta(days=months * 30)
            
            cursor.execute(
                "UPDATE subscriptions SET paid_until = ? WHERE user_id = ?",
                (new_end.isoformat(), user_id)
            )
            self.conn.commit()
    
    def get_user_id_by_username(self, username: str) -> Optional[int]:
        cursor = self.conn.cursor()
        username = username.lstrip('@').lower()
        cursor.execute("SELECT user_id FROM users WHERE LOWER(username) = ?", (username,))
        row = cursor.fetchone()
        return row[0] if row else None
    
    def get_user_by_short_id(self, short_id: int) -> Optional[Dict]:
        """Получает пользователя по внутреннему ID"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT u.user_id, u.short_id, u.username, u.first_name, u.is_blocked, u.created_at,
                   s.is_forever, s.trial_end, s.paid_until
            FROM users u
            LEFT JOIN subscriptions s ON u.user_id = s.user_id
            WHERE u.short_id = ?
        ''', (short_id,))
        row = cursor.fetchone()
        if row:
            return {
                "user_id": row[0],
                "short_id": row[1],
                "username": row[2],
                "first_name": row[3],
                "is_blocked": bool(row[4]),
                "created_at": row[5],
                "is_forever": bool(row[6]) if row[6] is not None else False,
                "trial_end": row[7],
                "paid_until": row[8]
            }
        return None
    
    def get_all_users_with_short_id(self) -> List[Dict]:
        """Получает всех пользователей с short_id для админ-списка"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT u.user_id, u.short_id, u.username, u.first_name, u.is_blocked,
                   s.is_forever, s.trial_end, s.paid_until
            FROM users u
            LEFT JOIN subscriptions s ON u.user_id = s.user_id
            ORDER BY u.short_id
        ''')
        rows = cursor.fetchall()
        
        users = []
        for r in rows:
            is_active = False
            status_text = "нет подписки"
            end_date = None
            
            if r[5]:  # is_forever
                is_active = True
                status_text = "бессрочно"
            elif r[7]:  # paid_until
                paid_until = date.fromisoformat(r[7])
                if paid_until >= date.today():
                    is_active = True
                    status_text = f"оплачено до {paid_until.strftime('%d.%m')}"
                    end_date = r[7]
                else:
                    status_text = "истекла"
            elif r[6]:  # trial_end
                trial_end = date.fromisoformat(r[6])
                if trial_end >= date.today():
                    is_active = True
                    status_text = f"триал до {trial_end.strftime('%d.%m')}"
                    end_date = r[6]
                else:
                    status_text = "триал истёк"
            
            users.append({
                "user_id": r[0],
                "short_id": r[1],
                "username": r[2],
                "first_name": r[3],
                "is_blocked": bool(r[4]),
                "is_active": is_active,
                "status_text": status_text,
                "end_date": end_date
            })
        
        return users
    
    def toggle_block_user(self, user_id: int) -> bool:
        """Переключает блокировку пользователя. Возвращает новое состояние (True = заблокирован)"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return False
        
        new_state = 0 if row[0] else 1
        cursor.execute("UPDATE users SET is_blocked = ? WHERE user_id = ?", (new_state, user_id))
        self.conn.commit()
        return bool(new_state)
    
    def is_user_blocked(self, user_id: int) -> bool:
        """Проверяет, заблокирован ли пользователь"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return bool(row[0]) if row else False
    
    def remove_subscription(self, user_id: int):
        """Удаляет подписку пользователя (обнуляет все даты)"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE subscriptions 
            SET is_forever = 0, trial_end = NULL, paid_until = NULL 
            WHERE user_id = ?
        ''', (user_id,))
        self.conn.commit()
    
    def add_user_product(self, user_id: int, name: str, protein: float, fat: float, carbs: float, calories: float) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO user_products (user_id, product_name, protein, fat, carbohydrates, calories)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, name, protein, fat, carbs, calories))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_user_products(self, user_id: int) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT product_name, protein, fat, carbohydrates, calories
            FROM user_products
            WHERE user_id = ?
            ORDER BY product_name
        ''', (user_id,))
        rows = cursor.fetchall()
        return [{
            "name": r[0],
            "protein": r[1],
            "fat": r[2],
            "carbohydrates": r[3],
            "calories": r[4]
        } for r in rows]
    
    def delete_user_product(self, user_id: int, name: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute('''
            DELETE FROM user_products WHERE user_id = ? AND product_name = ?
        ''', (user_id, name))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def get_all_products_for_search(self, user_id: int) -> Dict:
        from config import FOOD_DB_PATH
        import json
        
        global_db = {}
        
        if os.path.exists(FOOD_DB_PATH):
            with open(FOOD_DB_PATH, 'r', encoding='utf-8') as f:
                global_db = json.load(f)
        else:
            print(f"ВНИМАНИЕ: Файл базы продуктов не найден: {FOOD_DB_PATH}")
            global_db = {
                "хлеб": {"protein": 7.5, "fat": 2.9, "carbohydrates": 50.9, "calories": 264},
                "яйцо куриное": {"protein": 12.5, "fat": 11.5, "carbohydrates": 0.7, "calories": 157},
                "сахар": {"protein": 0, "fat": 0, "carbohydrates": 100, "calories": 400},
            }
        
        user_products = self.get_user_products(user_id)
        for p in user_products:
            global_db[p["name"]] = {
                "protein": p["protein"],
                "fat": p["fat"],
                "carbohydrates": p["carbohydrates"],
                "calories": p["calories"]
            }
        
        return global_db
    
    def set_report_settings(self, user_id: int, enabled: bool, report_time: str = None):
        cursor = self.conn.cursor()
        if report_time:
            cursor.execute('''
                UPDATE subscriptions SET report_enabled = ?, report_time = ? WHERE user_id = ?
            ''', (1 if enabled else 0, report_time, user_id))
        else:
            cursor.execute('''
                UPDATE subscriptions SET report_enabled = ? WHERE user_id = ?
            ''', (1 if enabled else 0, user_id))
        self.conn.commit()
    
    def get_report_settings(self, user_id: int) -> dict:
        cursor = self.conn.cursor()
        cursor.execute("SELECT report_enabled, report_time FROM subscriptions WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return {"enabled": bool(row[0]), "time": row[1] or "07:00"}
        return {"enabled": False, "time": "07:00"}
    
    def get_report_users(self) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT user_id, report_time FROM subscriptions 
            WHERE report_enabled = 1 AND (is_forever = 1 OR paid_until >= date('now') OR trial_end >= date('now'))
        ''')
        rows = cursor.fetchall()
        return [{"user_id": r[0], "report_time": r[1] or "07:00"} for r in rows]
    
    def get_yesterday_stats(self, user_id: int) -> Optional[Dict]:
        cursor = self.conn.cursor()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        cursor.execute('''
            SELECT total_calories, total_protein, total_fat, total_carbs
            FROM daily_stats
            WHERE user_id = ? AND date = ?
        ''', (user_id, yesterday))
        row = cursor.fetchone()
        if row:
            return {
                "calories": row[0] or 0,
                "protein": row[1] or 0,
                "fat": row[2] or 0,
                "carbs": row[3] or 0
            }
        return None
    
    def get_stats_for_period(self, user_id: int, days: int) -> List[Dict]:
        cursor = self.conn.cursor()
        start_date = (date.today() - timedelta(days=days)).isoformat()
        cursor.execute('''
            SELECT date, total_calories, total_protein, total_fat, total_carbs
            FROM daily_stats
            WHERE user_id = ? AND date >= ?
            ORDER BY date
        ''', (user_id, start_date))
        rows = cursor.fetchall()
        return [{
            "date": r[0],
            "calories": r[1] or 0,
            "protein": r[2] or 0,
            "fat": r[3] or 0,
            "carbs": r[4] or 0
        } for r in rows]
    
    def set_goal(self, user_id: int, goal: str):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE profiles SET goal = ? WHERE user_id = ?", (goal, user_id))
        self.conn.commit()
    
    def get_goal(self, user_id: int) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT goal FROM profiles WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    
    def generate_referral_link(self, username: str, commission_percent: int, bonus_months: int) -> str:
        cursor = self.conn.cursor()
        temp_id = -abs(hash(username)) % 1000000
        
        while True:
            code = 'ref_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
            cursor.execute("SELECT code FROM referral_links WHERE code = ?", (code,))
            if not cursor.fetchone():
                break
        
        cursor.execute('''
            INSERT INTO referral_links (code, referrer_id, referrer_username, commission_percent, bonus_months)
            VALUES (?, ?, ?, ?, ?)
        ''', (code, temp_id, username, commission_percent, bonus_months))
        self.conn.commit()
        
        return code
    
    def get_referral_stats(self) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 
                COALESCE(u.user_id, rl.referrer_id) as user_id,
                COALESCE(u.username, rl.referrer_username) as username,
                COALESCE(u.first_name, rl.referrer_username) as first_name,
                COUNT(DISTINCT ref.id) as total_refs,
                SUM(CASE WHEN ref.is_paid = 1 THEN 1 ELSE 0 END) as paid_refs,
                SUM(ref.commission_earned) as total_commission,
                rl.commission_percent,
                rl.bonus_months
            FROM referral_links rl
            LEFT JOIN users u ON u.user_id = rl.referrer_id OR (u.username = rl.referrer_username)
            LEFT JOIN referrals ref ON rl.code = ref.link_code
            GROUP BY rl.code
            ORDER BY total_refs DESC
        ''')
        rows = cursor.fetchall()
        
        result = []
        seen = set()
        for r in rows:
            key = r[0] or r[1]
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "user_id": r[0],
                "username": r[1],
                "first_name": r[2],
                "total_refs": r[3] or 0,
                "paid_refs": r[4] or 0,
                "total_commission": r[5] or 0,
                "commission_percent": r[6],
                "bonus_months": r[7]
            })
        
        return result
    
    def get_referral_link_info(self, code: str) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT rl.code, rl.referrer_id, rl.referrer_username, rl.commission_percent, rl.bonus_months, rl.created_at,
                   COALESCE(u.username, rl.referrer_username) as username,
                   COALESCE(u.first_name, rl.referrer_username) as first_name,
                   COUNT(ref.id) as total_refs,
                   SUM(CASE WHEN ref.is_paid = 1 THEN 1 ELSE 0 END) as paid_refs
            FROM referral_links rl
            LEFT JOIN users u ON u.user_id = rl.referrer_id OR (u.username = rl.referrer_username)
            LEFT JOIN referrals ref ON rl.code = ref.link_code
            WHERE rl.code = ?
            GROUP BY rl.code
        ''', (code,))
        row = cursor.fetchone()
        
        if row:
            return {
                "code": row[0],
                "referrer_id": row[1],
                "referrer_username": row[2],
                "commission_percent": row[3],
                "bonus_months": row[4],
                "created_at": row[5],
                "username": row[6],
                "first_name": row[7],
                "total_refs": row[8] or 0,
                "paid_refs": row[9] or 0
            }
        return None
    
    def mark_referral_paid(self, referee_id: int, amount: float):
        cursor = self.conn.cursor()
        
        cursor.execute(
            "SELECT referrer_id, link_code FROM referrals WHERE referee_id = ? AND is_paid = 0",
            (referee_id,)
        )
        row = cursor.fetchone()
        
        if row:
            referrer_id = row[0]
            link_code = row[1]
            
            cursor.execute("SELECT commission_percent FROM referral_links WHERE code = ?", (link_code,))
            link_row = cursor.fetchone()
            commission_percent = link_row[0] if link_row else 20
            
            commission = amount * commission_percent / 100
            
            cursor.execute('''
                UPDATE referrals 
                SET is_paid = 1, commission_earned = ?, paid_at = CURRENT_TIMESTAMP
                WHERE referee_id = ?
            ''', (commission, referee_id))
            self.conn.commit()
            
            return referrer_id, commission
        
        return None, 0
    
    def get_referrer_stats(self, user_id: int) -> Dict:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 
                COUNT(DISTINCT r.referee_id) as total_refs,
                SUM(CASE WHEN r.is_paid = 1 THEN 1 ELSE 0 END) as paid_refs,
                SUM(r.commission_earned) as total_commission
            FROM referrals r
            WHERE r.referrer_id = ?
        ''', (user_id,))
        row = cursor.fetchone()
        
        return {
            "total_refs": row[0] or 0,
            "paid_refs": row[1] or 0,
            "total_commission": row[2] or 0
        }
    
    def get_profile(self, user_id: int) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT name, weight, height, age, activity_level, gender, goal FROM profiles WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "name": row[0],
                "weight": row[1],
                "height": row[2],
                "age": row[3],
                "activity_level": row[4],
                "gender": row[5],
                "goal": row[6]
            }
        return None
    
    def save_profile(self, user_id: int, data: Dict):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO profiles (user_id, name, weight, height, age, activity_level, gender, goal, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            user_id,
            data.get("name"),
            data.get("weight"),
            data.get("height"),
            data.get("age"),
            data.get("activity_level"),
            data.get("gender"),
            data.get("goal")
        ))
        self.conn.commit()
    
    def calculate_bmr(self, profile: Dict) -> float:
        weight = profile.get("weight", 70)
        height = profile.get("height", 170)
        age = profile.get("age", 30)
        gender = profile.get("gender", "male")
        
        if gender == "male":
            return 10 * weight + 6.25 * height - 5 * age + 5
        else:
            return 10 * weight + 6.25 * height - 5 * age - 161
    
    def calculate_tdee(self, profile: Dict) -> float:
        bmr = self.calculate_bmr(profile)
        activity_level = profile.get("activity_level", "2")
        factor = ACTIVITY_LEVELS.get(activity_level, {"factor": 1.375})["factor"]
        return bmr * factor
    
    def get_subscription_status(self, user_id: int) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT is_active, is_forever, trial_end, paid_until FROM subscriptions WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        
        if not row:
            return {"is_active": True, "is_forever": False, "trial_end": None, "paid_until": None, "days_left": TRIAL_DAYS}
        
        is_active = row[0]
        is_forever = row[1]
        trial_end = row[2]
        paid_until = row[3]
        
        if is_forever:
            return {"is_active": True, "is_forever": True, "trial_end": None, "paid_until": None, "days_left": 9999}
        
        today = date.today()
        days_left = 0
        
        if trial_end:
            trial_end_date = date.fromisoformat(trial_end)
            if trial_end_date >= today:
                days_left = (trial_end_date - today).days
        
        if paid_until:
            paid_until_date = date.fromisoformat(paid_until)
            if paid_until_date >= today:
                days_left = max(days_left, (paid_until_date - today).days)
        
        return {
            "is_active": is_active and days_left > 0,
            "is_forever": False,
            "trial_end": trial_end,
            "paid_until": paid_until,
            "days_left": days_left
        }
    
    def activate_subscription(self, user_id: int, days: int = 30):
        cursor = self.conn.cursor()
        paid_until = (datetime.now() + timedelta(days=days)).date().isoformat()
        cursor.execute(
            "UPDATE subscriptions SET is_active = 1, is_forever = 0, paid_until = ? WHERE user_id = ?",
            (paid_until, user_id)
        )
        self.conn.commit()
        self.mark_referral_paid(user_id, SUBSCRIPTION_PRICE)
    
    def activate_forever_subscription(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE subscriptions SET is_active = 1, is_forever = 1, trial_end = NULL, paid_until = NULL WHERE user_id = ?",
            (user_id,)
        )
        self.conn.commit()
        self.mark_referral_paid(user_id, SUBSCRIPTION_PRICE)
    
    def extend_subscription(self, user_id: int, days: int):
        cursor = self.conn.cursor()
        cursor.execute("SELECT paid_until FROM subscriptions WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
        if row and row[0]:
            current_end = date.fromisoformat(row[0])
            new_end = max(current_end, date.today()) + timedelta(days=days)
        else:
            new_end = date.today() + timedelta(days=days)
        
        cursor.execute(
            "UPDATE subscriptions SET is_active = 1, is_forever = 0, paid_until = ? WHERE user_id = ?",
            (new_end.isoformat(), user_id)
        )
        self.conn.commit()
        self.mark_referral_paid(user_id, SUBSCRIPTION_PRICE)
    
    def clear_all_user_data(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM meals WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM daily_stats WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM user_products WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM referrals WHERE referrer_id = ? OR referee_id = ?", (user_id, user_id))
        cursor.execute("DELETE FROM referral_links WHERE referrer_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        self.conn.commit()
    
    def get_user_info(self, user_id: int) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT username, first_name, created_at FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        if not user:
            return None
        
        stats = self.get_today_stats(user_id)
        sub = self.get_subscription_status(user_id)
        ref_stats = self.get_referrer_stats(user_id)
        
        return {
            "username": user[0],
            "first_name": user[1],
            "created_at": user[2],
            "calories": stats["calories"],
            "protein": stats["protein"],
            "fat": stats["fat"],
            "carbs": stats["carbs"],
            "subscription": sub,
            "referral_stats": ref_stats
        }
    
    def add_meal(self, user_id: int, product: Dict[str, Any]):
        cursor = self.conn.cursor()
        
        product_name = product.get("name", "Unknown")
        protein = product.get("protein", 0)
        fat = product.get("fat", 0)
        carbs = product.get("carbs", 0)
        calories = product.get("calories", 0)
        weight_grams = product.get("weight_grams", 100)
        
        cursor.execute('''
            INSERT INTO meals (user_id, product_name, protein, fat, carbohydrates, calories, weight_grams)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, product_name, protein, fat, carbs, calories, weight_grams))
        
        self.conn.commit()
        
        today = date.today().isoformat()
        cursor.execute('''
            INSERT INTO daily_stats (user_id, date, total_protein, total_fat, total_carbs, total_calories)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                total_protein = total_protein + ?,
                total_fat = total_fat + ?,
                total_carbs = total_carbs + ?,
                total_calories = total_calories + ?
        ''', (
            user_id, today,
            protein, fat, carbs, calories,
            protein, fat, carbs, calories
        ))
        self.conn.commit()
    
    def get_today_stats(self, user_id: int) -> dict:
        cursor = self.conn.cursor()
        today = date.today().isoformat()
        cursor.execute('''
            SELECT total_protein, total_fat, total_carbs, total_calories
            FROM daily_stats
            WHERE user_id = ? AND date = ?
        ''', (user_id, today))
        row = cursor.fetchone()
        if row:
            return {"protein": row[0] or 0, "fat": row[1] or 0, "carbs": row[2] or 0, "calories": row[3] or 0}
        return {"protein": 0, "fat": 0, "carbs": 0, "calories": 0}
    
    def get_recent_meals(self, user_id: int, limit: int = 10) -> List[dict]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT product_name, protein, fat, carbohydrates, calories, weight_grams, meal_time
            FROM meals
            WHERE user_id = ?
            ORDER BY meal_time DESC
           