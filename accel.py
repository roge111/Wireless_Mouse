#!/usr/bin/env python3
"""
Проект "Воздушная мышь" на Raspberry Pi с датчиком LSM6DS33.
Основной скрипт для чтения данных гироскопа и управления курсором мыши.
Включает веб-сервер для визуализации данных и WebSocket для передачи данных.

Автор: Студенты ИТМО, курс "Интернет вещей"
Версия: 1.0
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

# Глобальные переменные
sox = None          # Экземпляр датчика LSM6DS33
shutdown = False    # Флаг завершения работы
m = Mouse()         # Объект мыши для эмуляции HID

# Настройка GPIO
GPIO.setmode(GPIO.BCM)                     # Используем нумерацию BCM
TOUCH_PIN = 4                              # Пин сенсора касания
GPIO.setup(TOUCH_PIN, GPIO.IN)            # Настройка как вход

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


async def sensor_reader():
    """
    Основной цикл чтения данных с датчика LSM6DS33.
    
    Функция выполняет:
    1. Инициализацию датчика при первом запуске
    2. Чтение гироскопических данных
    3. Коррекцию смещения (калибровка)
    4. Управление курсором мыши при активации сенсором касания
    5. Логирование производительности (итераций в секунду)
    
    Данные сохраняются в глобальной переменной sensor_reader.latest_data
    для доступа из WebSocket-обработчика.
    """
    global sox, shutdown, m

    if sox is None:
        i2c = board.I2C()
        sox = LSM6DS33(i2c, address=0x6B)
        sox.accelerometer_rate = Rate.RATE_SHUTDOWN
        sox.accelerometer_range = AccelRange.RANGE_2G
        sox.high_pass_filter = AccelHPF.HPF_DIV400
        sox.gyro_data_rate = Rate.RATE_208_HZ
        soxgyro_range = GyroRange.RANGE_125_DPS

    last_click = False

    iteration_count = 0
    last_print_time = time.time()
    while not shutdown:
        try:
            #accel = sox.acceleration
            gyro = list(sox.gyro)

            gyro[0] += 0.11011872144861299
            gyro[1] -= 0.24510967682695364
            gyro[2] += 0.030543261909900768
            
            # Store latest reading
            sensor_reader.latest_data = {
                #"accel": {"x": accel[0], "y": accel[1], "z": accel[2]},
                "accel": {"x": 0, "y": 0, "z": 0},
                "gyro": {"x": zero_if_small(gyro[0]), "y": zero_if_small(gyro[1]), "z": zero_if_small(gyro[2])}
            }
            #m.move(-round(accel[1] * 2), -round((accel[2] - 3.5) * 4))

            touch_state = GPIO.input(TOUCH_PIN)
            new_click = touch_state == GPIO.HIGH

            if new_click:
                m.move(-round(gyro[2] * 8), -round(gyro[0] * 8))
            
            #if not last_click == new_click:
            #    if new_click:
            #        m.left_click(release=False)
            #    else:
            #        m.release()
            #    last_click = new_click
            
            await asyncio.sleep(0)  # ~10 Hz update rate
            iteration_count += 1
            
            current_time = time.time()
            if current_time - last_print_time >= 1.0:
                iterations_per_sec = iteration_count
                print(f"Iterations per second: {iterations_per_sec:,}")
                
                # Reset counters
                iteration_count = 0
                last_print_time = current_time
            
        except Exception as e:
            print(f"Sensor error: {e}. Reconnecting...")
            sox = None
            await asyncio.sleep(1)

async def websocket_handler(websocket, path):
    """
    Обработчик WebSocket-соединений для передачи данных сенсора.
    
    Аргументы:
        websocket: Объект WebSocket-соединения
        path: Путь запроса (не используется)
    
    Функция отправляет клиенту JSON с последними данными сенсора
    с частотой ~100 Гц (каждые 0.01 секунды).
    """
    global shutdown
    print("Client connected")
    
    try:
        while not shutdown:
            if hasattr(sensor_reader, 'latest_data'):
                await websocket.send(json.dumps(sensor_reader.latest_data))
            await asyncio.sleep(0.01)  # Отправка данных с частотой 100 Гц
    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")

async def http_handler(reader, writer):
    """
    Обработчик HTTP-запросов для обслуживания HTML-страницы визуализации.
    
    Аргументы:
        reader: asyncio.StreamReader для чтения запроса
        writer: asyncio.StreamWriter для отправки ответа
    
    Возвращает статическую HTML-страницу с графиками Chart.js
    для визуализации данных сенсора в реальном времени.
    """
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
    """
    Основная асинхронная функция, запускающая все компоненты системы.
    
    Выполняет:
    1. Настройку обработчиков сигналов SIGINT/SIGTERM
    2. Запуск задачи чтения сенсора (sensor_reader)
    3. Запуск WebSocket-сервера на порту 8765
    4. Запуск HTTP-сервера на порту 8000 для визуализации
    5. Ожидание завершения с корректной обработкой shutdown
    """
    global shutdown

    loop = asyncio.get_running_loop()
    for signal_enum in [SIGINT, SIGTERM]:
        loop.add_signal_handler(signal_enum, loop.stop)
    
    # Запуск задачи чтения сенсора
    sensor_task = asyncio.create_task(sensor_reader())
    
    # Запуск WebSocket-сервера
    ws_server = await websockets.serve(websocket_handler, "0.0.0.0", 8765)
    print("WebSocket server started on ws://0.0.0.0:8765")
    
    # Запуск HTTP-сервера
    http_server = await asyncio.start_server(http_handler, "0.0.0.0", 8000)
    print("HTTP server started on http://0.0.0.0:8000")
    
    # Обработка сигналов завершения
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
        # Корректное завершение серверов
        ws_server.close()
        http_server.close()
        await ws_server.wait_closed()
        await http_server.wait_closed()
        print("Servers stopped")

if __name__ == "__main__":
    """
    Точка входа в программу.
    
    Запускает асинхронный цикл с обработкой исключений и очисткой GPIO.
    """
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        expected_msg = "Event loop stopped before Future completed."
        print("Bye")
        GPIO.cleanup()
        if not exc.args is None:
            print(exc.args and exc.args[0])
        