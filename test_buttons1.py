#!/usr/bin/env python3
"""
Тестовый скрипт для проверки кнопок левого и правого клика мыши.
Скрипт отслеживает изменения состояния кнопок на GPIO 17 и 18
и выводит сообщение при каждом нажатии/отпускании.

Используется для отладки подключения кнопок в проекте "Воздушная мышь".

Автор: Студенты ИТМО, курс "Интернет вещей"
Версия: 1.0
Дата: 2025
"""

import RPi.GPIO as GPIO
import time

# Пины подключения кнопок (BCM нумерация)
LEFT_BTN = 17   # Левая кнопка мыши
RIGHT_BTN = 18  # Правая кнопка мыши

# Настройка GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(LEFT_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(RIGHT_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

print("Тест кнопок. Нажимайте левую (GPIO17) и правую (GPIO18). Ctrl+C для выхода.")
print("Логика: ненажата -> 1, нажата -> 0\n")

# Предыдущие состояния для детектирования изменений
last_left = GPIO.HIGH
last_right = GPIO.HIGH

try:
    while True:
        left = GPIO.input(LEFT_BTN)
        right = GPIO.input(RIGHT_BTN)

        # Детектирование изменения состояния левой кнопки
        if left != last_left:
            state = "НАЖАТА" if left == GPIO.LOW else "ОТПУЩЕНА"
            print(f"ЛЕВАЯ  кнопка {state} (пин={LEFT_BTN}, значение={left})")
            last_left = left

        # Детектирование изменения состояния правой кнопки
        if right != last_right:
            state = "НАЖАТА" if right == GPIO.LOW else "ОТПУЩЕНА"
            print(f"ПРАВАЯ кнопка {state} (пин={RIGHT_BTN}, значение={right})")
            last_right = right

        time.sleep(0.01)  # Небольшая задержка для снижения нагрузки
except KeyboardInterrupt:
    print("\nЗавершение работы.")
finally:
    GPIO.cleanup()
    print("GPIO очищены.")