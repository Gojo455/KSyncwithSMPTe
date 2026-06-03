from app import hash_pw, verify_pw

hashed = hash_pw("testpassword")
print(verify_pw(hashed, "testpassword"))   # should print True
print(verify_pw(hashed, "wrongpassword"))  # should print False