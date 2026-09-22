"""
Google Wallet integration.

To make this actually work you need (all free):
1. A Google Cloud project: https://console.cloud.google.com/
2. Enable the "Google Wallet API" for that project
3. Create a Service Account, download its JSON key -> save as service-account.json in this folder
4. Register as a Google Wallet issuer: https://pay.google.com/business/console
   (get your ISSUER_ID from there)

Until you've done that setup, WALLET_ENABLED stays False and the app runs in
"mock mode" so you can still test the stamp-counting logic without wallet passes.
"""
import os
import json
import time
import uuid

ISSUER_ID = os.environ.get("GOOGLE_WALLET_ISSUER_ID", "")
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "..", "service-account.json")
WALLET_ENABLED = bool(ISSUER_ID) and os.path.exists(SERVICE_ACCOUNT_FILE)

CLASS_ID = f"{ISSUER_ID}.stampcard_class" if ISSUER_ID else None

print(f"ISSUER_ID: '{ISSUER_ID}'")
print(f"SERVICE_ACCOUNT_FILE: {SERVICE_ACCOUNT_FILE}")
print(f"File exists: {os.path.exists(SERVICE_ACCOUNT_FILE)}")
print(f"WALLET_ENABLED: {WALLET_ENABLED}")

if WALLET_ENABLED:
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
    )
    session = AuthorizedSession(credentials)
    BASE_URL = "https://walletobjects.googleapis.com/walletobjects/v1"


def ensure_loyalty_class(shop_name: str, stamps_required: int):
    """Creates the 'template' for the loyalty card (once per shop), or updates it if it already exists."""
    if not WALLET_ENABLED:
        return None

    class_payload = {
        "id": CLASS_ID,
        "issuerName": "MFS",
        "programName": "MFS",
        "reviewStatus": "UNDER_REVIEW",
        "hexBackgroundColor": "#cfa588",
        "programLogo": {
            "sourceUri": {
                "uri": "https://placehold.co/300x300/7a4a2e/f2e2d3?text=MFS"
            },
            "contentDescription": {
                "defaultValue": {
                    "language": "en-US",
                    "value": f"{shop_name} logo"
                }
            }
        }
    }

    resp = session.post(f"{BASE_URL}/loyaltyClass", json=class_payload)
    if resp.status_code == 200:
        return CLASS_ID

    if resp.status_code == 409:
        # Already exists — update it with the current payload instead
        update_resp = session.put(f"{BASE_URL}/loyaltyClass/{CLASS_ID}", json=class_payload)
        if update_resp.status_code not in (200,):
            print("Warning updating wallet class:", update_resp.status_code, update_resp.text)
    else:
        print("Warning creating wallet class:", resp.status_code, resp.text)

    return CLASS_ID


def create_or_update_pass(customer_identifier: str, shop_name: str, stamp_count: int,
                           stamps_required: int, reward_description: str):
    """
    Creates a loyalty object (the actual card) for a customer, or updates it if it exists.
    Returns the 'Add to Google Wallet' link the customer taps.
    """
    if not WALLET_ENABLED:
        # Mock mode: return a fake link so the rest of the flow can be tested end-to-end
        return f"MOCK_WALLET_LINK_for_{customer_identifier}_stamps_{stamp_count}"

    ensure_loyalty_class(shop_name, stamps_required)

    object_id = f"{ISSUER_ID}.{customer_identifier}"

    points_remaining = max(stamps_required - stamp_count, 0)
    object_payload = {
        "id": object_id,
        "classId": CLASS_ID,
        "state": "ACTIVE",
        "accountId": customer_identifier,
        "accountName": "Loyalty Member",
        "loyaltyPoints": {
            "label": "Stamps",
            "balance": {"int": stamp_count},
        },
        "barcode": {
            "type": "QR_CODE",
            "value": customer_identifier,
            "alternateText": customer_identifier
        },
        "textModulesData": [
            {
                "header": "Reward",
                "body": (
                    f"{points_remaining} more to go for: {reward_description}"
                    if points_remaining > 0
                    else f"Reward ready: {reward_description}!"
                ),
            }
        ],
    }

    resp = session.get(f"{BASE_URL}/loyaltyObject/{object_id}")
    if resp.status_code == 200:
        update_resp = session.put(f"{BASE_URL}/loyaltyObject/{object_id}", json=object_payload)
        if update_resp.status_code not in (200,):
            print("Error updating wallet object:", update_resp.status_code, update_resp.text)
    else:
        create_resp = session.post(f"{BASE_URL}/loyaltyObject", json=object_payload)
        if create_resp.status_code not in (200,):
            print("Error creating wallet object:", create_resp.status_code, create_resp.text)

    # Build the "Add/View in Google Wallet" JWT link
    import jwt

    with open(SERVICE_ACCOUNT_FILE) as f:
        service_account_info = json.load(f)

    claims = {
        "iss": credentials.service_account_email,
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        "payload": {"loyaltyObjects": [{"id": object_id}]},
    }
    signed_jwt = jwt.encode(claims, service_account_info["private_key"], algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{signed_jwt}"

