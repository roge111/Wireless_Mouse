#!/usr/bin/env python3
"""
Проект "Воздушная мышь" - улучшенная версия с дебаунсингом кнопок.
Версия 2.0: Добавлены левая и правая кнопки мыши с программным дебаунсингом,
            настраиваемая чувствительность и мёртвая зона гироскопа.

Автор: Студенты ИТМО, курс "Интернет вещей"
Версия: 2.0
Дата: 2025
"""

import asyncio
import json
import signal
import sys
import time
import board
from adafruit_lsm6ds.lsm6ds33 import LSM6DS33
from adafruit_lsm6ds import Rate, AccelRange, AccelHPF, GyroRange
import websockets
from signal import SIGINT, SIGTERM
from zero_hid import Mouse
import RPi.GPIO as GPIO

# ==================== КОНФИГУРАЦИЯ ====================
# Пины сенсоров и кнопок (нумерация BCM)
TOUCH_PIN = 4      # Сенсор движения (активация перемещения курсора)
BTN_LEFT = 17      # Левая кнопка мыши
BTN_RIGHT = 18     # Правая кнопка мыши

# Настройки
DEBOUNCE_TIME = 0.05      # Защита от дребезга кнопок (сек)
GYRO_THRESHOLD = 0.02     # Мёртвая зона гироскопа (порог обнуления)
GYRO_SENSITIVITY = 8.0    # Множитель чувствительности мыши
# ======================================================

# Глобальные переменные
sox = None          # Экземпляр датчика LSM6DS33
shutdown = False    # Флаг завершения работы
m = Mouse()         # Объект мыши для эмуляции HID

# Настройка GPIO
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

# Инициализация пинов: TOUCH_PIN как вход (без подтяжки - зависит от вашей схемы)
GPIO.setup(TOUCH_PIN, GPIO.IN)

