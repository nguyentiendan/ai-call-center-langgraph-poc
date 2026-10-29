# Test scenarios (Section 11 mapping)

All rows are automated in `tests/test_scenarios.py`, `tests/test_yaml_validation.py`, `tests/test_intent_eval.py` unless noted.

| ID | Test | File |
|---|---|---|
| T01 | `test_t01_happy_path` | test_scenarios.py |
| T02 | `test_t02_faq_no_verification` | test_scenarios.py |
| T03 | `test_t03_operator_immediately` | test_scenarios.py |
| T04 | `test_t04_wrong_pin_once_then_valid` | test_scenarios.py |
| T05 | `test_t05_wrong_pin_three_times_locks_handoff` | test_scenarios.py |
| T06 | `test_t06_prompt_injection_still_requires_verification` | test_scenarios.py |
| T07 | `test_t07_operator_during_pin_step` | test_scenarios.py |
| T08 | `test_t08_unknown_twice_handoff` | test_scenarios.py |
| T09 | `test_t09_verified_no_invoice_handoff` | test_scenarios.py |
| T10 | `test_t10_billing_timeout_one_retry_then_handoff` (asserts exactly 2 attempts) | test_scenarios.py |
| T11 | `test_t11_evil_model_output_replaced_by_grounded_template` | test_scenarios.py |
| T12 | `test_t12_unknown_action_rejected`, `test_t12_unknown_tool_rejected` | test_yaml_validation.py |
| T13 | `test_t13_missing_transition_state_rejected` | test_yaml_validation.py |
| T14 | `test_t14_direct_billing_without_verification_denied` | test_scenarios.py |
| T15 | `test_t15_interleaved_conversations_isolated` | test_scenarios.py |
| T16 | `test_t16_duplicate_handoff_idempotent` (second turn 409, single ticket; service-level idempotency in HandoffService) | test_scenarios.py |
| T17 | `test_t17_message_after_closed_409` | test_scenarios.py |
| T18 | `test_t18_pin_absent_from_checkpoint_transcript_logs` (inspects real checkpoint values + debug payload) | test_scenarios.py |
| T19 | `test_t19_empty_and_long_input` | test_scenarios.py |
| T20 | `test_t20_low_confidence_clarifies` | test_scenarios.py |

Intent quality: 30-utterance fixture, `test_intent_accuracy_report` (prints accuracy, asserts ≥0.90).
YAML-change proof: `test_yaml_change_proof_unknown_goes_straight_to_handoff` (Section 16.1.3).
