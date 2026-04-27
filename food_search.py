import json
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, Any
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_MODEL_FALLBACK

class FoodSearch:
    async def _call_api(self, message: str, model: str) -> Dict[str, Any]:
        """Вызов API с указанной моделью"""
        hour = datetime.now().hour
        if 6 <= hour < 12:
            time_emoji = "🌅"
        elif 12 <= hour < 18:
            time_emoji = "☀️"
        elif 18 <= hour < 24:
            time_emoji = "🌙"
        else:
            time_emoji = "🌃"

        prompt = f"""Ты — профессиональный диетолог-нутрициолог.

Пользователь написал: "{message}"

ГЛАВНЫЕ ПРАВИЛА (НЕ НАРУШАТЬ):

1. Если пользователь назвал БЛЮДО (яичница, омлет, борщ) — верни ЕГО ОДНОЙ СТРОКОЙ:
   - "яичница из 4 яиц" = 4 яйца (200г) + масло для жарки (20г) = 220г
   - КБЖУ яичницы: 496 ккал, 25.2г белка, 42.0г жира, 1.6г углеводов

2. Если пользователь назвал НАПИТОК С ДОБАВКАМИ ("кофе с 2 ложками сахара"):
   - Верни КАЖДЫЙ компонент ОТДЕЛЬНОЙ строкой
   - "кофе чёрный" и "сахар" как разные продукты
   - 1 ложка сахара = 10г, 40 ккал

3. ПРОСТЫЕ ПРОДУКТЫ:
   - "чёрный кофе 200 грамм" — просто посчитай КБЖУ чёрного кофе на 200г
   - "овсянка на молоке 300 грамм" — посчитай как готовое блюдо (овсяные хлопья + молоко)
   
   КБЖУ овсянки на молоке (300г): ~195 ккал, 8.4г белка, 4.8г жира, 30.0г углеводов
   КБЖУ чёрного кофе (200г): ~2 ккал, 0.2г белка, 0г жира, 0.3г углеводов

4. Пример правильного ответа на "овсянка на молоке 300 грамм, чёрный кофе 200 грамм":
{{
    "products": [
        {{
            "name": "овсянка на молоке",
            "weight_grams": 300,
            "calories": 195,
            "protein": 8.4,
            "fat": 4.8,
            "carbs": 30.0
        }},
        {{
            "name": "чёрный кофе",
            "weight_grams": 200,
            "calories": 2,
            "protein": 0.2,
            "fat": 0,
            "carbs": 0.3
        }}
    ],
    "total": {{
        "calories": 197,
        "protein": 8.6,
        "fat": 4.8,
        "carbs": 30.3
    }}
}}

Ты обладаешь точными знаниями КБЖУ всех продуктов и блюд.

Верни ТОЛЬКО JSON. Без пояснений. Без эмодзи."""

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Ты — диетолог. Отвечаешь ТОЛЬКО JSON. Яичница = яйца + масло. Кофе с сахаром = кофе + сахар отдельно. Овсянка на молоке = хлопья + молоко. Ты точно знаешь КБЖУ."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 2000
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                print(f"🔄 [{model}] Отправка запроса...")
                
                async with session.post(
                    f"{OPENAI_BASE_URL}/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=60
                ) as response:
                    status = response.status
                    response_text = await response.text()
                    
                    if status != 200:
                        print(f"❌ [{model}] Ошибка API (статус {status}): {response_text[:300]}")
                        return {"success": False, "error": f"Ошибка API (статус {status})"}
                    
                    result = json.loads(response_text)
                    
                    if "choices" not in result or not result["choices"]:
                        print(f"❌ [{model}] Пустой ответ")
                        return {"success": False, "error": "Пустой ответ от API"}
                    
                    content = result["choices"][0]["message"]["content"]
                    content = content.strip()
                    
                    print(f"✅ [{model}] Ответ получен ({len(content)} символов)")
                    
                    # Очистка от markdown-обёртки
                    if content.startswith("```json"):
                        content = content[7:]
                    if content.startswith("```"):
                        content = content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                    
                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError as e:
                        print(f"❌ [{model}] Ошибка парсинга JSON: {e}")
                        print(f"📄 Содержимое: {content[:300]}")
                        return {"success": False, "error": "Неверный формат ответа от ИИ"}
                    
                    products = parsed.get("products", [])
                    total = parsed.get("total", {})
                    
                    if not products:
                        print(f"❌ [{model}] Нет продуктов в ответе")
                        return {"success": False, "error": "ИИ не распознал продукты"}
                    
                    # Формируем текст для пользователя
                    lines = [f"{time_emoji} Ваш приём пищи:"]
                    lines.append("")
                    
                    for p in products:
                        name = p.get("name", "")
                        weight = p.get("weight_grams", 0)
                        cal = p.get("calories", 0)
                        prot = p.get("protein", 0)
                        fat = p.get("fat", 0)
                        carbs = p.get("carbs", 0)
                        lines.append(f"{name} - {weight}г, К {cal:.0f}, Б {prot:.1f}, Ж {fat:.1f}, У {carbs:.1f}")
                    
                    lines.append("")
                    lines.append(f"ИТОГО: {total.get('calories', 0):.0f} ккал | Б: {total.get('protein', 0):.1f}г | Ж: {total.get('fat', 0):.1f}г | У: {total.get('carbs', 0):.1f}г")
                    
                    print(f"✅ [{model}] Успешно: {len(products)} продуктов")
                    
                    return {
                        "success": True,
                        "data": parsed,
                        "user_text": "\n".join(lines)
                    }
                    
        except asyncio.TimeoutError:
            print(f"❌ [{model}] Таймаут (60 сек)")
            return {"success": False, "error": "Таймаут"}
        except aiohttp.ClientError as e:
            print(f"❌ [{model}] Ошибка соединения: {e}")
            return {"success": False, "error": "Ошибка соединения"}
        except Exception as e:
            print(f"❌ [{model}] Ошибка: {type(e).__name__}: {e}")
            return {"success": False, "error": str(e)[:100]}
    
    async def parse_and_calculate(self, message: str) -> Dict[str, Any]:
        """Основной метод с автопереключением на резервную модель"""
        
        # Пробуем основную модель
        print(f"🚀 Пробуем основную модель: {OPENAI_MODEL}")
        result = await self._call_api(message, OPENAI_MODEL)
        
        if result["success"]:
            return result
        
        # Если основная не сработала — пробуем резервную
        print(f"⚠️ Основная модель не сработала. Пробуем резервную: {OPENAI_MODEL_FALLBACK}")
        result = await self._call_api(message, OPENAI_MODEL_FALLBACK)
        
        if result["success"]:
            return result
        
        # Обе не сработали
        print(f"❌ Обе модели не сработали")
        return {"success": False, "error": "Сервис временно недоступен. Попробуйте позже."}