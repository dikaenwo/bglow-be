import os
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

# Muat .env jika ada (untuk development lokal)
load_dotenv()

DB_CONFIG = {
    'host':     os.environ.get('DB_HOST', 'localhost'),
    'user':     os.environ.get('DB_USER', 'bglow'),
    'password': os.environ.get('DB_PASSWORD', 'Bglow@2026'),
    'database': os.environ.get('DB_NAME', 'bglow_db')
}

def get_db_connection():
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"Error connecting to MySQL: {e}")
        return None
