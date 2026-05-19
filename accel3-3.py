#!/usr/bin/env python3
"""
Проект "Воздушная мышь" - версия 3.3 с настраиваемой чувствительностью.
Версия 3.3: Добавлена переменная чувствительности, передаваемая в WebSocket,
            улучшена структура кода и комментарии.

Автор: Студенты ИТМО, курс "Интернет вещей"
Версия: 3.3
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
sensitivity = 8     # Коэффициент чувствительности (по умолчанию)

# --------------------- НАСТРОЙКА GPIO ---------------------
GPIO.setmode(GPIO.BCM)
GPIO.setup(TOUCH_PIN, GPIO.IN)                        # Сенсор касания (HIGH при касании)
GPIO.setup(LEFT_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)   # Левая кнопка
GPIO.setup(RIGHT_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Правая кнопка

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
    6. Передачу текущей чувствительности в WebSocket-данные
    
    Данные сохраняются в sensor_reader.latest_data для WebSocket.
    """
    global sox, shutdown, m, sensitivity

    # Инициализация датчика (выполняется один раз)
    if sox is None:
        i2c = board.I2C()
        sox = LSM6DS33(i2c, address=0x6B)
        sox.accelerometer_rate = Rate.RATE_SHUTDOWN
        sox.accelerometer_range = AccelRange.RANGE_2G
        sox.high_pass_filter = AccelHPF.HPF_DIV400
        sox.gyro_data_rate = Rate.RATE_208_HZ
        sox.gyro_range = GyroRange.RANGE_125_DPS

    # Переменные для антидребезга кнопок
    last_left_time = 0
    last_right_time = 0
    last_left_state = GPIO.HIGH
    last_right_state = GPIO.HIGH

    iteration_count = 0
    last_print_time = time.time()

    while not shutdown:
        try:
            # Читаем гироскоп
            gyro = list(sox.gyro)

            # Калибровочные смещения
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
                },
                "sensitivity": sensitivity    # добавили текущую чувствительность
            }

            # ---------- ДВИЖЕНИЕ МЫШИ (только при касании сенсора) ----------
            touch_state = GPIO.input(TOUCH_PIN)
            if touch_state == GPIO.HIGH:
                # Используем актуальную sensitivity
                m.move(-round(gyro[2] * sensitivity), -round(gyro[0] * sensitivity))

            # ---------- ОБРАБОТКА КНОПОК ----------
            now_ms = time.time() * 1000

            left_now = GPIO.input(LEFT_BTN)
            if last_left_state == GPIO.HIGH and left_now == GPIO.LOW:
                if (now_ms - last_left_time) > DEBOUNCE_MS:
                    m.left_click()
                    last_left_time = now_ms
            last_left_state = left_now

            right_now = GPIO.input(RIGHT_BTN)
            if last_right_state == GPIO.HIGH and right_now == GPIO.LOW:
                if (now_ms - last_right_time) > DEBOUNCE_MS:
                    m.right_click()
                    last_right_time = now_ms
            last_right_state = right_now

            await asyncio.sleep(0.01)

            # Статистика (отладка)
            iteration_count += 1
            current_time = time.time()
            if current_time - last_print_time >= 1.0:
                iterations_per_sec = iteration_count
                print(f"Iterations per second: {iterations_per_sec:,}, sensitivity: {sensitivity}")
                iteration_count = 0
                last_print_time = current_time

        except Exception as e:
            print(f"Sensor error: {e}. Reconnecting...")
            sox = None
            await asyncio.sleep(1)

# --------------------- ОБРАБОТЧИКИ СЕРВЕРА ---------------------
async def websocket_handler(websocket, path):
    global shutdown
    print("Client connected")
    try:
        while not shutdown:
            if hasattr(sensor_reader, 'latest_data'):
                # Отправляем данные, включая sensitivity
                data = sensor_reader.latest_data.copy()
                data["sensitivity"] = sensitivity   # гарантируем актуальность
                await websocket.send(json.dumps(data))
            await asyncio.sleep(0.01)
    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")