# Инициализация кнопок: PUD_UP = кнопка замыкает на GND при нажатии
# LOW (0) = нажата, HIGH (1) = отпущена
GPIO.setup(BTN_LEFT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BTN_RIGHT, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def zero_if_small(x, threshold=GYRO_THRESHOLD):
    """
    Обнуляет малые значения для устранения дрейфа гироскопа.
    
    Аргументы:
        x (float): Входное значение
        threshold (float): Порог обнуления (по умолчанию GYRO_THRESHOLD)
    
    Возвращает:
        float: 0.0 если |x| < threshold, иначе x
    """
    return 0.0 if abs(x) < threshold else x


class ButtonDebouncer:
    """
    Программный дебаунсер для устранения дребезга контактов кнопок.
    
    Атрибуты:
        pin (int): Номер GPIO-пина
        debounce_time (float): Время стабилизации (сек)
        last_state (int): Последнее считанное состояние
        last_change (float): Время последнего изменения состояния
        stable_state (int): Текущее стабильное состояние
    
    Методы:
        read(): Возвращает True если кнопка нажата (стабильно)
    """
    def __init__(self, pin, debounce_time):
        """
        Инициализация дебаунсера для указанного пина.
        
        Аргументы:
            pin (int): Номер GPIO-пина (BCM)
            debounce_time (float): Время дебаунса в секундах
        """
        self.pin = pin
        self.debounce_time = debounce_time
        self.last_state = GPIO.input(pin)
        self.last_change = time.time()
        self.stable_state = self.last_state
    
    def read(self):
        """
        Чтение стабильного состояния кнопки с учётом дебаунса.
        
        Возвращает:
            bool: True если кнопка нажата (стабильно), False если отпущена.
        
        Примечание:
            Для схемы с подтяжкой к питанию (PUD_UP) нажатие соответствует LOW.
        """
        current_read = GPIO.input(self.pin)
        current_time = time.time()
        
        # Если состояние изменилось - сбрасываем таймер
        if current_read != self.last_state:
            self.last_change = current_time
            self.last_state = current_read
        
        # Если прошло достаточно времени с последнего изменения - считаем состояние стабильным
        if (current_time - self.last_change) > self.debounce_time:
            if self.stable_state != self.last_state:
                self.stable_state = self.last_state
        
        # Для схемы PUD_UP: LOW (0) = нажата
        return self.stable_state == GPIO.LOW


async def sensor_reader():
    """
    Основной цикл чтения данных сенсора и управления мышью.
    
    Функция выполняет:
    1. Инициализацию датчика LSM6DS33 (при первом запуске)
    2. Инициализацию дебаунсеров для левой и правой кнопок
    3. Чтение гироскопических данных с калибровкой смещения
    4. Управление курсором мыши при активации сенсором касания
    5. Обработку нажатий кнопок с эмуляцией левого/правого клика
    6. Логирование производительности (итераций в секунду)
    
    Данные сохраняются в sensor_reader.latest_data для WebSocket.
    """
    global sox, shutdown, m

    # Инициализация IMU (один раз)
    if sox is None:
        i2c = board.I2C()
        sox = LSM6DS33(i2c, address=0x6B)
        sox.accelerometer_rate = Rate.RATE_SHUTDOWN
        sox.accelerometer_range = AccelRange.RANGE_2G
        sox.high_pass_filter = AccelHPF.HPF_DIV400
        sox.gyro_data_rate = Rate.RATE_208_HZ
        sox.gyro_range = GyroRange.RANGE_125_DPS  # Fixed typo: soxgyro_range -> sox.gyro_range

    # Инициализация дебаунсеров для кнопок
    btn_left_db = ButtonDebouncer(BTN_LEFT, DEBOUNCE_TIME)
    btn_right_db = ButtonDebouncer(BTN_RIGHT, DEBOUNCE_TIME)
    
    # Состояния кнопок для детектирования нажатий (фронты)
    left_pressed = False
    right_pressed = False

    iteration_count = 0
    last_print_time = time.time()
    
    while not shutdown:
        try:
            # === Чтение гироскопа ===
            gyro = list(sox.gyro)

            # Калибровка (ваши смещения)
            gyro[0] += 0.11011872144861299
            gyro[1] -= 0.24510967682695364
            gyro[2] += 0.030543261909900768
            
            # Store latest reading for WebSocket
            sensor_reader.latest_data = {
                "accel": {"x": 0, "y": 0, "z": 0},
                "gyro": {"x": zero_if_small(gyro[0]), 
                        "y": zero_if_small(gyro[1]), 
                        "z": zero_if_small(gyro[2])}
            }

            # === Логика движения мыши (только при нажатом сенсоре) ===
            touch_state = GPIO.input(TOUCH_PIN)
            movement_enabled = (touch_state == GPIO.HIGH)
            
            if movement_enabled:
                # Маппинг осей: gyro[2] -> X, gyro[0] -> Y (под вашу перчатку)
                dx = -round(gyro[2] * GYRO_SENSITIVITY)
                dy = -round(gyro[0] * GYRO_SENSITIVITY)
                
                if dx != 0 or dy != 0:
                    m.move(dx, dy)
            
            # === Обработка кнопок мыши ===
            # Левая кнопка
            left_btn_now = btn_left_db.read()
            if left_btn_now and not left_pressed:
                # Фронт нажатия: была отпущена -> стала нажата
                m.left_click(press=True, release=False)
                left_pressed = True
            elif not left_btn_now and left_pressed:
                # Фронт отпускания
                m.release()
                left_pressed = False
            
            # Правая кнопка
            right_btn_now = btn_right_db.read()
            if right_btn_now and not right_pressed:
                m.right_click(press=True, release=False)
                right_pressed = True
            elif not right_btn_now and right_pressed:
                m.release()
                right_pressed = False
            
            # === Статистика и задержка ===
            await asyncio.sleep(0.01)  # ~100 Hz update rate (оптимально для мыши)
            iteration_count += 1
            
            current_time = time.time()
            if current_time - last_print_time >= 1.0:
                print(f"Iterations/sec: {iteration_count:,} | "
                      f"Touch: {'ON' if movement_enabled else 'OFF'} | "
                      f"Buttons: L{'1' if left_pressed else '0'} R{'1' if right_pressed else '0'}")
                iteration_count = 0
                last_print_time = current_time
            
        except Exception as e:
            print(f"Sensor error: {e}. Reconnecting...")
            sox = None
            await asyncio.sleep(1)


async def websocket_handler(websocket, path):
    """Handle WebSocket connections and send sensor data"""
    global shutdown
    print("Client connected")
    
    try:
        while not shutdown:
            if hasattr(sensor_reader, 'latest_data'):
                await websocket.send(json.dumps(sensor_reader.latest_data))
            await asyncio.sleep(0.01)
    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")


async def http_handler(reader, writer):
    """Serve the HTML visualization page"""
    request = await reader.read(1024)
    response = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/html\r\n"
        "Connection: close\r\n"
        "\r\n"
        """
<!DOCTYPE html>
<html>
<head>
    <title>LSM6DS33 Sensor Preview</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .chart-container { width: 90%; max-width: 900px; height: 300px; margin-bottom: 25px; background: white; padding: 10px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        h1, h2 { color: #333; }
        .status { padding: 8px 12px; margin: 10px 0; border-radius: 4px; font-weight: bold; }
        .connected { background-color: #d4edda; color: #155724; }
        .disconnected { background-color: #f8d7da; color: #721c24; }
        .debug { font-size: 0.9em; color: #666; margin-top: 5px; }
        .controls { background: #e7f3fe; padding: 10px; border-left: 4px solid #2196F3; margin: 10px 0; }
    </style>
</head>
<body>
    <h1>LSM6DS33 Sensor Real-time Preview</h1>
    
    <div class="controls">
        <strong>Управление:</strong><br>
        • Движение курсора: удерживать сенсор на перчатке (GPIO 4)<br>
        • Левый клик: кнопка на GPIO 17<br>
        • Правый клик: кнопка на GPIO 18
    </div>
    
    <div id="status" class="status disconnected">WebSocket: Disconnected</div>
    <div id="debug" class="debug">Waiting for data...</div>
    
    <h2>Gyroscope (rad/s)</h2>
    <div class="chart-container">
        <canvas id="gyroChart"></canvas>
    </div>

    <script>
        const maxPoints = 100;
        const datasets = {
            gyro: {
                x: { label: 'Gyro X', data: Array(maxPoints).fill(0), borderColor: 'red' },
                y: { label: 'Gyro Y', data: Array(maxPoints).fill(0), borderColor: 'green' },
                z: { label: 'Gyro Z', data: Array(maxPoints).fill(0), borderColor: 'blue' }
            }
        };

        function createChart(ctx, title, data) {
            return new Chart(ctx, {
                type: 'line',
                data: {
                    labels: Array(maxPoints).fill(''),
                    datasets: Object.values(data).map(d => ({
                        label: d.label,
                        data: d.data,
                        borderColor: d.borderColor,
                        borderWidth: 2,
                        fill: false,
                        tension: 0.1,
                        pointRadius: 0
                    }))
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    scales: {
                        x: { display: false },
                        y: { min: -0.5, max: 0.5 }
                    },
                    plugins: { legend: { position: 'top' } }
                }
            });
        }

        const gyroChart = createChart(
            document.getElementById('gyroChart').getContext('2d'),
            'Gyroscope',
            datasets.gyro
        );

        const debugDiv = document.getElementById('debug');
        const statusDiv = document.getElementById('status');
        const ws = new WebSocket(`ws://${location.hostname}:8765`);
        
        ws.onopen = () => {
            statusDiv.textContent = 'WebSocket: Connected';
            statusDiv.className = 'status connected';
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            datasets.gyro.x.data.shift(); datasets.gyro.x.data.push(data.gyro.x);
            datasets.gyro.y.data.shift(); datasets.gyro.y.data.push(data.gyro.y);
            datasets.gyro.z.data.shift(); datasets.gyro.z.data.push(data.gyro.z);
            
            gyroChart.update('none');
            debugDiv.textContent = `Gyro: [${data.gyro.x.toFixed(3)}, ${data.gyro.y.toFixed(3)}, ${data.gyro.z.toFixed(3)}]`;
        };

        ws.onclose = () => {
            statusDiv.textContent = 'WebSocket: Disconnected';
            statusDiv.className = 'status disconnected';
            setTimeout(() => location.reload(), 3000);
        };
    </script>
</body>
</html>
        """
    )
    writer.write(response.encode())
    await writer.drain()
    writer.close()


