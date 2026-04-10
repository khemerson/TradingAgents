"""Unit tests for soul_enforcer.py"""
import sys
sys.path.insert(0, "/home/khemerson/tradingagents")

from soul_enforcer import enforce, parse_decision, extract_decision_json, EnforcementResult

def test(name, result, expected_valid, expected_violation_keywords=None):
    ok = result.valid == expected_valid
    if expected_violation_keywords:
        for kw in expected_violation_keywords:
            if not any(kw in v for v in result.violations):
                ok = False
                print(f"  FAIL: expected violation containing '{kw}' not found in {result.violations}")
    status = "✅" if ok else "❌"
    viol_str = ", ".join(result.violations) if result.violations else "none"
    print(f"{status} {name} — valid={result.valid}, violations=[{viol_str}]")
    return ok

passed = 0
total = 0

# --- Test 1: Valid BUY ---
total += 1
r = enforce({"action": "BUY", "ticker": "BTC-USD", "entry_price": 100, "stop_loss": 95,
             "take_profit": 120, "position_size_pct": 25, "confidence": 7}, cash_pct=80)
if test("Valid BUY with SL/TP", r, True): passed += 1

# --- Test 2: SHORT rejected ---
total += 1
r = enforce({"action": "SHORT", "ticker": "ETH-USD"})
if test("SHORT rejected", r, False, ["LONG_ONLY"]): passed += 1

# --- Test 3: SELL_SHORT rejected ---
total += 1
r = enforce({"action": "SELL_SHORT", "ticker": "AAPL"})
if test("SELL_SHORT rejected", r, False, ["LONG_ONLY"]): passed += 1

# --- Test 4: BUY without stop-loss ---
total += 1
r = enforce({"action": "BUY", "ticker": "AAPL", "entry_price": 200,
             "take_profit": 250, "position_size_pct": 20, "confidence": 6}, cash_pct=80)
if test("BUY without stop-loss", r, False, ["STOP_LOSS_REQUIRED"]): passed += 1

# --- Test 5: BUY without take-profit ---
total += 1
r = enforce({"action": "BUY", "ticker": "AAPL", "entry_price": 200, "stop_loss": 190,
             "position_size_pct": 20, "confidence": 6}, cash_pct=80)
if test("BUY without take-profit", r, False, ["TAKE_PROFIT_REQUIRED"]): passed += 1

# --- Test 6: Position too large (50%) ---
total += 1
r = enforce({"action": "BUY", "ticker": "AAPL", "entry_price": 200, "stop_loss": 190,
             "take_profit": 250, "position_size_pct": 50, "confidence": 7}, cash_pct=80)
if test("Position 50% too large", r, False, ["POSITION_TOO_LARGE"]): passed += 1

# --- Test 7: Low confidence (2/10) ---
total += 1
r = enforce({"action": "BUY", "ticker": "AAPL", "entry_price": 200, "stop_loss": 190,
             "take_profit": 250, "position_size_pct": 20, "confidence": 2}, cash_pct=80)
if test("Confidence 2/10 too low", r, False, ["LOW_CONFIDENCE"]): passed += 1

# --- Test 8: Cash minimum violation ---
total += 1
r = enforce({"action": "BUY", "ticker": "AAPL", "entry_price": 200, "stop_loss": 190,
             "take_profit": 250, "position_size_pct": 25, "confidence": 7}, cash_pct=40)
if test("Cash minimum violation (40%-25%=15%)", r, False, ["CASH_MINIMUM"]): passed += 1

# --- Test 9: Stop-loss too wide (-10%) ---
total += 1
r = enforce({"action": "BUY", "ticker": "AAPL", "entry_price": 200, "stop_loss": 178,
             "take_profit": 250, "position_size_pct": 20, "confidence": 7}, cash_pct=80)
if test("Stop-loss too wide (-11%)", r, False, ["STOP_LOSS_TOO_WIDE"]): passed += 1

# --- Test 10: HOLD always valid ---
total += 1
r = enforce({"action": "HOLD", "ticker": "AAPL"})
if test("HOLD always valid", r, True): passed += 1

# --- Test 11: SELL always valid ---
total += 1
r = enforce({"action": "SELL", "ticker": "AAPL"})
if test("SELL always valid", r, True): passed += 1

# --- Test 12: Multiple violations at once ---
total += 1
r = enforce({"action": "BUY", "ticker": "AAPL", "entry_price": 200,
             "position_size_pct": 50, "confidence": 1}, cash_pct=30)
if test("Multiple violations (no SL, no TP, pos too large, low conf, cash)", r, False,
        ["STOP_LOSS_REQUIRED", "TAKE_PROFIT_REQUIRED", "POSITION_TOO_LARGE", "LOW_CONFIDENCE", "CASH_MINIMUM"]):
    passed += 1

# --- Test 13: JSON extraction from LLM text ---
total += 1
text = '''Based on analysis, here is my decision:
```json
{"action": "BUY", "ticker": "BTC-USD", "entry_price": 60000, "stop_loss": 56000, "take_profit": 72000, "position_size_pct": 20, "strategy": "Simons", "confidence": 8, "rationale": "Momentum convergence"}
```
'''
d = parse_decision(text, "BTC-USD")
r = enforce(d, cash_pct=80)
if test("JSON extraction from fenced block", r, True): passed += 1

# --- Test 14: Fallback regex extraction ---
total += 1
text = "FINAL TRANSACTION PROPOSAL: **BUY** Entry price: $258.50, Stop loss: $241, Target: $300, Position size: 15%, Confidence: 7/10"
d = parse_decision(text, "AAPL")
assert d["action"] == "BUY", f"Expected BUY, got {d['action']}"
assert d.get("entry_price") == 258.5, f"Expected 258.5, got {d.get('entry_price')}"
r = enforce(d, cash_pct=80)
if test("Regex fallback extraction", r, True): passed += 1

print(f"\n{'='*50}")
print(f"Results: {passed}/{total} passed")
if passed == total:
    print("All tests passed! ✅")
else:
    print(f"{total - passed} test(s) FAILED ❌")
    sys.exit(1)
