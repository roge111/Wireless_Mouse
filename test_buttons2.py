#!/usr/bin/env python3
"""
Продвинутый тест кнопок для Raspberry Pi с программным дебаунсингом.
Проверяет GPIO 17 (левая кнопка мыши) и GPIO 18 (правая кнопка мыши)
с использованием объектно-ориентированного подхода и детектированием
фронтов нажатия/отпускания.

Используется для отладки кнопок в проекте "Воздушная мышь".

Автор: Студенты ИТМО, курс "Интернет вещей"
Версия: 2.0
Дата: 2025
"""

import RPi.GPIO as GPIO
import time
import sys

# ================= КОНФИГУРАЦИЯ =================
BUTTONS = {
    'LEFT':  {'pin': 17, 'label': '🖱️ ЛКМ (GPIO 17)'},
    'RIGHT': {'pin': 18, 'label': '🖱️ ПКМ (GPIO 18)'},
}
DEBOUNCE_TIME = 0.05  # Время дебаунса в секундах
# ================================================

def setup_gpio():
    """
    Настройка GPIO-пинов для кнопок.
    
    Выполняет:
    1. Отключение предупреждений GPIO
    2. Установку нумерации BCM
    3. Конфигурацию каждого пина как входа с внутренней подтяжкой к питанию (PUD_UP)
    
    Примечание:
        Если используется внешний резистор подтяжки, замените pull_up_down=GPIO.PUD_UP
        на pull_up_down=GPIO.PUD_OFF.
    """
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    
    for name, cfg in BUTTONS.items():
        # Используем внутреннюю подтяжку PUD_UP
        # Если у вас внешний резистор — замените на pull_up_down=GPIO.PUD_OFF
        GPIO.setup(cfg['pin'], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        print(f"✓ {cfg['label']} initialized")

class ButtonMonitor:
    """
    Монитор кнопки с программным дебаунсингом и детектированием фронтов.
    
    Атрибуты:
        pin (int): Номер GPIO-пина (BCM)
        debounce_time (float): Время стабилизации (сек)
        last_raw (int): Последнее считанное сырое состояние
        last_change (float): Время последнего изменения состояния
        stable (int): Текущее стабильное состояние
        prev_stable (int): Предыдущее стабильное состояние (для детектирования фронтов)
    
    Методы:
        read(): Возвращает True если кнопка нажата (стабильно)
        on_press(): Возвращает True при фронте нажатия
        on_release(): Возвращает True при фронте отпускания
    """
    def __init__(self, pin, debounce_time):
        """
        Инициализация монитора для указанного пина.
        
        Аргументы:
            pin (int): Номер GPIO-пина (BCM)
            debounce_time (float): Время дебаунса в секундах
        """
        self.pin = pin
        self.debounce_time = debounce_time
        self.last_raw = GPIO.input(pin)
        self.last_change = time.time()
        self.stable = self.last_raw
        self.prev_stable = self.stable
        
    def read(self):
        """
        Чтение стабильного состояния кнопки с учётом дебаунса.
        
        Возвращает:
            bool: True если кнопка нажата (стабильно), False если отпущена.
        
        Примечание:
            Для схемы с подтяжкой к питанию (PUD_UP) нажатие соответствует LOW.
        """
        raw = GPIO.input(self.pin)
        now = time.time()
        
        # Сброс таймера при изменении сигнала
        if raw != self.last_raw:
            self.last_change = now
            self.last_raw = raw
        
        # Обновляем стабильное состояние после задержки
        if (now - self.last_change) > self.debounce_time:
            self.stable = self.last_raw
            
        # Для PUD_UP: LOW (0) = нажата
        return self.stable == GPIO.LOW
    
    def on_press(self):
        """
        Детектирование фронта нажатия (был отпущен → стал нажат).
        
        Возвращает:
            bool: True если произошло нажатие с момента последнего вызова,
                  False в противном случае.
        """
        current = self.read()
        result = current and not self.prev_stable
        self.prev_stable = current
        return result
    
    def on_release(self):
        """
        Детектирование фронта отпускания (был нажат → стал отпущен).
        
        Возвращает:
            bool: True если произошло отпускание с момента последнего вызова,
                  False в противном случае.
        """
        current = self.read()
        result = not current and self.prev_stable
        self.prev_stable = current
        return result


def main():
    """
    Основная функция теста кнопок.
    
    Выполняет:
    1. Настройку GPIO через setup_gpio()
    2. Создание мониторов для каждой кнопки
    3. Бесконечный цикл опроса мониторов с выводом текущего состояния
       и событий нажатия/отпускания
    4. Обработку прерывания Ctrl+C с корректной очисткой GPIO
    """
    setup_gpio()
    
    # Создаём мониторы для кнопок
    monitors = {name: ButtonMonitor(cfg['pin'], DEBOUNCE_TIME)
                for name, cfg in BUTTONS.items()}
    
    print("\n" + "="*50)
    print("🔘 ТЕСТ КНОПОК — нажимайте, наблюдайте")
    print("Выход: Ctrl+C")
    print("="*50 + "\n")
    
    try:
        while True:
            output = []
            
            for name, mon in monitors.items():
                status = "🔴 НАЖАТА" if mon.read() else "⚪ отпущена"
                output.append(f"{BUTTONS[name]['label']}: {status}")
                
                # Детектируем события для наглядности
                if mon.on_press():
                    output.append(f"  → ✨ CLICK (press)")
                if mon.on_release():
                    output.append(f"  → ✨ RELEASE")
            
            # Вывод в одну строку с очисткой
            sys.stdout.write('\r' + ' | '.join(output) + ' ' * 10)
            sys.stdout.flush()
            
            time.sleep(0.02)  # Опрос с частотой 50 Гц
            
    except KeyboardInterrupt:
        print("\n\n✓ Завершено")
    finally:
        GPIO.cleanup()
        print("✓ GPIO cleaned up")


if __name__ == "__main__":
    main()