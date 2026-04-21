---
name: mortgage-domain
description: Use when implementing borrower segmentation, in-the-money rules, CLIP, Owner Link, lien, permit, listing, or offer logic.
---


# Mortgage domain modeling Skill

Use this skill for Module 0 business logic.

Domain anchors:
- CLIP identifies mastered property records.
- Owner Link connects owners to one or more properties.
- Open voluntary lien approximates active mortgage debt.
- In-the-money means economic incentive to transact based on rate spread, equity/LTV, maturity, and market assumptions.
- Building permits imply renovation funding needs and can support HELOC/cash-out offers.
- Listings imply possible purchase mortgage need.
- Multi-property ownership implies investor/repeat borrower profile.

Rules:
1. Scores must be explainable and deterministic in demo mode.
2. Each segment flag must cite source evidence.
3. Next-best-offer must include alternatives and rationale.
4. Never use real credit, protected-class, or sensitive demographic attributes.
5. Contactability/suppression are placeholders for compliance realism only.

