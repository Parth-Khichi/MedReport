
import unittest
import time
from selenium import webdriver
from selenium.webdriver.common.by import By


class MedicalPortalTest(unittest.TestCase):

    def setUp(self):
        self.driver = webdriver.Chrome()
        self.driver.implicitly_wait(5)

    def test_login_page(self):
        self.driver.get("http://127.0.0.1:5000/login")

        time.sleep(10)

        self.assertIn(
            "/login",
            self.driver.current_url.lower()
        )

    def test_invalid_login(self):
        self.driver.get("http://127.0.0.1:5000/login")

        time.sleep(10)

        self.driver.find_element(
            By.NAME, "email"
        ).send_keys("wrong@test.com")

        self.driver.find_element(
            By.NAME, "password"
        ).send_keys("wrong123")

        self.driver.find_element(
            By.CSS_SELECTOR, "button[type='submit']"
        ).click()

        time.sleep(10)

        self.assertNotIn(
            "/dashboard",
            self.driver.current_url.lower()
        )



    def tearDown(self):
        time.sleep(10)
        self.driver.quit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
