import mysql.connector
from mysql.connector import Error

# Konfigurasi awal tanpa nama database untuk membuat database-nya terlebih dahulu
INIT_CONFIG = {
    'host': 'localhost',
    'user': 'bglow',
    'password': 'Bglow@2026'
}

def init_database():
    try:
        # Koneksi ke MySQL server
        conn = mysql.connector.connect(**INIT_CONFIG)
        if conn.is_connected():
            cursor = conn.cursor()
            
            # Buat database jika belum ada
            cursor.execute("CREATE DATABASE IF NOT EXISTS bglow_db")
            print("Database 'bglow_db' checked/created.")
            
            # Gunakan database tersebut
            cursor.execute("USE bglow_db")
            
            # 1. Buat tabel users jika belum ada (hanya memuat data core profile)
            create_users_table = """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                profile_photo LONGTEXT,
                skin_type VARCHAR(255),
                acne_level VARCHAR(255),
                oil_level VARCHAR(255),
                pore_condition VARCHAR(255),
                skin_score INT DEFAULT 0,
                sunscreen_interval INT DEFAULT 2,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
            """
            cursor.execute(create_users_table)
            print("Table 'users' checked/created.")
            
            # Cek jika kolom lama ada di tabel users, jika ada drop untuk membersihkan tabel users (normalisasi)
            cursor.execute("SHOW COLUMNS FROM users")
            existing_columns = [col[0] for col in cursor.fetchall()]
            
            old_columns = ['favorites', 'diary_entries', 'routine', 'special_schedule', 'streak', 'routine_progress']
            for col in old_columns:
                if col in existing_columns:
                    cursor.execute(f"ALTER TABLE users DROP COLUMN {col}")
                    print(f"Dropped old column '{col}' from 'users' table to separate data.")
            
            # Pastikan kolom sunscreen_interval terdaftar
            cursor.execute("SHOW COLUMNS FROM users")
            refreshed_cols = [col[0] for col in cursor.fetchall()]
            if 'sunscreen_interval' not in refreshed_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN sunscreen_interval INT DEFAULT 2")
                print("Added column 'sunscreen_interval' to 'users' table.")
            
            # 2. Buat tabel user_favorites jika belum ada (Pemisahan data produk favorit)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_favorites (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                product_id VARCHAR(255),
                product_name VARCHAR(255),
                product_brand VARCHAR(255),
                product_price INT,
                product_emoji VARCHAR(50),
                product_bg_color VARCHAR(50),
                product_rating DECIMAL(3, 1),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
            """)
            print("Table 'user_favorites' checked/created.")

            # 3. Buat tabel user_diary jika belum ada (Pemisahan data diary harian)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_diary (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                entry_date VARCHAR(100) NOT NULL,
                mood VARCHAR(50) NOT NULL,
                conditions_json TEXT NOT NULL,
                products TEXT,
                notes TEXT,
                image_url LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
            """)
            print("Table 'user_diary' checked/created.")

            # 4. Buat tabel user_routines jika belum ada (Pemisahan data rutinitas)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_routines (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL UNIQUE,
                routine_data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
            """)
            print("Table 'user_routines' checked/created.")

            # 5. Buat tabel user_routine_progress jika belum ada (Pemisahan data progres)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_routine_progress (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL UNIQUE,
                progress_data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
            """)
            print("Table 'user_routine_progress' checked/created.")

            # 6. Buat tabel user_streaks jika belum ada (Pemisahan data streak)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_streaks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL UNIQUE,
                streak_data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
            """)
            print("Table 'user_streaks' checked/created.")

            # 7. Buat tabel user_special_schedules jika belum ada (Pemisahan data jadwal khusus)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_special_schedules (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL UNIQUE,
                schedule_data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
            """)
            print("Table 'user_special_schedules' checked/created.")
            
            # 8. Buat tabel user_bpom_history jika belum ada
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_bpom_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                product_name VARCHAR(255) NOT NULL,
                reg_no VARCHAR(255) NOT NULL,
                manufacturer VARCHAR(255) DEFAULT '',
                status VARCHAR(50) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
            """)
            print("Table 'user_bpom_history' checked/created.")
            
            conn.commit()
            cursor.close()
            conn.close()
            print("Database initialization and separation successful!")
    except Error as e:
        print(f"Error during database initialization: {e}")

if __name__ == "__main__":
    init_database()
