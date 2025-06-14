#!/usr/bin/env python3
import requests
import json
import time

# AWX настройки
AWX_HOST = "192.168.0.216:32315"
AWX_TOKEN = "XurM7z4Ntj2FORjUB1Kvinkc8Ma2K9"
JOB_TEMPLATE_ID = 7

# Заголовки для API запросов
headers = {
    "Authorization": f"Bearer {AWX_TOKEN}",
    "Content-Type": "application/json"
}

def launch_job_template():
    """Запускает job template"""
    url = f"http://{AWX_HOST}/api/v2/job_templates/{JOB_TEMPLATE_ID}/launch/"
    
    print(f"Launching job template {JOB_TEMPLATE_ID}...")
    
    try:
        response = requests.post(url, headers=headers, verify=False, timeout=30)
        
        if response.status_code == 201:
            job_data = response.json()
            job_id = job_data['job']
            print(f"Job launched successfully! Job ID: {job_id}")
            
            # Мониторим статус job
            monitor_job(job_id)
            
        else:
            print(f"Failed to launch job. Status: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"Error launching job: {str(e)}")

def monitor_job(job_id):
    """Мониторит выполнение job"""
    url = f"http://{AWX_HOST}/api/v2/jobs/{job_id}/"
    
    print(f"Monitoring job {job_id}...")
    
    while True:
        try:
            response = requests.get(url, headers=headers, verify=False, timeout=30)
            if response.status_code == 200:
                job_data = response.json()
                status = job_data['status']
                
                print(f"Job status: {status}")
                
                if status in ['successful', 'failed', 'error', 'canceled']:
                    print(f"Job finished with status: {status}")
                    
                    # Получаем stdout
                    get_job_output(job_id)
                    break
                
                time.sleep(5)  # Ждем 5 секунд перед следующей проверкой
            else:
                print(f"Failed to get job status: {response.status_code}")
                break
                
        except Exception as e:
            print(f"Error monitoring job: {str(e)}")
            break

def get_job_output(job_id):
    """Получает вывод job"""
    url = f"http://{AWX_HOST}/api/v2/jobs/{job_id}/stdout/?format=json"
    
    try:
        response = requests.get(url, headers=headers, verify=False, timeout=30)
        if response.status_code == 200:
            output_data = response.json()
            print("\n=== JOB OUTPUT ===")
            print(output_data.get('content', 'No output available'))
        else:
            print(f"Failed to get job output: {response.status_code}")
            
    except Exception as e:
        print(f"Error getting job output: {str(e)}")

if __name__ == "__main__":
    launch_job_template() 