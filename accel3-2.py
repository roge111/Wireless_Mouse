#!/usr/bin/env python3
"""
Проект "Воздушная мышь" - версия 3.2 с исправлениями и улучшениями.
Версия 3.2: Исправлена опечатка soxgyro_range -> sox.gyro_range,
            улучшена структура кода с разделением на секции.

Автор: Студенты ИТМО, курс "Интернет вещей"
Версия: 3.2
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

# --------------------- НАСТРОЙКИ ПИНОВ ---------------------
TOUCH_PIN = 4      # Сенсор касания (активирует движение мыши)
LEFT_BTN = 17      # Кнопка левого клика мыши
RIGHT_BTN = 18     # Кнопка правого клика мыши

DEBOUNCE_MS = 50   # Антидребезг (мс)

# --------------------- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---------------------
sox = None          # Экземпляр датчика LSM6DS33
shutdown = False    # Флаг завершения работы
m = Mouse()         # Объект мыши для эмуляции HID

# --------------------- НАСТРОЙКА GPIO ---------------------
GPIO.setmode(GPIO.BCM)
GPIO.setup(TOUCH_PIN, GPIO.IN)                        # Сенсор касания (HIGH при касании)
GPIO.setup(LEFT_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)   # Левая кнопка (замыкает на GND)
GPIO.setup(RIGHT_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Правая кнопка (замыкает на GND)

# --------------------- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---------------------
def zero_if_small(x, threshold=0.02):
    """
    Обнуляет значения ниже порога для устранения дрейфа датчика.
    
    Аргументы:
        x (float): Входное значение
        threshold (float): Порог обнуления (по умолчанию 0.02)
    
    Возвращает:
        float: 0.0 если |x| < threshold, иначе x
    """
    return 0.0 if abs(x) < threshold else x

# --------------------- ОСНОВНОЙ ЦИКЛ ---------------------
async def sensor_reader():
    """
    Основной цикл чтения данных сенсора и управления мышью.
    
    Функция выполняет:
    1. Инициализацию датчика LSM6DS33 (при первом запуске)
    2. Чтение гироскопических данных с калибровкой смещения
    3. Управление курсором мыши при активации сенсором касания
    4. Обработку нажатий кнопок с антидребезгом на основе временных меток
    5. Логирование производительности (итераций в секунду)
    
    Данные сохраняются в sensor_reader.latest_data для WebSocket.
    """
    global sox, shutdown, m

    # Инициализация датчика (выполняется один раз)
    if sox is None:
        i2c = board.I2C()
        sox = LSM6DS33(i2c, address=0x6B)
        sox.accelerometer_rate = Rate.RATE_SHUTDOWN
        sox.accelerometer_range = AccelRange.RANGE_2G
        sox.high_pass_filter = AccelHPF.HPF_DIV400
        sox.gyro_data_rate = Rate.RATE_208_HZ
        sox.gyro_range = GyroRange.RANGE_125_DPS   # Исправлено с soxgyro_range

    # Переменные для антидребезга кнопок
    last_left_time = 0
    last_right_time = 0
    last_left_state = GPIO.HIGH   # По умолчанию не нажата (PULL_UP)
    last_right_state = GPIO.HIGH

    iteration_count = 0
    last_print_time = time.time()

    while not shutdown:
        try:
            # Читаем гироскоп
            gyro = list(sox.gyro)

            # Калибровочные смещения (можно подставить свои)
            gyro[0] += 0.11011872144861299
            gyro[1] -= 0.24510967682695364
            gyro[2] += 0.030543261909900768

            # Сохраняем последние данные для веб-интерфейса
            sensor_reader.latest_data = {
                "accel": {"x": 0, "y": 0, "z": 0},
                "gyro": {
                    "x": zero_if_small(gyro[0]),
                    "y": zero_if_small(gyro[1]),
                    "z": zero_if_small(gyro[2])
                }
            }

            # ---------- ДВИЖЕНИЕ МЫШИ (только при касании сенсора) ----------
            touch_state = GPIO.input(TOUCH_PIN)
            if touch_state == GPIO.HIGH:
                m.move(-round(gyro[2] * 8), -round(gyro[0] * 8))

            # ---------- ОБРАБОТКА КНОПОК МЫШИ (с антидребезгом) ----------
            now_ms = time.time() * 1000

            # Левая кнопка (нажатие = переход HIGH → LOW)
            left_now = GPIO.input(LEFT_BTN)
            if last_left_state == GPIO.HIGH and left_now == GPIO.LOW:
                if (now_ms - last_left_time) > DEBOUNCE_MS:
                    m.left_click()
                    last_left_time = now_ms
            last_left_state = left_now

            # Правая кнопка
            right_now = GPIO.input(RIGHT_BTN)
            if last_right_state == GPIO.HIGH and right_now == GPIO.LOW:
                if (now_ms - last_right_time) > DEBOUNCE_MS:
                    m.right_click()
                    last_right_time = now_ms
            last_right_state = right_now

            # Фиксированная задержка для стабильной частоты (~100 Гц)
            await asyncio.sleep(0.01)

            # Статистика итераций (для отладки)
            iteration_count += 1
            current_time = time.time()
            if current_time - last_print_time >= 1.0:
                iterations_per_sec = iteration_count  # <-- Исправлено название
                print(f"Iterations per second: {iterations_per_sec:,}")
                iteration_count = 0
                last_print_time = current_time

        except Exception as e:
            print(f"Sensor error: {e}. Reconnecting...")
            sox = None
            await asyncio.sleep(1)

# --------------------- СЕРВЕРНАЯ ЧАСТЬ (без изменений) ---------------------
async def websocket_handler(websocket, path):
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
    </style>
</head>
<body>
    <h1>LSM6DS33 Sensor Real-time Preview</h1>
    <div id="status" class="status disconnected">WebSocket: Disconnected</div>
    <div id="debug" class="debug">Waiting for data...</div>
    
    <h2>Acceleration (m/s²)</h2>
    <div class="chart-container">
        <canvas id="accelChart"></canvas>
    </div>
    
    <h2>Gyroscope (rad/s)</h2>
    <div class="chart-container">
        <canvas id="gyroChart"></canvas>
    </div>

    <script>
        const maxPoints = 100;
        // ✅ FIXED: Properly initialize all datasets with 'data' property
        const datasets = {
            accel: {
                x: { label: 'Accel X', data: Array(maxPoints).fill(0), borderColor: 'red' },
                y: { label: 'Accel Y', data: Array(maxPoints).fill(0), borderColor: 'green' },
                z: { label: 'Accel Z', data: Array(maxPoints).fill(0), borderColor: 'blue' }
            },
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
                        data: d.data,  // ✅ Reference the actual data array
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
                        y: { 
                            min: title.includes('Accel') ? -20 : -0.1,
                            max: title.includes('Accel') ? 20 : 0.1
                        }
                    },
                    plugins: { 
                        legend: { position: 'top' },
                        tooltip: { enabled: true }
                    }
                }
            });
        }

        const accelChart = createChart(
            document.getElementById('accelChart').getContext('2d'),
            'Acceleration',
            datasets.accel
        );
        const gyroChart = createChart(
            document.getElementById('gyroChart').getContext('2d'),
            'Gyroscope',
            datasets.gyro
        );

        const debugDiv = document.getElementById('debug');
        const statusDiv = document.getElementById('status');

        // ✅ CORRECT PORT: Connect to WebSocket server on 8765
        const ws = new WebSocket(`ws://${location.hostname}:8765`);
        
        ws.onopen = () => {
            statusDiv.textContent = 'WebSocket: Connected';
            statusDiv.className = 'status connected';
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            // Update acceleration data
            datasets.accel.x.data.shift(); datasets.accel.x.data.push(data.accel.x);
            datasets.accel.y.data.shift(); datasets.accel.y.data.push(data.accel.y);
            datasets.accel.z.data.shift(); datasets.accel.z.data.push(data.accel.z);
            
            // Update gyroscope data
            datasets.gyro.x.data.shift(); datasets.gyro.x.data.push(data.gyro.x);
            datasets.gyro.y.data.shift(); datasets.gyro.y.data.push(data.gyro.y);
            datasets.gyro.z.data.shift(); datasets.gyro.z.data.push(data.gyro.z);
            
            // ✅ Force chart update by modifying the data arrays in-place
            accelChart.update('none');  // 'none' = no animation, instant update
            gyroChart.update('none');
            
            // Debug info
            debugDiv.textContent = `Accel: [${data.accel.x.toFixed(2)}, ${data.accel.y.toFixed(2)}, ${data.accel.z.toFixed(2)}]`;
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            statusDiv.textContent = 'WebSocket: Error';
            statusDiv.className = 'status disconnected';
        };
        
        ws.onclose = () => {
            statusDiv.textContent = 'WebSocket: Disconnected';
            statusDiv.className = 'status disconnected';
            setTimeout(() => {
                location.reload();
            }, 3000);
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

    sensor_task = asyncio.create_task(sensor_reader())

    ws_server = await websockets.serve(websocket_handler, "0.0.0.0", 8765)
    print("WebSocket server started on ws://0.0.0.0:8765")

    http_server = await asyncio.start_server(http_handler, "0.0.0.0", 8000)
    print("HTTP server started on http://0.0.0.0:8000")

    def signal_handler():
        global shutdown
        shutdown = True
        print("\nShutting down...")

    signal.signal(signal.SIGINT, lambda s, f: signal_handler())
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler())

    try:
        await asyncio.gather(sensor_task, asyncio.Future())
    except asyncio.CancelledError:
        pass
    finally:
        ws_server.close()
        http_server.close()
        await ws_server.wait_closed()
        await http_server.wait_closed()
        GPIO.cleanup()
        print("Servers stopped")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print("Bye")
        GPIO.cleanup()
        if exc.args:
            print(exc.args[0])