async def main():
    global shutdown

    loop = asyncio.get_running_loop()
    for signal_enum in [SIGINT, SIGTERM]:
        loop.add_signal_handler(signal_enum, loop.stop)
    
    # Start sensor reader
    sensor_task = asyncio.create_task(sensor_reader())
    
    # Start WebSocket server
    ws_server = await websockets.serve(websocket_handler, "0.0.0.0", 8765)
    print("WebSocket server started on ws://0.0.0.0:8765")
    
    # Start HTTP server
    http_server = await asyncio.start_server(http_handler, "0.0.0.0", 8000)
    print("HTTP server started on http://0.0.0.0:8000")
    
    # Handle shutdown
    def signal_handler():
        global shutdown
        shutdown = True
        print("\nShutting down...")
    
    # Используем только loop.add_signal_handler для asyncio-совместимости
    # (signal.signal может конфликтовать с event loop)
    
    try:
        await asyncio.gather(sensor_task, asyncio.Future())
    except asyncio.CancelledError:
        pass
    finally:
        ws_server.close()
        http_server.close()
        await ws_server.wait_closed()
        await http_server.wait_closed()
        GPIO.cleanup()  # Обязательно освобождаем GPIO
        print("Servers stopped, GPIO cleaned")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print("Bye")
        GPIO.cleanup()
        if exc.args:
            print(f"Exit reason: {exc.args[0]}")