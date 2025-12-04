"""
Environment Variable Configuration Test
This script verifies that DRY_RUN and other critical environment variables are properly loaded.
"""
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

print("=" * 60)
print("Environment Variable Configuration Check")
print("=" * 60)

# Check DRY_RUN
dry_run_env = os.getenv("DRY_RUN")
print(f"\n1. DRY_RUN Environment Variable")
print(f"   Raw value: {dry_run_env}")
print(f"   Type: {type(dry_run_env)}")

# Test Settings loading
try:
    from config.settings import settings
    
    print(f"\n2. Settings.dry_run Value")
    print(f"   Parsed value: {settings.dry_run}")
    print(f"   Type: {type(settings.dry_run)}")
    
    # Check trading mode
    if settings.dry_run:
        print(f"\n   ⚠️  CURRENT MODE: SIMULATION (Paper Trading)")
        print(f"   → No real money will be used")
        print(f"   → To enable live trading, set DRY_RUN=false in .env file")
    else:
        print(f"\n   ⚠️  CURRENT MODE: LIVE TRADING")
        print(f"   → Real money will be used!")
        print(f"   → Make sure you have tested thoroughly in simulation mode")
    
    # Check other critical settings
    print(f"\n3. Other Critical Settings")
    print(f"   DEBUG: {settings.debug}")
    print(f"   Markets: {settings.trading.markets}")
    print(f"   Leverage (base/max/min): {settings.leverage.base_leverage}x / {settings.leverage.max_leverage}x / {settings.leverage.min_leverage}x")
    print(f"   Margin Mode: {settings.leverage.margin_mode_name}")
    
    # Check API credentials
    print(f"\n4. API Configuration")
    api_key = settings.trading.api_key
    private_key = settings.trading.private_key
    print(f"   LIGHTER_API_KEY: {'✓ Set' if api_key else '✗ Not set'}")
    print(f"   LIGHTER_PRIVATE_KEY: {'✓ Set' if private_key else '✗ Not set'}")
    print(f"   LIGHTER_HOST: {settings.trading.host}")
    
    # Final check
    print(f"\n5. Configuration Status")
    if settings.dry_run:
        print(f"   ✓ Safe to run - Simulation mode")
    else:
        if api_key and private_key:
            print(f"   ⚠️  LIVE TRADING MODE - Real money at risk!")
            print(f"   ✓ API credentials configured")
        else:
            print(f"   ✗ ERROR: Live trading mode but missing API credentials!")
    
except Exception as e:
    print(f"\n❌ Error loading settings: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("Test Complete")
print("=" * 60)
