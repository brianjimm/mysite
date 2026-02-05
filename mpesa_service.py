
import requests
import base64
from datetime import datetime
from requests.auth import HTTPBasicAuth
import os

class MpesaService:
    def __init__(self):
        self.consumer_key = os.getenv('MPESA_CONSUMER_KEY')
        self.consumer_secret = os.getenv('MPESA_CONSUMER_SECRET')
        self.shortcode = os.getenv('MPESA_SHORTCODE')
        self.passkey = os.getenv('MPESA_PASSKEY')
        self.base_url_env = os.getenv('MPESA_ENVIRONMENT', 'sandbox')
        self.callback_url = f"{os.getenv('BASE_URL')}/mpesa/callback"

        # Set base URL
        if self.base_url_env == 'production':
            self.base_url = 'https://api.safaricom.co.ke'
        else:
            self.base_url = 'https://sandbox.safaricom.co.ke'

    def get_access_token(self):
        """Generate access token"""
        try:
            url = f'{self.base_url}/oauth/v1/generate?grant_type=client_credentials'
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret)
            )
            response.raise_for_status()
            return response.json()['access_token']
        except Exception as e:
            print(f"Error getting access token: {e}")
            return None

    def generate_password(self, timestamp):
        """Generate password for STK Push"""
        data_to_encode = f"{self.shortcode}{self.passkey}{timestamp}"
        encoded = base64.b64encode(data_to_encode.encode())
        return encoded.decode('utf-8')

    def format_phone_number(self, phone):
        """Format phone number to 254XXXXXXXXX"""
        phone = phone.strip().replace(' ', '')
        if phone.startswith('+'):
            phone = phone[1:]
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        if not phone.startswith('254'):
            phone = '254' + phone
        return phone

    def stk_push(self, phone_number, amount, account_reference, transaction_desc="Payment"):
        """Initiate STK Push"""
        access_token = self.get_access_token()
        if not access_token:
            return {'success': False, 'message': 'Failed to authenticate with M-Pesa'}

        phone_number = self.format_phone_number(phone_number)
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password = self.generate_password(timestamp)

        url = f'{self.base_url}/mpesa/stkpush/v1/processrequest'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'TransactionType': 'CustomerPayBillOnline',
            'Amount': int(amount),
            'PartyA': phone_number,
            'PartyB': self.shortcode,
            'PhoneNumber': phone_number,
            'CallBackURL': self.callback_url,
            'AccountReference': account_reference,
            'TransactionDesc': transaction_desc
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()

            return {
                'success': True,
                'checkout_request_id': result.get('CheckoutRequestID'),
                'merchant_request_id': result.get('MerchantRequestID'),
                'response_code': result.get('ResponseCode'),
                'response_description': result.get('ResponseDescription'),
                'customer_message': result.get('CustomerMessage')
            }
        except requests.exceptions.RequestException as e:
            error_message = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    error_message = error_data.get('errorMessage', error_message)
                except:
                    error_message = e.response.text

            return {'success': False, 'message': error_message}
