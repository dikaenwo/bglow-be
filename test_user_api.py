import unittest
import json
from main import app, generate_token
from database import get_db_connection

class TestUserAPI(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

        self.email = "test_antigravity@example.com"
        self.password = "password123"
        self.name = "Test Antigravity"

        # Bersihkan test user kalau ada
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE email = %s", (self.email,))
            conn.commit()
            cursor.close()
            conn.close()

        # Helper: simpan auth header setelah login
        self.auth_headers = {}

    def _auth_headers(self, user_id: int) -> dict:
        """Buat Authorization header dengan JWT token yang valid."""
        token = generate_token(user_id)
        return {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }

    def test_complete_user_flow(self):
        # 1. Register User
        reg_response = self.app.post('/api/register',
            data=json.dumps({
                'name': self.name,
                'email': self.email,
                'password': self.password
            }),
            content_type='application/json'
        )
        self.assertEqual(reg_response.status_code, 201)

        # 2. Login User — harus dapat token
        login_response = self.app.post('/api/login',
            data=json.dumps({
                'email': self.email,
                'password': self.password
            }),
            content_type='application/json'
        )
        self.assertEqual(login_response.status_code, 200)
        login_data = json.loads(login_response.data)
        user_id = login_data['user']['id']
        self.assertIn('token', login_data, "Login harus mengembalikan JWT token")
        self.assertEqual(login_data['user']['name'], self.name)
        self.assertEqual(login_data['user']['email'], self.email)
        self.assertIsNone(login_data['user']['skin_type'])

        auth = self._auth_headers(user_id)

        # 3. Update User Profile Info (Name & Email)
        update_response = self.app.put(f'/api/user/{user_id}',
            data=json.dumps({
                'name': "Updated Name",
                'email': "updated_email@example.com"
            }),
            headers=auth
        )
        self.assertEqual(update_response.status_code, 200)
        update_data = json.loads(update_response.data)
        self.assertEqual(update_data['user']['name'], "Updated Name")
        self.assertEqual(update_data['user']['email'], "updated_email@example.com")

        # 4. Update Profile Photo
        photo_response = self.app.put(f'/api/user/{user_id}',
            data=json.dumps({
                'profile_photo': "data:image/png;base64,iVBORw0KGgoAAAANS="
            }),
            headers=auth
        )
        self.assertEqual(photo_response.status_code, 200)
        photo_data = json.loads(photo_response.data)
        self.assertEqual(photo_data['user']['profile_photo'], "data:image/png;base64,iVBORw0KGgoAAAANS=")

        # 5. Update Skin Profile Scan Results
        skin_response = self.app.put(f'/api/user/{user_id}',
            data=json.dumps({
                'skin_type': "Kombinasi",
                'acne_level': "Ringan — Grade 1",
                'oil_level': "Sedang — T-Zone",
                'pore_condition': "Baik — Minimal",
                'skin_score': 65
            }),
            headers=auth
        )
        self.assertEqual(skin_response.status_code, 200)
        skin_data = json.loads(skin_response.data)
        self.assertEqual(skin_data['user']['skin_type'], "Kombinasi")
        self.assertEqual(skin_data['user']['acne_level'], "Ringan — Grade 1")
        self.assertEqual(skin_data['user']['oil_level'], "Sedang — T-Zone")
        self.assertEqual(skin_data['user']['pore_condition'], "Baik — Minimal")
        self.assertEqual(skin_data['user']['skin_score'], 65)

        # 6. Fetch Profile details
        get_response = self.app.get(f'/api/user/{user_id}', headers=auth)
        self.assertEqual(get_response.status_code, 200)
        get_data = json.loads(get_response.data)
        self.assertEqual(get_data['name'], "Updated Name")
        self.assertEqual(get_data['email'], "updated_email@example.com")
        self.assertEqual(get_data['profile_photo'], "data:image/png;base64,iVBORw0KGgoAAAANS=")
        self.assertEqual(get_data['skin_type'], "Kombinasi")
        self.assertEqual(get_data['skin_score'], 65)

    def test_unauthorized_access(self):
        """Akses endpoint tanpa token harus ditolak dengan 401."""
        response = self.app.get('/api/user/1')
        self.assertEqual(response.status_code, 401)

    def test_wrong_user_access(self):
        """User tidak boleh akses data user lain — harus dapat 403."""
        # Register user A
        self.app.post('/api/register',
            data=json.dumps({'name': self.name, 'email': self.email, 'password': self.password}),
            content_type='application/json'
        )
        login_data = json.loads(self.app.post('/api/login',
            data=json.dumps({'email': self.email, 'password': self.password}),
            content_type='application/json'
        ).data)
        user_id = login_data['user']['id']

        # Coba akses profil user lain (user_id + 9999 = pasti tidak ada)
        response = self.app.get(f'/api/user/{user_id + 9999}',
            headers=self._auth_headers(user_id))
        self.assertEqual(response.status_code, 403)

    def tearDown(self):
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE email IN (%s, %s)",
                           (self.email, "updated_email@example.com"))
            conn.commit()
            cursor.close()
            conn.close()

if __name__ == '__main__':
    unittest.main()
