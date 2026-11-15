"""OAuth credentials detection + validation, using the REAL file on disk.
No secret values are printed - only structural verdicts."""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from app.core import youtube

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")


print("=== 1. DETECTION ===")
found = youtube.find_client_secret()
check("client secret file found", found is not None,
      f"({found.name if found else 'NONE'})")
err = youtube.client_secret_error()
check("no error reported when file is valid", err == "",
      (err[:80] + "...") if err else "")
check("has_client_secret() now True", youtube.has_client_secret() is True)

print("=== 2. VALIDATION ===")
if found:
    reason = youtube._validate_secret_json(found)
    check("JSON structure valid", reason is None, (reason or "ok")[:60])
    data = json.loads(found.read_text(encoding="utf-8"))
    kind = "installed" if "installed" in data else \
        ("web" if "web" in data else "NONE")
    check("desktop-app client type", kind in ("installed", "web"),
          f"(type={kind})")
    block = data.get(kind, {})
    check("client_id present", bool(block.get("client_id")))
    check("client_secret present", bool(block.get("client_secret")))
    check("auth/token URIs present",
          bool(block.get("auth_uri")) and bool(block.get("token_uri")))
    # never print any value - only key names
    print(f"    (keys present: {sorted(block.keys())})")

print("=== 3. OFFLINE FLOW CONSTRUCTION ===")
if found:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(
            str(found), scopes=youtube.SCOPES)
        check("InstalledAppFlow constructs (file usable by Google libs)",
              flow.client_config.get("client_id") is not None)
        url, _state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true",
            prompt="consent")
        check("authorization URL generates", url.startswith(
            "https://accounts.google.com/o/oauth2"))
    except Exception as e:  # noqa: BLE001
        check("InstalledAppFlow constructs", False, str(e)[:120])

print("=== 4. ERROR PATHS (synthetic bad files in temp) ===")
tmp = config.TEMP_DIR / "render"
tmp.mkdir(parents=True, exist_ok=True)
bad1 = tmp / "client_secret_bad.json"
bad1.write_text("{not json", encoding="utf-8")
r = youtube._validate_secret_json(bad1)
check("invalid JSON detected with reason", r is not None and "not valid JSON"
      in r, (r or "")[:60])
bad2 = tmp / "client_secret_wrong.json"
bad2.write_text('{"foo": {"bar": 1}}', encoding="utf-8")
r = youtube._validate_secret_json(bad2)
check("wrong structure detected", r is not None and "no 'installed'" in r,
      (r or "")[:60])

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("OAUTH DETECTION PASSED")
