import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["METAAPI_TOKEN"]
BASE = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"
HEADERS = {"auth-token": TOKEN, "Content-Type": "application/json"}

OLD_ACCOUNT_ID = "bdc02312-4721-40b7-a051-81c16a3b23a5"


def list_accounts():
    r = requests.get(f"{BASE}/users/current/accounts", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def print_accounts(accounts):
    print(f"{'id':<38} {'name':<25} {'state':<12} {'connectionStatus':<18} {'reliability':<12} login")
    for a in accounts:
        print(f"{a.get('_id',''):<38} {a.get('name',''):<25} {a.get('state',''):<12} "
              f"{a.get('connectionStatus',''):<18} {a.get('reliability',''):<12} {a.get('login','')}")


def undeploy(account_id):
    r = requests.post(f"{BASE}/users/current/accounts/{account_id}/undeploy", headers=HEADERS, timeout=60)
    return r


def remove(account_id):
    r = requests.delete(f"{BASE}/users/current/accounts/{account_id}", headers=HEADERS, timeout=60)
    return r


print("=== STEP 1: Listing ALL accounts ===")
accounts = list_accounts()
print_accounts(accounts)

# Determine which to delete
to_delete = []
for a in accounts:
    if a.get("_id") == OLD_ACCOUNT_ID:
        to_delete.append(a)
    elif a.get("name") == "Bot v2" and str(a.get("login")) == "413879493":
        to_delete.append(a)

print("\n=== STEP 2: Accounts targeted for deletion ===")
print_accounts(to_delete)

print("\n=== STEP 2: Deleting ===")
for a in to_delete:
    acc_id = a["_id"]
    state = a.get("state")
    print(f"\n-- Processing {acc_id} (name={a.get('name')}, state={state}) --")
    try:
        if state not in ("UNDEPLOYED", "CREATED"):
            print(f"  Calling undeploy() ...")
            r = undeploy(acc_id)
            print(f"  undeploy status={r.status_code} body={r.text[:300]}")
            time.sleep(5)
        else:
            print(f"  Skipping undeploy (state={state})")
    except Exception as e:
        print(f"  ERROR during undeploy: {e}")

    try:
        print(f"  Calling remove() ...")
        r = remove(acc_id)
        print(f"  remove status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        print(f"  ERROR during remove: {e}")

print("\n=== STEP 3: Listing accounts again to confirm ===")
time.sleep(3)
accounts_after = list_accounts()
print_accounts(accounts_after)

remaining_ids = {a["_id"] for a in accounts_after}
still_present = [a for a in to_delete if a["_id"] in remaining_ids]
if still_present:
    print(f"\nWARNING: {len(still_present)} target account(s) still present:")
    print_accounts(still_present)
else:
    print("\nAll targeted accounts successfully removed.")

print(f"\nTotal remaining accounts: {len(accounts_after)}")
