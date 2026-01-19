from dotenv import load_dotenv
load_dotenv()

import os

print("SUPABASE_URL:", os.environ.get("SUPABASE_URL"))
print("SERVICE_ROLE prefix:", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")[:20])
print("ANON_SEED:", os.environ.get("ANON_SEED"))
