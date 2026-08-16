"""The policy rules, one test per rule."""

from __future__ import annotations

import unittest
from decimal import Decimal

from support import request  # noqa: F401  (keeps the path setup in one place)

from app import plans, policy
from app.models import Role


class PolicyRules(unittest.TestCase):
    def test_read_runs_alone(self) -> None:
        verdict = policy.evaluate("read_account", {"account": "ACC-100"})
        self.assertFalse(verdict.needs_approval)
        self.assertEqual(verdict.rule, "read_runs_alone")

    def test_small_credit_runs_alone(self) -> None:
        verdict = policy.evaluate("apply_credit", {"amount": Decimal("100.00")})
        self.assertFalse(verdict.needs_approval)
        self.assertEqual(verdict.rule, "small_credit_runs_alone")

    def test_large_credit_needs_finance(self) -> None:
        verdict = policy.evaluate("apply_credit", {"amount": Decimal("100.01")})
        self.assertTrue(verdict.needs_approval)
        self.assertIs(verdict.role, Role.FINANCE)

    def test_credit_amount_may_arrive_as_a_string(self) -> None:
        self.assertTrue(policy.evaluate("apply_credit", {"amount": "250.00"}).needs_approval)

    def test_debit_needs_finance(self) -> None:
        verdict = policy.evaluate("apply_debit", {"amount": Decimal("1.00")})
        self.assertIs(verdict.role, Role.FINANCE)

    def test_freezing_needs_risk(self) -> None:
        for operation in ("freeze_account", "unfreeze_account"):
            with self.subTest(operation=operation):
                self.assertIs(policy.evaluate(operation, {}).role, Role.RISK)

    def test_anything_else_runs_alone(self) -> None:
        verdict = policy.evaluate("notify_customer", {"account": "ACC-100"})
        self.assertFalse(verdict.needs_approval)
        self.assertEqual(verdict.rule, "default_runs_alone")

    def test_the_catalogue_lists_the_rules_in_order(self) -> None:
        names = [rule["rule"] for rule in policy.catalogue()]
        self.assertEqual(names[0], "read_runs_alone")
        self.assertEqual(names[-1], "default_runs_alone")


class Plans(unittest.TestCase):
    def test_each_kind_has_its_steps(self) -> None:
        steps = [step.operation for step in plans.build(request())]
        self.assertEqual(steps, ["read_account", "apply_credit", "notify_customer"])

    def test_notify_carries_the_origin_and_reference(self) -> None:
        step = plans.build(request())[-1]
        self.assertEqual(step.arguments["origin"], "internal")
        self.assertEqual(step.arguments["reference"], "REQ-1")


if __name__ == "__main__":
    unittest.main()