async def http_handler(reader, writer):
    global sensitivity
    request = await reader.read(1024)
    request_str = request.decode('utf-8', errors='ignore')
    
    # Проверяем, не запрос ли на изменение чувствительности
    if request_str.startswith("GET /set?"):
        # Извлекаем параметр sensitivity
        try:
            # Пример: GET /set?sensitivity=15.5
            query = request_str.split(' ')[1]  # /set?sensitivity=15.5
            if '?' in query:
                param_str = query.split('?', 1)[1]
                params = dict(p.split('=') for p in param_str.split('&'))
                if 'sensitivity' in params:
                    new_val = float(params['sensitivity'])
                    if 0.1 <= new_val <= 50:   # разумные пределы
                        sensitivity = new_val
                        response_body = json.dumps({"status": "ok", "sensitivity": sensitivity})
                    else:
                        response_body = json.dumps({"status": "error", "message": "Value out of range (0.1-50)"})
                else:
                    response_body = json.dumps({"status": "error", "message": "Missing sensitivity parameter"})
            else:
                response_body = json.dumps({"status": "error", "message": "Bad request"})
            
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "Connection: close\r\n"
                "\r\n"
                + response_body
            )
        except Exception as e:
            response = (
                "HTTP/1.1 400 Bad Request\r\n"
                "Content-Type: application/json\r\n"
                "Connection: close\r\n"
                "\r\n"
                + json.dumps({"status": "error", "message": str(e)})
            )
    else:
        # Отдаём HTML страницу
        html = """<!DOCTYPE html>
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
        .control-panel { background: white; padding: 15px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .control-panel label { font-weight: bold; display: inline-block; width: 150px; }
        .control-panel input[type=range] { width: 300px; vertical-align: middle; }
        .control-panel span { display: inline-block; width: 60px; text-align: center; }
    </style>
</head>
<body>
    <h1>LSM6DS33 Sensor Real-time Preview</h1>
    <div id="status" class="status disconnected">WebSocket: Disconnected</div>
    <div id="debug" class="debug">Waiting for data...</div>

    <!-- Панель управления чувствительностью -->
    <div class="control-panel">
        <label for="sensitivitySlider">Sensitivity (DPI):</label>
        <input type="range" id="sensitivitySlider" min="0.5" max="30" step="0.5" value="8">
        <span id="sensValue">8.0</span>
    </div>

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
        const sensSlider = document.getElementById('sensitivitySlider');
        const sensValue = document.getElementById('sensValue');

        const ws = new WebSocket(`ws://${location.hostname}:8765`);
        
        ws.onopen = () => {
            statusDiv.textContent = 'WebSocket: Connected';
            statusDiv.className = 'status connected';
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            // Обновляем графики
            datasets.accel.x.data.shift(); datasets.accel.x.data.push(data.accel.x);
            datasets.accel.y.data.shift(); datasets.accel.y.data.push(data.accel.y);
            datasets.accel.z.data.shift(); datasets.accel.z.data.push(data.accel.z);
            
            datasets.gyro.x.data.shift(); datasets.gyro.x.data.push(data.gyro.x);
            datasets.gyro.y.data.shift(); datasets.gyro.y.data.push(data.gyro.y);
            datasets.gyro.z.data.shift(); datasets.gyro.z.data.push(data.gyro.z);
            
            accelChart.update('none');
            gyroChart.update('none');
            
            // Обновляем слайдер, если сервер прислал свою sensitivity
            if (data.sensitivity !== undefined) {
                sensSlider.value = data.sensitivity;
                sensValue.textContent = parseFloat(data.sensitivity).toFixed(1);
            }

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
            setTimeout(() => location.reload(), 3000);
        };

        // Обработчик слайдера: отправляем новое значение на сервер
        sensSlider.addEventListener('input', () => {
            const val = sensSlider.value;
            sensValue.textContent = parseFloat(val).toFixed(1);
            // Отправляем через fetch (можно было бы через WS, но так проще)
            fetch(`/set?sensitivity=${val}`, { method: 'GET' })
                .then(response => response.json())
                .then(data => {
                    if (data.status !== 'ok') {
                        console.error('Failed to set sensitivity:', data);
                    }
                })
                .catch(err => console.error('Error setting sensitivity:', err));
        });
    </script>
</body>
</html>"""
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html\r\n"
            "Connection: close\r\n"
            "\r\n"
            + html
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