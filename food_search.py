import json
import asyncio
import aiohttp
import base64
from datetime import datetime
from typing import Dict, Any
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

class FoodSearch:
    def _get_time_emoji(self) -> str:
        hour = datetime.now().hour
        if 6 <= hour < 12:
            return "🌅"
        elif 12 <= hour < 18:
            return "☀️"
        elif 18 <= hour < 24:
            return "🌙"
        else:
            return "🌃"

    def _build_prompt(self, message: str = None, is_voice: bool = False, is_photo: bool = False) -> str:
        base_rules = """Ты — профессиональный диетолог-нутрициолог.

ГЛАВНЫЕ ПРАВИЛА (НЕ НАРУШАТЬ):

1. Если названо БЛЮДО (яичница, омлет, борщ) — верни ЕГО ОДНОЙ СТРОКОЙ:
   - "яичница из 4 яиц" = 4 яйца (200г) + масло для жарки (20г) = 220г
   - КБЖУ яичницы: 496 ккал, 25.2г белка, 42.0г жира, 1.6г углеводов

2. Если назван НАПИТОК С ДОБАВКАМИ ("кофе с 2 ложками сахара"):
   - Верни КАЖДЫЙ компонент ОТДЕЛЬНОЙ строкой
   - "кофе чёрный" и "сахар" как разные продукты
   - 1 ложка сахара = 10г, 40 ккал

3. ПРОСТЫЕ ПРОДУКТЫ — посчитай КБЖУ для каждого отдельно.

4. КБЖУ популярных продуктов:
   - Овсянка на молоке (300г): 195 ккал, 8.4г белка, 4.8г жира, 30.0г углеводов
   - Чёрный кофе (200г): 2 ккал, 0.2г белка, 0г жира, 0.3г углеводов
   - Куриная грудка отварная (150г): 247 ккал, 46.5г белка, 5.4г жира, 0г углеводов
   - Гречка отварная (150г): 165 ккал, 5.7г белка, 1.5г жира, 33.0г углеводов

5. Формат ответа — ТОЛЬКО JSON:
{
    "products": [
        {
            "name": "название продукта",
            "weight_grams": вес в граммах,
            "calories": калории,
            "protein": белки,
            "fat": жиры,
            "carbs": углеводы
        }
    ],
    "total": {
        "calories": сумма калорий,
        "protein": сумма белков,
        "fat": сумма жиров,
        "carbs": сумма углеводов
    }
}

Верни ТОЛЬКО JSON. Без пояснений. Без эмодзи."""

        if is_photo:
            return f"""Посмотри на фото еды. Опиши, какие продукты и блюда ты видишь.
Оцени примерный вес каждого продукта в граммах.
Посчитай КБЖУ для каждого продукта.

{base_rules}"""
        
        if is_voice:
            return f"""Пользователь произнёс: "{message}"

Распознай и нормализуй текст, посчитай КБЖУ.

{base_rules}"""
        
        return f"""Пользователь написал: "{message}"

{base_rules}"""

    async def _call_api(
        self, 
        text: str = None, 
        audio_data: bytes = None, 
        image_data: bytes = None,
        image_mime_type: str = None
    ) -> Dict[str, Any]:
        
        time_emoji = self._get_time_emoji()
        is_voice = audio_data is not None
        is_photo = image_data is not None
        
        prompt = self._build_prompt(text, is_voice, is_photo)
        
        content_parts = [{"type": "text", "text": prompt}]
        
        if audio_data:
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            content_parts.append({
                "type": "input_audio",
                "input_audio": {
                    "data": audio_base64,
                    "format": "ogg"
                }
            })
        
        if image_data:
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_mime_type};base64,{image_base64}"
                }
            })
        
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": OPENAI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "Ты — диетолог. Отвечаешь ТОЛЬКО JSON. Считаешь КБЖУ точно."
                },
                {
                    "role": "user",
                    "content": content_parts
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2000
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                request_type = "📝 текстовый" if not is_voice and not is_photo else (
                    "🎤 голосовой" if is_voice else "🖼️ фото"
                )
                print(f"🔄 [{OPENAI_MODEL}] Отправка {request_type} запроса...")
                
                async with session.post(
                    f"{OPENAI_BASE_URL}/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=90
                ) as response:
                    status = response.status
                    response_text = await response.text()
                    
                    if status != 200:
                        print(f"❌ Ошибка API (статус {status}): {response_text[:300]}")
                        return {"success": False, "error": f"Ошибка API (статус {status})"}
                    
                    result = json.loads(response_text)
                    
                    if "choices" not in result or not result["choices"]:
                        print(f"❌ Пустой ответ от API")
                        return {"success": False, "error": "Пустой ответ от API"}
                    
                    content = result["choices"][0]["message"]["content"]
                    content = content.strip()
                    
                    print(f"✅ Ответ получен ({len(content)} символов)")
                    
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
                        print(f"❌ Ошибка парсинга JSON: {e}")
                        print(f"📄 Содержимое: {content[:300]}")
                        return {"success": False, "error": "Неверный формат ответа от ИИ"}
                    
                    products = parsed.get("products", [])
                    total = parsed.get("total", {})
                    
                    if not products:
                        print(f"❌ Нет продуктов в ответе")
                        return {"success": False, "error": "ИИ не распознал продукты"}
                    
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
                    
                    print(f"✅ Успешно: {len(products)} продуктов")
                    
                    return {
                        "success": True,
                        "data": parsed,
                        "user_text": "\n".join(lines)
                    }
                    
        except asyncio.TimeoutError:
            print(f"❌ Таймаут (90 сек)")
            return {"success": False, "error": "Сервер не ответил вовремя. Попробуйте ещё раз."}
        except aiohttp.ClientError as e:
            print(f"❌ Ошибка соединения: {e}")
            return {"success": False, "error": "Ошибка соединения с сервером."}
        except Exception as e:
            print(f"❌ Ошибка: {type(e).__name__}: {e}")
            return {"success": False, "error": str(e)[:100]}

    async def parse_and_calculate(self, message: str) -> Dict[str, Any]:
        return await self._call_api(text=message)

    async def parse_voice(self, recognized_text: str, audio_data: bytes) -> Dict[str, Any]:
        return await self._call_api(text=recognized_text, audio_data=audio_data)

    async def parse_photo(self, image_data: bytes, mime_type: str) -> Dict[str, Any]:
        return await self._call_api(image_data=image_data, image_mime_type=mime_type)