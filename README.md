# B-Glow Backend

This is the backend service for the B-Glow application, built with Python and Flask. It provides RESTful APIs for user authentication, profile management, diary entries, skincare routines, and BPOM product scanning.

## Tech Stack
- **Framework:** Flask
- **Database:** MySQL (using `mysql-connector-python`)
- **Authentication:** JWT (JSON Web Tokens)
- **Environment Management:** `python-dotenv`

## Features
- User Registration & Login (Local & Social)
- JWT-based Authentication
- User Profile Management (Skin Type, Favorites, Diary, Routines, Streaks)
- BPOM Product Scanning & History

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/dikaenwo/bglow-be.git
   cd bglow-be
   ```

2. **Create a virtual environment (optional but recommended):**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables:**
   Copy `.env.example` to `.env` and configure your database and JWT secrets:
   ```bash
   cp .env.example .env
   ```

5. **Initialize the Database:**
   Run the initialization script to set up your MySQL database tables.
   ```bash
   python init_db.py
   ```

6. **Run the Application:**
   ```bash
   python main.py
   ```
   The server will start at `http://0.0.0.0:5050`.

## API Endpoints Overview
- `POST /api/register` - User registration
- `POST /api/login` - User login
- `POST /api/forgot-password` - Forgot password (Mock OTP)
- `POST /api/reset-password` - Reset password
- `POST /api/social-login` - Social login integration
- `GET /api/user/<user_id>` - Get user profile
- `PUT /api/user/<user_id>` - Update user profile
- `GET /api/bpom-history/<user_id>` - Get BPOM scan history
- `POST /api/bpom-history` - Add BPOM scan history
- `GET/POST /api/scan-bpom` - Mock endpoint to scan BPOM registry
